"""Durable execution coordinator service without runtime-specific effects."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Protocol

from control_plane_kit_core.operations import (
    EffectAttemptIdentity,
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
    RunId,
)
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
    AddSocketConnection,
    AllocatePublicIngress,
    PlannedActivity,
    RemovePublicIngress,
    RemoveSocketConnection,
    SwitchSocketConnection,
)
from control_plane_kit_core.planning.saga import (
    ExecutionSchedule,
    SagaJournalProjection,
    derive_schedule,
    project_activity_journal,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_fingerprint,
    runtime_effect_request_for_intent,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectFailure,
    RuntimeEffectRequest,
    RuntimeEffectResult,
)
from control_plane_kit_core.secrets import (
    SecretResolutionGrant,
)
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    EffectAttemptFoldNotFound,
    EffectAttemptFoldResult,
    ExistingFold,
    FoldEffectAttempt,
    NewlyFolded,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import (
    EffectAttemptFoldService,
)
from control_plane_kit_operations.effect_attempt_reconciliation import (
    EffectAttemptReconciliationConflict,
    EffectAttemptReconciliationDenied,
    EffectAttemptReconciliationNotFound,
    ReconcileEffectAttempt,
)
from control_plane_kit_operations.effect_attempt_start import (
    EffectAttemptStartConflict,
    EffectAttemptStartDenied,
    EffectAttemptStartNotFound,
    EffectAttemptStartResult,
    ExistingAttempt,
    NewlyStarted,
    StartEffectAttempt,
)
from control_plane_kit_operations.effect_attempt_start_interpreter import (
    EffectAttemptStartService,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_outcome_evidence import (
    ExecutionEffectOutcome,
    effect_outcome_failure,
    effect_outcome_transition,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import (
    CompleteActivityRun,
    ExecutionWorkerAuthority,
    FailActivityRun,
    RunLifecycleCommandService,
    RunLifecycleConflict,
)
from control_plane_kit_operations.ingress_authorities import (
    CloudflareOwnedIngressResource,
    GeneratedIngressSecretReference,
    RegisteredIngressAuthority,
)
from control_plane_kit_operations.products import (
    RegisteredImagePullAuthority,
    RegisteredProduct,
)
from control_plane_kit_operations.runtime_authorities import (
    RegisteredRuntimeAuthority,
    RegisteredRuntimeAuthorityDelivery,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    SecretProviderRegistrationError,
    SecretUseResolutionAuthorizer,
    secret_use_correlation_for,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    BoundedEvidence,
    CoordinatorStatus,
    ExecutionCommandReceiptRecord,
    ExecutionCommandReceiptStatus,
    ExecutionCommandResultRecord,
    ExecutionRequestRecord,
    FailureEvidence,
    ObservationRecord,
    OperationsRecordError,
    RealizedGraphProjectionRecord,
    execution_command_intent_fingerprint,
)
from control_plane_kit_operations.workflows import IdempotencyKey, InvalidOperationCommand


class ExecutionCoordinatorError(RuntimeError):
    """Base error for operations-owned coordinator execution."""


class ExecutionCoordinatorNotFound(ExecutionCoordinatorError):
    """Raised when durable coordinator truth is missing."""


class ExecutionCoordinatorConflict(ExecutionCoordinatorError):
    """Raised when durable state rejects coordinator progress."""


class ExecutionCoordinatorDenied(ExecutionCoordinatorError):
    """Raised when worker authority is insufficient."""


@dataclass(frozen=True)
class ExecuteActivityRun:
    """Advance one claimed, running activity run by at most max_effects steps."""

    run_id: str
    authority: ExecutionWorkerAuthority
    fence: ExecutionLeaseFence
    idempotency_key: IdempotencyKey
    max_effects: int = 1

    def __post_init__(self) -> None:
        _require_run_id(self.run_id)
        if not isinstance(self.authority, ExecutionWorkerAuthority):
            raise InvalidOperationCommand("authority must be ExecutionWorkerAuthority")
        if not isinstance(self.fence, ExecutionLeaseFence):
            raise InvalidOperationCommand("fence must be ExecutionLeaseFence")
        if self.authority.worker_id != self.fence.worker_id:
            raise InvalidOperationCommand("authority and fence must agree")
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")
        if type(self.max_effects) is not int or self.max_effects < 1:
            raise InvalidOperationCommand("max_effects must be a positive integer")


@dataclass(frozen=True)
class ActivityRealizationContext:
    """Pinned durable material handed to an execution adapter after intent."""

    activity: PlannedActivity
    request: ExecutionRequestRecord
    run: ActivityRunRecord
    plan_record: ActivityPlanRecord
    base_graph: RealizedGraphProjectionRecord
    desired_graph: RealizedGraphProjectionRecord
    registered_products: tuple[RegisteredProduct, ...]
    authority: ExecutionWorkerAuthority
    fence: ExecutionLeaseFence
    intent_event: ActivityEventRecord
    image_pull_authorities: tuple[RegisteredImagePullAuthority, ...] = ()
    runtime_authorities: tuple[RegisteredRuntimeAuthority, ...] = ()
    runtime_authority_deliveries: tuple[RegisteredRuntimeAuthorityDelivery, ...] = ()
    ingress_authorities: tuple[RegisteredIngressAuthority, ...] = ()
    ingress_resources: tuple[CloudflareOwnedIngressResource, ...] = ()
    generated_ingress_secrets: tuple[GeneratedIngressSecretReference, ...] = ()

    @property
    def plan(self) -> ActivityPlan:
        return self.plan_record.plan

    def __post_init__(self) -> None:
        if not isinstance(self.activity, PlannedActivity):
            raise InvalidOperationCommand("realization activity must be PlannedActivity")
        if not isinstance(self.request, ExecutionRequestRecord):
            raise InvalidOperationCommand("realization request must be ExecutionRequestRecord")
        if not isinstance(self.run, ActivityRunRecord):
            raise InvalidOperationCommand("realization run must be ActivityRunRecord")
        if not isinstance(self.plan_record, ActivityPlanRecord):
            raise InvalidOperationCommand("realization plan must be ActivityPlanRecord")
        if not isinstance(self.base_graph, RealizedGraphProjectionRecord):
            raise InvalidOperationCommand(
                "realization base graph must be RealizedGraphProjectionRecord"
            )
        if not isinstance(self.desired_graph, RealizedGraphProjectionRecord):
            raise InvalidOperationCommand(
                "realization desired graph must be RealizedGraphProjectionRecord"
            )
        products = tuple(self.registered_products)
        if not all(isinstance(value, RegisteredProduct) for value in products):
            raise InvalidOperationCommand("realization products must be RegisteredProduct values")
        object.__setattr__(self, "registered_products", products)
        pull_authorities = tuple(self.image_pull_authorities)
        if not all(
            isinstance(value, RegisteredImagePullAuthority)
            for value in pull_authorities
        ):
            raise InvalidOperationCommand(
                "realization image pull authorities must be RegisteredImagePullAuthority values"
            )
        object.__setattr__(self, "image_pull_authorities", pull_authorities)
        runtime_authorities = tuple(self.runtime_authorities)
        if not all(
            isinstance(value, RegisteredRuntimeAuthority)
            for value in runtime_authorities
        ):
            raise InvalidOperationCommand(
                "realization runtime authorities must be RegisteredRuntimeAuthority values"
            )
        object.__setattr__(self, "runtime_authorities", runtime_authorities)
        runtime_authority_deliveries = tuple(self.runtime_authority_deliveries)
        if not all(
            isinstance(value, RegisteredRuntimeAuthorityDelivery)
            for value in runtime_authority_deliveries
        ):
            raise InvalidOperationCommand(
                "realization runtime authority deliveries must be RegisteredRuntimeAuthorityDelivery values"
            )
        object.__setattr__(
            self,
            "runtime_authority_deliveries",
            runtime_authority_deliveries,
        )
        ingress_authorities = tuple(self.ingress_authorities)
        if not all(
            isinstance(value, RegisteredIngressAuthority)
            for value in ingress_authorities
        ):
            raise InvalidOperationCommand(
                "realization ingress authorities must be RegisteredIngressAuthority values"
            )
        object.__setattr__(self, "ingress_authorities", ingress_authorities)
        ingress_resources = tuple(self.ingress_resources)
        if not all(
            isinstance(value, CloudflareOwnedIngressResource)
            for value in ingress_resources
        ):
            raise InvalidOperationCommand(
                "realization ingress resources must be CloudflareOwnedIngressResource values"
            )
        object.__setattr__(self, "ingress_resources", ingress_resources)
        generated_ingress_secrets = tuple(self.generated_ingress_secrets)
        if not all(
            isinstance(value, GeneratedIngressSecretReference)
            for value in generated_ingress_secrets
        ):
            raise InvalidOperationCommand(
                "realization generated ingress secrets must be GeneratedIngressSecretReference values"
            )
        object.__setattr__(
            self,
            "generated_ingress_secrets",
            generated_ingress_secrets,
        )
        if not isinstance(self.authority, ExecutionWorkerAuthority):
            raise InvalidOperationCommand("realization authority must be ExecutionWorkerAuthority")
        if not isinstance(self.fence, ExecutionLeaseFence):
            raise InvalidOperationCommand("realization fence must be ExecutionLeaseFence")
        if self.authority.worker_id != self.fence.worker_id:
            raise InvalidOperationCommand("realization authority and fence must agree")
        if not isinstance(self.intent_event, ActivityEventRecord):
            raise InvalidOperationCommand("realization intent must be ActivityEventRecord")
        workspace_id = self.request.identity.workspace_id
        if self.run.admission.request_id != self.request.identity.request_id:
            raise InvalidOperationCommand("realization run must belong to request")
        if self.request.claim is None or self.request.claim.fence != self.fence:
            raise InvalidOperationCommand("realization fence must match request claim")
        if self.run.plan_id != self.plan_record.plan_id:
            raise InvalidOperationCommand("realization run must use the pinned plan")
        if self.request.identity.plan_id != self.plan_record.plan_id:
            raise InvalidOperationCommand("realization request must use the pinned plan")
        if self.plan_record.base_graph_id != self.base_graph.source_authored_graph_id:
            raise InvalidOperationCommand("realization base graph source must match plan")
        if (
            self.plan_record.desired_graph_id
            != self.desired_graph.source_authored_graph_id
        ):
            raise InvalidOperationCommand("realization desired graph source must match plan")
        if (
            self.plan_record.base_realized_projection_id is not None
            and self.plan_record.base_realized_projection_id
            != self.base_graph.projection_id
        ):
            raise InvalidOperationCommand("realization base projection must match plan")
        if (
            self.plan_record.desired_realized_projection_id is not None
            and self.plan_record.desired_realized_projection_id
            != self.desired_graph.projection_id
        ):
            raise InvalidOperationCommand("realization desired projection must match plan")
        if self.base_graph.workspace_id != workspace_id:
            raise InvalidOperationCommand("realization base graph must match workspace")
        if self.desired_graph.workspace_id != workspace_id:
            raise InvalidOperationCommand("realization desired graph must match workspace")
        for product in products:
            if product.workspace_id != workspace_id:
                raise InvalidOperationCommand("realization product must match workspace")
        for pull_authority in pull_authorities:
            if pull_authority.workspace_id != workspace_id:
                raise InvalidOperationCommand(
                    "realization image pull authority must match workspace"
                )
        for runtime_authority in runtime_authorities:
            if runtime_authority.workspace_id != workspace_id:
                raise InvalidOperationCommand(
                    "realization runtime authority must match workspace"
                )
        for runtime_authority_delivery in runtime_authority_deliveries:
            if runtime_authority_delivery.workspace_id != workspace_id:
                raise InvalidOperationCommand(
                    "realization runtime authority delivery must match workspace"
                )
        for ingress_authority in ingress_authorities:
            if ingress_authority.workspace_id != workspace_id:
                raise InvalidOperationCommand(
                    "realization ingress authority must match workspace"
                )
        for ingress_resource in ingress_resources:
            if ingress_resource.workspace_id != workspace_id:
                raise InvalidOperationCommand(
                    "realization ingress resource must match workspace"
                )
        for generated_ingress_secret in generated_ingress_secrets:
            if generated_ingress_secret.workspace_id != workspace_id:
                raise InvalidOperationCommand(
                    "realization generated ingress secret must match workspace"
                )
        if self.intent_event.run_id != self.run.run_id:
            raise InvalidOperationCommand("realization intent must match run")
        match self.intent_event.kind:
            case candidate if candidate is ActivityEventKind.STEP_STARTED:
                pass
            case candidate if candidate is ActivityEventKind.STEP_COMPENSATION_STARTED:
                pass
            case _:
                raise InvalidOperationCommand(
                    "realization intent must be step_started or "
                    "step_compensation_started"
                )
        if self.intent_event.activity_id != self.activity.activity_id.value:
            raise InvalidOperationCommand("realization intent must match activity")


@dataclass(frozen=True)
class ActivityExecutionOutcome:
    """Proof-adapter outcome expressed with the core effect result vocabulary."""

    kind: EffectResultKind
    evidence: BoundedEvidence = field(default_factory=BoundedEvidence)
    failure: FailureEvidence | None = None
    observations: tuple[ObservationRecord, ...] = ()

    @classmethod
    def succeeded(
        cls,
        evidence: BoundedEvidence | None = None,
        observations: tuple[ObservationRecord, ...] = (),
    ) -> "ActivityExecutionOutcome":
        return cls(
            EffectResultKind.SUCCEEDED,
            evidence or BoundedEvidence(),
            observations=observations,
        )

    @classmethod
    def failed(cls, failure: FailureEvidence) -> "ActivityExecutionOutcome":
        return cls(EffectResultKind.FAILED, BoundedEvidence(), failure)

    @classmethod
    def unsupported(cls, failure: FailureEvidence) -> "ActivityExecutionOutcome":
        return cls(EffectResultKind.UNSUPPORTED, BoundedEvidence(), failure)

    @classmethod
    def uncertain(cls, failure: FailureEvidence) -> "ActivityExecutionOutcome":
        return cls(EffectResultKind.UNCERTAIN, BoundedEvidence(), failure)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EffectResultKind):
            raise InvalidOperationCommand("outcome kind must be EffectResultKind")
        if not isinstance(self.evidence, BoundedEvidence):
            raise InvalidOperationCommand("outcome evidence must be BoundedEvidence")
        if self.failure is not None and not isinstance(self.failure, FailureEvidence):
            raise InvalidOperationCommand("outcome failure must be FailureEvidence")
        observations = tuple(self.observations)
        if not all(isinstance(value, ObservationRecord) for value in observations):
            raise InvalidOperationCommand("outcome observations must be ObservationRecord values")
        object.__setattr__(self, "observations", observations)
        if self.kind in {
            EffectResultKind.FAILED,
            EffectResultKind.UNSUPPORTED,
            EffectResultKind.UNCERTAIN,
        } and self.failure is None:
            raise InvalidOperationCommand("non-success outcomes require failure evidence")
        if self.kind is EffectResultKind.SUCCEEDED and self.failure is not None:
            raise InvalidOperationCommand("successful outcomes must not carry failure")
        if self.kind not in {
            EffectResultKind.SUCCEEDED,
            EffectResultKind.FAILED,
            EffectResultKind.UNSUPPORTED,
            EffectResultKind.UNCERTAIN,
        }:
            raise InvalidOperationCommand("adapter outcome is not executable")


class ActivityExecutionAdapter(Protocol):
    """Effect-proof adapter called only after durable intent commits."""

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome: ...

    def execute_runtime(
        self,
        context: ActivityRealizationContext,
        request: RuntimeEffectRequest,
    ) -> RuntimeEffectResult: ...


@dataclass(frozen=True)
class ActivityExecutionDispatcher:
    """Dispatch a planned activity to its closed operations-owned effect family."""

    runtime: ActivityExecutionAdapter
    ingress: ActivityExecutionAdapter | None = None

    def __post_init__(self) -> None:
        if not hasattr(self.runtime, "execute"):
            raise InvalidOperationCommand("runtime activity adapter must expose execute")
        if self.ingress is not None and not hasattr(self.ingress, "execute"):
            raise InvalidOperationCommand("ingress activity adapter must expose execute")

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome:
        if isinstance(context, ActivityRealizationContext):
            if isinstance(
                context.activity.operation,
                (AllocatePublicIngress, RemovePublicIngress),
            ):
                if self.ingress is None:
                    return ActivityExecutionOutcome.unsupported(
                        FailureEvidence(
                            FailureCategory.OPERATOR_REVIEW,
                            "ingress.interpreter-missing",
                            "no ingress activity adapter is configured",
                            BoundedEvidence.from_mapping(
                                {
                                    "activity_id": context.activity.activity_id.value,
                                    "operation": type(
                                        context.activity.operation
                                    ).__name__,
                                }
                            ),
                        )
                    )
                return self.ingress.execute(context)
            match context.activity.operation:
                case (
                    AddSocketConnection()
                    | SwitchSocketConnection()
                    | RemoveSocketConnection()
                ):
                    return self.runtime.execute(context)
                case _:
                    pass
        raise InvalidOperationCommand(
            "runtime activities require the runtime dispatch arm"
        )

    def execute_runtime(
        self,
        context: ActivityRealizationContext,
        request: RuntimeEffectRequest,
    ) -> RuntimeEffectResult:
        return self.runtime.execute_runtime(context, request)


class RuntimeEffectInterpreter(Protocol):
    """Injected runtime interpreter over the pure core effect boundary."""

    def execute(
        self,
        request: RuntimeEffectRequest,
    ) -> RuntimeEffectResult: ...


class RuntimeAuthorityAwareInterpreter(Protocol):
    """Runtime interpreter that can consume admitted authority material."""

    def execute_with_authority(
        self,
        request: RuntimeEffectRequest,
        authority: RegisteredRuntimeAuthority,
    ) -> RuntimeEffectResult: ...


@dataclass(frozen=True)
class RuntimeInterpreterDispatcher:
    """Operations-owned adapter that dispatches pinned work by runtime kind."""

    interpreters: Mapping[RuntimeKind, RuntimeEffectInterpreter]
    secret_use_authorizer: SecretUseResolutionAuthorizer | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.interpreters, Mapping):
            raise InvalidOperationCommand("runtime interpreters must be a mapping")
        normalized: dict[RuntimeKind, RuntimeEffectInterpreter] = {}
        for key, interpreter in self.interpreters.items():
            if not isinstance(key, RuntimeKind):
                raise InvalidOperationCommand("runtime interpreter keys must be RuntimeKind")
            if not hasattr(interpreter, "execute"):
                raise InvalidOperationCommand("runtime interpreter must expose execute")
            normalized[key] = interpreter
        object.__setattr__(self, "interpreters", normalized)
        if (
            self.secret_use_authorizer is not None
            and not hasattr(self.secret_use_authorizer, "authorize_resolution")
        ):
            raise InvalidOperationCommand(
                "secret use authorizer must expose authorize_resolution"
            )

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome:
        if (
            isinstance(context, ActivityRealizationContext)
            and _is_socket_connection_operation(context.activity)
        ):
            return _socket_connection_outcome(context)
        raise InvalidOperationCommand(
            "runtime activities require the runtime dispatch arm"
        )

    def execute_runtime(
        self,
        context: ActivityRealizationContext,
        request: RuntimeEffectRequest,
    ) -> RuntimeEffectResult:
        invalid_message = None
        if (
            type(context) is not ActivityRealizationContext
            or type(request) is not RuntimeEffectRequest
        ):
            invalid_message = "runtime dispatch requires exact context and request"
        else:
            source = request.source
            if (
                request.effect_id != context.intent_event.event_id
                or source.workspace_id != context.request.identity.workspace_id
                or source.request_id != context.request.identity.request_id
                or source.run_id.value != context.run.run_id
                or source.plan_id != context.plan_record.plan_id
                or source.base_graph_id != context.plan_record.base_graph_id
                or source.desired_graph_id != context.plan_record.desired_graph_id
                or source.intent_event_id != context.intent_event.event_id
                or request.activity_id != context.activity.activity_id
                or request.operation != context.activity.operation
            ):
                invalid_message = (
                    "runtime dispatch context and request are incongruent"
                )
        if invalid_message is not None:
            raise InvalidOperationCommand(invalid_message)
        runtime_kind = request.runtime_kind
        interpreter = self.interpreters.get(runtime_kind)
        if interpreter is None:
            return _unsupported_runtime_result(
                request,
                "runtime.interpreter-missing",
                f"no runtime interpreter is configured for {runtime_kind.value!r}",
                runtime_kind=runtime_kind,
            )
        authority = _runtime_authority_for_request(context, request)
        if authority is _MISSING_RUNTIME_AUTHORITY:
            return _unsupported_runtime_result(
                request,
                "runtime.authority-missing",
                "runtime authority reference has no active registration",
                runtime_kind=runtime_kind,
            )
        try:
            request = self._authorize_secret_resolutions(
                context,
                request,
                authority=authority,
            )
        except SecretProviderRegistrationError:
            return _unsupported_runtime_result(
                request,
                "secret.use-not-authorized",
                "runtime secret use was not authorized",
                runtime_kind=runtime_kind,
            )
        except InvalidOperationCommand:
            return _unsupported_runtime_result(
                request,
                "secret.resolution-authorizer-invalid",
                "runtime secret authorization could not be established",
                runtime_kind=runtime_kind,
            )
        result = None
        try:
            if authority is None:
                result = interpreter.execute(request)
            else:
                execute_with_authority = getattr(
                    interpreter,
                    "execute_with_authority",
                    None,
                )
                if execute_with_authority is None:
                    return _unsupported_runtime_result(
                        request,
                        "runtime.authority-interpreter-unsupported",
                        "runtime interpreter cannot consume registered runtime authority",
                        runtime_kind=runtime_kind,
                    )
                result = execute_with_authority(request, authority)
        except Exception:  # noqa: BLE001 - provider faults become direct uncertainty.
            pass
        if (
            type(result) is not RuntimeEffectResult
            or result.effect_id != request.effect_id
        ):
            return _uncertain_runtime_result(request)
        return result

    def _authorize_secret_resolutions(
        self,
        context: ActivityRealizationContext,
        request: RuntimeEffectRequest,
        *,
        authority: RegisteredRuntimeAuthority | None,
    ) -> RuntimeEffectRequest:
        required_uses = required_secret_uses_for_runtime_effect(request, authority)
        if not required_uses:
            return request
        if self.secret_use_authorizer is None:
            raise InvalidOperationCommand(
                "runtime secret resolution requires an operations authorizer"
            )
        grants: list[SecretResolutionGrant] = []
        for reference, intent in required_uses:
            grant = self.secret_use_authorizer.authorize_resolution(
                AuthorizeSecretUse(
                    workspace_id=request.source.workspace_id,
                    reference=reference,
                    intent=intent,
                    actor_subject=context.authority.worker_id,
                    correlation_id=secret_use_correlation_for(
                        workspace_id=request.source.workspace_id,
                        reference=reference,
                        intent=intent,
                        actor_subject=context.authority.worker_id,
                        operation_id=request.source.request_id,
                        run_id=request.source.run_id.value,
                        activity_id=request.activity_id.value,
                        effect_id=request.effect_id,
                    ),
                    requested_at=context.intent_event.occurred_at,
                    actor_scopes=context.authority.scopes,
                    operation_id=request.source.request_id,
                    run_id=request.source.run_id.value,
                    activity_id=request.activity_id.value,
                    effect_id=request.effect_id,
                )
            )
            if (
                not isinstance(grant, SecretResolutionGrant)
                or grant.workspace_id != request.source.workspace_id
                or grant.effect_id != request.effect_id
                or not grant.permits(reference, intent)
            ):
                raise InvalidOperationCommand(
                    "secret use authorizer returned an invalid resolution grant"
                )
            grants.append(grant)
        return replace(
            request,
            secret_resolution_grants=tuple(grants),
        )


def _is_socket_connection_operation(activity: PlannedActivity) -> bool:
    return isinstance(
        activity.operation,
        (AddSocketConnection, SwitchSocketConnection, RemoveSocketConnection),
    )


def _socket_connection_outcome(
    context: ActivityRealizationContext,
) -> ActivityExecutionOutcome:
    operation = context.activity.operation
    target = getattr(operation, "target", None)
    edge_id = getattr(target, "edge_id", None)
    return ActivityExecutionOutcome.succeeded(
        BoundedEvidence.from_mapping(
            {
                "action": "socket-connection-recorded",
                "operation": type(operation).__name__,
                "edge_id": edge_id,
            }
        )
    )



_MISSING_RUNTIME_AUTHORITY = object()


def _runtime_authority_for_request(
    context: ActivityRealizationContext,
    request: RuntimeEffectRequest,
) -> RegisteredRuntimeAuthority | None | object:
    authority_ref = request.authority_ref
    if authority_ref is None:
        return None
    for authority in context.runtime_authorities:
        if (
            authority.authority_ref == authority_ref
            and authority.runtime_kind is request.runtime_kind
        ):
            return authority
    return _MISSING_RUNTIME_AUTHORITY

@dataclass(frozen=True)
class ExecutionCoordinatorResult:
    """Visible result of one coordinator command."""

    run: ActivityRunRecord
    status: CoordinatorStatus
    effects_attempted: int = 0
    activity_id: str | None = None

    def descriptor(self) -> dict[str, object]:
        return {
            "run_id": self.run.run_id,
            "run_status": self.run.status.value,
            "coordinator_status": self.status.value,
            "effects_attempted": self.effects_attempted,
            "activity_id": self.activity_id,
        }


def _execution_command_fingerprint(command: ExecuteActivityRun) -> str:
    return execution_command_intent_fingerprint(
        run_id=command.run_id,
        worker_id=command.authority.worker_id,
        authority_scopes=command.authority.scopes,
        claim_generation=command.fence.generation,
        max_effects=command.max_effects,
    )


def _command_result_record(
    result: ExecutionCoordinatorResult,
) -> ExecutionCommandResultRecord:
    return ExecutionCommandResultRecord(
        run=result.run,
        status=result.status,
        effects_attempted=result.effects_attempted,
        activity_id=result.activity_id,
    )


def _coordinator_result(
    result: ExecutionCommandResultRecord,
) -> ExecutionCoordinatorResult:
    return ExecutionCoordinatorResult(
        run=result.run,
        status=result.status,
        effects_attempted=result.effects_attempted,
        activity_id=result.activity_id,
    )


class ExecutionCoordinator:
    """Operations-owned durable coordinator over core plan and saga languages."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        lifecycle: RunLifecycleCommandService,
        adapter: ActivityExecutionAdapter,
        start_service: EffectAttemptStartService,
        fold_service: EffectAttemptFoldService,
        reconciliation_service: EffectAttemptReconciliationService,
        clock: Callable[[], str],
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._lifecycle = lifecycle
        self._adapter = adapter
        self._start_service = start_service
        self._fold_service = fold_service
        self._reconciliation_service = reconciliation_service
        self._clock = clock
        self._id_factory = id_factory

    def execute(self, command: ExecuteActivityRun) -> ExecutionCoordinatorResult:
        _require_operate_scope(command.authority)
        replay = self._admit_command(command)
        if replay is not None:
            return replay
        result = self._execute_admitted(command)
        self._complete_command(command, result)
        return result

    def _admit_command(
        self,
        command: ExecuteActivityRun,
    ) -> ExecutionCoordinatorResult | None:
        fingerprint = _execution_command_fingerprint(command)
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.lock_command_idempotency(
                command.run_id,
                command.idempotency_key.value,
            )
            _, run = _locked_request_and_run(stores, command)
            receipt = stores.execution.command_receipt_for_idempotency(
                command.run_id,
                command.idempotency_key.value,
                for_update=True,
            )
            if receipt is not None:
                if receipt.intent_fingerprint != fingerprint:
                    raise ExecutionCoordinatorConflict(
                        "execution command idempotency key conflicts with prior intent"
                    )
                if receipt.status is ExecutionCommandReceiptStatus.COMPLETED:
                    assert receipt.result is not None
                    return _coordinator_result(receipt.result)
                return ExecutionCoordinatorResult(
                    run,
                    CoordinatorStatus.UNCERTAIN,
                )
            stores.execution.add_command_receipt(
                ExecutionCommandReceiptRecord(
                    run_id=command.run_id,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                    worker_id=command.authority.worker_id,
                    authority_scopes=command.authority.scopes,
                    claim_generation=command.fence.generation,
                    max_effects=command.max_effects,
                    admitted_at=self._clock(),
                    initial_run=run,
                )
            )
            unit_of_work.commit()
        return None

    def _complete_command(
        self,
        command: ExecuteActivityRun,
        result: ExecutionCoordinatorResult,
    ) -> None:
        fingerprint = _execution_command_fingerprint(command)
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.lock_command_idempotency(
                command.run_id,
                command.idempotency_key.value,
            )
            completed = stores.execution.complete_command_receipt(
                command.run_id,
                command.idempotency_key.value,
                intent_fingerprint=fingerprint,
                completed_at=self._clock(),
                result=_command_result_record(result),
            )
            if completed is None:
                raise ExecutionCoordinatorConflict(
                    "execution command completion receipt is invalid"
                )
            unit_of_work.commit()

    def _execute_admitted(
        self,
        command: ExecuteActivityRun,
    ) -> ExecutionCoordinatorResult:
        attempted = 0
        current: ExecutionCoordinatorResult | None = None
        for _ in range(command.max_effects):
            context = self._load_context(command)
            current = self._classify_current(context, attempted)
            if current.status not in (
                CoordinatorStatus.PROGRESSED,
                CoordinatorStatus.IN_FLIGHT,
            ):
                return current
            activity = current.activity_id
            if activity is None:
                raise ExecutionCoordinatorConflict("ready activity identity is missing")
            planned = context.plan.activity(ActivityId(activity))
            legacy = False
            match planned.operation:
                case (
                    AllocatePublicIngress()
                    | RemovePublicIngress()
                    | AddSocketConnection()
                    | SwitchSocketConnection()
                    | RemoveSocketConnection()
                ):
                    legacy = True
                case _:
                    pass
            if current.status is CoordinatorStatus.IN_FLIGHT and legacy:
                return current
            if legacy:
                selected_event = self._record_step_event(
                    command,
                    planned,
                    ActivityEventKind.STEP_STARTED,
                    BoundedEvidence.from_mapping({"phase": "intent"}),
                )
                attempted += 1
            else:
                selected_event = None
            service_failure = None
            service_message = None
            started: EffectAttemptStartResult | None = None
            intent = None
            transition = None
            if not legacy:
                intent = _runtime_effect_intent_for_context(context, planned)
                transition = EffectAttemptTransition(
                    EffectAttemptTransitionKind.STARTED,
                    EffectAttemptIdentity(
                        intent.source.run_id,
                        planned.activity_id.value,
                        1,
                    ),
                    request_fingerprint=runtime_effect_intent_fingerprint(intent),
                )
                start_command = StartEffectAttempt(
                    request_id=context.request.identity.request_id,
                    transition=transition,
                    intent=intent,
                    authority=command.authority,
                    fence=command.fence,
                )
                try:
                    started = self._start_service.execute(start_command)
                except EffectAttemptStartNotFound:
                    service_failure = "not-found"
                    service_message = "effect attempt start truth was not found"
                except EffectAttemptStartConflict:
                    service_failure = "conflict"
                    service_message = "effect attempt start truth is invalid"
                except EffectAttemptStartDenied:
                    service_failure = "denied"
                    service_message = "effect attempt start authority is invalid"
                attempted += 1
            selected_conflict = None
            attempt = None
            existing = False
            if not legacy and service_failure is None:
                assert transition is not None
                if type(started) not in (NewlyStarted, ExistingAttempt):
                    selected_conflict = "effect attempt service result is invalid"
                else:
                    attempt = started.attempt
                    invalid_attempt = type(attempt) is not EffectAttemptRecord
                    if not invalid_attempt:
                        try:
                            attempt = EffectAttemptRecord(
                                attempt.state,
                                attempt.original_start_event,
                                attempt.latest_transition_event,
                            )
                        except OperationsRecordError:
                            invalid_attempt = True
                    if invalid_attempt:
                        selected_conflict = "effect attempt service result is invalid"
                    elif (
                        attempt.state.identity != transition.identity
                        or attempt.state.request_fingerprint
                        != transition.request_fingerprint
                        or attempt.state.fence.worker_id != command.fence.worker_id
                        or attempt.state.fence.generation != command.fence.generation
                        or attempt.original_start_event.run_id != command.run_id
                        or attempt.original_start_event.activity_id
                        != planned.activity_id.value
                        or attempt.original_start_event.kind
                        is not ActivityEventKind.STEP_STARTED
                    ):
                        selected_conflict = "effect attempt start result is invalid"
                    else:
                        existing = started.__class__ is ExistingAttempt

            realization = None
            if legacy or (
                service_failure is None
                and selected_conflict is None
                and not existing
            ):
                if selected_event is None:
                    assert attempt is not None
                    selected_event = attempt.original_start_event
                realization = context.realization_context(planned, selected_event)

            if legacy:
                assert realization is not None
                try:
                    legacy_outcome = self._adapter.execute(realization)
                except Exception as error:  # noqa: BLE001 - adapter uncertainty.
                    legacy_outcome = ActivityExecutionOutcome.uncertain(
                        FailureEvidence(
                            FailureCategory.UNCERTAIN,
                            "adapter-result-unknown",
                            "adapter raised before a durable result was recorded",
                            BoundedEvidence.from_mapping(
                                {"exception_type": type(error).__name__}
                            ),
                        )
                    )
                legacy_outcome = self._record_outcome(
                    command,
                    planned,
                    legacy_outcome,
                )
                if legacy_outcome.kind is not EffectResultKind.SUCCEEDED:
                    classified = self._classify_current(
                        self._load_context(command)
                    )
                    if legacy_outcome.kind is EffectResultKind.UNSUPPORTED:
                        status = CoordinatorStatus.UNSUPPORTED
                    elif legacy_outcome.kind is EffectResultKind.UNCERTAIN:
                        status = CoordinatorStatus.UNCERTAIN
                    else:
                        status = CoordinatorStatus.FAILED
                    return ExecutionCoordinatorResult(
                        classified.run,
                        status,
                        attempted,
                        planned.activity_id.value,
                    )
            elif service_failure is None and selected_conflict is None and existing:
                assert attempt is not None
                if attempt.state.recovery_decision is not None:
                    selected_conflict = (
                        "effect attempt recovery requires explicit recovery authority"
                    )
                else:
                    reconciled: EffectAttemptFoldResult | None = None
                    try:
                        reconciled = self._reconciliation_service.execute(
                            ReconcileEffectAttempt(
                                context.request.identity.request_id,
                                attempt.state.identity,
                                command.authority,
                                command.fence,
                            )
                        )
                    except EffectAttemptReconciliationNotFound:
                        service_failure = "not-found"
                        service_message = (
                            "effect attempt reconciliation truth was not found"
                        )
                    except EffectAttemptReconciliationConflict:
                        service_failure = "conflict"
                        service_message = (
                            "effect attempt reconciliation truth is invalid"
                        )
                    except EffectAttemptReconciliationDenied:
                        service_failure = "denied"
                        service_message = (
                            "effect attempt reconciliation authority is invalid"
                        )
                    if service_failure is None:
                        invalid_result = False
                        result_type = type(reconciled)
                        if result_type is NewlyFolded:
                            try:
                                reconciled = NewlyFolded(
                                    reconciled.attempt,
                                    reconciled.outcome_record,
                                )
                            except OperationsRecordError:
                                invalid_result = True
                        elif result_type is ExistingFold:
                            try:
                                reconciled = ExistingFold(
                                    reconciled.attempt,
                                    reconciled.outcome_record,
                                )
                            except OperationsRecordError:
                                invalid_result = True
                        else:
                            invalid_result = True
                        if invalid_result or (
                            reconciled.attempt.state.identity
                            != attempt.state.identity
                            or reconciled.attempt.state.request_fingerprint
                            != attempt.state.request_fingerprint
                        ):
                            selected_conflict = (
                                "effect attempt service result is invalid"
                            )
            elif service_failure is None and selected_conflict is None:
                assert attempt is not None
                assert intent is not None
                assert realization is not None
                request = runtime_effect_request_for_intent(
                    intent,
                    effect_id=attempt.original_start_event.event_id,
                    secret_resolution_grants=(),
                )
                runtime_result = None
                try:
                    runtime_result = self._adapter.execute_runtime(
                        realization,
                        request,
                    )
                except Exception:  # noqa: BLE001 - provider faults become uncertainty.
                    pass
                if (
                    type(runtime_result) is not RuntimeEffectResult
                    or runtime_result.effect_id != request.effect_id
                ):
                    runtime_result = _uncertain_runtime_result(request)
                outcome = ExecutionEffectOutcome(
                    attempt.state.identity,
                    attempt.state.request_fingerprint,
                    runtime_result,
                )
                fold_command = FoldEffectAttempt(
                    request_id=context.request.identity.request_id,
                    transition=effect_outcome_transition(outcome),
                    authority=command.authority,
                    fence=command.fence,
                    failure=effect_outcome_failure(outcome),
                    outcome=outcome,
                )
                folded: EffectAttemptFoldResult | None = None
                try:
                    folded = self._fold_service.execute(fold_command)
                except EffectAttemptFoldNotFound:
                    service_failure = "not-found"
                    service_message = "effect attempt fold truth was not found"
                except EffectAttemptFoldConflict:
                    service_failure = "conflict"
                    service_message = "effect attempt fold truth is invalid"
                except EffectAttemptFoldDenied:
                    service_failure = "denied"
                    service_message = "effect attempt fold authority is invalid"
                if service_failure is None:
                    invalid_result = False
                    result_type = type(folded)
                    if result_type is NewlyFolded:
                        try:
                            folded = NewlyFolded(
                                folded.attempt,
                                folded.outcome_record,
                            )
                        except OperationsRecordError:
                            invalid_result = True
                    elif result_type is ExistingFold:
                        try:
                            folded = ExistingFold(
                                folded.attempt,
                                folded.outcome_record,
                            )
                        except OperationsRecordError:
                            invalid_result = True
                    else:
                        invalid_result = True
                    if invalid_result or (
                        folded.attempt.state.identity
                        != attempt.state.identity
                        or folded.attempt.state.request_fingerprint
                        != attempt.state.request_fingerprint
                    ):
                        selected_conflict = "effect attempt service result is invalid"

            if service_failure == "not-found":
                raise ExecutionCoordinatorNotFound(service_message) from None
            if service_failure == "denied":
                raise ExecutionCoordinatorDenied(service_message) from None
            if service_failure == "conflict":
                selected_conflict = service_message
            if selected_conflict is not None:
                raise ExecutionCoordinatorConflict(selected_conflict)
        context = self._load_context(command)
        current = self._classify_current(context, attempted)
        return ExecutionCoordinatorResult(
            current.run,
            CoordinatorStatus.PROGRESSED
            if current.status is CoordinatorStatus.PROGRESSED
            else current.status,
            attempted,
            current.activity_id,
        )

    def _classify_current(
        self,
        context: "_CoordinatorContext",
        effects_attempted: int = 0,
    ) -> ExecutionCoordinatorResult:
        run = context.run
        if run.status is ActivityRunStatus.CLAIMED:
            raise ExecutionCoordinatorConflict("activity run must be started")
        if run.status is ActivityRunStatus.PAUSED:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.BLOCKED,
                effects_attempted,
            )
        if run.status is ActivityRunStatus.SUCCEEDED:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.COMPLETED,
                effects_attempted,
            )
        if run.status is ActivityRunStatus.FAILED:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.FAILED,
                effects_attempted,
            )
        if run.status in {ActivityRunStatus.CANCELLED, ActivityRunStatus.COMPENSATING}:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.BLOCKED,
                effects_attempted,
            )
        if run.status is not ActivityRunStatus.RUNNING:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.BLOCKED,
                effects_attempted,
            )

        if context.projection.uncertain:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.UNCERTAIN,
                effects_attempted,
                activity_id=context.projection.uncertain[0].activity_id,
            )
        if context.schedule.failed:
            failed_activity_ids = [
                value.activity_id.value for value in context.schedule.failed
            ]
            failure_details = (
                {"activity_id": failed_activity_ids[0]}
                if failed_activity_ids[1:] == []
                else {}
            )
            failure = FailureEvidence(
                FailureCategory.TERMINAL,
                "activity-step-failed",
                "one or more planned activities failed",
                BoundedEvidence.from_mapping(failure_details),
            )
            try:
                result = self._lifecycle.execute(
                    FailActivityRun(
                        run.run_id,
                        context.authority,
                        context.fence,
                        IdempotencyKey(f"coordinator:{run.run_id}:fail"),
                        failure,
                    )
                )
                run = result.run
            except RunLifecycleConflict:
                run = self._fresh_run(run.run_id)
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.FAILED,
                effects_attempted,
            )
        if context.schedule.running:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.IN_FLIGHT,
                effects_attempted,
                activity_id=context.schedule.running[0].activity_id.value,
            )
        if context.schedule.successful:
            result = self._lifecycle.execute(
                CompleteActivityRun(
                    run.run_id,
                    context.authority,
                    context.fence,
                    IdempotencyKey(f"coordinator:{run.run_id}:complete"),
                    BoundedEvidence.from_mapping({"result": "all-activities-succeeded"}),
                )
            )
            return ExecutionCoordinatorResult(
                result.run,
                CoordinatorStatus.COMPLETED,
                effects_attempted,
            )
        if context.schedule.ready:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.PROGRESSED,
                effects_attempted,
                activity_id=context.schedule.ready[0].activity_id.value,
            )
        if context.schedule.blocked or context.schedule.waiting:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.BLOCKED,
                effects_attempted,
            )
        return ExecutionCoordinatorResult(
            run,
            CoordinatorStatus.BLOCKED,
            effects_attempted,
        )

    def _record_step_event(
        self,
        command: ExecuteActivityRun,
        activity: PlannedActivity,
        kind: ActivityEventKind,
        evidence: BoundedEvidence | None = None,
        failure: FailureEvidence | None = None,
        observations: tuple[ObservationRecord, ...] = (),
    ) -> ActivityEventRecord:
        now = self._clock()
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            request, run = _locked_request_and_run(stores, command)
            if run.status is not ActivityRunStatus.RUNNING:
                raise ExecutionCoordinatorConflict("run is not executable")
            _validate_observations(
                observations,
                workspace_id=request.identity.workspace_id,
            )
            event = stores.execution.add_event(
                ActivityEventRecord(
                    event_id=self._id_factory(),
                    run_id=run.run_id,
                    ordinal=stores.execution.next_event_ordinal(run.run_id),
                    kind=kind,
                    occurred_at=now,
                    activity_id=activity.activity_id.value,
                    evidence=_step_evidence(
                        command.fence,
                        evidence or BoundedEvidence(),
                    ),
                    failure=failure,
                )
            )
            for observation in observations:
                stores.observed_state.put(observation)
            unit_of_work.commit()
            return event

    def _record_outcome(
        self,
        command: ExecuteActivityRun,
        activity: PlannedActivity,
        outcome: ActivityExecutionOutcome,
    ) -> ActivityExecutionOutcome:
        now = self._clock()
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            request, run = _locked_request_and_run(stores, command)
            if run.status is not ActivityRunStatus.RUNNING:
                raise ExecutionCoordinatorConflict("run is not executable")
            if any(
                observation.workspace_id != request.identity.workspace_id
                for observation in outcome.observations
            ):
                outcome = ActivityExecutionOutcome.uncertain(
                    FailureEvidence(
                        FailureCategory.UNCERTAIN,
                        "adapter-observation-workspace-mismatch",
                        "adapter returned observation evidence for a different workspace",
                    )
                )
            try:
                event_evidence = _step_evidence(command.fence, outcome.evidence)
            except OperationsRecordError:
                outcome = ActivityExecutionOutcome.uncertain(
                    FailureEvidence(
                        FailureCategory.UNCERTAIN,
                        "adapter-evidence-envelope-invalid",
                        "adapter evidence could not enter durable step history",
                    )
                )
                event_evidence = _step_evidence(command.fence, outcome.evidence)
            kind = _outcome_event_kind(outcome.kind)
            event = ActivityEventRecord(
                event_id=self._id_factory(),
                run_id=run.run_id,
                ordinal=stores.execution.next_event_ordinal(run.run_id),
                kind=kind,
                occurred_at=now,
                activity_id=activity.activity_id.value,
                evidence=event_evidence,
                failure=outcome.failure,
            )
            stores.execution.add_event(event)
            for observation in outcome.observations:
                stores.observed_state.put(observation)
            unit_of_work.commit()
            return outcome

    def _load_context(self, command: ExecuteActivityRun) -> "_CoordinatorContext":
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            request, run = _locked_request_and_run(stores, command)
            missing_plan = False
            try:
                plan_record = stores.activity_history.get_plan(run.plan_id)
            except KeyError:
                missing_plan = True
            if missing_plan:
                raise ExecutionCoordinatorNotFound("activity plan was not found")
            for projection_id, graph_id, label in (
                (
                    plan_record.base_realized_projection_id,
                    plan_record.base_graph_id,
                    "base",
                ),
                (
                    plan_record.desired_realized_projection_id,
                    plan_record.desired_graph_id,
                    "desired",
                ),
            ):
                if projection_id is not None:
                    continue
                missing_authored = False
                try:
                    authored = stores.graphs.get(graph_id)
                except KeyError:
                    missing_authored = True
                if missing_authored:
                    raise ExecutionCoordinatorNotFound(
                        "pinned graph was not found"
                    )
                if authored.workspace_id != request.identity.workspace_id:
                    raise ExecutionCoordinatorConflict(
                        f"{label} graph must match execution workspace"
                    )
            missing_projection = False
            try:
                base_graph = (
                    stores.realized_graphs.get(
                        plan_record.base_realized_projection_id
                    )
                    if plan_record.base_realized_projection_id is not None
                    else stores.realized_graphs.identity_for_authored(
                        request.identity.workspace_id,
                        plan_record.base_graph_id,
                    )
                )
                desired_graph = (
                    stores.realized_graphs.get(
                        plan_record.desired_realized_projection_id
                    )
                    if plan_record.desired_realized_projection_id is not None
                    else stores.realized_graphs.identity_for_authored(
                        request.identity.workspace_id,
                        plan_record.desired_graph_id,
                    )
                )
            except KeyError:
                missing_projection = True
            if missing_projection:
                raise ExecutionCoordinatorNotFound("pinned graph was not found")
            registered_products = stores.registered_products.list_active(
                request.identity.workspace_id
            )
            image_pull_authorities = stores.image_pull_authorities.list_active(
                request.identity.workspace_id
            )
            runtime_authorities = stores.runtime_authorities.list_active(
                request.identity.workspace_id
            )
            runtime_authority_deliveries = (
                stores.runtime_authority_deliveries.list_active(
                    request.identity.workspace_id
                )
            )
            ingress_authorities = stores.ingress_authorities.list_active(
                request.identity.workspace_id
            )
            ingress_resources = stores.ingress_resources.list_cloudflare(
                request.identity.workspace_id
            )
            generated_ingress_secrets = (
                stores.generated_ingress_secrets.list_for_workspace(
                    request.identity.workspace_id
                )
            )
            events = stores.execution.events_for_run(run.run_id)
        journal = activity_journal_events(events)
        projection = project_activity_journal(plan_record.plan, journal)
        schedule = derive_schedule(plan_record.plan, projection.state)
        return _CoordinatorContext(
            request=request,
            run=run,
            plan_record=plan_record,
            base_graph=base_graph,
            desired_graph=desired_graph,
            registered_products=registered_products,
            image_pull_authorities=image_pull_authorities,
            runtime_authorities=runtime_authorities,
            runtime_authority_deliveries=runtime_authority_deliveries,
            ingress_authorities=ingress_authorities,
            ingress_resources=ingress_resources,
            generated_ingress_secrets=generated_ingress_secrets,
            events=events,
            projection=projection,
            schedule=schedule,
            authority=command.authority,
            fence=command.fence,
        )

    def _fresh_run(self, run_id: str) -> ActivityRunRecord:
        with self._unit_of_work_factory() as unit_of_work:
            return _get_run(unit_of_work.stores, run_id)


