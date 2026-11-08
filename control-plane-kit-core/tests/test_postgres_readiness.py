"""Supplied-fact SQL congruence laws; fixtures are not committed provider proof."""
from dataclasses import replace
import unittest

from control_plane_kit_core.configuration_installation import ConfigurationInstallationDisposition
from control_plane_kit_core.configuration_instances import ConfigurationInstanceSelection
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding, SocketDerivedEnvironmentBinding
from control_plane_kit_core.node_health_read_results import NodeHealthReadOutcome
from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_core.planning import ActivityId, NodeTarget, WaitForHealthy
from control_plane_kit_core.postgres_readiness import (
    PostgresReadinessBinding, PostgresReadinessObserver, PostgresReadinessUnavailable,
    postgres_readiness_password_supported, postgres_readiness_result_matches,
)
from control_plane_kit_core.products import ProductDescriptorCodec, ProductReference
from control_plane_kit_core.receiver_health_read_results import ReceiverHealthReadResult
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_core.secret_installation import (
    SecretInstallationReceipt, secret_installation_correlation_for_request, secret_material_selection_fingerprint,
)
from control_plane_kit_core.secret_selections import SecretVersionSelection, UnselectedSecretUse
from control_plane_kit_core.secrets import SecretFileDelivery, SecretFilePathBinding, SecretReference, SecretUseIntent, SecretValue
from control_plane_kit_core.verification import PostgresPasswordAuthentication, PostgresQueryCheck, VerificationContract
from control_plane_kit_core.wrapper_configuration import WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT
from control_plane_kit_core.types import Protocol
from tests.test_receiver_configuration import ReceiverConfigurationTests
from tests.test_runtime_effect_intent import _request
from tests.test_configuration_instances import instance


def supplied_binding(**changes):
    fixture = ReceiverConfigurationTests()
    fixture.setUp()
    document = fixture.document()
    document["target"].update(runtime_id="docker", node_id="api")
    artifact = fixture.artifact(document=document)
    receiver = fixture.codec.decode(document)
    reference = SecretReference("secret://local/workspace-a/postgres/password")
    delivery = SecretFileDelivery("/run/secrets/postgres/password", reference, SecretUseIntent.POSTGRES_PASSWORD,
        path_binding=SecretFilePathBinding("POSTGRES_PASSWORD_FILE"))
    check = PostgresQueryCheck(check_id="installation-postgres-ready", provider_socket="sql",
        authentication=PostgresPasswordAuthentication("cpk", "cpk", reference))
    product = replace(fixture.product, runtime_contract=replace(fixture.product.runtime_contract,
        configuration_artifacts=(artifact,),
        public_environment=(PublicStaticEnvironmentBinding(WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT,
            artifact.target_path),), secret_deliveries=(delivery,), verification=VerificationContract((check,))))
    expected = ProductReference.from_document(ProductDescriptorCodec().encode_document(product))
    install = _request(complete=False)
    material = replace(install.products[0], product=product, reference=expected,
        public_environment=product.runtime_contract.public_environment, socket_environment=(), pull_authority=None,
        secret_selections=(SecretVersionSelection(reference, SecretUseIntent.POSTGRES_PASSWORD, "version-1"),))
    allocation = instance(artifact_id=artifact.artifact_id, target_path=artifact.target_path,
        media_type=artifact.media_type, file_mode=artifact.file_mode, content_digest=artifact.content_digest)
    install = replace(install, products=(material,), kind=RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1,
        configuration_instances=ConfigurationInstanceSelection((allocation,)))
    wait = replace(install, effect_id="event-wait", source=replace(install.source, intent_event_id="event-wait"),
        activity_id=ActivityId("wait-a"), operation=WaitForHealthy(NodeTarget("api")),
        kind=RuntimeEffectKind.REALIZE_ACTIVITY, configuration_instances=None)
    context = secret_installation_correlation_for_request(install)
    receipt = SecretInstallationReceipt(context.request_fingerprint, secret_material_selection_fingerprint(install),
        ConfigurationInstallationDisposition.CREATED, None, "a" * 64)
    observer = PostgresReadinessObserver(expected, product.image, "sql", "cpk", "cpk", delivery.target_path, "POSTGRES_PASSWORD_FILE")
    values = dict(wait=wait, attempt=EffectAttemptIdentity(wait.source.run_id, wait.activity_id.value, 1),
        installation=install, receipt=receipt, observer=observer, receiver=receiver,
        base_projection_id="projection-base", desired_projection_id="projection-desired",
        check_id=check.check_id, started_at_epoch_ms=100_000)
    return PostgresReadinessBinding(**(values | changes))


