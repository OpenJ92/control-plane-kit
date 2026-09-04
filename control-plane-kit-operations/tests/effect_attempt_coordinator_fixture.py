from __future__ import annotations

from dataclasses import replace
import inspect

from control_plane_kit_core.operations import (
    EffectAttemptFence,
    EffectAttemptState,
    EffectAttemptStatus,
    EffectRecoveryDecision,
    EffectRecoveryResolution,
)
from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.planning import (
    ActivityId,
    NodeTarget,
    PlannedActivity,
    StartNode,
)
from control_plane_kit_core.planning.saga import (
    derive_schedule,
    project_activity_journal,
)
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_fingerprint,
    runtime_effect_intent_for_request,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectRequest,
    RuntimeEffectResult,
)
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.coordinator import (
    ActivityExecutionOutcome,
    ExecuteActivityRun,
    ExecutionCoordinator,
    _CoordinatorContext,
)
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    EffectAttemptFoldNotFound,
    ExistingFold,
    FoldEffectAttempt,
    NewlyFolded,
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
    ExistingAttempt,
    NewlyStarted,
    StartEffectAttempt,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord,
    ExecutionEffectOutcome,
    effect_outcome_failure,
    effect_outcome_transition,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.records import ActivityEventRecord, ClaimIdentity
