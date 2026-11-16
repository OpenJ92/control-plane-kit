"""PostgreSQL planning provenance for the retained-runtime worker profile."""
from dataclasses import replace
import unittest
from uuid import uuid4

from control_plane_kit_core.receiver_management_routes import ReceiverManagementRoutesCodec
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService, DesiredGraphCommandService, RequestActivityPlan, SetDesiredGraph)
from control_plane_kit_operations.receiver_lifecycle import ReceiverLifecycleExpectation
from control_plane_kit_operations.records import GraphVersionRecord
from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority
from control_plane_kit_operations.workflows import IdempotencyKey, InvalidOperationCommand
from tests.draft_catalogue_fixture import DraftCatalogueFixture, NOW
from tests.test_managed_image_update_admission import registered_bootstrap_graphs
from control_plane_kit_operations.runtime_management_admission import runtime_management_execution_is_unsupported


class ExistingRuntimeBootstrapPlanningTests(DraftCatalogueFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.current, self.desired, products = registered_bootstrap_graphs(self)
        with self.unit_of_work() as uow:
            for product in products:
                uow.stores.registered_products.register(workspace_id="workspace-a",
                    descriptor_document=product.descriptor_document, source=product.source,
                    imported_by="operator-a", imported_at=NOW)
            runtime = self.desired.runtimes["docker"]
            uow.stores.runtime_authorities.register(workspace_id="workspace-a", authority_ref=runtime.authority_ref,
                runtime_kind=runtime.kind, authority=LocalDockerSocketAuthority(), admitted_at=NOW, admitted_by="operator-a")
            self.base = self.save_graph(uow.stores, self.current)
            uow.stores.workspaces.set_current_graph("workspace-a", self.base)
            uow.commit()

    def save_graph(self, stores, graph):
        identity = uuid4().hex
        stores.graphs.save(GraphVersionRecord.from_graph(graph_id=identity, workspace_id="workspace-a",
            version=stores.graphs.next_version_for_workspace("workspace-a"), graph=graph,
            created_by="operator-a", created_at=NOW))
        return identity

    def request(self, graph):
        with self.unit_of_work() as uow:
            state = uow.stores.workspaces.get("workspace-a")
        session = self.sessions["workspace-a"]
        edit = DesiredGraphCommandService(self.unit_of_work, clock=lambda: NOW, id_factory=lambda: uuid4().hex)
        expectation = ReceiverLifecycleExpectation(self.base, state.current_realized_projection_id,
            state.desired_graph_id, state.desired_realized_projection_id, state.desired_graph_revision)
        saved = edit.execute(SetDesiredGraph(session, "workspace-a", "operator-a", graph,
            state.desired_graph_id, IdempotencyKey(uuid4().hex), state.desired_realized_projection_id,
            state.desired_graph_revision, receiver_lifecycle=expectation))
        command = RequestActivityPlan(session, "workspace-a", "operator-a",
            self.base, saved.graph_version_id, IdempotencyKey(uuid4().hex), state.current_realized_projection_id,
            saved.desired_realized_projection_id, saved.desired_graph_revision)
        return ActivityPlanningCommandService(self.unit_of_work, clock=lambda: NOW,
            id_factory=lambda: uuid4().hex).execute(command).plan_record

    def test_wrong_or_extra_route_refuses_preparation_and_original_plan_substitution(self):
        approved_subject = self.request(self.desired)
        before = self.rows("cpk_activity_plans")
        codec = ReceiverManagementRoutesCodec()
        gateway = self.desired.node("gateway")
        artifact, = (item for item in gateway.configuration_artifacts if item.artifact_id == "gateway-health-targets")
        routes = codec.decode_bytes(artifact.content.encode())
        first, *remaining = routes.targets
        extra = replace(first, target_id="extra", target=replace(first.target, receiver_id="f" * 32))
        with self.unit_of_work() as uow:
            products = uow.stores.registered_products.list_active("workspace-a")
        for targets in ((replace(first, origin="http://other:8000"), *remaining), (*routes.targets, extra)):
            encoded = codec.encode_bytes(replace(routes, targets=targets)).decode()
            changed = self.desired.update_node(replace(gateway, configuration_artifacts=tuple(
                replace(item, content=encoded, source_digest=None) if item == artifact else item
                for item in gateway.configuration_artifacts)))
            with self.assertRaises(InvalidOperationCommand):
                self.request(changed)
            self.assertTrue(runtime_management_execution_is_unsupported(self.current, changed, approved_subject.plan,
                registered_products=products, derivation_profile=approved_subject.derivation_profile))
        self.assertEqual(self.rows("cpk_activity_plans"), before)
        for table in ("cpk_approval_requests", "cpk_execution_requests", "cpk_effect_attempts"):
            self.assertEqual(self.rows(table), [])
