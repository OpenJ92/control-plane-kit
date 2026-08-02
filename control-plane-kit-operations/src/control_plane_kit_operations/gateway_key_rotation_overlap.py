"""Derive and publish the overlap verifier projection for one key rotation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from control_plane_kit_core.delegation_authority import (
    DelegationAuthorityError,
    DelegationVerifierProjection,
    materialize_delegation_verifiers,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    GraphDescriptorError,
)
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyError,
    RegisteredDelegationSigningKey,
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.desired_realized_projections import (
    DesiredRealizedProjectionPublicationError,
    DesiredRealizedProjectionPublicationResult,
    PublishDesiredRealizedProjection,
    publish_desired_realized_projection_in_unit_of_work,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotation,
    GatewayKeyRotationError,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.records import (
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
)
from control_plane_kit_operations.workflows import IdempotencyKey, InvalidOperationCommand


class GatewayKeyRotationOverlapProjectionError(RuntimeError):
    """Base error for overlap verifier projection publication."""


class GatewayKeyRotationOverlapProjectionConflict(
    GatewayKeyRotationOverlapProjectionError
):
    """Raised when rotation, graph, key, session, or replay truth disagrees."""


class GatewayKeyRotationOverlapProjectionAuthorizationDenied(
    GatewayKeyRotationOverlapProjectionError
):
    """Raised when the actor lacks focused key-rotation authority."""


@dataclass(frozen=True)
class PublishGatewayKeyRotationOverlapProjection:
    """Request A+B projection derivation from one exact key-generated rotation."""

    rotation_id: str
    session_id: str
    actor_id: str
    expected_rotation_version: int
    expected_authored_graph_id: str
    expected_current_realized_projection_id: str
    expected_desired_realized_projection_id: str
    expected_desired_graph_revision: int
    actor_scopes: tuple[PolicyScope, ...]
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        for value, field in (
            (self.rotation_id, "rotation_id"),
            (self.session_id, "session_id"),
            (self.actor_id, "actor_id"),
            (self.expected_authored_graph_id, "expected_authored_graph_id"),
            (
                self.expected_current_realized_projection_id,
                "expected_current_realized_projection_id",
            ),
            (
                self.expected_desired_realized_projection_id,
                "expected_desired_realized_projection_id",
            ),
        ):
            if not isinstance(value, str) or not value.strip():
                raise InvalidOperationCommand(f"{field} must not be empty")
        if type(self.expected_rotation_version) is not int or self.expected_rotation_version < 1:
            raise InvalidOperationCommand("expected_rotation_version must be positive")
        if (
            type(self.expected_desired_graph_revision) is not int
            or self.expected_desired_graph_revision < 0
        ):
            raise InvalidOperationCommand(
                "expected_desired_graph_revision must be nonnegative"
            )
        if not isinstance(self.actor_scopes, tuple) or not all(
            isinstance(value, PolicyScope) for value in self.actor_scopes
        ):
            raise InvalidOperationCommand("actor_scopes must be a typed tuple")
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")


@dataclass(frozen=True)
class GatewayKeyRotationOverlapProjectionResult:
    """Rotation identity plus committed desired projection evidence."""

    rotation_id: str
    publication: DesiredRealizedProjectionPublicationResult


class GatewayKeyRotationOverlapProjectionService:
    """Compile exact A+B verifier material inside one operations transaction."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], str],
        action_id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._action_id_factory = action_id_factory

    def execute(
        self,
        command: PublishGatewayKeyRotationOverlapProjection,
    ) -> GatewayKeyRotationOverlapProjectionResult:
        if not isinstance(command, PublishGatewayKeyRotationOverlapProjection):
            raise TypeError(
                "command must be PublishGatewayKeyRotationOverlapProjection"
            )
        if PolicyScope.DELEGATION_KEY_ROTATE not in command.actor_scopes:
            raise GatewayKeyRotationOverlapProjectionAuthorizationDenied(
                "overlap projection publication requires delegation-key.rotate"
            )
        with self._unit_of_work_factory() as unit_of_work:
            created_at = self._clock()
            try:
                publication_command = self._publication_command(
                    unit_of_work,
                    command,
                    created_at=created_at,
                )
                publication = publish_desired_realized_projection_in_unit_of_work(
                    unit_of_work,
                    publication_command,
                    created_at=created_at,
                    action_id=self._action_id_factory(),
                )
            except GatewayKeyRotationOverlapProjectionError:
                raise
            except (
                DelegationAuthorityError,
                DelegationSigningKeyError,
                DesiredRealizedProjectionPublicationError,
                GatewayKeyRotationError,
                GraphDescriptorError,
                KeyError,
                ValueError,
            ) as error:
                raise GatewayKeyRotationOverlapProjectionConflict(str(error)) from error
            unit_of_work.commit()
            return GatewayKeyRotationOverlapProjectionResult(
                command.rotation_id,
                publication,
            )

    def _publication_command(
        self,
        unit_of_work: Any,
        command: PublishGatewayKeyRotationOverlapProjection,
        *,
        created_at: str,
    ) -> PublishDesiredRealizedProjection:
        stores = unit_of_work.stores
        existing = stores.activity_history.action_for_idempotency(
            command.session_id,
            command.idempotency_key.value,
        )
        if existing is not None:
            payload = existing.payload
            projection_id = payload.get("desired_realized_projection_id")
            revision = payload.get("desired_graph_revision")
            if not isinstance(projection_id, str) or type(revision) is not int:
                raise GatewayKeyRotationOverlapProjectionConflict(
                    "overlap publication action evidence is incomplete"
                )
            if (
                command.expected_authored_graph_id
                != _payload_text(payload, "authored_graph_id")
                or command.expected_current_realized_projection_id
                != _payload_text(payload, "previous_realized_projection_id")
                or command.expected_desired_realized_projection_id
                != _payload_text(payload, "previous_realized_projection_id")
                or command.expected_desired_graph_revision != revision - 1
            ):
                raise GatewayKeyRotationOverlapProjectionConflict(
                    "overlap publication replay lineage changed"
                )
            projection = stores.realized_graphs.get(projection_id)
            return PublishDesiredRealizedProjection(
                session_id=command.session_id,
                workspace_id=_payload_text(payload, "workspace_id"),
                actor_id=command.actor_id,
                expected_authored_graph_id=_payload_text(
                    payload,
                    "authored_graph_id",
                ),
                expected_realized_projection_id=_payload_text(
                    payload,
                    "previous_realized_projection_id",
                ),
                expected_desired_graph_revision=revision - 1,
                projection=projection,
                source_operation_id=command.rotation_id,
                source_operation_version=command.expected_rotation_version,
                idempotency_key=command.idempotency_key,
            )

        rotation = stores.gateway_key_rotations.get_for_update(command.rotation_id)
        if (
            rotation.status is not GatewayKeyRotationStatus.KEY_GENERATED
            or rotation.version != command.expected_rotation_version
            or rotation.new_key_id is None
        ):
            raise GatewayKeyRotationOverlapProjectionConflict(
                "rotation is not the expected key-generated truth"
            )
        workspace = stores.workspaces.get_for_update(rotation.workspace_id)
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
            raise GatewayKeyRotationOverlapProjectionConflict(
                "overlap requires one settled authored and realized graph lineage"
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
            raise GatewayKeyRotationOverlapProjectionConflict(
                "workspace graph lineage is not owned by the rotation workspace"
            )
        authored = DEFAULT_GRAPH_CODEC.decode(authored_record.graph_descriptor)
        current = DEFAULT_GRAPH_CODEC.decode(current_record.graph_descriptor)
        realized = derive_gateway_key_rotation_overlap_graph(
            stores,
            rotation,
            authored,
            current,
        )
        projection_id = f"gateway-rotation-{rotation.rotation_id}-overlap"
        projection = RealizedGraphProjectionRecord.from_graph(
            projection_id=projection_id,
            workspace_id=rotation.workspace_id,
            source_authored_graph_id=authored_record.graph_id,
            projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
            projection_key=f"gateway-rotation:{rotation.rotation_id}:overlap",
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


def derive_gateway_key_rotation_overlap_graph(
    stores: Any,
    rotation: GatewayKeyRotation,
    authored: DeploymentGraph,
    current: DeploymentGraph,
) -> DeploymentGraph:
    """Derive exact A+B material from durable rotation and signing-key truth."""

    projections = _current_projections(authored, current)
    identity = (rotation.gateway_node_id, rotation.purpose)
    bindings = {
        binding.identity: binding for binding in authored.delegation_authorities
    }
    binding = bindings.get(identity)
    if binding is None or binding.issuer != rotation.issuer:
        raise GatewayKeyRotationOverlapProjectionConflict(
            "rotation target does not match an exact authored delegation binding"
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
    _require_rotation_keys(rotation, old_key, new_key)
    verification_keys = stores.delegation_signing_keys.list_for_verification(
        rotation.workspace_id,
        rotation.purpose,
        rotation.issuer,
    )
    if {value.key_id for value in verification_keys} != {
        rotation.old_key_id,
        rotation.new_key_id,
    } or len(verification_keys) != 2:
        raise GatewayKeyRotationOverlapProjectionConflict(
            "rotation verifier scope contains unexpected key truth"
        )
    current_target = projections[identity]
    expected_audience = f"gateway:{rotation.workspace_id}:{rotation.gateway_node_id}"
    if (
        current_target.issuer != rotation.issuer
        or current_target.audience != expected_audience
        or tuple(key.key_id for key in current_target.public_keys)
        != (rotation.old_key_id,)
        or current_target.public_keys[0] != old_key.public_key
    ):
        raise GatewayKeyRotationOverlapProjectionConflict(
            "current realized target is not exact old-key projection A"
        )
    projections[identity] = DelegationVerifierProjection(
        delegate_node_id=rotation.gateway_node_id,
        purpose=rotation.purpose,
        issuer=rotation.issuer,
        audience=expected_audience,
        projection_id=f"gateway-rotation-{rotation.rotation_id}-overlap-verifier",
        public_keys=(old_key.public_key, new_key.public_key),
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


def _current_projections(authored: Any, current: Any) -> dict[Any, Any]:
    binding_by_identity = {
        value.identity: value for value in authored.delegation_authorities
    }
    projections: dict[Any, Any] = {}
    for identity, binding in binding_by_identity.items():
        projection = current.node(binding.delegate_node_id).delegation_verifier_projection
        if (
            projection is None
            or projection.binding_identity != identity
            or projection.issuer != binding.issuer
        ):
            raise GatewayKeyRotationOverlapProjectionConflict(
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
        raise GatewayKeyRotationOverlapProjectionConflict(
            "current realized graph contains unbound verifier material"
        )
    return projections


def _require_rotation_keys(
    rotation: Any,
    old_key: RegisteredDelegationSigningKey,
    new_key: RegisteredDelegationSigningKey,
) -> None:
    if old_key.status is not RegisteredDelegationSigningKeyStatus.ACTIVE:
        raise GatewayKeyRotationOverlapProjectionConflict(
            "rotation old key is not active"
        )
    if new_key.status is not RegisteredDelegationSigningKeyStatus.VERIFY_ONLY:
        raise GatewayKeyRotationOverlapProjectionConflict(
            "rotation new key is not verify-only"
        )
    if new_key.private_key_reference != rotation.new_secret_reference:
        raise GatewayKeyRotationOverlapProjectionConflict(
            "rotation new key does not match generated custody reference"
        )


def _payload_text(payload: Any, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GatewayKeyRotationOverlapProjectionConflict(
            "overlap publication action evidence is incomplete"
        )
    return value


__all__ = [
    "GatewayKeyRotationOverlapProjectionAuthorizationDenied",
    "GatewayKeyRotationOverlapProjectionConflict",
    "GatewayKeyRotationOverlapProjectionError",
    "GatewayKeyRotationOverlapProjectionResult",
    "GatewayKeyRotationOverlapProjectionService",
    "PublishGatewayKeyRotationOverlapProjection",
    "derive_gateway_key_rotation_overlap_graph",
]