from control_plane_kit_operations.runtime_effects import (
    runtime_effect_request_for_context,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.effect_outcome_evidence_fixture import EffectOutcomeEvidenceFixture
from tests.test_runtime_effect_translation import _context


class ForbiddenInteraction:
    def __init__(self, label: str, ledger: list[str]) -> None:
        self.label = label
        self.ledger = ledger

    def __call__(self, *_args, **_kwargs):
        self.ledger.append(self.label)
        raise AssertionError(f"forbidden interaction: {self.label}")

    def __getattr__(self, name: str):
        return ForbiddenInteraction(f"{self.label}.{name}", self.ledger)


class RecordingStartService:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.commands: list[StartEffectAttempt] = []

    def execute(self, command: StartEffectAttempt):
        self.commands.append(command)
        result = self.results.pop(0)
        if type(result) in (
            EffectAttemptStartNotFound,
            EffectAttemptStartConflict,
            EffectAttemptStartDenied,
            RuntimeError,
        ):
            raise result
        return result


class RecordingFoldService:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.commands: list[FoldEffectAttempt] = []

    def execute(self, command: FoldEffectAttempt):
        self.commands.append(command)
        result = self.results.pop(0)
        if type(result) in (
            EffectAttemptFoldNotFound,
            EffectAttemptFoldConflict,
            EffectAttemptFoldDenied,
            RuntimeError,
        ):
            raise result
        if callable(result):
            return result(command)
        return result


class RecordingReconciliationService:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.commands: list[ReconcileEffectAttempt] = []

    def execute(self, command: ReconcileEffectAttempt):
        self.commands.append(command)
        result = self.results.pop(0)
        if type(result) in (
            EffectAttemptReconciliationNotFound,
            EffectAttemptReconciliationConflict,
            EffectAttemptReconciliationDenied,
            RuntimeError,
        ):
            raise result
        return result


class RecordingCoordinatorAdapter:
    def __init__(self, *runtime_results: object) -> None:
        self.runtime_results = list(runtime_results)
        self.legacy_contexts: list[object] = []
        self.runtime_calls: list[tuple[object, RuntimeEffectRequest]] = []

    def execute(self, context):
        self.legacy_contexts.append(context)
        return ActivityExecutionOutcome.succeeded()

    def execute_runtime(self, context, request: RuntimeEffectRequest):
        self.runtime_calls.append((context, request))
        result = self.runtime_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class RecordingLifecycle:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.commands: list[object] = []

    def execute(self, command: object):
        self.commands.append(command)
        if not self.results:
            raise AssertionError("unexpected lifecycle interaction")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class DBFreeExecutionCoordinator(ExecutionCoordinator):
    def execute(self, command: ExecuteActivityRun):
        """Exercise effect logic without claiming durable command-replay coverage."""

        return self._execute_admitted(command)

    def _load_context(self, command: ExecuteActivityRun) -> _CoordinatorContext:
        self.load_commands.append(command)
        return self.pinned_context

    def _record_step_event(self, *_args, **_kwargs):
        self.legacy_writes.append("event")
        raise AssertionError("legacy direct event path was used")

    def _record_outcome(self, *_args, **_kwargs):
        self.legacy_writes.append("outcome")
        raise AssertionError("legacy direct outcome path was used")


class EffectAttemptCoordinatorFixture(EffectOutcomeEvidenceFixture):
    def pinned_runtime_context(self) -> _CoordinatorContext:
        activity = PlannedActivity(
            ActivityId("activity-a"),
            StartNode(NodeTarget("api")),
        )
        realization = _context(activity=activity)
        fence = ExecutionLeaseFence("worker-a", 7)
        request = replace(
            realization.request,
            claim=ClaimIdentity(
                "worker-a",
                7,
                "2026-07-22T10:01:00Z",
                "2026-07-22T10:30:00Z",
            ),
        )
        journal = activity_journal_events(())
        projection = project_activity_journal(realization.plan, journal)
        return _CoordinatorContext(
            request=request,
            run=realization.run,
            plan_record=realization.plan_record,
            base_graph=realization.base_graph,
            desired_graph=realization.desired_graph,
            registered_products=realization.registered_products,
            image_pull_authorities=realization.image_pull_authorities,
            runtime_authorities=realization.runtime_authorities,
            runtime_authority_deliveries=realization.runtime_authority_deliveries,
            ingress_authorities=realization.ingress_authorities,
            ingress_resources=realization.ingress_resources,
            generated_ingress_secrets=realization.generated_ingress_secrets,
            events=(),
            projection=projection,
            schedule=derive_schedule(realization.plan, projection.state),
            authority=realization.authority,
            fence=fence,
        )

    def runtime_intent(self):
        context = self.pinned_runtime_context()
        activity = context.plan.activity(ActivityId("activity-a"))
        intent_event = _context(activity=activity).intent_event
        realization = context.realization_context(activity, intent_event)
        return runtime_effect_intent_for_request(
            runtime_effect_request_for_context(realization)
        )

    def started_attempt(
        self,
        *,
        identity=None,
        request_fingerprint: str | None = None,
        fence: EffectAttemptFence | None = None,
        original_event_id: str = "effect-start-event-a",
    ) -> EffectAttemptRecord:
        intent = self.runtime_intent()
        identity = identity or self.identity(activity_id="activity-a")
        state = EffectAttemptState(
            identity=identity,
            request_fingerprint=(
                runtime_effect_intent_fingerprint(intent)
                if request_fingerprint is None
                else request_fingerprint
            ),
            fence=fence or EffectAttemptFence("worker-a", 7),
            status=EffectAttemptStatus.STARTED,
        )
        event = ActivityEventRecord(
            event_id=original_event_id,
            run_id=state.identity.run_id.value,
            ordinal=1,
            kind=ActivityEventKind.STEP_STARTED,
            occurred_at="2030-01-01T00:00:01Z",
            activity_id=state.identity.activity_id,
            evidence=self.evidence_for(state),
        )
        attempt = EffectAttemptRecord(state, event, event)
        return attempt

    def newly_started(self) -> NewlyStarted:
        return NewlyStarted(self.started_attempt())

    def forged_original_event_attempt(self) -> EffectAttemptRecord:
        lawful = self.started_attempt()
        foreign = replace(
            lawful.original_start_event,
            activity_id="foreign-activity",
        )
        candidate = object.__new__(EffectAttemptRecord)
        object.__setattr__(candidate, "state", lawful.state)
        object.__setattr__(candidate, "original_start_event", foreign)
        object.__setattr__(candidate, "latest_transition_event", foreign)
        return candidate

    def exact_fold_result(self, *, existing: bool = False):
        started = self.newly_started()
        result = RuntimeEffectResult.succeeded(
            started.attempt.original_start_event.event_id
        )
        return self.fold_result_for(
            self.fold_command_for(result),
            started,
            existing=existing,
        )

    def lawful_foreign_fold_result(
        self,
        *,
        drift: str,
        existing: bool = False,
    ):
        if drift == "identity":
            identity = self.identity(run_id="run-b")
            request_fingerprint = None
        elif drift == "fingerprint":
            identity = self.identity(activity_id="activity-a")
            request_fingerprint = "b" * 64
        else:
            raise AssertionError("unknown lawful foreign fold drift")
        started = NewlyStarted(
            self.started_attempt(
                identity=identity,
                request_fingerprint=request_fingerprint,
                original_event_id=f"foreign-effect-start-event-{drift}",
            )
        )
        result = RuntimeEffectResult.succeeded(
            started.attempt.original_start_event.event_id
        )
        outcome = ExecutionEffectOutcome(
            started.attempt.state.identity,
            started.attempt.state.request_fingerprint,
            result,
        )
        command = FoldEffectAttempt(
            request_id="request-a",
            transition=effect_outcome_transition(outcome),
            authority=self.pinned_runtime_context().authority,
            fence=self.pinned_runtime_context().fence,
            failure=effect_outcome_failure(outcome),
            outcome=outcome,
        )
        return self.fold_result_for(
            command,
            started,
            existing=existing,
        )

    def direct_attempt(self) -> EffectAttemptRecord:
        return self.exact_fold_result().attempt

    def recovery_attempt(self) -> EffectAttemptRecord:
        direct = self.direct_attempt()
        state = EffectAttemptState(
            identity=direct.state.identity,
            request_fingerprint=direct.state.request_fingerprint,
            fence=direct.state.fence,
            status=EffectAttemptStatus.SUCCEEDED,
            outcome_fingerprint=direct.state.outcome_fingerprint,
            recovery_decision=EffectRecoveryDecision(
                "decision-a",
                direct.state.identity,
                EffectRecoveryResolution.SUCCEEDED,
                "c" * 64,
                direct.state.outcome_fingerprint,
            ),
        )
        latest = ActivityEventRecord(
            event_id="effect-recovered-event-a",
            run_id=state.identity.run_id.value,
            ordinal=direct.latest_transition_event.ordinal + 1,
            kind=ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED,
            occurred_at="2030-01-01T00:00:03Z",
            activity_id=state.identity.activity_id,
            evidence=self.evidence_for(state),
        )
        return EffectAttemptRecord(
            state,
            direct.original_start_event,
            latest,
        )

    def fold_result_for(
        self,
        command: FoldEffectAttempt,
        started: NewlyStarted,
        *,
        existing: bool = False,
    ):
        outcome = command.outcome
        assert type(outcome) is ExecutionEffectOutcome
        state = EffectAttemptState(
            identity=started.attempt.state.identity,
            request_fingerprint=started.attempt.state.request_fingerprint,
            fence=started.attempt.state.fence,
            status=outcome.status,
            outcome_fingerprint=outcome.outcome_fingerprint,
        )
        latest_kind = {
            EffectAttemptStatus.SUCCEEDED: ActivityEventKind.STEP_SUCCEEDED,
            EffectAttemptStatus.FAILED: ActivityEventKind.STEP_FAILED,
            EffectAttemptStatus.UNSUPPORTED: ActivityEventKind.STEP_UNSUPPORTED,
            EffectAttemptStatus.UNCERTAIN: ActivityEventKind.STEP_UNCERTAIN,
        }[state.status]
        latest = ActivityEventRecord(
            event_id="effect-result-event-a",
            run_id=state.identity.run_id.value,
            ordinal=started.attempt.original_start_event.ordinal + 1,
            kind=latest_kind,
            occurred_at="2030-01-01T00:00:02Z",
            activity_id=state.identity.activity_id,
            evidence=self.evidence_for(state),
            failure=command.failure,
        )
        attempt = EffectAttemptRecord(
            state,
            started.attempt.original_start_event,
            latest,
        )
        record = EffectAttemptOutcomeRecord(
            "workspace-a",
            outcome,
            attempt,
            (),
        )
        result_type = ExistingFold if existing else NewlyFolded
        return result_type(attempt, record)

    def execution_outcome(self, result: RuntimeEffectResult) -> ExecutionEffectOutcome:
        attempt = self.started_attempt()
        return ExecutionEffectOutcome(
            attempt.state.identity,
            attempt.state.request_fingerprint,
            result,
        )

    def fold_command_for(self, result: RuntimeEffectResult) -> FoldEffectAttempt:
        outcome = self.execution_outcome(result)
        return FoldEffectAttempt(
            request_id="request-a",
            transition=effect_outcome_transition(outcome),
            authority=self.pinned_runtime_context().authority,
            fence=self.pinned_runtime_context().fence,
            failure=effect_outcome_failure(outcome),
            outcome=outcome,
        )

    def coordinator_command(self, *, max_effects: int = 1) -> ExecuteActivityRun:
        context = self.pinned_runtime_context()
        return ExecuteActivityRun(
            "run-a",
            context.authority,
            context.fence,
            IdempotencyKey("coordinator-a"),
            max_effects,
        )

    def db_free_coordinator(
        self,
        *,
        start_service: RecordingStartService,
        fold_service: RecordingFoldService,
        reconciliation_service: RecordingReconciliationService,
        adapter: RecordingCoordinatorAdapter,
        lifecycle: RecordingLifecycle | None = None,
        clock=None,
        id_factory=None,
    ) -> DBFreeExecutionCoordinator:
        effect_ledger: list[str] = []
        dependencies = {
            "unit_of_work_factory": ForbiddenInteraction("uow", effect_ledger),
            "lifecycle": lifecycle or RecordingLifecycle(),
            "adapter": adapter,
            "start_service": start_service,
            "fold_service": fold_service,
            "reconciliation_service": reconciliation_service,
            "clock": clock or ForbiddenInteraction("clock", effect_ledger),
            "id_factory": id_factory or ForbiddenInteraction("id", effect_ledger),
        }
        if "start_service" in inspect.signature(ExecutionCoordinator).parameters:
            candidate = DBFreeExecutionCoordinator(**dependencies)
        else:
            candidate = object.__new__(DBFreeExecutionCoordinator)
            candidate._unit_of_work_factory = dependencies["unit_of_work_factory"]
            candidate._lifecycle = dependencies["lifecycle"]
            candidate._adapter = dependencies["adapter"]
            candidate._clock = dependencies["clock"]
            candidate._id_factory = dependencies["id_factory"]
            candidate._start_service = dependencies["start_service"]
            candidate._fold_service = dependencies["fold_service"]
            candidate._reconciliation_service = dependencies[
                "reconciliation_service"
            ]
        candidate.pinned_context = self.pinned_runtime_context()
        candidate.load_commands = []
        candidate.legacy_writes = []
        candidate.effect_ledger = effect_ledger
        return candidate


__all__ = [
    "DBFreeExecutionCoordinator",
    "EffectAttemptCoordinatorFixture",
    "ForbiddenInteraction",
    "RecordingCoordinatorAdapter",
    "RecordingFoldService",
    "RecordingLifecycle",
    "RecordingReconciliationService",
    "RecordingStartService",
]
