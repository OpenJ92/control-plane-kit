"""Native observation material captured at the real Operations dispatch port."""

from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationMediaType
from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_core.planning import StartNode
from control_plane_kit_core.products import ProductReference
from control_plane_kit_core.secrets import SecretUseIntent
from tests import test_managed_application_chain as chain
from tests import test_managed_configuration_update_execution as content
from tests.test_managed_configuration_update_admission import APPLICATION


class ConnectorObservationMaterialTests(chain.ManagedApplicationFixture):
    application_artifacts = (APPLICATION,)
    connector_artifacts = (ConfigurationArtifact(
        "connector-settings", "/etc/connector/settings.json", ConfigurationMediaType.JSON,
        '{"mode":"managed"}',
    ),)
    complete_initial = chain.ManagedUpdateExecutionTests.complete_initial
    desired_update = content.ManagedConfigurationUpdateExecutionTests.desired_update
    prepare_content = content.ManagedConfigurationUpdateExecutionTests.prepare_content
    execute_content = content.ManagedConfigurationUpdateExecutionTests.execute_content

    def assert_connector_material(self, request, *, attempt=1):
        # Compare the actual observation with the existing owner's actual install
        # request, rather than synthesizing material for the observation port.
        self.assertEqual(len(request.products), 1, "native observation omitted connector material")
        material, = request.products
        installation, = (value for value in self.runtime.calls
            if type(value.operation) is StartNode and value.operation.target.node_id == "connector")
        self.assertEqual(request.products, installation.products)
        self.assertEqual(material.node_id, "connector")
        self.assertEqual(material.runtime_id, "docker")
        self.assertEqual(material.reference, ProductReference.from_document(self.documents["connector"]))
        self.assertEqual(material.product.runtime_contract.configuration_artifacts, self.connector_artifacts)
        self.assertEqual(material.public_environment, self.graph.node("connector").public_environment)
        delivery, = material.product.runtime_contract.secret_deliveries
        selection, = material.secret_selections
        self.assertEqual(delivery.intent, SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN)
        self.assertEqual((selection.reference, selection.intent), (delivery.reference, delivery.intent))
        self.assertTrue(selection.version_id)
        self.assertEqual(request.source.workspace_id, "workspace-a")
        self.assertEqual(request.authority_ref, self.graph.runtimes["docker"].authority_ref)
        self.assertEqual(request.authority_deliveries, ())
        self.assertEqual(request.secret_resolution_grants, ())
        # Verify durable correspondence after dispatch; existing attempt-start
        # laws separately establish that intent persistence precedes the port.
        identity = EffectAttemptIdentity(request.source.run_id, request.activity_id.value, attempt)
        with self.unit_of_work() as uow:
            retained = uow.stores.effect_attempt_intents.get(identity)
        self.assertEqual(retained.intent.products, request.products)

    async def test_fresh_native_request_carries_connector_material(self):
        await self.reach_native_wait()
        self.assert_connector_material(self.health.native_reads[0])
        self.assertTrue(self.health.signed_reads)
        self.assertTrue(all(request.products == () for request, _, _ in self.health.signed_reads))
        await self.reobserve(1, "material-successor")
        self.assert_connector_material(self.health.native_reads[1], attempt=2)

    async def test_retained_native_request_keeps_connector_material_and_original_history(self):
        await self.prepare_content()
        original_reads = tuple(self.health.native_reads)
        result = await self.execute_content()
        self.assertEqual((result["coordinator_status"], result["run_status"]), ("completed", "succeeded"))
        retained, = self.health.native_reads[len(original_reads):]
        self.assertNotEqual(retained.source.run_id, original_reads[0].source.run_id)
        self.assert_connector_material(retained)
        self.assertEqual(self.history(), self.origin_history)
