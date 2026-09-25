"""Transactional interpreter for one effect-attempt fold."""

from __future__ import annotations

from dataclasses import dataclass
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
    ActivityRunStatus,
    ExecutionRequestStatus,
)
from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.policies import ApprovalPolicy, PolicyScope
from control_plane_kit_core.planning import resolve_management_observation
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_core.runtime_effects import RuntimeEffectResult, RuntimeEffectFailure
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    EffectAttemptFoldNotFound,
    EffectAttemptFoldResult,
    ExistingFold,
    FoldEffectAttempt,
    FoldNativeConnectionObservation,
    GuardedObservedEffectFold,
    GuardedHealthEffectFold,
    NewlyFolded,
    _valid_fold_command,
    _valid_guarded_observed_fold,
    _valid_native_fold,
    _valid_health_fold,
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
    ExecutionEffectOutcome,
    NativeConnectionEffectOutcome,
    NativeConnectionObservation,
    NativeConnectionRefused,
    ObservedEffectOutcome,
    _legacy_effect_outcome_failure,
    effect_outcome_failure,
    effect_outcome_observation_records,
    effect_outcome_transition,
)
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile
from control_plane_kit_operations.runtime_management_targets import (
    is_native_connection_operation, is_signed_management_health_operation,
)
from control_plane_kit_operations.health_signing_authority import (
    HealthSigningAuthorityReloadService, ReloadHealthSigningAuthority,
)
from control_plane_kit_operations.runtime_management_admission import runtime_management_execution_is_unsupported
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanStatus,
    ApprovalDecisionKind,
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

    def execute_native(
        self, command: FoldNativeConnectionObservation,
    ) -> EffectAttemptFoldResult:
        if not _valid_native_fold(command):
            raise InvalidOperationCommand("native connection fold command is invalid")
        required = (PolicyScope.NODE_CONTROL_READ, PolicyScope.NODE_CONTROL_EXECUTE)
        if any(scope not in command.context.granted_scopes for scope in required):
            raise EffectAttemptFoldDenied(_AUTHORITY_ERROR)
        return _execute_fold(self, command, None)

    def execute_health(self, command: GuardedHealthEffectFold, *,
            signing_authority: HealthSigningAuthorityReloadService) -> EffectAttemptFoldResult:
        if (not _valid_health_fold(command)
                or type(signing_authority) is not HealthSigningAuthorityReloadService):
            raise InvalidOperationCommand("signed health fold command is invalid")
        required = (PolicyScope.NODE_CONTROL_READ, PolicyScope.NODE_CONTROL_EXECUTE,
            PolicyScope.DELEGATION_KEY_USE, PolicyScope.SECRET_PROVIDER_USE)
        if any(scope not in command.context.granted_scopes for scope in required):
            raise EffectAttemptFoldDenied(_AUTHORITY_ERROR)
        return _execute_fold(self, command.fold, None, command, signing_authority)

    def _plan_result(
        self,
        command: FoldEffectAttempt,
        current: EffectAttemptRecord,
        next_state: EffectAttemptState,
        *,
        observed_at: str,
        event_ordinal: int,
        workspace_id: str,
        intent_record: EffectAttemptIntentRecord,
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
                        intent_record=intent_record,
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
    command: FoldEffectAttempt | FoldNativeConnectionObservation,
    guarded: GuardedObservedEffectFold | None,
    health: GuardedHealthEffectFold | None = None,
    signing_authority: HealthSigningAuthorityReloadService | None = None,
) -> EffectAttemptFoldResult:
    if PolicyScope.EXECUTION_OPERATE not in command.authority.scopes:
        raise EffectAttemptFoldDenied("scope execution:operate is missing")
    fence = _translate_fence(command)
    native = command if type(command) is FoldNativeConnectionObservation else None
    identity = native.identity if native is not None else command.transition.identity

    with self._unit_of_work_factory() as unit_of_work:
        stores = unit_of_work.stores
        request = _request_for_update(stores, command.request_id)
        if native is not None and native.context.workspace_id != request.identity.workspace_id:
            raise EffectAttemptFoldDenied(_AUTHORITY_ERROR)
        if health is not None and health.context.workspace_id != request.identity.workspace_id:
            raise EffectAttemptFoldDenied(_AUTHORITY_ERROR)
        run = _run_for_request_for_update(
            stores,
            command.request_id,
            identity.run_id.value,
        )
        attempt = _attempt_for_update(stores, identity)
        _require_current_authority(command, request)
        native_intent, native_clock = None, None
        if native is not None:
            command, native_intent, native_clock = _prepare_native_fold(
                stores, native, request, run, attempt, fence)
        _require_request_run(command, request, run, attempt)
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
            try:
                intent_record = stores.effect_attempt_intents.get(
                    attempt.state.identity
                )
            except (KeyError, OperationsRecordError):
                invalid_truth = True
            else:
                invalid_truth = (
                    type(intent_record) is not EffectAttemptIntentRecord
                    or intent_record.identity != attempt.state.identity
                    or intent_record.original_start_event
                    != attempt.original_start_event
                    or intent_record.request_id != command.request_id
                    or intent_record.request_fingerprint
                    != attempt.state.request_fingerprint
                    or (
                        guarded is not None
                        and intent_record != guarded.intent_record
                    )
                    or (health is not None and intent_record != health.intent_record)
                )
            observation = None
            if (not invalid_truth and native is None
                    and is_native_connection_operation(intent_record.intent.operation)):
                # Generic mutation success is not native connection evidence.
                # A separate guarded native refusal/uncertainty entrance owns
                # unsuccessful dispatch results as well.
                raise EffectAttemptFoldDenied(_AUTHORITY_ERROR)
            if (not invalid_truth and health is None
                    and is_signed_management_health_operation(intent_record.intent.operation)):
                raise EffectAttemptFoldDenied(_AUTHORITY_ERROR)
            health_clock = None
            if not invalid_truth and health is not None:
                _require_managed_plan(stores, intent_record, request)
                try:
                    current_runtime = stores.runtime_authorities.get_active_for_update(
                        request.identity.workspace_id, intent_record.intent.authority_ref)
                except RuntimeAuthorityNotFound:
                    raise EffectAttemptFoldDenied(_AUTHORITY_ERROR) from None
                if current_runtime != health.runtime_authority:
                    raise EffectAttemptFoldDenied(_AUTHORITY_ERROR)
                authority, health_clock = signing_authority.in_unit_of_work(unit_of_work,
                    ReloadHealthSigningAuthority(command.request_id, attempt.state.identity,
                        health.context, command.authority, command.fence))
                if authority.preparation != health.preparation:
                    raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)
            if not invalid_truth:
                observation = (native_clock if native is not None else health_clock
                    if health is not None else _observation(stores, command.request_id))
                invalid_truth = observation.request != request
                denied = (guarded is not None or native is not None or health is not None) and observation.expired
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
                intent_record=intent_record,
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