class OriginalPostgresReadinessTests(unittest.TestCase):
    def test_request_binds_realization_whole_policy_and_original_effect(self):
        binding = supplied_binding()
        changed_installation = replace(binding, receipt=replace(binding.receipt, current_realization_fingerprint="b" * 64))
        self.assertNotEqual(binding.request(), changed_installation.request())
        self.assertEqual(binding.request(), supplied_binding().request())
        contract = binding.wait.products[0].product.runtime_contract
        original = contract.verification.checks[0]
        changed = replace(original, policy=replace(original.policy, interval_seconds=2))
        material = replace(binding.wait.products[0], product=replace(binding.wait.products[0].product,
            runtime_contract=replace(contract, verification=VerificationContract((changed,)))))
        install = replace(binding.installation, products=(material,))
        receipt = replace(binding.receipt,
            request_fingerprint=secret_installation_correlation_for_request(install).request_fingerprint,
            selection_fingerprint=secret_material_selection_fingerprint(install))
        other_policy = replace(binding, wait=replace(binding.wait, products=(material,)), installation=install, receipt=receipt)
        self.assertNotEqual(binding.request(), other_policy.request())
        other_wait = replace(binding.wait, effect_id="another-original-event",
            source=replace(binding.wait.source, intent_event_id="another-original-event"))
        self.assertNotEqual(binding.request(), replace(binding, wait=other_wait).request())

    def test_late_cross_wired_or_nonhealthy_is_never_a_positive(self):
        binding = supplied_binding()
        result = ReceiverHealthReadResult(binding.request(), binding.receiver.declaration, NodeHealthReadOutcome.HEALTHY)
        self.assertTrue(postgres_readiness_result_matches(binding, result, observed_at_epoch_ms=104_999))
        self.assertFalse(postgres_readiness_result_matches(binding, result, observed_at_epoch_ms=105_000))
        self.assertFalse(postgres_readiness_result_matches(binding, result, observed_at_epoch_ms=99_999))
        other = replace(binding, desired_projection_id="another-projection")
        self.assertFalse(postgres_readiness_result_matches(other, result, observed_at_epoch_ms=101_000))
        for outcome in (NodeHealthReadOutcome.UNKNOWN, NodeHealthReadOutcome.UNHEALTHY):
            self.assertFalse(postgres_readiness_result_matches(binding, replace(result, outcome=outcome),
                observed_at_epoch_ms=101_000))

    def test_unselected_wrong_target_product_or_receipt_refuses_without_secret_output(self):
        binding = supplied_binding()
        material = binding.wait.products[0]
        selection = material.secret_selections[0]
        candidates = (
            dict(observer=replace(binding.observer, username="other")),
            dict(observer=replace(binding.observer, image=replace(binding.observer.image, digest="sha256:" + "f" * 64))),
            dict(receipt=replace(binding.receipt, request_fingerprint="f" * 64)),
            dict(receiver=replace(binding.receiver, target=replace(binding.receiver.target, receiver_id="f" * 32))),
            dict(installation=replace(binding.installation, source=replace(binding.installation.source, plan_id="other"))),
        )
        for changes in candidates:
            with self.subTest(fields=tuple(changes)), self.assertRaises(PostgresReadinessUnavailable) as caught:
                replace(binding, **changes)
            self.assertEqual(str(caught.exception), "PostgreSQL readiness binding is unavailable")
            self.assertIsNone(caught.exception.__context__)

        # Existing request construction already refuses unresolved StartNode
        # material; the binding must not invent a selected version around it.
        unselected = replace(material, secret_selections=(UnselectedSecretUse(selection.reference, selection.intent),))
        with self.assertRaises(ValueError):
            replace(binding.installation, products=(unselected,))

    def test_actual_runtime_environment_cannot_redirect_config_or_password_delivery(self):
        binding = supplied_binding()
        material = binding.wait.products[0]
        for environment in ((), (PublicStaticEnvironmentBinding(WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT, "/etc/missing"),)):
            candidate = replace(material, public_environment=environment)
            install = replace(binding.installation, products=(candidate,))
            receipt = replace(binding.receipt,
                request_fingerprint=secret_installation_correlation_for_request(install).request_fingerprint,
                selection_fingerprint=secret_material_selection_fingerprint(install))
            with self.assertRaises(PostgresReadinessUnavailable):
                replace(binding, wait=replace(binding.wait, products=(candidate,)),
                    installation=install, receipt=receipt)
        # The existing public environment owner rejects even constructing this
        # secret-shaped override; no malformed env value is invented here.
        with self.assertRaises(ValueError):
            PublicStaticEnvironmentBinding("POSTGRES_PASSWORD_FILE", "/run/secrets/other")
        candidate = replace(material, socket_environment=(SocketDerivedEnvironmentBinding(
            "POSTGRES_PASSWORD_FILE", "/run/secrets/other", "api.control->api.other"),))
        install = replace(binding.installation, products=(candidate,))
        receipt = replace(binding.receipt,
            request_fingerprint=secret_installation_correlation_for_request(install).request_fingerprint,
            selection_fingerprint=secret_material_selection_fingerprint(install))
        with self.assertRaises(PostgresReadinessUnavailable):
            replace(binding, wait=replace(binding.wait, products=(candidate,)), installation=install, receipt=receipt)

    def test_authority_context_is_validated_before_a_binding_can_be_returned(self):
        binding = supplied_binding()
        for changes in (dict(desired_projection_id="projection:alternate"),
                dict(base_projection_id="projection:alternate")):
            with self.assertRaises(PostgresReadinessUnavailable) as caught:
                replace(binding, **changes)
            self.assertEqual(str(caught.exception), "PostgreSQL readiness binding is unavailable")

    def test_equal_protocol_values_do_not_depend_on_python_object_identity(self):
        binding = supplied_binding()
        material = binding.wait.products[0]
        contract = material.product.runtime_contract
        same_protocol = Protocol(Protocol.POSTGRES.transport, Protocol.POSTGRES.application)
        providers = tuple(replace(port, protocol=same_protocol) if port.name == "sql" else port
            for port in contract.sockets.providers)
        candidate = replace(material, product=replace(material.product,
            runtime_contract=replace(contract, sockets=replace(contract.sockets, providers=providers))))
        copied = replace(binding, wait=replace(binding.wait, products=(candidate,)),
            installation=replace(binding.installation, products=(candidate,)))
        self.assertEqual(copied.request(), binding.request())

    def test_password_domain_is_private_and_never_normalizes_original_bytes(self):
        self.assertTrue(postgres_readiness_password_supported(SecretValue("synthetic-exact-password")))
        for text in ("synthetic-exact-password\n", "embedded\nline", "carriage\rreturn", "nul\x00value", "é", "\ud800", "x" * 4097):
            self.assertFalse(postgres_readiness_password_supported(SecretValue(text)))
        self.assertNotIn("synthetic-exact-password", repr(supplied_binding()))


if __name__ == "__main__":
    unittest.main()