@dataclass(frozen=True)
class _CoordinatorContext:
    request: ExecutionRequestRecord
    run: ActivityRunRecord
    plan_record: ActivityPlanRecord
    base_graph: RealizedGraphProjectionRecord
    desired_graph: RealizedGraphProjectionRecord
    registered_products: tuple[RegisteredProduct, ...]
    image_pull_authorities: tuple[RegisteredImagePullAuthority, ...]
    runtime_authorities: tuple[RegisteredRuntimeAuthority, ...]
    runtime_authority_deliveries: tuple[RegisteredRuntimeAuthorityDelivery, ...]
    ingress_authorities: tuple[RegisteredIngressAuthority, ...]
    ingress_resources: tuple[CloudflareOwnedIngressResource, ...]
    generated_ingress_secrets: tuple[GeneratedIngressSecretReference, ...]
    events: tuple[ActivityEventRecord, ...]
    projection: SagaJournalProjection
    schedule: ExecutionSchedule
    authority: ExecutionWorkerAuthority
    fence: ExecutionLeaseFence

    def __post_init__(self) -> None:
        workspace_id = self.request.identity.workspace_id
        if self.run.admission.request_id != self.request.identity.request_id:
            raise ExecutionCoordinatorConflict("run must belong to execution request")
        if self.request.claim is None or self.request.claim.fence != self.fence:
            raise ExecutionCoordinatorDenied(
                "worker does not own the execution request claim"
            )
        if self.authority.worker_id != self.fence.worker_id:
            raise ExecutionCoordinatorDenied(
                "worker does not own the execution request claim"
            )
        if self.run.plan_id != self.plan_record.plan_id:
            raise ExecutionCoordinatorConflict("run must use pinned activity plan")
        if self.request.identity.plan_id != self.plan_record.plan_id:
            raise ExecutionCoordinatorConflict("request must use pinned activity plan")
        if self.plan_record.base_graph_id != self.base_graph.source_authored_graph_id:
            raise ExecutionCoordinatorConflict(
                "base graph source must match activity plan"
            )
        if (
            self.plan_record.desired_graph_id
            != self.desired_graph.source_authored_graph_id
        ):
            raise ExecutionCoordinatorConflict(
                "desired graph source must match activity plan"
            )
        if (
            self.plan_record.base_realized_projection_id is not None
            and self.plan_record.base_realized_projection_id
            != self.base_graph.projection_id
        ):
            raise ExecutionCoordinatorConflict(
                "base realized projection must match activity plan"
            )
        if (
            self.plan_record.desired_realized_projection_id is not None
            and self.plan_record.desired_realized_projection_id
            != self.desired_graph.projection_id
        ):
            raise ExecutionCoordinatorConflict(
                "desired realized projection must match activity plan"
            )
        if self.base_graph.workspace_id != workspace_id:
            raise ExecutionCoordinatorConflict("base graph must match execution workspace")
        if self.desired_graph.workspace_id != workspace_id:
            raise ExecutionCoordinatorConflict("desired graph must match execution workspace")
        for product in self.registered_products:
            if product.workspace_id != workspace_id:
                raise ExecutionCoordinatorConflict("registered product must match workspace")
        for pull_authority in self.image_pull_authorities:
            if pull_authority.workspace_id != workspace_id:
                raise ExecutionCoordinatorConflict(
                    "registered image pull authority must match workspace"
                )
        for runtime_authority in self.runtime_authorities:
            if runtime_authority.workspace_id != workspace_id:
                raise ExecutionCoordinatorConflict(
                    "registered runtime authority must match workspace"
                )
        for runtime_authority_delivery in self.runtime_authority_deliveries:
            if runtime_authority_delivery.workspace_id != workspace_id:
                raise ExecutionCoordinatorConflict(
                    "registered runtime authority delivery must match workspace"
                )
        for ingress_authority in self.ingress_authorities:
            if ingress_authority.workspace_id != workspace_id:
                raise ExecutionCoordinatorConflict(
                    "registered ingress authority must match workspace"
                )
        for ingress_resource in self.ingress_resources:
            if ingress_resource.workspace_id != workspace_id:
                raise ExecutionCoordinatorConflict(
                    "owned ingress resource must match workspace"
                )
        for generated_ingress_secret in self.generated_ingress_secrets:
            if generated_ingress_secret.workspace_id != workspace_id:
                raise ExecutionCoordinatorConflict(
                    "generated ingress secret must match workspace"
                )

    @property
    def plan(self) -> ActivityPlan:
        return self.plan_record.plan

    def realization_context(
        self,
        activity: PlannedActivity,
        intent_event: ActivityEventRecord,
    ) -> ActivityRealizationContext:
        return ActivityRealizationContext(
            activity=activity,
            request=self.request,
            run=self.run,
            plan_record=self.plan_record,
            base_graph=self.base_graph,
            desired_graph=self.desired_graph,
            registered_products=self.registered_products,
            authority=self.authority,
            fence=self.fence,
            intent_event=intent_event,
            image_pull_authorities=self.image_pull_authorities,
            runtime_authorities=self.runtime_authorities,
            runtime_authority_deliveries=self.runtime_authority_deliveries,
            ingress_authorities=self.ingress_authorities,
            ingress_resources=self.ingress_resources,
            generated_ingress_secrets=self.generated_ingress_secrets,
        )