@dataclass(frozen=True)
class _AcceptedNativeFold:
    """Private normalized fold, constructed only under the durable guard."""

    request_id: str
    authority: object
    fence: object
    outcome: NativeConnectionEffectOutcome | ExecutionEffectOutcome

    @property
    def transition(self):
        return effect_outcome_transition(self.outcome)

    @property
    def failure(self):
        return effect_outcome_failure(self.outcome)


def _prepare_native_fold(stores, command, request, run, attempt, fence):
    if (attempt.state.identity != command.identity
            or run.admission.request_id != command.request_id
            or run.plan_id != request.identity.plan_id
            or attempt.state.fence != fence
            or attempt.state.recovery_decision is not None):
        raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)
    try:
        intent = stores.effect_attempt_intents.get(command.identity)
    except (KeyError, OperationsRecordError):
        raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR) from None
    if (type(intent) is not EffectAttemptIntentRecord or intent != command.intent_record
            or intent.workspace_id != request.identity.workspace_id
            or intent.original_start_event != attempt.original_start_event
            or intent.request_fingerprint != attempt.state.request_fingerprint):
        raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)

    if attempt.state.status is not EffectAttemptStatus.STARTED:
        # Replay recovers the accepted value; it never asks today's runtime or
        # clock to reinterpret an old sample. The ordinary fold checks it again.
        try:
            retained = stores.effect_outcomes.get(command.identity,
                attempt.latest_transition_event.event_id)
        except (KeyError, OperationsRecordError):
            raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR) from None
        if (type(retained) is not EffectAttemptOutcomeRecord
                or retained.attempt != attempt
                or retained.workspace_id != request.identity.workspace_id):
            raise EffectAttemptFoldConflict(_REPLAY_ERROR)
        if type(command.observation) is NativeConnectionObservation:
            matches = (type(retained.outcome) is NativeConnectionEffectOutcome
                and retained.outcome.observation == command.observation)
        else:
            matches = retained.outcome == _native_read_failure_outcome(command, intent)
        if not matches:
            raise EffectAttemptFoldConflict(_REPLAY_ERROR)
        outcome, clock = retained.outcome, None
    else:
        if (run.status is not ActivityRunStatus.RUNNING
                or stores.execution.get_latest_run_for_request_for_update(command.request_id) != run):
            raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)
        _require_native_plan(stores, intent, request)
        try:
            active = stores.runtime_authorities.get_active_for_update(
                request.identity.workspace_id, intent.intent.authority_ref)
        except RuntimeAuthorityNotFound:
            raise EffectAttemptFoldDenied(_AUTHORITY_ERROR) from None
        except RuntimeAuthorityRegistrationError:
            raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR) from None
        if active != command.runtime_authority:
            raise EffectAttemptFoldDenied(_AUTHORITY_ERROR)
        clock = _observation(stores, command.request_id)
        if clock.request != request:
            raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)
        if clock.expired:
            raise EffectAttemptFoldDenied(_AUTHORITY_ERROR)
        outcome = None
        try:
            if type(command.observation) is NativeConnectionObservation:
                outcome = NativeConnectionEffectOutcome.from_observation(
                    identity=command.identity, request_fingerprint=intent.request_fingerprint,
                    observation=command.observation, accepted_at=clock.observed_at)
            else:
                outcome = _native_read_failure_outcome(command, intent)
        except (ValueError, TypeError):
            pass
        if outcome is None:
            raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)
    return _AcceptedNativeFold(command.request_id, command.authority, command.fence, outcome), intent, clock


