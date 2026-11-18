"""#1903 no partial admission through supported graph/projection/pointer/head APIs."""

from dataclasses import replace
import unittest

from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.desired_topology_drafts import (
    DesiredTopologyDraftRecord, DesiredTopologyDraftRevisionRecord,
)
from control_plane_kit_operations.graph_authoring import (
    GraphAuthoringError, GraphAuthoringService, GraphIdentityConflict,
    SetDesiredGraphCommand, set_desired_graph_in_unit_of_work,
)
from control_plane_kit_operations.records import (
    GraphVersionRecord, RealizedGraphProjectionKind, RealizedGraphProjectionRecord, WorkspaceRecord,
)
from control_plane_kit_operations.planning import DesiredGraphCommandError
from tests.draft_catalogue_fixture import NOW
from tests.receiver_admission_fixture import ReceiverAdmissionFixture
from tests.lifecycle_lock_fixture import LifecycleLockFixture, LIFECYCLE_LOCK, WORKSPACE_LOCK


class ReceiverAdmissionBoundaryTests(LifecycleLockFixture, ReceiverAdmissionFixture, unittest.TestCase):
    def fresh_record(self, graph=None):
        return GraphVersionRecord.from_graph(graph_id="direct-receiver", workspace_id="workspace-a",
            version=2, graph=self.receiver_graph()[0] if graph is None else graph,
            created_by="operator-a", created_at=NOW)

    def test_public_graph_save_cannot_commit_unowned_receiver_graph_even_with_guard(self):
        before = self.admission_truth()
        with self.assertRaises(ValueError):
            with self.unit_of_work() as uow:
                uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                uow.stores.graphs.save(self.fresh_record())
                uow.commit()
        self.assertEqual(self.admission_truth(), before)

    def test_public_projection_save_cannot_add_receiver_material_to_legacy_authored_graph(self):
        before = self.admission_truth()
        record = RealizedGraphProjectionRecord.from_graph(projection_id="fresh-receiver-projection",
            workspace_id="workspace-a", source_authored_graph_id="workspace-a-current",
            projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
            projection_key="fresh-receiver", graph=self.receiver_graph()[0],
            created_by="operator-a", created_at=NOW)
        with self.assertRaises(ValueError):
            with self.unit_of_work() as uow:
                uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                uow.stores.realized_graphs.save(record)
                uow.commit()
        self.assertEqual(self.admission_truth(), before)

    def test_standalone_authoring_and_action_free_helper_refuse_receiver_material(self):
        current = self.workspace()
        command = SetDesiredGraphCommand("workspace-a", "operator-a", self.receiver_graph()[0],
            current.desired_graph_id, current.desired_realized_projection_id, current.desired_graph_revision)
        before = self.admission_truth()
        with self.assertRaises(GraphAuthoringError):
            GraphAuthoringService(self.unit_of_work, graph_id_factory=lambda: "standalone",
                clock=lambda: NOW).set_desired_graph(command)
        with self.assertRaises(GraphAuthoringError):
            with self.unit_of_work() as uow:
                guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                set_desired_graph_in_unit_of_work(uow, command, lifecycle_guard=guard,
                    graph_id="helper", created_at=NOW)
                uow.commit()
        self.assertEqual(self.admission_truth(), before)

    def test_legacy_save_and_graph_identity_conflict_keep_original_contract(self):
        record = self.fresh_record(DeploymentGraph("legacy"))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.graphs.save(record), record)
            uow.commit()
        before = self.admission_truth()
        with self.assertRaises(GraphIdentityConflict):
            with self.unit_of_work() as uow:
                uow.stores.graphs.save(record)
                uow.commit()
        self.assertEqual(self.admission_truth(), before)

    def test_exact_retained_projection_replay_adds_no_binding_or_permission(self):
        result = self.desired_service().execute(self.desired_command())
        before = self.admission_truth()
        with self.unit_of_work() as uow:
            stored = uow.stores.realized_graphs.get(result.desired_realized_projection_id)
            self.assertEqual(uow.stores.realized_graphs.save(stored), stored)
            uow.commit()
        self.assertEqual(self.admission_truth(), before)

    def test_all_direct_pointer_variants_refuse_receiver_to_empty_or_current_advancement(self):
        result = self.desired_service().execute(self.desired_command())
        current = self.workspace()
        with self.unit_of_work() as uow:
            empty = uow.stores.realized_graphs.identity_for_authored("workspace-a", "workspace-a-current")
        variants = (
            lambda store: store.set_desired_graph("workspace-a", "workspace-a-current"),
            lambda store: store.set_desired_graph("workspace-a", "workspace-a-current", empty.projection_id),
            lambda store: store.set_current_graph("workspace-a", result.graph_version_id),
            lambda store: store.set_current_graph("workspace-a", "workspace-a-current", empty.projection_id),
            lambda store: store.compare_and_set_current_graph("workspace-a",
                expected_graph_id=current.current_graph_id,
                expected_realized_projection_id=current.current_realized_projection_id,
                replacement_graph_id=result.graph_version_id,
                replacement_realized_projection_id=result.desired_realized_projection_id,
                expected_desired_graph_id=current.desired_graph_id,
                expected_desired_realized_projection_id=current.desired_realized_projection_id,
                expected_desired_graph_revision=current.desired_graph_revision),
            lambda store: store.compare_and_set_desired_projection("workspace-a",
                expected_authored_graph_id=current.desired_graph_id,
                expected_realized_projection_id=current.desired_realized_projection_id,
                expected_revision=current.desired_graph_revision,
                replacement_realized_projection_id=current.desired_realized_projection_id),
        )
        before = self.admission_truth()
        for index, mutate in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(ValueError):
                with self.unit_of_work() as uow:
                    mutate(uow.stores.workspaces)
                    uow.commit()
            self.assertEqual(self.admission_truth(), before)

    def test_direct_pointer_classification_takes_lifecycle_before_workspace(self):
        def execute(factory):
            with factory() as uow:
                result = uow.stores.workspaces.set_desired_graph("workspace-a", "workspace-a-current")
                uow.commit()
                return result
        before = self.admission_truth()
        with self.blocked_command(LIFECYCLE_LOCK, ("receiver-lifecycle:workspace-a",), execute) as future:
            self.assert_row_lockable(WORKSPACE_LOCK, ("workspace-a",))
            self.assertEqual(self.admission_truth(), before)
        self.assertEqual(future.result(timeout=1).desired_graph_id, "workspace-a-current")

    def test_public_draft_create_append_cannot_turn_copied_receiver_into_live_head(self):
        first = self.catalogue().execute(self.receiver_create())
        before = self.admission_truth()
        with self.assertRaises(ValueError):
            with self.unit_of_work() as uow:
                uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                uow.stores.desired_topology_drafts.create(DesiredTopologyDraftRecord(
                    "workspace-a", "copied-draft", "Copied", 1, "operator-a", NOW))
                uow.stores.desired_topology_drafts.append(DesiredTopologyDraftRevisionRecord(
                    "workspace-a", "copied-draft", 1, first.graph_id, "operator-a", NOW),
                    expected_head_revision=None)
                uow.commit()
        self.assertEqual(self.admission_truth(), before)

    def test_empty_workspace_bootstrap_remains_supported(self):
        record = WorkspaceRecord("empty-workspace", "Empty")
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.workspaces.create(record), record)
            uow.commit()
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.workspaces.get("empty-workspace"), record)

    def test_public_append_cannot_replace_receiver_head_with_legacy_graph(self):
        draft = self.catalogue().execute(self.receiver_create())
        before = self.admission_truth()
        with self.assertRaises(ValueError):
            with self.unit_of_work() as uow:
                uow.stores.desired_topology_drafts.append(DesiredTopologyDraftRevisionRecord(
                    "workspace-a", draft.draft_id, 2, "workspace-a-current", "operator-a", NOW),
                    expected_head_revision=1)
                uow.commit()
        self.assertEqual(self.admission_truth(), before)

    def test_populated_receiver_bootstrap_refuses_before_workspace_insert(self):
        admitted = self.desired_service().execute(self.desired_command())
        record = WorkspaceRecord("bootstrap-workspace", "Bootstrap",
            current_graph_id=admitted.graph_version_id,
            current_realized_projection_id=admitted.desired_realized_projection_id)
        # Existing graph/projection truth is workspace-owned, so a new workspace
        # also cannot borrow it. Refusal must be bounded and prewrite, not raw FK
        # detail after attempting a pointer-bearing insertion.
        statements = []
        before = self.admission_truth()
        with self.assertRaises(ValueError):
            with self.observed_uow(statements)() as uow:
                uow.stores.workspaces.create(record)
                uow.commit()
        self.assertEqual(self.admission_truth(), before)
        self.assertFalse(any(query.startswith("INSERT INTO cpk_workspaces") for query in statements))

    def test_receiver_admission_first_forces_waiting_direct_legacy_pointer_to_reclassify(self):
        command = self.desired_command()
        def admit(factory):
            return self.desired_service(uow=factory).execute(command)
        def point(factory):
            with factory() as uow:
                result = uow.stores.workspaces.set_desired_graph("workspace-a", "workspace-a-current")
                uow.commit()
                return result
        first, second = self.opposing_commands(admit, point,
            lambda query, params: "pg_advisory_xact_lock" in query
                and params == ("receiver-lifecycle:workspace-a",))
        admitted = first.result(timeout=1)
        with self.assertRaises(ValueError):
            second.result(timeout=1)
        self.assertEqual(self.workspace().desired_graph_id, admitted.graph_version_id)
        self.assertEqual(self.introduction().introducing_graph_id, admitted.graph_version_id)

    def test_direct_legacy_pointer_first_invalidates_waiting_receiver_expected_generation(self):
        command = self.desired_command()
        def point(factory):
            with factory() as uow:
                result = uow.stores.workspaces.set_desired_graph("workspace-a", "workspace-a-current")
                uow.commit()
                return result
        def admit(factory):
            return self.desired_service(uow=factory).execute(command)
        first, second = self.opposing_commands(point, admit,
            lambda query, params: "pg_advisory_xact_lock" in query
                and params == ("receiver-lifecycle:workspace-a",))
        updated = first.result(timeout=1)
        with self.assertRaises(DesiredGraphCommandError):
            second.result(timeout=1)
        self.assertEqual(self.workspace(), updated)
        self.assertEqual(self.rows("cpk_graph_receiver_introductions"), [])
        self.assertEqual(self.rows("cpk_graph_receiver_bindings"), [])
