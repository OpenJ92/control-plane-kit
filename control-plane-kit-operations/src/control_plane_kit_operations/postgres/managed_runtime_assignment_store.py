"""Exact assignment rows and receipts in the caller's existing transaction."""
from dataclasses import replace

from psycopg.types.json import Jsonb

from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_operations.managed_runtime_assignments import (
    GrantManagedRuntimeAccess, RevokeManagedRuntimeAccess, ManagedRuntimeAssignment,
    ManagedRuntimeAssignmentError, ManagedRuntimeAssignmentReceipt, ManagedRuntimeAssignmentResult,
    ManagedRuntimeAssignmentNotFound, ManagedRuntimeAssignmentConflict,
    ManagedRuntimeAssignmentStatus, ManagedRuntimeSelection, managed_runtime_assignment_id,
    managed_runtime_assignment_intent, select_managed_runtime_assignment,
)
from control_plane_kit_operations.runtime_authorities import RegisteredRuntimeAuthority, RuntimeAuthorityNotFound
from control_plane_kit_operations.workspace_permissions import (
    WorkspaceAdministrationContext, _text, permission_fingerprint,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from .runtime_authority_store import RuntimeAuthorityStore
from .temporal import decode_postgres_timestamp
from .workspace_permission_store import WorkspacePermissionStore


_COLUMNS = "assignment_id,installation_id,workspace_id,runtime_id,authority_ref,registration_id,capability_id,status,revision"
_KEY = "installation_id=%s AND workspace_id=%s AND runtime_id=%s AND authority_ref=%s"
_ORDER = "installation_id,workspace_id,runtime_id,authority_ref"


def _parameters(installation_id, workspace_id, runtime_id, authority_ref):
    managed_runtime_assignment_id(installation_id, workspace_id, runtime_id, authority_ref)
    return installation_id, workspace_id, runtime_id, authority_ref.reference_id


def _record(row):
    if row is None:
        return None
    try:
        return ManagedRuntimeAssignment(row[0], row[1], row[2], row[3], RuntimeAuthorityReference(row[4]),
                                        row[5], row[6], ManagedRuntimeAssignmentStatus(row[7]), row[8])
    except (ValueError, TypeError, IndexError):
        raise ManagedRuntimeAssignmentError("persisted management assignment is malformed") from None


def _intent_for_receipt(receipt):
    if receipt.operation == "authority-revoked":
        return None
    result = receipt.resulting_assignment
    args = dict(context=WorkspaceAdministrationContext(receipt.actor, result.installation_id),
        workspace_id=result.workspace_id, runtime_id=result.runtime_id, authority_ref=result.authority_ref,
        expected_revision=result.revision - 1, idempotency_key=IdempotencyKey(receipt.idempotency_key))
    if receipt.operation == "grant":
        command = GrantManagedRuntimeAccess(**args, registration_id=result.registration_id,
                                           capability_id=result.capability_id)
    else:
        command = RevokeManagedRuntimeAccess(**args)
    return permission_fingerprint(managed_runtime_assignment_intent(command, receipt.operation))


class ManagedRuntimeAssignmentStore:
    def __init__(self, connection):
        self.connection = connection

    def get(self, installation_id, workspace_id, runtime_id, authority_ref, *, for_update=False):
        parameters = _parameters(installation_id, workspace_id, runtime_id, authority_ref)
        if type(for_update) is not bool:
            raise ManagedRuntimeAssignmentError("management lookup is malformed")
        return _record(self.connection.execute("SELECT " + _COLUMNS
            + " FROM cpk_managed_runtime_assignments WHERE " + _KEY
            + (" FOR UPDATE" if for_update else ""), parameters).fetchone())

    def change(self, command, operation, *, capability_policy, clock, id_factory):
        installation = command.context.installation_id
        WorkspacePermissionStore(self.connection).require_installation(installation)
        key = _parameters(installation, command.workspace_id, command.runtime_id, command.authority_ref)
        registration = getattr(command, "registration_id", None)
        observed = None
        if operation == "revoke":
            observed = self.get(installation, command.workspace_id, command.runtime_id, command.authority_ref)
            if observed is None:
                raise ManagedRuntimeAssignmentNotFound("management assignment is unavailable")
            registration = observed.registration_id
        try:
            authority = RuntimeAuthorityStore(self.connection).get_registration_for_update(
                command.workspace_id, command.authority_ref, registration)
        except RuntimeAuthorityNotFound:
            raise ManagedRuntimeAssignmentError("management authority is unavailable") from None
        if operation == "grant":
            candidate = ManagedRuntimeAssignment(managed_runtime_assignment_id(*key[:3], command.authority_ref),
                *key[:3], command.authority_ref, registration, command.capability_id,
                ManagedRuntimeAssignmentStatus.REVOKED, 0)
            self.connection.execute("INSERT INTO cpk_managed_runtime_assignments (" + _COLUMNS
                + ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (candidate.assignment_id, *key, registration, command.capability_id, "revoked", 0))
        current = self.get(installation, command.workspace_id, command.runtime_id, command.authority_ref, for_update=True)
        if current is None or (observed is not None and observed.registration_id != current.registration_id):
            raise ManagedRuntimeAssignmentError("management binding changed while waiting")
        fingerprint = permission_fingerprint(managed_runtime_assignment_intent(command, operation))
        stored = self.connection.execute("SELECT intent_fingerprint,receipt FROM cpk_managed_runtime_assignment_receipts "
            "WHERE assignment_id=%s AND idempotency_key=%s", (current.assignment_id, command.idempotency_key.value)).fetchone()
        if stored is not None:
            receipt = ManagedRuntimeAssignmentReceipt.from_descriptor(stored[1])
            if (_intent_for_receipt(receipt) != stored[0]
                    or receipt.idempotency_key != command.idempotency_key.value
                    or receipt.resulting_assignment.assignment_id != current.assignment_id
                    or receipt.resulting_assignment.revision > current.revision):
                raise ManagedRuntimeAssignmentError("persisted management receipt is inconsistent")
            if stored[0] != fingerprint:
                raise ManagedRuntimeAssignmentConflict("management command key has different intent")
            return ManagedRuntimeAssignmentResult(receipt, replayed=True)
        if current.revision != command.expected_revision:
            raise ManagedRuntimeAssignmentConflict("management assignment revision changed")
        before = current if current.revision else None
        if operation == "grant":
            after = replace(current, registration_id=registration, capability_id=command.capability_id,
                            status=ManagedRuntimeAssignmentStatus.ACTIVE, revision=current.revision + 1)
            if select_managed_runtime_assignment(installation_id=installation, workspace_id=command.workspace_id,
                    runtime_id=command.runtime_id, authority=authority, assignment=after,
                    capability_policy=capability_policy) is None:
                raise ManagedRuntimeAssignmentError("management authority or capability is unavailable")
        elif operation == "revoke" and before is not None:
            after = replace(current, status=ManagedRuntimeAssignmentStatus.REVOKED, revision=current.revision + 1)
        else:
            raise ManagedRuntimeAssignmentError("management transition is malformed")
        receipt = ManagedRuntimeAssignmentReceipt(id_factory(), command.context.identity, operation,
                                                 before, after, command.idempotency_key.value, clock())
        self._save(receipt, fingerprint)
        return ManagedRuntimeAssignmentResult(receipt)

    def _save(self, receipt, fingerprint):
        result = receipt.resulting_assignment
        self.connection.execute("UPDATE cpk_managed_runtime_assignments SET registration_id=%s,capability_id=%s,"
            "status=%s,revision=%s WHERE assignment_id=%s", (result.registration_id, result.capability_id,
            result.status.value, result.revision, result.assignment_id))
        self.connection.execute("INSERT INTO cpk_managed_runtime_assignment_receipts "
            "(assignment_id,revision,idempotency_key,intent_fingerprint,receipt) VALUES (%s,%s,%s,%s,%s)",
            (result.assignment_id, result.revision, receipt.idempotency_key, fingerprint, Jsonb(receipt.descriptor())))

    def invalidate_authority(self, authority):
        """Called only by the authority owner while holding its exact row lock."""
        after_key = None
        occurred_at = None
        while True:
            extra = "" if after_key is None else " AND (" + _ORDER + ") > (%s,%s,%s,%s)"
            parameters = (authority.registration_id,) + (() if after_key is None else after_key)
            rows = self.connection.execute("SELECT " + _COLUMNS + " FROM cpk_managed_runtime_assignments "
                "WHERE registration_id=%s AND status='active'" + extra
                + " ORDER BY " + _ORDER + " LIMIT 64 FOR UPDATE", parameters).fetchall()
            if not rows:
                return
            if occurred_at is None:
                occurred_at = decode_postgres_timestamp(self.connection.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0])
            for row in rows:
                before = _record(row)
                if before.workspace_id != authority.workspace_id or before.authority_ref != authority.authority_ref:
                    raise ManagedRuntimeAssignmentError("management authority binding disagrees")
                after = replace(before, status=ManagedRuntimeAssignmentStatus.REVOKED, revision=before.revision + 1)
                receipt_id = "mra_event_" + permission_fingerprint(dict(assignment_id=after.assignment_id,
                    revision=after.revision, operation="authority-revoked", registration_id=authority.registration_id))
                self._save(ManagedRuntimeAssignmentReceipt(receipt_id, None, "authority-revoked", before,
                                                           after, None, occurred_at), None)
            last = _record(rows[-1])
            after_key = (last.installation_id, last.workspace_id, last.runtime_id, last.authority_ref.reference_id)

    def select(self, *, workspace_id, runtime_id, authority, capability_policy, expected=None):
        if type(authority) is not RegisteredRuntimeAuthority or authority.workspace_id != workspace_id:
            return None
        _text(runtime_id)
        binding = WorkspacePermissionStore(self.connection).installation_receipt()
        if binding is None or binding.installation_id != capability_policy.installation_id:
            return None
        try:
            current_authority = RuntimeAuthorityStore(self.connection).get_registration_for_update(
                workspace_id, authority.authority_ref, authority.registration_id)
        except RuntimeAuthorityNotFound:
            return None
        if (current_authority.runtime_kind != authority.runtime_kind
                or current_authority.authority != authority.authority):
            return None
        current = self.get(binding.installation_id, workspace_id, runtime_id, authority.authority_ref, for_update=True)
        selected = select_managed_runtime_assignment(installation_id=binding.installation_id,
            workspace_id=workspace_id, runtime_id=runtime_id, authority=current_authority,
            assignment=current, capability_policy=capability_policy)
        if expected is not None and (type(expected) is not ManagedRuntimeSelection or selected != expected):
            return None
        return selected

    def list(self, installation_id, workspace_id, *, limit, cursor):
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ManagedRuntimeAssignmentError("management store page limit is malformed")
        extra = "" if cursor is None else " AND (runtime_id,authority_ref) > (%s,%s)"
        parameters = (installation_id, workspace_id) + (() if cursor is None else
            (cursor.after_runtime_id, cursor.after_authority_ref.reference_id)) + (limit,)
        rows = self.connection.execute("SELECT " + _COLUMNS + " FROM cpk_managed_runtime_assignments "
            "WHERE installation_id=%s AND workspace_id=%s" + extra + " ORDER BY runtime_id,authority_ref LIMIT %s",
            parameters).fetchall()
        return tuple(_record(row) for row in rows)

    def history(self, installation_id, workspace_id, runtime_id, authority_ref, *, limit, cursor):
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ManagedRuntimeAssignmentError("management store page limit is malformed")
        identity = managed_runtime_assignment_id(installation_id, workspace_id, runtime_id, authority_ref)
        rows = self.connection.execute("SELECT revision,idempotency_key,intent_fingerprint,receipt "
            "FROM cpk_managed_runtime_assignment_receipts WHERE assignment_id=%s AND revision>%s "
            "ORDER BY revision LIMIT %s", (identity, 0 if cursor is None else cursor.after_revision, limit)).fetchall()
        result = []
        for row in rows:
            receipt = ManagedRuntimeAssignmentReceipt.from_descriptor(row[3])
            if (receipt.resulting_assignment.assignment_id != identity
                    or receipt.resulting_assignment.revision != row[0] or receipt.idempotency_key != row[1]
                    or _intent_for_receipt(receipt) != row[2]):
                raise ManagedRuntimeAssignmentError("persisted management history disagrees")
            result.append(receipt)
        return tuple(result)