def _native_read_failure_outcome(command, intent):
    refused = type(command.observation) is NativeConnectionRefused
    result = (RuntimeEffectResult.unsupported if refused else RuntimeEffectResult.uncertain)(
        intent.original_start_event.event_id, RuntimeEffectFailure(
            "native-read-refused" if refused else "native-read-result-unknown",
            "native reader did not produce a completed correlated observation"))
    return ExecutionEffectOutcome(command.identity, intent.request_fingerprint, result)


def _require_native_plan(stores, intent_record, request):
    if not is_native_connection_operation(intent_record.intent.operation):
        raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)
    return _require_managed_plan(stores, intent_record, request)


def _require_managed_plan(stores, intent_record, request):
    """Re-resolve the original accepted operation from durable graph pins."""
    valid = False
    try:
        intent = intent_record.intent
        plan = stores.activity_history.get_plan(request.identity.plan_id)
        source = intent.source
        if (plan.derivation_profile is not PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1
                or plan.status is not ActivityPlanStatus.PLANNED
                or plan.session_id != request.identity.session_id
                or source.plan_id != plan.plan_id
                or source.base_graph_id != plan.base_graph_id
                or source.desired_graph_id != plan.desired_graph_id
                or not (is_native_connection_operation(intent.operation)
                    or is_signed_management_health_operation(intent.operation))):
            raise ValueError
        graphs = []
        for projection_id, authored_id in ((plan.base_realized_projection_id, plan.base_graph_id),
                (plan.desired_realized_projection_id, plan.desired_graph_id)):
            projection = stores.realized_graphs.get(projection_id)
            authored = stores.graphs.get(authored_id)
            if (projection.workspace_id != request.identity.workspace_id
                    or projection.projection_id != projection_id
                    or projection.source_authored_graph_id != authored_id
                    or authored.workspace_id != request.identity.workspace_id
                    or authored.graph_id != authored_id):
                raise ValueError
            graph = validate_graph(DEFAULT_GRAPH_CODEC.decode(projection.graph_descriptor))
            graph.require_valid()
            graphs.append(graph)
        if runtime_management_execution_is_unsupported(graphs[0].graph, graphs[1].graph,
                plan.plan, derivation_profile=plan.derivation_profile):
            raise ValueError
        approval = stores.activity_history.get_approval_request(request.approval_request_id)
        decision = stores.activity_history.approval_decision_for_request(approval.request_id)
        requirement = ApprovalPolicy().requirement_for(plan.plan)
        if (type(approval.subject) is not ActivityPlanApprovalSubject
                or approval.request_id != request.approval_request_id
                or approval.session_id != request.identity.session_id
                or approval.subject.plan_id != plan.plan_id
                or decision.decision_id != request.approval_decision_id
                or decision.request_id != approval.request_id
                or decision.decision is not ApprovalDecisionKind.APPROVED
                or decision.scope is not approval.required_scope
                or (approval.required_scope, approval.destructive, approval.max_risk)
                    != (requirement.required_scope, requirement.destructive, requirement.max_risk)):
            raise ValueError
        resolved = resolve_management_observation(intent.operation, *graphs,
            expected_operation=plan.plan.activity(intent.activity_id).operation)
        runtime = resolved.selected_graph.graph.runtimes[intent.operation.target.runtime_id]
        valid = (runtime.authority_ref == intent.authority_ref
            and runtime.kind is intent.runtime_kind)
    except (KeyError, ValueError, TypeError, AttributeError):
        pass
    if not valid:
        raise EffectAttemptFoldConflict(_INVALID_TRUTH_ERROR)


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
    recorded_failure = attempt.latest_transition_event.failure
    if recorded_failure == command.failure:
        return True
    if command.outcome is None or command.failure is None:
        return False
    legacy_failure = _legacy_effect_outcome_failure(command.outcome)
    return (
        command.failure == effect_outcome_failure(command.outcome)
        and legacy_failure is not None
        and legacy_failure != command.failure
        and recorded_failure == legacy_failure
    )


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
    (False, EffectAttemptStatus.NOT_READY, False): ActivityEventKind.STEP_OBSERVATION_NOT_READY,
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
