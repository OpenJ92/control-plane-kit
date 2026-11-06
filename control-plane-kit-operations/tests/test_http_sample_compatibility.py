"""Read-only registered compatibility, never execution or installed-state proof."""
from dataclasses import replace
import importlib
import importlib.util
import json
import unittest

import control_plane_kit_core as core
from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationArtifactRequirement, ConfigurationFileMode, ConfigurationMediaType
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.http_verification_sample_installation import (
    HTTP_VERIFICATION_SAMPLES_CONFIGURATION_ENVIRONMENT, HttpVerificationSampleCheckBinding,
    HttpVerificationSampleReceiverConfiguration, HttpVerificationSampleReceiverConfigurationCodec,
    HttpVerificationSampleVerifierConfiguration,
)
from control_plane_kit_core.http_verification_samples import http_verification_check_digest
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec, select_receiver_node_control_configuration_artifact
from control_plane_kit_core.receiver_management_routes import ReceiverManagementRouteBinding, ReceiverManagementRoutes, ReceiverManagementRoutesCodec
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_core.topology.graph import Endpoint, LiteralAddress
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.verification import HttpCheck, VerificationContract, VerificationPolicy
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile
from control_plane_kit_operations.products import InlineDescriptorSource, RegisteredProduct
from control_plane_kit_operations.records import ActivityPlanRecord, ActivityPlanStatus, GraphVersionRecord, RealizedGraphProjectionRecord
from control_plane_kit_operations.runtime_management_admission import runtime_management_execution_is_unsupported
from tests.health_receiver_trust_fixture import context, public_key

MODULE = "control_plane_kit_operations.http_sample_compatibility"
ROUTE_SLOT = ConfigurationArtifactRequirement("gateway-health-targets", "/etc/cpk/gateway/health-targets.json",
    ConfigurationMediaType.JSON, ConfigurationFileMode.READ_ONLY)


