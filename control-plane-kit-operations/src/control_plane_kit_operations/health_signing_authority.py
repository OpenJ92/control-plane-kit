"""Current health signing references; no resolution, signing or dispatch effects."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
import re
from typing import Any

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.delegation_keys import DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey
from control_plane_kit_core.node_control import (
    NodeControlGraphReference, NodeControlGraphReferenceRole, NodeControlTarget,
    workload_node_control_audience,
)
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.node_health_reads import verify_workload_node_health_read_grant
from control_plane_kit_core.node_health_transit import verify_gateway_node_health_read_transit_grant
from control_plane_kit_core.operations import EffectAttemptFence, EffectAttemptIdentity, EffectAttemptStatus, RunId
from control_plane_kit_core.operations.lifecycle import ActivityEventKind, ActivityRunStatus, ExecutionRequestStatus
from control_plane_kit_core.planning import ActivityId, PlanGraphSide
from control_plane_kit_core.policies import ApprovalPolicy, PolicyScope
from control_plane_kit_core.identity import TrustedCommandContext
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference, SecretReference, SecretResolutionGrant, SecretUseIntent,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_operations._health_effect_attempt_start import _valid_key, _valid_value
from control_plane_kit_operations._health_receiver_trust import require_health_receiver_coverage
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp
from control_plane_kit_operations.delegation_signing_keys import delegation_signing_key_registration_id_for
from control_plane_kit_operations.effect_attempt_start import _bounded_command_text
from control_plane_kit_operations.effect_attempt_intent_evidence import EffectAttemptIntentRecord
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.health_effect_attempt_start import _valid_context
from control_plane_kit_operations.health_receiver_trust import HealthReceiverDecoders, HealthReceiverTrustError
from control_plane_kit_operations.health_effect_preparations import (
    HealthEffectPreparationCodec, HealthEffectPreparationRecord, _same_nominal_tree,
    health_effect_attempt_wire_id,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.node_control_signing_authority import _LockedSigningFamily, _LockedSigningTruth
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, ActivityRunRecord, ApprovalDecisionKind,
    ApprovalDecisionRecord, ApprovalRequestRecord, ClaimIdentity, ExecutionRequestRecord,
    GraphVersionRecord, RealizedGraphProjectionRecord,
)
from control_plane_kit_operations.runtime_management_targets import is_signed_management_health_operation, project_management_health_target
from control_plane_kit_operations.secret_providers import (
    AuthorizedSecretUse, AuthorizeSecretUse, RegisteredSecretProvider,
    RegisteredSecretProviderStatus, RegisteredSecretReference, RegisteredSecretReferenceStatus,
    _secret_use_fingerprint_for, _validate_reference_admission,
    authorized_secret_use_for, secret_resolution_grant_for,
    secret_use_correlation_for,
)


class HealthSigningAuthorityError(RuntimeError):
    """A bounded invalid health signing command or reference value."""


class HealthSigningAuthorityUnavailable(HealthSigningAuthorityError):
    """The retained health permission is not currently eligible."""


_INVALID = "health signing authority value is invalid"
_UNAVAILABLE = "health signing authority is unavailable"
_FAMILIES = (
    ("transit", DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT,
        SecretUseIntent.GATEWAY_NODE_HEALTH_READ_TRANSIT_SIGNING_KEY),
    ("workload", DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ,
        SecretUseIntent.WORKLOAD_NODE_HEALTH_READ_SIGNING_KEY),
)
_SCOPES = (PolicyScope.NODE_CONTROL_READ, PolicyScope.NODE_CONTROL_EXECUTE,
    PolicyScope.DELEGATION_KEY_USE, PolicyScope.SECRET_PROVIDER_USE)


@dataclass(frozen=True, slots=True, repr=False)
class ReloadHealthSigningAuthority:
    request_id: str
    identity: EffectAttemptIdentity
    context: TrustedCommandContext
    authority: ExecutionWorkerAuthority
    fence: ExecutionLeaseFence

    def __post_init__(self) -> None:
        if not _valid_command(self):
            raise HealthSigningAuthorityError(_INVALID)


@dataclass(frozen=True, slots=True, repr=False)
class GatewayNodeHealthReadTransitSigningAuthority:
    public_key: DelegationPublicKey
    resolution_grant: SecretResolutionGrant

    def __post_init__(self) -> None:
        if (type(self) is not GatewayNodeHealthReadTransitSigningAuthority
                or not _valid_family(self.public_key, self.resolution_grant, _FAMILIES[0][2])):
            raise HealthSigningAuthorityError(_INVALID)


@dataclass(frozen=True, slots=True, repr=False)
class WorkloadNodeHealthReadSigningAuthority:
    public_key: DelegationPublicKey
    resolution_grant: SecretResolutionGrant

    def __post_init__(self) -> None:
        if (type(self) is not WorkloadNodeHealthReadSigningAuthority
                or not _valid_family(self.public_key, self.resolution_grant, _FAMILIES[1][2])):
            raise HealthSigningAuthorityError(_INVALID)


@dataclass(frozen=True, slots=True, repr=False)
class HealthSigningAuthorityPair:
    """Congruent protected references; construction alone proves no current authority."""

    preparation: HealthEffectPreparationRecord
    transit: GatewayNodeHealthReadTransitSigningAuthority
    workload: WorkloadNodeHealthReadSigningAuthority

    def __post_init__(self) -> None:
        if not _valid_pair(self):
            raise HealthSigningAuthorityError(_INVALID)


def _valid_command(command: object) -> bool:
    if type(command) is not ReloadHealthSigningAuthority:
        return False
    try:
        identity, authority, fence = command.identity, command.authority, command.fence
        if (not _bounded_command_text(command.request_id)
                or type(identity) is not EffectAttemptIdentity
                or type(identity.run_id) is not RunId
                or type(identity.run_id.value) is not str
                or type(identity.activity_id) is not str or type(identity.attempt) is not int
                or identity.attempt != 1 or not _valid_context(command.context)
                or re.fullmatch(r"[a-z][a-z0-9._-]{0,127}", command.context.actor_id) is None
                or type(authority) is not ExecutionWorkerAuthority
                or type(authority.worker_id) is not str or type(authority.scopes) is not tuple
                or any(type(scope) is not PolicyScope for scope in authority.scopes)
                or type(fence) is not ExecutionLeaseFence
                or type(fence.worker_id) is not str or type(fence.generation) is not int
                or not _bounded_command_text(fence.worker_id)
                or authority.worker_id != fence.worker_id):
            return False
        return (_same_nominal_tree(identity, EffectAttemptIdentity.from_descriptor(identity.descriptor()))
            and authority == ExecutionWorkerAuthority(authority.worker_id, authority.scopes)
            and fence == ExecutionLeaseFence(fence.worker_id, fence.generation)
            and EffectAttemptFence(fence.worker_id, fence.generation).worker_id == fence.worker_id)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False


def _valid_family(public: object, resolution: object, intent: SecretUseIntent) -> bool:
    try:
        if (type(public) is not DelegationPublicKey or public.algorithm is not DelegationKeyAlgorithm.ED25519
                or type(public.key_id) is not str or type(public.public_key_pem) is not str
                or type(public.fingerprint_sha256) is not str
                or type(resolution) is not SecretResolutionGrant or resolution.intent is not intent):
            return False
        references = {"endpoint_reference": SecretProviderEndpointReference,
            "credential_reference": SecretReference, "reference": SecretReference}
        for name, expected in references.items():
            value = getattr(resolution, name)
            if type(value) is not expected or type(value.reference_id) is not str:
                return False
            if not _same_nominal_tree(value, expected(value.reference_id)):
                return False
        for item in fields(resolution):
            if item.name not in (*references, "intent"):
                value = getattr(resolution, item.name)
                if value is not None and type(value) is not str:
                    return False
        return (_same_nominal_tree(public, replace(public))
            and _same_nominal_tree(resolution, replace(resolution)))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False


def _valid_preparation(value: object) -> bool:
    if type(value) is not HealthEffectPreparationRecord:
        return False
    try:
        HealthEffectPreparationCodec().encode_canonical_bytes(value)
        return True
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False


def _valid_pair(pair: object) -> bool:
    if type(pair) is not HealthSigningAuthorityPair:
        return False
    try:
        preparation = pair.preparation
        if (not _valid_preparation(preparation)
                or type(pair.transit) is not GatewayNodeHealthReadTransitSigningAuthority
                or type(pair.workload) is not WorkloadNodeHealthReadSigningAuthority):
            return False
        resolutions = []
        for name, purpose, intent in _FAMILIES:
            family, grant = getattr(pair, name), getattr(preparation, name + "_grant")
            resolution, public = family.resolution_grant, family.public_key
            if not _valid_family(public, resolution, intent):
                return False
            fingerprint = _secret_use_fingerprint_for(
                workspace_id=resolution.workspace_id,
                reference_registration_id=resolution.reference_registration_id,
                provider_registration_id=resolution.provider_registration_id,
                reference=resolution.reference, intent=resolution.intent,
                actor_subject=resolution.actor_subject, correlation_id=resolution.correlation_id,
                operation_id=resolution.operation_id, session_id=resolution.session_id,
                run_id=resolution.run_id, activity_id=resolution.activity_id,
                effect_id=resolution.effect_id, probe_id=resolution.probe_id,
            )
            if (public.key_id != grant.key_id
                    or delegation_signing_key_registration_id_for(workspace_id=preparation.workspace_id,
                        purpose=purpose, issuer=grant.issuer, public_key=public,
                        private_key_reference=resolution.reference)
                        != getattr(preparation, name + "_key_registration_id")
                    or resolution.authorization_id != getattr(preparation, name + "_authorization_id")
                    or resolution.authorization_id != "suse_" + resolution.intent_fingerprint
                    or resolution.intent_fingerprint != fingerprint
                    or resolution.workspace_id != preparation.workspace_id
                    or resolution.operation_id != health_effect_attempt_wire_id(preparation.identity)
                    or resolution.run_id != preparation.identity.run_id.value
                    or resolution.activity_id != preparation.identity.activity_id
                    or resolution.session_id is None or resolution.effect_id is not None or resolution.probe_id is not None
                    or resolution.correlation_id != secret_use_correlation_for(**_use_fields(
                        preparation, resolution.reference, intent, resolution.actor_subject, resolution.session_id))):
                return False
            resolutions.append(resolution)
        return (pair.transit.public_key.fingerprint_sha256 != pair.workload.public_key.fingerprint_sha256
            and resolutions[0].reference != resolutions[1].reference
            and resolutions[0].actor_subject == resolutions[1].actor_subject
            and resolutions[0].session_id == resolutions[1].session_id)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False


def _use_fields(preparation, reference, intent, actor, session):
    return dict(workspace_id=preparation.workspace_id, reference=reference, intent=intent,
        actor_subject=actor, operation_id=health_effect_attempt_wire_id(preparation.identity),
        session_id=session, run_id=preparation.identity.run_id.value,
        activity_id=preparation.identity.activity_id)


def _require(condition: bool) -> None:
    if condition is not True:
        raise HealthSigningAuthorityUnavailable(_UNAVAILABLE)


def _record(value: Any, expected: type):
    _require(_valid_value(value, expected))
    return value


class HealthSigningAuthorityReloadService:
    """Check a saved first health attempt at one locked database observation."""

    def __init__(self, unit_of_work_factory: Callable[[], Any], *,
            health_receiver_decoders: HealthReceiverDecoders = HealthReceiverDecoders(())) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        if type(health_receiver_decoders) is not HealthReceiverDecoders:
            raise HealthReceiverTrustError("health receiver trust is unavailable")
        self._health_receiver_decoders = replace(health_receiver_decoders)

    def execute(self, command: ReloadHealthSigningAuthority) -> HealthSigningAuthorityPair:
        if not _valid_command(command):
            raise HealthSigningAuthorityError(_INVALID)
        _require(all(scope in command.context.granted_scopes for scope in _SCOPES)
            and PolicyScope.EXECUTION_OPERATE in command.authority.scopes)
        with self._unit_of_work_factory() as unit_of_work:
            result, _ = self.in_unit_of_work(unit_of_work, command)
            unit_of_work.commit()
        return result

    def in_unit_of_work(self, unit_of_work, command: ReloadHealthSigningAuthority):
        """Return authority and its DB observation under the caller's transaction."""
        if not _valid_command(command):
            raise HealthSigningAuthorityError(_INVALID)
        _require(all(scope in command.context.granted_scopes for scope in _SCOPES)
            and PolicyScope.EXECUTION_OPERATE in command.authority.scopes)
        stores = unit_of_work.stores
        # Match the existing first-start lock order. Owner calls stay outside
        # pure refusal catches, preserving raw/driver/domain error identity.
        request = _record(stores.execution.get_request_for_update(command.request_id), ExecutionRequestRecord)
        run = _record(stores.execution.get_run_for_request_for_update(command.request_id,
            command.identity.run_id.value), ActivityRunRecord)
        attempt = _record(stores.effect_attempts.get_for_update(command.identity), EffectAttemptRecord)
        latest = _record(stores.execution.get_latest_run_for_request_for_update(command.request_id), ActivityRunRecord)
        _require(request.identity.request_id == command.request_id
            and request.identity.workspace_id == command.context.workspace_id
            and request.status is ExecutionRequestStatus.CLAIMED
            and _valid_value(request.claim, ClaimIdentity) and request.claim.fence == command.fence
            and run.run_id == command.identity.run_id.value and run.admission.request_id == command.request_id
            and run.plan_id == request.identity.plan_id and run.status is ActivityRunStatus.RUNNING and latest == run
            and attempt.state.identity == command.identity and attempt.state.status is EffectAttemptStatus.STARTED
            and attempt.state.prior_attempt is None
            and attempt.state.fence == EffectAttemptFence(command.fence.worker_id, command.fence.generation)
            and attempt.original_start_event == attempt.latest_transition_event
            and attempt.original_start_event.kind is ActivityEventKind.STEP_STARTED)
        evidence = _record(stores.effect_attempt_intents.get(command.identity), EffectAttemptIntentRecord)
        preparation = stores.health_effect_preparations.get(command.identity)
        _require(_valid_preparation(preparation))
        intent, source = evidence.intent, evidence.intent.source
        _require(evidence.identity == preparation.identity == command.identity
            and preparation.workspace_id == command.context.workspace_id
            and evidence.original_start_event == attempt.original_start_event
            and preparation.original_event_id == attempt.original_start_event.event_id
            and preparation.request_fingerprint == evidence.request_fingerprint == attempt.state.request_fingerprint
            and preparation.request_fingerprint == runtime_effect_intent_fingerprint(intent)
            and is_signed_management_health_operation(intent.operation)
            and source.request_id == command.request_id and source.run_id == command.identity.run_id
            and source.workspace_id == command.context.workspace_id
            and source.plan_id == request.identity.plan_id and intent.activity_id.value == command.identity.activity_id)
        plan = _record(stores.activity_history.get_plan_for_share(request.identity.plan_id), ActivityPlanRecord)
        _require(plan.plan_id == request.identity.plan_id and plan.session_id == request.identity.session_id
            and plan.status is ActivityPlanStatus.PLANNED and plan.plan.ready_for_execution
            and (plan.base_graph_id, plan.desired_graph_id) == (source.base_graph_id, source.desired_graph_id)
            and (plan.base_realized_projection_id, plan.desired_realized_projection_id)
                == (preparation.base_realized_projection_id, preparation.desired_realized_projection_id))
        _approval(stores, request, plan)
        selected, target, runtime, declaration, gateway, graphs = _target(stores, plan, intent, preparation)
        keys = []
        for name, purpose, _ in _FAMILIES:
            key = stores.delegation_signing_keys.require_unambiguous_active(preparation.workspace_id, purpose)
            _require(_valid_key(key, preparation.workspace_id, purpose))
            grant = getattr(preparation, name + "_grant")
            _require(key.registration_id == getattr(preparation, name + "_key_registration_id")
                and key.issuer == grant.issuer and key.key_id == grant.key_id and grant.purpose is purpose)
            keys.append(key)
        _require(keys[0].registration_id != keys[1].registration_id
            and keys[0].public_key.fingerprint_sha256 != keys[1].public_key.fingerprint_sha256
            and keys[0].private_key_reference != keys[1].private_key_reference)
        require_health_receiver_coverage(stores, self._health_receiver_decoders, plan=plan, graphs=graphs,
            selected=selected, workspace=preparation.workspace_id, keys=keys,
            refuse=lambda: HealthSigningAuthorityUnavailable(_UNAVAILABLE))
        truth = stores.node_control_signing_authority.get_health_for_share(preparation)
        _require(type(truth) is _LockedSigningTruth)
        resolutions = tuple(_resolution(preparation, key, getattr(truth, name), intent_kind,
            command.context.actor_id, request.identity.session_id, attempt.original_start_event.occurred_at,
            getattr(preparation, name + "_authorization_id"))
            for (name, _, intent_kind), key in zip(_FAMILIES, keys))
        observation = stores.execution.observe_request_lease_for_update(command.request_id)
        now = _observed_epoch(observation, request)
        transit = verify_gateway_node_health_read_transit_grant(preparation.transit_grant, preparation.request,
            expected_issuer=keys[0].issuer, expected_key_id=keys[0].key_id,
            expected_attempt_id=health_effect_attempt_wire_id(command.identity), expected_gateway_node_id=gateway,
            expected_target=target, expected_runtime_id=runtime, expected_declaration=declaration,
            expected_kind=selected.target_health_kind, now=now)
        _require(transit.is_accepted)
        workload = verify_workload_node_health_read_grant(preparation.workload_grant, preparation.request,
            expected_issuer=keys[1].issuer, expected_key_id=keys[1].key_id,
            expected_target=target, expected_runtime_id=runtime, expected_declaration=declaration,
            expected_kind=selected.target_health_kind, expected_audience=workload_node_control_audience(target), now=now)
        _require(workload.is_accepted)
        result = HealthSigningAuthorityPair(preparation,
            GatewayNodeHealthReadTransitSigningAuthority(keys[0].public_key, resolutions[0]),
            WorkloadNodeHealthReadSigningAuthority(keys[1].public_key, resolutions[1]))
        return result, observation


