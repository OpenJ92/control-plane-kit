"""Command-owned semantic admission; capability IO belongs to the caller."""

from dataclasses import dataclass, field, replace
import hashlib
from typing import Protocol

import rfc8785

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.planning import ManagementBootstrapStage, ObserveManagementBootstrap
from control_plane_kit_operations.effect_attempt_start import NewlyStarted
from control_plane_kit_operations.effect_permissions import (
    CurrentWorkspaceEffectPolicy, EffectCommandContext, _require_execution_binding,
)
from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationControlError, ExecutionInvocationConflict, ExecutionInvocationDenied,
    ExecutionInvocationExisting, ExecutionInvocationRecord, ExecutionInvocationDecision,
    ExecutionInvocationKind as Kind, ExecutionInvocationDisposition as Disposition,
    ExecutionInvocationOutcome as Outcome, secret_invocation_target_fingerprint,
    signed_health_invocation_target_fingerprint,
)
from control_plane_kit_operations.health_effect_attempt_start import (
    StartHealthEffectAttempt, HealthEffectAttemptStartResult, _valid_health_command,
)
from control_plane_kit_operations.health_effect_preparations import ReceiverHealthEffectPreparationRecord
from control_plane_kit_operations.health_receiver_trust import HealthReceiverDecoders
from control_plane_kit_operations.health_signing_authority import (
    HealthSigningAuthorityReloadService, ReloadHealthSigningAuthority,
    _lock_health_prefix, _observed_epoch,
)
from control_plane_kit_operations.managed_effect_plan import require_managed_effect_plan
from control_plane_kit_operations.managed_runtime_assignments import ManagedRuntimeSelection
from control_plane_kit_operations.prepared_runtime_invocations import prepared_runtime_invocation_profile
from control_plane_kit_operations.effect_attempt_start import StartEffectAttempt, _valid_start_command
from control_plane_kit_operations.runtime_secret_uses import OperationalSecretUse, secret_authorization_command
from control_plane_kit_operations.secret_providers import (
    AuthorizedSecretUse, authorize_secret_use_in_unit_of_work, validate_authorized_secret_use,
)
from control_plane_kit_core.runtime_effect_observation import runtime_effect_request_for_intent
from control_plane_kit_operations.effect_run_prefix import _lock_effect_run_prefix
from control_plane_kit_operations.effect_attempt_start_interpreter import (
    _require_request_run, _require_current_authority, _require_fresh_effect_receiver_permission,
)
from control_plane_kit_operations.records import ActivityRunStatus, OperationSessionStatus


_KINDS = (Kind.ENDPOINT_OBSERVATION, Kind.SECRET_RESOLUTION, Kind.SIGNED_HEALTH_TRANSPORT)
_SCOPES = (PolicyScope.EXECUTION_OPERATE, PolicyScope.NODE_CONTROL_READ,
    PolicyScope.NODE_CONTROL_EXECUTE, PolicyScope.DELEGATION_KEY_USE, PolicyScope.SECRET_PROVIDER_USE)


class BoundExecutionInvocations(Protocol):
    control_error: type[Exception]

    def admit(self, kind: str, *, authorization_id: str | None = None) -> object: ...
    def record_outcome(self, permit: object, outcome: str) -> None: ...


def _require(condition):
    if not condition:
        raise ExecutionInvocationConflict()


@dataclass(frozen=True, slots=True, repr=False)
class _Permit:
    owner: object
    record: ExecutionInvocationRecord


@dataclass(frozen=True, slots=True, repr=False)
class _BoundHealthInvocations:
    service: object
    context: EffectCommandContext
    start: StartHealthEffectAttempt
    started: HealthEffectAttemptStartResult
    selection: ManagedRuntimeSelection
    owner: object = field(default_factory=object)
    control_error = ExecutionInvocationControlError

    def admit(self, kind, *, authorization_id=None):
        return self.service._admit(self, kind, authorization_id)

    def record_outcome(self, permit, outcome):
        self.service._record_outcome(self, permit, outcome)