def case(test, *, socket="http-api", receiver_port=8080, check_socket=None, check_port=None,
         interval=2, route_port=None, selected=("live", "ready"), sample_purpose=None,
         route_changes=None, issuer="sample-controller", key_id=None, route_alias="api"):
    """Immutable owner records and real Core values; no database or execution fixture."""
    check_socket = socket if check_socket is None else check_socket
    check_port = receiver_port if check_port is None else check_port
    route_port = receiver_port if route_port is None else route_port
    receiver_codec = ReceiverNodeControlConfigurationCodec()
    saved = {}

    def transform(nodes, documents, chosen):
        node = nodes["api"]
        receiver = receiver_codec.decode_bytes(chosen["workload"].content.encode())
        reference = core.NodeControlGraphReference(core.NodeControlGraphReferenceRole.PROVIDER_SOCKET, socket)
        surface = replace(receiver.declaration.surface, provider_socket_name=reference)
        receiver = replace(receiver, target=replace(receiver.target, provider_socket_name=reference),
            declaration=replace(receiver.declaration, surface=surface))
        chosen["workload"] = replace(chosen["workload"], content=receiver_codec.encode_bytes(receiver).decode())
        checks = tuple(HttpCheck(check_id=name, provider_socket=check_socket, path="/health/" + name,
            policy=VerificationPolicy(timeout_seconds=5, interval_seconds=interval, maximum_attempts=10)) for name in ("live", "ready"))
        samples = HttpVerificationSampleReceiverConfiguration(receiver.target, receiver.declaration.identity().value,
            HttpVerificationSampleVerifierConfiguration(core.DelegationKeyPurpose.WORKLOAD_HTTP_VERIFICATION_SAMPLE,
                "sample-controller", (public_key("workload"),)),
            tuple(HttpVerificationSampleCheckBinding(check.check_id, http_verification_check_digest(check))
                for check in checks if check.check_id in selected))
        sample = ConfigurationArtifact("sample-checks", "/etc/test/samples.json", ConfigurationMediaType.JSON,
            HttpVerificationSampleReceiverConfigurationCodec().encode_canonical_bytes(samples).decode())
        names = tuple(dict.fromkeys((socket, check_socket)))
        sockets = BlockSockets(providers=tuple(ProviderSocket(name, Protocol.HTTP) for name in names))
        ports = tuple(core.ProviderRuntimePort(name, receiver_port if name == socket else check_port) for name in names)
        contract = documents["workload"].product.runtime_contract
        default = receiver_codec.decode_bytes(contract.configuration_artifacts[0].content.encode())
        default = replace(default, target=receiver.target, declaration=receiver.declaration)
        environment = node.public_environment + (PublicStaticEnvironmentBinding(
            HTTP_VERIFICATION_SAMPLES_CONFIGURATION_ENVIRONMENT, sample.target_path),)
        contract = replace(contract, sockets=sockets, provider_ports=ports, verification=VerificationContract(checks),
            control_surfaces=(surface,), public_environment=environment,
            configuration_artifacts=(replace(contract.configuration_artifacts[0], content=receiver_codec.encode_bytes(default).decode()), sample))
        nodes["api"] = replace(node, sockets=sockets, public_environment=environment,
            endpoints={name: Endpoint(LiteralAddress(f"http://api:{receiver_port if name == socket else check_port}"), Protocol.HTTP) for name in names},
            block_spec=replace(node.block_spec, verification=contract.verification, control_surfaces=(surface,)),
            configuration_artifacts=(chosen["workload"], sample))
        documents["workload"] = core.ProductDescriptorCodec().encode_document(replace(documents["workload"].product, runtime_contract=contract))
        gateway = nodes["gateway"]
        artifact = select_receiver_node_control_configuration_artifact(artifacts=gateway.configuration_artifacts,
            environment=gateway.public_environment, control_surfaces=gateway.block_spec.control_surfaces)
        own = receiver_codec.decode_bytes(artifact.content.encode())
        purpose = core.DelegationKeyPurpose.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT if sample_purpose is None else sample_purpose
        trust = ConfigurationArtifact("sample-transit", "/etc/test/sample-transit.json", ConfigurationMediaType.JSON,
            json.dumps(dict(target=own.target.descriptor(), purpose=purpose.value, issuer=issuer,
                keys=[dict(key_id=public_key("transit").key_id if key_id is None else key_id,
                    pem=public_key("transit").public_key_pem)])))
        routes = ConfigurationArtifact("gateway-health-targets", "/etc/cpk/gateway/health-targets.json", ConfigurationMediaType.JSON,
            ReceiverManagementRoutesCodec().encode_bytes(ReceiverManagementRoutes(own.target, (
                ReceiverManagementRouteBinding(route_alias, receiver.target, receiver.declaration, f"http://api:{route_port}"),
                ReceiverManagementRouteBinding("gateway", own.target, own.declaration, "http://gateway:8000")))).decode())
        routes = replace(routes, **(route_changes or {}))
        extras = (trust, routes)
        contract = documents["transit"].product.runtime_contract
        documents["transit"] = core.ProductDescriptorCodec().encode_document(replace(documents["transit"].product,
            runtime_contract=replace(contract, configuration_artifacts=contract.configuration_artifacts + extras)))
        nodes["gateway"] = replace(gateway, configuration_artifacts=gateway.configuration_artifacts + extras)
        for family, node_id in (("workload", "api"), ("transit", "gateway")):
            ref = core.ProductReference.from_document(documents[family])
            nodes[node_id] = replace(nodes[node_id], metadata={**nodes[node_id].metadata,
                "product_identity": ref.identity.key, "product_descriptor_digest": ref.descriptor_sha256.value})
        saved["slot"] = ConfigurationArtifactRequirement(trust.artifact_id, trust.target_path, trust.media_type, trust.file_mode)
        return nodes, documents, chosen

    world, documents, _ = context(test, receiver=True, transform=transform)
    current, desired = world[2], world[3]
    plan = core.compile_http_sample_bootstrap_activity_plan(current, desired)
    products = tuple(RegisteredProduct.from_document(workspace_id="workspace-a", descriptor_document=document,
        source=InlineDescriptorSource(), imported_by="operator-a", imported_at="2026-11-06T12:00:00Z") for document in documents.values())
    projections = tuple(RealizedGraphProjectionRecord.identity_for_authored(authored_record=GraphVersionRecord.from_graph(
        graph_id=name, workspace_id="workspace-a", version=index, graph=graph.graph,
        created_by="operator-a", created_at="2026-11-06T12:00:00Z")) for index, (name, graph) in enumerate((
            ("base", current), ("desired", desired)), 1))
    record = ActivityPlanRecord("plan", "session", "base", "desired", ActivityPlanStatus.PLANNED,
        "2026-11-06T12:01:00Z", plan, projections[0].projection_id, projections[1].projection_id, 1,
        derivation_profile=PlanDerivationProfile.MANAGED_HTTP_SAMPLE_BOOTSTRAP_V1)
    return dict(workspace_id="workspace-a", plan=record, current=projections[0], desired=projections[1],
        products=products, node_id="api"), core.ProductReference.from_document(documents["transit"]), saved["slot"]