def _approval(stores, request, plan):
    _require(request.approval_request_id is not None and request.approval_decision_id is not None)
    approval = _record(stores.activity_history.get_approval_request(request.approval_request_id), ApprovalRequestRecord)
    decision = _record(stores.activity_history.approval_decision_for_request(approval.request_id), ApprovalDecisionRecord)
    requirement = ApprovalPolicy().requirement_for(plan.plan)
    _require(type(approval.subject) is ActivityPlanApprovalSubject
        and approval.request_id == request.approval_request_id and approval.session_id == request.identity.session_id
        and approval.subject.plan_id == plan.plan_id and decision.decision_id == request.approval_decision_id
        and decision.request_id == approval.request_id and decision.decision is ApprovalDecisionKind.APPROVED
        and decision.scope is approval.required_scope
        and (approval.required_scope, approval.destructive, approval.max_risk)
            == (requirement.required_scope, requirement.destructive, requirement.max_risk))


def _target(stores, plan, intent, preparation):
    graphs = []
    for projection_id, authored_id in ((plan.base_realized_projection_id, plan.base_graph_id),
            (plan.desired_realized_projection_id, plan.desired_graph_id)):
        projection = _record(stores.realized_graphs.get(projection_id), RealizedGraphProjectionRecord)
        authored = _record(stores.graphs.get(authored_id), GraphVersionRecord)
        _require(projection.projection_id == projection_id and authored.graph_id == authored_id
            and projection.workspace_id == authored.workspace_id == preparation.workspace_id
            and projection.source_authored_graph_id == authored_id)
        graph = None
        try:
            graph = validate_graph(DEFAULT_GRAPH_CODEC.decode(projection.graph_descriptor))
            graph.require_valid()
        except (ValueError, TypeError, KeyError, AttributeError):
            graph = None
        _require(graph is not None)
        graphs.append(graph)
    selected = None
    try:
        selected = project_management_health_target(plan.plan, ActivityId(preparation.identity.activity_id),
            intent.operation, *graphs)
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    _require(selected is not None)
    operation = selected.operation
    authored = plan.base_graph_id if operation.target.graph_side is PlanGraphSide.BASE_GRAPH else plan.desired_graph_id
    def reference(role, value):
        return NodeControlGraphReference(role, value)
    target = NodeControlTarget(reference(NodeControlGraphReferenceRole.WORKSPACE, preparation.workspace_id),
        reference(NodeControlGraphReferenceRole.GRAPH_REVISION, authored),
        reference(NodeControlGraphReferenceRole.NODE, selected.target_node_id),
        reference(NodeControlGraphReferenceRole.PROVIDER_SOCKET, selected.target_provider_socket_name))
    runtime = reference(NodeControlGraphReferenceRole.RUNTIME, operation.target.runtime_id)
    gateway = reference(NodeControlGraphReferenceRole.NODE, selected.gateway_node_id)
    declaration = WorkloadNodeControlSurfaceDeclaration(selected.target_surface, WorkloadNodeControlSurfaceDeclarationProfile.V2)
    _require(preparation.request.target == target and preparation.request.runtime_id == runtime
        and preparation.request.kind is selected.target_health_kind and preparation.request.declaration_identity == declaration.identity()
        and preparation.transit_grant.gateway_node_id == gateway)
    return selected, target, runtime, declaration, gateway, tuple(graphs)


