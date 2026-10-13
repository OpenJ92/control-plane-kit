"""Immutable image transitions reuse the real Operations/Postgres command flow."""
from dataclasses import replace
import json

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.planning import ObserveNodeHealth, ReconcileNode, StartNode
from control_plane_kit_core.products import ProductDescriptorCodec, ProductReference
from tests import test_managed_configuration_update_execution as content
from tests import test_managed_application_chain as chain


class ManagedImageUpdateExecutionTests(content.ManagedConfigurationUpdateExecutionTests):
    # Inherit the maintained missing-receipt and fresh-pull-authority controls.
    # Only selected desired material differs; command/replay/receipt owners do not.
    def authored_graph(self):
        graph, documents, registry = super().authored_graph()
        node = graph.node("api")
        graph = graph.update_node(replace(node, metadata={**node.metadata,
            "oci_image": documents["api"].product.image.execution_reference}))
        return self.native_before_path_graph(graph), documents, registry

    async def desired_update(self):
        previous = self.documents["api"].product
        successor = replace(previous, identity=replace(previous.identity, contract_revision=2),
            image=replace(previous.image, digest="sha256:" + "b" * 64))
        self.successor_document = ProductDescriptorCodec().encode_document(successor)
        await self.invoke("command.product.import", ControlPlaneServiceRole.PLANNING,
            path={"workspace_id": "workspace-a"}, payload={
                "descriptor_document": json.loads(self.successor_document.content),
                "imported_at": chain.now(), "idempotency_key": "product-api-image-successor"})
        self.documents["api-image-successor"] = self.successor_document
        reference = ProductReference.from_document(self.successor_document)
        node = self.graph.node("api")
        return self.graph.update_node(replace(node, metadata={**node.metadata,
            "product_identity": reference.identity.key,
            "product_descriptor_digest": reference.descriptor_sha256.value,
            "oci_image": successor.image.execution_reference}))

    async def test_replacement_preserves_wrapper_origin_and_history_then_advances(self):
        await self.prepare_content()
        result = await self.execute_content()
        self.assertEqual((result["coordinator_status"], result["run_status"]), ("completed", "succeeded"))
        self.assertTrue(self.forged_managed_fold_refused)
        update, = self.runtime.calls[len(self.origin_calls):]
        self.assertIs(type(update.operation), ReconcileNode)
        self.assertEqual(update.operation.target.node_id, "api")
        original, = (value for value in self.origin_calls if type(value.operation) is StartNode
            and value.operation.target.node_id == "api")
        old_product, = original.products
        new_product, = update.products
        self.assertEqual(new_product.reference, ProductReference.from_document(self.successor_document))
        self.assertEqual(new_product.product.image, self.successor_document.product.image)
        self.assertNotEqual(new_product.reference, old_product.reference)
        self.assertNotEqual(new_product.product.image.digest, old_product.product.image.digest)
        self.assertEqual(new_product.product.runtime_contract, old_product.product.runtime_contract)
        old = {value.artifact_id: value for value in original.configuration_instances.instances}
        new = {value.artifact_id: value for value in update.configuration_instances.instances}
        self.assertEqual(old.keys(), new.keys())
        wrapper, = old.keys() - {"application"}
        self.assertEqual(new[wrapper], old[wrapper])
        self.assertEqual(new["application"].content_digest, old["application"].content_digest)
        reads = [preparation for request, preparation, _ in self.health.signed_reads
            if type(request.operation) is ObserveNodeHealth and request.operation.node_id == "api"]
        self.assertEqual(reads[0].request.target, reads[-1].request.target)
        self.assertEqual(self.history(), self.origin_history)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.workspaces.get("workspace-a").current_graph_id,
                self.before_update.current_graph_id)
            active = uow.stores.registered_products.list_active("workspace-a")
            self.assertTrue({old_product.reference, new_product.reference} <= {value.reference for value in active})
        plan = self.update_plan
        advanced = await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
            payload={"plan_id": self.record.plan_id,
                "expected_current_graph_id": self.before_update.current_graph_id,
                "expected_current_realized_projection_id": self.before_update.current_realized_projection_id,
                "desired_graph_id": plan["desired_graph_id"],
                "desired_realized_projection_id": plan["desired_realized_projection_id"],
                "expected_desired_graph_revision": plan["desired_graph_revision"],
                "claim_generation": self.update_generation, "idempotency_key": "advance-image"})
        self.assertEqual(advanced["to_graph_id"], plan["desired_graph_id"])
