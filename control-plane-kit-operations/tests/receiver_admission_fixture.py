"""C2 command fixtures: real stores; no substitute admission or history predicate."""

from dataclasses import replace
import uuid

from control_plane_kit_operations import receiver_lifecycle
from control_plane_kit_operations.desired_realized_projections import (
    DesiredRealizedProjectionCommandService, PublishDesiredRealizedProjection,
)
from control_plane_kit_operations.planning import DesiredGraphCommandService, SetDesiredGraph
from control_plane_kit_operations.records import RealizedGraphProjectionKind, RealizedGraphProjectionRecord
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.draft_catalogue_fixture import NOW
from tests.receiver_storage_fixture import ReceiverStorageFixture
from tests.saved_preparation_fixture import SavedPreparationFixture


class ReceiverAdmissionFixture(ReceiverStorageFixture, SavedPreparationFixture):
    def setUp(self):
        # Older fixtures use both bare and tests.* module imports. Select one
        # complete setup chain explicitly so one schema is created and the
        # catalogue/selection/preparation APIs are all initialized.
        SavedPreparationFixture.setUp(self)

    def require_expectation(self):
        value = getattr(receiver_lifecycle, "ReceiverLifecycleExpectation", None)
        self.assertTrue(callable(value), "#1903 missing five-pin ReceiverLifecycleExpectation")
        return value

    def pins(self, workspace="workspace-a"):
        value = self.require_expectation()
        with self.unit_of_work() as uow:
            current = uow.stores.workspaces.get(workspace)
        # A caller's explicit snapshot for command construction, never a
        # production fallback for omitted expectations.
        return value(current_graph_id=current.current_graph_id,
            current_realized_projection_id=current.current_realized_projection_id,
            desired_graph_id=current.desired_graph_id,
            desired_realized_projection_id=current.desired_realized_projection_id,
            desired_graph_revision=current.desired_graph_revision)

    def desired_command(self, *, graph=None, key="desired", workspace="workspace-a", pins=None):
        pins = self.pins(workspace) if pins is None else pins
        return SetDesiredGraph(session_id=self.sessions[workspace], workspace_id=workspace,
            actor_id="operator-a", graph=self.receiver_graph(workspace=workspace)[0] if graph is None else graph,
            expected_desired_graph_id=pins.desired_graph_id,
            expected_desired_realized_projection_id=pins.desired_realized_projection_id,
            expected_desired_graph_revision=pins.desired_graph_revision,
            idempotency_key=IdempotencyKey(key), receiver_lifecycle=pins)

    def desired_service(self, *, uow=None, ids=None):
        return DesiredGraphCommandService(uow or self.unit_of_work, clock=lambda: NOW,
            id_factory=ids or (lambda: uuid.uuid4().hex))

    def receiver_create(self, *, graph=None, key="create-receiver", workspace="workspace-a"):
        return replace(self.create_command(key=key, workspace=workspace,
            graph=self.receiver_graph(workspace=workspace)[0] if graph is None else graph),
            receiver_lifecycle=self.pins(workspace))

    def receiver_revise(self, draft, *, graph=None, key="revise-receiver"):
        return replace(self.revise_command(draft, key=key, expected=draft.revision,
            graph=self.receiver_graph()[0] if graph is None else graph), receiver_lifecycle=self.pins())

    def receiver_select(self, draft, *, key="select-receiver"):
        return replace(self.select_command(draft, key=key), receiver_lifecycle=self.pins())

    def publication(self, *, graph=None, key="publish-receiver", pins=None):
        pins = self.pins() if pins is None else pins
        with self.unit_of_work() as uow:
            original = uow.stores.realized_graphs.get(pins.desired_realized_projection_id)
        projection = original if graph is None else RealizedGraphProjectionRecord.from_graph(
            projection_id=uuid.uuid4().hex, workspace_id="workspace-a",
            source_authored_graph_id=pins.desired_graph_id,
            projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER, projection_key=uuid.uuid4().hex,
            graph=graph, created_by="operator-a", created_at=NOW)
        return PublishDesiredRealizedProjection(session_id=self.sessions["workspace-a"],
            workspace_id="workspace-a", actor_id="operator-a",
            expected_authored_graph_id=pins.desired_graph_id,
            expected_realized_projection_id=pins.desired_realized_projection_id,
            expected_desired_graph_revision=pins.desired_graph_revision, projection=projection,
            source_operation_id="receiver-publication", source_operation_version=1,
            idempotency_key=IdempotencyKey(key), receiver_lifecycle=pins)

    def publisher(self, *, uow=None):
        return DesiredRealizedProjectionCommandService(uow or self.unit_of_work,
            clock=lambda: NOW, action_id_factory=lambda: uuid.uuid4().hex)

    def admission_truth(self):
        return {name: self.rows(name) for name in (
            "cpk_workspaces", "cpk_graph_versions", "cpk_realized_graph_projections",
            "cpk_desired_topology_drafts", "cpk_desired_topology_draft_revisions",
            "cpk_operation_actions", "cpk_graph_receiver_introductions",
            "cpk_graph_receiver_bindings")}

    def command_action(self, command):
        with self.unit_of_work() as uow:
            return uow.stores.activity_history.action_for_idempotency(
                command.session_id, command.idempotency_key.value)

    def bindings_for_graph(self, graph_id):
        with self.unit_of_work() as uow:
            identity = uow.stores.realized_graphs.identity_for_authored("workspace-a", graph_id)
            stored = uow.stores.realized_graphs.get(identity.projection_id)
            self.assertEqual(stored, identity)
            return uow.stores.graphs.receiver_bindings("workspace-a", graph_id, identity.projection_id)

    def mixed_graph(self, *graphs):
        nodes = {name: node for graph in graphs for name, node in graph.nodes.items()}
        return replace(graphs[0], nodes=nodes, runtimes={
            "docker": replace(graphs[0].runtimes["docker"], children=tuple(nodes))})

    def forbid_allocation(self):
        self.fail("refusal/replay allocated a new durable identity")