def validate_managed_runtime_assignment_rows(connection):
    """Bounded current/receipt consistency without importing installer credentials."""
    last = ""
    while True:
        rows = connection.execute("SELECT " + _COLUMNS + " FROM cpk_managed_runtime_assignments "
            "WHERE assignment_id>%s ORDER BY assignment_id LIMIT 64", (last,)).fetchall()
        if not rows:
            break
        for row in rows:
            current = _record(row)
            WorkspacePermissionStore(connection).require_installation(current.installation_id)
            authority = connection.execute("SELECT workspace_id,authority_ref,runtime_kind,authority_kind,status "
                "FROM cpk_runtime_authorities WHERE registration_id=%s", (current.registration_id,)).fetchone()
            if (authority is None or tuple(authority[:4]) != (current.workspace_id,
                    current.authority_ref.reference_id, "docker", "local-docker-socket")
                    or (current.status is ManagedRuntimeAssignmentStatus.ACTIVE and authority[4] != "active")):
                raise ManagedRuntimeAssignmentError("management authority and current assignment disagree")
            latest = connection.execute("SELECT receipt FROM cpk_managed_runtime_assignment_receipts "
                "WHERE assignment_id=%s ORDER BY revision DESC LIMIT 1", (current.assignment_id,)).fetchone()
            if latest is None or ManagedRuntimeAssignmentReceipt.from_descriptor(latest[0]).resulting_assignment != current:
                raise ManagedRuntimeAssignmentError("management current revision and history disagree")
        last = rows[-1][0]
    after = ("", 0)
    while True:
        rows = connection.execute("SELECT assignment_id,revision,idempotency_key,intent_fingerprint,receipt "
            "FROM cpk_managed_runtime_assignment_receipts WHERE (assignment_id,revision)>(%s,%s) "
            "ORDER BY assignment_id,revision LIMIT 64", after).fetchall()
        if not rows:
            return
        for row in rows:
            receipt = ManagedRuntimeAssignmentReceipt.from_descriptor(row[4])
            if (receipt.resulting_assignment.assignment_id != row[0] or receipt.resulting_assignment.revision != row[1]
                    or receipt.idempotency_key != row[2] or _intent_for_receipt(receipt) != row[3]):
                raise ManagedRuntimeAssignmentError("persisted management receipt disagrees")
        after = (rows[-1][0], rows[-1][1])
