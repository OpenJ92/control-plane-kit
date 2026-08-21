"""Transactional interpreter for one effect-attempt start."""

from __future__ import annotations

from typing import Any, Callable

from control_plane_kit_core.operations import (
    EffectAttemptFence,
    fold_effect_attempt,
)
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.planning import (
    SagaJournalError,
    SagaStateError,
    derive_schedule,
    project_activity_journal,
)
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.effect_attempt_start import (
    EffectAttemptStartConflict,
    EffectAttemptStartDenied,
    EffectAttemptStartNotFound,
    EffectAttemptStartResult,
    ExistingAttempt,
    NewlyStarted,
    StartEffectAttempt,
    _valid_start_command,
)
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptEventEvidence,
    EffectAttemptRecord,
    effect_attempt_state_fingerprint,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    OperationsRecordError,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


_AUTHORITY_ERROR = "effect attempt start authority is invalid"
_ELIGIBILITY_ERROR = "effect attempt start is not eligible"
_INVALID_TRUTH_ERROR = "effect attempt start truth is invalid"
_NOT_FOUND_ERROR = "effect attempt start truth was not found"
_REPLAY_ERROR = "effect attempt replay is incongruent"
_SERIALIZATION_ERROR = "effect attempt start changed concurrently"


class EffectAttemptStartService:
    """Start or observe one exact effect attempt in a caller-owned UoW."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._id_factory = id_factory

    def execute(
        self,
        command: StartEffectAttempt,
    ) -> EffectAttemptStartResult:
        if not _valid_start_command(command):
            raise InvalidOperationCommand(
                "effect attempt start command is invalid"
            )
        if PolicyScope.EXECUTION_OPERATE not in command.authority.scopes:
            raise EffectAttemptStartDenied(
                "scope execution:operate is missing"
            )
        fence = _translate_fence(command)

        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            request = _request_for_update(stores, command.request_id)
            run = _run_for_request_for_update(
                stores,
                command.request_id,
                command.transition.identity.run_id.value,
            )
            _require_request_run(command, request, run)
            attempt = _attempt_for_update(stores, command.transition.identity)
            _require_current_authority(command, request)
            if attempt is not None:
                _require_replay(command, fence, request, run, attempt)
                _require_intent_replay(stores, command, attempt)
                result = ExistingAttempt(attempt)
                unit_of_work.commit()
                return result

            latest_run = _latest_run_for_update(stores, command.request_id)
            plan = _plan(stores, request.identity.plan_id)
            events = _events_for_run(stores, run.run_id)
            event_kind = _require_first_start(
                command,
                request,
                run,
                latest_run,
                plan,
                events,
            )
            activity = plan.plan.activity(command.intent.activity_id)
            expected_operation = (
                activity.operation
                if event_kind is ActivityEventKind.STEP_STARTED
                else activity.compensation.operation
            )
            source = command.intent.source
            if (
                source.workspace_id != request.identity.workspace_id
                or source.plan_id != plan.plan_id
                or source.base_graph_id != plan.base_graph_id
                or source.desired_graph_id != plan.desired_graph_id
                or command.intent.activity_id != activity.activity_id
                or command.intent.operation != expected_operation
            ):
                raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)
            observation = _observation(stores, request.identity.request_id)
            if observation.request != request:
                raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)
            if observation.expired:
                raise EffectAttemptStartDenied(_AUTHORITY_ERROR)
            event_ordinal = stores.execution.next_event_ordinal(run.run_id)
            result = self._plan_result(
                command,
                fence,
                event_kind,
                observed_at=observation.observed_at,
                event_ordinal=event_ordinal,
            )
            event = result.attempt.original_start_event
            intent_record = EffectAttemptIntentRecord(
                result.attempt.state.identity,
                event,
                command.intent,
            )
            if stores.execution.add_event(event) != event:
                raise EffectAttemptStartConflict(_SERIALIZATION_ERROR)
            if stores.effect_attempt_intents.insert(intent_record) != intent_record:
                raise EffectAttemptStartConflict(_SERIALIZATION_ERROR)
            if stores.effect_attempts.insert_absent(result.attempt) != result.attempt:
                raise EffectAttemptStartConflict(_SERIALIZATION_ERROR)
            unit_of_work.commit()
            return result

    def _plan_result(
        self,
        command: StartEffectAttempt,
        fence: EffectAttemptFence,
        event_kind: ActivityEventKind,
        *,
        observed_at: str,
        event_ordinal: int,
    ) -> NewlyStarted:
        state = fold_effect_attempt(
            None,
            command.transition,
            fence=fence,
        )
        identity = state.identity
        evidence = BoundedEvidence.from_mapping(
            {
                "effect_attempt": EffectAttemptEventEvidence(
                    identity.attempt,
                    effect_attempt_state_fingerprint(state),
                ).descriptor()
            }
        )
        event = ActivityEventRecord(
            self._id_factory(),
            identity.run_id.value,
            event_ordinal,
            event_kind,
            observed_at,
            activity_id=identity.activity_id,
            evidence=evidence,
        )
        record = EffectAttemptRecord(state, event, event)
        return NewlyStarted(record)


def _translate_fence(command: StartEffectAttempt) -> EffectAttemptFence:
    worker_id = command.fence.worker_id
    generation = command.fence.generation
    if not _representable_effect_fence(worker_id, generation):
        raise InvalidOperationCommand(
            "execution lease fence cannot identify an effect attempt"
        )
    return EffectAttemptFence(worker_id, generation)


def _representable_effect_fence(worker_id: object, generation: object) -> bool:
    if (
        type(worker_id) is not str
        or not worker_id.strip()
        or len(worker_id) > 256
        or any(ord(character) < 32 for character in worker_id)
        or type(generation) is not int
        or not 1 <= generation <= 2**63 - 1
    ):
        return False
    try:
        worker_id.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _request_for_update(stores: Any, request_id: str):
    try:
        request = stores.execution.get_request_for_update(request_id)
    except KeyError:
        failure = "missing"
    except (OperationsRecordError, ValueError):
        failure = "invalid"
    else:
        return request
    if failure == "missing":
        raise EffectAttemptStartNotFound(_NOT_FOUND_ERROR)
    raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)


def _run_for_request_for_update(stores: Any, request_id: str, run_id: str):
    try:
        run = stores.execution.get_run_for_request_for_update(request_id, run_id)
    except KeyError:
        failure = "missing"
    except (OperationsRecordError, ValueError):
        failure = "invalid"
    else:
        return run
    if failure == "missing":
        raise EffectAttemptStartNotFound(_NOT_FOUND_ERROR)
    raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)


def _attempt_for_update(stores: Any, identity: Any):
    try:
        attempt = stores.effect_attempts.get_for_update(identity)
    except KeyError:
        return None
    except (OperationsRecordError, ValueError):
        pass
    else:
        return attempt
    raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)


def _latest_run_for_update(stores: Any, request_id: str):
    try:
        run = stores.execution.get_latest_run_for_request_for_update(request_id)
    except KeyError:
        failure = "missing"
    except (OperationsRecordError, ValueError):
        failure = "invalid"
    else:
        return run
    if failure == "missing":
        raise EffectAttemptStartNotFound(_NOT_FOUND_ERROR)
    raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)


def _plan(stores: Any, plan_id: str):
    try:
        plan = stores.activity_history.get_plan(plan_id)
    except KeyError:
        failure = "missing"
    except (OperationsRecordError, ValueError):
        failure = "invalid"
    else:
        return plan
    if failure == "missing":
        raise EffectAttemptStartNotFound(_NOT_FOUND_ERROR)
    raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)


def _events_for_run(stores: Any, run_id: str):
    try:
        events = stores.execution.events_for_run(run_id)
    except (OperationsRecordError, ValueError):
        pass
    else:
        return events
    raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)


def _observation(stores: Any, request_id: str):
    try:
        observation = stores.execution.observe_request_lease_for_update(request_id)
    except (OperationsRecordError, ValueError):
        pass
    else:
        return observation
    raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)


def _require_request_run(command: StartEffectAttempt, request: Any, run: Any) -> None:
    if (
        request.identity.request_id != command.request_id
        or run.run_id != command.transition.identity.run_id.value
        or run.admission.request_id != request.identity.request_id
        or run.plan_id != request.identity.plan_id
    ):
        raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)


def _require_current_authority(command: StartEffectAttempt, request: Any) -> None:
    claim = request.claim
    if (
        request.status is not ExecutionRequestStatus.CLAIMED
        or claim is None
        or claim.fence != command.fence
    ):
        raise EffectAttemptStartDenied(_AUTHORITY_ERROR)


def _require_replay(
    command: StartEffectAttempt,
    fence: EffectAttemptFence,
    request: Any,
    run: Any,
    attempt: EffectAttemptRecord,
) -> None:
    state = attempt.state
    transition = command.transition
    if (
        request.identity.request_id != command.request_id
        or run.admission.request_id != request.identity.request_id
        or run.plan_id != request.identity.plan_id
        or state.identity != transition.identity
        or state.request_fingerprint != transition.request_fingerprint
        or state.prior_attempt != transition.prior_attempt
        or state.fence != fence
    ):
        raise EffectAttemptStartConflict(_REPLAY_ERROR)


def _require_intent_replay(
    stores: Any,
    command: StartEffectAttempt,
    attempt: EffectAttemptRecord,
) -> None:
    try:
        expected = EffectAttemptIntentRecord(
            attempt.state.identity,
            attempt.original_start_event,
            command.intent,
        )
        observed = stores.effect_attempt_intents.get(attempt.state.identity)
    except (KeyError, OperationsRecordError, ValueError):
        failed = True
    else:
        failed = observed != expected
    if failed:
        raise EffectAttemptStartConflict(_REPLAY_ERROR)


def _require_first_start(
    command: StartEffectAttempt,
    request: Any,
    run: Any,
    latest_run: Any,
    plan: Any,
    events: Any,
) -> ActivityEventKind:
    if (
        latest_run != run
        or plan.plan_id != request.identity.plan_id
        or plan.session_id != request.identity.session_id
    ):
        raise EffectAttemptStartConflict(_ELIGIBILITY_ERROR)
    try:
        projection = project_activity_journal(
            plan.plan,
            activity_journal_events(events),
        )
        schedule = derive_schedule(plan.plan, projection.state)
    except (SagaJournalError, SagaStateError):
        projection_failure = True
    else:
        projection_failure = False
    if projection_failure:
        raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)

    activity_id = command.transition.identity.activity_id
    ready = tuple(
        activity.activity_id.value
        for activity in schedule.ready
        if activity.activity_id.value == activity_id
    )
    compensation_ready = tuple(
        activity.activity_id.value
        for activity in schedule.compensation_ready
        if activity.activity_id.value == activity_id
    )
    if (
        ready == (activity_id,)
        and not compensation_ready
        and run.status is ActivityRunStatus.RUNNING
    ):
        return ActivityEventKind.STEP_STARTED
    if (
        compensation_ready == (activity_id,)
        and not ready
        and run.status is ActivityRunStatus.COMPENSATING
    ):
        return ActivityEventKind.STEP_COMPENSATION_STARTED
    raise EffectAttemptStartConflict(_ELIGIBILITY_ERROR)


__all__ = ["EffectAttemptStartService"]
