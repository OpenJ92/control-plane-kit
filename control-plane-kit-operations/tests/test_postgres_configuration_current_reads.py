"""Public current/use observations over genuine accepted and staged evidence."""
from dataclasses import replace
import re
import unittest

import psycopg

from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService
from tests import test_postgres_configuration_acceptance_membership as membership_fixture
from tests.test_postgres_configuration_evidence import _ObservedConnection as TransportObservedConnection


class PostgresConfigurationCurrentReadTests(unittest.TestCase):
    def setUp(self):
        self.fixture = membership_fixture.PostgresConfigurationAcceptanceMembershipTests()
        self.fixture.node_ids = getattr(self, "node_ids", ("api",))
        self.fixture.registered_product = getattr(self, "registered_product", None)
        self.fixture.configuration_result_for_request = getattr(self, "configuration_result_for_request", None)
        self.addCleanup(self.cleanup_fixture)
        self.fixture.setUp()
        self.base = self.fixture.fixture
        self.refs = self.fixture.original.intent.configuration_instances.instances

    def cleanup_fixture(self):
        self.assertTrue(self.fixture.doCleanups(), "nested membership fixture cleanup failed")

    def read_current(self, *, workspace_id="workspace-a", node_id=None):
        return self.read("read_current_configuration", workspace_id, node_id=node_id)

    def read_use(self, refs):
        return self.read("read_configuration_use", "workspace-a", refs)

    def read(self, name, *args, observations=None, source_queries=None, **kwargs):
        statements = []

        class ObservedConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, attribute):
                return getattr(self.connection, attribute)

            def execute(self, query, parameters=None):
                statements.append(str(query))
                if source_queries is not None:
                    for table in ("cpk_effect_attempt_intents", "cpk_effect_attempt_outcomes"):
                        if table in str(query):
                            source_queries.append((table, tuple(parameters[:3])))
                return self.connection.execute(query, parameters)

        def connection():
            raw = psycopg.connect(self.base.database_url)
            return ObservedConnection(raw if observations is None else TransportObservedConnection(raw, observations))

        with PostgresUnitOfWork(connection) as uow:
            reader = getattr(uow.stores.configuration_acceptance, name, None)
            self.assertTrue(callable(reader), "mapped configuration read boundary is missing: " + name)
            result = reader(*args, **kwargs)
            self.assertFalse(any(re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|ALTER|CREATE|DROP)\b", query,
                re.IGNORECASE) for query in statements), "read observation dispatched mutation/row locking")
        return result

    def assert_complete_bindings(self, evidence):
        self.assertEqual(evidence.state, "complete")
        self.assertEqual((evidence.workspace_id, evidence.graph_id, evidence.projection_id,
            evidence.pinned_revision, evidence.manifest_slot_count), ("workspace-a", "graph-configured",
                self.fixture.workspace.desired_realized_projection_id, self.fixture.workspace.desired_graph_revision, 2))
        self.assertEqual(tuple(binding.ref for binding in evidence.bindings), self.refs)
        for binding in evidence.bindings:
            self.assertEqual(binding.source.ref, binding.ref)
            self.assertEqual(binding.birth.ref, binding.ref)
            self.assertEqual(binding.source.identity, self.fixture.original.identity)
            self.assertEqual(binding.source.source.original_event_id, self.fixture.original.original_start_event.event_id)
            self.assertEqual(binding.source.birth_identity, binding.birth.identity)
            self.assertEqual(binding.source.birth_artifact_id, binding.ref.artifact_id)
            self.assertEqual(binding.birth.identity, binding.birth.birth_identity)

    def assert_unavailable(self, evidence):
        self.assert_closed(evidence, "unavailable")

    def assert_closed(self, evidence, state):
        self.assertEqual(evidence.state, state)
        self.assertEqual(evidence.bindings, ())
        for field in ("workspace_id", "graph_id", "projection_id", "pinned_revision", "manifest_slot_count"):
            self.assertIsNone(getattr(evidence, field), "unavailable result retained partial proof")

    def test_all_and_selected_node_reads_return_complete_direct_binding_proofs(self):
        self.fixture.advance()
        before = self.base.retained_snapshot()
        self.assert_complete_bindings(self.read_current())
        self.assert_complete_bindings(self.read_current(node_id="api"))
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def test_desired_only_edit_preserves_authoritative_current_occurrence(self):
        self.fixture.advance()
        with self.base.unit_of_work() as uow:
            changed = uow.stores.workspaces.set_desired_graph("workspace-a", "graph-current")
            uow.commit()
        self.assertGreater(changed.desired_graph_revision, self.fixture.workspace.desired_graph_revision)
        self.assertEqual(changed.desired_graph_id, "graph-current")
        before = self.base.retained_snapshot()
        self.assert_complete_bindings(self.read_current())
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def test_inverse_known_staged_refs_are_absent_then_present_with_claims_unchanged(self):
        # Actual installation completed, but its graph is not yet accepted.
        before = self.base.retained_snapshot()
        staged = self.read_use(self.refs)
        self.assertEqual(staged.state, "complete")
        self.assertEqual((staged.graph_id, staged.projection_id, staged.pinned_revision, staged.manifest_slot_count),
            (self.fixture.workspace.current_graph_id, self.fixture.workspace.current_realized_projection_id, 1, 0))
        self.assertEqual(staged.bindings, ())
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)
        self.fixture.advance()
        before = self.base.retained_snapshot()
        self.assert_complete_bindings(self.read_use(self.refs))
        one = self.read_use((self.refs[0],))
        self.assertEqual(one.state, "complete")
        self.assertEqual(one.manifest_slot_count, 2)
        self.assertEqual(tuple(binding.ref for binding in one.bindings), (self.refs[0],))
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def test_inverse_unknown_or_conflicting_ref_is_unavailable_even_without_current_slots(self):
        before = self.base.retained_snapshot()
        # Both are valid ref values; neither is established original evidence.
        for candidate in (replace(self.refs[0], allocation_id="cfg-" + "f" * 64),
                replace(self.refs[0], target_path="/etc/cpk/conflicting.json")):
            with self.subTest(allocation=candidate.allocation_id, path=candidate.target_path):
                self.assert_unavailable(self.read_use((candidate,)))
                self.assertEqual(self.base.retained_snapshot(), before)
                self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def create_empty(self):
        return WorkspaceCommandService(self.base.unit_of_work, clock=lambda: "2026-07-22T14:00:00Z",
            id_factory=lambda: "graph-empty").create(CreateWorkspace("workspace-empty", "Empty", "operator-a",
                IdempotencyKey("create-empty")))

    def empty_reads(self, *, observations=None):
        return (self.read("read_current_configuration", "workspace-empty", observations=observations),
            self.read("read_configuration_use", "workspace-empty", (), observations=observations))

    def test_missing_initialization_returns_closed_unavailable_for_both_reads(self):
        self.create_empty()
        self.assertTrue(all(value.state == "complete" for value in self.empty_reads()))
        self.base.connection.execute("DELETE FROM cpk_workspace_initializations WHERE workspace_id='workspace-empty'")
        before = self.base.retained_snapshot()
        for value in self.empty_reads():
            self.assert_unavailable(value)
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def test_corrupt_initialization_returns_closed_unavailable_for_both_reads(self):
        self.create_empty()
        self.assertTrue(all(value.state == "complete" for value in self.empty_reads()))
        self.base.connection.execute("UPDATE cpk_workspace_initializations SET graph_descriptor_sha256=%s "
            "WHERE workspace_id='workspace-empty'", ("0" * 64,))
        before = self.base.retained_snapshot()
        for value in self.empty_reads():
            self.assert_unavailable(value)
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def test_oversized_initialization_material_returns_capacity_before_transport(self):
        self.create_empty()
        self.assertTrue(all(value.state == "complete" for value in self.empty_reads()))
        # Defensive committed corruption, not owner-valid initialization growth.
        marker = "private-origin-canary"
        self.base.connection.execute("UPDATE cpk_graph_versions SET metadata=jsonb_build_object('padding',%s::text) "
            "WHERE graph_id='graph-empty'", (marker * 2000,))
        before = self.base.retained_snapshot()
        observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)
        for value in self.empty_reads(observations=observed):
            self.assert_closed(value, "capacity")
            self.assertNotIn(marker, repr(value))
        self.assertGreater(observed["statements"], 0)
        self.assertLessEqual(observed["largest_cell"], 16384)
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def test_original_initialization_is_an_explicit_complete_empty_snapshot(self):
        created = self.create_empty()
        before = self.base.retained_snapshot()
        evidence = self.read_current(workspace_id="workspace-empty")
        self.assertEqual(evidence.state, "complete")
        self.assertEqual((evidence.workspace_id, evidence.graph_id, evidence.projection_id),
            ("workspace-empty", created.current_graph.graph_id, created.workspace.current_realized_projection_id))
        self.assertIsNone(evidence.pinned_revision)
        self.assertEqual(evidence.manifest_slot_count, 0)
        self.assertEqual(evidence.bindings, ())
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def test_argument_refusals_are_closed_and_unknown_node_is_complete_empty(self):
        self.fixture.advance()
        before = self.base.retained_snapshot()
        for arguments in (("read_current_configuration", ""),
                ("read_current_configuration", "workspace-missing"),
                ("read_configuration_use", "workspace-a", list(self.refs)),
                ("read_configuration_use", "workspace-a", (self.refs[0], self.refs[0]))):
            with self.subTest(arguments=arguments[:2]):
                self.assert_unavailable(self.read(*arguments))
        self.assert_unavailable(self.read_current(node_id="invalid\nnode"))
        self.assert_closed(self.read_use((self.refs[0],) * 33), "capacity")
        unknown = self.read_current(node_id="absent")
        self.assertEqual((unknown.state, unknown.manifest_slot_count, unknown.bindings), ("complete", 2, ()))
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def test_nested_reads_charge_actual_queries_to_one_budget_without_reset(self):
        from control_plane_kit_operations._configuration_preparation import _configuration_accounting
        self.fixture.advance()
        before = self.base.retained_snapshot()
        observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)
        states = []
        # Real successive reads under one caller's logical-operation ledger.
        # No seeded counters, fabricated rows or altered query results.
        with _configuration_accounting(("test-current-observations", "workspace-a")) as accounting:
            for _ in range(256):
                result = self.read("read_current_configuration", "workspace-a", observations=observed)
                states.append(result.state)
                if result.state != "complete":
                    self.assert_closed(result, "capacity")
                    break
            self.assertEqual(states[0], "complete")
            self.assertEqual(states[-1], "capacity")
            self.assertGreater(len(states), 1)
            self.assertEqual(accounting.used.statements, observed["statements"])
            self.assertGreaterEqual(accounting.used.records, observed["rows"])
            self.assertGreaterEqual(accounting.used.accounted_bytes, observed["bytes"])
            self.assertLessEqual(accounting.used.records, 4096)
            self.assertLessEqual(accounting.used.accounted_bytes, 16 * 1024 * 1024)
        self.assert_complete_bindings(self.read_current())
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)


