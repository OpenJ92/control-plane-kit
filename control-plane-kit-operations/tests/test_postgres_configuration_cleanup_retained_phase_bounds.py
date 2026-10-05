"""Retained STARTED read bounds; no cleanup dispatch or fold implementation."""
from contextlib import contextmanager
from dataclasses import replace
import unittest

import psycopg

from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.effect_run_prefix import _lock_effect_run_prefix
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_cleanup_read_ceilings import _CleanupOriginalReadCeilingsOwner
from control_plane_kit_operations.postgres.configuration_cleanup_phase_read_bounds import _CleanupPhaseReadBoundsOwner
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead, _joined_read, _Unavailable, _Capacity
from tests.configuration_cleanup_history_fixture import ConfigurationCleanupHistoryFixture, key
from tests.configuration_cleanup_phase_read_bounds_fixture import _PhaseConnection, _components


class PostgresConfigurationCleanupRetainedPhaseBoundsTests(ConfigurationCleanupHistoryFixture, unittest.TestCase):
    def foreign_completion(self):
        from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
        from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
        from control_plane_kit_core.planning import StartNode, NodeTarget
        from control_plane_kit_core.products import ProductInstanceConfiguration, instantiate_product
        from control_plane_kit_core.topology import compile_topology
        from tests.test_runtime_effect_translation import _configuration_product
        from tests.configuration_cleanup_postgres_fixture import completion_result
        operator = self.carry_operator()
        product = _configuration_product().descriptor_document.product
        block = instantiate_product(product, "foreign", ProductInstanceConfiguration.from_contract(product.runtime_contract))
        extra = compile_topology(DeploymentTopology("foreign", DockerRuntime(runtime_id="runtime-a",
            authority_ref=self.runtime_authority_ref, children=(block,))))
        runtime = operator.graph.runtimes["runtime-a"]
        graph = replace(operator.graph, nodes={**operator.graph.nodes, **extra.nodes},
            runtimes={**operator.graph.runtimes, "runtime-a": replace(runtime, children=(*runtime.children, "foreign"))})
        command = operator.admit("foreign-d1", "graph-foreign-d1", StartNode(NodeTarget("foreign")), graph=graph)
        def completed(request):
            result = completion_result(request)
            return replace(result, observations=tuple(replace(o, subject_id="foreign") for o in result.observations))
        self.execute_later(command, "foreign-d1", producer=completed)
        identity = EffectAttemptIdentity(RunId(command.run_id), "activity-foreign-d1", 1)
        with self.unit_of_work() as uow:
            result = uow.stores.configuration_completions.get(identity)
            self.assertIsNotNone(result)
            return result

    @contextmanager
    def retained_premise(self):
        identity = self.retained_intent.identity
        invocation_keys = {key(value.identity) for value in self.retained_completions}
        def role(query, params):
            if " FROM cpk_effect_configuration_refs" in query and params[:3] in invocation_keys:
                return "invocation-refs"
            return None
        with _configuration_accounting(identity.run_id.value) as accounting:
            observed = dict(bytes=0, rows=0, largest_cell=0, statements=0,
                queries=[], accounting=accounting, role_label=role)
            factory = lambda: _PhaseConnection(psycopg.connect(self.database_url), observed)
            with PostgresUnitOfWork(factory) as uow, _joined_read(uow.stores.connection) as read:
                guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                request = uow.stores.execution.get_request_for_update(self.retained_intent.request_id)
                prefix = _lock_effect_run_prefix(uow, request, identity.run_id.value, latest_required=True)
                original_owner = _CleanupOriginalReadCeilingsOwner(uow)
                original = original_owner.capture(guard, prefix, self.retained_plan,
                    intent_identity=identity, prospective_intent=self.retained_intent.intent)
                with original_owner.bind(original):
                    yield uow, guard, prefix, read, observed

    def capture_retained(self, owner, guard, prefix):
        return owner.capture_retained(guard, prefix, self.retained_plan,
            original_identity=self.retained_intent.identity)

    def cold_retained(self, uow):
        return uow.stores.configuration_cleanup_ownership._get(self.retained_intent.identity,
            _EvidenceRead(uow.stores.connection))

    def mutate(self, read, sql, params):
        self.assertEqual(read.query(sql + " RETURNING 1", params, records=1, octets=1, cells=1), [(1,)])

    def remove_retained_claim(self, read, *, remove_ref=False):
        identity, ref = self.retained_claims[0]
        locator = (*key(identity), ref.artifact_id)
        # Remove the actual immediate-FK children first. These are explicit
        # rollback-only corruptions; no constraints or proofs are disabled.
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.mutate(read, "UPDATE " + table + " SET cleanup_run_id=NULL,cleanup_activity_id=NULL,cleanup_attempt=NULL "
                "WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", locator)
        self.mutate(read, "DELETE FROM cpk_configuration_claim_closures "
            "WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", locator)
        if remove_ref:
            self.mutate(read, "DELETE FROM cpk_configuration_cleanup_members "
                "WHERE (cleanup_run_id,cleanup_activity_id,cleanup_attempt,workspace_id,allocation_id)=(%s,%s,%s,%s,%s)",
                (*key(self.retained_intent.identity), ref.workspace_id, ref.allocation_id))
            self.mutate(read, "DELETE FROM cpk_effect_configuration_refs "
                "WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", locator)

    def measure(self, read, observed, label, before, offset):
        self.assertEqual(read.used.statements, observed["statements"])
        self.assertGreaterEqual(read.used.accounted_bytes, observed["bytes"])
        self.assertGreaterEqual(read.used.records, observed["rows"])
        self.assertLessEqual(read.used.records, 4096)
        self.assertLessEqual(read.used.accounted_bytes, 16*1024*1024)
        segment = observed["queries"][offset:]
        peaks = [b+128*r+16*c+256*s for r,b,c,s in (q["peak"] for q in segment)]
        self.assertTrue(peaks)
        self.assertLessEqual(max(peaks), 16*1024*1024)
        physical = sum(256 + sum(128+16*len(row)+sum(row) for row in q["widths"]) for q in segment)
        print("#1941 retained phase", dict(stage=label, prefix=_components(before), used=_components(read.used),
            physical_weighted_bytes=physical, maximum_reservation_bytes=max(peaks)))

    def test_retained_capture_preserves_complete_b_and_d1_after_authority_and_current_changes(self):
        self.retain_cleanup()
        expected = self.read_retained()
        for changed in (False, True):
            if changed:
                # Existing B's established historical-read law. No new live
                # authority or eligibility is inferred from these observations.
                self.connection.execute("UPDATE cpk_runtime_authorities SET status='revoked' WHERE registration_id=%s",
                    (self.base.runtime_registration.registration_id,))
                with self.unit_of_work() as uow:
                    uow.stores.workspaces.set_desired_graph("workspace-a", "graph-current")
                    uow.commit()
                self.assertEqual(self.read_retained(), expected)
            before_truth = self.cleanup_snapshot()
            with self.subTest(changed=changed), self.retained_premise() as (uow, guard, prefix, read, observed):
                before, offset = read.used, len(observed["queries"])
                self.assertEqual(self.cold_retained(uow), expected)
                self.measure(read, observed, "ordinary-baseline", before, offset)
                owner = _CleanupPhaseReadBoundsOwner(uow)
                before, offset = read.used, len(observed["queries"])
                issued = self.capture_retained(owner, guard, prefix)
                self.measure(read, observed, "retained-capture", before, offset)
                counts = [q for q in observed["queries"][offset:] if q["sql"].startswith("SELECT COUNT(*)")]
                self.assertEqual(len(counts), len(expected.completions))
                self.assertTrue(all(q["role"] == "invocation-refs" and "LIMIT %s)" in q["sql"]
                    and q["limit"] == 33 for q in counts))
                with owner.bind(issued):
                    before, offset = read.used, len(observed["queries"])
                    self.assertEqual(self.cold_retained(uow), expected)
                    for completion in expected.completions:
                        self.assertEqual(uow.stores.configuration_completions._get(completion.identity,
                            _EvidenceRead(uow.stores.connection)), completion)
                    self.measure(read, observed, "retained-application", before, offset)
                    segment = observed["queries"][offset:]
                    probes = [q for q in segment if q["role"] == "invocation-refs"
                        and q["sql"].startswith("SELECT octet_length(")]
                    self.assertEqual(len(probes), 2*len(expected.completions))
                    for q in probes:
                        self.assertEqual(segment[segment.index(q)-1]["sql"], "SELECT txid_current()")
                        self.assertEqual(q["limit"], len(expected.claims)+1)
            self.assertEqual(self.cleanup_snapshot(), before_truth)

    def test_retained_capture_requires_exact_original_plan_and_complete_started_aggregate(self):
        self.retain_cleanup()
        before = self.cleanup_snapshot()
        for fault in ("plan", "identity", "attempt", "missing", "corrupt"):
            with self.subTest(fault=fault), self.retained_premise() as (uow, guard, prefix, read, observed):
                self.assertIsNotNone(self.cold_retained(uow))
                plan, identity = self.retained_plan, self.retained_intent.identity
                if fault == "plan":
                    plan = replace(plan, plan_id="foreign-retained-plan")
                elif fault == "identity":
                    identity = replace(identity, activity_id="foreign-retained-activity")
                elif fault == "attempt":
                    identity = replace(identity, attempt=identity.attempt+1)
                elif fault == "missing":
                    self.remove_retained_claim(read)
                else:
                    self.mutate(read, "UPDATE cpk_configuration_cleanup_reservations SET proposal_fingerprint=%s "
                        "WHERE (cleanup_run_id,cleanup_activity_id,cleanup_attempt)=(%s,%s,%s)", ("0"*64, *key(identity)))
                owner = _CleanupPhaseReadBoundsOwner(uow)
                with self.assertRaises(ValueError):
                    owner.capture_retained(guard, prefix, plan, original_identity=identity)
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_retained_whole_invocation_growth_membership_and_context_refuse(self):
        self.retain_cleanup()
        before = self.cleanup_snapshot()
        source = self.retained_completion.identity
        artifact = self.refs[0].artifact_id
        for fault in ("width", "membership", "context", "copy", "transaction"):
            with self.subTest(fault=fault), self.retained_premise() as (uow, guard, prefix, read, observed):
                owner = _CleanupPhaseReadBoundsOwner(uow)
                issued = self.capture_retained(owner, guard, prefix)
                if fault == "copy":
                    with self.assertRaises(_Unavailable):
                        with owner.bind(replace(issued)):
                            self.fail("copied retained bound accepted")
                    continue
                if fault == "width":
                    self.mutate(read, "UPDATE cpk_effect_configuration_refs SET ref_preimage=%s "
                        "WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", (b"x"*4000, *key(source), artifact))
                elif fault == "membership":
                    self.remove_retained_claim(read, remove_ref=True)
                with owner.bind(issued):
                    offset = len(observed["queries"])
                    if fault == "transaction":
                        uow.stores.connection.commit()
                    if fault == "context":
                        with _configuration_accounting("foreign-retained"):
                            with self.assertRaises(_Unavailable):
                                uow.stores.configuration_completions._get(source, _EvidenceRead(uow.stores.connection))
                    else:
                        with self.assertRaises(_Capacity if fault == "width" else ValueError):
                            uow.stores.configuration_completions._get(source, _EvidenceRead(uow.stores.connection))
                    if fault == "width":
                        segment = observed["queries"][offset:]
                        self.assertTrue(any(q["role"] == "invocation-refs" for q in segment))
                        self.assertFalse(any(row[8] >= 4000 for q in segment if q["role"] == "invocation-refs"
                            and not q["sql"].startswith("SELECT octet_length(") for row in q["widths"]))
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_retained_parent_rejects_foreign_completed_child_but_direct_d1_keeps_defaults(self):
        foreign = self.foreign_completion()
        self.retain_cleanup()
        expected = self.read_retained()
        self.assertNotIn(foreign.identity, {c.identity for c in expected.completions})
        self.assertGreaterEqual(len(expected.claims), 2)
        before = self.cleanup_snapshot()
        with self.retained_premise() as (uow, guard, prefix, read, observed):
            owner = _CleanupPhaseReadBoundsOwner(uow)
            issued = self.capture_retained(owner, guard, prefix)
            # Add an FK-valid existing completed invocation to the captured B
            # parent. It is deliberately not one of this reservation's claims.
            self.mutate(read, "INSERT INTO cpk_configuration_invocation_closures "
                "(run_id,activity_id,attempt,cleanup_run_id,cleanup_activity_id,cleanup_attempt,workspace_id,"
                "request_fingerprint,selection_fingerprint,outcome_fingerprint) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (*key(foreign.identity), *key(self.retained_intent.identity), foreign.workspace_id,
                    foreign.request_fingerprint, foreign.selection_fingerprint, foreign.outcome_fingerprint))
            self.mutate(read, "UPDATE cpk_configuration_cleanup_reservations SET invocation_count=invocation_count+1 "
                "WHERE (cleanup_run_id,cleanup_activity_id,cleanup_attempt)=(%s,%s,%s)", key(self.retained_intent.identity))
            with owner.bind(issued):
                offset = len(observed["queries"])
                self.assertEqual(uow.stores.configuration_completions._get(foreign.identity,
                    _EvidenceRead(uow.stores.connection)), foreign)
                ordinary = observed["queries"][offset:]
                self.assertTrue(any(" ORDER BY r.artifact_id LIMIT 33" in q["sql"] for q in ordinary))
                self.assertFalse(any(q["sql"] == "SELECT txid_current()" for q in ordinary))
                offset = len(observed["queries"])
                # The original run-config sorts before run-foreign-d1, so the
                # captured child can execute. The foreign child must not fetch.
                original_label = observed["role_label"]
                def label(query, params):
                    if params[:3] == key(foreign.identity) and " FROM cpk_configuration_invocation_completions " in query:
                        return "foreign-completion"
                    return original_label(query, params)
                observed["role_label"] = label
                with self.assertRaises(_Unavailable):
                    self.cold_retained(uow)
                self.assertFalse(any(q["role"] == "foreign-completion" for q in observed["queries"][offset:]),
                    "retained parent fetched uncaptured completion before refusal")
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_retained_parent_refuses_existing_foreign_plan_before_payload_fetch(self):
        self.retain_cleanup()
        before = self.cleanup_snapshot()
        with self.retained_premise() as (uow, guard, prefix, read, observed):
            owner = _CleanupPhaseReadBoundsOwner(uow)
            issued = self.capture_retained(owner, guard, prefix)
            foreign = "foreign-retained-plan"
            self.mutate(read, "INSERT INTO cpk_activity_plans "
                "(plan_id,session_id,base_graph_id,desired_graph_id,base_realized_projection_id,"
                "desired_realized_projection_id,desired_graph_revision,status,created_at,payload) "
                "SELECT %s,session_id,base_graph_id,desired_graph_id,base_realized_projection_id,"
                "desired_realized_projection_id,desired_graph_revision,status,created_at,payload "
                "FROM cpk_activity_plans WHERE plan_id=%s", (foreign, self.retained_plan.plan_id))
            foreign_plan = uow.stores.activity_history.get_plan(foreign)
            offset = len(observed["queries"])
            with self.assertRaises(_Unavailable):
                _CleanupPhaseReadBoundsOwner(uow).capture_retained(guard, prefix, foreign_plan,
                    original_identity=self.retained_intent.identity)
            self.assertEqual(len(observed["queries"]), offset,
                "retained issuance borrowed1939 scope for a valid different plan")
            with owner.bind(issued):
                offset = len(observed["queries"])
                self.assertEqual(uow.stores.activity_history.get_plan(foreign).plan_id, foreign)
                self.assertFalse(any(q["sql"] == "SELECT txid_current()" for q in observed["queries"][offset:]))
                # One statement keeps the immediate request/plan/run/header
                # FKs consistent. Only explicit negative history is changed;
                # original intent/approval semantic correspondence stays cold.
                rows = read.query("WITH request_change AS (UPDATE cpk_execution_requests SET plan_id=%s "
                    "WHERE request_id=%s RETURNING 1), run_change AS (UPDATE cpk_activity_runs SET plan_id=%s "
                    "WHERE run_id=%s RETURNING 1), header_change AS (UPDATE cpk_configuration_cleanup_reservations "
                    "SET plan_id=%s WHERE (cleanup_run_id,cleanup_activity_id,cleanup_attempt)=(%s,%s,%s) RETURNING 1) "
                    "SELECT (SELECT COUNT(*) FROM request_change),(SELECT COUNT(*) FROM run_change),"
                    "(SELECT COUNT(*) FROM header_change)",
                    (foreign, self.retained_intent.request_id, foreign, self.retained_intent.identity.run_id.value,
                        foreign, *key(self.retained_intent.identity)), records=1, octets=3, cells=3, identities=3)
                self.assertEqual(rows, [(1, 1, 1)])
                offset = len(observed["queries"])
                with self.assertRaises(_Unavailable):
                    self.cold_retained(uow)
                self.assertFalse(any(" FROM cpk_activity_plans " in q["sql"] for q in observed["queries"][offset:]),
                    "retained parent fetched a foreign plan before correspondence refusal")
        self.assertEqual(self.cleanup_snapshot(), before)
