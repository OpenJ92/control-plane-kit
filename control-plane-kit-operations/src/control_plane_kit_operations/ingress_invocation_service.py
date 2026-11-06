"""Fresh admission for each reached ingress capability; IO stays in interpreters."""

from dataclasses import dataclass, field, replace

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.effect_permissions import (
    CurrentWorkspaceEffectPolicy, EffectCommandContext, _require_execution_binding,
)
from control_plane_kit_operations.effect_run_prefix import _lock_effect_run_prefix
from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationControlError, ExecutionInvocationConflict, ExecutionInvocationDenied,
    ExecutionInvocationExisting, ExecutionInvocationRecord, ExecutionInvocationDecision,
    ExecutionInvocationKind as Kind, ExecutionInvocationDisposition as Disposition, ExecutionInvocationOutcome as Outcome,
)
from control_plane_kit_operations.health_signing_authority import _observed_epoch, _approval
from control_plane_kit_operations.ingress_authorities import (
    IngressAuthorityNotFound, IngressAuthorityProviderKind, RegisteredIngressAuthorityStatus, OwnedIngressResourceStatus,
    CloudflareOwnedIngressResource, GeneratedSecretPurpose, record_generated_ingress_secret,
)
from control_plane_kit_operations.ingress_invocations import (
    IngressInvocationContextRecord, custody_receipt_from_descriptor,
)
from control_plane_kit_operations.receiver_lifecycle import _validate_receiver_execution
from control_plane_kit_operations.records import ActivityRunStatus, OperationSessionStatus
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse, SecretProviderRegistrationError, authorize_secret_use_in_unit_of_work,
    secret_resolution_grant_for, secret_custody_grant_for, generated_secret_reference_candidate,
)


def _require(condition):
    if not condition:
        raise ExecutionInvocationConflict()


@dataclass(frozen=True, slots=True, repr=False)
class _Permit:
    owner: object
    record: ExecutionInvocationRecord


@dataclass(frozen=True, slots=True, repr=False)
class _BoundIngressInvocations:
    service: object
    command_context: EffectCommandContext
    realization: object
    profile: IngressInvocationContextRecord
    resolution_grant: object
    owner: object = field(default_factory=object)
    control_error = ExecutionInvocationControlError

    def admit(self, kind, *, authorization_id=None):
        return self.service._admit(self, kind, authorization_id)

    def record_outcome(self, permit, outcome):
        _require(type(permit) is _Permit and permit.record.kind is Kind.SECRET_RESOLUTION)
        return self.service._complete(self, permit, outcome, None)

    def record_result(self, permit, outcome, result):
        _require(type(permit) is _Permit and permit.record.kind is not Kind.SECRET_RESOLUTION)
        return self.service._complete(self, permit, outcome, result)


