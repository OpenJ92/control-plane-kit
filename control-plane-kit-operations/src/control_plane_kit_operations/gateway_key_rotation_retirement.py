"""Publish the exact B-only verifier projection for gateway key retirement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from control_plane_kit_core.delegation_authority import DelegationAuthorityError
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import GraphDescriptorError
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyError,
)
from control_plane_kit_operations.desired_realized_projections import (
    DesiredRealizedProjectionPublicationError,
    DesiredRealizedProjectionPublicationResult,
    publish_desired_realized_projection_in_unit_of_work,
)
from control_plane_kit_operations.gateway_key_rotation_projection import (
    GatewayKeyRotationProjectionConflict,
    build_gateway_key_rotation_projection_publication,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationError,
)
from control_plane_kit_operations.workflows import IdempotencyKey, InvalidOperationCommand


class GatewayKeyRotationRetirementProjectionError(RuntimeError):
    """Base bounded failure for B-only projection publication."""


class GatewayKeyRotationRetirementProjectionConflict(
    GatewayKeyRotationRetirementProjectionError
):
    """Raised when rotation, graph, key, deadline, or replay truth diverges."""


class GatewayKeyRotationRetirementProjectionAuthorizationDenied(
    GatewayKeyRotationRetirementProjectionError
):
    """Raised when focused key-rotation authority is absent."""


@dataclass(frozen=True)
class PublishGatewayKeyRotationRetirementProjection:
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
class GatewayKeyRotationRetirementProjectionResult:
    rotation_id: str
    publication: DesiredRealizedProjectionPublicationResult


class GatewayKeyRotationRetirementProjectionService:
    """Publish exact G[B] inside one transaction after the durable drain."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], str],
        trusted_epoch_clock: Callable[[], int],
        action_id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._trusted_epoch_clock = trusted_epoch_clock
        self._action_id_factory = action_id_factory

    def execute(
        self,
        command: PublishGatewayKeyRotationRetirementProjection,
    ) -> GatewayKeyRotationRetirementProjectionResult:
        if not isinstance(command, PublishGatewayKeyRotationRetirementProjection):
            raise TypeError(
                "command must be PublishGatewayKeyRotationRetirementProjection"
            )
        if PolicyScope.DELEGATION_KEY_ROTATE not in command.actor_scopes:
            raise GatewayKeyRotationRetirementProjectionAuthorizationDenied(
                "retirement projection publication requires delegation-key.rotate"
            )
        with self._unit_of_work_factory() as unit_of_work:
            created_at = self._clock()
            try:
                publication_command = (
                    build_gateway_key_rotation_projection_publication(
                        unit_of_work,
                        command,
                        phase=GatewayKeyRotationDeploymentPhase.RETIREMENT,
                        created_at=created_at,
                        trusted_epoch=self._trusted_epoch_clock(),
                    )
                )
                publication = publish_desired_realized_projection_in_unit_of_work(
                    unit_of_work,
                    publication_command,
                    created_at=created_at,
                    action_id=self._action_id_factory(),
                )
            except GatewayKeyRotationRetirementProjectionError:
                raise
            except (
                DelegationAuthorityError,
                DelegationSigningKeyError,
                DesiredRealizedProjectionPublicationError,
                GatewayKeyRotationError,
                GatewayKeyRotationProjectionConflict,
                GraphDescriptorError,
                KeyError,
                ValueError,
            ) as error:
                raise GatewayKeyRotationRetirementProjectionConflict(
                    str(error)
                ) from error
            unit_of_work.commit()
            return GatewayKeyRotationRetirementProjectionResult(
                command.rotation_id,
                publication,
            )


__all__ = [
    "GatewayKeyRotationRetirementProjectionAuthorizationDenied",
    "GatewayKeyRotationRetirementProjectionConflict",
    "GatewayKeyRotationRetirementProjectionError",
    "GatewayKeyRotationRetirementProjectionResult",
    "GatewayKeyRotationRetirementProjectionService",
    "PublishGatewayKeyRotationRetirementProjection",
]
