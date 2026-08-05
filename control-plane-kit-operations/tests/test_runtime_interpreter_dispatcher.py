from __future__ import annotations

import unittest

from control_plane_kit_core.algebra import BlockSockets, BlockSpec, ProviderSocket
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    FailureCategory,
)
from control_plane_kit_core.planning import (
    ActivityId,
    ActivityPlan,
    AllocatePublicIngress,
    PlannedActivity,
    NodeTarget,
    PublicIngressActivityTarget,
    ReconcileRuntime,
    RemoveNodeResource,
    RuntimeTarget,
    SocketConnectionTarget,
    StartNode,
    StartRuntime,
    StopNode,
    SwitchSocketConnection,
    WaitForPublicIngressReady,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductReference,
    ProductRuntimeContract,
    ProviderRuntimePort,
)
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
    RuntimeEndpointObservation,
)
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectFailure,
    RuntimeEffectRequest,
    RuntimeEffectResult,
)
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery,
    SecretProviderEndpointReference,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)
from control_plane_kit_core.topology import DeploymentGraph, Node, RuntimeRecord
from control_plane_kit_core.types import BlockFamily, Protocol, RuntimeKind
from control_plane_kit_operations.coordinator import (
    ActivityExecutionDispatcher,
    ActivityExecutionOutcome,
    ActivityRealizationContext,
    RuntimeInterpreterDispatcher,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.products import InlineDescriptorSource, RegisteredProduct
from control_plane_kit_operations.runtime_authorities import (
    LocalDockerSocketAuthority,
    RegisteredRuntimeAuthority,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    SecretProviderAuthorizationDenied,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityPlanStatus,
    ActivityRunRecord,
    AdmittedRun,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    FailureEvidence,
    GraphVersionRecord,
    RealizedGraphProjectionRecord,
    RetryIdentity,
)


class RecordingInterpreter:
    def __init__(self, name: str, result: RuntimeEffectResult | None = None) -> None:
        self.name = name
        self.result = result
        self.requests: list[RuntimeEffectRequest] = []

    def execute(
        self,
        request: RuntimeEffectRequest,
    ) -> RuntimeEffectResult:
        self.requests.append(request)
        return self.result or RuntimeEffectResult.succeeded(
            request.effect_id,
            evidence={"interpreter": self.name},
        )


class RecordingActivityAdapter:
    def __init__(self, outcome: ActivityExecutionOutcome) -> None:
        self.outcome = outcome
        self.contexts: list[ActivityRealizationContext] = []

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome:
        self.contexts.append(context)
        return self.outcome


class AuthorityAwareRecordingInterpreter(RecordingInterpreter):
    def __init__(self, name: str, result: RuntimeEffectResult | None = None) -> None:
        super().__init__(name, result)
        self.authorities: list[RegisteredRuntimeAuthority] = []

    def execute_with_authority(
        self,
        request: RuntimeEffectRequest,
        authority: RegisteredRuntimeAuthority,
    ) -> RuntimeEffectResult:
        self.requests.append(request)
        self.authorities.append(authority)
        return self.result or RuntimeEffectResult.succeeded(
            request.effect_id,
            evidence={"authority_ref": authority.authority_ref.reference_id},
        )


class RecordingSecretUseAuthorizer:
    def __init__(self, *, denied: bool = False) -> None:
        self.denied = denied
        self.commands: list[AuthorizeSecretUse] = []

    def authorize_resolution(
        self,
        command: AuthorizeSecretUse,
    ) -> SecretResolutionGrant:
        self.commands.append(command)
        if self.denied:
            raise SecretProviderAuthorizationDenied("denied")
        return SecretResolutionGrant(
            authorization_id="suse_" + "a" * 64,
            workspace_id=command.workspace_id,
            reference_registration_id="sref_" + "b" * 64,
            provider_registration_id="sprov_" + "c" * 64,
            endpoint_reference=SecretProviderEndpointReference("provider-a"),
            credential_reference=SecretReference(
                "secret://bootstrap/provider-a-token"
            ),
            reference=command.reference,
            intent=command.intent,
            actor_subject=command.actor_subject,
            correlation_id=command.correlation_id,
            intent_fingerprint="d" * 64,
            operation_id=command.operation_id,
            session_id=command.session_id,
            run_id=command.run_id,
            activity_id=command.activity_id,
            effect_id=command.effect_id,
            probe_id=command.probe_id,
        )


class RuntimeInterpreterDispatcherTests(unittest.TestCase):
    def test_activity_dispatcher_preserves_handled_ingress_failure(self) -> None:
        ingress_failure = ActivityExecutionOutcome.unsupported(
            FailureEvidence(
                FailureCategory.OPERATOR_REVIEW,
                "secret.use-not-authorized",
                "ingress authority secret use was not authorized",
                BoundedEvidence(),
            )
        )
        ingress = RecordingActivityAdapter(ingress_failure)
        runtime = RecordingActivityAdapter(ActivityExecutionOutcome.succeeded())
        dispatcher = ActivityExecutionDispatcher(runtime=runtime, ingress=ingress)
        context = context_for(
            AllocatePublicIngress(PublicIngressActivityTarget("gateway-public"))
        )

        outcome = dispatcher.execute(context)

        self.assertIs(outcome, ingress_failure)
        self.assertEqual(ingress.contexts, [context])
        self.assertEqual(runtime.contexts, [])

    def test_activity_dispatcher_routes_public_readiness_only_to_ingress(self) -> None:
        ingress_outcome = ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping({"adapter": "ingress-readiness"})
        )
        ingress = RecordingActivityAdapter(ingress_outcome)
        runtime = RecordingActivityAdapter(ActivityExecutionOutcome.succeeded())
        dispatcher = ActivityExecutionDispatcher(runtime=runtime, ingress=ingress)
        context = context_for(
            WaitForPublicIngressReady(
                PublicIngressActivityTarget("gateway-public")
            )
        )

        outcome = dispatcher.execute(context)

        self.assertIs(outcome, ingress_outcome)
        self.assertEqual(ingress.contexts, [context])
        self.assertEqual(runtime.contexts, [])

    def test_activity_dispatcher_routes_runtime_operation_only_to_runtime(self) -> None:
        ingress = RecordingActivityAdapter(ActivityExecutionOutcome.succeeded())
        runtime_outcome = ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping({"adapter": "runtime"})
        )
        runtime = RecordingActivityAdapter(runtime_outcome)
        dispatcher = ActivityExecutionDispatcher(runtime=runtime, ingress=ingress)
        context = context_for(StartRuntime(RuntimeTarget("runtime-a")))

        outcome = dispatcher.execute(context)

        self.assertIs(outcome, runtime_outcome)
        self.assertEqual(runtime.contexts, [context])
        self.assertEqual(ingress.contexts, [])

    def test_activity_dispatcher_reports_missing_ingress_without_runtime_call(
        self,
    ) -> None:
        runtime = RecordingActivityAdapter(ActivityExecutionOutcome.succeeded())
        dispatcher = ActivityExecutionDispatcher(runtime=runtime)
        context = context_for(
            AllocatePublicIngress(PublicIngressActivityTarget("gateway-public"))
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        self.assertIsNotNone(outcome.failure)
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "ingress.interpreter-missing")
        self.assertEqual(
            outcome.failure.details.descriptor(),
            {
                "activity_id": "activity-a",
                "operation": "AllocatePublicIngress",
            },
        )
        self.assertEqual(runtime.contexts, [])

    def test_start_node_dispatches_by_desired_graph_runtime_kind(self) -> None:
        docker = RecordingInterpreter("docker")
        dry_run = RecordingInterpreter("dry-run")
        dispatcher = RuntimeInterpreterDispatcher(
            {
                RuntimeKind.DOCKER: docker,
                RuntimeKind.DRY_RUN: dry_run,
            }
        )
        context = context_for(
            StartNode(NodeTarget("api")),
            base_kind=RuntimeKind.DRY_RUN,
            desired_kind=RuntimeKind.DOCKER,
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(outcome.evidence.descriptor(), {"interpreter": "docker"})
        self.assertEqual(len(docker.requests), 1)
        self.assertEqual(docker.requests[0].runtime_kind, RuntimeKind.DOCKER)
        self.assertEqual(docker.requests[0].activity_id, ActivityId("activity-a"))
        self.assertEqual(dry_run.requests, [])

    def test_stop_node_dispatches_by_base_graph_runtime_kind(self) -> None:
        docker = RecordingInterpreter("docker")
        dry_run = RecordingInterpreter("dry-run")
        dispatcher = RuntimeInterpreterDispatcher(
            {
                RuntimeKind.DOCKER: docker,
                RuntimeKind.DRY_RUN: dry_run,
            }
        )
        context = context_for(
            StopNode(NodeTarget("api")),
            base_kind=RuntimeKind.DOCKER,
            desired_kind=RuntimeKind.DRY_RUN,
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(outcome.evidence.descriptor(), {"interpreter": "docker"})
        self.assertEqual(len(docker.requests), 1)
        self.assertEqual(docker.requests[0].runtime_kind, RuntimeKind.DOCKER)
        self.assertEqual(dry_run.requests, [])

    def test_runtime_operation_dispatches_from_pinned_runtime_record(self) -> None:
        dry_run = RecordingInterpreter("dry-run")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DRY_RUN: dry_run})
        context = context_for(
            ReconcileRuntime(RuntimeTarget("runtime-a")),
            base_kind=RuntimeKind.DOCKER,
            desired_kind=RuntimeKind.DRY_RUN,
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.evidence.descriptor(), {"interpreter": "dry-run"})
        self.assertEqual(len(dry_run.requests), 1)
        self.assertEqual(dry_run.requests[0].runtime_kind, RuntimeKind.DRY_RUN)

    def test_missing_interpreter_is_explicit_unsupported_without_attempt(self) -> None:
        docker = RecordingInterpreter("docker")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: docker})
        context = context_for(
            StartRuntime(RuntimeTarget("runtime-a")),
            desired_kind=RuntimeKind.AWS,
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        self.assertIsNotNone(outcome.failure)
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "runtime.interpreter-missing")
        self.assertEqual(
            outcome.failure.details.descriptor(),
            {
                "activity_id": "activity-a",
                "operation": "StartRuntime",
                "runtime_kind": "aws",
            },
        )
        self.assertEqual(docker.requests, [])

    def test_registered_runtime_authority_is_supplied_to_authority_aware_interpreter(self) -> None:
        docker = AuthorityAwareRecordingInterpreter("docker")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: docker})
        authority = _registered_runtime_authority()
        context = context_for(
            StartRuntime(RuntimeTarget("runtime-a")),
            authority_ref=authority.authority_ref,
            runtime_authorities=(authority,),
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(
            outcome.evidence.descriptor(),
            {"authority_ref": "local-docker"},
        )
        self.assertEqual(len(docker.requests), 1)
        self.assertEqual(docker.requests[0].authority_ref, RuntimeAuthorityReference("local-docker"))
        self.assertEqual(docker.authorities, [authority])

    def test_missing_registered_runtime_authority_fails_before_interpreter_io(self) -> None:
        docker = AuthorityAwareRecordingInterpreter("docker")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: docker})
        context = context_for(
            StartRuntime(RuntimeTarget("runtime-a")),
            authority_ref=RuntimeAuthorityReference("missing-docker"),
            runtime_authorities=(),
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        self.assertIsNotNone(outcome.failure)
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "runtime.authority-missing")
        self.assertEqual(docker.requests, [])
        self.assertEqual(docker.authorities, [])

    def test_authority_ref_requires_authority_aware_interpreter(self) -> None:
        docker = RecordingInterpreter("docker")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: docker})
        authority = _registered_runtime_authority()
        context = context_for(
            StartRuntime(RuntimeTarget("runtime-a")),
            authority_ref=authority.authority_ref,
            runtime_authorities=(authority,),
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        self.assertIsNotNone(outcome.failure)
        assert outcome.failure is not None
        self.assertEqual(
            outcome.failure.code,
            "runtime.authority-interpreter-unsupported",
        )
        self.assertEqual(docker.requests, [])

    def test_socket_connection_operation_is_recorded_without_runtime_effect(self) -> None:
        docker = RecordingInterpreter("docker")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: docker})
        context = context_for(
            SwitchSocketConnection(SocketConnectionTarget("edge-a")),
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertIsNone(outcome.failure)
        self.assertEqual(
            outcome.evidence.descriptor(),
            {
                "action": "socket-connection-recorded",
                "edge_id": "edge-a",
                "operation": "SwitchSocketConnection",
            },
        )
        self.assertEqual(docker.requests, [])

    def test_missing_base_node_is_explicit_unsupported_without_desired_lookup(self) -> None:
        docker = RecordingInterpreter("docker")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: docker})
        context = context_for(
            RemoveNodeResource(NodeTarget("api")),
            base_graph=graph_without_node(RuntimeKind.DOCKER),
            desired_kind=RuntimeKind.DOCKER,
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        self.assertIsNotNone(outcome.failure)
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "runtime.dispatch-target-unsupported")
        self.assertEqual(outcome.failure.message, "runtime effect node target is missing")
        self.assertEqual(docker.requests, [])

    def test_runtime_result_failure_is_converted_to_activity_outcome(self) -> None:
        interpreter = RecordingInterpreter(
            "docker",
            RuntimeEffectResult.failed(
                "event-intent",
                RuntimeEffectFailure(
                    "docker.container-failed",
                    "container failed",
                    {"container": "api"},
                ),
            ),
        )
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: interpreter})

        outcome = dispatcher.execute(context_for(StartNode(NodeTarget("api"))))

        self.assertEqual(outcome.kind.name, "FAILED")
        self.assertIsNotNone(outcome.failure)
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "docker.container-failed")
        self.assertEqual(outcome.failure.details.descriptor(), {"container": "api"})

    def test_runtime_result_effect_id_mismatch_becomes_uncertain(self) -> None:
        interpreter = RecordingInterpreter(
            "docker",
            RuntimeEffectResult.succeeded("different-effect"),
        )
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: interpreter})

        outcome = dispatcher.execute(context_for(StartNode(NodeTarget("api"))))

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertIsNotNone(outcome.failure)
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "runtime.effect-id-mismatch")

    def test_runtime_endpoint_observations_become_operations_observations(self) -> None:
        interpreter = RecordingInterpreter(
            "docker",
            RuntimeEffectResult.succeeded(
                "event-intent",
                observations=(
                    RuntimeEndpointObservation(
                        "api",
                        "http",
                        "graph-desired",
                        Protocol.HTTP,
                        EndpointContext.RUNTIME_PRIVATE,
                        LiteralEndpointMaterial("http://api-http:8000"),
                    ),
                ),
            ),
        )
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: interpreter})

        outcome = dispatcher.execute(context_for(StartNode(NodeTarget("api"))))

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(len(outcome.observations), 1)
        observation = outcome.observations[0]
        self.assertEqual(observation.observation_id, "event-intent:runtime-endpoint:1")
        self.assertEqual(observation.workspace_id, "workspace-a")
        self.assertEqual(observation.subject_id, "api")
        self.assertEqual(observation.graph_id, "graph-desired")
        self.assertEqual(observation.endpoint_context, EndpointContext.RUNTIME_PRIVATE)
        self.assertEqual(observation.evidence.descriptor()["runtime_endpoint"]["subject_id"], "api")

    def test_secret_use_is_authorized_before_runtime_interpreter_io(self) -> None:
        interpreter = RecordingInterpreter("docker")
        authorizer = RecordingSecretUseAuthorizer()
        dispatcher = RuntimeInterpreterDispatcher(
            {RuntimeKind.DOCKER: interpreter},
            secret_use_authorizer=authorizer,
        )

        outcome = dispatcher.execute(
            context_for(
                StartNode(NodeTarget("api")),
                secret_delivery=True,
                worker_scopes=(
                    PolicyScope.EXECUTION_OPERATE,
                    PolicyScope.SECRET_PROVIDER_USE,
                ),
            )
        )

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(len(authorizer.commands), 1)
        command = authorizer.commands[0]
        self.assertEqual(
            command.reference,
            SecretReference("secret://provider-a/application/token"),
        )
        self.assertEqual(command.intent, SecretUseIntent.APPLICATION_CONTROL_TOKEN)
        self.assertEqual(command.actor_subject, "worker-a")
        self.assertEqual(command.effect_id, "event-intent")
        self.assertEqual(len(interpreter.requests), 1)
        self.assertEqual(
            interpreter.requests[0].secret_resolution_grants,
            (
                SecretResolutionGrant(
                    authorization_id="suse_" + "a" * 64,
                    workspace_id="workspace-a",
                    reference_registration_id="sref_" + "b" * 64,
                    provider_registration_id="sprov_" + "c" * 64,
                    endpoint_reference=SecretProviderEndpointReference("provider-a"),
                    credential_reference=SecretReference(
                        "secret://bootstrap/provider-a-token"
                    ),
                    reference=SecretReference(
                        "secret://provider-a/application/token"
                    ),
                    intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
                    actor_subject="worker-a",
                    correlation_id=command.correlation_id,
                    intent_fingerprint="d" * 64,
                    operation_id="request-a",
                    session_id="session-a",
                    run_id="run-a",
                    activity_id="activity-a",
                    effect_id="event-intent",
                ),
            ),
        )

    def test_missing_secret_authorizer_fails_before_runtime_io(self) -> None:
        interpreter = RecordingInterpreter("docker")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: interpreter})

        outcome = dispatcher.execute(
            context_for(StartNode(NodeTarget("api")), secret_delivery=True)
        )

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        assert outcome.failure is not None
        self.assertEqual(
            outcome.failure.code,
            "secret.resolution-authorizer-invalid",
        )
        self.assertEqual(interpreter.requests, [])

    def test_denied_secret_use_fails_before_runtime_io(self) -> None:
        interpreter = RecordingInterpreter("docker")
        authorizer = RecordingSecretUseAuthorizer(denied=True)
        dispatcher = RuntimeInterpreterDispatcher(
            {RuntimeKind.DOCKER: interpreter},
            secret_use_authorizer=authorizer,
        )

        outcome = dispatcher.execute(
            context_for(StartNode(NodeTarget("api")), secret_delivery=True)
        )

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "secret.use-not-authorized")
        self.assertEqual(interpreter.requests, [])

    def test_secret_use_retry_correlation_is_deterministic(self) -> None:
        interpreter = RecordingInterpreter("docker")
        authorizer = RecordingSecretUseAuthorizer()
        dispatcher = RuntimeInterpreterDispatcher(
            {RuntimeKind.DOCKER: interpreter},
            secret_use_authorizer=authorizer,
        )
        context = context_for(
            StartNode(NodeTarget("api")),
            secret_delivery=True,
            worker_scopes=(
                PolicyScope.EXECUTION_OPERATE,
                PolicyScope.SECRET_PROVIDER_USE,
            ),
        )

        dispatcher.execute(context)
        dispatcher.execute(context)

        self.assertEqual(len(authorizer.commands), 2)
        self.assertEqual(
            authorizer.commands[0].correlation_id,
            authorizer.commands[1].correlation_id,
        )