@dataclass(frozen=True, slots=True, repr=False)
class _BoundRuntimeInvocations:
    service: object
    context: EffectCommandContext
    start: StartEffectAttempt
    started: NewlyStarted
    marker: ExecutionInvocationRecord
    runtime: object
    profile: object
    owner: object = field(default_factory=object)
    control_error = ExecutionInvocationControlError

    def admit(self, kind, *, authorization_id=None):
        return self.service._admit_runtime(self, kind, authorization_id)

    def record_outcome(self, permit, outcome):
        self.service._record_outcome(self, permit, outcome)


class ExecutionInvocationService:
    def __init__(self, unit_of_work_factory, *, permission_policy, clock,
            health_receiver_decoders=HealthReceiverDecoders(())):
        _require(callable(unit_of_work_factory) and callable(clock)
            and type(permission_policy) is CurrentWorkspaceEffectPolicy
            and type(health_receiver_decoders) is HealthReceiverDecoders)
        permission_policy.__post_init__()
        self._unit_of_work_factory = unit_of_work_factory
        self._permission_policy, self._clock = permission_policy, clock
        self._health = HealthSigningAuthorityReloadService(unit_of_work_factory,
            health_receiver_decoders=health_receiver_decoders)

    @property
    def permission_policy(self) -> CurrentWorkspaceEffectPolicy:
        return self._permission_policy

    def bind_health(self, *, command_context, start, started, managed_target):
        failed = False
        try:
            _require(type(command_context) is EffectCommandContext and _valid_health_command(start)
                and type(started) is HealthEffectAttemptStartResult and type(started.start) is NewlyStarted
                and type(started.preparation) is ReceiverHealthEffectPreparationRecord
                and type(managed_target) is ManagedRuntimeSelection)
            command_context.__post_init__()
            started.__post_init__()
            managed_target.__post_init__()
            attempt = started.start.attempt
            _require(command_context.original == start.context
                and command_context.receipt.run_id == start.start.transition.identity.run_id.value
                and attempt.state.identity == start.start.transition.identity
                and attempt.state.request_fingerprint == start.start.transition.request_fingerprint
                and attempt.state.fence.worker_id == start.start.fence.worker_id
                and attempt.state.fence.generation == start.start.fence.generation
                and managed_target.installation_id == self._permission_policy.installation_id
                and managed_target.workspace_id == start.context.workspace_id
                and managed_target.runtime_id == start.start.intent.operation.target.runtime_id
                and managed_target.authority_ref == start.start.intent.authority_ref)
        except Exception:
            failed = True
        if failed:
            raise ExecutionInvocationConflict()
        return _BoundHealthInvocations(self, command_context, start, started, managed_target)

    def inspect_health_dispatch(self, bound: BoundExecutionInvocations) -> ExecutionInvocationRecord:
        """Read original final state; neither a permit nor a retained result."""
        control, marker = None, None
        try:
            _require(type(bound) is _BoundHealthInvocations and bound.service is self)
            with self._unit_of_work_factory() as uow:
                reference = bound.context.receipt
                uow.stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                marker, _, _ = self._history(uow.stores, bound, Kind.SIGNED_HEALTH_TRANSPORT, None)
        except ExecutionInvocationControlError as error:
            control = error
        except Exception:
            control = ExecutionInvocationConflict()
        if control is not None:
            raise control
        return marker

    def _binding(self, stores, bound, *, fresh):
        start = bound.start if type(bound) is _BoundRuntimeInvocations else bound.start.start
        request = stores.execution.get_request(start.request_id)
        run = stores.execution.get_run(bound.context.receipt.run_id)
        return _require_execution_binding(stores, bound.context, request, run,
            start.authority, start.fence, require_incomplete=fresh)

    def _history(self, stores, bound, kind, authorization_id):
        """Only immutable owner reads here; command advisory is already held."""
        self._binding(stores, bound, fresh=False)
        attempt = bound.started.start.attempt
        identity = attempt.state.identity
        evidence = stores.effect_attempt_intents.get(identity)
        preparation = stores.health_effect_preparations.get(identity)
        _require(evidence.intent == bound.start.start.intent
            and evidence.original_start_event == attempt.original_start_event
            and preparation == bound.started.preparation)
        marker = stores.execution.command_invocation_for_slot(bound.context.receipt,
            activity_id=identity.activity_id, kind=Kind.SIGNED_HEALTH_TRANSPORT)
        expected = ExecutionInvocationRecord(bound.context.receipt, preparation.workspace_id,
            identity.activity_id, Kind.SIGNED_HEALTH_TRANSPORT,
            signed_health_invocation_target_fingerprint(preparation), attempt.original_start_event.occurred_at,
            attempt=identity.attempt, original_event_id=preparation.original_event_id,
            request_fingerprint=preparation.request_fingerprint)
        _require(marker is not None and marker.invocation_id == expected.invocation_id
            and marker.created_at == expected.created_at)
        if kind is Kind.SECRET_RESOLUTION:
            _require(authorization_id in (preparation.transit_authorization_id, preparation.workload_authorization_id))
            authorization = stores.secret_use_authorizations.get(preparation.workspace_id, authorization_id)
            target = secret_invocation_target_fingerprint(authorization)
        else:
            _require(authorization_id is None)
            target = (expected.target_fingerprint if kind is Kind.SIGNED_HEALTH_TRANSPORT else
                hashlib.sha256(rfc8785.dumps(dict(domain="cpk.health-endpoint-target.v1",
                    final_target_fingerprint=expected.target_fingerprint))).hexdigest())
        current = (marker if kind is Kind.SIGNED_HEALTH_TRANSPORT else
            stores.execution.command_invocation_for_slot(bound.context.receipt,
                activity_id=identity.activity_id, kind=kind, authorization_id=authorization_id))
        if current is not None:
            _require(current.target_fingerprint == target and current.attempt == identity.attempt
                and current.original_event_id == preparation.original_event_id
                and current.request_fingerprint == preparation.request_fingerprint)
        return marker, current, target

    def _admit(self, bound, kind, authorization_id):
        control, admitted = None, None
        try:
            _require(type(kind) is str)
            kind = Kind(kind)
            _require(kind in _KINDS)
            if kind is Kind.ENDPOINT_OBSERVATION:
                operation = bound.start.start.intent.operation
                _require(type(operation) is ObserveManagementBootstrap
                    and operation.stage is ManagementBootstrapStage.GATEWAY_LOCAL_READY)
            with self._unit_of_work_factory() as uow:
                stores = uow.stores
                reference = bound.context.receipt
                stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                marker, current, target = self._history(stores, bound, kind, authorization_id)
                if current is not None and current.disposition is not Disposition.PLANNED:
                    control = (ExecutionInvocationDenied(current.invocation_id)
                        if current.disposition is Disposition.DENIED else ExecutionInvocationExisting(current.invocation_id))
                elif marker.disposition is not Disposition.PLANNED:
                    control = (ExecutionInvocationDenied(marker.invocation_id)
                        if marker.disposition is Disposition.DENIED else ExecutionInvocationExisting(marker.invocation_id))
                else:
                    start = bound.start.start
                    command = ReloadHealthSigningAuthority(start.request_id, bound.started.preparation.identity,
                        bound.context.original, start.authority, start.fence)
                    locator = stores.execution.get_request(command.request_id)
                    prefix = _lock_health_prefix(uow, locator, command.identity, command_reference=reference)
                    original = self._binding(stores, bound, fresh=True)
                    prepared = self._health._prepare_in_unit_of_work(uow, command, run_prefix=prefix)
                    _require(prefix.attempt == bound.started.start.attempt
                        and prepared.preparation == bound.started.preparation
                        and prepared.evidence.intent == start.intent)
                    pinned = require_managed_effect_plan(stores, prepared.evidence.intent, prefix.runs.request)
                    _require(bound.selection.authority_ref == pinned.authority_ref
                        and bound.selection.runtime_id == start.intent.operation.target.runtime_id)
                    decision = self._permission_policy.evaluate_in_unit_of_work(uow, original,
                        required_scopes=_SCOPES, managed_target=bound.selection)
                    if decision.allowed:
                        _, observation = self._health._reload_prepared_in_unit_of_work(uow, prepared)
                    else:
                        observation = stores.execution.observe_request_lease_for_update(command.request_id)
                        _observed_epoch(observation, prefix.runs.request)
                    selected = decision.managed_selection
                    evidence = ExecutionInvocationDecision(decision.permission_revision,
                        None if selected is None else selected.assignment_id,
                        None if selected is None else selected.revision, decision.denial_code)
                    if kind is Kind.SIGNED_HEALTH_TRANSPORT:
                        record = marker.decided(evidence, observation.observed_at)
                        _require(stores.execution.replace_command_invocation(marker, record))
                    else:
                        record = ExecutionInvocationRecord(reference, marker.workspace_id, marker.activity_id,
                            kind, target, observation.observed_at, attempt=marker.attempt,
                            original_event_id=marker.original_event_id, request_fingerprint=marker.request_fingerprint,
                            authorization_id=authorization_id,
                            disposition=Disposition.ADMITTED if decision.allowed else Disposition.DENIED,
                            decision_at=observation.observed_at, decision=evidence,
                            outcome=Outcome.PENDING if decision.allowed else None)
                        _require(stores.execution.add_command_invocation(record) == record)
                    uow.commit()
                    if decision.allowed:
                        admitted = record
                    else:
                        control = ExecutionInvocationDenied(record.invocation_id)
        except ExecutionInvocationControlError as error:
            control = error
        except Exception:
            # No capability has been called, and no failed/ambiguous commit
            # may return a permit. Do not relabel owner/storage failure as A/B denial.
            control = ExecutionInvocationConflict()
        if control is not None:
            raise control
        _require(admitted is not None)
        return _Permit(bound.owner, admitted)

    def _record_outcome(self, bound, permit, outcome):
        control = None
        try:
            _require(type(permit) is _Permit and permit.owner is bound.owner
                and type(outcome) is str)
            outcome = Outcome(outcome)
            _require(outcome is not Outcome.PENDING)
            reference = bound.context.receipt
            _require(permit.record.command == reference)
            with self._unit_of_work_factory() as uow:
                stores = uow.stores
                stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                stores.execution.command_receipt_for_idempotency(reference.run_id,
                    reference.idempotency_key, for_update=True)
                self._binding(stores, bound, fresh=False)
                if type(bound) is _BoundRuntimeInvocations:
                    self._runtime_history(stores, bound, Kind.PREPARED_RUNTIME_DISPATCH, None)
                current = stores.execution.command_invocation(reference, permit.record.invocation_id, for_update=True)
                _require(current is not None)
                if current.outcome is Outcome.PENDING:
                    _require(current == permit.record)
                    completed = current.completed(outcome, self._clock())
                    _require(stores.execution.replace_command_invocation(current, completed))
                else:
                    _require(current == permit.record.completed(outcome, current.completed_at))
                uow.commit()
        except ExecutionInvocationControlError as error:
            control = error
        except Exception:
            control = ExecutionInvocationConflict()
        if control is not None:
            raise control


    def _runtime_material(self, stores, context, start, started):
        _require(type(context) is EffectCommandContext and type(start) is StartEffectAttempt
            and _valid_start_command(start) and type(started) is NewlyStarted)
        context.__post_init__()
        started.__post_init__()
        attempt = started.attempt
        identity = attempt.state.identity
        _require(context.receipt.run_id == identity.run_id.value
            and identity == start.transition.identity
            and attempt.state.request_fingerprint == start.transition.request_fingerprint
            and attempt.state.fence.worker_id == start.fence.worker_id
            and attempt.state.fence.generation == start.fence.generation)
        request = stores.execution.get_request(start.request_id)
        run = stores.execution.get_run(context.receipt.run_id)
        _require_execution_binding(stores, context, request, run, start.authority, start.fence,
            require_incomplete=False)
        evidence = stores.effect_attempt_intents.get(identity)
        _require(evidence.intent == start.intent and evidence.original_start_event == attempt.original_start_event)
        marker = stores.execution.command_invocation_for_slot(context.receipt,
            activity_id=identity.activity_id, kind=Kind.PREPARED_RUNTIME_DISPATCH)
        _require(marker is not None and marker.attempt == identity.attempt
            and marker.original_event_id == attempt.original_start_event.event_id
            and marker.request_fingerprint == attempt.state.request_fingerprint
            and marker.created_at == attempt.original_start_event.occurred_at)
        runtime = stores.runtime_authorities.get_registration(marker.workspace_id,
            start.intent.authority_ref, marker.runtime_registration_id)
        profile = prepared_runtime_invocation_profile(start.intent,
            original_event_id=marker.original_event_id, runtime_authority=runtime)
        _require(profile.target_fingerprint == marker.target_fingerprint)
        return marker, runtime, profile

    def bind_runtime(self, *, command_context, start, started) -> BoundExecutionInvocations:
        bound, failed = None, False
        try:
            _require(type(command_context) is EffectCommandContext)
            command_context.__post_init__()
            with self._unit_of_work_factory() as uow:
                reference = command_context.receipt
                uow.stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                marker, runtime, profile = self._runtime_material(uow.stores, command_context, start, started)
                bound = _BoundRuntimeInvocations(self, command_context, start, started, marker, runtime, profile)
        except Exception:
            failed = True
        if failed:
            raise ExecutionInvocationConflict()
        return bound

    def _runtime_history(self, stores, bound, kind, authorization_id):
        _require(type(bound) is _BoundRuntimeInvocations and bound.service is self)
        marker, runtime, profile = self._runtime_material(stores, bound.context, bound.start, bound.started)
        _require(marker.invocation_id == bound.marker.invocation_id and profile == bound.profile
            and replace(runtime, status=bound.runtime.status) == bound.runtime)
        authorized = None
        if kind is Kind.SECRET_RESOLUTION:
            authorized = stores.secret_use_authorizations.get(marker.workspace_id, authorization_id)
            validate_authorized_secret_use(authorized)
            _require(type(authorized) is AuthorizedSecretUse)
            use = OperationalSecretUse(authorized.reference, authorized.intent)
            request = stores.execution.get_request(bound.start.request_id)
            _require(use in profile.required_uses and authorized.session_id == request.identity.session_id)
            runtime_request = runtime_effect_request_for_intent(bound.start.intent,
                effect_id=marker.original_event_id, secret_resolution_grants=())
            expected = secret_authorization_command(runtime_request, use,
                actor_subject=bound.context.original.actor_id, requested_at=authorized.requested_at,
                actor_scopes=bound.context.original.granted_scopes, session_id=request.identity.session_id)
            for name in ("workspace_id", "actor_subject", "operation_id", "session_id", "run_id",
                    "activity_id", "effect_id", "reference", "intent", "correlation_id", "probe_id"):
                _require(getattr(authorized, name) == getattr(expected, name))
            current = stores.execution.command_invocation_for_slot(bound.context.receipt,
                activity_id=marker.activity_id, kind=kind, authorization_id=authorization_id)
            target = secret_invocation_target_fingerprint(authorized)
        else:
            _require(kind is Kind.PREPARED_RUNTIME_DISPATCH and authorization_id is None)
            current, target = marker, marker.target_fingerprint
        if current is not None:
            _require(current.target_fingerprint == target and current.attempt == marker.attempt
                and current.original_event_id == marker.original_event_id
                and current.request_fingerprint == marker.request_fingerprint)
        return marker, current, target, authorized

    def inspect_runtime_dispatch(self, bound: BoundExecutionInvocations) -> ExecutionInvocationRecord:
        marker, failed = None, False
        try:
            _require(type(bound) is _BoundRuntimeInvocations and bound.service is self)
            with self._unit_of_work_factory() as uow:
                reference = bound.context.receipt
                uow.stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                marker, _, _, _ = self._runtime_history(uow.stores, bound, Kind.PREPARED_RUNTIME_DISPATCH, None)
        except Exception:
            failed = True
        if failed:
            raise ExecutionInvocationConflict()
        return marker

    def _admit_runtime(self, bound, kind, authorization_id):
        admitted, control = None, None
        try:
            _require(type(kind) is str and type(bound) is _BoundRuntimeInvocations and bound.service is self)
            kind = Kind(kind)
            _require(kind in (Kind.SECRET_RESOLUTION, Kind.PREPARED_RUNTIME_DISPATCH))
            with self._unit_of_work_factory() as uow:
                stores, reference = uow.stores, bound.context.receipt
                stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                marker, current, target, authorized = self._runtime_history(stores, bound, kind, authorization_id)
                if current is not None and current.disposition is not Disposition.PLANNED:
                    control = (ExecutionInvocationDenied(current.invocation_id)
                        if current.disposition is Disposition.DENIED else ExecutionInvocationExisting(current.invocation_id))
                elif marker.disposition is not Disposition.PLANNED:
                    control = (ExecutionInvocationDenied(marker.invocation_id)
                        if marker.disposition is Disposition.DENIED else ExecutionInvocationExisting(marker.invocation_id))
                else:
                    guard = stores.graphs.lock_receiver_lifecycle(marker.workspace_id)
                    request = stores.execution.get_request_for_update(bound.start.request_id)
                    prefix = _lock_effect_run_prefix(uow, request, reference.run_id, latest_required=True)
                    stores.execution.command_receipt_for_idempotency(reference.run_id,
                        reference.idempotency_key, for_update=True)
                    attempt = stores.effect_attempts.get_for_update(bound.started.attempt.state.identity)
                    _require(attempt == bound.started.attempt and prefix.latest_run == prefix.requested_run
                        and prefix.requested_run.status is ActivityRunStatus.RUNNING)
                    _require_request_run(bound.start, request, prefix.requested_run)
                    _require_current_authority(bound.start, request)
                    session = stores.activity_history.get_session_for_update(request.identity.session_id)
                    _require(session.status is OperationSessionStatus.OPEN)
                    original = self._binding(stores, bound, fresh=True)
                    scopes = (PolicyScope.EXECUTION_OPERATE,)
                    if kind is Kind.SECRET_RESOLUTION:
                        scopes += (PolicyScope.SECRET_PROVIDER_USE,)
                    decision = self._permission_policy.evaluate_in_unit_of_work(uow, original, required_scopes=scopes)
                    _require_fresh_effect_receiver_permission(stores, request, guard, bound.start.intent, compensation=False)
                    if decision.allowed:
                        runtime = stores.runtime_authorities.get_registration_for_update(marker.workspace_id,
                            bound.start.intent.authority_ref, marker.runtime_registration_id)
                        _require(runtime.status.value == "active"
                            and replace(runtime, status=bound.runtime.status) == bound.runtime)
                        if authorized is not None:
                            runtime_request = runtime_effect_request_for_intent(bound.start.intent,
                                effect_id=marker.original_event_id, secret_resolution_grants=())
                            use = OperationalSecretUse(authorized.reference, authorized.intent)
                            command = secret_authorization_command(runtime_request, use,
                                actor_subject=original.actor_id, requested_at=authorized.requested_at,
                                actor_scopes=decision.effective_scopes, session_id=request.identity.session_id)
                            retained, _ = authorize_secret_use_in_unit_of_work(uow, command)
                            _require(retained == authorized)
                    observation = stores.execution.observe_request_lease_for_update(request.identity.request_id)
                    _require(observation.request == request and not observation.expired)
                    evidence = ExecutionInvocationDecision(decision.permission_revision, denial_code=decision.denial_code)
                    if kind is Kind.PREPARED_RUNTIME_DISPATCH:
                        record = marker.decided(evidence, observation.observed_at)
                        _require(stores.execution.replace_command_invocation(marker, record))
                    else:
                        record = ExecutionInvocationRecord(reference, marker.workspace_id, marker.activity_id,
                            kind, target, observation.observed_at, attempt=marker.attempt,
                            original_event_id=marker.original_event_id, request_fingerprint=marker.request_fingerprint,
                            authorization_id=authorization_id, disposition=Disposition.ADMITTED if decision.allowed else Disposition.DENIED,
                            decision_at=observation.observed_at, decision=evidence,
                            outcome=Outcome.PENDING if decision.allowed else None)
                        _require(stores.execution.add_command_invocation(record) == record)
                    uow.commit()
                    if decision.allowed:
                        admitted = record
                    else:
                        control = ExecutionInvocationDenied(record.invocation_id)
        except ExecutionInvocationControlError as error:
            control = error
        except Exception:
            control = ExecutionInvocationConflict()
        if control is not None:
            raise control
        _require(admitted is not None)
        return _Permit(bound.owner, admitted)


__all__ = ["ExecutionInvocationService", "BoundExecutionInvocations"]
