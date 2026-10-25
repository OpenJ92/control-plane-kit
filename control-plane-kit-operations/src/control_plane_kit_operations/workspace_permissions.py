"""Explicit installation administration and durable workspace permission owners."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Callable

import rfc8785

from control_plane_kit_core.identity import (
    AuthenticatedPrincipal, IdentityContractError, PrincipalIdentity, PrincipalKind,
    TrustedCommandContext, WorkspaceGrant,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp
from control_plane_kit_operations.workflows import IdempotencyKey


class WorkspacePermissionError(ValueError):
    """Bounded malformed policy or command failure."""


class WorkspacePermissionDenied(WorkspacePermissionError):
    """The verified caller has no authority for this operation."""


class WorkspacePermissionConflict(WorkspacePermissionError):
    """Current revision, installation or original command intent differs."""


class WorkspacePermissionNotFound(WorkspacePermissionError):
    """No exact workspace or persisted permission exists."""


def _text(value: object, maximum: int = 512) -> str:
    try:
        valid = type(value) is str and bool(value.strip()) and len(value.encode("utf-8")) <= maximum
    except UnicodeError:
        valid = False
    if not valid:
        raise WorkspacePermissionError("permission identifier is malformed")
    return value


def _identity(value: object) -> PrincipalIdentity:
    if type(value) is not PrincipalIdentity:
        raise WorkspacePermissionError("permission identity is malformed")
    _text(value.issuer)
    _text(value.subject_id)
    return value


def _scopes(value: object, *, empty: bool = False) -> tuple[PolicyScope, ...]:
    if (type(value) is not tuple or (not value and not empty)
            or any(type(scope) is not PolicyScope for scope in value)
            or len(set(value)) != len(value)):
        raise WorkspacePermissionError("permission scopes are malformed")
    return tuple(sorted(value, key=lambda scope: scope.value))


def _revision(value: object) -> int:
    if type(value) is not int or not 0 <= value < 9_223_372_036_854_775_807:
        raise WorkspacePermissionError("permission revision is malformed")
    return value


def _key(value: object) -> IdempotencyKey:
    if type(value) is not IdempotencyKey:
        raise WorkspacePermissionError("permission command key is malformed")
    _text(value.value, 200)
    return value


def permission_fingerprint(value: dict) -> str:
    return sha256(rfc8785.dumps(value)).hexdigest()


@dataclass(frozen=True)
class WorkspaceAdministrationContext:
    identity: PrincipalIdentity
    installation_id: str

    def __post_init__(self):
        _identity(self.identity)
        _text(self.installation_id)


@dataclass(frozen=True)
class WorkspaceAdministrationPolicy:
    """Trusted composition policy; it contains no credentials or workspace grants."""

    installation_id: str
    administrators: tuple[PrincipalIdentity, ...]
    known_identities: tuple[PrincipalIdentity, ...]

    def __post_init__(self):
        _text(self.installation_id)
        for identities in (self.administrators, self.known_identities):
            if type(identities) is not tuple or not identities or len(identities) > 1024:
                raise WorkspacePermissionError("installation identity policy is malformed")
            for identity in identities:
                _identity(identity)
            if len(set(identities)) != len(identities):
                raise WorkspacePermissionError("installation identity policy is malformed")
        if not set(self.administrators) <= set(self.known_identities):
            raise WorkspacePermissionError("administrator identity is not configured")

    def authorize(self, verified_principal: AuthenticatedPrincipal) -> WorkspaceAdministrationContext:
        if (type(verified_principal) is not AuthenticatedPrincipal
                or verified_principal.identity not in self.administrators):
            raise WorkspacePermissionDenied("workspace administration is denied")
        return WorkspaceAdministrationContext(verified_principal.identity, self.installation_id)

    def require(self, context: WorkspaceAdministrationContext) -> None:
        if (type(context) is not WorkspaceAdministrationContext
                or context.installation_id != self.installation_id
                or context.identity not in self.administrators):
            raise WorkspacePermissionDenied("workspace administration is denied")

    def require_known(self, target: PrincipalIdentity) -> None:
        _identity(target)
        if target not in self.known_identities:
            raise WorkspacePermissionDenied("new workspace grant target is not configured")


@dataclass(frozen=True)
class GrantWorkspacePermissions:
    context: WorkspaceAdministrationContext
    workspace_id: str
    target: PrincipalIdentity
    scopes: tuple[PolicyScope, ...]
    expected_revision: int
    idempotency_key: IdempotencyKey

    def __post_init__(self):
        if type(self.context) is not WorkspaceAdministrationContext:
            raise WorkspacePermissionError("administrative context is required")
        _text(self.workspace_id)
        _identity(self.target)
        object.__setattr__(self, "scopes", _scopes(self.scopes))
        _revision(self.expected_revision)
        _key(self.idempotency_key)


@dataclass(frozen=True)
class RevokeWorkspacePermissions(GrantWorkspacePermissions):
    """Remove exact scopes; this may address a no-longer-configured identity."""


@dataclass(frozen=True)
class WorkspacePermissionRecord:
    installation_id: str
    workspace_id: str
    target: PrincipalIdentity
    scopes: tuple[PolicyScope, ...]
    revision: int

    def __post_init__(self):
        _text(self.installation_id)
        _text(self.workspace_id)
        _identity(self.target)
        object.__setattr__(self, "scopes", _scopes(self.scopes, empty=True))
        _revision(self.revision)

    def descriptor(self) -> dict:
        return dict(installation_id=self.installation_id, workspace_id=self.workspace_id,
                    target=self.target.descriptor(), scopes=[scope.value for scope in self.scopes],
                    revision=self.revision)


@dataclass(frozen=True)
class WorkspacePermissionReceipt:
    receipt_id: str
    installation_id: str
    workspace_id: str
    actor: PrincipalIdentity
    target: PrincipalIdentity
    operation: str
    scopes: tuple[PolicyScope, ...]
    resulting_scopes: tuple[PolicyScope, ...]
    prior_revision: int
    revision: int
    idempotency_key: str
    occurred_at: str

    def __post_init__(self):
        for value in (self.receipt_id, self.installation_id, self.workspace_id):
            _text(value)
        _identity(self.actor)
        _identity(self.target)
        if self.operation not in ("grant", "revoke"):
            raise WorkspacePermissionError("permission receipt operation is malformed")
        object.__setattr__(self, "scopes", _scopes(self.scopes))
        object.__setattr__(self, "resulting_scopes", _scopes(self.resulting_scopes, empty=True))
        _revision(self.prior_revision)
        _revision(self.revision)
        if self.revision != self.prior_revision + 1:
            raise WorkspacePermissionError("permission receipt revision is malformed")
        _text(self.idempotency_key, 200)
        # Use the same temporal contract as other durable Operations records.
        validate_canonical_utc_timestamp(self.occurred_at)

    def descriptor(self) -> dict:
        return dict(receipt_id=self.receipt_id, installation_id=self.installation_id,
                    workspace_id=self.workspace_id, actor=self.actor.descriptor(),
                    target=self.target.descriptor(), operation=self.operation,
                    scopes=[scope.value for scope in self.scopes],
                    resulting_scopes=[scope.value for scope in self.resulting_scopes], prior_revision=self.prior_revision,
                    revision=self.revision, idempotency_key=self.idempotency_key,
                    occurred_at=self.occurred_at)


@dataclass(frozen=True)
class WorkspacePermissionResult:
    receipt: WorkspacePermissionReceipt
    replayed: bool = False

    def descriptor(self) -> dict:
        return dict(receipt=self.receipt.descriptor(), replayed=self.replayed)


@dataclass(frozen=True)
class WorkspacePermissionInitializationReceipt:
    installation_id: str
    actor: PrincipalIdentity
    idempotency_key: str
    occurred_at: str

    def __post_init__(self):
        _text(self.installation_id)
        _identity(self.actor)
        _text(self.idempotency_key, 200)
        validate_canonical_utc_timestamp(self.occurred_at)

    def descriptor(self) -> dict:
        return dict(installation_id=self.installation_id, actor=self.actor.descriptor(),
                    idempotency_key=self.idempotency_key, occurred_at=self.occurred_at,
                    kind="fresh-workspace-policy-binding")

    @classmethod
    def from_descriptor(cls, payload):
        try:
            if (type(payload) is not dict or set(payload) != {
                    "installation_id", "actor", "idempotency_key", "occurred_at", "kind"}
                    or payload["kind"] != "fresh-workspace-policy-binding"
                    or type(payload["actor"]) is not dict
                    or set(payload["actor"]) != {"issuer", "subject_id", "kind"}
                    or len(rfc8785.dumps(payload)) > 32768):
                raise ValueError
            actor = payload["actor"]
            return cls(payload["installation_id"],
                PrincipalIdentity(actor["issuer"], actor["subject_id"], PrincipalKind(actor["kind"])),
                payload["idempotency_key"], payload["occurred_at"])
        except (ValueError, TypeError, KeyError):
            raise WorkspacePermissionError("persisted installation receipt is malformed") from None


@dataclass(frozen=True)
class WorkspacePermissionInitializationResult:
    receipt: WorkspacePermissionInitializationReceipt
    replayed: bool = False

    def descriptor(self):
        return dict(receipt=self.receipt.descriptor(), replayed=self.replayed)


def permission_intent(command: GrantWorkspacePermissions, operation: str) -> dict:
    return dict(installation_id=command.context.installation_id, workspace_id=command.workspace_id,
                actor=command.context.identity.descriptor(), target=command.target.descriptor(),
                operation=operation, scopes=[scope.value for scope in command.scopes],
                expected_revision=command.expected_revision, idempotency_key=command.idempotency_key.value)


def current_workspace_context(identity: PrincipalIdentity, workspace_id: str,
                              record: WorkspacePermissionRecord | None,
                              original: tuple[PolicyScope, ...] | None = None) -> TrustedCommandContext:
    """One evaluator shared by initial resolution and caller-locked late admission."""
    if record is None or record.target != identity or record.workspace_id != workspace_id:
        raise IdentityContractError("current workspace permission is unavailable or denied")
    scopes = record.scopes if original is None else tuple(s for s in record.scopes if s in original)
    if not scopes:
        raise IdentityContractError("current workspace permission is unavailable or denied")
    return AuthenticatedPrincipal(identity, (WorkspaceGrant(workspace_id, scopes),)).command_context(workspace_id)


class WorkspacePermissionCommandService:
    def __init__(self, unit_of_work_factory: Callable, *, administration_policy: WorkspaceAdministrationPolicy,
                 clock: Callable, id_factory: Callable):
        self._factory = unit_of_work_factory
        self._policy = administration_policy
        self._clock = clock
        self._ids = id_factory

    def initialize(self, context: WorkspaceAdministrationContext, *, idempotency_key: IdempotencyKey):
        """Explicit fresh-installation binding, without grants or automatic seeding."""
        self._policy.require(context)
        _key(idempotency_key)
        with self._factory() as uow:
            result = uow.stores.workspace_permissions.initialize(
                context, idempotency_key, occurred_at=self._clock())
            uow.commit()
            return result

    def grant(self, command: GrantWorkspacePermissions) -> WorkspacePermissionResult:
        if type(command) is not GrantWorkspacePermissions:
            raise WorkspacePermissionError("grant command is required")
        self._policy.require(command.context)
        self._policy.require_known(command.target)
        return self._execute(command, "grant")

    def revoke(self, command: RevokeWorkspacePermissions) -> WorkspacePermissionResult:
        if type(command) is not RevokeWorkspacePermissions:
            raise WorkspacePermissionError("revoke command is required")
        self._policy.require(command.context)
        return self._execute(command, "revoke")

    def _execute(self, command, operation):
        with self._factory() as uow:
            result = uow.stores.workspace_permissions.change(
                command, operation, clock=self._clock, id_factory=self._ids)
            uow.commit()
            return result


class WorkspaceContextResolver:
    def __init__(self, unit_of_work_factory: Callable, *, installation_id: str):
        self._factory = unit_of_work_factory
        self._installation = _text(installation_id)

    def resolve(self, verified_principal: AuthenticatedPrincipal, workspace_id: str) -> TrustedCommandContext:
        if type(verified_principal) is not AuthenticatedPrincipal:
            raise IdentityContractError("verified principal is required")
        _text(workspace_id)
        with self._factory() as uow:
            record = uow.stores.workspace_permissions.get(
                self._installation, workspace_id, verified_principal.identity)
            return current_workspace_context(verified_principal.identity, workspace_id, record)


class DurablePrincipalAuthorizer(WorkspaceContextResolver):
    def authorize(self, principal: AuthenticatedPrincipal, workspace_id: str) -> TrustedCommandContext:
        if type(principal) is not AuthenticatedPrincipal:
            raise IdentityContractError("verified principal is required")
        original = principal.command_context(workspace_id)
        with self._factory() as uow:
            record = uow.stores.workspace_permissions.get(self._installation, workspace_id, principal.identity)
            return current_workspace_context(principal.identity, workspace_id, record, original.granted_scopes)
