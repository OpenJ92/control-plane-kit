"""Pure installed sample trust and allowlist; signatures and I/O belong outside Core."""
from dataclasses import replace
import importlib
import importlib.util
import unittest

import rfc8785

import control_plane_kit_core as core
from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.configuration import (
    ConfigurationArtifact, ConfigurationArtifactRequirement, ConfigurationFileMode, ConfigurationMediaType,
)
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding, SocketDerivedEnvironmentBinding
from control_plane_kit_core.http_verification_samples import (
    HttpVerificationSampleError, HttpVerificationSampleRequest, http_verification_check_digest,
)
from control_plane_kit_core.products import ProductRuntimeContract, ProviderRuntimePort
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.verification import HttpCheck, VerificationContract, VerificationPolicy
from tests import test_receiver_configuration as receiver_fixtures


def fixture():
    old = receiver_fixtures.ReceiverConfigurationTests()
    old.setUp()
    document = old.document()
    document["target"]["provider_socket_name"] = "http-api"
    declaration = replace(old.declaration, surface=replace(old.declaration.surface,
        provider_socket_name=core.NodeControlGraphReference(core.NodeControlGraphReferenceRole.PROVIDER_SOCKET, "http-api")))
    document["declaration"] = declaration.descriptor()
    receiver = old.codec.decode(document)
    checks = tuple(HttpCheck(check_id=name, provider_socket="http-api", path="/health/" + name,
        policy=VerificationPolicy(interval_seconds=2, maximum_attempts=10)) for name in ("live", "ready"))
    sockets = (ProviderSocket("http-api", Protocol.HTTP), ProviderSocket("mcp", Protocol.MCP_STREAMABLE_HTTP))
    ports = (ProviderRuntimePort("http-api", 8080), ProviderRuntimePort("mcp", 8080))
    contract = ProductRuntimeContract(sockets=BlockSockets(providers=sockets),
        provider_ports=ports, verification=VerificationContract(checks))
    return receiver, old.key, contract


class HttpSampleInstallationPredecessorTests(unittest.TestCase):
    def test_existing_v2_and_shared_port_fixture_are_valid(self):
        receiver, _, contract = fixture()
        codec = ReceiverNodeControlConfigurationCodec()
        self.assertEqual(codec.decode_bytes(codec.encode_bytes(receiver)), receiver)
        self.assertEqual(contract.sockets.provider("http-api").protocol, Protocol.HTTP)
        self.assertEqual(contract.sockets.provider("mcp").protocol, Protocol.MCP_STREAMABLE_HTTP)
        self.assertEqual(tuple(port.container_port for port in contract.provider_ports), (8080, 8080))


