"""Real PostgreSQL setup for staged B integrity, never C admission evidence."""

from dataclasses import replace
from importlib import import_module
from importlib.util import find_spec
import json
import uuid

from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationMediaType
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
from control_plane_kit_core.topology import validate_graph
from control_plane_kit_core.wrapper_configuration import WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT
from control_plane_kit_operations.desired_topology_drafts import (
    DesiredTopologyDraftRecord, DesiredTopologyDraftRevisionRecord,
)
from control_plane_kit_operations.records import (
    GraphVersionRecord, OperationActionRecord, RealizedGraphProjectionRecord,
)
from tests.draft_catalogue_fixture import DraftCatalogueFixture, NOW
from tests.runtime_management_fixtures import sdk_health_graph
from tests.test_delegation_signing_keys import PUBLIC_KEY_A


RECEIVER = "a" * 32
INTRODUCTIONS = "cpk_graph_receiver_introductions"
BINDINGS = "cpk_graph_receiver_bindings"


class ReceiverStorageFixture(DraftCatalogueFixture):
    def require_storage(self, store):
        for name in (
            "receiver_introduction", "receiver_bindings", "_reserve_receiver_introductions",
            "_persist_receiver_bindings", "_record_receiver_first_acceptance",
            "_record_receiver_retirement",
        ):
            self.assertTrue(callable(getattr(store, name, None)),
                            "#1897 missing graph-owned receiver storage: " + name)
        module = "control_plane_kit_operations.receiver_lifecycle"
        self.assertIsNotNone(find_spec(module), "#1897 receiver storage values are missing")
        return import_module(module)

    def receiver_graph(self, *, workspace="workspace-a", receiver=RECEIVER,
                       target_changes=None, artifact_changes=None, pretty=False):
        graph = sdk_health_graph()
        node = graph.node("api")
        declaration = WorkloadNodeControlSurfaceDeclaration(
            node.block_spec.control_surfaces[0], WorkloadNodeControlSurfaceDeclarationProfile.V2,
        )
        document = {
            "profile": "workload-node-control-configuration.v2",
            "target": dict(workspace_id=workspace, runtime_id="docker", node_id="api",
                           provider_socket_name="http", receiver_id=receiver),
            "declaration": declaration.descriptor(),
            "verifiers": [dict(purpose=purpose, issuer="test-issuer", public_keys=[dict(
                key_id="test-public", algorithm="ed25519", public_key_pem=PUBLIC_KEY_A,
            )]) for purpose in ("workload-node-control-surface-read", "workload-node-health-read")],
        }
        document["target"].update(target_changes or {})
        codec = ReceiverNodeControlConfigurationCodec()
        # Real Core decoding validates the fixture before the proposed B writer.
        configuration = codec.decode(document)
        content = json.dumps(document, indent=2) if pretty else codec.encode_bytes(configuration).decode()
        artifact = ConfigurationArtifact("selected", "/etc/test/receiver.json",
                                         ConfigurationMediaType.JSON, content)
        artifact = replace(artifact, **(artifact_changes or {}))
        unrelated = ConfigurationArtifact("application", "/etc/test/application.txt",
            ConfigurationMediaType.TEXT, '{"receiver_id":"not-a-receiver-configuration"}')
        node = replace(node, configuration_artifacts=(unrelated, artifact), public_environment=(
            PublicStaticEnvironmentBinding(WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT,
                                           "/etc/test/receiver.json"),
        ))
        graph = replace(graph, nodes={"api": node})
        validate_graph(graph).require_valid()
        return graph, artifact, declaration

    def material(self, uow, *, workspace="workspace-a", graph=None):
        if graph is None:
            graph = self.receiver_graph(workspace=workspace)[0]
        record = GraphVersionRecord.from_graph(
            graph_id=uuid.uuid4().hex, workspace_id=workspace,
            version=uow.stores.graphs.next_version_for_workspace(workspace),
            graph=graph, created_by="operator-a", created_at=NOW,
        )
        projection = RealizedGraphProjectionRecord.identity_for_authored(authored_record=record)
        uow.stores.graphs.save(record)
        uow.stores.realized_graphs.save(projection)
        return record, projection

    def action(self, uow, workspace="workspace-a"):
        history = uow.stores.activity_history
        action = OperationActionRecord(
            uuid.uuid4().hex, self.sessions[workspace],
            history.next_action_ordinal(self.sessions[workspace]),
            OperatorCommandKind.SET_DESIRED_GRAPH, "operator-a", created_at=NOW,
        )
        history.add_action(action)
        return action

    def draft(self, uow, graph):
        draft_id = uuid.uuid4().hex
        uow.stores.desired_topology_drafts.create(DesiredTopologyDraftRecord(
            graph.workspace_id, draft_id, "Receiver origin", 1, "operator-a", NOW,
        ))
        uow.stores.desired_topology_drafts.append(DesiredTopologyDraftRevisionRecord(
            graph.workspace_id, draft_id, 1, graph.graph_id, "operator-a", NOW,
        ), expected_head_revision=None)
        return draft_id

    def reserve(self, uow, graph, projection, action, guard, *, draft_id=None):
        self.require_storage(uow.stores.graphs)
        uow.stores.graphs._reserve_receiver_introductions(
            graph, projection, action_id=action.action_id, session_id=action.session_id,
            draft_id=draft_id, lifecycle_guard=guard,
        )

    def seed(self, *, workspace="workspace-a", graph=None, with_draft=False):
        with self.unit_of_work() as uow:
            self.require_storage(uow.stores.graphs)
            guard = uow.stores.graphs.lock_receiver_lifecycle(workspace)
            record, projection = self.material(uow, workspace=workspace, graph=graph)
            draft_id = self.draft(uow, record) if with_draft else None
            action = self.action(uow, workspace)
            self.reserve(uow, record, projection, action, guard, draft_id=draft_id)
            uow.stores.graphs._persist_receiver_bindings(record, projection, lifecycle_guard=guard)
            uow.commit()
        return record, projection, action, draft_id

    def introduction(self, workspace="workspace-a", receiver=RECEIVER):
        with self.unit_of_work() as uow:
            self.require_storage(uow.stores.graphs)
            return uow.stores.graphs.receiver_introduction(workspace, receiver)

    def truth(self):
        # Exact test-owned schema only; no live provider or shared database.
        return tuple(self.connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
                     for table in ("cpk_graph_versions", "cpk_realized_graph_projections",
                                   "cpk_operation_actions", "cpk_desired_topology_drafts",
                                   "cpk_desired_topology_draft_revisions", INTRODUCTIONS, BINDINGS))

    def assert_bounded(self, error, message):
        self.assertEqual(str(error), message)
        self.assertEqual(vars(error), {})
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
