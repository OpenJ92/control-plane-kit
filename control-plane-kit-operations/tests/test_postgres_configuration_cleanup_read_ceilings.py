"""#1939 strengthened original-read laws; cleanup execution remains unsupported."""
from dataclasses import replace
from importlib import import_module
from importlib.util import find_spec
import json
import unittest

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.admission import ExecutionAdmissionCommandService, ExecutionAdmissionConflict
from control_plane_kit_operations.postgres.configuration_evidence import _Capacity, _Unavailable
from control_plane_kit_operations.postgres.stores import PostgresStoreBundle
from control_plane_kit_operations.runtime_effects import (
    runtime_effect_request_for_context, _runtime_effect_intent_for_context,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.configuration_cleanup_read_ceilings_fixture import ConfigurationCleanupReadCeilingsFixture
from tests.test_runtime_effect_translation import _context


class PostgresConfigurationCleanupReadCeilingsTests(ConfigurationCleanupReadCeilingsFixture, unittest.TestCase):
    def owner(self, uow):
        name = "control_plane_kit_operations.postgres.configuration_cleanup_read_ceilings"
        self.assertIsNotNone(find_spec(name), "#1939 private cleanup-original read-ceiling issuer is missing")
        module = import_module(name)
        self.assertTrue(hasattr(module, "_CleanupOriginalReadCeilingsOwner"),
            "#1939 private cleanup-original read-ceiling issuer is missing")
        return module._CleanupOriginalReadCeilingsOwner(uow)

    def capture(self, owner, guard, prefix):
        return owner.capture(guard, prefix, self.plan,
            intent_identity=self.identity, prospective_intent=self.intent)

    def independent_reads(self, uow):
        # New store objects prove lexical propagation, not one reader's cache.
        stores = PostgresStoreBundle(uow.stores.connection)
        self.assertEqual(stores.activity_history.get_plan(self.plan.plan_id), self.plan)
        graph = stores.graphs.get(self.plan.base_graph_id)
        projection = stores.realized_graphs.get(self.plan.base_realized_projection_id)
        self.assertEqual(graph.graph_id, projection.source_authored_graph_id)
        self.assertEqual(graph.workspace_id, projection.workspace_id)
        return graph, projection

    def test_00_existing_owner_control_precedes_missing_issuer(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.read_premise() as (uow, guard, prefix, read, observed):
            self.assert_existing_premise(uow, guard, prefix)
            payload_width, graph_widths, intent_width = self.width_evidence(uow.stores.connection)
            self.assertGreater(payload_width, 0)
            self.assertGreater(intent_width, 0)
            self.assertGreater(graph_widths[0], graph_widths[1])
            footprint, transport = read.used, observed["bytes"]
            self.independent_reads(uow)
            self.assertGreater(read.used.statements, footprint.statements)
            self.assertGreaterEqual(read.used.accounted_bytes - footprint.accounted_bytes,
                observed["bytes"] - transport)
        self.assertEqual(self.ceiling_truth(), before)
        calls = []

        def forbidden():
            calls.append("clock-or-id")
            self.fail("cleanup admission reached clock or identity allocation")

        with self.assertRaisesRegex(ExecutionAdmissionConflict, "configuration cleanup.*unsupported"):
            ExecutionAdmissionCommandService(self.unit_of_work, clock=forbidden, id_factory=forbidden).execute(
                self.command(plan_id=self.plan.plan_id, approval_request_id=self.approval.request_id,
                    scopes=tuple(PolicyScope),
                    key="ceilings-public-refusal"))
        self.assertEqual(calls, [])
        activity = self.plan.plan.activities[0]
        context = _context(activity=activity)
        for translate in (lambda: runtime_effect_request_for_context(context),
                lambda: _runtime_effect_intent_for_context(context, activity)):
            with self.assertRaisesRegex(InvalidOperationCommand, "configuration cleanup.*unsupported"):
                translate()
        self.assertEqual(self.ceiling_truth(), before)

    def test_capture_propagates_to_independent_original_readers(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.read_premise() as (uow, guard, prefix, read, observed):
            self.assert_existing_premise(uow, guard, prefix)
            self.width_evidence(uow.stores.connection)
            owner = self.owner(uow)
            initial_queries = len(observed["queries"])
            issued = self.capture(owner, guard, prefix)
            captures = [query for query in observed["queries"][initial_queries:]
                if query.startswith("SELECT octet_length(") and query.endswith("LIMIT 1")]
            self.assertEqual(len(captures), 3)
            self.assertEqual(sum(query.count("octet_length(") for query in captures), 26)
            self.assertNotIn(self.plan.plan_id, repr(issued))
            with owner.bind(issued):
                initial, physical = read.used, observed["bytes"]
                expected = self.independent_reads(uow)
                self.assertEqual(self.independent_reads(uow), expected)
                # Six matching point reads: txid + length + guarded value each.
                self.assertEqual(read.used.statements - initial.statements, 18)
                self.assertGreaterEqual(read.used.accounted_bytes - initial.accounted_bytes,
                    observed["bytes"] - physical)
                for _ in range(2):
                    initial_queries = len(observed["queries"])
                    original, derived = uow.stores.execution._receiver_execution_material(prefix.request.identity, guard)
                    self.assertEqual(original[0], self.plan)
                    self.assertTrue(derived.scopes)
                    queries = observed["queries"][initial_queries:]
                    self.assertEqual(sum(query == "SELECT txid_current()" for query in queries), 6)
                    self.assertEqual(sum(query.startswith("SELECT octet_length(") and any(
                        " FROM " + table + " " in query for table in
                        ("cpk_activity_plans", "cpk_graph_versions", "cpk_realized_graph_projections"))
                        for query in queries), 3)
        self.assertEqual(self.ceiling_truth(), before)

    def test_metadata_growth_refuses_before_full_value_transport(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.read_premise() as (uow, guard, prefix, read, observed):
            self.assert_existing_premise(uow, guard, prefix)
            owner = self.owner(uow)
            issued = self.capture(owner, guard, prefix)
            # Explicit same-UoW corrupt-history injection, below the ordinary
            # 1MiB metadata cap. The positive fixture was established above.
            enlarged = json.dumps({"fault-injected": "x" * 65536})
            uow.stores.connection.execute("UPDATE cpk_graph_versions SET metadata=%s::jsonb WHERE graph_id=%s",
                (enlarged, self.plan.base_graph_id))
            observed["largest_cell"] = 0
            with owner.bind(issued):
                with self.assertRaises(_Capacity):
                    PostgresStoreBundle(uow.stores.connection).graphs.get(self.plan.base_graph_id)
            self.assertLess(observed["largest_cell"], 65536)
        self.assertEqual(self.ceiling_truth(), before)

    def test_equal_copy_cannot_borrow_issued_binding(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.read_premise() as (uow, guard, prefix, read, observed):
            self.assert_existing_premise(uow, guard, prefix)
            owner = self.owner(uow)
            with self.assertRaises(_Unavailable):
                with owner.bind(None):
                    self.fail("fresh owner accepted an unissued binding")
            issued = self.capture(owner, guard, prefix)
            copied = replace(issued)
            self.assertIsNot(copied, issued)
            for invalid in (None, copied):
                with self.assertRaises(ValueError):
                    with owner.bind(invalid):
                        self.independent_reads(uow)
            with self.assertRaises(_Unavailable):
                with self.owner(uow).bind(issued):
                    self.independent_reads(uow)
            with owner.bind(issued):
                self.independent_reads(uow)
        self.assertEqual(self.ceiling_truth(), before)

    def test_retained_intent_bytea_read_is_separate_from_prospective_capture(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.read_premise() as (uow, guard, prefix, read, observed):
            self.assert_existing_premise(uow, guard, prefix)
            retained = self.record_intent_premise(uow.stores)
            owner = self.owner(uow)
            issued = self.capture(owner, guard, prefix)
            from control_plane_kit_operations.effect_attempt_intent_evidence import _encode_runtime_effect_intent
            preimage = _encode_runtime_effect_intent(self.intent)
            self.assertIn(("intent", (self.identity.run_id.value, self.identity.activity_id,
                self.identity.attempt), (("preimage", len(preimage)),)), issued.originals)
            with owner.bind(issued):
                initial, physical = read.used, observed["bytes"]
                for _ in range(2):
                    self.assertEqual(PostgresStoreBundle(uow.stores.connection).effect_attempt_intents.get(
                        self.identity), retained)
                self.assertEqual(read.used.statements - initial.statements, 10)
                self.assertEqual(read.used.accounted_bytes - initial.accounted_bytes,
                    observed["bytes"] - physical)
                uow.stores.connection.execute("UPDATE cpk_effect_attempt_intents SET preimage=%s "
                    "WHERE run_id=%s AND activity_id=%s AND attempt=%s",
                    (preimage + b" ", self.identity.run_id.value, self.identity.activity_id, self.identity.attempt))
                observed["largest_cell"] = 0
                with self.assertRaises(_Capacity):
                    uow.stores.effect_attempt_intents.get(self.identity)
                self.assertLess(observed["largest_cell"], len(preimage))
        self.assertEqual(self.ceiling_truth(), before)

    def test_binding_rejects_context_uow_transaction_and_lifetime_reuse(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        from control_plane_kit_operations._configuration_preparation import _configuration_accounting
        # Each invalid case gets an independently issued, single-use binding.
        for invalid in ("accounting", "inactive", "connection", "transaction", "post-scope", "task", "thread"):
            with self.subTest(invalid=invalid):
                with self.read_premise() as (uow, guard, prefix, read, observed):
                    owner = self.owner(uow)
                    issued = self.capture(owner, guard, prefix)
                    with owner.bind(issued):
                        if invalid in ("accounting", "inactive"):
                            with _configuration_accounting("foreign", active=invalid != "inactive"):
                                with self.assertRaises(_Unavailable):
                                    uow.stores.graphs.get(self.plan.base_graph_id)
                        elif invalid == "connection":
                            with self.unit_of_work() as other:
                                with self.assertRaises(_Unavailable):
                                    other.stores.graphs.get(self.plan.base_graph_id)
                        elif invalid == "transaction":
                            # Deliberate premature physical commit, no writes:
                            # same Python UoW/connection/accounting, new txid.
                            uow.stores.connection.commit()
                            with self.assertRaises(_Unavailable):
                                uow.stores.graphs.get(self.plan.base_graph_id)
                        elif invalid == "task":
                            import asyncio
                            async def changed_task():
                                with self.assertRaises(_Unavailable):
                                    uow.stores.graphs.get(self.plan.base_graph_id)
                            asyncio.run(changed_task())
                        elif invalid == "thread":
                            from concurrent.futures import ThreadPoolExecutor
                            from contextvars import copy_context
                            def changed_thread():
                                with self.assertRaises(_Unavailable):
                                    uow.stores.graphs.get(self.plan.base_graph_id)
                            with ThreadPoolExecutor(max_workers=1) as executor:
                                executor.submit(copy_context().run, changed_thread).result()
                        else:
                            self.independent_reads(uow)
                    with self.assertRaises(_Unavailable):
                        with owner.bind(issued):
                            self.fail("spent token rebound")
                    # No stale binding affects ordinary defaults after exit.
                    self.independent_reads(uow)
        # Actual exception exit clears binding as well as ordinary exit.
        with self.read_premise() as (uow, guard, prefix, read, observed):
            owner = self.owner(uow)
            issued = self.capture(owner, guard, prefix)
            with self.assertRaisesRegex(RuntimeError, "test exit"):
                with owner.bind(issued):
                    raise RuntimeError("test exit")
            self.independent_reads(uow)
        with self.assertRaises(_Unavailable):
            with self.read_premise() as (uow, guard, prefix, read, observed):
                owner = self.owner(uow)
                issued = self.capture(owner, guard, prefix)
                with owner.bind(issued):
                    uow.commit()  # Binding must finish before the outer commit request.
        self.assertEqual(self.ceiling_truth(), before)

    def test_distinct_pins_capture_actual_widths_and_receiver_applications(self):
        self.prepare_ceiling_premise(distinct_pins=True)
        before = self.ceiling_truth()
        with self.read_premise() as (uow, guard, prefix, read, observed):
            self.assert_existing_premise(uow, guard, prefix)
            owner = self.owner(uow)
            initial = len(observed["queries"])
            issued = self.capture(owner, guard, prefix)
            captures = [query for query in observed["queries"][initial:]
                if query.startswith("SELECT octet_length(") and query.endswith("LIMIT 1")]
            self.assertEqual(len(captures), 5)
            self.assertEqual(sum(query.count("octet_length(") for query in captures), 42)
            self.assertEqual(len(issued.originals), 6)  # Five DB originals + prospective bytea.
            for kind, key, columns in issued.originals:
                if kind == "intent":
                    continue
                table, primary = {"plan": ("cpk_activity_plans", "plan_id"),
                    "graph": ("cpk_graph_versions", "graph_id"),
                    "projection": ("cpk_realized_graph_projections", "projection_id")}[kind]
                for name, width in columns:
                    actual = uow.stores.connection.execute(f"SELECT octet_length({name}::text) FROM {table} "
                        f"WHERE {primary}=%s", (key,)).fetchone()[0]
                    self.assertEqual(width, actual)
            with owner.bind(issued):
                for _ in range(2):
                    initial = len(observed["queries"])
                    material = uow.stores.execution._receiver_execution_material(prefix.request.identity, guard)
                    self.assertEqual(material[0][0], self.plan)
                    queries = observed["queries"][initial:]
                    # One existing lifecycle guard check + five matching reads.
                    self.assertEqual(sum(query == "SELECT txid_current()" for query in queries), 6)
                    self.assertEqual(sum(query.startswith("SELECT octet_length(") and any(
                        " FROM " + table + " " in query for table in
                        ("cpk_activity_plans", "cpk_graph_versions", "cpk_realized_graph_projections"))
                        for query in queries), 5)
        self.assertEqual(self.ceiling_truth(), before)

    def test_probe_fetch_growth_and_same_width_wrong_plan_still_refuse(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        for corruption in ("between-probe-fetch", "same-width-plan"):
            with self.subTest(corruption=corruption):
                with self.read_premise() as (uow, guard, prefix, read, observed):
                    owner = self.owner(uow)
                    issued = self.capture(owner, guard, prefix)
                    with owner.bind(issued):
                        if corruption == "between-probe-fetch":
                            changed = []
                            def inject(query):
                                if not changed and query.startswith("SELECT CASE WHEN") and "FROM cpk_graph_versions" in query:
                                    changed.append(True)
                                    # Negative fixture mutation, not a reader query;
                                    # actual bounded fetch still reaches PostgreSQL.
                                    uow.stores.connection.connection.execute(
                                        "UPDATE cpk_graph_versions SET metadata=%s::jsonb WHERE graph_id=%s",
                                        (json.dumps({"race": "x" * 65536}), self.plan.base_graph_id))
                            uow.stores.connection.before_execute = inject
                            observed["largest_cell"] = 0
                            with self.assertRaises(_Unavailable):
                                uow.stores.graphs.get(self.plan.base_graph_id)
                            self.assertEqual(changed, [True])
                            self.assertLess(observed["largest_cell"], 65536)
                            uow.stores.connection.before_execute = None
                        else:
                            lengths = uow.stores.connection.execute("SELECT octet_length(payload::text) "
                                "FROM cpk_activity_plans WHERE plan_id=%s", (self.plan.plan_id,)).fetchone()
                            uow.stores.connection.execute("UPDATE cpk_activity_plans SET payload=jsonb_set(payload, "
                                "'{cleanup_proposal_fingerprint}', to_jsonb(repeat('0',64))) WHERE plan_id=%s",
                                (self.plan.plan_id,))
                            self.assertEqual(uow.stores.connection.execute("SELECT octet_length(payload::text) "
                                "FROM cpk_activity_plans WHERE plan_id=%s", (self.plan.plan_id,)).fetchone(), lengths)
                            with self.assertRaises(ValueError):
                                uow.stores.execution._receiver_execution_material(prefix.request.identity, guard)
        self.assertEqual(self.ceiling_truth(), before)

    def test_capture_refuses_stale_request_and_keeps_prelude_and_failed_reservations(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.read_premise() as (uow, guard, prefix, read, observed):
            uow.stores.connection.execute("UPDATE cpk_execution_requests SET intent_fingerprint='changed' WHERE request_id=%s",
                (prefix.request.identity.request_id,))
            initial = read.used
            with self.assertRaises(_Unavailable):
                self.capture(self.owner(uow), guard, prefix)
            self.assertGreater(read.used.statements, initial.statements)
            self.assertGreater(read.used.accounted_bytes, initial.accounted_bytes)
        with self.read_premise() as (uow, guard, prefix, read, observed):
            initial = read.used
            owner = self.owner(uow)
            issued = self.capture(owner, guard, prefix)
            self.assertGreater(read.used.accounted_bytes, initial.accounted_bytes)
            with owner.bind(issued):
                # Explicit injected exhaustion, never natural-fit evidence:
                # txid's final record fits; the following length reservation cannot.
                read.used = replace(read.used, records=4095)
                initial = read.used
                with self.assertRaises(_Capacity):
                    uow.stores.graphs.get(self.plan.base_graph_id)
                self.assertEqual(read.used.records, 4096)
                self.assertEqual(read.used.statements, initial.statements + 1)
        self.assertEqual(self.ceiling_truth(), before)

    def test_ordinary_receiver_history_keeps_large_bytea_supported(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        from control_plane_kit_operations.postgres.receiver_execution_scopes import _ExecutionScopeStorage
        with self.read_premise() as (uow, guard, prefix, read, observed):
            owner = self.owner(uow)
            issued = self.capture(owner, guard, prefix)
            request = uow.stores.execution.get_request("execution-ceilings-companion")
            run = uow.stores.execution.get_run("run-ceilings-companion")
            with owner.bind(issued):
                storage = _ExecutionScopeStorage(uow.stores.connection, read)
                original = storage.originals(request.identity)
                events = storage.events(run.run_id)
                attempts, intents, outcomes = storage.effects(request, original, run, events)
                self.assertTrue(attempts and intents and outcomes)
                from control_plane_kit_operations.effect_attempt_intent_evidence import _encode_runtime_effect_intent
                self.assertGreater(max(len(_encode_runtime_effect_intent(item.intent)) for item in intents), 2048)
                self.assertGreater(observed["largest_cell"], 2048)
        self.assertEqual(self.ceiling_truth(), before)

    def test_each_fixed_original_entrance_rejects_growth_before_transport(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        for surface in ("plan", "graph", "projection", "receiver"):
            with self.subTest(surface=surface):
                with self.read_premise() as (uow, guard, prefix, read, observed):
                    owner = self.owner(uow)
                    issued = self.capture(owner, guard, prefix)
                    table, column, primary, identity = {
                        "plan": ("cpk_activity_plans", "payload", "plan_id", self.plan.plan_id),
                        "graph": ("cpk_graph_versions", "graph_descriptor", "graph_id", self.plan.base_graph_id),
                        "projection": ("cpk_realized_graph_projections", "graph_descriptor", "projection_id",
                            self.plan.base_realized_projection_id),
                        "receiver": ("cpk_graph_versions", "metadata", "graph_id", self.plan.base_graph_id),
                    }[surface]
                    uow.stores.connection.execute(f"UPDATE {table} SET {column}={column} || %s::jsonb WHERE {primary}=%s",
                        (json.dumps({"fault_injected_growth": "x" * 65536}), identity))
                    observed["largest_cell"] = 0
                    with owner.bind(issued):
                        with self.assertRaises(_Capacity):
                            if surface == "receiver":
                                uow.stores.execution._receiver_execution_material(prefix.request.identity, guard)
                            elif surface == "plan":
                                uow.stores.activity_history.get_plan(identity)
                            elif surface == "graph":
                                uow.stores.graphs.get(identity)
                            else:
                                uow.stores.realized_graphs.get(identity)
                    self.assertLess(observed["largest_cell"], 65536)
        self.assertEqual(self.ceiling_truth(), before)

    def test_defensive_null_width_and_fixed_named_columns_keep_semantics(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.read_premise() as (uow, guard, prefix, read, observed):
            original = uow.stores.graphs.get(self.plan.base_graph_id)
            owner = self.owner(uow)
            # Current schema originals are NOT NULL. This isolated real-SQL
            # projection tests defensive normalization, not valid issuer input.
            nullable = "(SELECT NULL::text AS metadata) AS nullable_material"
            self.assertEqual(owner._lengths(read, nullable, ("metadata",), ("metadata",), "TRUE", ()),
                (("metadata", 0),))
            self.assertEqual(read.bounded_rows(nullable, (("metadata", "json", 0),), "TRUE", ()),
                ((None,),))
            from control_plane_kit_operations.records import OperationsRecordError
            with self.assertRaises(OperationsRecordError):
                replace(original, metadata=None)
            issued = self.capture(owner, guard, prefix)
            widths = dict(next(columns for kind, key, columns in issued.originals
                if kind == "graph" and key == self.plan.base_graph_id))
            self.assertEqual(widths["metadata"], 2)
            # Mapping by names must survive a caller's different scalar caps.
            from control_plane_kit_operations.postgres.configuration_cleanup_read_ceilings import _cleanup_original_columns
            with owner.bind(issued):
                columns = _cleanup_original_columns(uow.stores.connection, "graph", self.plan.base_graph_id,
                    (("metadata", "json", 1048576), ("graph_id", "text", 1), ("version", "int", 32)))
                self.assertEqual(columns, (("metadata", "json", 2), ("graph_id", "text", 1),
                    ("version", "int", widths["version"])))
                self.assertEqual(uow.stores.graphs.get(self.plan.base_graph_id), original)
        self.assertEqual(self.ceiling_truth(), before)

    def test_nonmatching_default_and_exact_metadata_edge_preserve_accounting(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.read_premise() as (uow, guard, prefix, read, observed):
            # A nonselected authored identity already exists from the real source.
            source = uow.stores.effect_attempt_intents.get(self.source_identity)
            other_graph = source.intent.source.desired_graph_id
            self.assertNotEqual(other_graph, self.plan.base_graph_id)
            uow.stores.connection.execute("UPDATE cpk_graph_versions SET metadata=%s::jsonb WHERE graph_id=%s",
                (json.dumps({"other": "x" * 4096}), other_graph))
            owner = self.owner(uow)
            issued = self.capture(owner, guard, prefix)
            with owner.bind(issued):
                initial = read.used
                other = uow.stores.graphs.get(other_graph)
                self.assertEqual(other.metadata["other"], "x" * 4096)
                self.assertEqual(read.used.statements - initial.statements, 2)
                initial = read.used
                with self.assertRaises(KeyError):
                    uow.stores.graphs.get("missing-unmatched")
                self.assertEqual(read.used.statements - initial.statements, 1)
                # Exact original '{}' metadata passes; '[0]' grows by one byte.
                self.assertEqual(uow.stores.graphs.get(self.plan.base_graph_id).metadata, {})
                uow.stores.connection.execute("UPDATE cpk_graph_versions SET metadata='[0]'::jsonb WHERE graph_id=%s",
                    (self.plan.base_graph_id,))
                initial = read.used
                with self.assertRaises(_Capacity):
                    uow.stores.graphs.get(self.plan.base_graph_id)
                self.assertEqual(read.used.statements - initial.statements, 2)
                self.assertGreater(read.used.accounted_bytes, initial.accounted_bytes)
        self.assertEqual(self.ceiling_truth(), before)
