"""Durable execution coordinator service without runtime-specific effects."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Callable, Mapping, Protocol

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
    PlannedActivity,
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
from control_plane_kit_core.probe_intents import (
    ProbeKind,
    ProbeOutcome,
    RuntimeEndpointObservation,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectRequest,
    RuntimeEffectResult,
)
from control_plane_kit_core.secrets import (
    SecretResolutionGrant,
)
from control_plane_kit_core.topology import (
    GraphDescriptorError,
)
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.activity_journal import activity_journal_events
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
    ExecutionRequestRecord,
    FailureEvidence,
    GraphVersionRecord,
    ObservationRecord,
    ObservationStatus,
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


class CoordinatorStatus(StrEnum):
    """Closed coordinator result statuses for the operations service boundary."""

    COMPLETED = "completed"
    FAILED = "failed"
    PROGRESSED = "progressed"
    IN_FLIGHT = "in-flight"
    UNCERTAIN = "uncertain"
    UNSUPPORTED = "unsupported"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ExecuteActivityRun:
    """Advance one claimed, running activity run by at most max_effects steps."""

    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey
    max_effects: int = 1

    def __post_init__(self) -> None:
        _required_text(self.run_id, "run_id")
        if not isinstance(self.authority, ExecutionWorkerAuthority):
            raise InvalidOperationCommand("authority must be ExecutionWorkerAuthority")
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
    base_graph: GraphVersionRecord
    desired_graph: GraphVersionRecord
    registered_products: tuple[RegisteredProduct, ...]
    authority: ExecutionWorkerAuthority
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
        if not isinstance(self.base_graph, GraphVersionRecord):
            raise InvalidOperationCommand("realization base graph must be GraphVersionRecord")
        if not isinstance(self.desired_graph, GraphVersionRecord):
            raise InvalidOperationCommand("realization desired graph must be GraphVersionRecord")
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
        if not isinstance(self.intent_event, ActivityEventRecord):
            raise InvalidOperationCommand("realization intent must be ActivityEventRecord")
        workspace_id = self.request.identity.workspace_id
        if self.run.admission.request_id != self.request.identity.request_id:
            raise InvalidOperationCommand("realization run must belong to request")
        if self.run.plan_id != self.plan_record.plan_id:
            raise InvalidOperationCommand("realization run must use the pinned plan")
        if self.request.identity.plan_id != self.plan_record.plan_id:
            raise InvalidOperationCommand("realization request must use the pinned plan")
        if self.plan_record.base_graph_id != self.base_graph.graph_id:
            raise InvalidOperationCommand("realization base graph must match plan")
        if self.plan_record.desired_graph_id != self.desired_graph.graph_id:
            raise InvalidOperationCommand("realization desired graph must match plan")
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
        if self.intent_event.kind is not ActivityEventKind.STEP_STARTED:
            raise InvalidOperationCommand("realization intent must be step_started")
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
        if not isinstance(context, ActivityRealizationContext):
            raise InvalidOperationCommand(
                "runtime dispatch requires ActivityRealizationContext"
            )
        if _is_socket_connection_operation(context.activity):
            return _socket_connection_outcome(context)
        try:
            from control_plane_kit_operations.runtime_effects import (
                runtime_effect_request_for_context,
            )

            request = runtime_effect_request_for_context(context)
        except (GraphDescriptorError, InvalidOperationCommand, KeyError, ValueError) as error:
            return _unsupported_dispatch(
                context,
                "runtime.dispatch-target-unsupported",
                str(error),
            )
        runtime_kind = request.runtime_kind
        interpreter = self.interpreters.get(runtime_kind)
        if interpreter is None:
            return _unsupported_dispatch(
                context,
                "runtime.interpreter-missing",
                f"no runtime interpreter is configured for {runtime_kind.value!r}",
                runtime_kind=runtime_kind,
            )
        authority = _runtime_authority_for_request(context, request)
        if authority is _MISSING_RUNTIME_AUTHORITY:
            return _unsupported_dispatch(
                context,
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
            return _unsupported_dispatch(
                context,
                "secret.use-not-authorized",
                "runtime secret use was not authorized",
                runtime_kind=runtime_kind,
            )
        except InvalidOperationCommand:
            return _unsupported_dispatch(
                context,
                "secret.resolution-authorizer-invalid",
                "runtime secret authorization could not be established",
                runtime_kind=runtime_kind,
            )
        if authority is None:
            result = interpreter.execute(request)
        else:
            execute_with_authority = getattr(interpreter, "execute_with_authority", None)
            if execute_with_authority is None:
                return _unsupported_dispatch(
                    context,
                    "runtime.authority-interpreter-unsupported",
                    "runtime interpreter cannot consume registered runtime authority",
                    runtime_kind=runtime_kind,
                )
            result = execute_with_authority(request, authority)
        return _outcome_from_runtime_result(context, result)

    def _authorize_secret_resolutions(
        self,
        context: ActivityRealizationContext,
        request: RuntimeEffectRequest,
        *,
        authority: RegisteredRuntimeAuthority | None,
    ) -> RuntimeEffectRequest:
        from control_plane_kit_operations.runtime_effects import (
            required_secret_uses_for_runtime_effect,
        )

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
                        session_id=context.plan_record.session_id,
                        run_id=request.source.run_id,
                        activity_id=request.activity_id.value,
                        effect_id=request.effect_id,
                    ),
                    requested_at=context.intent_event.occurred_at,
                    actor_scopes=context.authority.scopes,
                    operation_id=request.source.request_id,
                    session_id=context.plan_record.session_id,
                    run_id=request.source.run_id,
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


class ExecutionCoordinator:
    """Operations-owned durable coordinator over core plan and saga languages."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        lifecycle: RunLifecycleCommandService,
        adapter: ActivityExecutionAdapter,
        clock: Callable[[], str],
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._lifecycle = lifecycle
        self._adapter = adapter
        self._clock = clock
        self._id_factory = id_factory

    def execute(self, command: ExecuteActivityRun) -> ExecutionCoordinatorResult:
        _require_operate_scope(command.authority)
        attempted = 0
        current: ExecutionCoordinatorResult | None = None
        for _ in range(command.max_effects):
            context = self._load_context(command)
            current = self._classify_current(context)
            if current.status is not CoordinatorStatus.PROGRESSED:
                return current
            activity = current.activity_id
            if activity is None:
                raise ExecutionCoordinatorConflict("ready activity identity is missing")
            planned = context.plan.activity(ActivityId(activity))
            intent_event = self._record_step_event(
                command,
                planned,
                ActivityEventKind.STEP_STARTED,
                BoundedEvidence.from_mapping({"phase": "intent"}),
            )
            realization = context.realization_context(planned, intent_event)
            attempted += 1
            try:
                outcome = self._adapter.execute(realization)
            except Exception as error:  # noqa: BLE001 - adapter errors become uncertainty evidence.
                outcome = ActivityExecutionOutcome.uncertain(
                    FailureEvidence(
                        FailureCategory.UNCERTAIN,
                        "adapter-result-unknown",
                        "adapter raised before a durable result was recorded",
                        BoundedEvidence.from_mapping(
                            {"exception_type": type(error).__name__}
                        ),
                    )
                )
            self._record_outcome(command, planned, outcome)
            if outcome.kind is EffectResultKind.SUCCEEDED:
                continue
            classified = self._classify_current(self._load_context(command))
            run = classified.run
            if outcome.kind is EffectResultKind.UNSUPPORTED:
                status = CoordinatorStatus.UNSUPPORTED
            elif outcome.kind is EffectResultKind.UNCERTAIN:
                status = CoordinatorStatus.UNCERTAIN
            else:
                status = CoordinatorStatus.FAILED
            return ExecutionCoordinatorResult(
                run,
                status,
                attempted,
                planned.activity_id.value,
            )
        context = self._load_context(command)
        current = self._classify_current(context)
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
    ) -> ExecutionCoordinatorResult:
        run = context.run
        if run.status is ActivityRunStatus.CLAIMED:
            raise ExecutionCoordinatorConflict("activity run must be started")
        if run.status is ActivityRunStatus.PAUSED:
            return ExecutionCoordinatorResult(run, CoordinatorStatus.BLOCKED)
        if run.status is ActivityRunStatus.SUCCEEDED:
            return ExecutionCoordinatorResult(run, CoordinatorStatus.COMPLETED)
        if run.status is ActivityRunStatus.FAILED:
            return ExecutionCoordinatorResult(run, CoordinatorStatus.FAILED)
        if run.status in {ActivityRunStatus.CANCELLED, ActivityRunStatus.COMPENSATING}:
            return ExecutionCoordinatorResult(run, CoordinatorStatus.BLOCKED)
        if run.status is not ActivityRunStatus.RUNNING:
            return ExecutionCoordinatorResult(run, CoordinatorStatus.BLOCKED)

        if context.projection.uncertain:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.UNCERTAIN,
                activity_id=context.projection.uncertain[0].activity_id,
            )
        if context.schedule.failed:
            failure = FailureEvidence(
                FailureCategory.TERMINAL,
                "activity-step-failed",
                "one or more planned activities failed",
                BoundedEvidence.from_mapping(
                    {
                        "activity_ids": [
                            value.activity_id.value for value in context.schedule.failed
                        ]
                    }
                ),
            )
            try:
                result = self._lifecycle.execute(
                    FailActivityRun(
                        run.run_id,
                        context.authority,
                        IdempotencyKey(f"coordinator:{run.run_id}:fail"),
                        failure,
                    )
                )
                run = result.run
            except RunLifecycleConflict:
                run = self._fresh_run(run.run_id)
            return ExecutionCoordinatorResult(run, CoordinatorStatus.FAILED)
        if context.schedule.running:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.IN_FLIGHT,
                activity_id=context.schedule.running[0].activity_id.value,
            )
        if context.schedule.successful:
            result = self._lifecycle.execute(
                CompleteActivityRun(
                    run.run_id,
                    context.authority,
                    IdempotencyKey(f"coordinator:{run.run_id}:complete"),
                    BoundedEvidence.from_mapping({"result": "all-activities-succeeded"}),
                )
            )
            return ExecutionCoordinatorResult(result.run, CoordinatorStatus.COMPLETED)
        if context.schedule.ready:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.PROGRESSED,
                activity_id=context.schedule.ready[0].activity_id.value,
            )
        if context.schedule.blocked or context.schedule.waiting:
            return ExecutionCoordinatorResult(run, CoordinatorStatus.BLOCKED)
        return ExecutionCoordinatorResult(run, CoordinatorStatus.BLOCKED)

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
            run = _get_run_for_update(stores, command.run_id)
            request = _get_request(stores, run.admission.request_id)
            _require_worker_owns(request, command.authority)
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
                    evidence=evidence or BoundedEvidence(),
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
    ) -> None:
        if not self._observations_match_workspace(command, outcome.observations):
            outcome = ActivityExecutionOutcome.uncertain(
                FailureEvidence(
                    FailureCategory.UNCERTAIN,
                    "adapter-observation-workspace-mismatch",
                    "adapter returned observation evidence for a different workspace",
                )
            )
        if outcome.kind is EffectResultKind.SUCCEEDED:
            self._record_step_event(
                command,
                activity,
                ActivityEventKind.STEP_SUCCEEDED,
                outcome.evidence,
                observations=outcome.observations,
            )
            return
        if outcome.kind is EffectResultKind.FAILED:
            assert outcome.failure is not None
            self._record_step_event(
                command,
                activity,
                ActivityEventKind.STEP_FAILED,
                failure=outcome.failure,
                observations=outcome.observations,
            )
            return
        if outcome.kind is EffectResultKind.UNSUPPORTED:
            assert outcome.failure is not None
            self._record_step_event(
                command,
                activity,
                ActivityEventKind.STEP_UNSUPPORTED,
                failure=outcome.failure,
                observations=outcome.observations,
            )
            return
        if outcome.kind is EffectResultKind.UNCERTAIN:
            assert outcome.failure is not None
            self._record_step_event(
                command,
                activity,
                ActivityEventKind.STEP_UNCERTAIN,
                failure=outcome.failure,
                observations=outcome.observations,
            )
            return
        raise ExecutionCoordinatorConflict("unsupported adapter outcome")

    def _observations_match_workspace(
        self,
        command: ExecuteActivityRun,
        observations: tuple[ObservationRecord, ...],
    ) -> bool:
        if not observations:
            return True
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            run = _get_run(stores, command.run_id)
            request = _get_request(stores, run.admission.request_id)
            _require_worker_owns(request, command.authority)
        workspace_id = request.identity.workspace_id
        return all(observation.workspace_id == workspace_id for observation in observations)

    def _load_context(self, command: ExecuteActivityRun) -> "_CoordinatorContext":
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            run = _get_run_for_update(stores, command.run_id)
            request = _get_request(stores, run.admission.request_id)
            _require_worker_owns(request, command.authority)
            try:
                plan_record = stores.activity_history.get_plan(run.plan_id)
            except KeyError as error:
                raise ExecutionCoordinatorNotFound("activity plan was not found") from error
            try:
                base_graph = stores.graphs.get(plan_record.base_graph_id)
                desired_graph = stores.graphs.get(plan_record.desired_graph_id)
            except KeyError as error:
                raise ExecutionCoordinatorNotFound("pinned graph was not found") from error
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
        )

    def _fresh_run(self, run_id: str) -> ActivityRunRecord:
        with self._unit_of_work_factory() as unit_of_work:
            return _get_run(unit_of_work.stores, run_id)


