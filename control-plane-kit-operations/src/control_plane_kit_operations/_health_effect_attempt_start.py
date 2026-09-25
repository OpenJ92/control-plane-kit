"""Health support for the sole first-start transaction owner; no commit or IO effects."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey,
)
from control_plane_kit_core.node_control import (
    NodeControlCanonicalization, NodeControlGraphReference,
    NodeControlGraphReferenceRole, NodeControlTarget, workload_node_control_audience,
)
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.node_health_reads import (
    DelegatedWorkloadNodeHealthReadGrant, DelegatedWorkloadNodeHealthReadGrantProfile,
    MAX_WORKLOAD_NODE_HEALTH_READ_GRANT_LIFETIME_SECONDS, NodeHealthReadRequest,
)
from control_plane_kit_core.node_health_transit import (
    DelegatedGatewayNodeHealthReadTransitGrant, DelegatedGatewayNodeHealthReadTransitGrantProfile,
    MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_LIFETIME_SECONDS,
)
from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.planning import PlanGraphSide
from control_plane_kit_core.policies import ApprovalPolicy
from control_plane_kit_core.secrets import SecretReference, health_signing_intent_for
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_operations.delegation_signing_keys import (
    RegisteredDelegationSigningKey, RegisteredDelegationSigningKeyStatus,
    delegation_signing_key_registration_id_for,
)
from control_plane_kit_operations._health_receiver_trust import require_health_receiver_coverage
from control_plane_kit_operations.effect_attempt_start import (
    EffectAttemptStartConflict, EffectAttemptStartDenied,
)
from control_plane_kit_operations.health_effect_attempt_start import (
    HealthEffectAttemptStartResult, StartHealthEffectAttempt,
)
from control_plane_kit_operations.health_effect_preparations import (
    HealthEffectPreparationCodec, HealthEffectPreparationRecord,
    _same_nominal_tree, health_effect_attempt_wire_id,
)
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, ApprovalDecisionKind,
    ApprovalDecisionRecord, ApprovalRequestRecord, GraphVersionRecord,
    RealizedGraphProjectionRecord,
)
from control_plane_kit_operations.runtime_management_targets import (
    ManagementHealthTargetProjection, project_management_health_target,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizedSecretUse, AuthorizeSecretUse, RegisteredSecretProvider,
    RegisteredSecretProviderStatus, RegisteredSecretReference, RegisteredSecretReferenceStatus,
    authorize_secret_use_in_unit_of_work, authorized_secret_use_for, secret_use_correlation_for,
)


_INVALID = "health effect start truth is invalid"
_DENIED = "health effect start is not eligible"
_ACK = "health effect start acknowledgement is incongruent"
_PURPOSES = (DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT,
    DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ)


@dataclass(frozen=True, repr=False)
class _HealthAdmission:
    projection: ManagementHealthTargetProjection
    authored_graph_id: str
    base_projection_id: str
    desired_projection_id: str
    keys: tuple[RegisteredDelegationSigningKey, RegisteredDelegationSigningKey]
    use_fields: tuple[dict[str, Any], dict[str, Any]]
    correlations: tuple[str, str]


@dataclass(frozen=True, repr=False)
class _HealthWrite:
    admission: _HealthAdmission
    request: NodeHealthReadRequest
    transit: DelegatedGatewayNodeHealthReadTransitGrant
    workload: DelegatedWorkloadNodeHealthReadGrant
    uses: tuple[AuthorizeSecretUse, AuthorizeSecretUse]


def health_replay(stores: Any, attempt: Any, command: StartHealthEffectAttempt | None):
    # Retained owner reconstruction is intentionally independent of current key
    # lifecycle and time. Owner exceptions keep their original identity.
    preparation = stores.health_effect_preparations.get(attempt.state.identity)
    valid = False
    if type(preparation) is HealthEffectPreparationRecord:
        try:
            HealthEffectPreparationCodec().encode_canonical_bytes(preparation)
            valid = (preparation.identity == attempt.state.identity
                and preparation.request_fingerprint == attempt.state.request_fingerprint
                and preparation.original_event_id == attempt.original_start_event.event_id)
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
    if not valid:
        raise EffectAttemptStartConflict(_INVALID)
    if command is not None:
        for authorization_id in (preparation.transit_authorization_id, preparation.workload_authorization_id):
            use = stores.secret_use_authorizations.get(preparation.workspace_id, authorization_id)
            if (not _valid_value(use, AuthorizedSecretUse)
                    or use.workspace_id != command.context.workspace_id
                    or use.authorization_id != authorization_id
                    or use.actor_subject != command.context.actor_id):
                raise EffectAttemptStartDenied(_DENIED)
    return preparation


def admit_health_start(stores: Any, command: StartHealthEffectAttempt,
        request: Any, plan: Any, event_kind: ActivityEventKind, receiver_decoders) -> _HealthAdmission:
    workspace = request.identity.workspace_id
    if (workspace != command.context.workspace_id
            or event_kind is not ActivityEventKind.STEP_STARTED
            or not _valid_value(plan, ActivityPlanRecord)
            or plan.status is not ActivityPlanStatus.PLANNED
            or not plan.plan.ready_for_execution):
        raise EffectAttemptStartDenied(_DENIED)
    approval = stores.activity_history.get_approval_request(request.approval_request_id)
    if (not _valid_value(approval, ApprovalRequestRecord)
            or type(approval.subject) is not ActivityPlanApprovalSubject
            or approval.request_id != request.approval_request_id
            or approval.session_id != request.identity.session_id
            or approval.subject.plan_id != plan.plan_id):
        raise EffectAttemptStartConflict(_INVALID)
    decision = stores.activity_history.approval_decision_for_request(approval.request_id)
    if (not _valid_value(decision, ApprovalDecisionRecord)
            or decision.decision_id != request.approval_decision_id
            or decision.request_id != approval.request_id
            or decision.decision is not ApprovalDecisionKind.APPROVED
            or decision.scope is not approval.required_scope):
        raise EffectAttemptStartDenied(_DENIED)
    requirement = ApprovalPolicy().requirement_for(plan.plan)
    if ((approval.required_scope, approval.destructive, approval.max_risk)
            != (requirement.required_scope, requirement.destructive, requirement.max_risk)):
        raise EffectAttemptStartDenied(_DENIED)

    projections = []
    for projection_id, authored_id in (
            (plan.base_realized_projection_id, plan.base_graph_id),
            (plan.desired_realized_projection_id, plan.desired_graph_id)):
        if projection_id is None:
            raise EffectAttemptStartConflict(_INVALID)
        projection = stores.realized_graphs.get(projection_id)
        authored = stores.graphs.get(authored_id)
        if (not _valid_value(projection, RealizedGraphProjectionRecord)
                or not _valid_value(authored, GraphVersionRecord)
                or projection.projection_id != projection_id
                or projection.workspace_id != workspace
                or projection.source_authored_graph_id != authored_id
                or authored.graph_id != authored_id or authored.workspace_id != workspace):
            raise EffectAttemptStartConflict(_INVALID)
        projections.append(projection)
    selected = None
    try:
        graphs = tuple(validate_graph(DEFAULT_GRAPH_CODEC.decode(item.graph_descriptor))
            for item in projections)
        for graph in graphs:
            graph.require_valid()
        selected = project_management_health_target(plan.plan, command.start.intent.activity_id,
            command.start.intent.operation, *graphs)
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    if selected is None:
        raise EffectAttemptStartConflict(_INVALID)

    keys = []
    for purpose in _PURPOSES:
        key = stores.delegation_signing_keys.require_unambiguous_active(workspace, purpose)
        if not _valid_key(key, workspace, purpose):
            raise EffectAttemptStartDenied(_DENIED)
        keys.append(key)
    if (keys[0].registration_id == keys[1].registration_id
            or keys[0].public_key.fingerprint_sha256 == keys[1].public_key.fingerprint_sha256
            or keys[0].private_key_reference == keys[1].private_key_reference):
        raise EffectAttemptStartDenied(_DENIED)

    require_health_receiver_coverage(stores, receiver_decoders, plan=plan, graphs=graphs,
        selected=selected, workspace=workspace, keys=keys,
        refuse=lambda: EffectAttemptStartDenied(_DENIED))

    use_fields = None
    try:
        use_fields = tuple(dict(workspace_id=workspace, reference=key.private_key_reference,
            intent=health_signing_intent_for(key.purpose), actor_subject=command.context.actor_id,
            operation_id=health_effect_attempt_wire_id(command.start.transition.identity),
            session_id=request.identity.session_id, run_id=command.start.transition.identity.run_id.value,
            activity_id=command.start.transition.identity.activity_id) for key in keys)
        correlations = tuple(secret_use_correlation_for(**values) for values in use_fields)
    except (ValueError, TypeError, KeyError, AttributeError):
        use_fields = None
    if use_fields is None:
        raise EffectAttemptStartDenied(_DENIED)
    # Lock and inspect BOTH before the helper can write either family. Its
    # ordinary retry adoption is not legal for an absent first start.
    for correlation in correlations:
        stores.secret_use_authorizations.lock_correlation(workspace, correlation)
    for correlation in correlations:
        if stores.secret_use_authorizations.for_correlation(workspace, correlation) is not None:
            raise EffectAttemptStartConflict(_INVALID)
    return _HealthAdmission(selected,
        plan.base_graph_id if selected.operation.target.graph_side is PlanGraphSide.BASE_GRAPH else plan.desired_graph_id,
        plan.base_realized_projection_id, plan.desired_realized_projection_id,
        tuple(keys), use_fields, correlations)


def health_interval(observation: Any) -> tuple[int, int]:
    interval = None
    try:
        observed = datetime.fromisoformat(observation.observed_at.replace("Z", "+00:00"))
        expiry = datetime.fromisoformat(observation.request.claim.lease_expires_at.replace("Z", "+00:00"))
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta, lease = observed - epoch, expiry - epoch
        floor = delta.days * 86400 + delta.seconds
        issued = floor + (1 if delta.microseconds else 0)
        expires = min(lease.days * 86400 + lease.seconds,
            floor + MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_LIFETIME_SECONDS,
            floor + MAX_WORKLOAD_NODE_HEALTH_READ_GRANT_LIFETIME_SECONDS)
        if 0 <= issued < expires:
            interval = (issued, expires)
    except (ValueError, TypeError, AttributeError, OverflowError):
        pass
    if interval is None:
        raise EffectAttemptStartDenied(_DENIED)
    return interval


def build_health_start(admission: _HealthAdmission, command: StartHealthEffectAttempt,
        start: Any, interval: tuple[int, int], logical_id: str, transit_jti: str,
        workload_jti: str) -> _HealthWrite:
    candidate = None
    try:
        selected = admission.projection
        operation = selected.operation
        target = NodeControlTarget(
            NodeControlGraphReference(NodeControlGraphReferenceRole.WORKSPACE, command.context.workspace_id),
            NodeControlGraphReference(NodeControlGraphReferenceRole.GRAPH_REVISION, admission.authored_graph_id),
            NodeControlGraphReference(NodeControlGraphReferenceRole.NODE, selected.target_node_id),
            NodeControlGraphReference(NodeControlGraphReferenceRole.PROVIDER_SOCKET, selected.target_provider_socket_name))
        declaration = WorkloadNodeControlSurfaceDeclaration(selected.target_surface,
            WorkloadNodeControlSurfaceDeclarationProfile.V2).identity()
        request = NodeHealthReadRequest(target,
            NodeControlGraphReference(NodeControlGraphReferenceRole.RUNTIME, operation.target.runtime_id),
            selected.target_health_kind, declaration, logical_id)
        common = dict(canonicalization=NodeControlCanonicalization.JCS_RFC8785_V1,
            target=target, runtime_id=request.runtime_id, kind=request.kind,
            declaration_identity=declaration, request_id=logical_id, request_digest=request.canonical_digest(),
            issued_at=interval[0], not_before=interval[0], expires_at=interval[1])
        transit_key, workload_key = admission.keys
        transit = DelegatedGatewayNodeHealthReadTransitGrant(
            profile=DelegatedGatewayNodeHealthReadTransitGrantProfile.V1, purpose=_PURPOSES[0],
            issuer=transit_key.issuer, key_id=transit_key.key_id,
            attempt_id=health_effect_attempt_wire_id(start.attempt.state.identity),
            gateway_node_id=NodeControlGraphReference(NodeControlGraphReferenceRole.NODE, selected.gateway_node_id),
            jti=transit_jti, **common)
        workload = DelegatedWorkloadNodeHealthReadGrant(
            profile=DelegatedWorkloadNodeHealthReadGrantProfile.V1, purpose=_PURPOSES[1],
            issuer=workload_key.issuer, key_id=workload_key.key_id,
            audience=workload_node_control_audience(target), jti=workload_jti, **common)
        uses = tuple(AuthorizeSecretUse(**values, correlation_id=correlation,
            requested_at=start.attempt.original_start_event.occurred_at,
            actor_scopes=command.context.granted_scopes)
            for values, correlation in zip(admission.use_fields, admission.correlations))
        candidate = _HealthWrite(admission, request, transit, workload, uses)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        pass
    if candidate is None:
        raise EffectAttemptStartDenied(_DENIED)
    return candidate


def retain_health_start(unit_of_work: Any, write: _HealthWrite, start: Any):
    uses = []
    for command in write.uses:
        # Mixed owner/helper calls remain outside pure-validation catches.
        acknowledgement = authorize_secret_use_in_unit_of_work(unit_of_work, command)
        if type(acknowledgement) is not tuple or len(acknowledgement) != 2:
            raise EffectAttemptStartConflict(_ACK)
        use, provider = acknowledgement
        if not _valid_value(use, AuthorizedSecretUse) or not _valid_value(provider, RegisteredSecretProvider):
            raise EffectAttemptStartConflict(_ACK)
        reference = unit_of_work.stores.secret_references.get_by_registration(
            command.workspace_id, use.reference_registration_id)
        if not _valid_use_ack(command, use, reference, provider):
            raise EffectAttemptStartConflict(_ACK)
        readback = unit_of_work.stores.secret_use_authorizations.get(command.workspace_id, use.authorization_id)
        if not _valid_value(readback, AuthorizedSecretUse) or readback != use:
            raise EffectAttemptStartConflict(_ACK)
        uses.append(use)
    preparation = HealthEffectPreparationRecord(
        identity=start.attempt.state.identity, request_fingerprint=start.attempt.state.request_fingerprint,
        original_event_id=start.attempt.original_start_event.event_id,
        base_realized_projection_id=write.admission.base_projection_id,
        desired_realized_projection_id=write.admission.desired_projection_id,
        transit_key_registration_id=write.admission.keys[0].registration_id,
        workload_key_registration_id=write.admission.keys[1].registration_id,
        transit_authorization_id=uses[0].authorization_id, workload_authorization_id=uses[1].authorization_id,
        request=write.request, transit_grant=write.transit, workload_grant=write.workload)
    acknowledgement = unit_of_work.stores.health_effect_preparations.insert_absent(preparation)
    if type(acknowledgement) is not HealthEffectPreparationRecord or acknowledgement != preparation:
        raise EffectAttemptStartConflict(_ACK)
    return HealthEffectAttemptStartResult(start, preparation)


def _valid_value(value: object, expected: type) -> bool:
    if type(value) is not expected:
        return False
    try:
        return _same_nominal_tree(value, replace(value))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False


def _valid_key(key: object, workspace: str, purpose: DelegationKeyPurpose) -> bool:
    if not _valid_value(key, RegisteredDelegationSigningKey):
        return False
    if (type(key.public_key) is not DelegationPublicKey
            or type(key.public_key.algorithm) is not DelegationKeyAlgorithm
            or type(key.private_key_reference) is not SecretReference):
        return False
    valid = False
    try:
        public_key = replace(key.public_key)
        private_reference = replace(key.private_key_reference)
        valid = (_same_nominal_tree(key.public_key, public_key)
            and _same_nominal_tree(key.private_key_reference, private_reference)
            and key.workspace_id == workspace and key.purpose is purpose
            and key.status is RegisteredDelegationSigningKeyStatus.ACTIVE
            and key.registration_id == delegation_signing_key_registration_id_for(
                workspace_id=workspace, purpose=purpose, issuer=key.issuer,
                public_key=key.public_key, private_key_reference=key.private_key_reference))
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return valid


def _valid_use_ack(command, use, reference, provider) -> bool:
    if not _valid_value(reference, RegisteredSecretReference):
        return False
    valid = False
    try:
        valid = (type(reference.reference) is SecretReference and type(use.reference) is SecretReference
            and reference.workspace_id == provider.workspace_id == use.workspace_id == command.workspace_id
            and reference.registration_id == use.reference_registration_id
            and reference.provider_registration_id == use.provider_registration_id == provider.registration_id
            and reference.reference == use.reference == command.reference
            and reference.status is RegisteredSecretReferenceStatus.ACTIVE
            and provider.status is RegisteredSecretProviderStatus.ACTIVE
            and command.intent in reference.allowed_intents and command.intent in provider.allowed_intents
            and authorized_secret_use_for(command, reference=reference, provider=provider) == use)
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return valid
