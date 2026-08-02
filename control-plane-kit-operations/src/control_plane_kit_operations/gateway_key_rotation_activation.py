"""Activate a gateway rotation key and enforce its durable grant-drain barrier."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import re
from typing import Any, Callable

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretUseIntent
from control_plane_kit_operations.delegation_signing_keys import (
    ActivateDelegationSigningKeyCommand,
    DelegationSigningKeyError,
    DelegationSigningKeyRegistrationService,
    RegisteredDelegationSigningKey,
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    GatewayKeyRotation,
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationDeploymentStatus,
    GatewayKeyRotationError,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
    GatewayKeyRotationTransition,
)
from control_plane_kit_operations.secret_providers import SecretProviderNotFound
from control_plane_kit_operations.workflows import InvalidOperationCommand


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class GatewayKeyRotationActivationError(ValueError):
    """Base bounded failure for key activation and grant draining."""


class GatewayKeyRotationActivationConflict(GatewayKeyRotationActivationError):
    """Raised when durable rotation, graph, key, or reference truth diverges."""


class GatewayKeyRotationActivationAuthorizationDenied(
    GatewayKeyRotationActivationError
):
    """Raised before progress when focused rotation authority is absent."""


class GatewayKeyRotationActivationOutcome(StrEnum):
    """One bounded observation of the activation and drain program."""

    WAITING = "waiting"
    READY_FOR_RETIREMENT = "ready-for-retirement"


@dataclass(frozen=True)
class ProgressGatewayKeyRotationActivation:
    rotation_id: str
    expected_overlap_version: int
    actor_id: str
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        _identifier(self.rotation_id, "rotation_id")
        _identifier(self.actor_id, "actor_id")
        if (
            type(self.expected_overlap_version) is not int
            or self.expected_overlap_version < 1
        ):
            raise InvalidOperationCommand(
                "expected_overlap_version must be positive"
            )
        if not isinstance(self.actor_scopes, tuple) or not all(
            isinstance(scope, PolicyScope) for scope in self.actor_scopes
        ):
            raise InvalidOperationCommand("actor_scopes must be a typed tuple")
        object.__setattr__(
            self,
            "actor_scopes",
            tuple(sorted(set(self.actor_scopes), key=lambda scope: scope.value)),
        )


@dataclass(frozen=True)
class GatewayKeyRotationActivationResult:
    rotation: GatewayKeyRotation
    outcome: GatewayKeyRotationActivationOutcome
    observed_at_epoch: int
    drain_deadline_epoch: int

    def __post_init__(self) -> None:
        if not isinstance(self.rotation, GatewayKeyRotation):
            raise GatewayKeyRotationActivationError("rotation result is malformed")
        if self.rotation.status is not GatewayKeyRotationStatus.DRAINING_OLD_GRANTS:
            raise GatewayKeyRotationActivationError(
                "activation result requires draining rotation truth"
            )
        if not isinstance(self.outcome, GatewayKeyRotationActivationOutcome):
            raise GatewayKeyRotationActivationError("activation outcome is malformed")
        for value, name in (
            (self.observed_at_epoch, "observed_at_epoch"),
            (self.drain_deadline_epoch, "drain_deadline_epoch"),
        ):
            if type(value) is not int or value < 0:
                raise GatewayKeyRotationActivationError(f"{name} is malformed")
        waiting = self.observed_at_epoch < self.drain_deadline_epoch
        if waiting != (self.outcome is GatewayKeyRotationActivationOutcome.WAITING):
            raise GatewayKeyRotationActivationError(
                "activation outcome disagrees with durable drain deadline"
            )


@dataclass(frozen=True)
class _ActivationSnapshot:
    rotation: GatewayKeyRotation
    old_key: RegisteredDelegationSigningKey
    new_key: RegisteredDelegationSigningKey
    transitions: tuple[GatewayKeyRotationTransition, ...]


class GatewayKeyRotationActivationProgram:
    """Converge accepted overlap to one signer and a durable drain barrier."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], str],
        trusted_epoch_clock: Callable[[], int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._trusted_epoch_clock = trusted_epoch_clock
        self._keys = DelegationSigningKeyRegistrationService(
            unit_of_work_factory
        )
        self._rotations = GatewayKeyRotationService(
            unit_of_work_factory,
            clock=trusted_epoch_clock,
        )

    def progress(
        self,
        command: ProgressGatewayKeyRotationActivation,
    ) -> GatewayKeyRotationActivationResult:
        if not isinstance(command, ProgressGatewayKeyRotationActivation):
            raise TypeError(
                "command must be ProgressGatewayKeyRotationActivation"
            )
        self._require_authority(command)
        snapshot = self._snapshot(command)
        rotation = snapshot.rotation
        self._validate_lineage(snapshot, command)

        if rotation.status is GatewayKeyRotationStatus.OVERLAP_READY:
            if (
                snapshot.old_key.status
                is RegisteredDelegationSigningKeyStatus.ACTIVE
                and snapshot.new_key.status
                is RegisteredDelegationSigningKeyStatus.VERIFY_ONLY
            ):
                try:
                    new_key = self._keys.activate(
                        ActivateDelegationSigningKeyCommand(
                            workspace_id=rotation.workspace_id,
                            purpose=rotation.purpose,
                            issuer=rotation.issuer,
                            key_id=snapshot.new_key.key_id,
                            activated_by=command.actor_id,
                            activated_at=self._clock(),
                            actor_scopes=command.actor_scopes,
                        )
                    )
                except DelegationSigningKeyError as error:
                    raise GatewayKeyRotationActivationConflict(str(error)) from error
            elif (
                snapshot.old_key.status
                is RegisteredDelegationSigningKeyStatus.VERIFY_ONLY
                and snapshot.new_key.status
                is RegisteredDelegationSigningKeyStatus.ACTIVE
            ):
                # A prior process committed activation but did not fold the aggregate.
                new_key = snapshot.new_key
            else:
                raise GatewayKeyRotationActivationConflict(
                    "rotation keys are not exact active-A/verify-B overlap truth"
                )
            if new_key.activated_at is None:
                raise GatewayKeyRotationActivationConflict(
                    "active replacement key lacks activation evidence"
                )
            rotation = self._advance_new_key_active(
                rotation,
                command,
                activated_at=new_key.activated_at,
            )

        if rotation.status is GatewayKeyRotationStatus.NEW_KEY_ACTIVE:
            rotation = self._advance_draining(rotation, command)

        if rotation.status is not GatewayKeyRotationStatus.DRAINING_OLD_GRANTS:
            raise GatewayKeyRotationActivationConflict(
                "rotation is not in the activation or drain phase"
            )
        if rotation.drain_deadline_epoch is None:
            raise GatewayKeyRotationActivationConflict(
                "draining rotation lacks a durable deadline"
            )
        now = self._trusted_epoch_clock()
        if type(now) is not int or now < 0:
            raise GatewayKeyRotationActivationError(
                "trusted clock returned malformed time"
            )
        outcome = (
            GatewayKeyRotationActivationOutcome.WAITING
            if now < rotation.drain_deadline_epoch
            else GatewayKeyRotationActivationOutcome.READY_FOR_RETIREMENT
        )
        return GatewayKeyRotationActivationResult(
            rotation=rotation,
            outcome=outcome,
            observed_at_epoch=now,
            drain_deadline_epoch=rotation.drain_deadline_epoch,
        )

    def _snapshot(
        self,
        command: ProgressGatewayKeyRotationActivation,
    ) -> _ActivationSnapshot:
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            try:
                rotation = stores.gateway_key_rotations.get(command.rotation_id)
                workspace = stores.workspaces.get(rotation.workspace_id)
                if rotation.new_key_id is None:
                    raise GatewayKeyRotationActivationConflict(
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
                reference = stores.secret_references.get_active(
                    rotation.workspace_id,
                    rotation.new_secret_reference,
                )
                if (
                    SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY
                    not in reference.allowed_intents
                ):
                    raise GatewayKeyRotationActivationConflict(
                        "replacement reference is not admitted for delegation signing"
                    )
                transitions = stores.gateway_key_rotations.transitions(
                    rotation.rotation_id
                )
            except (
                GatewayKeyRotationError,
                DelegationSigningKeyError,
                SecretProviderNotFound,
                KeyError,
            ) as error:
                raise GatewayKeyRotationActivationConflict(
                    "activation prerequisite truth is missing or inactive"
                ) from error
            _accepted_overlap(rotation, workspace)
            unit_of_work.commit()
        return _ActivationSnapshot(rotation, old_key, new_key, transitions)

    def _validate_lineage(
        self,
        snapshot: _ActivationSnapshot,
        command: ProgressGatewayKeyRotationActivation,
    ) -> None:
        rotation = snapshot.rotation
        if snapshot.new_key.private_key_reference != rotation.new_secret_reference:
            raise GatewayKeyRotationActivationConflict(
                "replacement key references different secret custody"
            )
        new_transition_id = _transition_id(rotation.rotation_id, "new-key-active")
        drain_transition_id = _transition_id(rotation.rotation_id, "draining")
        transition_ids = {item.transition_id for item in snapshot.transitions}
        expected = command.expected_overlap_version
        if rotation.status is GatewayKeyRotationStatus.OVERLAP_READY:
            valid = rotation.version == expected and not {
                new_transition_id,
                drain_transition_id,
            }.intersection(transition_ids)
        elif rotation.status is GatewayKeyRotationStatus.NEW_KEY_ACTIVE:
            valid = (
                rotation.version == expected + 1
                and new_transition_id in transition_ids
                and drain_transition_id not in transition_ids
            )
        elif rotation.status is GatewayKeyRotationStatus.DRAINING_OLD_GRANTS:
            valid = (
                rotation.version == expected + 2
                and {new_transition_id, drain_transition_id}.issubset(transition_ids)
            )
        else:
            valid = False
        if not valid:
            raise GatewayKeyRotationActivationConflict(
                "rotation activation lineage is stale or foreign"
            )
        if rotation.status in {
            GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
            GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
        } and not (
            snapshot.old_key.status
            is RegisteredDelegationSigningKeyStatus.VERIFY_ONLY
            and snapshot.new_key.status
            is RegisteredDelegationSigningKeyStatus.ACTIVE
        ):
            raise GatewayKeyRotationActivationConflict(
                "durable signer truth disagrees with rotation state"
            )

    def _advance_new_key_active(
        self,
        rotation: GatewayKeyRotation,
        command: ProgressGatewayKeyRotationActivation,
        *,
        activated_at: str,
    ) -> GatewayKeyRotation:
        try:
            return self._rotations.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=rotation.rotation_id,
                    transition_id=_transition_id(
                        rotation.rotation_id, "new-key-active"
                    ),
                    expected_status=GatewayKeyRotationStatus.OVERLAP_READY,
                    expected_version=rotation.version,
                    target_status=GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
                    advanced_by=command.actor_id,
                    advanced_at=self._clock(),
                    actor_scopes=command.actor_scopes,
                    new_key_activated_at=activated_at,
                )
            )
        except GatewayKeyRotationError as error:
            raise GatewayKeyRotationActivationConflict(str(error)) from error

    def _advance_draining(
        self,
        rotation: GatewayKeyRotation,
        command: ProgressGatewayKeyRotationActivation,
    ) -> GatewayKeyRotation:
        if rotation.status is GatewayKeyRotationStatus.DRAINING_OLD_GRANTS:
            return rotation
        try:
            return self._rotations.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=rotation.rotation_id,
                    transition_id=_transition_id(rotation.rotation_id, "draining"),
                    expected_status=GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
                    expected_version=rotation.version,
                    target_status=GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
                    advanced_by=command.actor_id,
                    advanced_at=self._clock(),
                    actor_scopes=command.actor_scopes,
                )
            )
        except GatewayKeyRotationError as error:
            raise GatewayKeyRotationActivationConflict(str(error)) from error

    @staticmethod
    def _require_authority(command: ProgressGatewayKeyRotationActivation) -> None:
        required = {
            PolicyScope.DELEGATION_KEY_ROTATE,
            PolicyScope.DELEGATION_KEY_ACTIVATE,
        }
        if not required.issubset(command.actor_scopes):
            raise GatewayKeyRotationActivationAuthorizationDenied(
                "rotation activation requires rotate and key-activate authority"
            )


