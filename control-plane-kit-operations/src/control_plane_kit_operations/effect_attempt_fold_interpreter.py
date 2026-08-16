"""Transactional interpreter for one effect-attempt fold."""

from __future__ import annotations

from typing import Any, Callable

from control_plane_kit_core.operations import (
    EffectAttemptFence,
    EffectAttemptState,
    EffectAttemptStatus,
    EffectAttemptTransitionKind,
    InvalidEffectRecoveryContract,
    fold_effect_attempt,
)
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ExecutionRequestStatus,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    EffectAttemptFoldNotFound,
    EffectAttemptFoldResult,
    ExistingFold,
    FoldEffectAttempt,
    NewlyFolded,
    _valid_fold_command,
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


_AUTHORITY_ERROR = "effect attempt fold authority is invalid"
_INVALID_TRUTH_ERROR = "effect attempt fold truth is invalid"
_NOT_FOUND_ERROR = "effect attempt fold truth was not found"
_REPLAY_ERROR = "effect attempt fold is incongruent"
_SERIALIZATION_ERROR = "effect attempt fold changed concurrently"


class EffectAttemptFoldService:
    """Fold or observe one exact effect attempt in a caller-owned UoW."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._id_factory = id_factory

    def execute(self, command: FoldEffectAttempt) -> EffectAttemptFoldResult:
        if not _valid_fold_command(command):
            raise InvalidOperationCommand("effect attempt fold command is invalid")
        if PolicyScope.EXECUTION_OPERATE not in command.authority.scopes:
            raise EffectAttemptFoldDenied("scope execution:operate is missing")
        fence = _translate_fence(command)

        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            request = _request_for_update(stores, command.request_id)
            run = _run_for_request_for_update(
                stores,
                command.request_id,
                command.transition.identity.run_id.value,
            )
            attempt = _attempt_for_update(stores, command.transition.identity)
            _require_request_run(command, request, run, attempt)
            _require_current_authority(command, request)
            _require_transition_authority(command, fence, attempt)
            next_state = _fold(command, attempt)
            if next_state == attempt.state:
                _require_exact_replay(command, attempt)
                result = ExistingFold(attempt)
                unit_of_work.commit()
                return result

            observation = _observation(stores, command.request_id)
            if observation.request != request:
                raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)
            event_ordinal = stores.execution.next_event_ordinal(run.run_id)
            result = self._plan_result(
                command,
                attempt,
                next_state,
                observed_at=observation.observed_at,
                event_ordinal=event_ordinal,
            )
            event = result.attempt.latest_transition_event
            if stores.execution.add_event(event) != event:
                raise EffectAttemptFoldConflict(_SERIALIZATION_ERROR)
            if (
                stores.effect_attempts.compare_and_set(attempt, result.attempt)
                != result.attempt
            ):
                raise EffectAttemptFoldConflict(_SERIALIZATION_ERROR)
            unit_of_work.commit()
            return result

    def _plan_result(
        self,
        command: FoldEffectAttempt,
        current: EffectAttemptRecord,
        next_state: EffectAttemptState,
        *,
        observed_at: str,
        event_ordinal: int,
    ) -> NewlyFolded:
        identity = next_state.identity
        event = ActivityEventRecord(
            self._id_factory(),
            identity.run_id.value,
            event_ordinal,
            _event_kind(current, next_state),
            observed_at,
            activity_id=identity.activity_id,
            evidence=BoundedEvidence.from_mapping(
                {
                    "effect_attempt": EffectAttemptEventEvidence(
                        identity.attempt,
                        effect_attempt_state_fingerprint(next_state),
                    ).descriptor()
                }
            ),
            failure=command.failure,
        )
        return NewlyFolded(
            EffectAttemptRecord(next_state, current.original_start_event, event)
        )


def _translate_fence(command: FoldEffectAttempt) -> EffectAttemptFence:
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
        or "\x00" in worker_id
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
        raise EffectAttemptFoldNotFound(_NOT_FOUND_ERROR)
    raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)


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
        raise EffectAttemptFoldNotFound(_NOT_FOUND_ERROR)
    raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)


def _attempt_for_update(stores: Any, identity: Any) -> EffectAttemptRecord:
    try:
        attempt = stores.effect_attempts.get_for_update(identity)
    except KeyError:
        failure = "missing"
    except (OperationsRecordError, ValueError):
        failure = "invalid"
    else:
        return attempt
    if failure == "missing":
        raise EffectAttemptFoldNotFound(_NOT_FOUND_ERROR)
    raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)


def _observation(stores: Any, request_id: str):
    try:
        observation = stores.execution.observe_request_lease_for_update(request_id)
    except (OperationsRecordError, ValueError):
        pass
    else:
        return observation
    raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)


def _require_request_run(
    command: FoldEffectAttempt,
    request: Any,
    run: Any,
    attempt: EffectAttemptRecord,
) -> None:
    transition = command.transition
    if (
        request.identity.request_id != command.request_id
        or run.run_id != transition.identity.run_id.value
        or run.admission.request_id != request.identity.request_id
        or run.plan_id != request.identity.plan_id
        or attempt.state.identity != transition.identity
    ):
        raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)


def _require_current_authority(command: FoldEffectAttempt, request: Any) -> None:
    claim = request.claim
    if (
        request.status is not ExecutionRequestStatus.CLAIMED
        or claim is None
        or claim.fence != command.fence
    ):
        raise EffectAttemptFoldDenied(_AUTHORITY_ERROR)


def _require_transition_authority(
    command: FoldEffectAttempt,
    fence: EffectAttemptFence,
    attempt: EffectAttemptRecord,
) -> None:
    historical = attempt.state.fence
    if command.transition.kind in {
        EffectAttemptTransitionKind.RECONCILED,
        EffectAttemptTransitionKind.ABANDONED,
    }:
        if fence.generation < historical.generation or (
            fence.generation == historical.generation
            and fence.worker_id != historical.worker_id
        ):
            raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)
        return
    if fence != historical:
        raise EffectAttemptFoldConflict(_REPLAY_ERROR)


def _fold(
    command: FoldEffectAttempt,
    attempt: EffectAttemptRecord,
) -> EffectAttemptState:
    next_state = None
    try:
        next_state = fold_effect_attempt(
            attempt.state,
            command.transition,
            fence=attempt.state.fence,
        )
    except InvalidEffectRecoveryContract:
        pass
    if next_state is None:
        raise EffectAttemptFoldConflict(_REPLAY_ERROR)
    return next_state


def _require_exact_replay(
    command: FoldEffectAttempt,
    attempt: EffectAttemptRecord,
) -> None:
    if attempt.latest_transition_event.failure != command.failure:
        raise EffectAttemptFoldConflict(_REPLAY_ERROR)


def _event_kind(
    current: EffectAttemptRecord,
    next_state: EffectAttemptState,
) -> ActivityEventKind:
    compensation = (
        current.original_start_event.kind
        is ActivityEventKind.STEP_COMPENSATION_STARTED
    )
    key = (
        compensation,
        next_state.status,
        next_state.recovery_decision is not None,
    )
    kind = _EVENT_KIND_BY_STATE.get(key)
    if kind is None:
        raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)
    return kind


_EVENT_KIND_BY_STATE = {
    (False, EffectAttemptStatus.SUCCEEDED, False): ActivityEventKind.STEP_SUCCEEDED,
    (False, EffectAttemptStatus.FAILED, False): ActivityEventKind.STEP_FAILED,
    (False, EffectAttemptStatus.UNSUPPORTED, False): ActivityEventKind.STEP_UNSUPPORTED,
    (False, EffectAttemptStatus.UNCERTAIN, False): ActivityEventKind.STEP_UNCERTAIN,
    (False, EffectAttemptStatus.SUCCEEDED, True): (
        ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED
    ),
    (False, EffectAttemptStatus.FAILED, True): (
        ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED
    ),
    (False, EffectAttemptStatus.ABANDONED, True): (
        ActivityEventKind.STEP_UNCERTAINTY_ABANDONED
    ),
    (True, EffectAttemptStatus.SUCCEEDED, False): (
        ActivityEventKind.STEP_COMPENSATION_SUCCEEDED
    ),
    (True, EffectAttemptStatus.FAILED, False): (
        ActivityEventKind.STEP_COMPENSATION_FAILED
    ),
    (True, EffectAttemptStatus.UNSUPPORTED, False): (
        ActivityEventKind.STEP_COMPENSATION_UNSUPPORTED
    ),
    (True, EffectAttemptStatus.UNCERTAIN, False): (
        ActivityEventKind.STEP_COMPENSATION_UNCERTAIN
    ),
    (True, EffectAttemptStatus.SUCCEEDED, True): (
        ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED
    ),
    (True, EffectAttemptStatus.FAILED, True): (
        ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED
    ),
    (True, EffectAttemptStatus.ABANDONED, True): (
        ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_ABANDONED
    ),
}


__all__ = ["EffectAttemptFoldService"]