class HttpSampleInstallationTests(unittest.TestCase):
    def setUp(self):
        # Establish existing inputs before asserting the newly missing contract.
        self.receiver, self.key, self.product = fixture()
        name = "control_plane_kit_core.http_verification_sample_installation"
        self.assertIsNotNone(importlib.util.find_spec(name), "HTTP sample installation contract is missing")
        self.api = importlib.import_module(name)
        self.slot = ConfigurationArtifactRequirement("cpk-http-samples", "/etc/cpk/cpk-server/http-samples.json",
            ConfigurationMediaType.JSON, ConfigurationFileMode.READ_ONLY)

    def configuration(self, checks=None, **changes):
        selected = (self.product.verification.checks[1],) if checks is None else checks
        return self.api.HttpVerificationSampleReceiverConfiguration(**dict(
            target=self.receiver.target, declaration_identity=self.receiver.declaration.identity().value,
            verifier=self.api.HttpVerificationSampleVerifierConfiguration(
                core.DelegationKeyPurpose.WORKLOAD_HTTP_VERIFICATION_SAMPLE, "sample-controller", (self.key,)),
            checks=tuple(self.api.HttpVerificationSampleCheckBinding(check.check_id,
                http_verification_check_digest(check)) for check in selected), **changes))

    def bind(self, configuration=None, **changes):
        values = dict(configuration=self.configuration() if configuration is None else configuration,
            receiver_configuration=self.receiver, verification_contract=self.product.verification,
            provider_sockets=self.product.sockets.providers, provider_ports=self.product.provider_ports)
        return self.api.HttpVerificationSampleReceiverBinding(**(values | changes))

    def request(self, check):
        return HttpVerificationSampleRequest(target=self.receiver.target,
            authority_context=core.NodeControlAuthorityContext("graph-a", "projection-a"),
            declaration_identity=self.receiver.declaration.identity().value,
            gateway_target=replace(self.receiver.target,
                node_id=replace(self.receiver.target.node_id, value="gateway"), receiver_id="b" * 32),
            route_id="child", run_id="run-a", activity_id="activity-a", outer_attempt=1,
            effect_id="event-start", request_fingerprint="c" * 64, check_id=check.check_id,
            check_digest=http_verification_check_digest(check), sample_ordinal=1, request_id="sample-a")

    def refusal(self, action):
        with self.assertRaises(HttpVerificationSampleError) as caught:
            action()
        self.assertEqual(str(caught.exception), "HTTP verification sample is invalid")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_companion_roundtrip_is_additive_to_unchanged_v2(self):
        codec = self.api.HttpVerificationSampleReceiverConfigurationCodec()
        value = self.configuration()
        document = codec.encode(value)
        self.assertEqual(document["profile"], "workload-http-verification-sample-configuration.v1")
        self.assertEqual(codec.decode(document), value)
        self.assertEqual(codec.encode_canonical_bytes(value), rfc8785.dumps(document))
        self.assertEqual(codec.decode_canonical_bytes(rfc8785.dumps(document)), value)
        self.assertNotIn(self.key.public_key_pem, repr(value))
        original = ReceiverNodeControlConfigurationCodec().encode_bytes(self.receiver)
        self.bind(value)
        self.assertEqual(ReceiverNodeControlConfigurationCodec().encode_bytes(self.receiver), original)
        for edit in ({"url": "http://private.invalid"}, {"port": 8081}, {"policy": {}}, {"checks": []}):
            self.refusal(lambda: codec.decode(document | edit))
        self.refusal(lambda: codec.decode_canonical_bytes(b" " + rfc8785.dumps(document)))
        self.refusal(lambda: codec.decode_canonical_bytes(b"x" * 65537))

    def test_ready_only_subset_controls_request_lookup_and_key_family(self):
        bound = self.bind()
        self.assertEqual(tuple(entry.check.check_id for entry in bound.checks), ("ready",))
        self.assertEqual(bound.audience, core.receiver_node_control_audience(self.receiver.target))
        self.assertEqual(bound.key(self.key.key_id), self.key)
        self.refusal(lambda: bound.key("not-installed"))
        ready = self.product.verification.checks[1]
        entry = bound.check_for_request(self.request(ready))
        self.assertEqual((entry.check, entry.container_port), (ready, 8080))
        # A scope-congruent request for a real product check is still not allowed.
        self.refusal(lambda: bound.check_for_request(self.request(self.product.verification.checks[0])))
        self.refusal(lambda: bound.check_for_request(replace(self.request(ready), sample_ordinal=11)))
        self.refusal(lambda: bound.check_for_request(replace(self.request(ready),
            target=replace(self.receiver.target, receiver_id="f" * 32))))
        self.refusal(lambda: replace(self.configuration().verifier,
            purpose=core.DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ))

    def test_socket_protocol_is_joined_by_name_not_shared_port_number(self):
        self.bind()  # HTTP and MCP sharing 8080 is legitimate.
        check = replace(self.product.verification.checks[1], provider_socket="mcp")
        self.refusal(lambda: self.bind(self.configuration((check,)),
            verification_contract=VerificationContract((check,))))
        self.refusal(lambda: self.bind(provider_ports=(ProviderRuntimePort("mcp", 8080),)))
        self.refusal(lambda: self.bind(provider_ports=self.product.provider_ports + (ProviderRuntimePort("http-api", 8081),)))

    def test_full_configuration_is_validated_before_any_entry_escapes(self):
        value = self.configuration()
        wrong_digest = replace(value.checks[0], check_digest="f" * 64)
        for changed in (replace(value, target=replace(value.target, receiver_id="f" * 32)),
                        replace(value, declaration_identity="f" * 64),
                        replace(value, checks=(wrong_digest,))):
            self.refusal(lambda: self.bind(changed))
        foreign = self.api.HttpVerificationSampleCheckBinding("foreign", "f" * 64)
        self.refusal(lambda: self.bind(replace(value, checks=(*value.checks, foreign))))
        self.refusal(lambda: replace(value, checks=(*value.checks, value.checks[0])))
        self.refusal(lambda: replace(value.verifier, public_keys=(self.key, self.key)))

    def test_first_installed_profile_refuses_hash_and_redirect_checks(self):
        original = self.product.verification.checks[1]
        for check in (replace(original, expected_body_sha256="d" * 64),
                      replace(original, expected_statuses=(302,)),
                      replace(original, expected_statuses=(200, 302))):
            self.refusal(lambda: self.bind(self.configuration((check,)),
                verification_contract=VerificationContract((check,))))
            self.assertEqual(http_verification_check_digest(check),
                             self.configuration((check,)).checks[0].check_digest)

    def test_product_owned_slot_and_effective_binding_are_both_required(self):
        select = self.api.select_http_verification_sample_configuration_slot
        name = self.api.HTTP_VERIFICATION_SAMPLES_CONFIGURATION_ENVIRONMENT
        empty = dict(expected_slot=self.slot, artifacts=(), requirements=(), environment=())
        self.assertIsNone(select(**empty))
        binding = PublicStaticEnvironmentBinding(name, self.slot.target_path)
        self.assertEqual(select(**(empty | dict(requirements=(self.slot,), environment=(binding,)))), self.slot)
        artifact = ConfigurationArtifact(self.slot.artifact_id, self.slot.target_path,
            self.slot.media_type, self.api.HttpVerificationSampleReceiverConfigurationCodec().encode_canonical_bytes(
                self.configuration()).decode(), self.slot.file_mode)
        self.assertEqual(select(**(empty | dict(artifacts=(artifact,), environment=(binding,)))), artifact)
        for arguments in (dict(environment=(binding,)), dict(requirements=(self.slot,)),
            dict(requirements=(self.slot,), environment=(binding, binding)),
            dict(requirements=(replace(self.slot, artifact_id="other"),), environment=(binding,)),
            dict(requirements=(self.slot,), environment=(SocketDerivedEnvironmentBinding(name, self.slot.target_path, "edge"),)),
            dict(requirements=(replace(self.slot, file_mode=ConfigurationFileMode.OWNER_READ_ONLY),), environment=(binding,)),
            dict(requirements=(self.slot,), artifacts=(artifact,), environment=(binding,))):
            self.refusal(lambda: select(**(empty | arguments)))

    def test_fixed_route_is_explicit_and_old_health_route_is_unchanged(self):
        from control_plane_kit_core.control_routes import HTTP_VERIFICATION_SAMPLE_ROUTES, NODE_HEALTH_ROUTES
        route, = HTTP_VERIFICATION_SAMPLE_ROUTES.routes
        self.assertEqual((route.method.value, route.path, route.scope.value),
            ("GET", "/__control/verification/http", "http-verification-sample:read"))
        old, = NODE_HEALTH_ROUTES.routes
        self.assertEqual((old.method.value, old.path, old.scope.value),
            ("GET", "/__control/health/{health_kind}", "node-health:read"))
