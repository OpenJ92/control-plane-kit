"""Shared own-health configuration through real first-start/reload owners."""
from dataclasses import replace
import unittest

from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.products import ProductDescriptorCodec, ProductIdentity, ProductReference
from control_plane_kit_core.wrapper_configuration import (
    WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT as ENVIRONMENT,
    WorkloadNodeControlConfigurationCodec, select_workload_node_control_configuration_artifact as select_artifact,
)
from control_plane_kit_operations import health_receiver_trust as trust
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartDenied
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.health_signing_authority import HealthSigningAuthorityReloadService, ReloadHealthSigningAuthority
from tests.execution_lease_recovery_fixture import Sequence
from tests.health_effect_start_fixture import trusted_health_context
from tests.health_receiver_trust_fixture import bindings, ByteDecoder, context, register_products
from tests.postgres_health_effect_start_fixture import PostgresHealthEffectStartFixture


class PostgresSharedHealthReceiverTests(PostgresHealthEffectStartFixture, unittest.TestCase):
    def setUp(self):
        self.transform = None
        self.changes = None
        super().setUp()
        # Real graph, registration and accepted Core bytes precede source red.
        selected = self.receiver_artifacts["workload"]
        configured = WorkloadNodeControlConfigurationCodec().decode_bytes(selected.content.encode())
        self.assertEqual(configured.target.node_id.value, "api")
        self.assertNotEqual(selected.content_digest,
            self.receiver_documents["workload"].product.runtime_contract.configuration_artifacts[0].content_digest)

    def health_context(self, **options):
        world, documents, selected = context(self, shared=True, transform=self.transform,
            changes=self.changes, **options)
        self.world = world
        self.receiver_documents, self.receiver_artifacts = documents, selected
        self.receiver_products = register_products(self, documents)
        return world

    def registry(self):
        # A downstream workload never causes a new transit registration entry.
        return trust.HealthReceiverDecoders(bindings(trust,
            {"transit": self.receiver_documents["transit"]}, ByteDecoder(trust)))

    def start(self, ids=None):
        ids = Sequence("shared-original", "shared-request", "shared-transit", "shared-workload") if ids is None else ids
        return EffectAttemptStartService(self.unit_of_work, id_factory=ids,
            health_receiver_decoders=self.registry()).execute_health(self.start_health_command())

    def refused(self):
        before, ids = self.health_snapshot(), Sequence("must-not-allocate")
        with self.observed_time("2030-01-01T00:00:00Z"):
            with self.assertRaises(EffectAttemptStartDenied) as caught:
                self.start(ids)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.health_snapshot(), before)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def publish(self, nodes, documents, selected, *, name, defaults, actual, declared_path, selected_path):
        document = documents["workload"]
        contract = replace(document.product.runtime_contract, configuration_artifacts=defaults,
            public_environment=(PublicStaticEnvironmentBinding(ENVIRONMENT, declared_path),))
        document = ProductDescriptorCodec().encode_document(replace(document.product,
            identity=ProductIdentity("independent", name, 1), runtime_contract=contract))
        reference = ProductReference.from_document(document)
        node = nodes["api"]
        nodes["api"] = replace(node, configuration_artifacts=actual,
            public_environment=(PublicStaticEnvironmentBinding(ENVIRONMENT, selected_path),), metadata={
                **node.metadata, "product_identity": reference.identity.key,
                "product_descriptor_digest": reference.descriptor_sha256.value})
        documents["workload"] = document
        return nodes, documents, selected

    def test_independently_named_registered_products_and_paths_start_and_reload_without_workload_decoder(self):
        for name, path in (("new-application", "/opt/new/receiver.json"),
                           ("another-application", "/var/run/another.json")):
            with self.subTest(name=name):
                def renamed(nodes, documents, selected):
                    default = documents["workload"].product.runtime_contract.configuration_artifacts[0]
                    default = replace(default, artifact_id=name, target_path=path)
                    chosen = replace(selected["workload"], artifact_id=name, target_path=path)
                    selected["workload"] = chosen
                    return self.publish(nodes, documents, selected, name=name, defaults=(default,),
                        actual=(chosen,), declared_path=path, selected_path=path)
                self.transform = renamed
                self.reset_health()
                self.assertEqual(len(self.registry().bindings), 1)
                with self.observed_time("2030-01-01T00:00:00Z"):
                    result = self.start()
                    before = self.health_snapshot()
                    command = ReloadHealthSigningAuthority(request_id="request-a", identity=result.preparation.identity,
                        context=trusted_health_context(), authority=self.start_value.authority, fence=self.start_value.fence)
                    pair = HealthSigningAuthorityReloadService(self.unit_of_work,
                        health_receiver_decoders=self.registry()).execute(command)
                self.assertEqual(pair.preparation, result.preparation)
                self.assertEqual(result.preparation.workload_key_registration_id, self.keys["workload"].registration_id)
                self.assertEqual(self.health_snapshot(), before)
                self.assertEqual(self.health_counts(), (1, 1, 2, 1))

    def test_redirect_from_declared_a_to_separately_valid_selected_b_refuses(self):
        def redirect(nodes, documents, selected):
            default = documents["workload"].product.runtime_contract.configuration_artifacts[0]
            chosen = selected["workload"]
            alternate = replace(chosen, artifact_id="alternate", target_path="/opt/alternate.json")
            other_default = replace(default, artifact_id="alternate", target_path=alternate.target_path)
            return self.publish(nodes, documents, selected, name="redirect-witness",
                defaults=(default, other_default), actual=(chosen, alternate),
                declared_path=default.target_path, selected_path=alternate.target_path)
        self.transform = redirect
        self.reset_health()
        contract = self.receiver_documents["workload"].product.runtime_contract
        node = self.world[3].graph.node("api")
        declared = select_artifact(artifacts=contract.configuration_artifacts,
            environment=contract.public_environment, control_surfaces=contract.control_surfaces)
        actual = select_artifact(artifacts=node.configuration_artifacts,
            environment=node.public_environment + node.socket_environment, control_surfaces=node.block_spec.control_surfaces)
        self.assertNotEqual((declared.artifact_id, declared.target_path), (actual.artifact_id, actual.target_path))
        self.refused()

    def test_missing_binding_and_unsupported_shared_profile_refuse_without_history(self):
        self.changes = {"workload": {"profile": "workload-node-control-configuration.v99"}}
        self.reset_health()
        self.refused()
        self.changes = None
        def missing(nodes, documents, selected):
            nodes["api"] = replace(nodes["api"], public_environment=())
            return nodes, documents, selected
        self.transform = missing
        self.reset_health()
        self.refused()

    def test_transit_decoder_port_cannot_reintroduce_workload_product_bindings(self):
        with self.assertRaises(trust.HealthReceiverTrustError) as caught:
            bindings(trust, {"workload": self.receiver_documents["workload"]}, ByteDecoder(trust))
        self.assertEqual(str(caught.exception), "health receiver trust is unavailable")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