def _resolution(preparation, key, family, intent, actor, session, requested_at, authorization_id):
    _require(type(family) is _LockedSigningFamily)
    use = _record(family.authorization, AuthorizedSecretUse)
    reference = _record(family.reference, RegisteredSecretReference)
    provider = _record(family.provider, RegisteredSecretProvider)
    _require(reference.status is RegisteredSecretReferenceStatus.ACTIVE
        and provider.status is RegisteredSecretProviderStatus.ACTIVE
        and use.authorization_id == authorization_id and use.requested_at == requested_at
        and use.workspace_id == reference.workspace_id == provider.workspace_id == preparation.workspace_id
        and reference.registration_id == use.reference_registration_id
        and reference.provider_registration_id == provider.registration_id == use.provider_registration_id
        and reference.reference == use.reference == key.private_key_reference
        and intent in reference.allowed_intents and intent in provider.allowed_intents)
    values = _use_fields(preparation, key.private_key_reference, intent, actor, session)
    correlation = secret_use_correlation_for(**values)
    expected = AuthorizeSecretUse(**values, correlation_id=correlation, requested_at=requested_at, actor_scopes=())
    _validate_reference_admission(reference, provider)
    # Existing pure owner projection proves the fingerprint and all context
    # fields; this creates no authorization row or new authority.
    _require(authorized_secret_use_for(expected, reference=reference, provider=provider) == use)
    return secret_resolution_grant_for(use, provider=provider)


def _observed_epoch(observation, request):
    valid, now = False, None
    try:
        if observation.request == request and type(observation.expired) is bool:
            validate_canonical_utc_timestamp(observation.observed_at)
            validate_canonical_utc_timestamp(request.claim.lease_expires_at)
            observed = datetime.fromisoformat(observation.observed_at.replace("Z", "+00:00"))
            expiry = datetime.fromisoformat(request.claim.lease_expires_at.replace("Z", "+00:00"))
            delta = observed - datetime(1970, 1, 1, tzinfo=timezone.utc)
            now = delta.days * 86400 + delta.seconds
            valid = (observation.expired == (expiry <= observed) and not observation.expired
                and type(now) is int and 0 <= now < 2**53)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        pass
    _require(valid)
    return now


__all__ = ["ReloadHealthSigningAuthority", "GatewayNodeHealthReadTransitSigningAuthority",
    "WorkloadNodeHealthReadSigningAuthority", "HealthSigningAuthorityPair",
    "HealthSigningAuthorityReloadService", "HealthSigningAuthorityError", "HealthSigningAuthorityUnavailable"]
