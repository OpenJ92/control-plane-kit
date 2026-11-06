"""Transactional interpreter for runtime-effect reconciliation."""

from __future__ import annotations

from typing import Any, Callable

from control_plane_kit_core.operations import EffectAttemptStatus
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_authority import RemoteDockerTlsConnectionAdmission
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationEvidence,
    RuntimeEffectObservationFailure,
    RuntimeEffectObservationRequest,
    RuntimeEffectObservedIndeterminate,
    runtime_effect_request_for_intent,
)
from control_plane_kit_core.secret_installation import secret_installation_receipt_for_observation
from control_plane_kit_core.secret_version_grants import ResolutionGrant
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
    _valid_reconcile_command, _require_current_claim, _require_historical_lineage,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_permissions import (
    CurrentWorkspaceEffectPolicy, EffectCommandContext, EffectPermissionDenied, _require_execution_binding,
)
from control_plane_kit_operations.execution_invocation_service import ExecutionInvocationService
from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationConflict, ExecutionInvocationDenied, ExecutionInvocationPlanned,
    ExecutionInvocationKind, ExecutionInvocationDisposition, ExecutionInvocationOutcome,
)
from control_plane_kit_operations.prepared_runtime_invocations import runtime_preparation_candidate
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord,
    ObservedEffectOutcome,
    _observed_configuration_candidate,
    _require_original_observation_plan,
    _require_observed_configuration_receipt,
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
    RemoteDockerTlsAuthority,
    RuntimeAuthorityNotFound,
    RuntimeAuthorityRegistrationError,
)
from control_plane_kit_operations.runtime_effects import (
    required_secret_uses_for_runtime_effect,
    secret_authorization_command,
)
from control_plane_kit_operations.secret_providers import (
    SecretProviderAuthorizationDenied,
    SecretProviderRegistrationError,
    SecretUseAuthorizationConflict,
    SecretUseAuthorizationService,
    authorize_runtime_secret_command,
)
from control_plane_kit_operations.runtime_adapter_registration import (
    RuntimeAdapterRegistration, adapter_is_qualified, registered_adapter, adapter_accepts_invocations,
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
        observer: RuntimeEffectObserver | RuntimeAdapterRegistration,
        fold_service: Any,
        *, permission_policy=None, invocation_service=None,
    ) -> None:
        if permission_policy is not None and type(permission_policy) is not CurrentWorkspaceEffectPolicy:
            raise ExecutionInvocationConflict()
        if permission_policy is not None:
            try:
                permission_policy.__post_init__()
            except (EffectPermissionDenied, ValueError, TypeError, AttributeError):
                raise ExecutionInvocationConflict() from None
        if invocation_service is not None and (type(invocation_service) is not ExecutionInvocationService
                or permission_policy is None or invocation_service.permission_policy != permission_policy):
            raise ExecutionInvocationConflict()
        self._permission_policy, self._invocation_service = permission_policy, invocation_service
        self._unit_of_work_factory = unit_of_work_factory
        self._observer = observer
        self._fold_service = fold_service
        self._secret_use_authorizer = SecretUseAuthorizationService(
            unit_of_work_factory
        )

    def _prepared_invocation_ready(self, permission_policy, invocation_service, *, original=False):
        return (type(self) is EffectAttemptReconciliationService
            and permission_policy is not None and self._permission_policy == permission_policy
            and type(invocation_service) is ExecutionInvocationService
            and self._invocation_service is invocation_service
            and adapter_accepts_invocations(self._observer, "observe")
            and (not original or self._observer.original_runtime_observation_invocations))

    def execute(
        self,
        command: ReconcileEffectAttempt,
        *, command_context=None,
    ) -> EffectAttemptFoldResult:
        if not _valid_reconcile_command(command):
            raise InvalidOperationCommand("effect attempt reconciliation command is invalid")
        from control_plane_kit_operations._configuration_preparation import _configuration_accounting
        with _configuration_accounting(command.identity.run_id.value, join=True, active=False):
            return self._execute(command, command_context=command_context)

    def _execute(
        self,
        command: ReconcileEffectAttempt,
        *, command_context=None,
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
            if command_context is not None:
                if type(command_context) is not EffectCommandContext:
                    raise ExecutionInvocationConflict()
                try:
                    command_context.__post_init__()
                except (EffectPermissionDenied, ValueError, TypeError, AttributeError):
                    raise ExecutionInvocationConflict() from None
                reference = command_context.receipt
                stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
            from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
            if not _ACCOUNTING.get().active:
                stores.configuration_preparation._configure_run(command.identity.run_id.value)
            request = _request_for_update(stores, command.request_id)
            run = _run_for_request_for_update(
                stores,
                command.request_id,
                command.identity.run_id.value,
            )
            attempt = _attempt_for_update(stores, command.identity)
            _require_current_claim(command, request, run, attempt)
            _require_historical_lineage(command, attempt)
            if command_context is not None:
                try:
                    _require_execution_binding(stores, command_context, request, run, command.authority, command.fence)
                except EffectPermissionDenied:
                    raise ExecutionInvocationConflict() from None
            if attempt.state.status is not EffectAttemptStatus.STARTED:
                try:
                    route = stores.configuration_preparation._configure_run(command.identity.run_id.value,
                        replay_request_id=request.identity.request_id, replay_activity_id=command.identity.activity_id)
                except (OperationsRecordError, ValueError, KeyError):
                    raise EffectAttemptReconciliationConflict(_INVALID_TRUTH_ERROR) from None
                if route not in ("ordinary", "cleanup"):
                    raise EffectAttemptReconciliationConflict(_INVALID_TRUTH_ERROR)
                return _existing_fold(stores, request, attempt, cleanup=route == "cleanup")

            invalid_truth = False
            denied = False
            observation = None
            intent_record = None
            runtime_authority = None
            prepared = False
            dynamic = any(value is not None for value in (
                self._permission_policy, self._invocation_service, command_context))
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

            if not invalid_truth and not denied:
                candidate = runtime_preparation_candidate(intent_record.intent)
                marker = (stores.execution.original_final_invocation(attempt.state.identity,
                    original_event_id=attempt.original_start_event.event_id,
                    request_fingerprint=attempt.state.request_fingerprint,
                    kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH) if candidate else None)
                if dynamic:
                    if command_context is None:
                        raise ExecutionInvocationConflict()
                    from .postgres.runtime_observation_source import original_observation_source
                    original_source, runtime_authority, profile, _ = original_observation_source(
                        stores.connection, intent_record)
                    if original_source.command == command_context.receipt:
                        raise ExecutionInvocationConflict()
                    if not self._prepared_invocation_ready(self._permission_policy, self._invocation_service,
                            original=profile.requires_original_qualification):
                        raise ExecutionInvocationConflict()
                    prepared = True
                elif marker is not None:
                    # Absence is a trusted legacy composition boundary, not
                    # proof that damaged history was originally unprepared.
                    raise ExecutionInvocationConflict(marker.invocation_id)
                elif (intent_record.intent.kind is RuntimeEffectKind.REALIZE_ACTIVITY
                        and intent_record.intent.authority_ref is not None
                        and getattr(stores, "runtime_effect_origins", None) is not None
                        and stores.runtime_effect_origins.get(attempt.state.identity) is not None):
                    raise ExecutionInvocationConflict()

            if (
                not invalid_truth
                and not denied
                and not prepared
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
            original_plan = None
            if _observed_configuration_candidate(intent_record):
                try:
                    from .postgres.configuration_evidence import _joined_read
                    with _joined_read(stores.connection):
                        original_plan = stores.activity_history.get_plan(intent_record.intent.source.plan_id)
                    _require_original_observation_plan(intent_record, original_plan, request=request)
                except (KeyError, ValueError, TypeError, AttributeError):
                    raise EffectAttemptReconciliationConflict(_INVALID_TRUTH_ERROR) from None

        invocations = (self._invocation_service.bind_reconciliation(command_context=command_context,
            command=command, attempt=attempt, intent_record=intent_record, runtime_authority=runtime_authority)
            if prepared else None)
        return _fresh_observed_fold(
            self,
            command,
            attempt,
            intent_record,
            runtime_authority,
            observation.observed_at,
            original_plan,
            invocations=invocations, session_id=request.identity.session_id if prepared else None,
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


def _existing_fold(
    stores: Any,
    request: ExecutionRequestRecord,
    attempt: EffectAttemptRecord,
    *,
    cleanup: bool,
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
            if cleanup:
                from control_plane_kit_core.planning import CleanupConfigurationInstances
                original = stores.effect_attempt_intents.get(attempt.state.identity)
                if (type(original) is not EffectAttemptIntentRecord
                        or type(original.intent.operation) is not CleanupConfigurationInstances
                        or original.identity != attempt.state.identity
                        or original.original_start_event != attempt.original_start_event
                        or original.request_id != request.identity.request_id
                        or original.workspace_id != request.identity.workspace_id
                        or original.request_fingerprint != attempt.state.request_fingerprint):
                    raise OperationsRecordError(_INVALID_TRUTH_ERROR)
                retained = stores.configuration_cleanup_ownership.get(attempt.state.identity)
                if (retained is None or retained.status is not attempt.state.status
                        or retained.original_event_id != attempt.original_start_event.event_id
                        or retained.outcome_fingerprint != outcome_record.outcome.outcome_fingerprint):
                    raise OperationsRecordError(_INVALID_TRUTH_ERROR)
            result = ExistingFold(attempt, outcome_record)
        except (KeyError, OperationsRecordError):
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
    *, session_id=None, observation_uses=None,
) -> tuple[ResolutionGrant, ...]:
    uses = (required_secret_uses_for_runtime_effect(runtime_request, runtime_authority)
        if observation_uses is None else observation_uses)
    if type(uses) is not tuple or (
        uses and PolicyScope.SECRET_PROVIDER_USE not in command.authority.scopes
    ):
        raise EffectAttemptReconciliationDenied(_AUTHORITY_ERROR)

    grants = ()
    authorization_error = False
    for use in uses:
        try:
            options = {} if session_id is None else dict(session_id=session_id)
            authorization = secret_authorization_command(runtime_request, use,
                actor_subject=command.authority.worker_id, requested_at=observed_at,
                actor_scopes=command.authority.scopes, **options)
            grant = authorize_runtime_secret_command(self._secret_use_authorizer, authorization)
        except (
            SecretProviderAuthorizationDenied,
            SecretProviderRegistrationError,
            SecretUseAuthorizationConflict,
        ):
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
    original_plan,
    *, invocations=None, session_id=None,
) -> EffectAttemptFoldResult:
    runtime_request = runtime_effect_request_for_intent(intent_record.intent,
        effect_id=attempt.original_start_event.event_id)
    if not adapter_is_qualified(self._observer, runtime_request, "observe"):
        raise EffectAttemptReconciliationDenied(_AUTHORITY_ERROR)
    observer = registered_adapter(self._observer)
    if not callable(getattr(observer, "observe", None)):
        raise EffectAttemptReconciliationDenied(_AUTHORITY_ERROR)
    grants = _authorize_required_secrets(self, command, attempt, intent_record,
        runtime_request, runtime_authority, observed_at, session_id=session_id,
        observation_uses=None if invocations is None else invocations.profile.required_uses)
    runtime_request = runtime_effect_request_for_intent(intent_record.intent,
        effect_id=attempt.original_start_event.event_id, secret_resolution_grants=grants)

    connection_admission = None
    if runtime_authority is not None and isinstance(runtime_authority.authority, RemoteDockerTlsAuthority):
        remote = runtime_authority.authority
        connection_admission = RemoteDockerTlsConnectionAdmission(
            runtime_authority.authority_ref,
            remote.ca_certificate,
            remote.client_certificate,
            remote.client_key,
        )
    observation_request = RuntimeEffectObservationRequest(
        runtime_request, connection_admission=connection_admission
    )
    observation_result = (observer.observe(observation_request, runtime_authority) if invocations is None else
        observer.observe(observation_request, runtime_authority, invocations=invocations))
    if invocations is not None:
        final = self._invocation_service.inspect_reconciliation_observation(invocations)
        if final is None:
            raise ExecutionInvocationConflict()
        if final.disposition is ExecutionInvocationDisposition.DENIED:
            raise ExecutionInvocationDenied(final.invocation_id)
        if (final.disposition is not ExecutionInvocationDisposition.ADMITTED
                or final.outcome is ExecutionInvocationOutcome.PENDING):
            raise ExecutionInvocationConflict(final.invocation_id)
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
            secret_installation_receipt_for_observation(runtime_request, outcome.observation)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            # This records our admission uncertainty after the external read,
            # not a provider assertion or permission to execute again.
            outcome = ObservedEffectOutcome(command.identity, RuntimeEffectObservedIndeterminate(
                attempt.original_start_event.event_id, intent_record.request_fingerprint,
                RuntimeEffectObservationEvidence({"admission_boundary": "secret-installation"}),
                RuntimeEffectObservationFailure("runtime.secret-installation-proof-unavailable",
                    "Secret installation proof is unavailable.")))
        try:
            _require_observed_configuration_receipt(intent_record, outcome.observation,
                original_plan=original_plan)
        except OperationsRecordError:
            outcome = ObservedEffectOutcome(command.identity, RuntimeEffectObservedIndeterminate(
                attempt.original_start_event.event_id, intent_record.request_fingerprint,
                RuntimeEffectObservationEvidence({"admission_boundary": "configuration-installation"}),
                RuntimeEffectObservationFailure("runtime.configuration-installation-proof-unavailable",
                    "Configuration installation proof is unavailable.")))
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