def context_for(
    operation,
    *,
    base_kind: RuntimeKind = RuntimeKind.DOCKER,
    desired_kind: RuntimeKind = RuntimeKind.DOCKER,
    base_graph: DeploymentGraph | None = None,
    authority_ref: RuntimeAuthorityReference | None = None,
    runtime_authorities: tuple[RegisteredRuntimeAuthority, ...] = (),
    secret_delivery: bool = False,
    worker_scopes: tuple[PolicyScope, ...] = (PolicyScope.EXECUTION_OPERATE,),
) -> ActivityRealizationContext:
    activity = PlannedActivity(ActivityId("activity-a"), operation)
    plan = ActivityPlan((activity,))
    registered_product = _registered_product(secret_delivery=secret_delivery)
    return ActivityRealizationContext(
        activity=activity,
        request=ExecutionRequestRecord(
            ExecutionRequestIdentity("request-a", "workspace-a", "session-a", "plan-a"),
            ExecutionRequestStatus.CLAIMED,
            "operator-a",
            "2026-07-22T10:00:00Z",
            "approval-request-a",
            "approval-decision-a",
            ExecutionIdempotency("execute-a", "fingerprint-a"),
            ClaimIdentity("worker-a", "2026-07-22T10:01:00Z", "2026-07-22T10:30:00Z"),
        ),
        run=ActivityRunRecord(
            "run-a",
            "plan-a",
            AdmittedRun("request-a"),
            RetryIdentity(1),
            ActivityRunStatus.RUNNING,
            "2026-07-22T10:01:00Z",
            started_at="2026-07-22T10:02:00Z",
        ),
        plan_record=ActivityPlanRecord(
            "plan-a",
            "session-a",
            "graph-current",
            "graph-desired",
            ActivityPlanStatus.PLANNED,
            "2026-07-22T10:00:30Z",
            plan,
        ),
        base_graph=projection_record_from_graph(
            "graph-current",
            base_graph
            if base_graph is not None
            else graph_with_node(
                base_kind,
                registered_product=registered_product,
            ),
        ),
        desired_graph=projection_record_from_graph(
            "graph-desired",
            graph_with_node(
                desired_kind,
                authority_ref=authority_ref,
                registered_product=registered_product,
            ),
            version=2,
        ),
        registered_products=(registered_product,),
        authority=ExecutionWorkerAuthority(
            "worker-a",
            worker_scopes,
        ),
        runtime_authorities=runtime_authorities,
        intent_event=ActivityEventRecord(
            "event-intent",
            "run-a",
            1,
            ActivityEventKind.STEP_STARTED,
            "2026-07-22T10:02:30Z",
            activity_id="activity-a",
        ),
    )


