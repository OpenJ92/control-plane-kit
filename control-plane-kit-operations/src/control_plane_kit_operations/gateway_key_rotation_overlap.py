"""Derive and publish the overlap verifier projection for one key rotation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from control_plane_kit_core.delegation_authority import DelegationAuthorityError
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DeploymentGraph, GraphDescriptorError
from control_plane_kit_operations.delegation_signing_keys import DelegationSigningKeyError
from control_plane_kit_operations.desired_realized_projections import (
    DesiredRealizedProjectionPublicationError,
    DesiredRealizedProjectionPublicationResult,
    PublishDesiredRealizedProjection,
    prepare_desired_realized_projection_publication,
    publish_desired_realized_projection_in_unit_of_work,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotation,
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationError,
)
from control_plane_kit_operations.gateway_key_rotation_projection import (
    GatewayKeyRotationProjectionConflict,
    build_gateway_key_rotation_projection_publication,
    derive_gateway_key_rotation_projection_graph,
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
        if (
            type(self.expected_rotation_version) is not int
            or self.expected_rotation_version < 1
        ):
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
                prepare_desired_realized_projection_publication(
                    unit_of_work,
                    command.session_id,
                    command.idempotency_key.value,
                )
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
        try:
            return build_gateway_key_rotation_projection_publication(
                unit_of_work,
                command,
                phase=GatewayKeyRotationDeploymentPhase.OVERLAP,
                created_at=created_at,
            )
        except GatewayKeyRotationProjectionConflict as error:
            raise GatewayKeyRotationOverlapProjectionConflict(str(error)) from error


def derive_gateway_key_rotation_overlap_graph(
    stores: Any,
    rotation: GatewayKeyRotation,
    authored: DeploymentGraph,
    current: DeploymentGraph,
) -> DeploymentGraph:
    """Derive exact A+B material from durable rotation and signing-key truth."""
    try:
        return derive_gateway_key_rotation_projection_graph(
            stores,
            rotation,
            authored,
            current,
            phase=GatewayKeyRotationDeploymentPhase.OVERLAP,
        )
    except GatewayKeyRotationProjectionConflict as error:
        raise GatewayKeyRotationOverlapProjectionConflict(str(error)) from error


__all__ = [
    "GatewayKeyRotationOverlapProjectionAuthorizationDenied",
    "GatewayKeyRotationOverlapProjectionConflict",
    "GatewayKeyRotationOverlapProjectionError",
    "GatewayKeyRotationOverlapProjectionResult",
    "GatewayKeyRotationOverlapProjectionService",
    "PublishGatewayKeyRotationOverlapProjection",
    "derive_gateway_key_rotation_overlap_graph",
]
