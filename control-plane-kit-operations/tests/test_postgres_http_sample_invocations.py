"""Original ordinary checks retain two registration-bound signing roles."""

from dataclasses import replace
import importlib
import importlib.util
import json
import unittest

import control_plane_kit_operations as operations
from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationArtifactRequirement, ConfigurationFileMode, ConfigurationMediaType
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.http_verification_sample_installation import (
    HTTP_VERIFICATION_SAMPLES_CONFIGURATION_ENVIRONMENT, HttpVerificationSampleCheckBinding,
    HttpVerificationSampleReceiverConfiguration, HttpVerificationSampleReceiverConfigurationCodec,
    HttpVerificationSampleVerifierConfiguration,
)
from control_plane_kit_core.http_verification_samples import http_verification_check_digest
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptTransition, EffectAttemptTransitionKind
from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.planning import WaitForHealthy, compile_http_sample_bootstrap_activity_plan
from control_plane_kit_core.products import ProductDescriptorCodec, ProductReference, ProductIdentity, ProductRuntimeContract
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
from control_plane_kit_core.receiver_management_routes import (
    ReceiverManagementRouteBinding, ReceiverManagementRoutes, ReceiverManagementRoutesCodec,
)
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.secrets import SecretUseIntent
from control_plane_kit_core.verification import HttpCheck, VerificationContract
from control_plane_kit_operations.execution_invocations import ExecutionInvocationKind
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile
from tests.health_receiver_trust_fixture import public_key
from tests.test_postgres_managed_effect_permissions import _ManagedPermissionStartFixture


MODULE = "control_plane_kit_operations.http_sample_compatibility"


class _SampleGatewayDecoder:
    """Interpret only supplied public fixture bytes, never expected identities."""

    def decode(self, artifact):
        from control_plane_kit_core.delegation_keys import DelegationKeyAlgorithm, DelegationPublicKey
        from control_plane_kit_core.receiver_identity import NodeControlReceiverTargetCodec
        value = json.loads(artifact.content)
        api = importlib.import_module(MODULE)
        return api.GatewayHttpSampleTrust(
            target=NodeControlReceiverTargetCodec().decode(value["target"]),
            purpose=DelegationKeyPurpose(value["purpose"]), issuer=value["issuer"],
            public_keys=tuple(DelegationPublicKey(item["key_id"], DelegationKeyAlgorithm.ED25519,
                                                item["pem"]) for item in value["keys"]))


