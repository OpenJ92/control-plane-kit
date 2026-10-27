"""Workspace policy storage bound to the caller's existing transaction."""

from __future__ import annotations

from psycopg.types.json import Jsonb

from control_plane_kit_core.identity import PrincipalIdentity, PrincipalKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp
from control_plane_kit_operations.workspace_permissions import (
    WorkspacePermissionConflict, WorkspacePermissionError, WorkspacePermissionNotFound,
    WorkspacePermissionReceipt, WorkspacePermissionRecord, WorkspacePermissionResult,
    WorkspacePermissionInitializationReceipt, WorkspacePermissionInitializationResult,
    WorkspacePermissionUpgradeReceipt,
    WorkspaceAdministrationReceipt,
    permission_fingerprint, permission_intent, _identity, _text,
)


_KEY = "installation_id=%s AND workspace_id=%s AND issuer=%s AND subject_id=%s AND kind=%s"
_COLUMNS = "installation_id, workspace_id, issuer, subject_id, kind, scopes, revision"


def _parameters(installation_id, workspace_id, target):
    _text(installation_id)
    _text(workspace_id)
    _identity(target)
    return (installation_id, workspace_id, target.issuer, target.subject_id, target.kind.value)


def _decode(row):
    if row is None:
        return None
    try:
        if type(row[5]) is not list:
            raise ValueError
        return WorkspacePermissionRecord(row[0], row[1],
            PrincipalIdentity(row[2], row[3], PrincipalKind(row[4])),
            tuple(PolicyScope(scope) for scope in row[5]), row[6])
    except (TypeError, ValueError, IndexError):
        raise WorkspacePermissionError("persisted workspace permission is malformed") from None


def _receipt(payload):
    try:
        if type(payload) is not dict or set(payload) != {
            "receipt_id", "installation_id", "workspace_id", "actor", "target", "operation",
            "scopes", "resulting_scopes", "prior_revision", "revision", "idempotency_key", "occurred_at",
        }:
            raise ValueError
        values = dict(payload)
        for key in ("actor", "target"):
            identity = values[key]
            if type(identity) is not dict or set(identity) != {"issuer", "subject_id", "kind"}:
                raise ValueError
            values[key] = PrincipalIdentity(identity["issuer"], identity["subject_id"],
                                            PrincipalKind(identity["kind"]))
        if type(values["scopes"]) is not list:
            raise ValueError
        values["scopes"] = tuple(PolicyScope(scope) for scope in values["scopes"])
        if type(values["resulting_scopes"]) is not list:
            raise ValueError
        values["resulting_scopes"] = tuple(PolicyScope(scope) for scope in values["resulting_scopes"])
        return WorkspacePermissionReceipt(**values)
    except (KeyError, TypeError, ValueError):
        raise WorkspacePermissionError("persisted permission receipt is malformed") from None


