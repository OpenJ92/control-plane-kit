"""Gate 1: publishing saved material cannot bypass selection preparation."""

from dataclasses import replace
import unittest
import uuid

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.products import (
    ProductDescriptorCodec, ProductIdentity, ProductInstanceConfiguration, instantiate_product,
)
from control_plane_kit_core.secret_selections import SecretVersionSelection
from control_plane_kit_core.secrets import SecretFileDelivery, SecretReference, SecretUseIntent
from control_plane_kit_core.topology import compile_topology
from control_plane_kit_operations.planning import (
    DesiredGraphCommandError, DesiredGraphCommandService, SetDesiredGraph,
)
from control_plane_kit_operations.products import InlineDescriptorSource
from control_plane_kit_operations.workflows import IdempotencyKey
from draft_catalogue_fixture import NOW
from draft_selection_fixture import DraftSelectionFixture
from secret_selection_authoring_fixture import admit_selection_reference


class SecretSelectionAuthoringEntranceTests(DraftSelectionFixture, unittest.TestCase):
    def selected_graph(self):
        reference = SecretReference("secret://workspace-secrets/workspace-a/token")
        intent = SecretUseIntent.APPLICATION_CONTROL_TOKEN
        product = replace(self.product, identity=ProductIdentity("catalogue", "secret-app", 1),
            runtime_contract=replace(self.product.runtime_contract,
                secret_deliveries=(SecretFileDelivery("/run/secrets/token", reference, intent),)))
        document = ProductDescriptorCodec().encode_document(product)
        with self.unit_of_work() as uow:
            uow.stores.registered_products.register(workspace_id="workspace-a",
                descriptor_document=document, source=InlineDescriptorSource(),
                imported_by="operator-a", imported_at=NOW)
            uow.commit()
        admit_selection_reference(self.unit_of_work, reference, intent)
        block = instantiate_product(product, "app",
            ProductInstanceConfiguration.from_contract(product.runtime_contract))
        graph = compile_topology(DeploymentTopology("selected", DockerRuntime(children=(block,))))
        return replace(graph, nodes={key: replace(node,
            secret_selections=(SecretVersionSelection(reference, intent, "version-1"),))
            for key, node in graph.nodes.items()})

    def test_planning_command_refuses_unprepared_selection_without_action_or_pointer_write(self):
        graph = self.selected_graph()
        workspace = self.workspace()
        command = SetDesiredGraph(session_id=self.sessions["workspace-a"],
            workspace_id="workspace-a", actor_id="operator-a", graph=graph,
            expected_desired_graph_id=workspace.desired_graph_id,
            expected_desired_realized_projection_id=workspace.desired_realized_projection_id,
            expected_desired_graph_revision=workspace.desired_graph_revision,
            idempotency_key=IdempotencyKey("unprepared-selection"))
        before = self.truth()
        service = DesiredGraphCommandService(self.unit_of_work,
            clock=lambda: NOW, id_factory=lambda: uuid.uuid4().hex)
        with self.assertRaises(DesiredGraphCommandError):
            service.execute(command)
        self.assertEqual(self.truth(), before)

    def test_saved_selected_revision_confers_no_authority_and_is_not_rewritten(self):
        graph = self.selected_graph()
        created = self.catalogue().execute(self.create_command(graph=graph))
        before = self.truth()
        with self.unit_of_work() as uow:
            original = uow.stores.graphs.get(created.graph_id).graph_descriptor
        with self.assertRaises(self.api.DesiredTopologyDraftError):
            self.catalogue().execute(self.select_command(created))
        self.assertEqual(self.truth(), before)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.graphs.get(created.graph_id).graph_descriptor, original)


if __name__ == "__main__":
    unittest.main()
