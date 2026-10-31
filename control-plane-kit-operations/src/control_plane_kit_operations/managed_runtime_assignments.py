"""Separate durable management permission; private transports remain installer-owned."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

import rfc8785

from control_plane_kit_core.identity import PrincipalIdentity, PrincipalKind
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp
from control_plane_kit_operations.runtime_authorities import (
    LocalDockerSocketAuthority, RegisteredRuntimeAuthority, RegisteredRuntimeAuthorityStatus,
    RuntimeAuthorityKind,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.workspace_permissions import (
    WorkspaceAdministrationContext, WorkspaceAdministrationPolicy, WorkspacePermissionError,
    _identity, _key, _revision, _text, permission_fingerprint,
)


class ManagedRuntimeAssignmentError(WorkspacePermissionError):
    """The exact management assignment is malformed, unavailable or conflicting."""


class ManagedRuntimeAssignmentNotFound(ManagedRuntimeAssignmentError):
    """The exact requested assignment does not exist."""


class ManagedRuntimeAssignmentConflict(ManagedRuntimeAssignmentError):
    """Submitted revision or idempotent intent conflicts with retained truth."""


class ManagedRuntimeAssignmentStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


def _reference(value):
    if type(value) is not RuntimeAuthorityReference:
        raise ManagedRuntimeAssignmentError("management authority reference is malformed")
    _text(value.reference_id, 128)
    return value


def _context(value):
    if type(value) is not WorkspaceAdministrationContext:
        raise ManagedRuntimeAssignmentError("management administration context is malformed")


@dataclass(frozen=True)
class ManagedRuntimeCapability:
    """Opaque identity for one fixed installer-admitted local Docker transport."""
    capability_id: str
    runtime_kind: RuntimeKind = RuntimeKind.DOCKER
    authority_kind: RuntimeAuthorityKind = RuntimeAuthorityKind.LOCAL_DOCKER_SOCKET

    def __post_init__(self):
        _text(self.capability_id, 128)
        if (self.runtime_kind is not RuntimeKind.DOCKER
                or self.authority_kind is not RuntimeAuthorityKind.LOCAL_DOCKER_SOCKET):
            raise ManagedRuntimeAssignmentError("management capability kind is unsupported")

    def descriptor(self):
        return dict(capability_id=self.capability_id, runtime_kind=self.runtime_kind.value,
                    authority_kind=self.authority_kind.value)


@dataclass(frozen=True)
class ManagedRuntimeCapabilityPolicy:
    installation_id: str
    capabilities: tuple[ManagedRuntimeCapability, ...]

    def __post_init__(self):
        _text(self.installation_id)
        if (type(self.capabilities) is not tuple or len(self.capabilities) > 64
                or any(type(value) is not ManagedRuntimeCapability for value in self.capabilities)
                or len({value.capability_id for value in self.capabilities}) != len(self.capabilities)):
            raise ManagedRuntimeAssignmentError("management capability policy is malformed")

    def require(self, capability_id):
        for value in self.capabilities:
            if value.capability_id == capability_id:
                return value
        raise ManagedRuntimeAssignmentError("management capability is unavailable")


@dataclass(frozen=True)
class GrantManagedRuntimeAccess:
    context: WorkspaceAdministrationContext
    workspace_id: str
    runtime_id: str
    authority_ref: RuntimeAuthorityReference
    registration_id: str
    capability_id: str
    expected_revision: int
    idempotency_key: IdempotencyKey

    def __post_init__(self):
        _context(self.context)
        for value in (self.workspace_id, self.runtime_id, self.registration_id):
            _text(value)
        _reference(self.authority_ref)
        _text(self.capability_id, 128)
        _revision(self.expected_revision)
        _key(self.idempotency_key)


@dataclass(frozen=True)
class RevokeManagedRuntimeAccess:
    context: WorkspaceAdministrationContext
    workspace_id: str
    runtime_id: str
    authority_ref: RuntimeAuthorityReference
    expected_revision: int
    idempotency_key: IdempotencyKey

    def __post_init__(self):
        _context(self.context)
        _text(self.workspace_id)
        _text(self.runtime_id)
        _reference(self.authority_ref)
        _revision(self.expected_revision)
        _key(self.idempotency_key)


def managed_runtime_assignment_id(installation_id, workspace_id, runtime_id, authority_ref):
    for value in (installation_id, workspace_id, runtime_id):
        _text(value)
    _reference(authority_ref)
    return "mra_" + permission_fingerprint(dict(installation_id=installation_id,
        workspace_id=workspace_id, runtime_id=runtime_id, authority_ref=authority_ref.reference_id))


def managed_runtime_assignment_intent(command, operation):
    value = dict(operation=operation, installation_id=command.context.installation_id,
        actor=command.context.identity.descriptor(), workspace_id=command.workspace_id,
        runtime_id=command.runtime_id, authority_ref=command.authority_ref.reference_id,
        expected_revision=command.expected_revision, idempotency_key=command.idempotency_key.value)
    if operation == "grant":
        value.update(registration_id=command.registration_id, capability_id=command.capability_id)
    return value


@dataclass(frozen=True)
class ManagedRuntimeAssignment:
    assignment_id: str
    installation_id: str
    workspace_id: str
    runtime_id: str
    authority_ref: RuntimeAuthorityReference
    registration_id: str
    capability_id: str
    status: ManagedRuntimeAssignmentStatus
    revision: int

    def __post_init__(self):
        if self.assignment_id != managed_runtime_assignment_id(
                self.installation_id, self.workspace_id, self.runtime_id, self.authority_ref):
            raise ManagedRuntimeAssignmentError("management assignment identity disagrees")
        _text(self.registration_id)
        _text(self.capability_id, 128)
        _revision(self.revision)
        if type(self.status) is not ManagedRuntimeAssignmentStatus:
            raise ManagedRuntimeAssignmentError("management assignment status is malformed")

    def descriptor(self):
        return dict(assignment_id=self.assignment_id, installation_id=self.installation_id,
            workspace_id=self.workspace_id, runtime_id=self.runtime_id,
            authority_ref=self.authority_ref.reference_id, registration_id=self.registration_id,
            capability_id=self.capability_id, status=self.status.value, revision=self.revision)

    @classmethod
    def from_descriptor(cls, payload):
        try:
            if type(payload) is not dict or set(payload) != {
                    "assignment_id", "installation_id", "workspace_id", "runtime_id", "authority_ref",
                    "registration_id", "capability_id", "status", "revision"}:
                raise ValueError
            values = dict(payload)
            values["authority_ref"] = RuntimeAuthorityReference(values["authority_ref"])
            values["status"] = ManagedRuntimeAssignmentStatus(values["status"])
            return cls(**values)
        except (ValueError, TypeError, KeyError):
            raise ManagedRuntimeAssignmentError("persisted management assignment is malformed") from None


@dataclass(frozen=True)
class ManagedRuntimeAssignmentReceipt:
    receipt_id: str
    actor: PrincipalIdentity | None
    operation: str
    prior_assignment: ManagedRuntimeAssignment | None
    resulting_assignment: ManagedRuntimeAssignment
    idempotency_key: str | None
    occurred_at: str

    def __post_init__(self):
        _text(self.receipt_id)
        validate_canonical_utc_timestamp(self.occurred_at)
        before, after = self.prior_assignment, self.resulting_assignment
        if (type(after) is not ManagedRuntimeAssignment or
                (before is not None and type(before) is not ManagedRuntimeAssignment)):
            raise ManagedRuntimeAssignmentError("management receipt assignment is malformed")
        if (after.revision != (0 if before is None else before.revision) + 1
                or (before is not None and before.assignment_id != after.assignment_id)):
            raise ManagedRuntimeAssignmentError("management receipt revision disagrees")
        if self.operation == "authority-revoked":
            if self.actor is not None or self.idempotency_key is not None or before is None:
                raise ManagedRuntimeAssignmentError("management invalidation cause is malformed")
            if before.status is not ManagedRuntimeAssignmentStatus.ACTIVE:
                raise ManagedRuntimeAssignmentError("management invalidation requires active assignment")
        elif self.operation in ("grant", "revoke"):
            _identity(self.actor)
            _text(self.idempotency_key, 200)
        else:
            raise ManagedRuntimeAssignmentError("management receipt operation is malformed")
        if self.operation == "grant":
            if after.status is not ManagedRuntimeAssignmentStatus.ACTIVE:
                raise ManagedRuntimeAssignmentError("management grant result disagrees")
        elif (before is None or after != replace(before,
                status=ManagedRuntimeAssignmentStatus.REVOKED, revision=before.revision + 1)):
            raise ManagedRuntimeAssignmentError("management revocation result disagrees")

    @property
    def registration_id(self):
        return self.resulting_assignment.registration_id

    def descriptor(self):
        return dict(receipt_id=self.receipt_id, actor=None if self.actor is None else self.actor.descriptor(),
            operation=self.operation, prior_assignment=None if self.prior_assignment is None
            else self.prior_assignment.descriptor(), resulting_assignment=self.resulting_assignment.descriptor(),
            idempotency_key=self.idempotency_key, occurred_at=self.occurred_at)

    @classmethod
    def from_descriptor(cls, payload):
        try:
            if (type(payload) is not dict or set(payload) != {"receipt_id", "actor", "operation",
                    "prior_assignment", "resulting_assignment", "idempotency_key", "occurred_at"}
                    or len(rfc8785.dumps(payload)) > 32768):
                raise ValueError
            values = dict(payload)
            if values["actor"] is not None:
                actor = values["actor"]
                if type(actor) is not dict or set(actor) != {"issuer", "subject_id", "kind"}:
                    raise ValueError
                values["actor"] = PrincipalIdentity(actor["issuer"], actor["subject_id"], PrincipalKind(actor["kind"]))
            for key in ("prior_assignment", "resulting_assignment"):
                if values[key] is not None:
                    values[key] = ManagedRuntimeAssignment.from_descriptor(values[key])
            return cls(**values)
        except (ValueError, TypeError, KeyError):
            raise ManagedRuntimeAssignmentError("persisted management receipt is malformed") from None


@dataclass(frozen=True)
class ManagedRuntimeAssignmentResult:
    receipt: ManagedRuntimeAssignmentReceipt
    replayed: bool = False

    def descriptor(self):
        return dict(receipt=self.receipt.descriptor(), replayed=self.replayed)


@dataclass(frozen=True)
class ManagedRuntimeSelection:
    assignment_id: str
    installation_id: str
    workspace_id: str
    runtime_id: str
    authority_ref: RuntimeAuthorityReference
    registration_id: str
    capability_id: str
    revision: int

    def __post_init__(self):
        ManagedRuntimeAssignment(self.assignment_id, self.installation_id, self.workspace_id,
            self.runtime_id, self.authority_ref, self.registration_id, self.capability_id,
            ManagedRuntimeAssignmentStatus.ACTIVE, self.revision)
        if self.revision < 1:
            raise ManagedRuntimeAssignmentError("management selection revision is malformed")

    def descriptor(self):
        return dict(assignment_id=self.assignment_id, installation_id=self.installation_id,
            workspace_id=self.workspace_id, runtime_id=self.runtime_id, authority_ref=self.authority_ref.reference_id,
            registration_id=self.registration_id, capability_id=self.capability_id, revision=self.revision)


def select_managed_runtime_assignment(*, installation_id, workspace_id, runtime_id,
                                      authority, assignment, capability_policy):
    """Pure exact selection; current-row observation and attempt admission are separate."""
    if (type(capability_policy) is not ManagedRuntimeCapabilityPolicy
            or capability_policy.installation_id != installation_id
            or type(assignment) is not ManagedRuntimeAssignment
            or assignment.status is not ManagedRuntimeAssignmentStatus.ACTIVE
            or assignment.revision < 1
            or (assignment.installation_id, assignment.workspace_id, assignment.runtime_id)
                != (installation_id, workspace_id, runtime_id)
            or type(authority) is not RegisteredRuntimeAuthority
            or authority.status is not RegisteredRuntimeAuthorityStatus.ACTIVE
            or authority.workspace_id != workspace_id
            or authority.authority_ref != assignment.authority_ref
            or authority.registration_id != assignment.registration_id
            or authority.runtime_kind is not RuntimeKind.DOCKER
            or type(authority.authority) is not LocalDockerSocketAuthority):
        return None
    try:
        capability_policy.require(assignment.capability_id)
    except ManagedRuntimeAssignmentError:
        return None
    return ManagedRuntimeSelection(assignment.assignment_id, installation_id, workspace_id, runtime_id,
        assignment.authority_ref, assignment.registration_id, assignment.capability_id, assignment.revision)


class ManagedRuntimeAssignmentCommandService:
    def __init__(self, unit_of_work_factory, *, administration_policy, capability_policy, clock, id_factory):
        if (type(administration_policy) is not WorkspaceAdministrationPolicy
                or type(capability_policy) is not ManagedRuntimeCapabilityPolicy
                or capability_policy.installation_id != administration_policy.installation_id):
            raise ManagedRuntimeAssignmentError("management policy binding is malformed")
        self._factory, self._administration, self._capabilities = unit_of_work_factory, administration_policy, capability_policy
        self._clock, self._ids = clock, id_factory

    def grant(self, command):
        return self._change(command, "grant", GrantManagedRuntimeAccess)

    def revoke(self, command):
        return self._change(command, "revoke", RevokeManagedRuntimeAccess)

    def _change(self, command, operation, expected):
        if type(command) is not expected:
            raise ManagedRuntimeAssignmentError("management command is malformed")
        self._administration.require(command.context)
        with self._factory() as uow:
            result = uow.stores.managed_runtime_assignments.change(command, operation,
                capability_policy=self._capabilities, clock=self._clock, id_factory=self._ids)
            uow.commit()
            return result


class ManagedRuntimeSelectionService:
    def __init__(self, unit_of_work_factory, *, capability_policy):
        if type(capability_policy) is not ManagedRuntimeCapabilityPolicy:
            raise ManagedRuntimeAssignmentError("management capability policy is malformed")
        self._factory, self._capabilities = unit_of_work_factory, capability_policy

    def select(self, *, workspace_id, runtime_id, authority):
        with self._factory() as uow:
            return uow.stores.managed_runtime_assignments.select(workspace_id=workspace_id,
                runtime_id=runtime_id, authority=authority, capability_policy=self._capabilities)


@dataclass(frozen=True)
class ManagedRuntimeAssignmentListCursor:
    installation_id: str
    workspace_id: str
    after_runtime_id: str
    after_authority_ref: RuntimeAuthorityReference

    def __post_init__(self):
        for value in (self.installation_id, self.workspace_id, self.after_runtime_id):
            _text(value)
        _reference(self.after_authority_ref)

    def descriptor(self):
        return dict(installation_id=self.installation_id, workspace_id=self.workspace_id,
                    after_runtime_id=self.after_runtime_id, after_authority_ref=self.after_authority_ref.reference_id)


@dataclass(frozen=True)
class ManagedRuntimeAssignmentHistoryCursor:
    installation_id: str
    workspace_id: str
    runtime_id: str
    authority_ref: RuntimeAuthorityReference
    after_revision: int

    def __post_init__(self):
        for value in (self.installation_id, self.workspace_id, self.runtime_id):
            _text(value)
        _reference(self.authority_ref)
        _revision(self.after_revision)
        if self.after_revision < 1:
            raise ManagedRuntimeAssignmentError("management history cursor is malformed")

    def descriptor(self):
        return dict(installation_id=self.installation_id, workspace_id=self.workspace_id,
                    runtime_id=self.runtime_id, authority_ref=self.authority_ref.reference_id,
                    after_revision=self.after_revision)


@dataclass(frozen=True)
class ManagedRuntimeAssignmentPage:
    items: tuple
    next_cursor: ManagedRuntimeAssignmentListCursor | ManagedRuntimeAssignmentHistoryCursor | None

    def __post_init__(self):
        if (type(self.items) is not tuple or len(self.items) > 100
                or (self.next_cursor is not None and type(self.next_cursor) not in
                    (ManagedRuntimeAssignmentListCursor, ManagedRuntimeAssignmentHistoryCursor))):
            raise ManagedRuntimeAssignmentError("management page is malformed")

    def descriptor(self):
        return dict(items=[item.descriptor() for item in self.items],
                    next_cursor=None if self.next_cursor is None else self.next_cursor.descriptor())


def _limit(value):
    if type(value) is not int or not 1 <= value <= 100:
        raise ManagedRuntimeAssignmentError("management page limit is malformed")


class ManagedRuntimeAssignmentReadService:
    """Current admin-only bounded independent pages, never transport disclosure."""
    def __init__(self, unit_of_work_factory, *, administration_policy):
        self._factory, self._policy = unit_of_work_factory, administration_policy

    def inspect(self, context, *, workspace_id, runtime_id, authority_ref):
        self._policy.require(context)
        with self._factory() as uow:
            uow.stores.workspace_permissions.require_installation(context.installation_id)
            result = uow.stores.managed_runtime_assignments.get(
                context.installation_id, workspace_id, runtime_id, authority_ref)
            if result is None:
                raise ManagedRuntimeAssignmentNotFound("management assignment is unavailable")
            return result

    def list(self, context, *, workspace_id, limit=50, cursor=None):
        self._policy.require(context)
        _text(workspace_id)
        _limit(limit)
        if cursor is not None and (type(cursor) is not ManagedRuntimeAssignmentListCursor
                or (cursor.installation_id, cursor.workspace_id) != (context.installation_id, workspace_id)):
            raise ManagedRuntimeAssignmentError("management list cursor is incongruent")
        with self._factory() as uow:
            uow.stores.workspace_permissions.require_installation(context.installation_id)
            items = uow.stores.managed_runtime_assignments.list(
                context.installation_id, workspace_id, limit=limit + 1, cursor=cursor)
        more, items = len(items) > limit, items[:limit]
        following = (ManagedRuntimeAssignmentListCursor(context.installation_id, workspace_id,
                     items[-1].runtime_id, items[-1].authority_ref) if more else None)
        return ManagedRuntimeAssignmentPage(items, following)

    def history(self, context, *, workspace_id, runtime_id, authority_ref, limit=50, cursor=None):
        self._policy.require(context)
        for value in (workspace_id, runtime_id):
            _text(value)
        _reference(authority_ref)
        _limit(limit)
        if cursor is not None and (type(cursor) is not ManagedRuntimeAssignmentHistoryCursor
                or (cursor.installation_id, cursor.workspace_id, cursor.runtime_id, cursor.authority_ref)
                    != (context.installation_id, workspace_id, runtime_id, authority_ref)):
            raise ManagedRuntimeAssignmentError("management history cursor is incongruent")
        with self._factory() as uow:
            uow.stores.workspace_permissions.require_installation(context.installation_id)
            items = uow.stores.managed_runtime_assignments.history(context.installation_id,
                workspace_id, runtime_id, authority_ref, limit=limit + 1, cursor=cursor)
        more, items = len(items) > limit, items[:limit]
        following = (ManagedRuntimeAssignmentHistoryCursor(context.installation_id, workspace_id,
                     runtime_id, authority_ref, items[-1].resulting_assignment.revision) if more else None)
        return ManagedRuntimeAssignmentPage(items, following)