class WorkspacePermissionStore:
    def __init__(self, connection):
        self.connection = connection

    def initialize(self, context, idempotency_key, *, occurred_at):
        validate_canonical_utc_timestamp(occurred_at)
        candidate = WorkspacePermissionInitializationReceipt(context.installation_id, context.identity,
                                                             idempotency_key.value, occurred_at)
        # One database namespace binds one installation. This lock also serializes
        # initialization with the explicit retained-schema transition.
        self.connection.execute("SELECT pg_advisory_xact_lock(hashtextextended("
                                "current_database() || chr(31) || current_schema(), 0))")
        existing = self.installation_receipt()
        if existing is not None:
            if (type(existing) is not WorkspacePermissionInitializationReceipt
                    or existing.installation_id != candidate.installation_id or existing.actor != candidate.actor
                    or existing.idempotency_key != candidate.idempotency_key):
                raise WorkspacePermissionConflict("installation binding already exists")
            return WorkspacePermissionInitializationResult(existing, replayed=True)
        self.connection.execute(
            "INSERT INTO cpk_workspace_permission_installation(singleton, installation_id, receipt) "
            "VALUES (true, %s, %s)", (context.installation_id, Jsonb(candidate.descriptor())),
        )
        return WorkspacePermissionInitializationResult(candidate)

    def installation_receipt(self):
        row = self.connection.execute(
            "SELECT installation_id, receipt FROM cpk_workspace_permission_installation WHERE singleton"
        ).fetchone()
        if row is None:
            return None
        payload = row[1]
        decoder = (WorkspacePermissionUpgradeReceipt if type(payload) is dict
                   and payload.get("kind") == "retained-workspace-policy-upgrade"
                   else WorkspacePermissionInitializationReceipt)
        receipt = decoder.from_descriptor(payload)
        if receipt.installation_id != row[0]:
            raise WorkspacePermissionError("installation receipt identity disagrees")
        return receipt

    def require_installation(self, installation_id):
        _text(installation_id)
        receipt = self.installation_receipt()
        if receipt is None or receipt.installation_id != installation_id:
            raise WorkspacePermissionConflict("workspace policy installation is unavailable")

    def get(self, installation_id, workspace_id, target, *, for_update=False):
        parameters = _parameters(installation_id, workspace_id, target)
        binding = self.installation_receipt()
        if binding is None or binding.installation_id != installation_id:
            return None
        if type(for_update) is not bool:
            raise WorkspacePermissionError("permission lookup is malformed")
        row = self.connection.execute(
            "SELECT " + _COLUMNS + " FROM cpk_workspace_permissions WHERE " + _KEY
            + (" FOR UPDATE" if for_update else ""), parameters,
        ).fetchone()
        return _decode(row)

    def change(self, command, operation, *, clock, id_factory):
        installation = command.context.installation_id
        self.require_installation(installation)
        parameters = _parameters(installation, command.workspace_id, command.target)
        workspace = self.connection.execute("SELECT workspace_id FROM cpk_workspaces WHERE workspace_id=%s",
                                            (command.workspace_id,)).fetchone()
        if workspace is None:
            raise WorkspacePermissionNotFound("workspace is unavailable")
        if operation == "grant":
            self.connection.execute(
                "INSERT INTO cpk_workspace_permissions (" + _COLUMNS + ") "
                "VALUES (%s,%s,%s,%s,%s,'[]'::jsonb,0) ON CONFLICT DO NOTHING", parameters,
            )
        current = self.get(installation, command.workspace_id, command.target, for_update=True)
        if current is None:
            raise WorkspacePermissionNotFound("workspace permission is unavailable")
        fingerprint = permission_fingerprint(permission_intent(command, operation))
        existing = self.connection.execute(
            "SELECT intent_fingerprint, receipt FROM cpk_workspace_permission_receipts WHERE "
            + _KEY + " AND idempotency_key=%s", parameters + (command.idempotency_key.value,),
        ).fetchone()
        if existing is not None:
            if existing[0] != fingerprint:
                raise WorkspacePermissionConflict("permission command key has different intent")
            receipt = _receipt(existing[1])
            if (receipt.target != command.target or receipt.actor != command.context.identity
                    or receipt.workspace_id != command.workspace_id
                    or receipt.installation_id != installation or receipt.operation != operation
                    or receipt.scopes != command.scopes
                    or receipt.idempotency_key != command.idempotency_key.value
                    or receipt.prior_revision != command.expected_revision
                    or receipt.revision > current.revision):
                raise WorkspacePermissionError("persisted permission receipt disagrees with intent")
            return WorkspacePermissionResult(receipt, replayed=True)
        if current.revision != command.expected_revision:
            raise WorkspacePermissionConflict("workspace permission revision changed")
        scopes = set(current.scopes)
        if operation == "grant":
            scopes.update(command.scopes)
        elif operation == "revoke":
            scopes.difference_update(command.scopes)
        else:
            raise WorkspacePermissionError("permission operation is malformed")
        receipt = WorkspacePermissionReceipt(id_factory(), installation, command.workspace_id,
            command.context.identity, command.target, operation, command.scopes, tuple(scopes), current.revision,
            current.revision + 1, command.idempotency_key.value, clock())
        self.connection.execute(
            "UPDATE cpk_workspace_permissions SET scopes=%s, revision=%s WHERE " + _KEY,
            (Jsonb(sorted(scope.value for scope in scopes)), receipt.revision) + parameters,
        )
        self.connection.execute(
            "INSERT INTO cpk_workspace_permission_receipts "
            "(installation_id,workspace_id,issuer,subject_id,kind,idempotency_key,revision,"
            "intent_fingerprint,receipt) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            parameters + (command.idempotency_key.value, receipt.revision, fingerprint,
                          Jsonb(receipt.descriptor())),
        )
        return WorkspacePermissionResult(receipt)

    def lock_administration_command(self, context, idempotency_key, fingerprint):
        self.require_installation(context.installation_id)
        identity = context.identity
        key = permission_fingerprint(dict(installation_id=context.installation_id,
            actor=identity.descriptor(), idempotency_key=idempotency_key.value))
        # The command key precedes lifecycle locks, including when two requests
        # reuse one key for different workspace IDs and no receipt yet exists.
        self.connection.execute("SELECT pg_advisory_xact_lock(hashtextextended("
            "current_database() || chr(31) || current_schema() || chr(31) || %s, 0))",
            ("workspace-administration:" + key,))
        row = self.connection.execute(
            "SELECT intent_fingerprint, receipt, workspace_id FROM cpk_workspace_administration_receipts "
            "WHERE installation_id=%s AND issuer=%s AND subject_id=%s AND kind=%s AND idempotency_key=%s",
            (context.installation_id, identity.issuer, identity.subject_id, identity.kind.value, idempotency_key.value),
        ).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise WorkspacePermissionConflict("administrative command key has different intent")
        receipt = WorkspaceAdministrationReceipt.from_descriptor(row[1])
        if (receipt.installation_id != context.installation_id or receipt.actor != identity
                or receipt.idempotency_key != idempotency_key.value or receipt.workspace_id != row[2]):
            raise WorkspacePermissionError("administrative receipt identity disagrees")
        return receipt

    def save_administration_receipt(self, receipt, fingerprint):
        self.connection.execute(
            "INSERT INTO cpk_workspace_administration_receipts "
            "(installation_id,issuer,subject_id,kind,idempotency_key,workspace_id,intent_fingerprint,receipt) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (receipt.installation_id, receipt.actor.issuer, receipt.actor.subject_id, receipt.actor.kind.value,
             receipt.idempotency_key, receipt.workspace_id, fingerprint, Jsonb(receipt.descriptor())),
        )

    def list(self, installation_id, workspace_id, *, limit, after=None):
        _text(installation_id)
        _text(workspace_id)
        if type(limit) is not int or not 1 <= limit <= 101:
            raise WorkspacePermissionError("permission page bound is malformed")
        parameters = (installation_id, workspace_id)
        suffix = ""
        if after is not None:
            _identity(after)
            suffix = " AND (issuer,subject_id,kind) > (%s,%s,%s)"
            parameters += (after.issuer, after.subject_id, after.kind.value)
        rows = self.connection.execute("SELECT " + _COLUMNS + " FROM cpk_workspace_permissions "
            "WHERE installation_id=%s AND workspace_id=%s" + suffix
            + " ORDER BY issuer,subject_id,kind LIMIT %s", parameters + (limit,)).fetchall()
        return tuple(_decode(row) for row in rows)

    def history(self, installation_id, workspace_id, target, *, limit, after=0):
        parameters = _parameters(installation_id, workspace_id, target)
        if (type(limit) is not int or not 1 <= limit <= 101
                or type(after) is not int or not 0 <= after < 9_223_372_036_854_775_807):
            raise WorkspacePermissionError("permission history bound is malformed")
        rows = self.connection.execute("SELECT revision,idempotency_key,receipt "
            "FROM cpk_workspace_permission_receipts WHERE " + _KEY
            + " AND revision>%s ORDER BY revision LIMIT %s", parameters + (after, limit)).fetchall()
        results = []
        for row in rows:
            receipt = _receipt(row[2])
            if (receipt.installation_id != installation_id or receipt.workspace_id != workspace_id
                    or receipt.target != target or receipt.revision != row[0] or receipt.idempotency_key != row[1]):
                raise WorkspacePermissionError("permission history identity disagrees")
            results.append(receipt)
        return tuple(results)