@dataclass(frozen=True)
class _CoordinatorContext:
    request: ExecutionRequestRecord
    run: ActivityRunRecord
    plan_record: ActivityPlanRecord
    base_graph: GraphVersionRecord
    desired_graph: GraphVersionRecord
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

    def __post_init__(self) -> None:
        workspace_id = self.request.identity.workspace_id
        if self.run.admission.request_id != self.request.identity.request_id:
            raise ExecutionCoordinatorConflict("run must belong to execution request")
        if self.run.plan_id != self.plan_record.plan_id:
            raise ExecutionCoordinatorConflict("run must use pinned activity plan")
        if self.request.identity.plan_id != self.plan_record.plan_id:
            raise ExecutionCoordinatorConflict("request must use pinned activity plan")
        if self.plan_record.base_graph_id != self.base_graph.graph_id:
            raise ExecutionCoordinatorConflict("base graph must match activity plan")
        if self.plan_record.desired_graph_id != self.desired_graph.graph_id:
            raise ExecutionCoordinatorConflict("desired graph must match activity plan")
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
            intent_event=intent_event,
            image_pull_authorities=self.image_pull_authorities,
            runtime_authorities=self.runtime_authorities,
            runtime_authority_deliveries=self.runtime_authority_deliveries,
            ingress_authorities=self.ingress_authorities,
            ingress_resources=self.ingress_resources,
            generated_ingress_secrets=self.generated_ingress_secrets,
        )


