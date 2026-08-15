"""Durable gateway delegation-key rotation state and transition laws."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Any, Callable

from control_plane_kit_core.operations import RunId
from control_plane_kit_core.approval_subjects import (
    GatewayKeyRotationApprovalSubject,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference

from control_plane_kit_operations._temporal import (
    validate_canonical_utc_timestamp,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.records import OperationsRecordError


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_TERMINAL = frozenset()


class GatewayKeyRotationError(ValueError):
    pass


class GatewayKeyRotationConflict(GatewayKeyRotationError):
    pass


class GatewayKeyRotationNotFound(GatewayKeyRotationError):
    pass


class GatewayKeyRotationAuthorizationDenied(GatewayKeyRotationError):
    pass


class GatewayKeyRotationStatus(StrEnum):
    REQUESTED = "requested"
    AWAITING_APPROVAL = "awaiting-approval"
    APPROVED = "approved"
    GENERATION_PREPARED = "generation-prepared"
    KEY_GENERATED = "key-generated"
    OVERLAP_DEPLOYING = "overlap-deploying"
    OVERLAP_READY = "overlap-ready"
    NEW_KEY_ACTIVE = "new-key-active"
    DRAINING_OLD_GRANTS = "draining-old-grants"
    RETIREMENT_DEPLOYING = "retirement-deploying"
    RETIREMENT_READY = "retirement-ready"
    OLD_KEY_RETIRED = "old-key-retired"
    REVOCATION_PREPARED = "revocation-prepared"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    REJECTED = "rejected"


_TERMINAL = frozenset({
    GatewayKeyRotationStatus.COMPLETED,
    GatewayKeyRotationStatus.BLOCKED,
    GatewayKeyRotationStatus.REJECTED,
})


class GatewayKeyRotationDeploymentPhase(StrEnum):
    OVERLAP = "overlap"
    RETIREMENT = "retirement"


class GatewayKeyRotationDeploymentStatus(StrEnum):
    PREPARED = "prepared"
    ACCEPTED = "accepted"


@dataclass(frozen=True)
class GatewayKeyRotationRevocationCheckpoint:
    """Secret-free identity for one prepared exact-version revocation."""

    provider_registration_id: str
    secret_reference: SecretReference
    provider_version_id: str
    provider_version_number: int
    revocation_id: str
    correlation_id: str
    action_digest: str
    prepared_at: str

    def __post_init__(self) -> None:
        for name in (
            "provider_registration_id",
            "provider_version_id",
            "revocation_id",
            "correlation_id",
        ):
            _identifier(getattr(self, name), name)
        if not isinstance(self.secret_reference, SecretReference):
            raise GatewayKeyRotationError(
                "revocation checkpoint secret reference is malformed"
            )
        if (
            type(self.provider_version_number) is not int
            or self.provider_version_number < 1
        ):
            raise GatewayKeyRotationError(
                "revocation checkpoint version number is malformed"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", self.action_digest):
            raise GatewayKeyRotationError(
                "revocation checkpoint action digest is malformed"
            )
        _text(self.prepared_at, "prepared_at")


@dataclass(frozen=True)
class GatewayKeyRotationDeploymentCheckpoint:
    phase: GatewayKeyRotationDeploymentPhase
    status: GatewayKeyRotationDeploymentStatus
    session_id: str
    plan_id: str
    approval_request_id: str
    approval_decision_id: str
    execution_request_id: str
    run_id: str
    base_authored_graph_id: str
    base_realized_projection_id: str
    desired_authored_graph_id: str
    desired_realized_projection_id: str
    desired_revision: int
    prepared_at: str
    accepted_current_graph_id: str | None = None
    accepted_current_projection_id: str | None = None
    accepted_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.phase, GatewayKeyRotationDeploymentPhase):
            raise GatewayKeyRotationError("deployment phase is unsupported")
        if not isinstance(self.status, GatewayKeyRotationDeploymentStatus):
            raise GatewayKeyRotationError("deployment status is unsupported")
        for name in (
            "session_id", "plan_id", "approval_request_id",
            "approval_decision_id", "execution_request_id",
            "base_authored_graph_id", "base_realized_projection_id",
            "desired_authored_graph_id", "desired_realized_projection_id",
        ):
            _identifier(getattr(self, name), name)
        _run_id(self.run_id)
        if type(self.desired_revision) is not int or self.desired_revision < 0:
            raise GatewayKeyRotationError("desired revision is malformed")
        _text(self.prepared_at, "prepared_at")
        accepted = self.status is GatewayKeyRotationDeploymentStatus.ACCEPTED
        values = (
            self.accepted_current_graph_id,
            self.accepted_current_projection_id,
            self.accepted_at,
        )
        if accepted != all(value is not None for value in values):
            raise GatewayKeyRotationError("accepted deployment evidence is incomplete")
        if not accepted and any(value is not None for value in values):
            raise GatewayKeyRotationError("prepared deployment cannot claim acceptance")
        accepted_identity_names = (
            "accepted_current_graph_id",
            "accepted_current_projection_id",
        )
        for name, value in zip(accepted_identity_names, values[:2]):
            if value is not None:
                _identifier(value, name)
        if self.accepted_at is not None:
            _text(self.accepted_at, "accepted_at")


@dataclass(frozen=True)
class GatewayKeyRotationDeploymentHandoff:
    """Authenticated restart snapshot; the request claim remains authority."""

    rotation_id: str
    checkpoint: GatewayKeyRotationDeploymentCheckpoint
    fence: ExecutionLeaseFence = field(repr=False)

    def __post_init__(self) -> None:
        _identifier(self.rotation_id, "rotation_id")
        if type(self.checkpoint) is not GatewayKeyRotationDeploymentCheckpoint:
            raise GatewayKeyRotationConflict("deployment checkpoint is malformed")
        if type(self.fence) is not ExecutionLeaseFence:
            raise GatewayKeyRotationConflict("deployment fence is malformed")


@dataclass(frozen=True)
class ReadGatewayKeyRotationDeploymentHandoff:
    rotation_id: str
    phase: GatewayKeyRotationDeploymentPhase
    worker_authority: ExecutionWorkerAuthority = field(repr=False)

    def __post_init__(self) -> None:
        _identifier(self.rotation_id, "rotation_id")
        if type(self.phase) is not GatewayKeyRotationDeploymentPhase:
            raise GatewayKeyRotationError("deployment phase is unsupported")
        if type(self.worker_authority) is not ExecutionWorkerAuthority:
            raise GatewayKeyRotationAuthorizationDenied(
                "gateway deployment worker authority is malformed"
            )


@dataclass(frozen=True)
class GatewayKeyRotation:
    rotation_id: str
    workspace_id: str
    gateway_node_id: str
    purpose: DelegationKeyPurpose
    issuer: str
    old_key_id: str
    new_secret_reference: SecretReference
    key_generation_correlation: str
    maximum_grant_lifetime_seconds: int
    clock_skew_seconds: int
    correlation_id: str
    requested_by: str
    requested_at: str
    intent_fingerprint: str
    status: GatewayKeyRotationStatus = GatewayKeyRotationStatus.REQUESTED
    version: int = 1
    approval_request_id: str | None = None
    approval_decision_id: str | None = None
    generation_provider_registration_id: str | None = None
    generation_action_digest: str | None = None
    new_key_id: str | None = None
    new_secret_version_id: str | None = None
    new_secret_version_number: int | None = None
    overlap_deployment: GatewayKeyRotationDeploymentCheckpoint | None = None
    new_key_activated_at: str | None = None
    drain_deadline_epoch: int | None = None
    retirement_deployment: GatewayKeyRotationDeploymentCheckpoint | None = None
    revocation: GatewayKeyRotationRevocationCheckpoint | None = None
    old_key_retired_at: str | None = None
    old_secret_revoked_at: str | None = None
    failure_code: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        for name in ("rotation_id", "workspace_id", "gateway_node_id", "issuer",
                     "old_key_id", "key_generation_correlation", "correlation_id",
                     "requested_by"):
            _identifier(getattr(self, name), name)
        if not isinstance(self.purpose, DelegationKeyPurpose):
            raise GatewayKeyRotationError("rotation purpose is unsupported")
        if not isinstance(self.new_secret_reference, SecretReference):
            raise GatewayKeyRotationError("new secret reference is malformed")
        if not 1 <= self.maximum_grant_lifetime_seconds <= 300:
            raise GatewayKeyRotationError("maximum grant lifetime is out of bounds")
        if not 0 <= self.clock_skew_seconds <= 60:
            raise GatewayKeyRotationError("clock skew is out of bounds")
        _text(self.requested_at, "requested_at")
        if not re.fullmatch(r"[0-9a-f]{64}", self.intent_fingerprint):
            raise GatewayKeyRotationError("intent fingerprint is malformed")
        if not isinstance(self.status, GatewayKeyRotationStatus):
            raise GatewayKeyRotationError("rotation status is unsupported")
        if type(self.version) is not int or self.version < 1:
            raise GatewayKeyRotationError("rotation version is malformed")
        for name in (
            "approval_request_id",
            "approval_decision_id",
            "generation_provider_registration_id",
            "new_key_id",
            "new_secret_version_id",
            "failure_code",
            "updated_by",
        ):
            value = getattr(self, name)
            if value is not None:
                _identifier(value, name)
        generation_evidence = (
            self.generation_provider_registration_id,
            self.generation_action_digest,
        )
        if any(value is not None for value in generation_evidence) != all(
            value is not None for value in generation_evidence
        ):
            raise GatewayKeyRotationError(
                "generation checkpoint evidence is incomplete"
            )
        if (
            self.generation_action_digest is not None
            and not re.fullmatch(r"[0-9a-f]{64}", self.generation_action_digest)
        ):
            raise GatewayKeyRotationError("generation action digest is malformed")
        if self.new_secret_version_number is not None and (
            type(self.new_secret_version_number) is not int
            or self.new_secret_version_number < 1
        ):
            raise GatewayKeyRotationError("secret version number is malformed")
        key_evidence = (
            self.new_key_id,
            self.new_secret_version_id,
            self.new_secret_version_number,
        )
        if any(value is not None for value in key_evidence) != all(
                value is not None for value in key_evidence):
            raise GatewayKeyRotationError("generated key evidence is incomplete")
        if (self.new_key_activated_at is None) != (self.drain_deadline_epoch is None):
            raise GatewayKeyRotationError("key activation evidence is incomplete")
        if self.old_secret_revoked_at is not None and self.old_key_retired_at is None:
            raise GatewayKeyRotationError(
                "secret revocation requires prior public-key retirement"
            )
        if self.overlap_deployment is not None and (
                not isinstance(self.overlap_deployment,
                               GatewayKeyRotationDeploymentCheckpoint)
                or self.overlap_deployment.phase
                    is not GatewayKeyRotationDeploymentPhase.OVERLAP):
            raise GatewayKeyRotationError("overlap deployment evidence is malformed")
        if self.retirement_deployment is not None and (
                not isinstance(self.retirement_deployment,
                               GatewayKeyRotationDeploymentCheckpoint)
                or self.retirement_deployment.phase
                    is not GatewayKeyRotationDeploymentPhase.RETIREMENT):
            raise GatewayKeyRotationError("retirement deployment evidence is malformed")
        if self.revocation is not None and not isinstance(
            self.revocation,
            GatewayKeyRotationRevocationCheckpoint,
        ):
            raise GatewayKeyRotationError("revocation checkpoint is malformed")
        if self.revocation is not None and self.old_key_retired_at is None:
            raise GatewayKeyRotationError(
                "revocation checkpoint requires prior public-key retirement"
            )
        if self.old_secret_revoked_at is not None and self.revocation is None:
            raise GatewayKeyRotationError(
                "secret revocation requires prepared exact-version evidence"
            )
        if self.status is GatewayKeyRotationStatus.COMPLETED and (
            self.revocation is None or self.old_secret_revoked_at is None
        ):
            raise GatewayKeyRotationError(
                "completed rotation requires exact revocation evidence"
            )
        for name in ("new_key_activated_at", "old_key_retired_at",
                     "old_secret_revoked_at", "updated_at"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)
        if self.drain_deadline_epoch is not None and (
            type(self.drain_deadline_epoch) is not int
            or self.drain_deadline_epoch < 0
        ):
            raise GatewayKeyRotationError("drain deadline is malformed")

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL

    def same_intent_as(self, other: "GatewayKeyRotation") -> bool:
        return self.intent_fingerprint == other.intent_fingerprint


@dataclass(frozen=True)
class GatewayKeyRotationReadModel:
    rotation_id: str
    workspace_id: str
    gateway_node_id: str
    purpose: DelegationKeyPurpose
    issuer: str
    old_key_id: str
    new_key_id: str | None
    status: GatewayKeyRotationStatus
    version: int
    correlation_id: str
    requested_by: str
    requested_at: str
    drain_deadline_epoch: int | None
    failure_code: str | None
    updated_at: str | None


@dataclass(frozen=True)
class GatewayKeyRotationTransition:
    rotation_id: str
    transition_id: str
    from_status: GatewayKeyRotationStatus
    to_status: GatewayKeyRotationStatus
    from_version: int
    to_version: int
    transition_fingerprint: str
    advanced_by: str
    advanced_at: str
    failure_code: str | None = None

    def __post_init__(self) -> None:
        for name in ("rotation_id", "transition_id", "advanced_by"):
            _identifier(getattr(self, name), name)
        if not isinstance(self.from_status, GatewayKeyRotationStatus):
            raise GatewayKeyRotationError("transition source status is unsupported")
        if not isinstance(self.to_status, GatewayKeyRotationStatus):
            raise GatewayKeyRotationError("transition target status is unsupported")
        if type(self.from_version) is not int or self.from_version < 1:
            raise GatewayKeyRotationError("transition source version is malformed")
        if self.to_version != self.from_version + 1:
            raise GatewayKeyRotationError("transition target version is malformed")
        if not re.fullmatch(r"[0-9a-f]{64}", self.transition_fingerprint):
            raise GatewayKeyRotationError("transition fingerprint is malformed")
        _text(self.advanced_at, "advanced_at")
        if self.failure_code is not None:
            _identifier(self.failure_code, "failure_code")


@dataclass(frozen=True)
class RequestGatewayKeyRotation:
    workspace_id: str
    gateway_node_id: str
    purpose: DelegationKeyPurpose
    issuer: str
    old_key_id: str
    new_secret_reference: SecretReference
    key_generation_correlation: str
    maximum_grant_lifetime_seconds: int
    clock_skew_seconds: int
    correlation_id: str
    requested_by: str
    requested_at: str
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        for name in ("workspace_id", "gateway_node_id", "issuer", "old_key_id",
                     "key_generation_correlation", "correlation_id", "requested_by"):
            _identifier(getattr(self, name), name)
        if not isinstance(self.purpose, DelegationKeyPurpose):
            raise GatewayKeyRotationError("rotation purpose is unsupported")
        if not isinstance(self.new_secret_reference, SecretReference):
            raise GatewayKeyRotationError("new secret reference is malformed")
        if not 1 <= self.maximum_grant_lifetime_seconds <= 300:
            raise GatewayKeyRotationError("maximum grant lifetime is out of bounds")
        if not 0 <= self.clock_skew_seconds <= 60:
            raise GatewayKeyRotationError("clock skew is out of bounds")
        _text(self.requested_at, "requested_at")
        if (not isinstance(self.actor_scopes, tuple)
                or any(not isinstance(scope, PolicyScope) for scope in self.actor_scopes)):
            raise GatewayKeyRotationError("rotation scopes are malformed")


@dataclass(frozen=True)
class AdvanceGatewayKeyRotation:
    rotation_id: str
    transition_id: str
    expected_status: GatewayKeyRotationStatus
    expected_version: int
    target_status: GatewayKeyRotationStatus
    advanced_by: str
    advanced_at: str
    actor_scopes: tuple[PolicyScope, ...]
    approval_request_id: str | None = None
    approval_decision_id: str | None = None
    generation_provider_registration_id: str | None = None
    generation_action_digest: str | None = None
    new_key_id: str | None = None
    new_secret_version_id: str | None = None
    new_secret_version_number: int | None = None
    deployment: GatewayKeyRotationDeploymentCheckpoint | None = None
    new_key_activated_at: str | None = None
    revocation: GatewayKeyRotationRevocationCheckpoint | None = None
    old_key_retired_at: str | None = None
    old_secret_revoked_at: str | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        for name in ("rotation_id", "transition_id", "advanced_by"):
            _identifier(getattr(self, name), name)
        if not isinstance(self.expected_status, GatewayKeyRotationStatus):
            raise GatewayKeyRotationError("expected rotation status is unsupported")
        if not isinstance(self.target_status, GatewayKeyRotationStatus):
            raise GatewayKeyRotationError("target rotation status is unsupported")
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise GatewayKeyRotationError("expected rotation version is malformed")
        if self.generation_provider_registration_id is not None:
            _identifier(
                self.generation_provider_registration_id,
                "generation_provider_registration_id",
            )
        if (
            self.generation_action_digest is not None
            and not re.fullmatch(r"[0-9a-f]{64}", self.generation_action_digest)
        ):
            raise GatewayKeyRotationError("generation action digest is malformed")
        _text(self.advanced_at, "advanced_at")


@dataclass(frozen=True)
class AdvanceGatewayKeyRotationDeployment:
    transition: AdvanceGatewayKeyRotation
    handoff: GatewayKeyRotationDeploymentHandoff

    def __post_init__(self) -> None:
        if type(self.transition) is not AdvanceGatewayKeyRotation:
            raise GatewayKeyRotationError("deployment transition is malformed")
        if type(self.handoff) is not GatewayKeyRotationDeploymentHandoff:
            raise GatewayKeyRotationError("deployment handoff is malformed")
        if self.transition.rotation_id != self.handoff.rotation_id:
            raise GatewayKeyRotationConflict("deployment rotation identity changed")
        _validate_deployment_transition_shape(self)


_LEGAL = {
    GatewayKeyRotationStatus.REQUESTED: {GatewayKeyRotationStatus.AWAITING_APPROVAL},
    GatewayKeyRotationStatus.AWAITING_APPROVAL: {
        GatewayKeyRotationStatus.APPROVED, GatewayKeyRotationStatus.REJECTED},
    GatewayKeyRotationStatus.APPROVED: {
        GatewayKeyRotationStatus.GENERATION_PREPARED,
        GatewayKeyRotationStatus.BLOCKED,
    },
    GatewayKeyRotationStatus.GENERATION_PREPARED: {
        GatewayKeyRotationStatus.KEY_GENERATED,
        GatewayKeyRotationStatus.BLOCKED,
    },
    GatewayKeyRotationStatus.KEY_GENERATED: {
        GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
        GatewayKeyRotationStatus.BLOCKED,
    },
    GatewayKeyRotationStatus.OVERLAP_DEPLOYING: {GatewayKeyRotationStatus.OVERLAP_READY,
                                                GatewayKeyRotationStatus.BLOCKED},
    GatewayKeyRotationStatus.OVERLAP_READY: {GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
                                            GatewayKeyRotationStatus.BLOCKED},
    GatewayKeyRotationStatus.NEW_KEY_ACTIVE: {GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
                                              GatewayKeyRotationStatus.BLOCKED},
    GatewayKeyRotationStatus.DRAINING_OLD_GRANTS: {
        GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
        GatewayKeyRotationStatus.BLOCKED},
    GatewayKeyRotationStatus.RETIREMENT_DEPLOYING: {
        GatewayKeyRotationStatus.RETIREMENT_READY,
        GatewayKeyRotationStatus.BLOCKED,
    },
    GatewayKeyRotationStatus.RETIREMENT_READY: {
        GatewayKeyRotationStatus.OLD_KEY_RETIRED,
        GatewayKeyRotationStatus.BLOCKED,
    },
    GatewayKeyRotationStatus.OLD_KEY_RETIRED: {
        GatewayKeyRotationStatus.REVOCATION_PREPARED,
        GatewayKeyRotationStatus.BLOCKED,
    },
    GatewayKeyRotationStatus.REVOCATION_PREPARED: {
        GatewayKeyRotationStatus.COMPLETED,
        GatewayKeyRotationStatus.BLOCKED,
    },
}


class GatewayKeyRotationService:
    def __init__(self, unit_of_work_factory: Any, *, clock: Callable[[], int]) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    def request(self, command: RequestGatewayKeyRotation) -> GatewayKeyRotation:
        if not isinstance(command, RequestGatewayKeyRotation):
            raise TypeError("command must be RequestGatewayKeyRotation")
        _scope(command.actor_scopes)
        validate_canonical_utc_timestamp(command.requested_at)
        candidate = _candidate(command)
        with self._unit_of_work_factory() as uow:
            store = uow.stores.gateway_key_rotations
            store.lock_binding(command.workspace_id, command.gateway_node_id,
                               command.purpose, command.issuer)
            existing = store.for_correlation(command.workspace_id,
                                             command.correlation_id)
            if existing is not None:
                if existing.same_intent_as(candidate):
                    uow.commit()
                    return existing
                raise GatewayKeyRotationConflict(
                    "rotation correlation was reused with different intent")
            active = store.nonterminal_for_binding(command.workspace_id,
                                                   command.gateway_node_id,
                                                   command.purpose, command.issuer)
            if active is not None:
                raise GatewayKeyRotationConflict(
                    "another nonterminal rotation owns this gateway authority")
            stored = store.add(candidate)
            uow.commit()
            return stored

    def get(self, rotation_id: str) -> GatewayKeyRotation:
        with self._unit_of_work_factory() as uow:
            value = uow.stores.gateway_key_rotations.get(rotation_id)
            uow.commit()
            return value

    def read(self, rotation_id: str) -> GatewayKeyRotationReadModel:
        value = self.get(rotation_id)
        return GatewayKeyRotationReadModel(
            rotation_id=value.rotation_id, workspace_id=value.workspace_id,
            gateway_node_id=value.gateway_node_id, purpose=value.purpose,
            issuer=value.issuer, old_key_id=value.old_key_id,
            new_key_id=value.new_key_id, status=value.status,
            version=value.version, correlation_id=value.correlation_id,
            requested_by=value.requested_by, requested_at=value.requested_at,
            drain_deadline_epoch=value.drain_deadline_epoch,
            failure_code=value.failure_code, updated_at=value.updated_at)

    def deployment_handoff(
        self,
        command: ReadGatewayKeyRotationDeploymentHandoff,
    ) -> GatewayKeyRotationDeploymentHandoff:
        if not isinstance(command, ReadGatewayKeyRotationDeploymentHandoff):
            raise TypeError(
                "command must be ReadGatewayKeyRotationDeploymentHandoff"
            )
        _worker_scope(command.worker_authority)
        with self._unit_of_work_factory() as uow:
            try:
                rotation = uow.stores.gateway_key_rotations.get(command.rotation_id)
                checkpoint = _deployment_checkpoint(rotation, command.phase)
                request = uow.stores.execution.get_request(
                    checkpoint.execution_request_id
                )
                run = uow.stores.execution.get_run(checkpoint.run_id)
            except (GatewayKeyRotationError, KeyError, OperationsRecordError):
                raise GatewayKeyRotationConflict(
                    "gateway deployment truth is unavailable"
                ) from None
            _validate_deployment_linkage(rotation, checkpoint, request, run)
            claim = request.claim
            if (
                claim is None
                or claim.worker_id != command.worker_authority.worker_id
            ):
                raise GatewayKeyRotationAuthorizationDenied(
                    "gateway deployment worker is not current"
                ) from None
            handoff = GatewayKeyRotationDeploymentHandoff(
                rotation.rotation_id,
                checkpoint,
                claim.fence,
            )
            uow.commit()
            return handoff

    def advance(self, command: AdvanceGatewayKeyRotation) -> GatewayKeyRotation:
        if not isinstance(command, AdvanceGatewayKeyRotation):
            raise TypeError("command must be AdvanceGatewayKeyRotation")
        _scope(command.actor_scopes)
        _validate_advance_timestamps(command)
        fingerprint = _transition_fingerprint(command)
        with self._unit_of_work_factory() as uow:
            store = uow.stores.gateway_key_rotations
            current = store.get_for_update(command.rotation_id)
            existing = store.transition_for_id(
                command.rotation_id, command.transition_id)
            if existing is not None:
                if existing.transition_fingerprint != fingerprint:
                    raise GatewayKeyRotationConflict(
                        "rotation transition id was reused with different intent")
                uow.commit()
                return current
            if (current.status is not command.expected_status
                    or current.version != command.expected_version):
                raise GatewayKeyRotationConflict("rotation expected state is stale")
            _validate_approval_evidence(uow, current, command)
            now = self._clock()
            if type(now) is not int or now < 0:
                raise GatewayKeyRotationError("trusted clock returned malformed time")
            replacement = _transition(current, command, now)
            updated = store.compare_and_set(current, replacement)
            if updated is None:
                raise GatewayKeyRotationConflict("rotation advanced concurrently")
            store.add_transition(GatewayKeyRotationTransition(
                rotation_id=current.rotation_id,
                transition_id=command.transition_id,
                from_status=current.status,
                to_status=updated.status,
                from_version=current.version,
                to_version=updated.version,
                transition_fingerprint=fingerprint,
                advanced_by=command.advanced_by,
                advanced_at=command.advanced_at,
                failure_code=command.failure_code,
            ))
            uow.commit()
            return updated

    def advance_deployment(
        self,
        command: AdvanceGatewayKeyRotationDeployment,
    ) -> GatewayKeyRotation:
        if not isinstance(command, AdvanceGatewayKeyRotationDeployment):
            raise TypeError("command must be AdvanceGatewayKeyRotationDeployment")
        transition = command.transition
        handoff = command.handoff
        _scope(transition.actor_scopes)
        _validate_advance_timestamps(transition)
        fingerprint = _transition_fingerprint(transition)
        checkpoint = handoff.checkpoint
        with self._unit_of_work_factory() as uow:
            stores = uow.stores
            try:
                request = stores.execution.get_request_for_update(
                    checkpoint.execution_request_id
                )
                run = stores.execution.get_run_for_update(checkpoint.run_id)
                current = stores.gateway_key_rotations.get_for_update(
                    transition.rotation_id
                )
            except (GatewayKeyRotationError, KeyError, OperationsRecordError):
                raise GatewayKeyRotationConflict(
                    "gateway deployment truth is unavailable"
                ) from None
            _validate_deployment_linkage(current, checkpoint, request, run)
            claim = request.claim
            if claim is None or claim.fence != handoff.fence:
                raise GatewayKeyRotationAuthorizationDenied(
                    "gateway deployment execution authority is stale"
                ) from None
            _validate_deployment_current(command, current)
            store = stores.gateway_key_rotations
            existing = store.transition_for_id(
                transition.rotation_id,
                transition.transition_id,
            )
            if existing is not None:
                if existing.transition_fingerprint != fingerprint:
                    raise GatewayKeyRotationConflict(
                        "rotation transition id was reused with different intent"
                    )
                uow.commit()
                return current
            if (
                current.status is not transition.expected_status
                or current.version != transition.expected_version
            ):
                raise GatewayKeyRotationConflict("rotation expected state is stale")
            _validate_approval_evidence(uow, current, transition)
            now = self._clock()
            if type(now) is not int or now < 0:
                raise GatewayKeyRotationError("trusted clock returned malformed time")
            replacement = _transition(current, transition, now)
            updated = store.compare_and_set(current, replacement)
            if updated is None:
                raise GatewayKeyRotationConflict("rotation advanced concurrently")
            store.add_transition(GatewayKeyRotationTransition(
                rotation_id=current.rotation_id,
                transition_id=transition.transition_id,
                from_status=current.status,
                to_status=updated.status,
                from_version=current.version,
                to_version=updated.version,
                transition_fingerprint=fingerprint,
                advanced_by=transition.advanced_by,
                advanced_at=transition.advanced_at,
                failure_code=transition.failure_code,
            ))
            uow.commit()
            return updated

    def transitions(self, rotation_id: str) -> tuple[GatewayKeyRotationTransition, ...]:
        with self._unit_of_work_factory() as uow:
            values = uow.stores.gateway_key_rotations.transitions(rotation_id)
            uow.commit()
            return values


def _validate_deployment_transition_shape(
    command: AdvanceGatewayKeyRotationDeployment,
) -> None:
    transition = command.transition
    checkpoint = command.handoff.checkpoint
    phase = checkpoint.phase
    if phase is GatewayKeyRotationDeploymentPhase.OVERLAP:
        deploying = GatewayKeyRotationStatus.OVERLAP_DEPLOYING
        ready = GatewayKeyRotationStatus.OVERLAP_READY
        preparation_source = GatewayKeyRotationStatus.KEY_GENERATED
    else:
        deploying = GatewayKeyRotationStatus.RETIREMENT_DEPLOYING
        ready = GatewayKeyRotationStatus.RETIREMENT_READY
        preparation_source = GatewayKeyRotationStatus.DRAINING_OLD_GRANTS
    shape = (transition.expected_status, transition.target_status)
    if shape == (preparation_source, deploying):
        expected_checkpoint_status = GatewayKeyRotationDeploymentStatus.PREPARED
        if transition.deployment != checkpoint:
            raise GatewayKeyRotationConflict(
                "prepared deployment transition changed checkpoint"
            )
    elif shape == (deploying, ready):
        expected_checkpoint_status = GatewayKeyRotationDeploymentStatus.ACCEPTED
        if transition.deployment != checkpoint:
            raise GatewayKeyRotationConflict(
                "accepted deployment transition changed checkpoint"
            )
    elif shape == (deploying, GatewayKeyRotationStatus.BLOCKED):
        expected_checkpoint_status = GatewayKeyRotationDeploymentStatus.PREPARED
        if transition.deployment is not None:
            raise GatewayKeyRotationConflict(
                "blocked deployment transition cannot replace checkpoint"
            )
    else:
        raise GatewayKeyRotationConflict(
            "gateway rotation transition is not deployment-owned"
        )
    if checkpoint.status is not expected_checkpoint_status:
        raise GatewayKeyRotationConflict("deployment checkpoint status is stale")


def _validate_deployment_current(
    command: AdvanceGatewayKeyRotationDeployment,
    current: GatewayKeyRotation,
) -> None:
    handoff = command.handoff
    checkpoint = handoff.checkpoint
    if current.rotation_id != handoff.rotation_id:
        raise GatewayKeyRotationConflict("deployment rotation identity changed")
    stored = _optional_deployment_checkpoint(current, checkpoint.phase)
    if stored is None:
        if command.transition.target_status not in {
            GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
            GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
        }:
            raise GatewayKeyRotationConflict("deployment checkpoint is missing")
        return
    if not _same_deployment_identity(stored, checkpoint):
        raise GatewayKeyRotationConflict("deployment checkpoint identity changed")


def _validate_deployment_linkage(
    rotation: GatewayKeyRotation,
    checkpoint: GatewayKeyRotationDeploymentCheckpoint,
    request: Any,
    run: Any,
) -> None:
    if (
        request.identity.request_id != checkpoint.execution_request_id
        or request.identity.workspace_id != rotation.workspace_id
        or request.identity.session_id != checkpoint.session_id
        or request.identity.plan_id != checkpoint.plan_id
        or request.approval_request_id != checkpoint.approval_request_id
        or request.approval_decision_id != checkpoint.approval_decision_id
        or run.run_id != checkpoint.run_id
        or run.plan_id != checkpoint.plan_id
        or run.admission.request_id != checkpoint.execution_request_id
    ):
        raise GatewayKeyRotationConflict(
            "gateway deployment execution linkage changed"
        )


def _deployment_checkpoint(
    rotation: GatewayKeyRotation,
    phase: GatewayKeyRotationDeploymentPhase,
) -> GatewayKeyRotationDeploymentCheckpoint:
    checkpoint = _optional_deployment_checkpoint(rotation, phase)
    if checkpoint is None:
        raise GatewayKeyRotationConflict("gateway deployment checkpoint is missing")
    return checkpoint


def _optional_deployment_checkpoint(
    rotation: GatewayKeyRotation,
    phase: GatewayKeyRotationDeploymentPhase,
) -> GatewayKeyRotationDeploymentCheckpoint | None:
    return (
        rotation.overlap_deployment
        if phase is GatewayKeyRotationDeploymentPhase.OVERLAP
        else rotation.retirement_deployment
    )


def _worker_scope(authority: ExecutionWorkerAuthority) -> None:
    if PolicyScope.EXECUTION_OPERATE not in authority.scopes:
        raise GatewayKeyRotationAuthorizationDenied(
            "gateway deployment worker requires execution:operate"
        )


def _validate_advance_timestamps(command: AdvanceGatewayKeyRotation) -> None:
    validate_canonical_utc_timestamp(command.advanced_at)
    for value in (
        command.new_key_activated_at,
        command.old_key_retired_at,
        command.old_secret_revoked_at,
    ):
        if value is not None:
            validate_canonical_utc_timestamp(value)
    if command.deployment is not None:
        validate_canonical_utc_timestamp(command.deployment.prepared_at)
        if command.deployment.accepted_at is not None:
            validate_canonical_utc_timestamp(command.deployment.accepted_at)
    if command.revocation is not None:
        validate_canonical_utc_timestamp(command.revocation.prepared_at)


def _candidate(command: RequestGatewayKeyRotation) -> GatewayKeyRotation:
    semantics = {
        "workspace_id": command.workspace_id, "gateway_node_id": command.gateway_node_id,
        "purpose": command.purpose.value, "issuer": command.issuer,
        "old_key_id": command.old_key_id,
        "new_secret_reference": command.new_secret_reference.reference_id,
        "key_generation_correlation": command.key_generation_correlation,
        "maximum_grant_lifetime_seconds": command.maximum_grant_lifetime_seconds,
        "clock_skew_seconds": command.clock_skew_seconds,
        "correlation_id": command.correlation_id,
    }
    fingerprint = _digest(semantics)
    return GatewayKeyRotation(
        rotation_id=f"gkrot_{fingerprint}", workspace_id=command.workspace_id,
        gateway_node_id=command.gateway_node_id, purpose=command.purpose,
        issuer=command.issuer, old_key_id=command.old_key_id,
        new_secret_reference=command.new_secret_reference,
        key_generation_correlation=command.key_generation_correlation,
        maximum_grant_lifetime_seconds=command.maximum_grant_lifetime_seconds,
        clock_skew_seconds=command.clock_skew_seconds,
        correlation_id=command.correlation_id, requested_by=command.requested_by,
        requested_at=command.requested_at, intent_fingerprint=fingerprint)


def gateway_key_rotation_approval_subject(
    rotation: GatewayKeyRotation,
) -> GatewayKeyRotationApprovalSubject:
    """Project one rotation into its immutable secret-free review meaning."""

    if not isinstance(rotation, GatewayKeyRotation):
        raise TypeError("rotation approval subject requires GatewayKeyRotation")
    return GatewayKeyRotationApprovalSubject(
        rotation_id=rotation.rotation_id,
        workspace_id=rotation.workspace_id,
        gateway_node_id=rotation.gateway_node_id,
        purpose=rotation.purpose,
        issuer=rotation.issuer,
        old_key_id=rotation.old_key_id,
        maximum_grant_lifetime_seconds=rotation.maximum_grant_lifetime_seconds,
        clock_skew_seconds=rotation.clock_skew_seconds,
        rotation_intent_digest=rotation.intent_fingerprint,
    )


def _validate_approval_evidence(uow, current, command) -> None:
    target = command.target_status
    if target is GatewayKeyRotationStatus.AWAITING_APPROVAL:
        request_id = command.approval_request_id
        if request_id is None:
            return
        try:
            request = uow.stores.activity_history.get_approval_request(request_id)
        except KeyError as error:
            raise GatewayKeyRotationConflict(
                "rotation approval request was not found"
            ) from error
        if request.subject != gateway_key_rotation_approval_subject(current):
            raise GatewayKeyRotationConflict(
                "rotation approval request reviews different intent"
            )
        if request.required_scope is not PolicyScope.DELEGATION_KEY_ROTATE_APPROVE:
            raise GatewayKeyRotationConflict(
                "rotation approval request has insufficient review authority"
            )
        if (
            uow.stores.activity_history.approval_decision_for_request(request_id)
            is not None
        ):
            raise GatewayKeyRotationConflict(
                "rotation approval request was already decided"
            )
        return
    if target not in {
        GatewayKeyRotationStatus.APPROVED,
        GatewayKeyRotationStatus.REJECTED,
    }:
        return
    if current.approval_request_id is None:
        raise GatewayKeyRotationConflict("rotation approval request is missing")
    try:
        request = uow.stores.activity_history.get_approval_request(
            current.approval_request_id
        )
    except KeyError as error:
        raise GatewayKeyRotationConflict(
            "rotation approval request was not found"
        ) from error
    if request.subject != gateway_key_rotation_approval_subject(current):
        raise GatewayKeyRotationConflict(
            "rotation approval request reviews different intent"
        )
    decision = uow.stores.activity_history.approval_decision_for_request(
        request.request_id
    )
    if decision is None or decision.decision_id != command.approval_decision_id:
        raise GatewayKeyRotationConflict("rotation approval decision is missing")
    expected = (
        "approved"
        if target is GatewayKeyRotationStatus.APPROVED
        else "rejected"
    )
    if decision.decision.value != expected:
        raise GatewayKeyRotationConflict(
            "rotation approval decision does not authorize target state"
        )
    if decision.scope is not PolicyScope.DELEGATION_KEY_ROTATE_APPROVE:
        raise GatewayKeyRotationConflict(
            "rotation approval decision has insufficient authority"
        )


def _transition(current: GatewayKeyRotation, command: AdvanceGatewayKeyRotation,
                now: int) -> GatewayKeyRotation:
    if command.target_status not in _LEGAL.get(current.status, set()):
        raise GatewayKeyRotationConflict("rotation transition is not legal")
    changes: dict[str, object] = {
        "status": command.target_status, "version": current.version + 1,
        "updated_by": command.advanced_by, "updated_at": command.advanced_at}
    target = command.target_status
    if target is GatewayKeyRotationStatus.AWAITING_APPROVAL:
        _required(command.approval_request_id, "approval_request_id")
        changes["approval_request_id"] = command.approval_request_id
    elif target in {GatewayKeyRotationStatus.APPROVED,
                    GatewayKeyRotationStatus.REJECTED}:
        _required(command.approval_decision_id, "approval_decision_id")
        changes["approval_decision_id"] = command.approval_decision_id
        if target is GatewayKeyRotationStatus.REJECTED:
            _required(command.failure_code, "failure_code")
            changes["failure_code"] = command.failure_code
    elif target is GatewayKeyRotationStatus.GENERATION_PREPARED:
        _required(
            command.generation_provider_registration_id,
            "generation_provider_registration_id",
        )
        if command.generation_action_digest is None:
            raise GatewayKeyRotationConflict(
                "generation_action_digest is required"
            )
        changes.update(
            generation_provider_registration_id=(
                command.generation_provider_registration_id
            ),
            generation_action_digest=command.generation_action_digest,
        )
    elif target is GatewayKeyRotationStatus.KEY_GENERATED:
        for name in ("new_key_id", "new_secret_version_id"):
            _required(getattr(command, name), name)
            changes[name] = getattr(command, name)
        if (type(command.new_secret_version_number) is not int
                or command.new_secret_version_number < 1):
            raise GatewayKeyRotationConflict("generated key version is required")
        changes["new_secret_version_number"] = command.new_secret_version_number
    elif target is GatewayKeyRotationStatus.OVERLAP_DEPLOYING:
        _checkpoint(command.deployment, GatewayKeyRotationDeploymentPhase.OVERLAP,
                    GatewayKeyRotationDeploymentStatus.PREPARED)
        changes["overlap_deployment"] = command.deployment
    elif target is GatewayKeyRotationStatus.OVERLAP_READY:
        _checkpoint(command.deployment, GatewayKeyRotationDeploymentPhase.OVERLAP,
                    GatewayKeyRotationDeploymentStatus.ACCEPTED)
        if (current.overlap_deployment is None
                or command.deployment is None
                or not _same_deployment_identity(current.overlap_deployment,
                                                 command.deployment)):
            raise GatewayKeyRotationConflict("overlap acceptance identity changed")
        changes["overlap_deployment"] = command.deployment
    elif target is GatewayKeyRotationStatus.NEW_KEY_ACTIVE:
        _required(command.new_key_activated_at, "new_key_activated_at")
        changes["new_key_activated_at"] = command.new_key_activated_at
        changes["drain_deadline_epoch"] = (
            now + current.maximum_grant_lifetime_seconds + current.clock_skew_seconds)
    elif target is GatewayKeyRotationStatus.RETIREMENT_DEPLOYING:
        if current.drain_deadline_epoch is None or now < current.drain_deadline_epoch:
            raise GatewayKeyRotationConflict("old capability grants have not drained")
        _checkpoint(command.deployment, GatewayKeyRotationDeploymentPhase.RETIREMENT,
                    GatewayKeyRotationDeploymentStatus.PREPARED)
        changes["retirement_deployment"] = command.deployment
    elif target is GatewayKeyRotationStatus.RETIREMENT_READY:
        _checkpoint(command.deployment, GatewayKeyRotationDeploymentPhase.RETIREMENT,
                    GatewayKeyRotationDeploymentStatus.ACCEPTED)
        if (current.retirement_deployment is None
                or command.deployment is None
                or not _same_deployment_identity(current.retirement_deployment,
                                                 command.deployment)):
            raise GatewayKeyRotationConflict("retirement acceptance identity changed")
        changes["retirement_deployment"] = command.deployment
    elif target is GatewayKeyRotationStatus.OLD_KEY_RETIRED:
        _required(command.old_key_retired_at, "old_key_retired_at")
        changes["old_key_retired_at"] = command.old_key_retired_at
    elif target is GatewayKeyRotationStatus.REVOCATION_PREPARED:
        if not isinstance(
            command.revocation,
            GatewayKeyRotationRevocationCheckpoint,
        ):
            raise GatewayKeyRotationConflict(
                "exact revocation checkpoint is required"
            )
        changes["revocation"] = command.revocation
    elif target is GatewayKeyRotationStatus.COMPLETED:
        _checkpoint(current.retirement_deployment,
                    GatewayKeyRotationDeploymentPhase.RETIREMENT,
                    GatewayKeyRotationDeploymentStatus.ACCEPTED)
        if current.revocation is None:
            raise GatewayKeyRotationConflict(
                "exact revocation checkpoint is missing"
            )
        _required(command.old_secret_revoked_at, "old_secret_revoked_at")
        changes["old_secret_revoked_at"] = command.old_secret_revoked_at
    elif target is GatewayKeyRotationStatus.BLOCKED:
        _required(command.failure_code, "failure_code")
        changes["failure_code"] = command.failure_code
    return replace(current, **changes)


def _transition_fingerprint(command: AdvanceGatewayKeyRotation) -> str:
    return _digest({
        "rotation_id": command.rotation_id,
        "transition_id": command.transition_id,
        "expected_status": command.expected_status.value,
        "expected_version": command.expected_version,
        "target_status": command.target_status.value,
        "advanced_by": command.advanced_by,
        "advanced_at": command.advanced_at,
        "approval_request_id": command.approval_request_id,
        "approval_decision_id": command.approval_decision_id,
        "generation_provider_registration_id": (
            command.generation_provider_registration_id
        ),
        "generation_action_digest": command.generation_action_digest,
        "new_key_id": command.new_key_id,
        "new_secret_version_id": command.new_secret_version_id,
        "new_secret_version_number": command.new_secret_version_number,
        "deployment": _checkpoint_semantics(command.deployment),
        "new_key_activated_at": command.new_key_activated_at,
        "revocation": _revocation_semantics(command.revocation),
        "old_key_retired_at": command.old_key_retired_at,
        "old_secret_revoked_at": command.old_secret_revoked_at,
        "failure_code": command.failure_code,
    })


def _checkpoint_semantics(value):
    if value is None:
        return None
    return {
        "phase": value.phase.value,
        "status": value.status.value,
        "session_id": value.session_id,
        "plan_id": value.plan_id,
        "approval_request_id": value.approval_request_id,
        "approval_decision_id": value.approval_decision_id,
        "execution_request_id": value.execution_request_id,
        "run_id": value.run_id,
        "base_authored_graph_id": value.base_authored_graph_id,
        "base_realized_projection_id": value.base_realized_projection_id,
        "desired_authored_graph_id": value.desired_authored_graph_id,
        "desired_realized_projection_id": value.desired_realized_projection_id,
        "desired_revision": value.desired_revision,
        "prepared_at": value.prepared_at,
        "accepted_current_graph_id": value.accepted_current_graph_id,
        "accepted_current_projection_id": value.accepted_current_projection_id,
        "accepted_at": value.accepted_at,
    }


def _revocation_semantics(value):
    if value is None:
        return None
    return {
        "provider_registration_id": value.provider_registration_id,
        "secret_reference": value.secret_reference.reference_id,
        "provider_version_id": value.provider_version_id,
        "provider_version_number": value.provider_version_number,
        "revocation_id": value.revocation_id,
        "correlation_id": value.correlation_id,
        "action_digest": value.action_digest,
        "prepared_at": value.prepared_at,
    }


def _checkpoint(value, phase, status):
    if (not isinstance(value, GatewayKeyRotationDeploymentCheckpoint)
            or value.phase is not phase or value.status is not status):
        raise GatewayKeyRotationConflict("deployment checkpoint is incomplete")


def _same_deployment_identity(left, right) -> bool:
    return replace(left, status=right.status,
                   accepted_current_graph_id=right.accepted_current_graph_id,
                   accepted_current_projection_id=right.accepted_current_projection_id,
                   accepted_at=right.accepted_at) == right


def _scope(scopes):
    if (not isinstance(scopes, tuple)
            or any(not isinstance(scope, PolicyScope) for scope in scopes)):
        raise GatewayKeyRotationAuthorizationDenied("rotation scopes are malformed")
    if PolicyScope.DELEGATION_KEY_ROTATE not in scopes:
        raise GatewayKeyRotationAuthorizationDenied(
            "gateway key rotation requires delegation-key:rotate")


def _identifier(value, name):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise GatewayKeyRotationError(f"{name} is malformed")


def _run_id(value: object) -> None:
    try:
        RunId(value)  # type: ignore[arg-type]
    except ValueError:
        valid = False
    else:
        valid = True
    if not valid:
        raise GatewayKeyRotationError("run_id is malformed")


def _required(value, name):
    if value is None:
        raise GatewayKeyRotationConflict(f"{name} is required")
    _identifier(value, name)


def _text(value, name):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise GatewayKeyRotationError(f"{name} is malformed")


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


__all__ = [
    "AdvanceGatewayKeyRotationDeployment",
    "AdvanceGatewayKeyRotation",
    "GatewayKeyRotation",
    "GatewayKeyRotationAuthorizationDenied",
    "GatewayKeyRotationConflict",
    "GatewayKeyRotationDeploymentCheckpoint",
    "GatewayKeyRotationDeploymentHandoff",
    "GatewayKeyRotationDeploymentPhase",
    "GatewayKeyRotationDeploymentStatus",
    "GatewayKeyRotationError",
    "GatewayKeyRotationNotFound",
    "GatewayKeyRotationReadModel",
    "GatewayKeyRotationService",
    "GatewayKeyRotationStatus",
    "GatewayKeyRotationTransition",
    "RequestGatewayKeyRotation",
    "ReadGatewayKeyRotationDeploymentHandoff",
    "gateway_key_rotation_approval_subject",
]
