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
    GuardedObservedEffectFold,
    NewlyFolded,
    _valid_fold_command,
    _valid_guarded_observed_fold,
)
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptEventEvidence,
    EffectAttemptRecord,
    effect_attempt_state_fingerprint,
)
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord,
    ObservedEffectOutcome,
    effect_outcome_observation_records,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    ObservationRecord,
    OperationsRecordError,
)
from control_plane_kit_operations.runtime_authorities import (
    RegisteredRuntimeAuthority,
    RuntimeAuthorityNotFound,
    RuntimeAuthorityRegistrationError,
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
        return _execute_fold(self, command, None)

    def execute_observed(
        self,
        command: GuardedObservedEffectFold,
    ) -> EffectAttemptFoldResult:
        if not _valid_guarded_observed_fold(command):
            raise InvalidOperationCommand(
                "guarded observed effect fold command is invalid"
            )
        return _execute_fold(self, command.fold, command)

    def _plan_result(
        self,
        command: FoldEffectAttempt,
        current: EffectAttemptRecord,
        next_state: EffectAttemptState,
        *,
        observed_at: str,
        event_ordinal: int,
        workspace_id: str,
    ) -> NewlyFolded:
        identity = next_state.identity
        identifiers = ()
        endpoints = () if command.outcome is None else command.outcome.endpoint_observations
        for _ in (None, *endpoints):
            identifiers = (*identifiers, self._id_factory())
        invalid = False
        seen = {}
        for identifier in identifiers:
            if type(identifier) is not str:
                invalid = True
            elif identifier in seen:
                invalid = True
            else:
                seen[identifier] = None

        result = None
        if not invalid:
            try:
                event = ActivityEventRecord(
                    identifiers[0],
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
                attempt = EffectAttemptRecord(
                    next_state,
                    current.original_start_event,
                    event,
                )
                outcome_record = None
                if command.outcome is not None:
                    endpoint_observations = effect_outcome_observation_records(
                        command.outcome,
                        attempt,
                        workspace_id=workspace_id,
                        observation_ids=identifiers[1:],
                    )
                    outcome_record = EffectAttemptOutcomeRecord(
                        workspace_id,
                        command.outcome,
                        attempt,
                        endpoint_observations,
                    )
                result = NewlyFolded(attempt, outcome_record)
            except OperationsRecordError:
                pass
        if result is None:
            raise EffectAttemptFoldConflict(_SERIALIZATION_ERROR)
        return result


def _execute_fold(
    self: EffectAttemptFoldService,
    command: FoldEffectAttempt,
    guarded: GuardedObservedEffectFold | None,
) -> EffectAttemptFoldResult:
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
        if guarded is None and type(command.outcome) is ObservedEffectOutcome:
            raise EffectAttemptFoldConflict(_REPLAY_ERROR)
        _require_transition_authority(command, fence, attempt, guarded is not None)
        next_state = _fold(command, attempt)
        if next_state == attempt.state:
            replay_error = None
            if not _require_exact_replay(command, attempt):
                replay_error = _REPLAY_ERROR
            outcome_record = None
            if replay_error is None and command.outcome is not None:
                try:
                    outcome_record = stores.effect_outcomes.get(
                        attempt.state.identity,
                        attempt.latest_transition_event.event_id,
                    )
                except (KeyError, OperationsRecordError):
                    replay_error = _INVALID_TRUTH_ERROR
                else:
                    if (
                        type(outcome_record) is not EffectAttemptOutcomeRecord
                        or outcome_record.workspace_id
                        != request.identity.workspace_id
                        or outcome_record.attempt != attempt
                    ):
                        replay_error = _INVALID_TRUTH_ERROR
                    elif outcome_record.outcome != command.outcome:
                        replay_error = _REPLAY_ERROR
            result = None
            if replay_error is None:
                try:
                    result = ExistingFold(attempt, outcome_record)
                except OperationsRecordError:
                    replay_error = _INVALID_TRUTH_ERROR
            if replay_error is not None:
                raise EffectAttemptFoldConflict(replay_error)
        else:
            invalid_truth = False
            denied = False
            intent_record = None
            if guarded is not None:
                try:
                    intent_record = stores.effect_attempt_intents.get(
                        attempt.state.identity
                    )
                except (KeyError, OperationsRecordError):
                    invalid_truth = True
                else:
                    invalid_truth = (
                        type(intent_record) is not EffectAttemptIntentRecord
                        or intent_record != guarded.intent_record
                        or intent_record.original_start_event
                        != attempt.original_start_event
                    )
            observation = None
            if not invalid_truth:
                observation = _observation(stores, command.request_id)
                invalid_truth = observation.request != request
                denied = guarded is not None and observation.expired
            if (
                not invalid_truth
                and not denied
                and guarded is not None
                and guarded.runtime_authority is not None
            ):
                try:
                    active_authority = (
                        stores.runtime_authorities.get_active_for_update(
                            request.identity.workspace_id,
                            intent_record.intent.authority_ref,
                        )
                    )
                except RuntimeAuthorityNotFound:
                    denied = True
                except RuntimeAuthorityRegistrationError:
                    invalid_truth = True
                else:
                    if (
                        type(active_authority) is not RegisteredRuntimeAuthority
                        or active_authority.runtime_kind
                        is not guarded.runtime_authority.runtime_kind
                        or active_authority.status
                        is not guarded.runtime_authority.status
                    ):
                        invalid_truth = True
                    elif active_authority != guarded.runtime_authority:
                        denied = True
            if invalid_truth:
                raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)
            if denied:
                raise EffectAttemptFoldDenied(_AUTHORITY_ERROR)
            event_ordinal = stores.execution.next_event_ordinal(run.run_id)
            result = self._plan_result(
                command,
                attempt,
                next_state,
                observed_at=observation.observed_at,
                event_ordinal=event_ordinal,
                workspace_id=request.identity.workspace_id,
            )
            event = result.attempt.latest_transition_event
            event_acknowledgement = stores.execution.add_event(event)
            changed = (
                type(event_acknowledgement) is not ActivityEventRecord
                or event_acknowledgement != event
            )
            if not changed and result.outcome_record is not None:
                for endpoint_observation in result.outcome_record.endpoint_observations:
                    observation_acknowledgement = stores.observed_state.put(
                        endpoint_observation
                    )
                    if (
                        type(observation_acknowledgement) is not ObservationRecord
                        or observation_acknowledgement != endpoint_observation
                    ):
                        changed = True
                        break
                if not changed:
                    outcome_acknowledgement = stores.effect_outcomes.insert(
                        result.outcome_record
                    )
                    changed = (
                        type(outcome_acknowledgement)
                        is not EffectAttemptOutcomeRecord
                        or outcome_acknowledgement != result.outcome_record
                    )
            if not changed:
                attempt_acknowledgement = stores.effect_attempts.compare_and_set(
                    attempt,
                    result.attempt,
                )
                changed = (
                    type(attempt_acknowledgement) is not EffectAttemptRecord
                    or attempt_acknowledgement != result.attempt
                )
            if changed:
                raise EffectAttemptFoldConflict(_SERIALIZATION_ERROR)
        unit_of_work.commit()
        return result


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
    observed: bool,
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
    direct_invalid = attempt.state.recovery_decision is not None
    if (
        not direct_invalid
        and (attempt.state.status is not EffectAttemptStatus.STARTED or observed)
    ):
        direct_invalid = fence.generation < historical.generation or (
            fence.generation == historical.generation
            and fence.worker_id != historical.worker_id
        )
    elif not direct_invalid:
        direct_invalid = fence != historical
    if direct_invalid:
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
) -> bool:
    return attempt.latest_transition_event.failure == command.failure


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
