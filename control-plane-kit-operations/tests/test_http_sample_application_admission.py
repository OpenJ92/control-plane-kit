"""Normal plan/approval/admission, with provider dispatch forbidden by the fixture.

No execution history is seeded and no native conformance is inferred from this
declared recording port. Rowan's normal-driver/native proof owns that join.
"""
from dataclasses import replace
import json

from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationArtifactRequirement, ConfigurationMediaType
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.http_verification_sample_installation import (
    HTTP_VERIFICATION_SAMPLES_CONFIGURATION_ENVIRONMENT, HttpVerificationSampleCheckBinding,
    HttpVerificationSampleReceiverConfiguration, HttpVerificationSampleReceiverConfigurationCodec,
    HttpVerificationSampleVerifierConfiguration,
)
from control_plane_kit_core.http_verification_samples import http_verification_check_digest
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose, DelegationKeyAlgorithm, DelegationPublicKey
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.products import ProductDescriptorCodec, ProductReference
from control_plane_kit_core.receiver_identity import NodeControlReceiverTargetCodec
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_core.verification import HttpCheck, VerificationContract
from control_plane_kit_operations.admission import ExecutionAdmissionCommandService, ExecutionAdmissionDenied
from control_plane_kit_operations.cpk_server import CpkServerAdmissionService
from control_plane_kit_operations.coordinator import ExecutionCoordinator, ExecuteActivityRun, ExecuteManagedActivityRun, RuntimeInterpreterDispatcher
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.execution_invocation_service import ExecutionInvocationService
from control_plane_kit_operations.health_receiver_trust import HealthReceiverDecoders
from control_plane_kit_operations.http_sample_compatibility import GatewayHttpSampleBinding, GatewayHttpSampleTrust
from control_plane_kit_operations.http_sample_execution import HttpSampleExecutionSupport
from control_plane_kit_operations.lifecycle import RunLifecycleCommandService, ExecutionWorkerAuthority
from control_plane_kit_operations.runtime_adapter_registration import RuntimeAdapterRegistration
from control_plane_kit_operations.workflows import IdempotencyKey
from tests import test_managed_application_chain as chain
from tests.test_postgres_coordinator_permissions import _CurrentCoordinatorPermission
from tests.health_receiver_trust_fixture import public_key


class _GatewayDecoder:
    def decode(self, artifact):
        value = json.loads(artifact.content)
        return GatewayHttpSampleTrust(NodeControlReceiverTargetCodec().decode(value["target"]),
            DelegationKeyPurpose(value["purpose"]), value["issuer"], tuple(
                DelegationPublicKey(key["key_id"], DelegationKeyAlgorithm.ED25519, key["pem"])
                for key in value["keys"]))


class _NoDispatch:
    def __init__(self, support):
        self.http_sample_support, self.calls = support, []

    def execute_with_authority(self, request, authority, *, invocations):
        self.calls.append(request)
        raise AssertionError("admission/preflight tests must never dispatch provider effects")


