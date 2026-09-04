from __future__ import annotations

import json
import unittest
from dataclasses import fields, replace

from control_plane_kit_core.algebra import BlockSockets, BlockSpec, ProviderSocket
from control_plane_kit_core.operations.execution import EffectResultKind
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
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
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
from control_plane_kit_operations.products import (
    InlineDescriptorSource,
    RegisteredProduct,
)
from control_plane_kit_operations.runtime_authorities import (
    LocalDockerSocketAuthority,
    RegisteredRuntimeAuthority,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    SecretProviderAuthorizationDenied,
    secret_use_correlation_for,
)
from control_plane_kit_operations.runtime_effects import (
    runtime_effect_request_for_context,
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
    OperationsRecordError,
    RealizedGraphProjectionRecord,
    RetryIdentity,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


ENDPOINT_TEXT_MAX = 512
BRIDGE_EVIDENCE_MAX_BYTES = 4_096


def _raw_endpoint_descriptor(*, subject_id: str) -> dict[str, object]:
    descriptor = RuntimeEndpointObservation(
        "api",
        "http",
        "graph-desired",
        Protocol.HTTP,
        EndpointContext.RUNTIME_PRIVATE,
        LiteralEndpointMaterial("http://api-http:8000"),
    ).descriptor()
    descriptor["subject_id"] = subject_id
    return descriptor


def _bridge_evidence_size(*, subject_id: str) -> int:
    document = {
        "runtime_endpoint": _raw_endpoint_descriptor(subject_id=subject_id),
    }
    return len(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _subject_for_bridge_evidence_size(target: int) -> str:
    marker = "\U0001f4a1"
    for marker_count in range(ENDPOINT_TEXT_MAX + 1):
        base = marker * marker_count
        remaining = target - _bridge_evidence_size(subject_id=base)
        if 0 <= remaining <= ENDPOINT_TEXT_MAX - marker_count:
            candidate = base + "s" * remaining
            if _bridge_evidence_size(subject_id=candidate) == target:
                return candidate
    raise AssertionError(f"cannot construct {target}-byte endpoint evidence")


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
    def __init__(
        self,
        outcome: ActivityExecutionOutcome,
        runtime_result: RuntimeEffectResult | None = None,
    ) -> None:
        self.outcome = outcome
        self.runtime_result = runtime_result
        self.contexts: list[ActivityRealizationContext] = []
        self.runtime_requests: list[RuntimeEffectRequest] = []

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome:
        self.contexts.append(context)
        return self.outcome

    def execute_runtime(
        self,
        context: ActivityRealizationContext,
        request: RuntimeEffectRequest,
    ) -> RuntimeEffectResult:
        self.contexts.append(context)
        self.runtime_requests.append(request)
        return self.runtime_result or RuntimeEffectResult.succeeded(
            request.effect_id,
            evidence={"adapter": "runtime"},
        )


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
    def execute_runtime(
        self,
        dispatcher,
        context: ActivityRealizationContext,
        request: RuntimeEffectRequest | None = None,
    ) -> RuntimeEffectResult:
        operation = getattr(dispatcher, "execute_runtime", None)
        self.assertIsNotNone(operation, "runtime adapter arm is missing")
        exact_request = request or runtime_effect_request_for_context(context)
        return operation(context, exact_request)

    def test_runtime_arm_forwards_the_exact_post_start_request(self) -> None:
        expected = RuntimeEffectResult.succeeded(
            "event-intent",
            evidence={"provider": "docker"},
        )
        interpreter = RecordingInterpreter("docker", expected)
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: interpreter})
        context = context_for(StartNode(NodeTarget("api")))
        request = runtime_effect_request_for_context(context)
        execute_runtime = getattr(dispatcher, "execute_runtime", None)
        self.assertIsNotNone(execute_runtime, "runtime adapter arm is missing")

        result = execute_runtime(context, request)
        with self.assertRaises(InvalidOperationCommand):
            dispatcher.execute(context)

        self.assertIs(result, expected)
        self.assertEqual(interpreter.requests, [request])
        self.assertIs(interpreter.requests[0], request)

    def test_activity_dispatcher_keeps_legacy_and_runtime_arms_disjoint(self) -> None:
        ingress_outcome = ActivityExecutionOutcome.succeeded()
        ingress = RecordingActivityAdapter(ingress_outcome)
        runtime = RecordingActivityAdapter(ActivityExecutionOutcome.succeeded())
        dispatcher = ActivityExecutionDispatcher(runtime=runtime, ingress=ingress)
        ingress_context = context_for(
            AllocatePublicIngress(PublicIngressActivityTarget("gateway-public"))
        )
        runtime_context = context_for(StartNode(NodeTarget("api")))
        request = runtime_effect_request_for_context(runtime_context)
        execute_runtime = getattr(dispatcher, "execute_runtime", None)
        self.assertIsNotNone(execute_runtime, "runtime adapter arm is missing")

        legacy = dispatcher.execute(ingress_context)
        runtime_result = execute_runtime(runtime_context, request)
        with self.assertRaises(InvalidOperationCommand):
            dispatcher.execute(runtime_context)

        self.assertIs(legacy, ingress_outcome)
        self.assertIs(type(runtime_result), RuntimeEffectResult)
        self.assertEqual(runtime_result.effect_id, request.effect_id)
        self.assertEqual(ingress.contexts, [ingress_context])
        self.assertEqual(runtime.contexts, [runtime_context])

    def test_runtime_wrong_arm_hostile_result_and_provider_fault_are_uncertain(
        self,
    ) -> None:
        context = context_for(StartNode(NodeTarget("api")))
        request = runtime_effect_request_for_context(context)
        dispatches: list[str] = []

        class HostileResult(RuntimeEffectResult):
            def __getattribute__(self, name):
                if name in {"effect_id", "kind", "failure", "__class__"}:
                    dispatches.append(name)
                    raise AssertionError("hostile runtime result dispatched")
                return super().__getattribute__(name)

            def __eq__(self, _other):
                dispatches.append("eq")
                raise AssertionError("hostile runtime result equality dispatched")

        lawful = RuntimeEffectResult.succeeded(request.effect_id)
        hostile = object.__new__(HostileResult)
        for item in fields(RuntimeEffectResult):
            object.__setattr__(hostile, item.name, getattr(lawful, item.name))

        class RaisingInterpreter:
            def execute(self, _request):
                raise RuntimeError("provider-secret-canary")

        cases = (
            (
                "wrong-arm",
                RecordingInterpreter("docker", ActivityExecutionOutcome.succeeded()),
            ),
            ("hostile-subclass", RecordingInterpreter("docker", hostile)),
            ("provider-fault", RaisingInterpreter()),
        )
        for label, interpreter in cases:
            with self.subTest(case=label):
                dispatches.clear()
                dispatcher = RuntimeInterpreterDispatcher(
                    {RuntimeKind.DOCKER: interpreter}
                )
                execute_runtime = getattr(dispatcher, "execute_runtime", None)
                self.assertIsNotNone(
                    execute_runtime,
                    "runtime adapter arm is missing",
                )
                result = execute_runtime(context, request)
                self.assertIs(type(result), RuntimeEffectResult)
                self.assertIs(result.kind, EffectResultKind.UNCERTAIN)
                self.assertEqual(result.effect_id, request.effect_id)
                self.assertIsNotNone(result.failure)
                assert result.failure is not None
                self.assertEqual(result.failure.code, "runtime.provider-result-unknown")
                rendered = f"{result!r} {result.failure!r}"
                self.assertNotIn("provider-secret-canary", rendered)
                self.assertEqual(dispatches, [])

    def test_runtime_nominal_admission_precedes_virtual_access_and_provider_io(
        self,
    ) -> None:
        dispatches: list[str] = []
        interpreter = RecordingInterpreter("docker")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: interpreter})
        context = context_for(StartNode(NodeTarget("api")))
        request = runtime_effect_request_for_context(context)

        class HostileRequest(RuntimeEffectRequest):
            def __getattribute__(self, name):
                if name in {"runtime_kind", "effect_id", "__class__"}:
                    dispatches.append(name)
                    raise AssertionError("hostile request dispatched")
                return super().__getattribute__(name)

        hostile = object.__new__(HostileRequest)
        for item in fields(RuntimeEffectRequest):
            object.__setattr__(hostile, item.name, getattr(request, item.name))

        class HostileContext(ActivityRealizationContext):
            def __getattribute__(self, name):
                if name in {"activity", "intent_event", "__class__"}:
                    dispatches.append(name)
                    raise AssertionError("hostile context dispatched")
                return super().__getattribute__(name)

        hostile_context = object.__new__(HostileContext)
        for item in fields(ActivityRealizationContext):
            object.__setattr__(
                hostile_context,
                item.name,
                getattr(context, item.name),
            )

        for label, candidate_context, candidate_request in (
            ("request", context, hostile),
            ("context", hostile_context, request),
        ):
            with self.subTest(candidate=label):
                execute_runtime = getattr(dispatcher, "execute_runtime", None)
                self.assertIsNotNone(
                    execute_runtime,
                    "runtime adapter arm is missing",
                )
                dispatches.clear()
                with self.assertRaises(InvalidOperationCommand) as caught:
                    execute_runtime(candidate_context, candidate_request)
                self.assertEqual(
                    str(caught.exception),
                    "runtime dispatch requires exact context and request",
                )
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertEqual(dispatches, [])
                self.assertEqual(interpreter.requests, [])

    def test_runtime_context_and_request_must_be_exactly_congruent_before_io(
        self,
    ) -> None:
        context_a = context_for(StartNode(NodeTarget("api")), run_id="run-a")
        context_b = context_for(StartNode(NodeTarget("api")), run_id="run-b")
        request_a = runtime_effect_request_for_context(context_a)
        request_b = runtime_effect_request_for_context(context_b)

        for label, context, request in (
            ("foreign-request", context_a, request_b),
            ("foreign-context", context_b, request_a),
        ):
            with self.subTest(case=label):
                interpreter = RecordingInterpreter("docker")
                authorizer = RecordingSecretUseAuthorizer()
                dispatcher = RuntimeInterpreterDispatcher(
                    {RuntimeKind.DOCKER: interpreter},
                    secret_use_authorizer=authorizer,
                )
                execute_runtime = getattr(dispatcher, "execute_runtime", None)
                self.assertIsNotNone(
                    execute_runtime,
                    "runtime adapter arm is missing",
                )

                with self.assertRaises(InvalidOperationCommand) as caught:
                    execute_runtime(context, request)

                self.assertEqual(
                    str(caught.exception),
                    "runtime dispatch context and request are incongruent",
                )
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertEqual(authorizer.commands, [])
                self.assertEqual(interpreter.requests, [])

    def test_live_grant_correlation_matches_reconciliation_without_session(
        self,
    ) -> None:
        interpreter = RecordingInterpreter("docker")
        authorizer = RecordingSecretUseAuthorizer()
        dispatcher = RuntimeInterpreterDispatcher(
            {RuntimeKind.DOCKER: interpreter},
            secret_use_authorizer=authorizer,
        )
        execute_runtime = getattr(dispatcher, "execute_runtime", None)
        self.assertIsNotNone(execute_runtime, "runtime adapter arm is missing")
        first = context_for(
            StartNode(NodeTarget("api")),
            secret_delivery=True,
            worker_scopes=(
                PolicyScope.EXECUTION_OPERATE,
                PolicyScope.SECRET_PROVIDER_USE,
            ),
        )
        later_event = replace(first.intent_event, occurred_at="2026-07-22T10:03:00Z")
        later = replace(first, intent_event=later_event)
        worker_b_authority = ExecutionWorkerAuthority(
            "worker-b",
            first.authority.scopes,
        )
        worker_b_fence = ExecutionLeaseFence("worker-b", 2)
        worker_b_request = replace(
            first.request,
            claim=ClaimIdentity(
                "worker-b",
                2,
                "2026-07-22T10:02:00Z",
                "2026-07-22T10:30:00Z",
            ),
        )
        worker_b = replace(
            first,
            request=worker_b_request,
            authority=worker_b_authority,
            fence=worker_b_fence,
            intent_event=replace(
                first.intent_event,
                occurred_at="2026-07-22T10:04:00Z",
            ),
        )

        for context in (first, later, worker_b):
            request = runtime_effect_request_for_context(context)
            execute_runtime(context, request)

        self.assertEqual(len(authorizer.commands), 3)
        first_command, later_command, worker_b_command = authorizer.commands
        self.assertEqual(first_command.correlation_id, later_command.correlation_id)
        self.assertNotEqual(
            first_command.requested_at,
            later_command.requested_at,
        )
        self.assertNotEqual(
            first_command.correlation_id,
            worker_b_command.correlation_id,
        )
        for command in authorizer.commands:
            self.assertEqual(command.workspace_id, "workspace-a")
            self.assertEqual(command.operation_id, "request-a")
            self.assertEqual(command.run_id, "run-a")
            self.assertEqual(command.activity_id, "activity-a")
            self.assertEqual(command.effect_id, "event-intent")
            self.assertIsNone(command.session_id)
            self.assertEqual(
                command.correlation_id,
                secret_use_correlation_for(
                    workspace_id=command.workspace_id,
                    reference=command.reference,
                    intent=command.intent,
                    actor_subject=command.actor_subject,
                    operation_id=command.operation_id,
                    run_id=command.run_id,
                    activity_id=command.activity_id,
                    effect_id=command.effect_id,
                ),
            )

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

    def test_activity_dispatcher_routes_runtime_operation_only_to_runtime(self) -> None:
        ingress = RecordingActivityAdapter(ActivityExecutionOutcome.succeeded())
        runtime_result = RuntimeEffectResult.succeeded(
            "event-intent",
            evidence={"adapter": "runtime"},
        )
        runtime = RecordingActivityAdapter(
            ActivityExecutionOutcome.succeeded(),
            runtime_result,
        )
        dispatcher = ActivityExecutionDispatcher(runtime=runtime, ingress=ingress)
        context = context_for(StartRuntime(RuntimeTarget("runtime-a")))
        request = runtime_effect_request_for_context(context)
        execute_runtime = getattr(dispatcher, "execute_runtime", None)
        self.assertIsNotNone(execute_runtime, "runtime adapter arm is missing")

        result = execute_runtime(context, request)

        self.assertIs(result, runtime_result)
        self.assertEqual(runtime.contexts, [context])
        self.assertEqual(runtime.runtime_requests, [request])
        self.assertIs(runtime.runtime_requests[0], request)
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

        result = self.execute_runtime(dispatcher, context)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertIs(result.kind, EffectResultKind.SUCCEEDED)
        self.assertEqual(result.evidence, {"interpreter": "docker"})
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

        result = self.execute_runtime(dispatcher, context)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertIs(result.kind, EffectResultKind.SUCCEEDED)
        self.assertEqual(result.evidence, {"interpreter": "docker"})
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

        result = self.execute_runtime(dispatcher, context)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertEqual(result.evidence, {"interpreter": "dry-run"})
        self.assertEqual(len(dry_run.requests), 1)
        self.assertEqual(dry_run.requests[0].runtime_kind, RuntimeKind.DRY_RUN)

    def test_missing_interpreter_is_explicit_unsupported_without_attempt(self) -> None:
        docker = RecordingInterpreter("docker")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: docker})
        context = context_for(
            StartRuntime(RuntimeTarget("runtime-a")),
            desired_kind=RuntimeKind.AWS,
        )

        result = self.execute_runtime(dispatcher, context)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertIs(result.kind, EffectResultKind.UNSUPPORTED)
        self.assertIsNotNone(result.failure)
        assert result.failure is not None
        self.assertEqual(result.failure.code, "runtime.interpreter-missing")
        self.assertEqual(
            result.failure.details,
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

        result = self.execute_runtime(dispatcher, context)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertIs(result.kind, EffectResultKind.SUCCEEDED)
        self.assertEqual(
            result.evidence,
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

        result = self.execute_runtime(dispatcher, context)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertIs(result.kind, EffectResultKind.UNSUPPORTED)
        self.assertIsNotNone(result.failure)
        assert result.failure is not None
        self.assertEqual(result.failure.code, "runtime.authority-missing")
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

        result = self.execute_runtime(dispatcher, context)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertIs(result.kind, EffectResultKind.UNSUPPORTED)
        self.assertIsNotNone(result.failure)
        assert result.failure is not None
        self.assertEqual(
            result.failure.code,
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

        with self.assertRaises(InvalidOperationCommand) as caught:
            runtime_effect_request_for_context(context)

        self.assertEqual(
            str(caught.exception),
            "runtime effect node target is missing",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(docker.requests, [])

    def test_runtime_result_failure_is_preserved_as_exact_runtime_result(self) -> None:
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
        context = context_for(StartNode(NodeTarget("api")))

        result = self.execute_runtime(dispatcher, context)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertIs(result.kind, EffectResultKind.FAILED)
        self.assertIsNotNone(result.failure)
        assert result.failure is not None
        self.assertEqual(result.failure.code, "docker.container-failed")
        self.assertEqual(result.failure.details, {"container": "api"})

    def test_runtime_result_effect_id_mismatch_becomes_uncertain(self) -> None:
        interpreter = RecordingInterpreter(
            "docker",
            RuntimeEffectResult.succeeded("different-effect"),
        )
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: interpreter})
        context = context_for(StartNode(NodeTarget("api")))
        request = runtime_effect_request_for_context(context)

        result = self.execute_runtime(dispatcher, context, request)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertIs(result.kind, EffectResultKind.UNCERTAIN)
        self.assertEqual(result.effect_id, request.effect_id)
        self.assertIsNotNone(result.failure)
        assert result.failure is not None
        self.assertEqual(result.failure.code, "runtime.provider-result-unknown")

    def test_runtime_endpoint_observations_remain_exact_raw_result_values(self) -> None:
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
        context = context_for(StartNode(NodeTarget("api")))

        result = self.execute_runtime(dispatcher, context)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertIs(result.kind, EffectResultKind.SUCCEEDED)
        self.assertEqual(len(result.observations), 1)
        observation = result.observations[0]
        self.assertIs(type(observation), RuntimeEndpointObservation)
        self.assertEqual(observation.subject_id, "api")
        self.assertEqual(observation.graph_id, "graph-desired")
        self.assertIs(observation.context, EndpointContext.RUNTIME_PRIVATE)

    def test_maximum_runtime_endpoint_shape_remains_exact_raw_result_value(self) -> None:
        subject_id = _subject_for_bridge_evidence_size(BRIDGE_EVIDENCE_MAX_BYTES)
        endpoint = RuntimeEndpointObservation(
            subject_id,
            "http",
            "graph-desired",
            Protocol.HTTP,
            EndpointContext.RUNTIME_PRIVATE,
            LiteralEndpointMaterial("http://api-http:8000"),
        )
        self.assertLessEqual(len(subject_id), ENDPOINT_TEXT_MAX)
        self.assertEqual(
            _bridge_evidence_size(subject_id=subject_id),
            BRIDGE_EVIDENCE_MAX_BYTES,
        )
        interpreter = RecordingInterpreter(
            "docker",
            RuntimeEffectResult.succeeded(
                "event-intent",
                observations=(endpoint,),
            ),
        )
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: interpreter})
        context = context_for(StartNode(NodeTarget("api")))

        result = self.execute_runtime(dispatcher, context)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertIs(result.kind, EffectResultKind.SUCCEEDED)
        self.assertEqual(result.observations, (endpoint,))
        self.assertIs(result.observations[0], endpoint)

    def test_secret_use_is_authorized_before_runtime_interpreter_io(self) -> None:
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
        result = self.execute_runtime(dispatcher, context)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertIs(result.kind, EffectResultKind.SUCCEEDED)
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
                    session_id=None,
                    run_id="run-a",
                    activity_id="activity-a",
                    effect_id="event-intent",
                ),
            ),
        )

    def test_malformed_retained_run_fails_at_record_boundary_before_dispatch(
        self,
    ) -> None:
        interpreter = RecordingInterpreter("docker")
        authorizer = RecordingSecretUseAuthorizer()
        RuntimeInterpreterDispatcher(
            {RuntimeKind.DOCKER: interpreter},
            secret_use_authorizer=authorizer,
        )

        with self.assertRaises(OperationsRecordError) as captured:
            context_for(
                StartNode(NodeTarget("api")),
                run_id="retained/run-canary",
                secret_delivery=True,
                worker_scopes=(
                    PolicyScope.EXECUTION_OPERATE,
                    PolicyScope.SECRET_PROVIDER_USE,
                ),
            )

        error = captured.exception
        self.assertIs(type(error), OperationsRecordError)
        self.assertEqual(str(error), "run_id is malformed")
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertEqual(authorizer.commands, [])
        self.assertEqual(interpreter.requests, [])
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        self.assertNotIn("retained/run-canary", rendered)

    def test_missing_secret_authorizer_fails_before_runtime_io(self) -> None:
        interpreter = RecordingInterpreter("docker")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: interpreter})

        context = context_for(
            StartNode(NodeTarget("api")),
            secret_delivery=True,
        )
        result = self.execute_runtime(dispatcher, context)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertIs(result.kind, EffectResultKind.UNSUPPORTED)
        assert result.failure is not None
        self.assertEqual(
            result.failure.code,
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

        context = context_for(
            StartNode(NodeTarget("api")),
            secret_delivery=True,
        )
        result = self.execute_runtime(dispatcher, context)

        self.assertIs(type(result), RuntimeEffectResult)
        self.assertIs(result.kind, EffectResultKind.UNSUPPORTED)
        assert result.failure is not None
        self.assertEqual(result.failure.code, "secret.use-not-authorized")
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

        self.execute_runtime(dispatcher, context)
        self.execute_runtime(dispatcher, context)

        self.assertEqual(len(authorizer.commands), 2)
        self.assertEqual(
            authorizer.commands[0].correlation_id,
            authorizer.commands[1].correlation_id,
        )
        self.assertIsNone(authorizer.commands[0].session_id)
        self.assertIsNone(authorizer.commands[1].session_id)


def context_for(
    operation,
    *,
    run_id: str = "run-a",
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
            ClaimIdentity("worker-a", 1, "2026-07-22T10:01:00Z", "2026-07-22T10:30:00Z"),
        ),
        run=ActivityRunRecord(
            run_id,
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
        fence=ExecutionLeaseFence("worker-a", 1),
        runtime_authorities=runtime_authorities,
        intent_event=ActivityEventRecord(
            "event-intent",
            run_id,
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
