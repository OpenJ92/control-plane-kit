"""Complete gateway-key rotation around exact provider-version revocation IO."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Any, Callable, Protocol

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretReference,
    SecretUseIntent,
    SecretVersionRevocationGrant,
    SecretVersionRevocationReceipt,
)
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyRegistrationService,
    RegisteredDelegationSigningKeyStatus,
    RetireDelegationSigningKeyCommand,
    RevokeDelegationSigningKeyCommand,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    GatewayKeyRotation,
    GatewayKeyRotationConflict,
    GatewayKeyRotationDeploymentStatus,
    GatewayKeyRotationNotFound,
    GatewayKeyRotationRevocationCheckpoint,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.secret_providers import SecretProviderNotFound


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class GatewayKeyRotationCompletionError(ValueError):
    """Base bounded error for old-key retirement and revocation."""


class GatewayKeyRotationCompletionConflict(GatewayKeyRotationCompletionError):
    """Raised when durable rotation, key, graph, or provider truth diverges."""


class GatewayKeyRotationCompletionAuthorizationDenied(
    GatewayKeyRotationCompletionError
):
    """Raised before mutation when focused retirement authority is absent."""


class GatewayKeyRotationRevocationEffectOutcome(StrEnum):
    REVOKED = "revoked"
    DEFINITE_FAILURE = "definite-failure"
    UNCERTAIN = "uncertain"


class GatewayKeyRotationCompletionOutcome(StrEnum):
    COMPLETED = "completed"
    COMPLETED_REPLAY = "completed-replay"
    RETRYABLE = "retryable"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class GatewayKeyRotationRevocationEffectResult:
    """Bounded interpreter result; never contains provider or secret material."""

    outcome: GatewayKeyRotationRevocationEffectOutcome
    receipt: SecretVersionRevocationReceipt | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, GatewayKeyRotationRevocationEffectOutcome):
            raise GatewayKeyRotationCompletionError(
                "revocation effect outcome is unsupported"
            )
        succeeded = self.outcome is GatewayKeyRotationRevocationEffectOutcome.REVOKED
        if succeeded != isinstance(self.receipt, SecretVersionRevocationReceipt):
            raise GatewayKeyRotationCompletionError(
                "revocation effect receipt does not match outcome"
            )
        if succeeded != (self.failure_code is None):
            raise GatewayKeyRotationCompletionError(
                "revocation effect failure code does not match outcome"
            )
        if self.failure_code is not None:
            _identifier(self.failure_code, "failure_code")


class GatewayKeyRotationRevocationAdapter(Protocol):
    """Concrete provider interpretation composed outside operations."""

    def revoke_version(
        self,
        grant: SecretVersionRevocationGrant,
    ) -> GatewayKeyRotationRevocationEffectResult: ...


@dataclass(frozen=True)
class CompleteGatewayKeyRotation:
    rotation_id: str
    expected_retirement_ready_version: int
    actor_id: str
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        _identifier(self.rotation_id, "rotation_id")
        if (
            type(self.expected_retirement_ready_version) is not int
            or self.expected_retirement_ready_version < 1
        ):
            raise GatewayKeyRotationCompletionError(
                "expected retirement-ready version is malformed"
            )
        _identifier(self.actor_id, "actor_id")
        _scopes(self.actor_scopes)


@dataclass(frozen=True)
class GatewayKeyRotationRevocationAction:
    rotation_id: str
    retirement_ready_version: int
    revocation_prepared_version: int
    checkpoint: GatewayKeyRotationRevocationCheckpoint
    grant: SecretVersionRevocationGrant

    def __post_init__(self) -> None:
        _identifier(self.rotation_id, "rotation_id")
        if (
            type(self.retirement_ready_version) is not int
            or self.retirement_ready_version < 1
            or self.revocation_prepared_version != self.retirement_ready_version + 2
        ):
            raise GatewayKeyRotationCompletionError(
                "revocation action rotation lineage is malformed"
            )
        if not isinstance(self.checkpoint, GatewayKeyRotationRevocationCheckpoint):
            raise GatewayKeyRotationCompletionError(
                "revocation action checkpoint is malformed"
            )
        if not isinstance(self.grant, SecretVersionRevocationGrant):
            raise GatewayKeyRotationCompletionError(
                "revocation action grant is malformed"
            )
        if (
            self.grant.revocation_id != self.checkpoint.revocation_id
            or self.grant.provider_registration_id
            != self.checkpoint.provider_registration_id
            or self.grant.reference != self.checkpoint.secret_reference
            or self.grant.version_id != self.checkpoint.provider_version_id
            or self.grant.version_number
            != self.checkpoint.provider_version_number
            or self.grant.correlation_id != self.checkpoint.correlation_id
            or self.grant.revocation_fingerprint != self.checkpoint.action_digest
        ):
            raise GatewayKeyRotationCompletionError(
                "revocation action changed from its durable checkpoint"
            )


@dataclass(frozen=True)
class GatewayKeyRotationCompletionResult:
    rotation: GatewayKeyRotation
    outcome: GatewayKeyRotationCompletionOutcome
    action: GatewayKeyRotationRevocationAction | None = None
    receipt: SecretVersionRevocationReceipt | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.rotation, GatewayKeyRotation):
            raise GatewayKeyRotationCompletionError(
                "completion result rotation is malformed"
            )
        if not isinstance(self.outcome, GatewayKeyRotationCompletionOutcome):
            raise GatewayKeyRotationCompletionError(
                "completion result outcome is unsupported"
            )
        completed = self.outcome in {
            GatewayKeyRotationCompletionOutcome.COMPLETED,
            GatewayKeyRotationCompletionOutcome.COMPLETED_REPLAY,
        }
        if completed != (
            self.rotation.status is GatewayKeyRotationStatus.COMPLETED
        ):
            raise GatewayKeyRotationCompletionError(
                "completion result and rotation status disagree"
            )
        if completed != isinstance(self.receipt, SecretVersionRevocationReceipt):
            raise GatewayKeyRotationCompletionError(
                "completion receipt does not match outcome"
            )
        if self.outcome is GatewayKeyRotationCompletionOutcome.RETRYABLE:
            if self.action is None or self.failure_code is None:
                raise GatewayKeyRotationCompletionError(
                    "retryable result requires action and bounded failure"
                )
        elif self.outcome is GatewayKeyRotationCompletionOutcome.BLOCKED:
            if self.failure_code is None:
                raise GatewayKeyRotationCompletionError(
                    "blocked result requires bounded failure"
                )
        elif self.failure_code is not None:
            raise GatewayKeyRotationCompletionError(
                "successful completion cannot carry failure"
            )


class GatewayKeyRotationCompletionProgram:
    """Own public retirement, exact provider IO, and durable result folding."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        revocation_adapter: GatewayKeyRotationRevocationAdapter,
        clock: Callable[[], str],
        trusted_epoch_clock: Callable[[], int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._adapter = revocation_adapter
        self._clock = clock
        self._rotations = GatewayKeyRotationService(
            unit_of_work_factory,
            clock=trusted_epoch_clock,
        )
        self._keys = DelegationSigningKeyRegistrationService(unit_of_work_factory)

    def progress(
        self,
        command: CompleteGatewayKeyRotation,
    ) -> GatewayKeyRotationCompletionResult:
        if not isinstance(command, CompleteGatewayKeyRotation):
            raise TypeError("command must be CompleteGatewayKeyRotation")
        self._authorize(command.actor_scopes)
        rotation = self._rotation(command.rotation_id)
        if rotation.status is GatewayKeyRotationStatus.COMPLETED:
            self._require_version(rotation, command, offset=3)
            receipt = self._receipt_from_completed(rotation)
            return GatewayKeyRotationCompletionResult(
                rotation=rotation,
                outcome=GatewayKeyRotationCompletionOutcome.COMPLETED_REPLAY,
                receipt=receipt,
            )
        if rotation.status is GatewayKeyRotationStatus.BLOCKED:
            return GatewayKeyRotationCompletionResult(
                rotation=rotation,
                outcome=GatewayKeyRotationCompletionOutcome.BLOCKED,
                failure_code=rotation.failure_code,
            )
        action = self._prepare(command, rotation)
        effect = self._adapter.revoke_version(action.grant)
        if not isinstance(effect, GatewayKeyRotationRevocationEffectResult):
            return self._block(
                command,
                action,
                "revocation-malformed-result",
            )
        if effect.outcome is GatewayKeyRotationRevocationEffectOutcome.DEFINITE_FAILURE:
            return GatewayKeyRotationCompletionResult(
                rotation=self._rotation(command.rotation_id),
                outcome=GatewayKeyRotationCompletionOutcome.RETRYABLE,
                action=action,
                failure_code=effect.failure_code,
            )
        if effect.outcome is GatewayKeyRotationRevocationEffectOutcome.UNCERTAIN:
            return self._block(command, action, effect.failure_code)
        receipt = effect.receipt
        if receipt is None or not receipt.matches(action.grant):
            return self._block(command, action, "revocation-receipt-mismatch")
        completed = self._fold_success(command, action, receipt)
        return GatewayKeyRotationCompletionResult(
            rotation=completed,
            outcome=GatewayKeyRotationCompletionOutcome.COMPLETED,
            receipt=receipt,
        )

    def _prepare(
        self,
        command: CompleteGatewayKeyRotation,
        rotation: GatewayKeyRotation,
    ) -> GatewayKeyRotationRevocationAction:
        if rotation.status is GatewayKeyRotationStatus.RETIREMENT_READY:
            self._require_version(rotation, command, offset=0)
            checkpoint, _grant = self._derive_revocation(rotation, command.actor_id)
            old_key = self._old_key(rotation)
            if old_key.status is RegisteredDelegationSigningKeyStatus.VERIFY_ONLY:
                retired_at = self._time()
                self._keys.retire(
                    RetireDelegationSigningKeyCommand(
                        workspace_id=rotation.workspace_id,
                        purpose=rotation.purpose,
                        issuer=rotation.issuer,
                        key_id=rotation.old_key_id,
                        retired_by=command.actor_id,
                        retired_at=retired_at,
                        actor_scopes=(PolicyScope.DELEGATION_KEY_RETIRE,),
                    )
                )
            elif old_key.status is not RegisteredDelegationSigningKeyStatus.RETIRED:
                raise GatewayKeyRotationCompletionConflict(
                    "old delegation key is not eligible for retirement"
                )
            retired = self._rotations.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=rotation.rotation_id,
                    transition_id=f"{rotation.rotation_id}:old-key-retired",
                    expected_status=GatewayKeyRotationStatus.RETIREMENT_READY,
                    expected_version=rotation.version,
                    target_status=GatewayKeyRotationStatus.OLD_KEY_RETIRED,
                    advanced_by=command.actor_id,
                    advanced_at=self._time(),
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                    old_key_retired_at=self._old_key(rotation).retired_at,
                )
            )
            rotation = retired
        if rotation.status is GatewayKeyRotationStatus.OLD_KEY_RETIRED:
            self._require_version(rotation, command, offset=1)
            checkpoint, _grant = self._derive_revocation(rotation, command.actor_id)
            prepared = self._rotations.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=rotation.rotation_id,
                    transition_id=f"{rotation.rotation_id}:prepare-revocation",
                    expected_status=GatewayKeyRotationStatus.OLD_KEY_RETIRED,
                    expected_version=rotation.version,
                    target_status=GatewayKeyRotationStatus.REVOCATION_PREPARED,
                    advanced_by=command.actor_id,
                    advanced_at=checkpoint.prepared_at,
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                    revocation=checkpoint,
                )
            )
            rotation = prepared
        if rotation.status is not GatewayKeyRotationStatus.REVOCATION_PREPARED:
            raise GatewayKeyRotationCompletionConflict(
                "rotation is not ready for exact secret-version revocation"
            )
        self._require_version(rotation, command, offset=2)
        checkpoint, grant = self._derive_revocation(
            rotation,
            command.actor_id,
            prepared_at=rotation.revocation.prepared_at if rotation.revocation else None,
        )
        if checkpoint != rotation.revocation:
            raise GatewayKeyRotationCompletionConflict(
                "prepared revocation action changed after restart"
            )
        return GatewayKeyRotationRevocationAction(
            rotation_id=rotation.rotation_id,
            retirement_ready_version=command.expected_retirement_ready_version,
            revocation_prepared_version=rotation.version,
            checkpoint=checkpoint,
            grant=grant,
        )

    def _derive_revocation(
        self,
        rotation: GatewayKeyRotation,
        actor_id: str,
        *,
        prepared_at: str | None = None,
    ) -> tuple[
        GatewayKeyRotationRevocationCheckpoint,
        SecretVersionRevocationGrant,
    ]:
        self._validate_retirement_truth(rotation)
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            old_key = stores.delegation_signing_keys.get(
                rotation.workspace_id,
                rotation.purpose,
                rotation.issuer,
                rotation.old_key_id,
            )
            try:
                reference = stores.secret_references.get_active(
                    rotation.workspace_id,
                    old_key.private_key_reference,
                )
                provider = stores.secret_providers.require_active_registration(
                    rotation.workspace_id,
                    reference.provider_registration_id,
                )
            except SecretProviderNotFound as error:
                raise GatewayKeyRotationCompletionConflict(
                    "old key custody truth is not actively admitted"
                ) from error
            if (
                SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY
                not in reference.allowed_intents
            ):
                raise GatewayKeyRotationCompletionConflict(
                    "old key reference is not admitted for delegation signing"
                )
            version_id = reference.metadata.get("provider_version_id")
            version_number = reference.metadata.get("provider_version_number")
            if (
                not isinstance(version_id, str)
                or not _IDENTIFIER.fullmatch(version_id)
                or type(version_number) is not int
                or version_number < 1
            ):
                raise GatewayKeyRotationCompletionConflict(
                    "old key reference lacks exact provider version metadata"
                )
            unit_of_work.commit()
        correlation_id = f"{rotation.correlation_id}:revoke-old-version"
        semantics = {
            "rotation_id": rotation.rotation_id,
            "workspace_id": rotation.workspace_id,
            "provider_registration_id": provider.registration_id,
            "endpoint_reference": provider.endpoint_reference.reference_id,
            "credential_reference": provider.credential_reference.reference_id,
            "reference": old_key.private_key_reference.reference_id,
            "version_id": version_id,
            "version_number": version_number,
            "actor_subject": actor_id,
            "correlation_id": correlation_id,
        }
        digest = sha256(
            json.dumps(
                semantics,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        revocation_id = f"srevoke_{digest}"
        grant = SecretVersionRevocationGrant(
            revocation_id=revocation_id,
            workspace_id=rotation.workspace_id,
            provider_registration_id=provider.registration_id,
            endpoint_reference=provider.endpoint_reference,
            credential_reference=provider.credential_reference,
            reference=old_key.private_key_reference,
            version_id=version_id,
            version_number=version_number,
            actor_subject=actor_id,
            correlation_id=correlation_id,
            revocation_fingerprint=digest,
            operation_id=rotation.rotation_id,
        )
        checkpoint = GatewayKeyRotationRevocationCheckpoint(
            provider_registration_id=provider.registration_id,
            secret_reference=old_key.private_key_reference,
            provider_version_id=version_id,
            provider_version_number=version_number,
            revocation_id=revocation_id,
            correlation_id=correlation_id,
            action_digest=digest,
            prepared_at=prepared_at or self._time(),
        )
        return checkpoint, grant

    def _fold_success(
        self,
        command: CompleteGatewayKeyRotation,
        action: GatewayKeyRotationRevocationAction,
        receipt: SecretVersionRevocationReceipt,
    ) -> GatewayKeyRotation:
        rotation = self._rotation(command.rotation_id)
        self._match_action(rotation, action)
        old_key = self._old_key(rotation)
        if old_key.status is RegisteredDelegationSigningKeyStatus.RETIRED:
            self._keys.revoke(
                RevokeDelegationSigningKeyCommand(
                    workspace_id=rotation.workspace_id,
                    purpose=rotation.purpose,
                    issuer=rotation.issuer,
                    key_id=rotation.old_key_id,
                    revoked_by=command.actor_id,
                    revoked_at=self._time(),
                    actor_scopes=(PolicyScope.DELEGATION_KEY_REVOKE,),
                )
            )
        elif old_key.status is not RegisteredDelegationSigningKeyStatus.REVOKED:
            raise GatewayKeyRotationCompletionConflict(
                "old delegation key changed before completion"
            )
        current = self._rotation(command.rotation_id)
        try:
            return self._rotations.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=current.rotation_id,
                    transition_id=f"{current.rotation_id}:complete-revocation",
                    expected_status=GatewayKeyRotationStatus.REVOCATION_PREPARED,
                    expected_version=current.version,
                    target_status=GatewayKeyRotationStatus.COMPLETED,
                    advanced_by=command.actor_id,
                    advanced_at=self._time(),
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                    old_secret_revoked_at=self._time(),
                )
            )
        except GatewayKeyRotationConflict as error:
            raise GatewayKeyRotationCompletionConflict(str(error)) from error

    def _block(
        self,
        command: CompleteGatewayKeyRotation,
        action: GatewayKeyRotationRevocationAction,
        failure_code: str | None,
    ) -> GatewayKeyRotationCompletionResult:
        code = failure_code or "revocation-uncertain"
        _identifier(code, "failure_code")
        rotation = self._rotation(command.rotation_id)
        self._match_action(rotation, action)
        try:
            blocked = self._rotations.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=rotation.rotation_id,
                    transition_id=f"{rotation.rotation_id}:revocation-uncertain",
                    expected_status=GatewayKeyRotationStatus.REVOCATION_PREPARED,
                    expected_version=rotation.version,
                    target_status=GatewayKeyRotationStatus.BLOCKED,
                    advanced_by=command.actor_id,
                    advanced_at=self._time(),
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                    failure_code=code,
                )
            )
        except GatewayKeyRotationConflict as error:
            raise GatewayKeyRotationCompletionConflict(str(error)) from error
        return GatewayKeyRotationCompletionResult(
            rotation=blocked,
            outcome=GatewayKeyRotationCompletionOutcome.BLOCKED,
            failure_code=code,
        )

    def _validate_retirement_truth(self, rotation: GatewayKeyRotation) -> None:
        checkpoint = rotation.retirement_deployment
        if (
            checkpoint is None
            or checkpoint.status is not GatewayKeyRotationDeploymentStatus.ACCEPTED
            or checkpoint.accepted_current_graph_id is None
            or checkpoint.accepted_current_projection_id is None
        ):
            raise GatewayKeyRotationCompletionConflict(
                "accepted B-only retirement evidence is missing"
            )
        with self._unit_of_work_factory() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get(rotation.workspace_id)
            active = unit_of_work.stores.delegation_signing_keys.require_active(
                rotation.workspace_id,
                rotation.purpose,
                rotation.issuer,
            )
            unit_of_work.commit()
        if (
            workspace.current_graph_id != checkpoint.accepted_current_graph_id
            or workspace.current_realized_projection_id
            != checkpoint.accepted_current_projection_id
            or active.key_id != rotation.new_key_id
        ):
            raise GatewayKeyRotationCompletionConflict(
                "accepted B-only retirement truth changed"
            )

    def _match_action(
        self,
        rotation: GatewayKeyRotation,
        action: GatewayKeyRotationRevocationAction,
    ) -> None:
        if (
            rotation.status is not GatewayKeyRotationStatus.REVOCATION_PREPARED
            or rotation.version != action.revocation_prepared_version
            or rotation.revocation != action.checkpoint
        ):
            raise GatewayKeyRotationCompletionConflict(
                "prepared revocation action is stale"
            )
        self._validate_retirement_truth(rotation)

    def _old_key(self, rotation: GatewayKeyRotation):
        with self._unit_of_work_factory() as unit_of_work:
            value = unit_of_work.stores.delegation_signing_keys.get(
                rotation.workspace_id,
                rotation.purpose,
                rotation.issuer,
                rotation.old_key_id,
            )
            unit_of_work.commit()
            return value

    def _receipt_from_completed(
        self,
        rotation: GatewayKeyRotation,
    ) -> SecretVersionRevocationReceipt:
        checkpoint = rotation.revocation
        if checkpoint is None:
            raise GatewayKeyRotationCompletionConflict(
                "completed rotation lacks revocation checkpoint"
            )
        return SecretVersionRevocationReceipt(
            revocation_id=checkpoint.revocation_id,
            provider_registration_id=checkpoint.provider_registration_id,
            reference=checkpoint.secret_reference,
            version_id=checkpoint.provider_version_id,
            version_number=checkpoint.provider_version_number,
        )

    def _rotation(self, rotation_id: str) -> GatewayKeyRotation:
        try:
            return self._rotations.get(rotation_id)
        except GatewayKeyRotationNotFound as error:
            raise GatewayKeyRotationCompletionConflict(
                "gateway key rotation was not found"
            ) from error

    @staticmethod
    def _require_version(
        rotation: GatewayKeyRotation,
        command: CompleteGatewayKeyRotation,
        *,
        offset: int,
    ) -> None:
        if rotation.version != command.expected_retirement_ready_version + offset:
            raise GatewayKeyRotationCompletionConflict(
                "rotation completion lineage is stale"
            )

    @staticmethod
    def _authorize(scopes: tuple[PolicyScope, ...]) -> None:
        required = {
            PolicyScope.DELEGATION_KEY_ROTATE,
            PolicyScope.DELEGATION_KEY_RETIRE,
            PolicyScope.DELEGATION_KEY_REVOKE,
            PolicyScope.SECRET_PROVIDER_REVOKE,
        }
        missing = required.difference(scopes)
        if missing:
            raise GatewayKeyRotationCompletionAuthorizationDenied(
                "gateway key rotation completion lacks focused authority"
            )

    def _time(self) -> str:
        value = self._clock()
        if not isinstance(value, str) or not value or len(value) > 128:
            raise GatewayKeyRotationCompletionError(
                "trusted clock returned malformed time"
            )
        return value


def _scopes(value: tuple[PolicyScope, ...]) -> None:
    if (
        not isinstance(value, tuple)
        or not value
        or not all(isinstance(scope, PolicyScope) for scope in value)
    ):
        raise GatewayKeyRotationCompletionError("actor scopes are malformed")


def _identifier(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise GatewayKeyRotationCompletionError(f"{field_name} is malformed")


__all__ = [
    "CompleteGatewayKeyRotation",
    "GatewayKeyRotationCompletionAuthorizationDenied",
    "GatewayKeyRotationCompletionConflict",
    "GatewayKeyRotationCompletionError",
    "GatewayKeyRotationCompletionOutcome",
    "GatewayKeyRotationCompletionProgram",
    "GatewayKeyRotationCompletionResult",
    "GatewayKeyRotationRevocationAction",
    "GatewayKeyRotationRevocationAdapter",
    "GatewayKeyRotationRevocationEffectOutcome",
    "GatewayKeyRotationRevocationEffectResult",
]