class HttpSampleApplicationAdmissionTests(_CurrentCoordinatorPermission, chain.ManagedApplicationFixture):
    def authored_graph(self):
        graph, documents, registry = super().authored_graph()
        codec = ReceiverNodeControlConfigurationCodec()
        api, gateway = graph.node("api"), graph.node("gateway")
        receiver = codec.decode_bytes(next(value for value in api.configuration_artifacts
            if value.artifact_id == "test-workload").content.encode())
        own = codec.decode_bytes(next(value for value in gateway.configuration_artifacts
            if value.artifact_id == "gateway-control").content.encode())
        checks = tuple(HttpCheck(check_id=name, provider_socket="http", path="/health/" + name)
            for name in ("live", "ready"))
        sample = HttpVerificationSampleReceiverConfiguration(receiver.target, receiver.declaration.identity().value,
            HttpVerificationSampleVerifierConfiguration(DelegationKeyPurpose.WORKLOAD_HTTP_VERIFICATION_SAMPLE,
                "sample-controller", (public_key("workload"),)), tuple(
                    HttpVerificationSampleCheckBinding(check.check_id, http_verification_check_digest(check)) for check in checks))
        artifact = ConfigurationArtifact("http-samples", "/etc/test/http-samples.json", ConfigurationMediaType.JSON,
            HttpVerificationSampleReceiverConfigurationCodec().encode_canonical_bytes(sample).decode())
        transit = ConfigurationArtifact("sample-transit", "/etc/test/sample-transit.json", ConfigurationMediaType.JSON,
            json.dumps(dict(target=own.target.descriptor(),
                purpose=DelegationKeyPurpose.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT.value,
                issuer="sample-controller", keys=[dict(key_id=public_key("transit").key_id,
                    pem=public_key("transit").public_key_pem)])))
        nodes, replacements = dict(graph.nodes), {}
        for name, extra in (("api", artifact), ("gateway", transit)):
            node, document = nodes[name], documents[name]
            contract = document.product.runtime_contract
            if name == "api":
                contract = replace(contract, verification=VerificationContract(checks),
                    public_environment=contract.public_environment + (PublicStaticEnvironmentBinding(
                        HTTP_VERIFICATION_SAMPLES_CONFIGURATION_ENVIRONMENT, artifact.target_path),))
            contract = replace(contract, configuration_artifacts=contract.configuration_artifacts + (extra,))
            updated = ProductDescriptorCodec().encode_document(replace(document.product, runtime_contract=contract))
            reference = ProductReference.from_document(updated)
            replacements[ProductReference.from_document(document)] = reference
            documents[name] = updated
            nodes[name] = replace(node, configuration_artifacts=node.configuration_artifacts + (extra,),
                public_environment=contract.public_environment, block_spec=replace(node.block_spec, verification=contract.verification),
                metadata={"product_identity": reference.identity.key, "product_descriptor_digest": reference.descriptor_sha256.value})
        self.support = HttpSampleExecutionSupport(GatewayHttpSampleBinding(ProductReference.from_document(documents["gateway"]),
            ConfigurationArtifactRequirement(transit.artifact_id, transit.target_path, transit.media_type, transit.file_mode),
            _GatewayDecoder(), route_slot=ConfigurationArtifactRequirement("gateway-health-targets",
                "/etc/cpk/gateway/health-targets.json", ConfigurationMediaType.JSON, transit.file_mode)), "http")
        return replace(graph, nodes=nodes), documents, HealthReceiverDecoders(tuple(replace(binding,
            product_reference=replacements.get(binding.product_reference, binding.product_reference)) for binding in registry.bindings))

    def setUp(self):
        super().setUp()
        self.enabled = True
        self.sample_adapter = _NoDispatch(self.support)
        self.registration = self.registered(self.sample_adapter)

    def registered(self, adapter):
        return RuntimeAdapterRegistration(adapter, exact_version_execute_with_authority=True,
            invocation_profile="cpk.semantic-invocations.v1", ordinary_verification_invocations=True,
            http_sample_support=adapter.http_sample_support)

    def application(self, **options):
        app = super().application(**options)
        services = dict(app.services)
        services[ControlPlaneServiceRole.ADMISSION] = CpkServerAdmissionService(ExecutionAdmissionCommandService(
            self.unit_of_work, clock=chain.now, id_factory=self.ids["admission"],
            http_sample_support=self.support if self.enabled else None,
            http_sample_registration=self.registration if self.enabled else None))
        return replace(app, services=services)

    async def invoke(self, route, role, **options):
        if route == "command.deployment.prepare":
            options["payload"] = dict(options.get("payload", {}), http_verification_intent="authenticated-samples")
        return await super().invoke(route, role, **options)

    async def prepared(self):
        try:
            await self.prepare_and_start()
        except ExecutionAdmissionDenied:
            self.fail("normally approved fully registered sample admission is unavailable")

    async def test_registered_sample_plan_admits_without_provider_dispatch(self):
        await self.prepared()
        self.assertEqual(len(self.rows("cpk_execution_requests")), 1)
        self.assertEqual(self.effects(), ((), (), (), (), ()))
        self.assertEqual(self.rows("cpk_secret_use_authorizations"), ())

    async def test_missing_registered_sender_refuses_before_creating_request(self):
        self.enabled = False
        with self.assertRaises(ExecutionAdmissionDenied):
            await self.prepare_and_start()
        self.assertEqual(self.rows("cpk_execution_requests"), ())
        self.assertEqual(self.effects(), ((), (), (), (), ()))

    async def test_incompatible_registered_route_refuses_before_creating_request(self):
        self.support = replace(self.support, gateway_binding=replace(self.support.gateway_binding,
            slot=replace(self.support.gateway_binding.slot, target_path="/etc/test/other-trust.json")))
        self.sample_adapter = _NoDispatch(self.support)
        self.registration = self.registered(self.sample_adapter)
        with self.assertRaises(ExecutionAdmissionDenied):
            await self.prepare_and_start()
        self.assertEqual(self.rows("cpk_execution_requests"), ())
        self.assertEqual(self.effects(), ((), (), (), (), ()))

    async def test_registration_changed_after_admission_refuses_before_gateway_effect(self):
        await self.prepared()
        self.configure_permissions(self.worker.identity, installation="sample-preflight")
        fold = EffectAttemptFoldService(self.unit_of_work, id_factory=self.ids["effect-fold"])
        changed = _NoDispatch(replace(self.support))
        owner = ExecutionCoordinator(self.unit_of_work,
            lifecycle=RunLifecycleCommandService(self.unit_of_work, clock=chain.now, id_factory=self.ids["lifecycle"]),
            adapter=RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: self.registered(changed)}),
            start_service=EffectAttemptStartService(self.unit_of_work, id_factory=self.ids["effect-start"],
                health_receiver_decoders=self.registry, permission_policy=self.policy), fold_service=fold,
            reconciliation_service=EffectAttemptReconciliationService(self.unit_of_work, chain.ForbiddenRecovery(), fold),
            clock=chain.now, id_factory=self.ids["execution"], permission_policy=self.policy,
            invocation_service=ExecutionInvocationService(self.unit_of_work, permission_policy=self.policy, clock=chain.now,
                http_sample_support=self.support, http_sample_registration=self.registration))
        with self.unit_of_work() as unit:
            request = unit.stores.execution.get_request(self.request_id)
        execution = ExecuteActivityRun(self.run_id, ExecutionWorkerAuthority(request.claim.worker_id,
            self.original.granted_scopes), request.claim.fence, IdempotencyKey("changed-registration"))
        result = await owner.execute_managed(ExecuteManagedActivityRun(execution, self.original))
        self.assertEqual((result.status.value, result.effects_attempted), ("unsupported", 0))
        self.assertEqual(changed.calls, [])
        self.assertEqual(self.effects(), ((), (), (), (), ()))