from control_plane_kit_operations.runtime_effects import (
    _runtime_effect_intent_for_context,
    required_secret_uses_for_runtime_effect,
)
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import (
    EffectAttemptReconciliationService,
)


def _get_run(stores: Any, run_id: str) -> ActivityRunRecord:
    missing_run = False
    try:
        run = stores.execution.get_run(run_id)
    except KeyError:
        missing_run = True
    if missing_run:
        raise ExecutionCoordinatorNotFound("activity run was not found")
    return run


def _get_run_for_update(stores: Any, run_id: str) -> ActivityRunRecord:
    missing_run = False
    try:
        run = stores.execution.get_run_for_update(run_id)
    except KeyError:
        missing_run = True
    if missing_run:
        raise ExecutionCoordinatorNotFound("activity run was not found")
    return run


def _get_request(stores: Any, request_id: str) -> ExecutionRequestRecord:
    missing_request = False
    try:
        request = stores.execution.get_request(request_id)
    except KeyError:
        missing_request = True
    if missing_request:
        raise ExecutionCoordinatorNotFound("execution request was not found")
    return request


def _get_request_for_update(stores: Any, request_id: str) -> ExecutionRequestRecord:
    missing_request = False
    try:
        request = stores.execution.get_request_for_update(request_id)
    except KeyError:
        missing_request = True
    if missing_request:
        raise ExecutionCoordinatorNotFound("execution request was not found")
    return request