def _get_run(stores: Any, run_id: str) -> ActivityRunRecord:
    try:
        return stores.execution.get_run(run_id)
    except KeyError as error:
        raise ExecutionCoordinatorNotFound("activity run was not found") from error


def _get_run_for_update(stores: Any, run_id: str) -> ActivityRunRecord:
    try:
        return stores.execution.get_run_for_update(run_id)
    except KeyError as error:
        raise ExecutionCoordinatorNotFound("activity run was not found") from error


def _get_request(stores: Any, request_id: str) -> ExecutionRequestRecord:
    try:
        return stores.execution.get_request(request_id)
    except KeyError as error:
        raise ExecutionCoordinatorNotFound("execution request was not found") from error


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
) -> None:
    if PolicyScope.EXECUTION_OPERATE not in authority.scopes:
        raise ExecutionCoordinatorDenied("scope execution:operate is missing")
    if (
        request.status is not ExecutionRequestStatus.CLAIMED
        or request.claim is None
        or request.claim.worker_id != authority.worker_id
    ):
        raise ExecutionCoordinatorDenied("worker does not own the execution request claim")


def _require_operate_scope(authority: ExecutionWorkerAuthority) -> None:
    if PolicyScope.EXECUTION_OPERATE not in authority.scopes:
        raise ExecutionCoordinatorDenied("scope execution:operate is missing")