def _accepted_overlap(rotation: GatewayKeyRotation, workspace: Any) -> None:
    checkpoint = rotation.overlap_deployment
    if (
        checkpoint is None
        or checkpoint.phase is not GatewayKeyRotationDeploymentPhase.OVERLAP
        or checkpoint.status is not GatewayKeyRotationDeploymentStatus.ACCEPTED
        or checkpoint.accepted_current_graph_id
        != checkpoint.desired_authored_graph_id
        or checkpoint.accepted_current_projection_id
        != checkpoint.desired_realized_projection_id
        or workspace.current_graph_id != checkpoint.accepted_current_graph_id
        or workspace.current_realized_projection_id
        != checkpoint.accepted_current_projection_id
    ):
        raise GatewayKeyRotationActivationConflict(
            "rotation lacks accepted current overlap graph evidence"
        )


def _transition_id(rotation_id: str, stage: str) -> str:
    digest = sha256(rotation_id.encode("utf-8")).hexdigest()
    return f"gkrot-activation:{digest}:{stage}"


def _identifier(value: object, name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise InvalidOperationCommand(f"{name} is malformed")


__all__ = [
    "GatewayKeyRotationActivationAuthorizationDenied",
    "GatewayKeyRotationActivationConflict",
    "GatewayKeyRotationActivationError",
    "GatewayKeyRotationActivationOutcome",
    "GatewayKeyRotationActivationProgram",
    "GatewayKeyRotationActivationResult",
    "ProgressGatewayKeyRotationActivation",
]
