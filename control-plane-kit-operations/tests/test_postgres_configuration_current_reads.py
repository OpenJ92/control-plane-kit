"""Public current/use observations over genuine accepted and staged evidence."""
from dataclasses import replace
import re
import unittest

import psycopg

from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService
from tests import test_postgres_configuration_acceptance_membership as membership_fixture


class PostgresConfigurationCurrentReadTests(unittest.TestCase):
    def setUp(self):
        self.fixture = membership_fixture.PostgresConfigurationAcceptanceMembershipTests()
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

    def read(self, name, *args, **kwargs):
        statements = []

        class ObservedConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, attribute):
                return getattr(self.connection, attribute)

            def execute(self, query, parameters=None):
                statements.append(str(query))
                return self.connection.execute(query, parameters)

        with PostgresUnitOfWork(lambda: ObservedConnection(psycopg.connect(self.base.database_url))) as uow:
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
        self.assertEqual(evidence.state, "unavailable")
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

    def test_original_initialization_is_an_explicit_complete_empty_snapshot(self):
        created = WorkspaceCommandService(self.base.unit_of_work, clock=lambda: "2026-07-22T14:00:00Z",
            id_factory=lambda: "graph-empty").create(CreateWorkspace("workspace-empty", "Empty", "operator-a",
                IdempotencyKey("create-empty")))
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