class IngressInvocationService:
    def __init__(self, unit_of_work_factory, *, permission_policy, clock):
        _require(callable(unit_of_work_factory) and callable(clock)
            and type(permission_policy) is CurrentWorkspaceEffectPolicy)
        permission_policy.__post_init__()
        self._unit_of_work_factory = unit_of_work_factory
        self.permission_policy, self._clock = permission_policy, clock

    def bind(self, *, command_context, realization, ingress, authority, resolution_grant,
            custody_grant, original_resource=None, original_secret=None):
        from control_plane_kit_operations.coordinator import ActivityRealizationContext
        from control_plane_kit_core.planning import AllocatePublicIngress
        try:
            _require(type(command_context) is EffectCommandContext and type(realization) is ActivityRealizationContext)
            command_context.__post_init__()
            realization.__post_init__()
            profile = IngressInvocationContextRecord(command_context.receipt, command_context.original.workspace_id,
                realization.activity.activity_id.value, realization.intent_event.event_id, realization.intent_event.occurred_at,
                "allocate" if type(realization.activity.operation) is AllocatePublicIngress else "remove",
                realization.plan_record.plan_id, realization.base_graph.projection_id, realization.desired_graph.projection_id,
                authority.registration_id, ingress, authority.authority, resolution_grant.authorization_id,
                custody_grant, original_resource, original_secret)
            bound = _BoundIngressInvocations(self, command_context, realization, profile, resolution_grant)
            with self._unit_of_work_factory() as uow:
                self._lock_command(uow, bound)
                self._binding(uow.stores, bound, fresh=True)
                self._prefix(uow, bound)
                # Context is original selection, never an invocation permit.
                _require(uow.stores.ingress_invocations.context_for_command_activity(profile.command, profile.activity_id) is None)
                uow.stores.ingress_invocations.add_context(profile)
                uow.commit()
            return bound
        except ExecutionInvocationControlError:
            raise
        except Exception:
            raise ExecutionInvocationConflict() from None

    def _lock_command(self, uow, bound):
        reference = bound.profile.command
        uow.stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)

    def _binding(self, stores, bound, *, fresh):
        profile, selected = bound.profile, bound.realization
        request = stores.execution.get_request(selected.request.identity.request_id)
        run = stores.execution.get_run(profile.command.run_id)
        return _require_execution_binding(stores, bound.command_context, request, run,
            selected.authority, selected.fence, require_incomplete=fresh)

    def _prefix(self, uow, bound):
        """Command -> lifecycle -> request/runs -> receipt -> session."""
        stores, profile, selected = uow.stores, bound.profile, bound.realization
        lifecycle = stores.graphs.lock_receiver_lifecycle(profile.workspace_id)
        request = stores.execution.get_request_for_update(selected.request.identity.request_id)
        runs = _lock_effect_run_prefix(uow, request, profile.command.run_id, latest_required=True)
        stores.execution.command_receipt_for_idempotency(profile.command.run_id,
            profile.command.idempotency_key, for_update=True)
        session = stores.activity_history.get_session_for_update(request.identity.session_id)
        self._binding(stores, bound, fresh=True)
        from control_plane_kit_operations.coordinator import _require_worker_owns
        _require_worker_owns(request, selected.authority, selected.fence)
        _require(runs.latest_run == runs.requested_run and runs.requested_run.status is ActivityRunStatus.RUNNING
            and session.status is OperationSessionStatus.OPEN and session.workspace_id == profile.workspace_id)
        return request, lifecycle

    def _plan_suffix(self, uow, bound, request, lifecycle):
        stores = uow.stores
        _validate_receiver_execution(stores, request, lifecycle)
        plan = stores.activity_history.get_plan_for_share(bound.profile.plan_id)
        _require(plan == bound.realization.plan_record)
        _approval(stores, request, plan)

    def _history(self, uow, bound):
        profile, stores = bound.profile, uow.stores
        _require(stores.ingress_invocations.context_for_command_activity(profile.command, profile.activity_id) == profile)
        self._binding(stores, bound, fresh=False)
        authorization = stores.ingress_invocations.require_owners(profile)
        records = stores.ingress_invocations.invocations(profile)
        results = stores.ingress_invocations.results_for_context(profile)
        return authorization, records, results

    def _admit(self, bound, kind, authorization_id):
        control, admitted = None, None
        try:
            _require(type(bound) is _BoundIngressInvocations and bound.service is self and type(kind) is str)
            kind = Kind(kind)
            profile = bound.profile
            _require(kind in profile.sequence and authorization_id == (
                profile.authorization_id if kind is Kind.SECRET_RESOLUTION else None))
            with self._unit_of_work_factory() as uow:
                stores = uow.stores
                self._lock_command(uow, bound)
                authorized, records, _ = self._history(uow, bound)
                current = next((record for record in records if record.kind is kind), None)
                if current is not None:
                    control = (ExecutionInvocationDenied(current.invocation_id) if current.disposition is Disposition.DENIED
                        else ExecutionInvocationExisting(current.invocation_id))
                else:
                    _require(len(records) < 3 and profile.sequence[len(records)] is kind
                        and all(record.disposition is Disposition.ADMITTED and record.outcome is Outcome.RETURNED for record in records))
                    request, lifecycle = self._prefix(uow, bound)
                    scopes = (PolicyScope.EXECUTION_OPERATE, PolicyScope.INGRESS_AUTHORITY_USE)
                    if kind in (Kind.SECRET_RESOLUTION, Kind.INGRESS_CUSTODY_WRITE, Kind.INGRESS_CUSTODY_REVOKE):
                        scopes += (PolicyScope.SECRET_PROVIDER_USE,)
                    decision = self.permission_policy.evaluate_in_unit_of_work(uow, bound.command_context.original,
                        required_scopes=scopes)
                    self._plan_suffix(uow, bound, request, lifecycle)
                    denial = decision.denial_code
                    if denial is None:
                        try:
                            selected = stores.ingress_authorities.get_registration_for_admission(profile.workspace_id,
                                profile.ingress.authority_ref, profile.ingress_registration_id)
                            if (selected.status is not RegisteredIngressAuthorityStatus.ACTIVE
                                    or selected.authority != profile.authority):
                                denial = "management-authority-denied"
                        except IngressAuthorityNotFound:
                            denial = "management-authority-denied"
                    if denial is None:
                        try:
                            if kind is Kind.SECRET_RESOLUTION:
                                command = AuthorizeSecretUse(workspace_id=authorized.workspace_id, reference=authorized.reference,
                                    intent=authorized.intent, actor_subject=authorized.actor_subject,
                                    correlation_id=authorized.correlation_id, requested_at=authorized.requested_at,
                                    actor_scopes=decision.effective_scopes, operation_id=authorized.operation_id,
                                    session_id=authorized.session_id, run_id=authorized.run_id,
                                    activity_id=authorized.activity_id, effect_id=authorized.effect_id)
                                current_use, provider = authorize_secret_use_in_unit_of_work(uow, command)
                                _require(current_use == authorized and secret_resolution_grant_for(current_use, provider=provider)
                                    == bound.resolution_grant)
                            elif kind in (Kind.INGRESS_CUSTODY_WRITE, Kind.INGRESS_CUSTODY_REVOKE):
                                grant = profile.custody_grant
                                if kind is Kind.INGRESS_CUSTODY_REVOKE:
                                    reference = stores.secret_references.get_active_for_update(profile.workspace_id, grant.reference)
                                    _require(reference.registration_id == profile.original_secret.reference_registration_id
                                        and reference.provider_registration_id == grant.provider_registration_id)
                                provider = stores.secret_providers.require_active_registration_for_admission(
                                    profile.workspace_id, grant.provider_registration_id)
                                current_grant = secret_custody_grant_for(provider=provider, workspace_id=profile.workspace_id,
                                    reference=grant.reference, intent=grant.intent, actor_subject=grant.actor_subject,
                                    actor_scopes=decision.effective_scopes, correlation_id=grant.correlation_id,
                                    operation_id=grant.operation_id, session_id=grant.session_id, run_id=grant.run_id,
                                    activity_id=grant.activity_id, effect_id=grant.effect_id)
                                _require(current_grant == grant)
                        except SecretProviderRegistrationError:
                            denial = "secret-use-denied"
                    if denial is None:
                        resources = stores.ingress_resources
                        resources.lock_resource(profile.workspace_id, profile.ingress_id)
                        if profile.operation == "remove":
                            current_resource = resources.get_cloudflare_epoch_for_update(profile.workspace_id,
                                profile.ingress_id, profile.original_resource.epoch)
                            expected = profile.original_resource
                            if kind is Kind.INGRESS_PROVIDER_REMOVE:
                                expected = replace(expected, status=OwnedIngressResourceStatus.REMOVING,
                                    source_run_id=profile.command.run_id)
                            _require(current_resource == expected)
                            if kind is Kind.INGRESS_CUSTODY_REVOKE:
                                resources._update_cloudflare_status(expected, replace(expected,
                                    status=OwnedIngressResourceStatus.REMOVING, source_run_id=profile.command.run_id))
                        else:
                            try:
                                resources.get_cloudflare(profile.workspace_id, profile.ingress_id)
                            except IngressAuthorityNotFound:
                                pass
                            else:
                                raise ExecutionInvocationConflict()
                    observation = stores.execution.observe_request_lease_for_update(request.identity.request_id)
                    _observed_epoch(observation, request)
                    evidence = ExecutionInvocationDecision(decision.permission_revision, denial_code=denial)
                    record = ExecutionInvocationRecord(profile.command, profile.workspace_id, profile.activity_id,
                        kind, profile.target_fingerprint(kind), observation.observed_at, authorization_id=authorization_id,
                        disposition=Disposition.ADMITTED if denial is None else Disposition.DENIED,
                        decision_at=observation.observed_at, decision=evidence,
                        outcome=Outcome.PENDING if denial is None else None)
                    _require(stores.execution.add_command_invocation(record) == record)
                    uow.commit()
                    if denial is None:
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

    def _complete(self, bound, permit, outcome, payload):
        try:
            _require(type(bound) is _BoundIngressInvocations and bound.service is self
                and type(permit) is _Permit and permit.owner is bound.owner and type(outcome) is str)
            outcome = Outcome(outcome)
            _require(outcome is not Outcome.PENDING)
            with self._unit_of_work_factory() as uow:
                self._lock_command(uow, bound)
                stores, profile = uow.stores, bound.profile
                stores.execution.command_receipt_for_idempotency(profile.command.run_id,
                    profile.command.idempotency_key, for_update=True)
                self._history(uow, bound)
                stores.ingress_invocations.require_invocation(profile, permit.record)
                current = stores.execution.command_invocation(profile.command, permit.record.invocation_id, for_update=True)
                _require(current is not None)
                if permit.record.kind is Kind.SECRET_RESOLUTION:
                    _require(payload is None)
                    if current.outcome is Outcome.PENDING:
                        _require(current == permit.record and stores.execution.replace_command_invocation(
                            current, current.completed(outcome, self._clock())))
                    else:
                        _require(current == permit.record.completed(outcome, current.completed_at))
                else:
                    # Exact repeated acknowledgement retains the original time.
                    when = self._clock() if current.outcome is Outcome.PENDING else current.completed_at
                    stores.ingress_invocations.add_result(profile, permit.record, outcome, payload, when)
                uow.commit()
        except ExecutionInvocationControlError:
            raise
        except Exception:
            raise ExecutionInvocationConflict() from None

    def fold(self, bound):
        """Retain only acknowledged successful units; never perform compensation."""
        try:
            with self._unit_of_work_factory() as uow:
                self._lock_command(uow, bound)
                _, records, results = self._history(uow, bound)
                _require(len(records) == 3 and all(record.outcome is Outcome.RETURNED for record in records))
                request, lifecycle = self._prefix(uow, bound)
                self._plan_suffix(uow, bound, request, lifecycle)
                stores, profile = uow.stores, bound.profile
                facts = {record.descriptor()["tag"]: record.descriptor() for record in results}
                if profile.operation == "allocate":
                    from control_plane_kit_operations.ingress_realization import _allocation_name
                    provider_fact = facts["provider-created"]
                    receipt = custody_receipt_from_descriptor(facts["custody-write-returned"]["receipt"])
                    grant = profile.custody_grant
                    provider = stores.secret_providers.require_active_registration_for_admission(profile.workspace_id,
                        grant.provider_registration_id)
                    _require(provider.endpoint_reference == grant.endpoint_reference
                        and provider.credential_reference == grant.credential_reference)
                    when = records[-1].completed_at
                    reference = generated_secret_reference_candidate(grant=grant, receipt=receipt, admitted_at=when)
                    stores.secret_references.register_new(reference)
                    graph = DEFAULT_GRAPH_CODEC.decode(bound.realization.desired_graph.graph_descriptor)
                    resource = CloudflareOwnedIngressResource(profile.workspace_id,
                        graph.node(profile.ingress.connector_node_id).runtime_id, profile.ingress_id,
                        profile.ingress.authority_ref, IngressAuthorityProviderKind.CLOUDFLARE,
                        _allocation_name(bound.realization, profile.ingress), provider_fact["tunnel_id"],
                        provider_fact["dns_record_id"], profile.ingress.hostname, profile.authority.zone_id,
                        profile.ingress.lifecycle, when, when, profile.command.run_id, profile.activity_id, profile.original_event_id)
                    resource = stores.ingress_resources.record_cloudflare(resource)
                    stores.generated_ingress_secrets.record(record_generated_ingress_secret(
                        workspace_id=profile.workspace_id, purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                        receipt=receipt, reference_registration_id=reference.registration_id,
                        source_run_id=profile.command.run_id, source_activity_id=profile.activity_id,
                        source_event_id=profile.original_event_id, recorded_at=when))
                else:
                    _require(set(facts) == {"custody-revoke-returned", "provider-removal-returned"})
                    expected = replace(profile.original_resource, status=OwnedIngressResourceStatus.REMOVING,
                        source_run_id=profile.command.run_id)
                    resource = replace(expected, status=OwnedIngressResourceStatus.REMOVED,
                        removed_at=records[-1].completed_at, removed_by_run_id=profile.command.run_id)
                    stores.ingress_resources._update_cloudflare_status(expected, resource)
                uow.commit()
            return resource
        except ExecutionInvocationControlError:
            raise
        except Exception:
            raise ExecutionInvocationConflict() from None