class _HttpSampleFixture(_ManagedPermissionStartFixture):
    health_plan_compiler = staticmethod(compile_http_sample_bootstrap_activity_plan)
    health_plan_profile = PlanDerivationProfile.MANAGED_HTTP_SAMPLE_BOOTSTRAP_V1

    def seed_execution_request(self):
        from control_plane_kit_core.policies import PolicyScope
        from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
        from control_plane_kit_core.types import RuntimeKind
        from control_plane_kit_operations.runtime_authorities import (
            RegisterRuntimeAuthorityCommand, RuntimeAuthorityRegistrationService, LocalDockerSocketAuthority,
        )
        from control_plane_kit_operations.workflows import IdempotencyKey
        self._seed_original_health(**self._health_context_options)
        self.sample_runtime = RuntimeAuthorityRegistrationService(self.unit_of_work).register(RegisterRuntimeAuthorityCommand(
            "workspace-a", RuntimeAuthorityReference("managed-docker"), RuntimeKind.DOCKER, LocalDockerSocketAuthority(),
            "operator-a", self.clock(), (PolicyScope.RUNTIME_AUTHORITY_REGISTER,)))
        identities = iter(("request-a", "action-admit-fixture-request-a"))
        support, registration = self.sample_support()
        operations.ExecutionAdmissionCommandService(self.unit_of_work, clock=lambda: "2026-08-15T03:59:00Z",
            id_factory=lambda: next(identities), http_sample_support=support, http_sample_registration=registration).execute(
                operations.RequestPlanExecution(workspace_id="workspace-a", session_id="session-a", plan_id="plan-a",
                    approval_request_id="approval-request-a", actor_id="operator-a", actor_scopes=(
                        PolicyScope.PLAN_EXECUTE, PolicyScope.RUNTIME_AUTHORITY_USE, PolicyScope.INGRESS_AUTHORITY_USE),
                    idempotency_key=IdempotencyKey("execute-a")))

    def sample_support(self):
        if not hasattr(self, "_sample_support"):
            from control_plane_kit_operations.http_sample_compatibility import GatewayHttpSampleBinding
            from control_plane_kit_operations.http_sample_execution import HttpSampleExecutionSupport
            from control_plane_kit_operations.runtime_adapter_registration import RuntimeAdapterRegistration
            from tests.test_http_sample_compatibility import ROUTE_SLOT
            self._sample_support = HttpSampleExecutionSupport(GatewayHttpSampleBinding(
                ProductReference.from_document(self.receiver_documents["transit"]), self.transit_slot,
                _SampleGatewayDecoder(), route_slot=ROUTE_SLOT), "http")
            class DeclaredNative:
                http_sample_support = self._sample_support
                def execute_with_authority(self, *args, **kwargs):
                    raise AssertionError("metadata fixture must not execute a runtime")
            self._sample_registration = RuntimeAdapterRegistration(DeclaredNative(),
                invocation_profile="cpk.semantic-invocations.v1", ordinary_verification_invocations=True,
                http_sample_support=self._sample_support)
        return self._sample_support, self._sample_registration

    def health_context(self, **options):
        # Reuse the real introduction/plan/approval/runtime fixture. Only its
        # pure product values gain the sample opt-in before the owner compiles.
        return super().health_context(**(options | {"transform": self.sample_products}))

    def sample_products(self, nodes, documents, selected):
        codec = ReceiverNodeControlConfigurationCodec()
        workload = codec.decode_bytes(selected["workload"].content.encode())
        gateway = codec.decode_bytes(nodes["gateway"].configuration_artifacts[1].content.encode())
        surface = replace(workload.declaration.surface,
                          health_reads=(NodeHealthReadKind.LIVENESS, NodeHealthReadKind.READINESS))
        workload = replace(workload, declaration=replace(workload.declaration, surface=surface))
        selected["workload"] = replace(selected["workload"], content=codec.encode_bytes(workload).decode())
        check = HttpCheck(check_id="ready", provider_socket="http", path="/ready")
        sample = HttpVerificationSampleReceiverConfiguration(
            workload.target, workload.declaration.identity().value,
            HttpVerificationSampleVerifierConfiguration(DelegationKeyPurpose.WORKLOAD_HTTP_VERIFICATION_SAMPLE,
                "cpk-server", (public_key("workload", key_id="sample-workload"),)),
            (HttpVerificationSampleCheckBinding(check.check_id, http_verification_check_digest(check)),))
        artifact = ConfigurationArtifact("http-samples", "/etc/test/http-samples.json", ConfigurationMediaType.JSON,
            HttpVerificationSampleReceiverConfigurationCodec().encode_canonical_bytes(sample).decode(),
            ConfigurationFileMode.READ_ONLY)
        transit = ConfigurationArtifact("sample-transit", "/etc/test/sample-transit.json", ConfigurationMediaType.JSON,
            json.dumps(dict(target=gateway.target.descriptor(),
                purpose=DelegationKeyPurpose.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT.value,
                issuer="cpk-server", audience="gateway:workspace-a:gateway",
                keys=[dict(key_id="sample-transit", pem=public_key("transit").public_key_pem)])),
            ConfigurationFileMode.READ_ONLY)
        routes = ConfigurationArtifact("gateway-health-targets", "/etc/cpk/gateway/health-targets.json", ConfigurationMediaType.JSON,
            ReceiverManagementRoutesCodec().encode_bytes(ReceiverManagementRoutes(gateway.target, (
                ReceiverManagementRouteBinding("api", workload.target, workload.declaration, "http://api:8000"),
                ReceiverManagementRouteBinding("gateway", gateway.target, gateway.declaration, "http://gateway:8000"),
            ))).decode(),
            ConfigurationFileMode.READ_ONLY)
        for family, node_id, extras in (("workload", "api", (artifact,)), ("transit", "gateway", (transit, routes))):
            node, document = nodes[node_id], documents[family]
            environment = node.public_environment
            contract = document.product.runtime_contract
            if family == "workload":
                environment += (PublicStaticEnvironmentBinding(HTTP_VERIFICATION_SAMPLES_CONFIGURATION_ENVIRONMENT,
                                                              artifact.target_path),)
                default = codec.decode_bytes(contract.configuration_artifacts[0].content.encode())
                default = replace(default, declaration=workload.declaration)
                contract = replace(contract, verification=VerificationContract((check,)), control_surfaces=(surface,),
                    configuration_artifacts=(replace(contract.configuration_artifacts[0],
                                                     content=codec.encode_bytes(default).decode()),))
                node = replace(node, configuration_artifacts=(selected["workload"],),
                               block_spec=replace(node.block_spec, control_surfaces=(surface,)))
            contract = replace(contract, configuration_artifacts=contract.configuration_artifacts + extras,
                               public_environment=environment)
            document = ProductDescriptorCodec().encode_document(replace(document.product, runtime_contract=contract))
            documents[family] = document
            reference = ProductReference.from_document(document)
            nodes[node_id] = replace(node, configuration_artifacts=node.configuration_artifacts + extras,
                public_environment=environment, block_spec=replace(node.block_spec, verification=contract.verification),
                metadata={**node.metadata, "product_identity": reference.identity.key,
                          "product_descriptor_digest": reference.descriptor_sha256.value})
        self.transit_slot = ConfigurationArtifactRequirement(transit.artifact_id, transit.target_path,
                                                            transit.media_type, transit.file_mode)
        connector = nodes["connector"]
        document = ProductDescriptorCodec().encode_document(replace(documents["transit"].product,
            identity=ProductIdentity("test", "sample-connector", 1),
            runtime_contract=ProductRuntimeContract(sockets=connector.sockets)))
        documents["connector"] = document
        reference = ProductReference.from_document(document)
        nodes["connector"] = replace(connector, metadata={**connector.metadata,
            "product_identity": reference.identity.key, "product_descriptor_digest": reference.descriptor_sha256.value})
        return nodes, documents, selected

    def seed_ready_health(self):
        health = self.health_activity
        self.health_activity = next(item for item in self.health_plan.activities
            if type(item.operation) is WaitForHealthy and item.operation.target.node_id == "api")
        super().seed_ready_health()
        self.sample_start = self.start_value
        # The parent uses the ordinary management value to register its runtime;
        # no management attempt is executed. The journal stops before our check.
        self.health_activity = health
        self.start_value = super().health_start_value(activity=health)

    def health_start_value(self, *, activity=None):
        if activity is None or type(activity.operation) is not WaitForHealthy:
            return super().health_start_value(activity=activity)
        management = next(item for item in self.health_plan.activities
            if hasattr(item.operation, "node_id") and item.operation.node_id == "api")
        value = super().health_start_value(activity=management)
        from tests.test_runtime_interpreter_dispatcher import context_for
        from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
        with self.unit_of_work() as unit:
            plan = unit.stores.activity_history.get_plan("plan-a")
            template = context_for(activity.operation)
            context = replace(template, activity=activity,
                intent_event=replace(template.intent_event, activity_id=activity.activity_id.value),
                authority=value.authority, fence=value.fence,
                request=unit.stores.execution.get_request("request-a"), run=unit.stores.execution.get_run("run-a"),
                plan_record=plan, base_graph=self.projections["health-base"], desired_graph=self.projections["health-desired"],
                registered_products=tuple(self.receiver_products.values()), runtime_authorities=(self.sample_runtime,))
        intent = _runtime_effect_intent_for_context(context, activity)
        transition = EffectAttemptTransition(EffectAttemptTransitionKind.STARTED,
            EffectAttemptIdentity(intent.source.run_id, activity.activity_id.value, 1),
            runtime_effect_intent_fingerprint(intent))
        return replace(value, intent=intent, transition=transition)

    def seed_health_registrations(self):
        super().seed_health_registrations()
        # The existing owner lifecycle admits the two exact sample families.
        from control_plane_kit_operations.delegation_signing_keys import (
            ActivateDelegationSigningKeyCommand, DelegationSigningKeyRegistrationService, RegisterDelegationSigningKeyCommand,
        )
        from control_plane_kit_operations.secret_providers import (
            RegisterSecretProviderCommand, RegisterSecretReferenceCommand, SecretProviderKind, SecretProviderRegistrationService,
        )
        from control_plane_kit_core.secrets import SecretProviderEndpointReference, SecretProviderId, SecretReference
        from control_plane_kit_core.policies import PolicyScope
        pairs = (("transit", DelegationKeyPurpose.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT,
                  SecretUseIntent.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT_SIGNING_KEY),
                 ("workload", DelegationKeyPurpose.WORKLOAD_HTTP_VERIFICATION_SAMPLE,
                  SecretUseIntent.WORKLOAD_HTTP_VERIFICATION_SAMPLE_SIGNING_KEY))
        service = SecretProviderRegistrationService(self.unit_of_work)
        provider = service.register_provider(RegisterSecretProviderCommand(
            workspace_id="workspace-a", provider_id=SecretProviderId("sample-secrets"),
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS, display_name="Sample fixture",
            endpoint_reference=SecretProviderEndpointReference("sample-provider"),
            credential_reference=SecretReference("secret://sample-secrets/token"),
            allowed_reference_prefixes=(SecretReference("secret://sample-secrets/keys"),),
            allowed_intents=tuple(intent for _, _, intent in pairs), admitted_by="operator-a", admitted_at=self.clock(),
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        owner = DelegationSigningKeyRegistrationService(self.unit_of_work)
        self.sample_keys = {}
        for family, purpose, intent in pairs:
            reference = SecretReference("secret://sample-secrets/keys/" + family)
            service.register_reference(RegisterSecretReferenceCommand("workspace-a", reference, provider.registration_id,
                (intent,), "operator-a", self.clock(), (PolicyScope.SECRET_PROVIDER_REGISTER,)))
            owner.register(RegisterDelegationSigningKeyCommand("workspace-a", purpose, "cpk-server",
                public_key(family, key_id="sample-" + family), reference, "operator-a", self.clock(),
                (PolicyScope.DELEGATION_KEY_REGISTER,)))
            self.sample_keys[family] = owner.activate(ActivateDelegationSigningKeyCommand("workspace-a", purpose,
                "cpk-server", "sample-" + family, "operator-a", self.clock(), (PolicyScope.DELEGATION_KEY_ACTIVATE,)))

    def sample_port(self):
        started = self.service().execute(self.sample_start, command_context=self.bound)
        with self.unit_of_work() as uow:
            marker = uow.stores.execution.original_final_invocation(started.attempt.state.identity,
                original_event_id=started.attempt.original_start_event.event_id,
                request_fingerprint=started.attempt.state.request_fingerprint,
                kind=ExecutionInvocationKind.ORDINARY_VERIFICATION_ACQUISITION)
        self.assertIsNotNone(marker)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
        support, registration = self.sample_support()
        service = operations.ExecutionInvocationService(self.unit_of_work, permission_policy=self.policy,
            clock=self.clock, http_sample_support=support, http_sample_registration=registration)
        port = service.bind_runtime(command_context=self.bound, start=self.sample_start, started=started)
        permit = port.admit("ordinary-verification-acquisition")
        port.record_outcome(permit, "returned")
        return service, port


class PostgresHttpSampleInvocationTests(_HttpSampleFixture, unittest.TestCase):
    def test_reached_original_check_returns_two_exact_signing_roles(self):
        _, port = self.sample_port()
        child = port.for_check("ready")
        pair = child.prepare_http_sample().sender_input
        self.assertIsNotNone(pair, "reached HTTP check lacks its two signing roles")
        self.assertEqual(pair.check, HttpCheck(check_id="ready", provider_socket="http", path="/ready"))
        self.assertEqual(pair.compatibility.receiver.configuration.target.node_id.value, "api")
        self.assertEqual(pair.compatibility.gateway_trust.target.node_id.value, "gateway")
        self.assertEqual(pair.compatibility.route.target_id, "api")
        with self.unit_of_work() as uow:
            rows = tuple(uow.stores.secret_use_authorizations.get("workspace-a",
                getattr(pair, family + "_grant").authorization_id) for family in ("transit", "workload"))
        from control_plane_kit_operations.secret_providers import AuthorizedSecretUse
        for family, row, intent in zip(("transit", "workload"), rows, (
                SecretUseIntent.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT_SIGNING_KEY,
                SecretUseIntent.WORKLOAD_HTTP_VERIFICATION_SAMPLE_SIGNING_KEY)):
            role, grant = getattr(pair, family + "_key"), getattr(pair, family + "_grant")
            self.assertIs(type(row), AuthorizedSecretUse)
            self.assertEqual(role.registration_id, self.sample_keys[family].registration_id)
            self.assertEqual(role.public_key, self.sample_keys[family].public_key)
            self.assertIs(row.intent, intent)
            self.assertEqual(row.reference, self.sample_keys[family].private_key_reference)
            self.assertEqual(row.reference, grant.reference)
            self.assertEqual(row.reference_registration_id, grant.reference_registration_id)
            self.assertEqual(row.provider_registration_id, grant.provider_registration_id)
            self.assertIn(role.registration_id, row.probe_id)
        self.assertNotEqual(pair.transit_grant.authorization_id, pair.workload_grant.authorization_id)
        self.assertEqual(port.for_check("ready").prepare_http_sample().sender_input, pair)
        with self.unit_of_work() as uow:
            self.assertEqual(tuple(uow.stores.secret_use_authorizations.get("workspace-a", row.authorization_id)
                                   for row in rows), rows)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 2)

    def test_foreign_check_creates_no_authorization_rows(self):
        # Original acquisition is committed, but the caller cannot invent a check.
        _, port = self.sample_port()
        with self.assertRaises(operations.ExecutionInvocationConflict):
            port.for_check("foreign-ready")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