def projection_record_from_graph(
    graph_id: str,
    graph: DeploymentGraph,
    *,
    version: int = 1,
) -> RealizedGraphProjectionRecord:
    return RealizedGraphProjectionRecord.identity_for_authored(
        authored_record=GraphVersionRecord.from_graph(
            graph_id=graph_id,
            workspace_id="workspace-a",
            version=version,
            graph=graph,
            created_by="operator-a",
            created_at="2026-07-22T10:00:00Z",
        )
    )


def graph_with_node(
    kind: RuntimeKind,
    *,
    authority_ref: RuntimeAuthorityReference | None = None,
    registered_product: RegisteredProduct | None = None,
) -> DeploymentGraph:
    product = registered_product or _registered_product()
    reference = ProductReference.from_document(product.descriptor_document)
    return DeploymentGraph(
        "graph",
        nodes={
            "api": Node(
                "api",
                BlockFamily.APPLICATION,
                BlockSpec("api"),
                "container",
                "runtime-a",
                BlockSockets(),
                metadata={
                    "product_identity": reference.identity.key,
                    "product_descriptor_digest": reference.descriptor_sha256.value,
                },
            )
        },
        runtimes={
            "runtime-a": RuntimeRecord(
                "runtime-a",
                kind,
                children=("api",),
                authority_ref=authority_ref,
            )
        },
    )


