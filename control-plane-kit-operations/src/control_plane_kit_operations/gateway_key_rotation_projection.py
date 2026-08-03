"""Phase-typed verifier projection mechanics for gateway key rotation."""

from __future__ import annotations

from typing import Any

from control_plane_kit_core.delegation_authority import (
    DelegationVerifierProjection,
    materialize_delegation_verifiers,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_operations.desired_realized_projections import (
    PublishDesiredRealizedProjection,
)
from control_plane_kit_operations.delegation_signing_keys import (
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotation,
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.records import (
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
)


class GatewayKeyRotationProjectionConflict(ValueError):
    """Raised when durable key and graph truth cannot form an exact phase."""


def build_gateway_key_rotation_projection_publication(
    unit_of_work: Any,
    command: Any,
    *,
    phase: GatewayKeyRotationDeploymentPhase,
    created_at: str,
    trusted_epoch: int | None = None,
) -> PublishDesiredRealizedProjection:
    """Validate one phase and build its generic immutable publication command."""

    stores = unit_of_work.stores
    suffix = phase.value
    existing = stores.activity_history.action_for_idempotency(
        command.session_id,
        command.idempotency_key.value,
    )
    if existing is not None:
        payload = existing.payload
        projection_id = payload.get("desired_realized_projection_id")
        revision = payload.get("desired_graph_revision")
        if not isinstance(projection_id, str) or type(revision) is not int:
            raise GatewayKeyRotationProjectionConflict(
                f"{suffix} publication action evidence is incomplete"
            )
        if (
            command.expected_authored_graph_id
            != _payload_text(payload, "authored_graph_id", phase)
            or command.expected_current_realized_projection_id
            != _payload_text(payload, "previous_realized_projection_id", phase)
            or command.expected_desired_realized_projection_id
            != _payload_text(payload, "previous_realized_projection_id", phase)
            or command.expected_desired_graph_revision != revision - 1
        ):
            raise GatewayKeyRotationProjectionConflict(
                f"{suffix} publication replay lineage changed"
            )
        projection = stores.realized_graphs.get(projection_id)
        return PublishDesiredRealizedProjection(
            session_id=command.session_id,
            workspace_id=_payload_text(payload, "workspace_id", phase),
            actor_id=command.actor_id,
            expected_authored_graph_id=_payload_text(
                payload, "authored_graph_id", phase
            ),
            expected_realized_projection_id=_payload_text(
                payload, "previous_realized_projection_id", phase
            ),
            expected_desired_graph_revision=revision - 1,
            projection=projection,
            source_operation_id=command.rotation_id,
            source_operation_version=command.expected_rotation_version,
            idempotency_key=command.idempotency_key,
        )

    rotation_locator = stores.gateway_key_rotations.get(command.rotation_id)
    workspace = stores.workspaces.get_for_update(rotation_locator.workspace_id)
    rotation = stores.gateway_key_rotations.get_for_update(command.rotation_id)
    if rotation.workspace_id != workspace.workspace_id:
        raise GatewayKeyRotationProjectionConflict(
            "rotation workspace linkage changed"
        )
    expected_status = (
        GatewayKeyRotationStatus.KEY_GENERATED
        if phase is GatewayKeyRotationDeploymentPhase.OVERLAP
        else GatewayKeyRotationStatus.DRAINING_OLD_GRANTS
    )
    if (
        rotation.status is not expected_status
        or rotation.version != command.expected_rotation_version
        or rotation.new_key_id is None
    ):
        raise GatewayKeyRotationProjectionConflict(
            f"rotation is not the expected {suffix} source truth"
        )
    if phase is GatewayKeyRotationDeploymentPhase.RETIREMENT:
        if (
            rotation.drain_deadline_epoch is None
            or type(trusted_epoch) is not int
            or trusted_epoch < rotation.drain_deadline_epoch
        ):
            raise GatewayKeyRotationProjectionConflict(
                "old capability grants have not drained"
            )
    if (
        workspace.current_graph_id != command.expected_authored_graph_id
        or workspace.desired_graph_id != command.expected_authored_graph_id
        or workspace.current_realized_projection_id
        != command.expected_current_realized_projection_id
        or workspace.desired_realized_projection_id
        != command.expected_desired_realized_projection_id
        or workspace.desired_graph_revision
        != command.expected_desired_graph_revision
        or command.expected_current_realized_projection_id
        != command.expected_desired_realized_projection_id
    ):
        raise GatewayKeyRotationProjectionConflict(
            f"{suffix} requires one settled authored and realized graph lineage"
        )
    authored_record = stores.graphs.get(workspace.current_graph_id)
    current_record = stores.realized_graphs.get(
        workspace.current_realized_projection_id
    )
    if (
        authored_record.workspace_id != rotation.workspace_id
        or current_record.workspace_id != rotation.workspace_id
        or current_record.source_authored_graph_id != authored_record.graph_id
    ):
        raise GatewayKeyRotationProjectionConflict(
            "workspace graph lineage is not owned by the rotation workspace"
        )
    authored = DEFAULT_GRAPH_CODEC.decode(authored_record.graph_descriptor)
    current = DEFAULT_GRAPH_CODEC.decode(current_record.graph_descriptor)
    realized = derive_gateway_key_rotation_projection_graph(
        stores,
        rotation,
        authored,
        current,
        phase=phase,
    )
    projection = RealizedGraphProjectionRecord.from_graph(
        projection_id=f"gateway-rotation-{rotation.rotation_id}-{suffix}",
        workspace_id=rotation.workspace_id,
        source_authored_graph_id=authored_record.graph_id,
        projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
        projection_key=f"gateway-rotation:{rotation.rotation_id}:{suffix}",
        graph=realized,
        created_by=command.actor_id,
        created_at=created_at,
    )
    return PublishDesiredRealizedProjection(
        session_id=command.session_id,
        workspace_id=rotation.workspace_id,
        actor_id=command.actor_id,
        expected_authored_graph_id=authored_record.graph_id,
        expected_realized_projection_id=current_record.projection_id,
        expected_desired_graph_revision=workspace.desired_graph_revision,
        projection=projection,
        source_operation_id=rotation.rotation_id,
        source_operation_version=rotation.version,
        idempotency_key=command.idempotency_key,
    )


def derive_gateway_key_rotation_projection_graph(
    stores: Any,
    rotation: GatewayKeyRotation,
    authored: DeploymentGraph,
    current: DeploymentGraph,
    *,
    phase: GatewayKeyRotationDeploymentPhase,
) -> DeploymentGraph:
    """Derive exact A+B or B verifier material without changing authored truth."""

    projections = _current_projections(authored, current)
    identity = (rotation.gateway_node_id, rotation.purpose)
    bindings = {
        binding.identity: binding for binding in authored.delegation_authorities
    }
    binding = bindings.get(identity)
    if binding is None or binding.issuer != rotation.issuer:
        raise GatewayKeyRotationProjectionConflict(
            "rotation target does not match an exact authored delegation binding"
        )
    if rotation.new_key_id is None:
        raise GatewayKeyRotationProjectionConflict(
            "rotation lacks generated replacement key identity"
        )
    old_key = stores.delegation_signing_keys.get(
        rotation.workspace_id,
        rotation.purpose,
        rotation.issuer,
        rotation.old_key_id,
    )
    new_key = stores.delegation_signing_keys.get(
        rotation.workspace_id,
        rotation.purpose,
        rotation.issuer,
        rotation.new_key_id,
    )
    if new_key.private_key_reference != rotation.new_secret_reference:
        raise GatewayKeyRotationProjectionConflict(
            "rotation replacement key does not match generated custody reference"
        )
    verification_keys = stores.delegation_signing_keys.list_for_verification(
        rotation.workspace_id,
        rotation.purpose,
        rotation.issuer,
    )
    if {value.key_id for value in verification_keys} != {
        rotation.old_key_id,
        rotation.new_key_id,
    } or len(verification_keys) != 2:
        raise GatewayKeyRotationProjectionConflict(
            "rotation verifier scope contains unexpected key truth"
        )
    expected_audience = (
        f"gateway:{rotation.workspace_id}:{rotation.gateway_node_id}"
    )
    current_target = projections[identity]
    if (
        current_target.issuer != rotation.issuer
        or current_target.audience != expected_audience
    ):
        raise GatewayKeyRotationProjectionConflict(
            "current realized target does not match rotation authority"
        )
    if phase is GatewayKeyRotationDeploymentPhase.OVERLAP:
        if (
            old_key.status is not RegisteredDelegationSigningKeyStatus.ACTIVE
            or new_key.status
            is not RegisteredDelegationSigningKeyStatus.VERIFY_ONLY
            or tuple(key.key_id for key in current_target.public_keys)
            != (rotation.old_key_id,)
            or current_target.public_keys[0] != old_key.public_key
        ):
            raise GatewayKeyRotationProjectionConflict(
                "overlap source is not exact active-A/verify-B projection A"
            )
        target_keys = (old_key.public_key, new_key.public_key)
    elif phase is GatewayKeyRotationDeploymentPhase.RETIREMENT:
        if (
            old_key.status
            is not RegisteredDelegationSigningKeyStatus.VERIFY_ONLY
            or new_key.status is not RegisteredDelegationSigningKeyStatus.ACTIVE
            or tuple(key.key_id for key in current_target.public_keys)
            != (rotation.old_key_id, rotation.new_key_id)
            or current_target.public_keys
            != (old_key.public_key, new_key.public_key)
        ):
            raise GatewayKeyRotationProjectionConflict(
                "retirement source is not exact verify-A/active-B projection A+B"
            )
        target_keys = (new_key.public_key,)
    else:
        raise GatewayKeyRotationProjectionConflict(
            "gateway key rotation projection phase is unsupported"
        )
    projections[identity] = DelegationVerifierProjection(
        delegate_node_id=rotation.gateway_node_id,
        purpose=rotation.purpose,
        issuer=rotation.issuer,
        audience=expected_audience,
        projection_id=(
            f"gateway-rotation-{rotation.rotation_id}-{phase.value}-verifier"
        ),
        public_keys=target_keys,
    )
    return materialize_delegation_verifiers(
        authored,
        tuple(
            projections[key]
            for key in sorted(
                projections,
                key=lambda value: (value[0], value[1].value),
            )
        ),
    )


def _current_projections(
    authored: DeploymentGraph,
    current: DeploymentGraph,
) -> dict[Any, DelegationVerifierProjection]:
    binding_by_identity = {
        value.identity: value for value in authored.delegation_authorities
    }
    projections: dict[Any, DelegationVerifierProjection] = {}
    for identity, binding in binding_by_identity.items():
        projection = current.node(
            binding.delegate_node_id
        ).delegation_verifier_projection
        if (
            projection is None
            or projection.binding_identity != identity
            or projection.issuer != binding.issuer
        ):
            raise GatewayKeyRotationProjectionConflict(
                "current realized graph does not cover exact authored bindings"
            )
        projections[identity] = projection
    projected_nodes = {
        node.node_id
        for node in current.nodes.values()
        if node.delegation_verifier_projection is not None
    }
    if projected_nodes != {
        binding.delegate_node_id for binding in authored.delegation_authorities
    }:
        raise GatewayKeyRotationProjectionConflict(
            "current realized graph contains unbound verifier material"
        )
    return projections


def _payload_text(
    payload: Any,
    key: str,
    phase: GatewayKeyRotationDeploymentPhase,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GatewayKeyRotationProjectionConflict(
            f"{phase.value} publication action evidence is incomplete"
        )
    return value


__all__ = [
    "build_gateway_key_rotation_projection_publication",
    "GatewayKeyRotationProjectionConflict",
    "derive_gateway_key_rotation_projection_graph",
]