class PostgresConfigurationReadIsolationTests(unittest.TestCase):
    def setUp(self):
        self.reader = PostgresConfigurationCurrentReadTests()
        self.reader.node_ids = ("api", "worker")
        self.addCleanup(self.cleanup_fixture)
        self.reader.setUp()
        self.fixture, self.base = self.reader.fixture, self.reader.base
        self.fixture.advance()
        self.api_refs = self.fixture.originals["api"].intent.configuration_instances.instances
        self.worker_refs = self.fixture.originals["worker"].intent.configuration_instances.instances
        queries = []
        control = self.reader.read("read_current_configuration", "workspace-a", source_queries=queries)
        self.assertEqual((control.state, control.manifest_slot_count), ("complete", 4))
        self.assertEqual(tuple(binding.ref for binding in control.bindings), self.fixture.refs)
        self.assertEqual(len({binding.source.identity for binding in control.bindings}), 2)
        self.assert_source_keys(queries, ("api", "worker"))
        for binding in control.bindings:
            self.assertEqual(binding.source.identity, self.fixture.originals[binding.ref.node_id].identity)
            self.assertEqual(binding.birth.identity, binding.source.identity)

    def cleanup_fixture(self):
        self.assertTrue(self.reader.doCleanups(), "nested current-read fixture cleanup failed")

    def assert_api_complete(self):
        requests = (("read_current_configuration", ("workspace-a",), {"node_id": "api"}),
            ("read_configuration_use", ("workspace-a", self.api_refs), {}))
        for name, arguments, keywords in requests:
            queries = []
            value = self.reader.read(name, *arguments, source_queries=queries, **keywords)
            self.assertEqual((value.state, value.manifest_slot_count), ("complete", 4))
            self.assertEqual(tuple(binding.ref for binding in value.bindings), self.api_refs)
            # Observe dispatched point keys before execution, including a
            # corrupt source query that could return only null markers.
            self.assert_source_keys(queries, ("api",))
            for binding in value.bindings:
                self.assertEqual(binding.source.identity, self.fixture.originals["api"].identity)
                self.assertEqual(binding.birth.identity, binding.source.identity)

    def assert_source_keys(self, queries, nodes):
        self.assertEqual(set(queries), {(table, ("run-config", "start-" + node, 1))
            for node in nodes for table in ("cpk_effect_attempt_intents", "cpk_effect_attempt_outcomes")})

    def retained_state(self):
        return (self.base.retained_snapshot(),
            self.base.connection.execute("SELECT * FROM cpk_effect_attempt_intents WHERE run_id='run-config' "
                "ORDER BY activity_id,attempt").fetchall(),
            self.base.connection.execute("SELECT * FROM cpk_effect_configuration_refs WHERE run_id='run-config' "
                "ORDER BY activity_id,attempt,artifact_id").fetchall())

    def assert_retained(self, before):
        self.assertEqual(self.retained_state(), before)
        self.assertEqual(self.fixture.protective_claims(), self.fixture.claims)

    def test_unselected_source_corruption_does_not_poison_requested_proof(self):
        self.base.connection.execute("UPDATE cpk_effect_attempt_intents SET preimage=preimage || ' '::bytea "
            "WHERE run_id='run-config' AND activity_id='start-worker' AND attempt=1")
        before = self.retained_state()
        self.assert_api_complete()
        for value in (self.reader.read_current(), self.reader.read_current(node_id="worker"),
                self.reader.read_use(self.worker_refs)):
            self.reader.assert_unavailable(value)
        self.assert_retained(before)

    def assert_every_read_unavailable(self):
        for value in (self.reader.read_current(), self.reader.read_current(node_id="api"),
                self.reader.read_current(node_id="absent"), self.reader.read_use(self.api_refs),
                self.reader.read_use(())):
            self.reader.assert_unavailable(value)

    def test_missing_unselected_slot_invalidates_every_current_read(self):
        self.base.connection.execute("DELETE FROM cpk_configuration_accepted_slots WHERE workspace_id='workspace-a' "
            "AND pinned_revision=%s AND node_id='worker' AND artifact_id='settings'",
            (self.fixture.workspace.desired_graph_revision,))
        before = self.retained_state()
        self.assert_every_read_unavailable()
        self.assert_retained(before)

    def test_unselected_full_ref_corruption_invalidates_even_empty_requests(self):
        self.base.connection.execute("UPDATE cpk_effect_configuration_refs SET ref_preimage=ref_preimage || ' '::bytea "
            "WHERE run_id='run-config' AND activity_id='start-worker' AND attempt=1 AND artifact_id='settings'")
        before = self.retained_state()
        self.assert_every_read_unavailable()
        self.assert_retained(before)

    def test_defensive_oversized_unselected_source_keeps_narrow_read_complete(self):
        # This is deliberate corrupt history, not a claim that an immutable
        # owner-admitted snapshot can outgrow its full-source admission budget.
        marker = "private-worker-source-canary"
        self.base.connection.execute("UPDATE cpk_activity_events SET payload=jsonb_set(payload,'{evidence,padding}',"
            "to_jsonb(%s::text)) WHERE event_id=%s", (marker * 1000, self.fixture.direct_events["worker"]))
        before = self.retained_state()
        self.assert_api_complete()
        observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)
        for value in (self.reader.read("read_current_configuration", "workspace-a", observations=observed),
                self.reader.read("read_current_configuration", "workspace-a", node_id="worker", observations=observed),
                self.reader.read("read_configuration_use", "workspace-a", self.worker_refs, observations=observed)):
            self.reader.assert_closed(value, "capacity")
            self.assertNotIn(marker, repr(value))
        self.assertGreater(observed["statements"], 0)
        self.assertLessEqual(observed["largest_cell"], 16384)
        self.assert_retained(before)