class HttpSampleCompatibilityTests(unittest.TestCase):
    def test_http_sample_compatibility_requires_independent_route_slot_after_repins(self):
        api = importlib.import_module(MODULE)
        for changes in (dict(target_path="/etc/test/wrong-routes.json"), dict(artifact_id="other-routes"),
                        dict(media_type=ConfigurationMediaType.TEXT), dict(file_mode=ConfigurationFileMode.OWNER_READ_ONLY)):
            with self.subTest(changes=changes):
                values, reference, slot = case(self, route_changes=changes)
                self.assertTrue(values["plan"].plan.ready_for_execution)
                # Both descriptor and graph agree and are re-pinned. The qualified
                # product-owned expected slot remains independent of those bytes.
                with self.assertRaises(api.HttpSampleCompatibilityUnavailable):
                    self.bind(values, reference, slot)

    def test_http_sample_compatibility_uses_sample_wire_issuer_keys_and_alias_unchanged(self):
        from control_plane_kit_core._node_control_public_wire import reference_violation
        api = importlib.import_module(MODULE)
        for options in (dict(issuer="localhost"), dict(key_id="localhost"), dict(route_alias="localhost")):
            with self.subTest(options=options):
                values, reference, slot = case(self, **options)
                with self.assertRaises(api.HttpSampleCompatibilityUnavailable):
                    self.bind(values, reference, slot)
        issuer = "issuer-" + "a" * 220
        self.assertIsNone(reference_violation(issuer))
        values, reference, slot = case(self, issuer=issuer, route_alias="child-route")
        try:
            result = self.bind(values, reference, slot)
        except api.HttpSampleCompatibilityUnavailable:
            self.fail("Core-valid issuer and unchanged route alias must remain supported")
        self.assertEqual(result.gateway_trust.issuer, issuer)
        self.assertEqual(result.route.target_id, "child-route")

    def bind(self, values, reference, slot):
        self.assertIsNotNone(importlib.util.find_spec(MODULE), "read-only sample compatibility is missing")
        api = importlib.import_module(MODULE)
        class Decoder:
            def decode(self, artifact):
                value = json.loads(artifact.content)
                return api.GatewayHttpSampleTrust(core.NodeControlReceiverTargetCodec().decode(value["target"]),
                    core.DelegationKeyPurpose(value["purpose"]), value["issuer"], tuple(
                        core.DelegationPublicKey(key["key_id"], core.DelegationKeyAlgorithm.ED25519, key["pem"]) for key in value["keys"]))
        return api.bind_http_sample_compatibility(**values, gateway_binding=api.GatewayHttpSampleBinding(reference, slot, Decoder()))

    def test_http_sample_compatibility_preserves_actual_pairs_and_distinct_destinations(self):
        for options in ({}, dict(socket="control", receiver_port=8081, interval=1),
                        dict(socket="control", receiver_port=8000, check_socket="application", check_port=8080)):
            with self.subTest(options=options):
                values, reference, slot = case(self, **options)
                self.assertTrue(values["plan"].plan.ready_for_execution)
                before = tuple(dict(value.graph_descriptor) for value in (values["current"], values["desired"]))
                result = self.bind(values, reference, slot)
                self.assertEqual(result.projection_id, values["desired"].projection_id)
                self.assertEqual(result.route.origin, f"http://api:{options.get('receiver_port', 8080)}")
                self.assertEqual(tuple(entry.container_port for entry in result.receiver.checks),
                    (options.get("check_port", options.get("receiver_port", 8080)),) * 2)
                self.assertEqual(tuple(entry.check.check_id for entry in result.receiver.checks), ("live", "ready"))
                self.assertEqual(tuple(dict(value.graph_descriptor) for value in (values["current"], values["desired"])), before)
                self.assertTrue(runtime_management_execution_is_unsupported(
                    DEFAULT_GRAPH_CODEC.decode(values["current"].graph_descriptor),
                    DEFAULT_GRAPH_CODEC.decode(values["desired"].graph_descriptor), values["plan"].plan,
                    registered_products=values["products"], derivation_profile=values["plan"].derivation_profile))

    def test_http_sample_compatibility_refuses_misroute_partial_and_wrong_trust(self):
        for options in (dict(socket="control", receiver_port=8000, check_socket="application", check_port=8080, route_port=8080),
                        dict(selected=("ready",)), dict(sample_purpose=core.DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT)):
            with self.subTest(options=options):
                values, reference, slot = case(self, **options)
                self.assertIsNotNone(importlib.util.find_spec(MODULE), "read-only sample compatibility is missing")
                api = importlib.import_module(MODULE)
                with self.assertRaises(api.HttpSampleCompatibilityUnavailable) as caught:
                    self.bind(values, reference, slot)
                self.assertEqual(str(caught.exception), "HTTP sample compatibility is unavailable")
                self.assertIsNone(caught.exception.__context__)

    def test_http_sample_compatibility_pins_workspace_product_projection_and_declared_slot(self):
        values, reference, slot = case(self)
        self.assertIsNotNone(importlib.util.find_spec(MODULE), "read-only sample compatibility is missing")
        api = importlib.import_module(MODULE)
        wrong, _, _ = case(self, socket="control", receiver_port=8081, interval=1)
        for changes, selected_reference, selected_slot in ((dict(workspace_id="foreign"), reference, slot),
                (dict(desired=wrong["desired"]), reference, slot), (dict(products=()), reference, slot),
                ({}, replace(reference, descriptor_sha256=core.ProductDescriptorDigest("e" * 64)), slot),
                ({}, reference, replace(slot, target_path="/etc/foreign.json"))):
            with self.subTest(changes=tuple(changes), slot=selected_slot.target_path), self.assertRaises(api.HttpSampleCompatibilityUnavailable):
                self.bind(values | changes, selected_reference, selected_slot)