def _locked_request_and_run(
    stores: Any,
    command: ExecuteActivityRun,
) -> tuple[ExecutionRequestRecord, ActivityRunRecord]:
    locator_run = _get_run(stores, command.run_id)
    request = _get_request_for_update(stores, locator_run.admission.request_id)
    run = _get_run_for_update(stores, command.run_id)
    if run.admission.request_id != request.identity.request_id:
        raise ExecutionCoordinatorConflict("activity run request linkage changed")
    _require_worker_owns(request, command.authority, command.fence)
    return request, run


def _validate_observations(
    observations: tuple[ObservationRecord, ...],
    *,
    workspace_id: str,
) -> None:
    for observation in observations:
        if observation.workspace_id != workspace_id:
            raise ExecutionCoordinatorConflict(
                "adapter observation must match execution workspace"
            )


def _require_worker_owns(
    request: ExecutionRequestRecord,
    authority: ExecutionWorkerAuthority,
    fence: ExecutionLeaseFence,
) -> None:
    if PolicyScope.EXECUTION_OPERATE not in authority.scopes:
        raise ExecutionCoordinatorDenied("scope execution:operate is missing")
    if (
        request.status is not ExecutionRequestStatus.CLAIMED
        or request.claim is None
        or request.claim.fence != fence
        or authority.worker_id != fence.worker_id
    ):
        raise ExecutionCoordinatorDenied("worker does not own the execution request claim")