def graph_without_node(kind: RuntimeKind) -> DeploymentGraph:
    return DeploymentGraph(
        "graph",
        runtimes={
            "runtime-a": RuntimeRecord(
                "runtime-a",
                kind,
            )
        },
    )


def _registered_runtime_authority() -> RegisteredRuntimeAuthority:
    return RegisteredRuntimeAuthority.from_authority(
        workspace_id="workspace-a",
        authority_ref=RuntimeAuthorityReference("local-docker"),
        runtime_kind=RuntimeKind.DOCKER,
        authority=LocalDockerSocketAuthority(),
        admitted_by="operator-a",
        admitted_at="2026-07-22T09:30:00Z",
    )


def _registered_product(*, secret_delivery: bool = False) -> RegisteredProduct:
    secret_deliveries = ()
    if secret_delivery:
        secret_deliveries = (
            SecretEnvironmentDelivery(
                "APPLICATION_CONTROL_TOKEN",
                SecretReference("secret://provider-a/application/token"),
                SecretUseIntent.APPLICATION_CONTROL_TOKEN,
            ),
        )
    product = ContainerServerProduct(
        identity=ProductIdentity("openj92", "hello-server", 1),
        image=OciImageReference(
            registry="ghcr.io",
            repository="openj92/control-plane-kit-servers/hello-server",
            digest="sha256:" + "a" * 64,
        ),
        runtime_contract=ProductRuntimeContract(
            sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),)),
            provider_ports=(ProviderRuntimePort("http", 8000),),
            secret_deliveries=secret_deliveries,
        ),
    )
    return RegisteredProduct.from_document(
        workspace_id="workspace-a",
        descriptor_document=ProductDescriptorCodec().encode_document(product),
        source=InlineDescriptorSource(),
        imported_by="operator-a",
        imported_at="2026-07-22T09:00:00Z",
    )


if __name__ == "__main__":
    unittest.main()
