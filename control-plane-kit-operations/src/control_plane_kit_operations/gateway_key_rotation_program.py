"""Approved gateway-key generation as a resumable prepare/effect/fold program."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import re
from typing import Any, Callable

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.delegation_key_generation import (
    AdmitGeneratedDelegationSigningKey,
    AdmittedGeneratedDelegationSigningKey,
    DelegationKeyGenerationConflict,
    DelegationKeyGenerationEvidence,
    DelegationKeyGenerationGrant,
    DelegationKeyGenerationService,
    GenerateDelegationSigningKey,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    GatewayKeyRotation,
    GatewayKeyRotationConflict,
    GatewayKeyRotationNotFound,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.secret_providers import SecretProviderNotFound


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class GatewayKeyRotationGenerationProgramError(ValueError):
    """Base bounded failure for the generation phase of key rotation."""


class GatewayKeyRotationGenerationProgramConflict(
    GatewayKeyRotationGenerationProgramError
):
    """Raised when durable rotation, provider, action, or evidence diverges."""


class GatewayKeyRotationGenerationProgramAuthorizationDenied(
    GatewayKeyRotationGenerationProgramError
):
    """Raised when focused rotation-generation authority is absent."""


class GatewayKeyGenerationOutcome(StrEnum):
    GENERATED = "generated"
    DEFINITE_FAILURE = "definite-failure"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class PrepareGatewayKeyRotationGeneration:
    rotation_id: str
    expected_version: int
    actor_subject: str
    prepared_by: str
    prepared_at: str
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.rotation_id, "rotation_id"),
            (self.actor_subject, "actor_subject"),
            (self.prepared_by, "prepared_by"),
        ):
            _identifier(value, name)
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise GatewayKeyRotationGenerationProgramError(
                "expected rotation version is malformed"
            )
        _text(self.prepared_at, "prepared_at")
        _scopes(
            self.actor_scopes,
            PolicyScope.DELEGATION_KEY_ROTATE,
            PolicyScope.DELEGATION_KEY_GENERATE,
        )


@dataclass(frozen=True)
class GatewayKeyRotationGenerationAction:
    rotation_id: str
    prepared_transition_id: str
    expected_rotation_version: int
    prepared_rotation_version: int
    provider_registration_id: str
    action_digest: str
    grant: DelegationKeyGenerationGrant

    def __post_init__(self) -> None:
        for value, name in (
            (self.rotation_id, "rotation_id"),
            (self.prepared_transition_id, "prepared_transition_id"),
            (self.provider_registration_id, "provider_registration_id"),
        ):
            _identifier(value, name)
        if (
            type(self.expected_rotation_version) is not int
            or self.expected_rotation_version < 1
            or self.prepared_rotation_version != self.expected_rotation_version + 1
        ):
            raise GatewayKeyRotationGenerationProgramError(
                "generation action rotation lineage is malformed"
            )
        if not isinstance(self.action_digest, str) or not _DIGEST.fullmatch(
            self.action_digest
        ):
            raise GatewayKeyRotationGenerationProgramError(
                "generation action digest is malformed"
            )
        if not isinstance(self.grant, DelegationKeyGenerationGrant):
            raise GatewayKeyRotationGenerationProgramError(
                "generation action grant is malformed"
            )
        if (
            self.grant.provider_registration_id != self.provider_registration_id
            or self.grant.custody_grant.custody_fingerprint != self.action_digest
        ):
            raise GatewayKeyRotationGenerationProgramError(
                "generation action does not match its custody grant"
            )

    def descriptor(self) -> dict[str, object]:
        """Return bounded operator evidence, never provider credentials."""

        return {
            "rotation_id": self.rotation_id,
            "prepared_transition_id": self.prepared_transition_id,
            "expected_rotation_version": self.expected_rotation_version,
            "prepared_rotation_version": self.prepared_rotation_version,
            "provider_registration_id": self.provider_registration_id,
            "action_digest": self.action_digest,
            "reference_provider_id": self.grant.reference.provider_id.value,
            "purpose": self.grant.purpose.value,
            "issuer": self.grant.issuer,
            "correlation_id": self.grant.correlation_id,
        }


@dataclass(frozen=True)
class GatewayKeyGenerationResult:
    outcome: GatewayKeyGenerationOutcome
    evidence: DelegationKeyGenerationEvidence | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, GatewayKeyGenerationOutcome):
            raise GatewayKeyRotationGenerationProgramError(
                "generation outcome is unsupported"
            )
        generated = self.outcome is GatewayKeyGenerationOutcome.GENERATED
        if generated != (self.evidence is not None):
            raise GatewayKeyRotationGenerationProgramError(
                "generation evidence does not match outcome"
            )
        failed = self.outcome in {
            GatewayKeyGenerationOutcome.DEFINITE_FAILURE,
            GatewayKeyGenerationOutcome.UNCERTAIN,
        }
        if failed != (self.failure_code is not None):
            raise GatewayKeyRotationGenerationProgramError(
                "generation failure evidence does not match outcome"
            )
        if self.evidence is not None and not isinstance(
            self.evidence, DelegationKeyGenerationEvidence
        ):
            raise GatewayKeyRotationGenerationProgramError(
                "generation evidence is malformed"
            )
        if self.failure_code is not None:
            _identifier(self.failure_code, "failure_code")

    @classmethod
    def generated(
        cls,
        evidence: DelegationKeyGenerationEvidence,
    ) -> "GatewayKeyGenerationResult":
        return cls(GatewayKeyGenerationOutcome.GENERATED, evidence=evidence)

    @classmethod
    def definite_failure(cls, code: str) -> "GatewayKeyGenerationResult":
        return cls(GatewayKeyGenerationOutcome.DEFINITE_FAILURE, failure_code=code)

    @classmethod
    def uncertain(cls, code: str) -> "GatewayKeyGenerationResult":
        return cls(GatewayKeyGenerationOutcome.UNCERTAIN, failure_code=code)


@dataclass(frozen=True)
class SubmitGatewayKeyRotationGeneration:
    action: GatewayKeyRotationGenerationAction
    result: GatewayKeyGenerationResult
    submitted_by: str
    submitted_at: str
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.action, GatewayKeyRotationGenerationAction):
            raise GatewayKeyRotationGenerationProgramError(
                "generation submission action is malformed"
            )
        if not isinstance(self.result, GatewayKeyGenerationResult):
            raise GatewayKeyRotationGenerationProgramError(
                "generation submission result is malformed"
            )
        _identifier(self.submitted_by, "submitted_by")
        _text(self.submitted_at, "submitted_at")
        _scopes(
            self.actor_scopes,
            PolicyScope.DELEGATION_KEY_ROTATE,
            PolicyScope.DELEGATION_KEY_REGISTER,
        )


@dataclass(frozen=True)
class GatewayKeyRotationGenerationProgramResult:
    rotation: GatewayKeyRotation
    outcome: GatewayKeyGenerationOutcome
    next_action: GatewayKeyRotationGenerationAction | None = None
    admitted: AdmittedGeneratedDelegationSigningKey | None = None
    replayed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.rotation, GatewayKeyRotation):
            raise GatewayKeyRotationGenerationProgramError(
                "generation program rotation is malformed"
            )
        if not isinstance(self.outcome, GatewayKeyGenerationOutcome):
            raise GatewayKeyRotationGenerationProgramError(
                "generation program outcome is unsupported"
            )
        retrying = self.outcome is GatewayKeyGenerationOutcome.DEFINITE_FAILURE
        if retrying != (self.next_action is not None):
            raise GatewayKeyRotationGenerationProgramError(
                "generation retry action does not match outcome"
            )
        if (
            self.admitted is not None
            and self.outcome is not GatewayKeyGenerationOutcome.GENERATED
        ):
            raise GatewayKeyRotationGenerationProgramError(
                "generation admission does not match outcome"
            )
        if type(self.replayed) is not bool:
            raise GatewayKeyRotationGenerationProgramError(
                "generation replay flag is malformed"
            )


class GatewayKeyRotationGenerationProgram:
    """Advance one approved rotation only to its next effect boundary."""

    def __init__(
        self,
        unit_of_work_factory: Any,
        *,
        clock: Callable[[], int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._rotations = GatewayKeyRotationService(
            unit_of_work_factory,
            clock=clock,
        )
        self._generation = DelegationKeyGenerationService(unit_of_work_factory)

    def prepare(
        self,
        command: PrepareGatewayKeyRotationGeneration,
    ) -> GatewayKeyRotationGenerationAction:
        if not isinstance(command, PrepareGatewayKeyRotationGeneration):
            raise TypeError("command must be PrepareGatewayKeyRotationGeneration")
        rotation = self._rotation(command.rotation_id)
        if rotation.status is GatewayKeyRotationStatus.APPROVED:
            if rotation.version != command.expected_version:
                raise GatewayKeyRotationGenerationProgramConflict(
                    "rotation expected state is stale"
                )
            provider_registration_id = self._active_provider_registration(rotation)
            grant = self._prepare_grant(
                rotation,
                provider_registration_id,
                command,
            )
            action_digest = grant.custody_grant.custody_fingerprint
            transition_id = f"{rotation.rotation_id}:prepare-generation"
            try:
                prepared = self._rotations.advance(
                    AdvanceGatewayKeyRotation(
                        rotation_id=rotation.rotation_id,
                        transition_id=transition_id,
                        expected_status=GatewayKeyRotationStatus.APPROVED,
                        expected_version=rotation.version,
                        target_status=GatewayKeyRotationStatus.GENERATION_PREPARED,
                        advanced_by=command.prepared_by,
                        advanced_at=command.prepared_at,
                        actor_scopes=command.actor_scopes,
                        generation_provider_registration_id=provider_registration_id,
                        generation_action_digest=action_digest,
                    )
                )
            except GatewayKeyRotationConflict as error:
                raise GatewayKeyRotationGenerationProgramConflict(str(error)) from error
            return self._action(rotation, prepared, grant, transition_id)
        if rotation.status is GatewayKeyRotationStatus.GENERATION_PREPARED:
            if rotation.version != command.expected_version + 1:
                raise GatewayKeyRotationGenerationProgramConflict(
                    "prepared generation lineage is stale"
                )
            provider_registration_id = rotation.generation_provider_registration_id
            if provider_registration_id is None:
                raise GatewayKeyRotationGenerationProgramConflict(
                    "prepared generation provider evidence is missing"
                )
            transition = self._prepared_transition(rotation)
            grant = self._prepare_grant(
                rotation,
                provider_registration_id,
                command,
                requested_at=transition.advanced_at,
            )
            action = self._action(
                replace(
                    rotation,
                    status=GatewayKeyRotationStatus.APPROVED,
                    version=rotation.version - 1,
                ),
                rotation,
                grant,
                transition.transition_id,
            )
            if action.action_digest != rotation.generation_action_digest:
                raise GatewayKeyRotationGenerationProgramConflict(
                    "prepared generation action changed after restart"
                )
            return action
        raise GatewayKeyRotationGenerationProgramConflict(
            "rotation is not approved for key generation"
        )

    def submit(
        self,
        command: SubmitGatewayKeyRotationGeneration,
    ) -> GatewayKeyRotationGenerationProgramResult:
        if not isinstance(command, SubmitGatewayKeyRotationGeneration):
            raise TypeError("command must be SubmitGatewayKeyRotationGeneration")
        rotation = self._rotation(command.action.rotation_id)
        self._match_persisted_action(rotation, command.action)
        if rotation.status is GatewayKeyRotationStatus.KEY_GENERATED:
            return self._generated_replay(rotation, command)
        if rotation.status is GatewayKeyRotationStatus.BLOCKED:
            if (
                command.result.outcome is not GatewayKeyGenerationOutcome.UNCERTAIN
                or command.result.failure_code != rotation.failure_code
            ):
                raise GatewayKeyRotationGenerationProgramConflict(
                    "blocked generation cannot accept another outcome"
                )
            return GatewayKeyRotationGenerationProgramResult(
                rotation=rotation,
                outcome=GatewayKeyGenerationOutcome.UNCERTAIN,
            )
        if command.result.outcome is GatewayKeyGenerationOutcome.DEFINITE_FAILURE:
            return GatewayKeyRotationGenerationProgramResult(
                rotation=rotation,
                outcome=GatewayKeyGenerationOutcome.DEFINITE_FAILURE,
                next_action=command.action,
            )
        if command.result.outcome is GatewayKeyGenerationOutcome.UNCERTAIN:
            try:
                blocked = self._rotations.advance(
                    AdvanceGatewayKeyRotation(
                        rotation_id=rotation.rotation_id,
                        transition_id=f"{rotation.rotation_id}:generation-uncertain",
                        expected_status=GatewayKeyRotationStatus.GENERATION_PREPARED,
                        expected_version=rotation.version,
                        target_status=GatewayKeyRotationStatus.BLOCKED,
                        advanced_by=command.submitted_by,
                        advanced_at=command.submitted_at,
                        actor_scopes=command.actor_scopes,
                        failure_code=command.result.failure_code,
                    )
                )
            except GatewayKeyRotationConflict as error:
                raise GatewayKeyRotationGenerationProgramConflict(str(error)) from error
            return GatewayKeyRotationGenerationProgramResult(
                rotation=blocked,
                outcome=GatewayKeyGenerationOutcome.UNCERTAIN,
            )
        evidence = command.result.evidence
        if evidence is None:
            raise GatewayKeyRotationGenerationProgramConflict(
                "generated result is missing evidence"
            )
        try:
            admitted = self._generation.admit_generated(
                AdmitGeneratedDelegationSigningKey(
                    grant=command.action.grant,
                    evidence=evidence,
                    admitted_by=command.submitted_by,
                    admitted_at=command.submitted_at,
                    actor_scopes=command.actor_scopes,
                )
            )
            completed = self._rotations.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=rotation.rotation_id,
                    transition_id=f"{rotation.rotation_id}:generation-succeeded",
                    expected_status=GatewayKeyRotationStatus.GENERATION_PREPARED,
                    expected_version=rotation.version,
                    target_status=GatewayKeyRotationStatus.KEY_GENERATED,
                    advanced_by=command.submitted_by,
                    advanced_at=command.submitted_at,
                    actor_scopes=command.actor_scopes,
                    new_key_id=admitted.signing_key.key_id,
                    new_secret_version_id=admitted.provider_version_id,
                    new_secret_version_number=admitted.provider_version_number,
                )
            )
        except (DelegationKeyGenerationConflict, GatewayKeyRotationConflict) as error:
            raise GatewayKeyRotationGenerationProgramConflict(str(error)) from error
        return GatewayKeyRotationGenerationProgramResult(
            rotation=completed,
            outcome=GatewayKeyGenerationOutcome.GENERATED,
            admitted=admitted,
        )

    def _rotation(self, rotation_id: str) -> GatewayKeyRotation:
        try:
            return self._rotations.get(rotation_id)
        except GatewayKeyRotationNotFound as error:
            raise GatewayKeyRotationGenerationProgramConflict(
                "gateway key rotation was not found"
            ) from error

    def _active_provider_registration(self, rotation: GatewayKeyRotation) -> str:
        with self._unit_of_work_factory() as unit_of_work:
            try:
                provider = unit_of_work.stores.secret_providers.get_active(
                    rotation.workspace_id,
                    rotation.new_secret_reference.provider_id,
                )
            except SecretProviderNotFound as error:
                raise GatewayKeyRotationGenerationProgramConflict(
                    "generation provider is not actively admitted"
                ) from error
            unit_of_work.commit()
            return provider.registration_id

    def _prepare_grant(
        self,
        rotation: GatewayKeyRotation,
        provider_registration_id: str,
        command: PrepareGatewayKeyRotationGeneration,
        *,
        requested_at: str | None = None,
    ) -> DelegationKeyGenerationGrant:
        try:
            return self._generation.prepare(
                GenerateDelegationSigningKey(
                    workspace_id=rotation.workspace_id,
                    provider_registration_id=provider_registration_id,
                    reference=rotation.new_secret_reference,
                    purpose=rotation.purpose,
                    issuer=rotation.issuer,
                    actor_subject=command.actor_subject,
                    correlation_id=rotation.key_generation_correlation,
                    requested_at=requested_at or command.prepared_at,
                    actor_scopes=command.actor_scopes,
                )
            )
        except DelegationKeyGenerationConflict as error:
            raise GatewayKeyRotationGenerationProgramConflict(str(error)) from error

    @staticmethod
    def _action(
        before: GatewayKeyRotation,
        prepared: GatewayKeyRotation,
        grant: DelegationKeyGenerationGrant,
        transition_id: str,
    ) -> GatewayKeyRotationGenerationAction:
        return GatewayKeyRotationGenerationAction(
            rotation_id=prepared.rotation_id,
            prepared_transition_id=transition_id,
            expected_rotation_version=before.version,
            prepared_rotation_version=prepared.version,
            provider_registration_id=grant.provider_registration_id,
            action_digest=grant.custody_grant.custody_fingerprint,
            grant=grant,
        )

    def _prepared_transition(self, rotation: GatewayKeyRotation):
        transition_id = f"{rotation.rotation_id}:prepare-generation"
        transitions = tuple(
            transition
            for transition in self._rotations.transitions(rotation.rotation_id)
            if transition.transition_id == transition_id
        )
        if (
            len(transitions) != 1
            or transitions[0].from_status is not GatewayKeyRotationStatus.APPROVED
            or transitions[0].to_status
            is not GatewayKeyRotationStatus.GENERATION_PREPARED
            or transitions[0].to_version != rotation.version
        ):
            raise GatewayKeyRotationGenerationProgramConflict(
                "prepared generation transition evidence is missing"
            )
        return transitions[0]

    @staticmethod
    def _match_persisted_action(
        rotation: GatewayKeyRotation,
        action: GatewayKeyRotationGenerationAction,
    ) -> None:
        expected_version = action.prepared_rotation_version
        if rotation.status in {
            GatewayKeyRotationStatus.KEY_GENERATED,
            GatewayKeyRotationStatus.BLOCKED,
        }:
            expected_version += 1
        if (
            rotation.status not in {
                GatewayKeyRotationStatus.GENERATION_PREPARED,
                GatewayKeyRotationStatus.KEY_GENERATED,
                GatewayKeyRotationStatus.BLOCKED,
            }
            or rotation.version != expected_version
            or rotation.generation_provider_registration_id
            != action.provider_registration_id
            or rotation.generation_action_digest != action.action_digest
            or action.prepared_transition_id
            != f"{rotation.rotation_id}:prepare-generation"
            or action.grant.workspace_id != rotation.workspace_id
            or action.grant.reference != rotation.new_secret_reference
            or action.grant.purpose is not rotation.purpose
            or action.grant.issuer != rotation.issuer
            or action.grant.correlation_id
            != rotation.key_generation_correlation
        ):
            raise GatewayKeyRotationGenerationProgramConflict(
                "generation action does not match durable rotation state"
            )

    @staticmethod
    def _generated_replay(
        rotation: GatewayKeyRotation,
        command: SubmitGatewayKeyRotationGeneration,
    ) -> GatewayKeyRotationGenerationProgramResult:
        evidence = command.result.evidence
        if (
            command.result.outcome is not GatewayKeyGenerationOutcome.GENERATED
            or evidence is None
            or evidence.workspace_id != rotation.workspace_id
            or evidence.reference != rotation.new_secret_reference
            or evidence.purpose is not rotation.purpose
            or evidence.issuer != rotation.issuer
            or evidence.correlation_id != rotation.key_generation_correlation
            or rotation.new_key_id != evidence.public_key.key_id
            or rotation.new_secret_version_id != evidence.version_id
            or rotation.new_secret_version_number != evidence.version_number
            or rotation.generation_action_digest != command.action.action_digest
        ):
            raise GatewayKeyRotationGenerationProgramConflict(
                "generated result conflicts with durable rotation evidence"
            )
        return GatewayKeyRotationGenerationProgramResult(
            rotation=rotation,
            outcome=GatewayKeyGenerationOutcome.GENERATED,
            replayed=True,
        )


def _scopes(scopes: tuple[PolicyScope, ...], *required: PolicyScope) -> None:
    if (
        not isinstance(scopes, tuple)
        or not scopes
        or any(not isinstance(scope, PolicyScope) for scope in scopes)
    ):
        raise GatewayKeyRotationGenerationProgramAuthorizationDenied(
            "gateway key generation scopes are malformed"
        )
    missing = tuple(scope.value for scope in required if scope not in scopes)
    if missing:
        raise GatewayKeyRotationGenerationProgramAuthorizationDenied(
            f"gateway key generation requires {', '.join(missing)}"
        )


def _identifier(value: object, name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise GatewayKeyRotationGenerationProgramError(f"{name} is malformed")


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise GatewayKeyRotationGenerationProgramError(f"{name} is malformed")


__all__ = [
    "GatewayKeyGenerationOutcome",
    "GatewayKeyGenerationResult",
    "GatewayKeyRotationGenerationAction",
    "GatewayKeyRotationGenerationProgram",
    "GatewayKeyRotationGenerationProgramAuthorizationDenied",
    "GatewayKeyRotationGenerationProgramConflict",
    "GatewayKeyRotationGenerationProgramError",
    "GatewayKeyRotationGenerationProgramResult",
    "PrepareGatewayKeyRotationGeneration",
    "SubmitGatewayKeyRotationGeneration",
]