def _step_evidence(
    fence: ExecutionLeaseFence,
    details: BoundedEvidence,
) -> BoundedEvidence:
    return BoundedEvidence.from_mapping(
        {
            "claim_generation": fence.generation,
            "details": details.descriptor(),
        }
    )


def _outcome_event_kind(kind: EffectResultKind) -> ActivityEventKind:
    try:
        return {
            EffectResultKind.SUCCEEDED: ActivityEventKind.STEP_SUCCEEDED,
            EffectResultKind.FAILED: ActivityEventKind.STEP_FAILED,
            EffectResultKind.UNSUPPORTED: ActivityEventKind.STEP_UNSUPPORTED,
            EffectResultKind.UNCERTAIN: ActivityEventKind.STEP_UNCERTAIN,
        }[kind]
    except KeyError:
        raise ExecutionCoordinatorConflict("unsupported adapter outcome") from None


def _require_operate_scope(authority: ExecutionWorkerAuthority) -> None:
    if PolicyScope.EXECUTION_OPERATE not in authority.scopes:
        raise ExecutionCoordinatorDenied("scope execution:operate is missing")


def _uncertain_runtime_result(
    request: RuntimeEffectRequest,
) -> RuntimeEffectResult:
    return RuntimeEffectResult.uncertain(
        request.effect_id,
        RuntimeEffectFailure(
            "runtime.provider-result-unknown",
            "runtime provider result could not be admitted",
        ),
    )


def _unsupported_runtime_result(
    request: RuntimeEffectRequest,
    code: str,
    message: str,
    *,
    runtime_kind: RuntimeKind | None = None,
) -> RuntimeEffectResult:
    details: dict[str, object] = {
        "activity_id": request.activity_id.value,
        "operation": type(request.operation).__name__,
    }
    if runtime_kind is not None:
        details["runtime_kind"] = runtime_kind.value
    return RuntimeEffectResult.unsupported(
        request.effect_id,
        RuntimeEffectFailure(
            code,
            message,
            details,
        )
    )


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{field} must not be empty")


def _require_run_id(value: object) -> None:
    try:
        RunId(value)  # type: ignore[arg-type]
    except ValueError:
        valid = False
    else:
        valid = True
    if not valid:
        raise InvalidOperationCommand("run_id is malformed")