def _outcome_from_runtime_result(
    context: ActivityRealizationContext,
    result: RuntimeEffectResult,
) -> ActivityExecutionOutcome:
    if not isinstance(result, RuntimeEffectResult):
        return ActivityExecutionOutcome.uncertain(
            FailureEvidence(
                FailureCategory.UNCERTAIN,
                "runtime.result-malformed",
                "runtime interpreter returned a non-runtime effect result",
            )
        )
    if result.effect_id != context.intent_event.event_id:
        return ActivityExecutionOutcome.uncertain(
            FailureEvidence(
                FailureCategory.UNCERTAIN,
                "runtime.effect-id-mismatch",
                "runtime interpreter returned a result for a different effect",
                BoundedEvidence.from_mapping(
                    {
                        "expected_effect_id": context.intent_event.event_id,
                        "actual_effect_id": result.effect_id,
                    }
                ),
            )
        )
    observations = tuple(
        _observation_from_runtime_endpoint(context, index, observation)
        for index, observation in enumerate(result.observations, start=1)
    )
    if result.kind is EffectResultKind.SUCCEEDED:
        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping(result.evidence),
            observations=observations,
        )
    assert result.failure is not None
    failure = FailureEvidence(
        _failure_category_for_runtime_result(result),
        result.failure.code,
        result.failure.message,
        BoundedEvidence.from_mapping(result.failure.details),
    )
    if result.kind is EffectResultKind.FAILED:
        return ActivityExecutionOutcome.failed(failure)
    if result.kind is EffectResultKind.UNSUPPORTED:
        return ActivityExecutionOutcome.unsupported(failure)
    if result.kind is EffectResultKind.UNCERTAIN:
        return ActivityExecutionOutcome.uncertain(failure)
    return ActivityExecutionOutcome.uncertain(
        FailureEvidence(
            FailureCategory.UNCERTAIN,
            "runtime.result-kind-unsupported",
            "runtime interpreter returned an unsupported result kind",
        )
    )


