"""#1941 fixed phase read targets; public cleanup execution stays closed."""
from dataclasses import replace
from contextlib import contextmanager
from contextvars import copy_context
from importlib import import_module
from importlib.util import find_spec
import json
import re
import unittest

from control_plane_kit_operations.postgres.configuration_evidence import _Capacity, _Unavailable
from control_plane_kit_operations.postgres.receiver_execution_scopes import _ExecutionScopeStorage
from control_plane_kit_operations.postgres.stores import PostgresStoreBundle
from tests.configuration_cleanup_phase_read_bounds_fixture import ConfigurationCleanupPhaseReadBoundsFixture


class PostgresConfigurationCleanupPhaseReadBoundsTests(ConfigurationCleanupPhaseReadBoundsFixture, unittest.TestCase):
    def phase_owner(self, uow):
        name = "control_plane_kit_operations.postgres.configuration_cleanup_phase_read_bounds"
        self.assertIsNotNone(find_spec(name), "#1941 fixed phase read-bound enforcement is missing")
        module = import_module(name)
        self.assertTrue(hasattr(module, "_CleanupPhaseReadBoundsOwner"),
            "#1941 fixed phase read-bound enforcement is missing")
        return module._CleanupPhaseReadBoundsOwner(uow)

    def capture_phase(self, owner, guard, prefix):
        return owner.capture(guard, prefix, self.plan,
            intent_identity=self.identity, prospective_intent=self.intent)

    @contextmanager
    def prepared_phase(self):
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            self.read_chain(uow, guard, prefix, read, observed)
            owner = self.phase_owner(uow)
            issued = self.capture_phase(owner, guard, prefix)
            yield uow, guard, prefix, read, observed, owner, issued

    def mutate(self, read, sql, params):
        # Explicit rolled-back fault injection; its acknowledgement is charged.
        self.assertEqual(read.query(sql + " RETURNING 1", params,
            records=1, octets=1, cells=1), [(1,)])

    def scope_read(self, uow, prefix, read):
        return _ExecutionScopeStorage(uow.stores.connection, read).verify(prefix.request.identity)

    def raw_binding_read(self, uow):
        return PostgresStoreBundle(uow.stores.connection).graphs.receiver_bindings(
            "workspace-a", self.plan.base_graph_id, self.plan.base_realized_projection_id)

    def test_each_fixed_consumer_enforces_later_width_before_full_transport(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        a = self.companion_acceptance
        request_id = a.action.payload["execution_request_id"]
        source = self.source_identity
        source_key = (source.run_id.value, source.activity_id, source.attempt, "settings")
        # Fixed test cases, not a production reader registry. Every corruption
        # is below the ordinary transport cap and is rolled back after its case.
        json_growth = lambda table, column, where, params, size=32768: (
            "UPDATE " + table + " SET " + column + "=jsonb_set(" + column +
            ",'{phase_bound_fault}'::text[],to_jsonb(repeat('x',%s))) WHERE " + where,
            (size, *params), size)
        text_growth = lambda table, column, where, params, size=1024: (
            "UPDATE " + table + " SET " + column + "=repeat('x',%s) WHERE " + where,
            (size, *params), size)
        plan_growth = json_growth("cpk_activity_plans", "payload", "plan_id=%s", (a.plan_id,))
        graph_growth = json_growth("cpk_graph_versions", "metadata", "graph_id=%s", (a.from_authored_graph_id,), 65536)
        projection_growth = json_growth("cpk_realized_graph_projections", "graph_descriptor", "projection_id=%s",
            (a.from_realized_projection_id,), 65536)
        request_growth = text_growth("cpk_execution_requests", "requested_by", "request_id=%s", (request_id,))
        run_growth = json_growth("cpk_activity_runs", "metadata", "run_id=%s", (a.run_id,))
        action_growth = json_growth("cpk_operation_actions", "payload", "action_id=%s", (a.action.action_id,))
        event_growth = json_growth("cpk_activity_events", "payload", "event_id=%s", (a.event.event_id,))
        ref_growth = ("UPDATE cpk_effect_configuration_refs SET ref_preimage=%s "
            "WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", (b"x"*4000, *source_key), 4000)
        cases = (
            ("history-plan-getter", "cpk_activity_plans", 9, None, plan_growth, "plan"),
            ("history-plan-scope", "cpk_activity_plans", 9, None, plan_growth, "verify"),
            ("history-base-graph-getter", "cpk_graph_versions", 6, None, graph_growth, "graph"),
            ("history-base-graph-scope", "cpk_graph_versions", 6, None, graph_growth, "verify"),
            ("history-base-projection-getter", "cpk_realized_graph_projections", 6, None, projection_growth, "projection"),
            ("history-base-projection-scope", "cpk_realized_graph_projections", 6, None, projection_growth, "verify"),
            ("raw-graph", "cpk_graph_versions", 6, None,
                json_growth("cpk_graph_versions", "metadata", "graph_id=%s", (self.plan.base_graph_id,), 65536), "bindings"),
            ("raw-projection", "cpk_realized_graph_projections", 6, None,
                json_growth("cpk_realized_graph_projections", "graph_descriptor", "projection_id=%s",
                    (self.plan.base_realized_projection_id,), 65536), "bindings"),
            ("origin-action", "cpk_operation_actions", 5, None,
                json_growth("cpk_operation_actions", "payload", "action_id=%s",
                    (self.companion_origin.introducing_action_id,)), "origin"),
            ("acceptance-action", "cpk_operation_actions", 5, None, action_growth, "acceptance"),
            ("manifest-action", "cpk_operation_actions", 5, None, action_growth, "originals"),
            ("manifest-event", "cpk_activity_events", 5, None, event_growth, "originals"),
            ("history-request-getter", "cpk_execution_requests", 5, None, request_growth, "request"),
            ("history-request-scope", "cpk_execution_requests", 5, None, request_growth, "scope-request"),
            ("history-run-getter", "cpk_activity_runs", 9, None, run_growth, "run"),
            ("history-session-getter", "cpk_operation_sessions", 3, None,
                text_growth("cpk_operation_sessions", "title", "session_id=%s", (a.action.session_id,)), "session"),
            ("cleanup-scopes", "cpk_execution_receiver_scopes", 4, "cleanup-scopes",
                text_growth("cpk_execution_receiver_scopes", "runtime_id", "request_id=%s",
                    (self.intent.source.request_id,), 100), "cleanup-verify"),
            ("acceptance-scopes", "cpk_execution_receiver_scopes", 4, "acceptance-scopes",
                text_growth("cpk_execution_receiver_scopes", "runtime_id", "request_id=%s", (request_id,), 100), "verify"),
            ("acceptance-runs", "cpk_activity_runs", 9, "acceptance-runs", run_growth, "runs"),
            ("acceptance-events", "cpk_activity_events", 5, "acceptance-events", event_growth, "events"),
            ("advancement-actions", "cpk_operation_actions", 5, "advancement-actions", action_growth, "actions"),
            ("allocation-refs", "cpk_effect_configuration_refs", 8, "allocation-refs", ref_growth, "allocation"),
            ("invocation-refs", "cpk_effect_configuration_refs", 8, "invocation-refs", ref_growth, "completion"),
            ("allocation-claims", "cpk_configuration_claims", 3, "allocation-claims",
                text_growth("cpk_configuration_claims", "artifact_id",
                    "(run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", source_key, 32), "allocation"),
            ("current-slots", "cpk_configuration_accepted_slots", 1, "current-slots",
                text_growth("cpk_configuration_accepted_slots", "node_id",
                    "(workspace_id,pinned_revision,runtime_id,node_id,artifact_id) IN "
                    "(SELECT workspace_id,pinned_revision,runtime_id,node_id,artifact_id "
                    "FROM cpk_configuration_accepted_slots WHERE workspace_id=%s AND pinned_revision=%s "
                    "ORDER BY runtime_id,node_id,artifact_id LIMIT 1)",
                    ("workspace-a", a.desired_graph_revision)), "manifest"),
        )
        for name, table, column, role, growth, consumer in cases:
            with self.subTest(role=name), self.prepared_phase() as (uow, guard, prefix, read, observed, owner, issued):
                stores = uow.stores
                # Obtain original identity via the existing owner, prior to
                # corruption. This is metered semantic input, not a width probe.
                old_request = stores.execution.get_request(request_id)
                storage = _ExecutionScopeStorage(stores.connection, type(read)(stores.connection))
                sql, params, minimum = growth
                self.mutate(read, sql, params)
                offset = len(observed["queries"])
                def consume():
                    if consumer == "plan": return stores.activity_history.get_plan(a.plan_id)
                    if consumer == "graph": return stores.graphs.get(a.from_authored_graph_id)
                    if consumer == "projection": return stores.realized_graphs.get(a.from_realized_projection_id)
                    if consumer == "request": return stores.execution.get_request(request_id)
                    if consumer == "scope-request": return storage.request("workspace-a", request_id)
                    if consumer == "run": return stores.execution.get_run(a.run_id)
                    if consumer == "session": return stores.activity_history.get_session(a.action.session_id)
                    if consumer == "verify": return storage.verify(old_request.identity)
                    if consumer == "cleanup-verify": return storage.verify(prefix.request.identity)
                    if consumer == "bindings": return self.raw_binding_read(uow)
                    if consumer == "origin": return stores.graphs._require_receiver_origin_action(self.companion_origin)
                    if consumer == "acceptance": return stores.execution._receiver_acceptance_evidence((self.companion_origin,))
                    if consumer == "originals": return stores.configuration_acceptance._originals(
                        type(read)(stores.connection), a.action.action_id, a.event.event_id)
                    if consumer == "runs": return storage.runs(old_request)
                    if consumer == "events": return storage.events(a.run_id)
                    if consumer == "actions": return storage.actions(a.action.session_id, a.run_id, "advance-current-graph")
                    if consumer == "completion": return stores.configuration_completions._get(source, type(read)(stores.connection))
                    if consumer == "allocation":
                        result = stores.configuration_preparation._allocation_evidence(self.selected_ref, type(read)(stores.connection))
                        self.assertEqual(result.state, "capacity", "ordinary semantic unavailability is not cap enforcement")
                        return
                    if consumer == "manifest": return stores.configuration_acceptance._receipt_manifest(
                        "workspace-a", a.desired_graph_revision, type(read)(stores.connection))
                    self.fail("unknown fixed consumer")
                with owner.bind(issued):
                    if consumer == "allocation":
                        consume()
                    else:
                        with self.assertRaises(ValueError):
                            consume()
                attempts = observed["queries"][offset:]
                self.assertTrue(any(q["sql"] == "SELECT txid_current()" for q in attempts))
                relevant = [q for q in attempts if " FROM " + table in q["sql"]
                    and (role is None or q["role"] == role)]
                self.assertTrue(relevant, "intended fixed consumer was not reached")
                probe_index = attempts.index(relevant[0])
                self.assertGreater(probe_index, 0)
                self.assertEqual(attempts[probe_index - 1]["sql"], "SELECT txid_current()",
                    "this consumer did not apply its own charged context guard")
                self.assertFalse(any(row[column] >= minimum for q in relevant
                    if not q["sql"].startswith("SELECT octet_length(") for row in q["widths"]),
                    "offending full column crossed PostgreSQL transport before refusal")
        self.assertEqual(self.ceiling_truth(), before)

    def test_fk_constrained_introduction_header_and_binding_widths_are_enforced(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        a = self.companion_acceptance
        for role in ("introduction", "receipt-header", "bindings", "acceptance-bindings"):
            with self.subTest(role=role), self.prepared_phase() as (uow, guard, prefix, read, observed, owner, issued):
                if role in ("introduction", "receipt-header"):
                    original_action = (self.companion_origin.introducing_action_id
                        if role == "introduction" else a.action.action_id)
                    long_action = "phase-fault-" + "x"*1012
                    # Explicit negative history, not a new accepted operation.
                    # A real referenced row keeps immediate FKs valid while the
                    # target column grows beyond its captured width.
                    self.mutate(read, "INSERT INTO cpk_operation_actions "
                        "(action_id,session_id,ordinal,action_type,actor_id,payload,created_at,idempotency_key,"
                        "intent_fingerprint,advancement_workspace_id,advancement_request_id,advancement_plan_id,"
                        "advancement_run_id,advancement_revision) SELECT %s,session_id,ordinal+100000,action_type,"
                        "actor_id,payload,created_at,NULL,intent_fingerprint,advancement_workspace_id,"
                        "advancement_request_id,advancement_plan_id,advancement_run_id,advancement_revision "
                        "FROM cpk_operation_actions WHERE action_id=%s", (long_action, original_action))
                    if role == "introduction":
                        self.mutate(read, "UPDATE cpk_graph_receiver_introductions SET introducing_action_id=%s "
                            "WHERE workspace_id='workspace-a' AND receiver_id=%s", (long_action, "a"*32))
                        table, column = "cpk_graph_receiver_introductions", 7
                    else:
                        self.mutate(read, "UPDATE cpk_configuration_acceptances SET action_id=%s "
                            "WHERE workspace_id='workspace-a' AND pinned_revision=%s",
                            (long_action, a.desired_graph_revision))
                        table, column = "cpk_configuration_acceptances", 5
                    minimum = len(long_action)
                else:
                    new_receiver, long_node = "a"*31 + "b", "x"*1024
                    # The two original-binding FKs are deferred. The immediate
                    # binding-scope FK references this real negative fixture row.
                    self.mutate(read, "INSERT INTO cpk_graph_receiver_introductions "
                        "(workspace_id,receiver_id,runtime_id,node_id,provider_socket_name,introducing_graph_id,"
                        "introducing_realized_projection_id,introducing_action_id,introducing_session_id,"
                        "introducing_draft_id,first_accepted_action_id,first_accepted_session_id,retired_action_id,retired_session_id) "
                        "SELECT workspace_id,%s,runtime_id,%s,provider_socket_name,introducing_graph_id,"
                        "introducing_realized_projection_id,introducing_action_id,introducing_session_id,"
                        "introducing_draft_id,first_accepted_action_id,first_accepted_session_id,retired_action_id,retired_session_id "
                        "FROM cpk_graph_receiver_introductions WHERE workspace_id='workspace-a' AND receiver_id=%s",
                        (new_receiver, long_node, "a"*32))
                    self.mutate(read, "UPDATE cpk_graph_receiver_bindings SET receiver_id=%s,node_id=%s "
                        "WHERE workspace_id='workspace-a' AND graph_id=%s AND realized_projection_id=%s AND receiver_id=%s",
                        (new_receiver, long_node, self.plan.base_graph_id, self.plan.base_realized_projection_id, "a"*32))
                    table, column, minimum = "cpk_graph_receiver_bindings", 4, len(long_node)
                offset = len(observed["queries"])
                with owner.bind(issued), self.assertRaises(ValueError):
                    if role == "introduction":
                        uow.stores.graphs.receiver_introduction("workspace-a", "a"*32)
                    elif role == "receipt-header":
                        uow.stores.configuration_acceptance._receipt_context(
                            "workspace-a", a.desired_graph_revision, type(read)(uow.stores.connection))
                    elif role == "acceptance-bindings":
                        uow.stores.execution._receiver_acceptance_evidence((self.companion_origin,))
                    else:
                        self.raw_binding_read(uow)
                attempts = observed["queries"][offset:]
                relevant = [q for q in attempts if " FROM " + table in q["sql"]]
                self.assertTrue(relevant)
                probe_index = attempts.index(relevant[0])
                self.assertGreater(probe_index, 0)
                self.assertEqual(attempts[probe_index - 1]["sql"], "SELECT txid_current()")
                self.assertFalse(any(row[column] >= minimum for q in relevant
                    if not q["sql"].startswith("SELECT octet_length(") for row in q["widths"]),
                    "FK-valid oversized column crossed transport before rejection")
        self.assertEqual(self.ceiling_truth(), before)

    def test_capture_counts_have_inner_caps_for_every_fixed_collection(self):
        self.prepare_ceiling_premise()
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            self.read_chain(uow, guard, prefix, read, observed)
            owner = self.phase_owner(uow)
            offset = len(observed["queries"])
            self.capture_phase(owner, guard, prefix)
            counts = [q for q in observed["queries"][offset:]
                if re.match(r"SELECT\s+COUNT\(\*\)", q["sql"], re.I)]
            self.assertEqual(len(counts), 10)
            expected = {
                "cleanup-scopes": (1025, "scope_ordinal"),
                "acceptance-scopes": (1025, "scope_ordinal"),
                "acceptance-runs": (257, "attempt"),
                "acceptance-events": (8193, "ordinal"),
                "advancement-actions": (2, "action_id"),
                "bindings": (2, "node_id"),
                "allocation-refs": (65, "run_id"),
                "allocation-claims": (65, "run_id"),
                "invocation-refs": (33, "artifact_id"),
                "current-slots": (257, "runtime_id"),
            }
            seen = set()
            for query in counts:
                normalized = " ".join(query["sql"].split())
                self.assertRegex(normalized.upper(),
                    r"^SELECT COUNT\(\*\) FROM \(SELECT 1 FROM .+ WHERE .+ ORDER BY .+ LIMIT (?:%s|[0-9]+)\)".replace("%s", "%S"))
                role = query["role"]
                self.assertIn(role, expected)
                self.assertNotIn(role, seen)
                maximum, order = expected[role]
                self.assertIn(order, normalized)
                literal = re.search(r"LIMIT ([0-9]+)\)", normalized)
                limit = int(literal.group(1)) if literal else query["limit"]
                self.assertIsNotNone(limit)
                self.assertGreater(limit, 0)
                self.assertLessEqual(limit, maximum)
                seen.add(role)
            self.assertEqual(seen, set(expected))

    def test_collection_growth_shrink_and_same_count_key_substitution_refuse(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        for fault in ("growth", "shrink", "replacement"):
            with self.subTest(fault=fault), self.prepared_phase() as (uow, guard, prefix, read, observed, owner, issued):
                request_id = prefix.request.identity.request_id
                if fault == "growth":
                    self.mutate(read, "INSERT INTO cpk_execution_receiver_scopes "
                        "(request_id,workspace_id,scope_ordinal,scope_kind,runtime_id,node_id) "
                        "VALUES (%s,'workspace-a',1,'node','docker','api')", (request_id,))
                elif fault == "shrink":
                    self.mutate(read, "DELETE FROM cpk_execution_receiver_scopes WHERE request_id=%s", (request_id,))
                else:
                    self.mutate(read, "UPDATE cpk_execution_receiver_scopes SET scope_ordinal=1 WHERE request_id=%s",
                        (request_id,))
                with owner.bind(issued), self.assertRaises(ValueError):
                    self.scope_read(uow, prefix, read)
        self.assertEqual(self.ceiling_truth(), before)

    def test_count_to_key_growth_is_detected_without_transporting_extra_members(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            self.read_chain(uow, guard, prefix, read, observed)
            owner = self.phase_owner(uow)
            injected = []
            def after_count(entry, row):
                if re.match(r"SELECT\s+COUNT\(\*\)", entry["sql"], re.I) and entry["role"] == "cleanup-scopes":
                    observed["after_row"] = None
                    self.mutate(read, "INSERT INTO cpk_execution_receiver_scopes "
                        "(request_id,workspace_id,scope_ordinal,scope_kind,runtime_id,node_id) "
                        "VALUES (%s,'workspace-a',1,'node','docker','api')", (prefix.request.identity.request_id,))
                    injected.append(True)
            observed["after_row"] = after_count
            with self.assertRaises(ValueError):
                self.capture_phase(owner, guard, prefix)
            self.assertEqual(injected, [True])
        self.assertEqual(self.ceiling_truth(), before)

    def test_event_collection_uses_sum_of_column_maxima_and_real_peak(self):
        self.prepare_ceiling_premise()
        with self.prepared_phase() as (uow, guard, prefix, read, observed, owner, issued):
            run_id = self.companion_acceptance.event.run_id
            with owner.bind(issued):
                before, offset = read.used.accounted_bytes, len(observed["queries"])
                events = _ExecutionScopeStorage(uow.stores.connection, read).events(run_id)
                segment = observed["queries"][offset:]
                full = [q for q in segment if q["sql"].startswith("SELECT CASE WHEN")
                    and " FROM cpk_activity_events " in q["sql"]]
                self.assertEqual(len(full), 1)
                rows = [row[:-1] for row in full[0]["widths"]]
                self.assertEqual(len(rows), len(events))
                self.assertGreater(len(rows), 1)
                maxima = tuple(max(row[i] for row in rows) for i in range(6))
                self.assertGreater(sum(maxima), max(map(sum, rows)),
                    "fixture must exercise different rows maximizing different columns")
                preceding = segment[:segment.index(full[0])]
                actual_prefix = sum(256 + sum(128 + 16*len(row) + sum(row)
                    for row in query["widths"]) for query in preceding)
                n = len(rows) + 1
                expected_reservation = 256 + n * (128 + 16*7 + sum(maxima) + 1)
                r, b, c, s = full[0]["peak"]
                self.assertEqual(b + 128*r + 16*c + 256*s,
                    before + actual_prefix + expected_reservation)
                print("#1941 event column maxima", dict(rows=len(rows), maxima=maxima,
                    maxima_sum=sum(maxima), maximum_row_sum=max(map(sum, rows))))

    def test_missing_reciprocal_claim_cannot_hide_in_captured_whole_invocation(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.prepared_phase() as (uow, guard, prefix, read, observed, owner, issued):
            identity = self.source_identity
            self.mutate(read, "DELETE FROM cpk_configuration_claims "
                "WHERE run_id=%s AND activity_id=%s AND attempt=%s AND artifact_id='settings'",
                (identity.run_id.value, identity.activity_id, identity.attempt))
            with owner.bind(issued):
                fresh = type(read)(uow.stores.connection)
                allocation = uow.stores.configuration_preparation._allocation_evidence(self.selected_ref, fresh)
                self.assertEqual(allocation.state, "unavailable")
                with self.assertRaises(ValueError):
                    uow.stores.configuration_completions._get(identity, type(read)(uow.stores.connection))
        self.assertEqual(self.ceiling_truth(), before)

    def test_same_width_receiver_origin_corruption_still_reaches_cold_proof(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.prepared_phase() as (uow, guard, prefix, read, observed, owner, issued):
            # Preserve the column width while breaking original provenance.
            old = self.companion_origin.introducing_graph_id
            changed = ("x" if old[0] != "x" else "y") + old[1:]
            self.mutate(read, "UPDATE cpk_operation_actions SET payload=jsonb_set(payload, "
                "'{desired_graph_id}',to_jsonb(%s::text)) WHERE action_id=%s",
                (changed, self.companion_origin.introducing_action_id))
            from control_plane_kit_operations.receiver_lifecycle import _validate_receiver_execution
            with owner.bind(issued), self.assertRaises(ValueError):
                _validate_receiver_execution(uow.stores, prefix.request, guard)
        self.assertEqual(self.ceiling_truth(), before)

    def test_phase_scope_exit_restores_ordinary_raw_material_defaults(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.prepared_phase() as (uow, guard, prefix, read, observed, owner, issued):
            expected = self.raw_binding_read(uow)
            self.mutate(read, "UPDATE cpk_graph_versions SET metadata=%s::jsonb WHERE graph_id=%s",
                (json.dumps({"fault-injected": "x"*65536}), self.plan.base_graph_id))
            with owner.bind(issued), self.assertRaises(_Capacity):
                self.raw_binding_read(uow)
            offset = len(observed["queries"])
            self.assertEqual(self.raw_binding_read(uow), expected)
            self.assertFalse(any(q["sql"] == "SELECT txid_current()" for q in observed["queries"][offset:]))
        self.assertEqual(self.ceiling_truth(), before)

    def test_unmatched_history_keeps_ordinary_limits_inside_phase_binding(self):
        self.prepare_ceiling_premise()
        with self.prepared_phase() as (uow, guard, prefix, read, observed, owner, issued):
            # The old source's full event history is not an acceptance-history
            # role. Its terminal point evidence does not widen that role set.
            with owner.bind(issued):
                offset = len(observed["queries"])
                events = _ExecutionScopeStorage(uow.stores.connection, read).events(self.source_identity.run_id.value)
                segment = observed["queries"][offset:]
                lengths = [q for q in segment if q["sql"].startswith("SELECT octet_length(")
                    and " FROM cpk_activity_events " in q["sql"]]
                self.assertEqual(len(lengths), 1)
                self.assertGreater(lengths[0]["limit"], len(events) + 1)
                self.assertFalse(any(q["sql"] == "SELECT txid_current()" for q in segment))

    def test_foreign_owner_accounting_task_thread_transaction_and_connection_refuse(self):
        self.prepare_ceiling_premise()
        from control_plane_kit_operations._configuration_preparation import _configuration_accounting
        from concurrent.futures import ThreadPoolExecutor
        import asyncio
        for fault in ("owner", "accounting", "inactive", "task", "thread", "transaction", "connection"):
            with self.subTest(fault=fault), self.prepared_phase() as (uow, guard, prefix, read, observed, owner, issued):
                if fault == "owner":
                    with self.assertRaises(_Unavailable):
                        with self.phase_owner(uow).bind(issued):
                            self.fail("foreign owner accepted a phase value")
                    continue
                with owner.bind(issued):
                    if fault in ("accounting", "inactive"):
                        with _configuration_accounting("foreign", active=fault != "inactive"):
                            with self.assertRaises(_Unavailable):
                                self.raw_binding_read(uow)
                    elif fault == "connection":
                        with self.unit_of_work() as other, self.assertRaises(_Unavailable):
                            self.raw_binding_read(other)
                    elif fault == "transaction":
                        uow.stores.connection.commit()
                        with self.assertRaises(_Unavailable):
                            self.raw_binding_read(uow)
                    elif fault == "thread":
                        def changed_thread():
                            with self.assertRaises(_Unavailable):
                                self.raw_binding_read(uow)
                        with ThreadPoolExecutor(max_workers=1) as executor:
                            executor.submit(copy_context().run, changed_thread).result()
                    else:
                        async def changed_task():
                            with self.assertRaises(_Unavailable):
                                self.raw_binding_read(uow)
                        asyncio.run(changed_task())

    def test_probe_to_fetch_growth_and_null_semantics_remain_guarded(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.prepared_phase() as (uow, guard, prefix, read, observed, owner, issued):
            with owner.bind(issued):
                original, derived = self.scope_read(uow, prefix, read)
                self.assertEqual(original[0], self.plan)
                self.assertEqual(len(derived.scopes), 1)
                self.assertIsNone(derived.scopes[0].node_id)
                self.assertIsNone(uow.stores.graphs.receiver_introduction(
                    "workspace-a", "a"*32).introducing_draft_id)
                injected = []
                def after_probe(entry, row):
                    if entry["sql"].startswith("SELECT octet_length(") and (
                            " FROM cpk_graph_versions WHERE workspace_id=%s AND graph_id=%s" in entry["sql"]):
                        observed["after_row"] = None
                        self.mutate(read, "UPDATE cpk_graph_versions SET metadata=%s::jsonb WHERE graph_id=%s",
                            (json.dumps({"fault-injected": "x"*65536}), self.plan.base_graph_id))
                        injected.append(True)
                observed["after_row"] = after_probe
                observed["largest_cell"] = 0
                with self.assertRaises(ValueError):
                    self.raw_binding_read(uow)
                self.assertEqual(injected, [True])
                self.assertLess(observed["largest_cell"], 65536)
        self.assertEqual(self.ceiling_truth(), before)

    def test_fixed_phase_issuance_rejects_foreign_plan_or_prospective_identity(self):
        self.prepare_ceiling_premise()
        for fault in ("plan", "intent", "identity"):
            with self.subTest(fault=fault), self.phase_premise() as (uow, guard, prefix, read, observed):
                self.read_chain(uow, guard, prefix, read, observed)
                owner = self.phase_owner(uow)
                plan, identity, intent = self.plan, self.identity, self.intent
                if fault == "plan":
                    plan = replace(plan, plan_id="foreign-phase-plan")
                elif fault == "identity":
                    identity = replace(identity, activity_id="foreign-phase-activity")
                else:
                    intent = replace(intent, source=replace(intent.source, request_id="foreign-phase-request"))
                with self.assertRaises(ValueError):
                    owner.capture(guard, prefix, plan, intent_identity=identity, prospective_intent=intent)

    def test_unissued_nested_and_exception_exit_do_not_leak_phase_authority(self):
        self.prepare_ceiling_premise()
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            self.read_chain(uow, guard, prefix, read, observed)
            owner = self.phase_owner(uow)
            with self.assertRaises(_Unavailable):
                with owner.bind(None):
                    self.fail("empty owner accepted an unissued phase")
            issued = self.capture_phase(owner, guard, prefix)
            with self.assertRaisesRegex(RuntimeError, "phase-test-exit"):
                with owner.bind(issued):
                    with self.assertRaises(_Unavailable):
                        with owner.bind(issued):
                            self.fail("nested phase binding was accepted")
                    self.raw_binding_read(uow)
                    raise RuntimeError("phase-test-exit")
            with self.assertRaises(_Unavailable):
                with owner.bind(issued):
                    self.fail("exception-exited phase value was reusable")
            offset = len(observed["queries"])
            self.raw_binding_read(uow)
            self.assertFalse(any(q["sql"] == "SELECT txid_current()" for q in observed["queries"][offset:]))

    def test_00_existing_receiver_read_rehearsal_precedes_missing_bounds(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            self.read_chain(uow, guard, prefix, read, observed)
        self.assertEqual(self.ceiling_truth(), before)

    def test_historical_point_entrances_refuse_foreign_context_before_legacy_sql(self):
        self.prepare_ceiling_premise()
        from control_plane_kit_operations._configuration_preparation import _configuration_accounting
        from concurrent.futures import ThreadPoolExecutor
        import asyncio
        with self.prepared_phase() as (uow, guard, prefix, read, observed, owner, issued):
            a, stores = self.companion_acceptance, uow.stores
            consumers = (
                lambda: stores.activity_history.get_session(a.action.session_id),
                lambda: stores.activity_history.get_plan(a.plan_id),
                lambda: stores.execution.get_request(a.action.payload["execution_request_id"]),
                lambda: stores.execution.get_run(a.run_id),
                lambda: stores.graphs._require_receiver_origin_action(self.companion_origin),
            )
            def refuse_all():
                offset = len(observed["queries"])
                for consume in consumers:
                    with self.assertRaises(_Unavailable):
                        consume()
                self.assertEqual(len(observed["queries"]), offset,
                    "foreign context reached SQL before confinement refusal")
            with owner.bind(issued):
                offset = len(observed["queries"])
                with self.assertRaises(_Unavailable):
                    _ExecutionScopeStorage(stores.connection).request("workspace-a", a.action.payload["execution_request_id"])
                with _configuration_accounting("foreign-read-object"):
                    foreign_read = type(read)(stores.connection)
                with self.assertRaises(_Unavailable):
                    _ExecutionScopeStorage(stores.connection, foreign_read).request(
                        "workspace-a", a.action.payload["execution_request_id"])
                self.assertEqual(len(observed["queries"]), offset)
                for active in (False, True):
                    with _configuration_accounting("foreign-point-context", active=active):
                        refuse_all()
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(copy_context().run, refuse_all).result()
                async def other_task():
                    refuse_all()
                asyncio.run(other_task())

    def test_matching_warm_receipt_cache_retains_its_charged_context_guard(self):
        self.prepare_ceiling_premise()
        from control_plane_kit_operations._configuration_preparation import _configuration_accounting
        for fault in ("accounting", "inactive", "transaction"):
            with self.subTest(fault=fault), self.prepared_phase() as (uow, guard, prefix, read, observed, owner, issued):
                fresh = type(read)(uow.stores.connection)
                revision = self.companion_acceptance.desired_graph_revision
                with owner.bind(issued):
                    receipt = uow.stores.configuration_acceptance._receipt_context("workspace-a", revision, fresh)
                    before, offset = read.used.accounted_bytes, len(observed["queries"])
                    self.assertEqual(uow.stores.configuration_acceptance._receipt_context("workspace-a", revision, fresh), receipt)
                    self.assertEqual([q["sql"] for q in observed["queries"][offset:]], ["SELECT txid_current()"])
                    self.assertEqual(read.used.accounted_bytes - before, 420)
                    offset = len(observed["queries"])
                    if fault == "transaction":
                        uow.stores.connection.commit()
                        with self.assertRaises(_Unavailable):
                            uow.stores.configuration_acceptance._receipt_context("workspace-a", revision, fresh)
                        self.assertEqual([q["sql"] for q in observed["queries"][offset:]], ["SELECT txid_current()"])
                    else:
                        with _configuration_accounting("foreign-cache-context", active=fault != "inactive"):
                            with self.assertRaises(_Unavailable):
                                uow.stores.configuration_acceptance._receipt_context("workspace-a", revision, fresh)
                        self.assertEqual(len(observed["queries"]), offset)

    def test_captured_parent_refuses_existing_uncaptured_child_before_payload_transport(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        for parent in ("receipt", "origin", "historical-plan", "original-plan"):
            with self.subTest(parent=parent), self.prepared_phase() as (uow, guard, prefix, read, observed, owner, issued):
                a, stores = self.companion_acceptance, uow.stores
                if parent in ("receipt", "origin"):
                    old = a.action.action_id if parent == "receipt" else self.companion_origin.introducing_action_id
                    foreign = ("z" if old[0] != "z" else "y") + old[1:]
                    self.mutate(read, "INSERT INTO cpk_operation_actions "
                        "(action_id,session_id,ordinal,action_type,actor_id,payload,created_at,idempotency_key,"
                        "intent_fingerprint,advancement_workspace_id,advancement_request_id,advancement_plan_id,"
                        "advancement_run_id,advancement_revision) SELECT %s,session_id,ordinal+100000,action_type,actor_id,"
                        "jsonb_set(payload,'{phase_foreign}'::text[],to_jsonb(repeat('x',32768))),created_at,NULL,"
                        "intent_fingerprint,advancement_workspace_id,advancement_request_id,advancement_plan_id,"
                        "advancement_run_id,advancement_revision FROM cpk_operation_actions WHERE action_id=%s", (foreign, old))
                    if parent == "receipt":
                        self.mutate(read, "UPDATE cpk_configuration_acceptances SET action_id=%s "
                            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (foreign, a.desired_graph_revision))
                        consume = lambda: stores.configuration_acceptance._receipt_context(
                            "workspace-a", a.desired_graph_revision, type(read)(stores.connection))
                    else:
                        self.mutate(read, "UPDATE cpk_graph_receiver_introductions SET introducing_action_id=%s "
                            "WHERE workspace_id='workspace-a' AND receiver_id=%s", (foreign, self.companion_origin.receiver_id))
                        consume = lambda: stores.graphs.receiver_introduction("workspace-a", self.companion_origin.receiver_id)
                    forbidden_table = "cpk_operation_actions"
                else:
                    old = a.from_authored_graph_id if parent == "historical-plan" else self.plan.base_graph_id
                    foreign = ("z" if old[0] != "z" else "y") + old[1:]
                    self.mutate(read, "INSERT INTO cpk_graph_versions "
                        "(graph_id,workspace_id,version,graph_descriptor,created_by,created_at,metadata) "
                        "SELECT %s,workspace_id,version+100000,graph_descriptor,created_by,created_at,"
                        "jsonb_set(metadata,'{phase_foreign}'::text[],to_jsonb(repeat('x',65536))) "
                        "FROM cpk_graph_versions WHERE graph_id=%s", (foreign, old))
                    plan_id = a.plan_id if parent == "historical-plan" else self.plan.plan_id
                    request_id = a.action.payload["execution_request_id"] if parent == "historical-plan" else prefix.request.identity.request_id
                    request = stores.execution.get_request(request_id)
                    if parent == "original-plan":
                        from control_plane_kit_operations.configuration_cleanup import ConfigurationCleanupProposalCodec
                        from control_plane_kit_operations.plan_derivation import encode_stored_activity_plan
                        document = self.plan.cleanup_proposal.descriptor()
                        document["context"]["base_graph_id"] = foreign
                        document["context"]["current_occurrence"]["graph_id"] = foreign
                        proposal = ConfigurationCleanupProposalCodec().decode(document)
                        payload = encode_stored_activity_plan(self.plan.plan, profile=self.plan.derivation_profile,
                            cleanup_proposal=proposal)
                        self.mutate(read, "UPDATE cpk_activity_plans SET base_graph_id=%s,payload=%s::jsonb WHERE plan_id=%s",
                            (foreign, json.dumps(payload), plan_id))
                    else:
                        self.mutate(read, "UPDATE cpk_activity_plans SET base_graph_id=%s WHERE plan_id=%s", (foreign, plan_id))
                    # It is a readable retained plan pointing at a real row,
                    # not an import/decoder/FK failure before the child entrance.
                    self.assertEqual(stores.activity_history.get_plan(plan_id).base_graph_id, foreign)
                    consume = lambda: _ExecutionScopeStorage(stores.connection, type(read)(stores.connection)).verify(request.identity)
                    forbidden_table = "cpk_graph_versions"
                offset = len(observed["queries"])
                with owner.bind(issued), self.assertRaises(ValueError):
                    consume()
                segment = observed["queries"][offset:]
                self.assertFalse(any(" FROM " + forbidden_table in q["sql"] for q in segment),
                    "captured parent reached an uncaptured child query")
        self.assertEqual(self.ceiling_truth(), before)

    def test_capture_refuses_a_second_uow_sharing_the_original_connection(self):
        self.prepare_ceiling_premise()
        from control_plane_kit_operations.postgres import PostgresUnitOfWork
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            # Deliberately give a second owner the same physical connection.
            # The outer UoW remains its sole rollback/close owner in this test.
            other = PostgresUnitOfWork(lambda: uow.stores.connection).__enter__()
            owner = self.phase_owner(other)
            offset = len(observed["queries"])
            with self.assertRaises(_Unavailable):
                self.capture_phase(owner, guard, prefix)
            self.assertEqual(len(observed["queries"]), offset)

    def test_capture_refuses_constructor_context_change_before_sql(self):
        self.prepare_ceiling_premise()
        from control_plane_kit_operations._configuration_preparation import _configuration_accounting
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            owner = self.phase_owner(uow)
            offset = len(observed["queries"])
            with _configuration_accounting("foreign-capture"):
                with self.assertRaises(_Unavailable):
                    self.capture_phase(owner, guard, prefix)
            self.assertEqual(len(observed["queries"]), offset)

    def test_capture_cannot_borrow_original_scope_for_another_valid_plan_or_attempt(self):
        self.prepare_ceiling_premise()
        from datetime import datetime, timedelta, timezone
        from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
        from control_plane_kit_core.operations import RunId
        from control_plane_kit_operations.configuration_cleanup import configuration_cleanup_proposal_fingerprint
        from control_plane_kit_operations.records import AdmittedRun
        from control_plane_kit_operations.effect_run_prefix import _lock_effect_run_prefix
        from tests.receiver_scope_history_fixture import insert_recorded_request
        foreign_plan = replace(self.plan, plan_id="foreign-phase-plan")
        foreign_identity = replace(self.identity, run_id=RunId("foreign-phase-run"))
        foreign_intent = replace(self.intent, source=replace(self.intent.source,
            request_id="foreign-phase-request", run_id=foreign_identity.run_id, plan_id=foreign_plan.plan_id))
        with self.unit_of_work() as setup:
            stores = setup.stores
            stores.activity_history.add_plan(foreign_plan)
            approval = replace(self.approval, request_id="foreign-phase-approval",
                subject=ActivityPlanApprovalSubject(foreign_plan.plan_id,
                    proposal_fingerprint=configuration_cleanup_proposal_fingerprint(foreign_plan.cleanup_proposal)),
                idempotency_key=None, intent_fingerprint=None)
            decision = replace(self.decision, decision_id="foreign-phase-decision", request_id=approval.request_id,
                idempotency_key=None, intent_fingerprint=None)
            stores.activity_history.add_approval_request(approval)
            stores.activity_history.add_approval_decision(decision)
            now = self.now()
            insert_recorded_request(stores.connection, request_id=foreign_intent.source.request_id,
                workspace_id="workspace-a", session_id=foreign_plan.session_id, plan_id=foreign_plan.plan_id,
                approval_request_id=approval.request_id, approval_decision_id=decision.decision_id,
                idempotency_key="foreign-phase-recorded", intent_fingerprint="recorded-history-only",
                requested_at=now, status="claimed", claim_worker_id="recorded-worker", claim_generation=1,
                claimed_at=now, lease_expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
            run = stores.execution.get_run(self.identity.run_id.value)
            stores.execution._add_run(replace(run, run_id=foreign_identity.run_id.value,
                plan_id=foreign_plan.plan_id, admission=AdmittedRun(foreign_intent.source.request_id)))
            setup.commit()
        for fault in ("plan-and-intent", "attempt"):
            with self.subTest(fault=fault), self.phase_premise() as (uow, guard, prefix, read, observed):
                plan, identity, intent = self.plan, replace(self.identity, attempt=2), self.intent
                if fault == "plan-and-intent":
                    request = uow.stores.execution.get_request_for_update(foreign_intent.source.request_id)
                    prefix = _lock_effect_run_prefix(uow, request, foreign_identity.run_id.value, latest_required=True)
                    plan, identity, intent = foreign_plan, foreign_identity, foreign_intent
                owner = self.phase_owner(uow)
                offset = len(observed["queries"])
                with self.assertRaises(_Unavailable):
                    owner.capture(guard, prefix, plan, intent_identity=identity, prospective_intent=intent)
                self.assertFalse(any(" FROM cpk_activity_plans " in q["sql"]
                    or " FROM cpk_effect_attempt_intents " in q["sql"] for q in observed["queries"][offset:]),
                    "mismatched original scope fetched uncaptured plan or intent")
                with self.assertRaises(_Unavailable):
                    with owner.bind(None):
                        self.fail("failed original correspondence issued a token")

    def test_phase_binding_reaches_independent_cold_proof_readers(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            self.read_chain(uow, guard, prefix, read, observed)
            owner = self.phase_owner(uow)
            capture_prefix, offset = read.used, len(observed["queries"])
            issued = self.capture_phase(owner, guard, prefix)
            segment = observed["queries"][offset:]
            physical = sum(256 + sum(128 + 16*len(row) + sum(row) for row in q["widths"]) for q in segment)
            self.assertGreaterEqual(read.used.accounted_bytes - capture_prefix.accounted_bytes, physical)
            self.assertEqual(read.used.statements - capture_prefix.statements, len(segment))
            peaks = [b+128*r+16*c+256*s for r,b,c,s in (q["peak"] for q in segment)]
            self.assertLessEqual(max(peaks), 16*1024*1024)
            from tests.configuration_cleanup_phase_read_bounds_fixture import _components
            print("#1941 start capture", dict(prefix=_components(capture_prefix), used=_components(read.used),
                physical_weighted_bytes=physical, maximum_reservation_bytes=max(peaks)))
            self.assertNotIn(self.plan.plan_id, repr(issued))
            with owner.bind(issued):
                self.read_chain(uow, guard, prefix, read, observed)
        self.assertEqual(self.ceiling_truth(), before)

    def test_raw_material_growth_is_rejected_before_full_transport(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            self.read_chain(uow, guard, prefix, read, observed)
            owner = self.phase_owner(uow)
            issued = self.capture_phase(owner, guard, prefix)
            self.mutate(read, "UPDATE cpk_graph_versions SET metadata=%s::jsonb WHERE graph_id=%s",
                (json.dumps({"fault-injected": "x" * 65536}), self.plan.base_graph_id))
            observed["largest_cell"] = 0
            with owner.bind(issued):
                # This independent lifecycle reader bypasses the #1939 getter.
                with self.assertRaises(_Capacity):
                    PostgresStoreBundle(uow.stores.connection).graphs.receiver_bindings(
                        "workspace-a", self.plan.base_graph_id, self.plan.base_realized_projection_id)
            self.assertLess(observed["largest_cell"], 65536)
        self.assertEqual(self.ceiling_truth(), before)

    def test_equal_copy_and_spent_phase_binding_refuse(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            self.read_chain(uow, guard, prefix, read, observed)
            owner = self.phase_owner(uow)
            issued = self.capture_phase(owner, guard, prefix)
            for invalid in (None, replace(issued)):
                with self.assertRaises(_Unavailable):
                    with owner.bind(invalid):
                        self.fail("unissued phase value entered the binding")
            with owner.bind(issued):
                self.read_chain(uow, guard, prefix, read, observed)
            with self.assertRaises(_Unavailable):
                with owner.bind(issued):
                    self.fail("spent phase value entered the binding")
        self.assertEqual(self.ceiling_truth(), before)
