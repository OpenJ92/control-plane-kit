"""Atomically bind the next compensation step to one fresh effect attempt."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from control_plane_kit_core.operations import (
    ActivityEventKind,
    ActivityRunStatus,
    EffectAttemptFence,
    EffectAttemptIdentity,
    EffectAttemptStatus,
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
    ExecutionRequestStatus,
    fold_effect_attempt,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntent,
    runtime_effect_intent_fingerprint,
)
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptEventEvidence,
    EffectAttemptRecord,
    effect_attempt_state_fingerprint,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ApprovalDecisionKind,
    BoundedEvidence,
    FailedRunCompensationAttemptBinding,
    OperationSessionStatus,
    OperationsRecordError,
)


class FailedRunCompensationAttemptError(RuntimeError):
    """Base error for compensation-attempt admission."""


class FailedRunCompensationAttemptConflict(FailedRunCompensationAttemptError):
    """Raised when durable compensation truth is incongruent."""


class FailedRunCompensationAttemptDenied(FailedRunCompensationAttemptError):
    """Raised when active execution authority is absent."""


class FailedRunCompensationAttemptNotFound(FailedRunCompensationAttemptError):
    """Raised when an exact compensation coordinate is absent."""


@dataclass(frozen=True, slots=True)
class StartFailedRunCompensationAttempt:
    program_id: str
    position: int
    intent: RuntimeEffectIntent
    authority: ExecutionWorkerAuthority
    fence: ExecutionLeaseFence

    def __post_init__(self) -> None:
        if type(self.program_id) is not str or not self.program_id:
            raise FailedRunCompensationAttemptError("program_id is invalid")
        if type(self.position) is not int or self.position < 1:
            raise FailedRunCompensationAttemptError("position is invalid")
        if type(self.intent) is not RuntimeEffectIntent:
            raise FailedRunCompensationAttemptError("intent is invalid")
        if type(self.authority) is not ExecutionWorkerAuthority:
            raise FailedRunCompensationAttemptDenied("authority is invalid")
        if PolicyScope.EXECUTION_OPERATE not in self.authority.scopes:
            raise FailedRunCompensationAttemptDenied(
                "execution authority is required"
            )
        if type(self.fence) is not ExecutionLeaseFence:
            raise FailedRunCompensationAttemptDenied("fence is invalid")
        if self.authority.worker_id != self.fence.worker_id:
            raise FailedRunCompensationAttemptDenied(
                "authority and fence are incongruent"
            )


@dataclass(frozen=True, slots=True)
class FailedRunCompensationAttemptStartResult:
    binding: FailedRunCompensationAttemptBinding
    attempt: EffectAttemptRecord
    intent: EffectAttemptIntentRecord
    replayed: bool


class NewlyBoundCompensationAttempt(FailedRunCompensationAttemptStartResult):
    """One newly persisted inverse attempt."""


class ExistingCompensationAttemptBinding(
    FailedRunCompensationAttemptStartResult
):
    """One exact write-free replay."""


class FailedRunCompensationAttemptStartService:
    """Start only the first incomplete admitted compensation step."""

    def __init__(
        self,
        unit_of_work: Callable[[], PostgresUnitOfWork],
        *,
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work = unit_of_work
        self._id_factory = id_factory

    def execute(
        self,
        command: StartFailedRunCompensationAttempt,
    ) -> FailedRunCompensationAttemptStartResult:
        if type(command) is not StartFailedRunCompensationAttempt:
            raise FailedRunCompensationAttemptError("command is invalid")
        with self._unit_of_work() as unit_of_work:
            result = self._start(unit_of_work.stores, command)
            unit_of_work.commit()
            return result

    def _start(self, stores, command):
        try:
            record, program = stores.failed_run_compensations.get_for_update(
                command.program_id
            )
        except KeyError as error:
            raise FailedRunCompensationAttemptNotFound(
                "compensation program was not found"
            ) from error
        except OperationsRecordError as error:
            raise FailedRunCompensationAttemptConflict(
                "compensation program is incongruent"
            ) from error
        _validate_program_lineage(stores, record, program, command)
        bindings = stores.failed_run_compensation_attempts.for_program(
            program.program_id
        )
        if tuple(binding.position for binding in bindings) != tuple(
            range(1, len(bindings) + 1)
        ):
            raise FailedRunCompensationAttemptConflict(
                "compensation bindings are not contiguous"
            )
        if command.position <= len(bindings):
            return _replay(stores, program, command, bindings[command.position - 1])
        if command.position != len(bindings) + 1:
            raise FailedRunCompensationAttemptConflict(
                "only the first incomplete compensation step may start"
            )
        if command.position > len(program.steps):
            raise FailedRunCompensationAttemptConflict(
                "compensation program has no incomplete step"
            )
        step = program.steps[command.position - 1]
        source, expected_intent = _source_truth(stores, program, step)
        if command.intent != expected_intent:
            raise FailedRunCompensationAttemptConflict(
                "inverse intent is incongruent"
            )
        source_identity = source.state.identity
        inverse_identity = EffectAttemptIdentity(
            source_identity.run_id,
            source_identity.activity_id,
            source_identity.attempt + 1,
        )
        binding = FailedRunCompensationAttemptBinding(
            program.program_id,
            command.position,
            source_identity,
            inverse_identity,
        )
        request_fingerprint = runtime_effect_intent_fingerprint(command.intent)
        state = fold_effect_attempt(
            None,
            EffectAttemptTransition(
                EffectAttemptTransitionKind.STARTED,
                inverse_identity,
                request_fingerprint=request_fingerprint,
                prior_attempt=source_identity,
            ),
            fence=EffectAttemptFence(
                command.fence.worker_id,
                command.fence.generation,
            ),
        )
        event = ActivityEventRecord(
            self._id_factory(),
            inverse_identity.run_id.value,
            stores.execution.next_event_ordinal(inverse_identity.run_id.value),
            ActivityEventKind.STEP_COMPENSATION_STARTED,
            _observed_at(stores),
            activity_id=inverse_identity.activity_id,
            evidence=BoundedEvidence.from_mapping(
                {
                    "effect_attempt": EffectAttemptEventEvidence(
                        inverse_identity.attempt,
                        effect_attempt_state_fingerprint(state),
                    ).descriptor()
                }
            ),
        )
        attempt = EffectAttemptRecord(state, event, event)
        intent = EffectAttemptIntentRecord(
            inverse_identity,
            event,
            command.intent,
        )
        stores.execution.add_event(event)
        stores.effect_attempt_intents.insert(intent)
        if stores.effect_attempts.insert_absent(attempt) is None:
            raise FailedRunCompensationAttemptConflict(
                "inverse attempt already exists"
            )
        stores.failed_run_compensation_attempts.insert(binding)
        return NewlyBoundCompensationAttempt(
            binding,
            attempt,
            intent,
            False,
        )


def _validate_program_lineage(stores, record, program, command) -> None:
    lineage = program.evidence.lineage
    try:
        observation = stores.execution.observe_request_lease_for_update(
            record.request_id
        )
        request = observation.request
        run = stores.execution.get_run_for_update(record.run_id)
        workspace = stores.workspaces.get_for_update(record.workspace_id)
        session = stores.activity_history.get_session(record.session_id)
        plan = stores.activity_history.get_plan(record.plan_id)
        decision = stores.activity_history.approval_decision_for_request(
            request.approval_request_id
        )
        event = stores.execution.get_event(record.event_id)
        action = next(
            candidate
            for candidate in stores.activity_history.actions_for_session(
                record.session_id
            )
            if candidate.action_id == record.action_id
        )
    except (KeyError, StopIteration, OperationsRecordError) as error:
        raise FailedRunCompensationAttemptConflict(
            "compensation lineage is incomplete"
        ) from error
    claim = request.claim
    invalid = (
        observation.expired
        or request.status is not ExecutionRequestStatus.CLAIMED
        or claim is None
        or claim.fence != command.fence
        or claim.worker_id != command.authority.worker_id
        or request.identity.request_id != record.request_id
        or request.identity.workspace_id != record.workspace_id
        or request.identity.session_id != record.session_id
        or request.identity.plan_id != record.plan_id
        or decision is None
        or request.approval_decision_id != decision.decision_id
        or decision.decision is not ApprovalDecisionKind.APPROVED
        or decision.request_id != request.approval_request_id
        or run.run_id != record.run_id
        or run.plan_id != record.plan_id
        or run.admission.request_id != record.request_id
        or run.status is not ActivityRunStatus.COMPENSATING
        or session.session_id != record.session_id
        or session.workspace_id != record.workspace_id
        or session.status is not OperationSessionStatus.OPEN
        or plan.plan_id != record.plan_id
        or plan.session_id != record.session_id
        or plan.base_graph_id != lineage.current_graph_id
        or plan.desired_graph_id != lineage.desired_graph_id
        or plan.desired_graph_revision != lineage.desired_graph_revision
        or workspace.current_graph_id != lineage.current_graph_id
        or workspace.desired_graph_id != lineage.desired_graph_id
        or workspace.desired_graph_revision != lineage.desired_graph_revision
        or lineage.workspace_id != record.workspace_id
        or lineage.request_id != record.request_id
        or lineage.run_id.value != record.run_id
        or lineage.plan_id != record.plan_id
        or request.idempotency.intent_fingerprint
        != lineage.execution_intent_fingerprint
        or event.event_id != record.event_id
        or event.run_id != record.run_id
        or event.kind is not ActivityEventKind.RUN_COMPENSATION_STARTED
        or action.action_id != record.action_id
        or action.session_id != record.session_id
        or action.actor_id != record.actor_id
        or action.created_at != record.created_at
    )
    if invalid:
        raise FailedRunCompensationAttemptConflict(
            "compensation lineage is incongruent"
        )


def _source_truth(stores, program, step):
    source_identity = step.source_effect.attempt_identity
    try:
        source = stores.effect_attempts.get_for_update(source_identity)
        outcome = stores.effect_outcomes.get(
            source_identity,
            step.source_effect.completion_event_id,
        )
        source_intent = stores.effect_attempt_intents.get(source_identity)
    except (KeyError, OperationsRecordError) as error:
        raise FailedRunCompensationAttemptConflict(
            "source effect truth is incomplete"
        ) from error
    lineage = program.evidence.lineage
    invalid = (
        source.state.status is not EffectAttemptStatus.SUCCEEDED
        or source.state.identity != source_identity
        or source.state.request_fingerprint
        != step.source_effect.request_fingerprint
        or source.state.outcome_fingerprint
        != step.source_effect.outcome_fingerprint
        or source.latest_transition_event.event_id
        != step.source_effect.completion_event_id
        or source.latest_transition_event.ordinal
        != step.source_effect.completion_ordinal
        or source.latest_transition_event.kind
        is not ActivityEventKind.STEP_SUCCEEDED
        or outcome.attempt != source
        or outcome.outcome.request_fingerprint
        != step.source_effect.request_fingerprint
        or outcome.outcome.outcome_fingerprint
        != step.source_effect.outcome_fingerprint
        or source_intent.identity != source_identity
        or source_intent.request_fingerprint
        != step.source_effect.request_fingerprint
        or source_intent.intent.source.workspace_id != lineage.workspace_id
        or source_intent.intent.source.request_id != lineage.request_id
        or source_intent.intent.source.run_id != lineage.run_id
        or source_intent.intent.source.plan_id != lineage.plan_id
        or source_intent.intent.source.base_graph_id != lineage.current_graph_id
        or source_intent.intent.source.desired_graph_id
        != lineage.desired_graph_id
    )
    if invalid:
        raise FailedRunCompensationAttemptConflict(
            "source effect truth is incongruent"
        )
    return source, replace(source_intent.intent, operation=step.operation)


def _replay(stores, program, command, binding):
    step = program.steps[command.position - 1]
    source, expected_intent = _source_truth(stores, program, step)
    try:
        attempt = stores.effect_attempts.get(binding.inverse_attempt)
        intent = stores.effect_attempt_intents.get(binding.inverse_attempt)
        reverse = stores.failed_run_compensation_attempts.get_for_attempt(
            binding.inverse_attempt
        )
    except (KeyError, OperationsRecordError) as error:
        raise FailedRunCompensationAttemptConflict(
            "inverse attempt truth is incomplete"
        ) from error
    invalid = (
        reverse != binding
        or binding.source_attempt != source.state.identity
        or attempt.state.identity != binding.inverse_attempt
        or attempt.state.prior_attempt != binding.source_attempt
        or attempt.state.status is not EffectAttemptStatus.STARTED
        or attempt.state.fence.worker_id != command.fence.worker_id
        or attempt.state.fence.generation != command.fence.generation
        or intent.identity != binding.inverse_attempt
        or intent.intent != expected_intent
        or intent.intent != command.intent
        or intent.request_fingerprint
        != runtime_effect_intent_fingerprint(command.intent)
    )
    if invalid:
        raise FailedRunCompensationAttemptConflict(
            "inverse attempt replay is incongruent"
        )
    return ExistingCompensationAttemptBinding(
        binding,
        attempt,
        intent,
        True,
    )


def _observed_at(stores) -> str:
    from control_plane_kit_operations.postgres.temporal import (
        decode_postgres_timestamp,
    )

    row = stores.connection.execute("SELECT clock_timestamp()").fetchone()
    if row is None:
        raise FailedRunCompensationAttemptConflict(
            "database clock observation is absent"
        )
    return decode_postgres_timestamp(row[0])


__all__ = [
    "ExistingCompensationAttemptBinding",
    "FailedRunCompensationAttemptBinding",
    "FailedRunCompensationAttemptConflict",
    "FailedRunCompensationAttemptDenied",
    "FailedRunCompensationAttemptError",
    "FailedRunCompensationAttemptNotFound",
    "FailedRunCompensationAttemptStartResult",
    "FailedRunCompensationAttemptStartService",
    "NewlyBoundCompensationAttempt",
    "StartFailedRunCompensationAttempt",
]