def _failure_category_for_runtime_result(
    result: RuntimeEffectResult,
) -> FailureCategory:
    if result.kind is EffectResultKind.UNSUPPORTED:
        return FailureCategory.OPERATOR_REVIEW
    if result.kind is EffectResultKind.UNCERTAIN:
        return FailureCategory.UNCERTAIN
    return FailureCategory.TERMINAL


def _observation_from_runtime_endpoint(
    context: ActivityRealizationContext,
    index: int,
    observation: RuntimeEndpointObservation,
) -> ObservationRecord:
    return ObservationRecord(
        observation_id=f"{context.intent_event.event_id}:runtime-endpoint:{index}",
        workspace_id=context.request.identity.workspace_id,
        subject_id=observation.subject_id,
        status=ObservationStatus.UNKNOWN,
        observed_at=context.intent_event.occurred_at,
        evidence=BoundedEvidence.from_mapping(
            {"runtime_endpoint": observation.descriptor()}
        ),
        graph_id=observation.graph_id,
        probe_kind=ProbeKind.TRANSPORT,
        probe_outcome=ProbeOutcome.UNKNOWN,
        endpoint_context=observation.context,
    )


def _unsupported_dispatch(
    context: ActivityRealizationContext,
    code: str,
    message: str,
    *,
    runtime_kind: RuntimeKind | None = None,
) -> ActivityExecutionOutcome:
    details: dict[str, object] = {
        "activity_id": context.activity.activity_id.value,
        "operation": type(context.activity.operation).__name__,
    }
    if runtime_kind is not None:
        details["runtime_kind"] = runtime_kind.value
    return ActivityExecutionOutcome.unsupported(
        FailureEvidence(
            FailureCategory.OPERATOR_REVIEW,
            code,
            message,
            BoundedEvidence.from_mapping(details),
        )
    )


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{field} must not be empty")
