"""Transactional interpreter for one effect-attempt start."""

from __future__ import annotations

from dataclasses import replace
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
from control_plane_kit_operations.runtime_management_targets import is_signed_management_health_operation
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
from control_plane_kit_operations.effect_run_prefix import _lock_effect_run_prefix
from control_plane_kit_operations.health_effect_attempt_start import (
    HealthEffectAttemptStartResult, StartHealthEffectAttempt, _valid_health_command,
)
from control_plane_kit_operations.health_receiver_trust import HealthReceiverDecoders, HealthReceiverTrustError
from control_plane_kit_operations._health_effect_attempt_start import (
    admit_health_start, build_health_start, health_interval, health_replay,
    retain_health_start,
)


_AUTHORITY_ERROR = "effect attempt start authority is invalid"
_ELIGIBILITY_ERROR = "effect attempt start is not eligible"
_INVALID_TRUTH_ERROR = "effect attempt start truth is invalid"
_NOT_FOUND_ERROR = "effect attempt start truth was not found"
_REPLAY_ERROR = "effect attempt replay is incongruent"
_SERIALIZATION_ERROR = "effect attempt start changed concurrently"


class _StartLocatorChanged(Exception):
    """An attempt appeared before locks; restart once outside the UoW."""


class EffectAttemptStartService:
    """Start or observe one exact effect attempt in a caller-owned UoW."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        id_factory: Callable[[], str],
        health_receiver_decoders: HealthReceiverDecoders = HealthReceiverDecoders(()),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._id_factory = id_factory
        if type(health_receiver_decoders) is not HealthReceiverDecoders:
            raise HealthReceiverTrustError("health receiver trust is unavailable")
        self._health_receiver_decoders = replace(health_receiver_decoders)

    def execute(
        self,
        command: StartEffectAttempt,
    ) -> EffectAttemptStartResult:
        return self._execute(command, None)

    def execute_health(
        self,
        command: StartHealthEffectAttempt,
    ) -> HealthEffectAttemptStartResult:
        if not _valid_health_command(command):
            raise InvalidOperationCommand("health effect start command is invalid")
        required = (PolicyScope.NODE_CONTROL_READ, PolicyScope.NODE_CONTROL_EXECUTE,
            PolicyScope.DELEGATION_KEY_USE, PolicyScope.SECRET_PROVIDER_USE)
        if any(scope not in command.context.granted_scopes for scope in required):
            raise EffectAttemptStartDenied("health effect start scope is missing")
        return self._execute(command.start, command)

    def _execute(
        self,
        command: StartEffectAttempt,
        health: StartHealthEffectAttempt | None,
    ) -> EffectAttemptStartResult | HealthEffectAttemptStartResult:
        for pass_number in range(2):
            try:
                return self._execute_once(command, health)
            except _StartLocatorChanged:
                if pass_number:
                    break
        raise EffectAttemptStartConflict(_SERIALIZATION_ERROR)

    def _execute_once(
        self,
        command: StartEffectAttempt,
        health: StartHealthEffectAttempt | None,
    ) -> EffectAttemptStartResult | HealthEffectAttemptStartResult:
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
            try:
                locator = stores.execution.get_request(command.request_id)
            except KeyError:
                locator = None
            except (ValueError, TypeError, AttributeError):
                locator = False
            if locator is None:
                raise EffectAttemptStartNotFound(_NOT_FOUND_ERROR)
            if locator is False:
                raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)
            try:
                located_attempt = stores.effect_attempts.get(command.transition.identity)
            except KeyError:
                located_attempt = None
            except (ValueError, TypeError, AttributeError):
                located_attempt = False
            if located_attempt is False:
                raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)
            guard = (stores.graphs.lock_receiver_lifecycle(locator.identity.workspace_id)
                     if located_attempt is None else None)
            request = _request_for_update(stores, command.request_id)
            if request.identity != locator.identity:
                raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)
            try:
                prefix = _lock_effect_run_prefix(unit_of_work, request,
                    command.transition.identity.run_id.value, latest_required=located_attempt is None)
            except KeyError:
                prefix = False
            except (ValueError, TypeError, AttributeError):
                prefix = None
            if prefix is False:
                raise EffectAttemptStartNotFound(_NOT_FOUND_ERROR)
            if prefix is None:
                raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)
            run = prefix.requested_run
            _require_request_run(command, request, run)
            if health is not None and health.context.workspace_id != request.identity.workspace_id:
                raise EffectAttemptStartDenied(_AUTHORITY_ERROR)
            attempt = _attempt_for_update(stores, command.transition.identity)
            if located_attempt is None and attempt is not None:
                # No writes or IDs have been allocated. Drop the fresh prefix
                # before choosing the winner's ordinary exact replay path.
                raise _StartLocatorChanged
            if (attempt is None) != (located_attempt is None):
                raise EffectAttemptStartConflict(_SERIALIZATION_ERROR)
            _require_current_authority(command, request)
            if attempt is not None:
                _require_replay(command, fence, request, run, attempt)
                _require_intent_replay(stores, command, attempt)
                result = ExistingAttempt(attempt)
                if is_signed_management_health_operation(command.intent.operation):
                    preparation = health_replay(stores, attempt, health)
                    if health is not None:
                        result = HealthEffectAttemptStartResult(result, preparation)
                unit_of_work.commit()
                return result

            if is_signed_management_health_operation(command.intent.operation) and health is None:
                raise EffectAttemptStartDenied("health effect start requires trusted admission")
            latest_run = prefix.latest_run
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
            try:
                session = stores.activity_history.get_session_for_update(request.identity.session_id)
                from control_plane_kit_operations.records import OperationSessionStatus
                if session.status is not OperationSessionStatus.OPEN:
                    raise ValueError("closed execution session")
                if health is not None:
                    # Keep the workspace prefix before health's correlation
                    # locks. Health owns its selected-slot denial contract.
                    stores.workspaces.get_for_update(request.identity.workspace_id)
            except (KeyError, ValueError, TypeError, AttributeError):
                permission_failed = True
            else:
                permission_failed = False
            if permission_failed:
                raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)
            admission = None
            if health is not None:
                admission = admit_health_start(stores, health, request, plan, event_kind,
                    self._health_receiver_decoders)
            try:
                _require_fresh_effect_receiver_permission(stores, request, guard, command.intent,
                    compensation=event_kind is ActivityEventKind.STEP_COMPENSATION_STARTED)
            except (KeyError, ValueError, TypeError, AttributeError):
                permission_failed = True
            else:
                permission_failed = False
            if permission_failed:
                raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)
            observation = _observation(stores, request.identity.request_id)
            if observation.request != request:
                raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)
            if observation.expired:
                raise EffectAttemptStartDenied(_AUTHORITY_ERROR)
            interval = health_interval(observation) if health is not None else None
            event_ordinal = stores.execution.next_event_ordinal(run.run_id)
            result = self._plan_result(
                command,
                fence,
                event_kind,
                observed_at=observation.observed_at,
                event_ordinal=event_ordinal,
            )
            health_write = None
            if health is not None:
                health_write = build_health_start(admission, health, result, interval,
                    self._id_factory(), self._id_factory(), self._id_factory())
            event = result.attempt.original_start_event
            intent_record = EffectAttemptIntentRecord(
                result.attempt.state.identity,
                event,
                command.intent,
            )
            if stores.execution.add_event(event) != event:
                raise EffectAttemptStartConflict(_SERIALIZATION_ERROR)
            intent_acknowledgement = stores.effect_attempt_intents._insert(
                intent_record
            )
            if (
                type(intent_acknowledgement) is not EffectAttemptIntentRecord
                or intent_acknowledgement != intent_record
            ):
                raise EffectAttemptStartConflict(_SERIALIZATION_ERROR)
            expected_attempt = result.attempt
            if stores.effect_attempts._insert_absent(expected_attempt) != expected_attempt:
                raise EffectAttemptStartConflict(_SERIALIZATION_ERROR)
            if health_write is not None:
                result = retain_health_start(unit_of_work, health_write, result)
            try:
                prefix.require(unit_of_work, request, run.run_id, latest_required=True)
                _require_fresh_effect_receiver_permission(stores, request, guard, command.intent,
                    compensation=event_kind is ActivityEventKind.STEP_COMPENSATION_STARTED)
                if (stores.execution.get_event(event.event_id) != event
                        or stores.effect_attempt_intents.get(intent_record.identity) != intent_record
                        or stores.effect_attempts.get(expected_attempt.state.identity) != expected_attempt):
                    raise ValueError("persisted start changed")
            except (KeyError, ValueError, TypeError, AttributeError):
                permission_failed = True
            else:
                permission_failed = False
            if permission_failed:
                raise EffectAttemptStartConflict(_INVALID_TRUTH_ERROR)
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
    except (KeyError, OperationsRecordError):
        failed = True
    else:
        failed = (
            type(observed) is not EffectAttemptIntentRecord
            or observed != expected
        )
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


def _require_fresh_effect_receiver_permission(stores, request, guard, intent, *, compensation):
    from control_plane_kit_operations.receiver_lifecycle import (
        _validate_receiver_execution, _validate_receiver_execution_approval,
    )
    from control_plane_kit_operations.receiver_execution_scopes import _validate_effect_receiver_material
    _validate_receiver_execution(stores, request, guard)
    _validate_receiver_execution_approval(stores, request)
    original, derived = stores.execution._receiver_execution_material(request.identity, guard)
    _validate_effect_receiver_material(request.identity, original, derived, intent, compensation=compensation)


__all__ = ["EffectAttemptStartService"]