def validate_workspace_permission_rows(connection):
    """Check bounded stored values and the current row's original latest receipt."""
    WorkspacePermissionStore(connection).installation_receipt()
    for rows in _batches(connection, "cpk_workspace_permissions", _COLUMNS,
                         ("installation_id", "workspace_id", "issuer", "subject_id", "kind")):
        for row in rows:
            current = _decode(row)
            latest = connection.execute(
                "SELECT receipt FROM cpk_workspace_permission_receipts WHERE " + _KEY
                + " AND revision=%s", tuple(row[:5]) + (current.revision,),
            ).fetchone()
            if latest is None or current.revision == 0:
                raise WorkspacePermissionError("permission revision has no original receipt")
            receipt = _receipt(latest[0])
            if (receipt.installation_id != current.installation_id
                    or receipt.workspace_id != current.workspace_id or receipt.target != current.target
                    or receipt.revision != current.revision or receipt.resulting_scopes != current.scopes):
                raise WorkspacePermissionError("permission state disagrees with original receipt")
    keys = ("installation_id", "workspace_id", "issuer", "subject_id", "kind", "idempotency_key")
    for rows in _batches(connection, "cpk_workspace_permission_receipts",
                         ",".join(keys) + ",revision,intent_fingerprint,receipt", keys):
        for row in rows:
            receipt = _receipt(row[8])
            if (tuple(row[:6]) != (receipt.installation_id, receipt.workspace_id, receipt.target.issuer,
                                  receipt.target.subject_id, receipt.target.kind.value, receipt.idempotency_key)
                    or row[6] != receipt.revision):
                raise WorkspacePermissionError("permission receipt identity disagrees")
            intent = dict(installation_id=receipt.installation_id, workspace_id=receipt.workspace_id,
                actor=receipt.actor.descriptor(), target=receipt.target.descriptor(), operation=receipt.operation,
                scopes=[scope.value for scope in receipt.scopes], expected_revision=receipt.prior_revision,
                idempotency_key=receipt.idempotency_key)
            if row[7] != permission_fingerprint(intent):
                raise WorkspacePermissionError("permission receipt intent disagrees")
    keys = ("installation_id", "issuer", "subject_id", "kind", "idempotency_key")
    for rows in _batches(connection, "cpk_workspace_administration_receipts",
                        ",".join(keys) + ",workspace_id,receipt", keys):
        for row in rows:
            receipt = WorkspaceAdministrationReceipt.from_descriptor(row[6])
            if tuple(row[:6]) != (receipt.installation_id, receipt.actor.issuer, receipt.actor.subject_id,
                                 receipt.actor.kind.value, receipt.idempotency_key, receipt.workspace_id):
                raise WorkspacePermissionError("administration receipt identity disagrees")


def _batches(connection, table, columns, keys):
    last = None
    while True:
        where = "" if last is None else " WHERE (" + ",".join(keys) + ") > (" + ",".join("%s" for _ in keys) + ")"
        rows = connection.execute("SELECT " + columns + " FROM " + table + where
                                  + " ORDER BY " + ",".join(keys) + " LIMIT 64", last or ()).fetchall()
        if not rows:
            return
        yield rows
        last = tuple(rows[-1][:len(keys)])
