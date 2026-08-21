"""Transactional interpreter for runtime-effect reconciliation."""

from __future__ import annotations

from typing import Any, Callable

from control_plane_kit_core.operations import EffectAttemptStatus
from control_plane_kit_core.operations.lifecycle import ExecutionRequestStatus
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationRequest,
    runtime_effect_request_for_intent,
)
from control_plane_kit_core.secrets import SecretResolutionGrant
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    EffectAttemptFoldNotFound,
    EffectAttemptFoldResult,
    ExistingFold,
    FoldEffectAttempt,
    GuardedObservedEffectFold,
    NewlyFolded,
)
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.effect_attempt_reconciliation import (
    EffectAttemptReconciliationConflict,
    EffectAttemptReconciliationDenied,
    EffectAttemptReconciliationNotFound,
    ReconcileEffectAttempt,
    RuntimeEffectObserver,
    _valid_reconcile_command,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord,
    ObservedEffectOutcome,
    effect_outcome_failure,
    effect_outcome_transition,
)
from control_plane_kit_operations.records import (
    ActivityRunRecord,
    ExecutionRequestRecord,
    OperationsRecordError,
)
from control_plane_kit_operations.runtime_authorities import (
    RegisteredRuntimeAuthority,
    RuntimeAuthorityNotFound,
    RuntimeAuthorityRegistrationError,
)
from control_plane_kit_operations.runtime_effects import (
    required_secret_uses_for_runtime_effect,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    SecretProviderAuthorizationDenied,
    SecretProviderRegistrationError,
    SecretUseAuthorizationConflict,
    SecretUseAuthorizationService,
    secret_use_correlation_for,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


_AUTHORITY_ERROR = "effect attempt reconciliation authority is invalid"
_INVALID_TRUTH_ERROR = "effect attempt reconciliation truth is invalid"
_NOT_FOUND_ERROR = "effect attempt reconciliation truth was not found"
_REPLAY_ERROR = "effect attempt reconciliation is incongruent"


class EffectAttemptReconciliationService:
    """Replay or reconcile one exact durable effect attempt."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        observer: RuntimeEffectObserver,
        fold_service: Any,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._observer = observer
        self._fold_service = fold_service
        self._secret_use_authorizer = SecretUseAuthorizationService(
            unit_of_work_factory
        )

    def execute(
        self,
        command: ReconcileEffectAttempt,
    ) -> EffectAttemptFoldResult:
        if not _valid_reconcile_command(command):
            raise InvalidOperationCommand(
                "effect attempt reconciliation command is invalid"
            )
        if PolicyScope.EXECUTION_OPERATE not in command.authority.scopes:
            raise EffectAttemptReconciliationDenied(
                "scope execution:operate is missing"
            )

        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            request = _request_for_update(stores, command.request_id)
            run = _run_for_request_for_update(
                stores,
                command.request_id,
                command.identity.run_id.value,
            )
            attempt = _attempt_for_update(stores, command.identity)
            _require_current_claim(command, request, run, attempt)
            _require_historical_lineage(command, attempt)
            if attempt.state.status is not EffectAttemptStatus.STARTED:
                return _existing_fold(stores, request, attempt)

            invalid_truth = False
            denied = False
            observation = None
            intent_record = None
            runtime_authority = None
            try:
                observation = stores.execution.observe_request_lease_for_update(
                    command.request_id
                )
            except (KeyError, OperationsRecordError):
                invalid_truth = True
            else:
                invalid_truth = (
                    type(observation.request) is not ExecutionRequestRecord
                    or type(observation.observed_at) is not str
                    or type(observation.expired) is not bool
                    or observation.request != request
                )
                denied = not invalid_truth and observation.expired

            if not invalid_truth and not denied:
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
                        or intent_record.request_id
                        != request.identity.request_id
                        or intent_record.workspace_id
                        != request.identity.workspace_id
                        or intent_record.request_fingerprint
                        != attempt.state.request_fingerprint
                    )

            if (
                not invalid_truth
                and not denied
                and intent_record.intent.authority_ref is not None
            ):
                try:
                    runtime_authority = (
                        stores.runtime_authorities.get_active_for_update(
                            intent_record.workspace_id,
                            intent_record.intent.authority_ref,
                        )
                    )
                except RuntimeAuthorityNotFound:
                    denied = True
                except RuntimeAuthorityRegistrationError:
                    invalid_truth = True
                else:
                    if type(runtime_authority) is not RegisteredRuntimeAuthority:
                        invalid_truth = True
                    elif (
                        runtime_authority.workspace_id
                        != intent_record.workspace_id
                        or runtime_authority.authority_ref
                        != intent_record.intent.authority_ref
                        or runtime_authority.runtime_kind
                        is not intent_record.intent.runtime_kind
                        or runtime_authority.status.value != "active"
                    ):
                        denied = True

            if invalid_truth:
                raise EffectAttemptReconciliationConflict(_INVALID_TRUTH_ERROR)
            if denied:
                raise EffectAttemptReconciliationDenied(_AUTHORITY_ERROR)

        return _fresh_observed_fold(
            self,
            command,
            attempt,
            intent_record,
            runtime_authority,
            observation.observed_at,
        )


def _request_for_update(stores: Any, request_id: str) -> ExecutionRequestRecord:
    missing = False
    invalid = False
    request = None
    try:
        request = stores.execution.get_request_for_update(request_id)
    except KeyError:
        missing = True
    except (OperationsRecordError, ValueError):
        invalid = True
    else:
        invalid = type(request) is not ExecutionRequestRecord
    if missing:
        raise EffectAttemptReconciliationNotFound(_NOT_FOUND_ERROR)
    if invalid:
        raise EffectAttemptReconciliationConflict(_INVALID_TRUTH_ERROR)
    return request


def _run_for_request_for_update(
    stores: Any,
    request_id: str,
    run_id: str,
) -> ActivityRunRecord:
    missing = False
    invalid = False
    run = None
    try:
        run = stores.execution.get_run_for_request_for_update(request_id, run_id)
    except KeyError:
        missing = True
    except (OperationsRecordError, ValueError):
        invalid = True
    else:
        invalid = type(run) is not ActivityRunRecord
    if missing:
        raise EffectAttemptReconciliationNotFound(_NOT_FOUND_ERROR)
    if invalid:
        raise EffectAttemptReconciliationConflict(_INVALID_TRUTH_ERROR)
    return run


def _attempt_for_update(stores: Any, identity: Any) -> EffectAttemptRecord:
    missing = False
    invalid = False
    attempt = None
    try:
        attempt = stores.effect_attempts.get_for_update(identity)
    except KeyError:
        missing = True
    except (OperationsRecordError, ValueError):
        invalid = True
    else:
        invalid = type(attempt) is not EffectAttemptRecord
    if missing:
        raise EffectAttemptReconciliationNotFound(_NOT_FOUND_ERROR)
    if invalid:
        raise EffectAttemptReconciliationConflict(_INVALID_TRUTH_ERROR)
    return attempt


def _require_current_claim(
    command: ReconcileEffectAttempt,
    request: ExecutionRequestRecord,
    run: ActivityRunRecord,
    attempt: EffectAttemptRecord,
) -> None:
    if (
        request.identity.request_id != command.request_id
        or run.run_id != command.identity.run_id.value
        or run.admission.request_id != request.identity.request_id
        or run.plan_id != request.identity.plan_id
        or attempt.state.identity != command.identity
    ):
        raise EffectAttemptReconciliationConflict(_INVALID_TRUTH_ERROR)
    claim = request.claim
    if (
        request.status is not ExecutionRequestStatus.CLAIMED
        or claim is None
        or claim.fence != command.fence
    ):
        raise EffectAttemptReconciliationDenied(_AUTHORITY_ERROR)


def _require_historical_lineage(
    command: ReconcileEffectAttempt,
    attempt: EffectAttemptRecord,
) -> None:
    historical = attempt.state.fence
    if attempt.state.recovery_decision is not None or (
        command.fence.generation < historical.generation
        or (
            command.fence.generation == historical.generation
            and command.fence.worker_id != historical.worker_id
        )
    ):
        raise EffectAttemptReconciliationConflict(_REPLAY_ERROR)


def _existing_fold(
    stores: Any,
    request: ExecutionRequestRecord,
    attempt: EffectAttemptRecord,
) -> ExistingFold:
    invalid = False
    outcome_record = None
    result = None
    try:
        outcome_record = stores.effect_outcomes.get(
            attempt.state.identity,
            attempt.latest_transition_event.event_id,
        )
    except (KeyError, OperationsRecordError):
        invalid = True
    else:
        invalid = (
            type(outcome_record) is not EffectAttemptOutcomeRecord
            or outcome_record.workspace_id != request.identity.workspace_id
            or outcome_record.attempt != attempt
        )
    if not invalid:
        try:
            result = ExistingFold(attempt, outcome_record)
        except OperationsRecordError:
            invalid = True
        else:
            invalid = type(result) is not ExistingFold
    if invalid:
        raise EffectAttemptReconciliationConflict(_INVALID_TRUTH_ERROR)
    return result


def _authorize_required_secrets(
    self: EffectAttemptReconciliationService,
    command: ReconcileEffectAttempt,
    attempt: EffectAttemptRecord,
    intent_record: EffectAttemptIntentRecord,
    runtime_request: Any,
    runtime_authority: RegisteredRuntimeAuthority | None,
    observed_at: str,
) -> tuple[SecretResolutionGrant, ...]:
    uses = required_secret_uses_for_runtime_effect(
        runtime_request,
        runtime_authority,
    )
    if type(uses) is not tuple or (
        uses and PolicyScope.SECRET_PROVIDER_USE not in command.authority.scopes
    ):
        raise EffectAttemptReconciliationDenied(_AUTHORITY_ERROR)

    grants = ()
    authorization_error = False
    for reference, intent in uses:
        values = {
            "workspace_id": intent_record.workspace_id,
            "reference": reference,
            "intent": intent,
            "actor_subject": command.authority.worker_id,
            "operation_id": intent_record.request_id,
            "run_id": attempt.state.identity.run_id.value,
            "activity_id": attempt.state.identity.activity_id,
            "effect_id": attempt.original_start_event.event_id,
        }
        try:
            authorization = AuthorizeSecretUse(
                **values,
                correlation_id=secret_use_correlation_for(**values),
                requested_at=observed_at,
                actor_scopes=command.authority.scopes,
            )
            grant = self._secret_use_authorizer.authorize_resolution(
                authorization
            )
        except (
            SecretProviderAuthorizationDenied,
            SecretProviderRegistrationError,
            SecretUseAuthorizationConflict,
        ):
            authorization_error = True
            break
        if type(grant) is not SecretResolutionGrant:
            authorization_error = True
            break
        grants = (*grants, grant)
    if authorization_error:
        raise EffectAttemptReconciliationDenied(_AUTHORITY_ERROR)
    return grants


def _fresh_observed_fold(
    self: EffectAttemptReconciliationService,
    command: ReconcileEffectAttempt,
    attempt: EffectAttemptRecord,
    intent_record: EffectAttemptIntentRecord,
    runtime_authority: RegisteredRuntimeAuthority | None,
    observed_at: str,
) -> EffectAttemptFoldResult:
    grants = ()
    uses_pending = True
    runtime_request = None
    for _ in (False, True):
        runtime_request = runtime_effect_request_for_intent(
            intent_record.intent,
            effect_id=attempt.original_start_event.event_id,
            secret_resolution_grants=grants,
        )
        if uses_pending:
            grants = _authorize_required_secrets(
                self,
                command,
                attempt,
                intent_record,
                runtime_request,
                runtime_authority,
                observed_at,
            )
            uses_pending = False

    observation_request = RuntimeEffectObservationRequest(runtime_request)
    observation_result = self._observer.observe(
        observation_request,
        runtime_authority,
    )
    failure = None
    outcome = None
    guarded = None
    invalid_truth = False
    try:
        outcome = ObservedEffectOutcome(command.identity, observation_result)
    except OperationsRecordError:
        invalid_truth = True
    else:
        invalid_truth = (
            type(outcome) is not ObservedEffectOutcome
            or outcome.observation.effect_id
            != attempt.original_start_event.event_id
            or outcome.request_fingerprint
            != intent_record.request_fingerprint
        )
    if not invalid_truth:
        try:
            transition = effect_outcome_transition(outcome)
            failure = effect_outcome_failure(outcome)
            fold = FoldEffectAttempt(
                command.request_id,
                transition,
                command.authority,
                command.fence,
                failure,
                outcome,
            )
            guarded = GuardedObservedEffectFold(
                fold,
                intent_record,
                runtime_authority,
            )
        except (InvalidOperationCommand, OperationsRecordError, ValueError):
            invalid_truth = True
    if invalid_truth:
        raise EffectAttemptReconciliationConflict(_INVALID_TRUTH_ERROR)

    fold_failure = None
    try:
        result = self._fold_service.execute_observed(guarded)
    except EffectAttemptFoldDenied:
        fold_failure = "denied"
    except (EffectAttemptFoldConflict, EffectAttemptFoldNotFound):
        fold_failure = "conflict"
    if fold_failure == "denied":
        raise EffectAttemptReconciliationDenied(_AUTHORITY_ERROR)
    selected_message = None
    if fold_failure == "conflict":
        selected_message = _REPLAY_ERROR
    elif type(result) not in (
        NewlyFolded,
        ExistingFold,
    ):
        selected_message = _INVALID_TRUTH_ERROR
    if selected_message is not None:
        raise EffectAttemptReconciliationConflict(selected_message)
    return result


__all__ = ["EffectAttemptReconciliationService"]
