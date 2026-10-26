"""Postgres store for execution admission and run ownership."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    FailureCategory,
    RecoveryDecisionKind,
)
from control_plane_kit_core.operations import RunId
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.effect_permissions import ExecutionCommandReference
from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationRecord, ExecutionInvocationDisposition, ExecutionInvocationOutcome,
    ExecutionInvocationConflict, ExecutionInvocationKind, secret_invocation_target_fingerprint,
    ExecutionInvocationCursor, ExecutionInvocationPage,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.postgres.temporal import (
    decode_postgres_cursor_timestamp,
    decode_postgres_timestamp,
    encode_postgres_cursor_timestamp,
    encode_postgres_timestamp,
)
from control_plane_kit_operations.read_pages import (
    OrdinalReadCursor,
    ReadCollection,
    ReadPage,
    ReadPageCandidate,
    ReadPageError,
    ReadPageRequest,
    TemporalReadCursor,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityRunRecord,
    AdmittedRun,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    CoordinatorStatus,
    ExecutionCommandReceiptRecord,
    ExecutionCommandReceiptStatus,
    ExecutionCommandResultRecord,
    ExecutionLeaseRecoveryEvidence,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    FailureEvidence,
    ManagedExecutionCommandIntent,
    OperationsRecordError,
    RetryIdentity,
    canonical_positive_decimal,
    positive_int_from_canonical_decimal,
)


@dataclass(frozen=True)
class _ExecutionLeaseObservation:
    request: ExecutionRequestRecord
    observed_at: str
    expired: bool


class PostgresExecutionStore:
    """Postgres-backed execution request store."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def _configuration_request(self, read, request_id, *, for_update=False):
        from .receiver_execution_scopes import _Transport, _REQUEST, _columns, _decode
        if for_update:
            read.query("SELECT 1 FROM cpk_execution_requests WHERE request_id=%s FOR UPDATE",
                (request_id,), records=1, octets=1, cells=1)
        rows = _Transport(self._connection, read).read("cpk_execution_requests", _columns(_REQUEST),
            "request_id=%s", (request_id,), point=True, phase=("request", (request_id,)))
        if not rows:
            raise KeyError("missing execution request")
        return _execution_request(_decode(rows[0], _REQUEST, int_columns=("claim_generation",),
            time_columns=("requested_at", "claimed_at", "lease_expires_at")))

    def _configuration_run(self, read, where, params, *, for_update=False, order=""):
        from .receiver_execution_scopes import _Transport, _RUN, _columns, _decode
        if for_update:
            suffix = " ORDER BY " + order if order else ""
            read.query("SELECT 1 FROM cpk_activity_runs WHERE " + where + suffix + " LIMIT 1 FOR UPDATE",
                params, records=1, octets=1, cells=1)
        rows = _Transport(self._connection, read).read("cpk_activity_runs",
            _columns(_RUN, json_columns=("metadata",), ceilings={"metadata": 65536}),
            where, params, order=order, point=True,
            phase=("run", params) if where == "run_id=%s" else None)
        if not rows:
            raise KeyError("missing activity run")
        return _activity_run(_decode(rows[0], _RUN, json_columns=("metadata",),
            int_columns=("attempt",), time_columns=("created_at", "started_at", "settled_at")))

    def add_request(self, record: ExecutionRequestRecord) -> ExecutionRequestRecord:
        """Direct history insertion is restricted to proved empty footprints."""
        from .receiver_execution_scopes import _ExecutionScopeStorage
        from control_plane_kit_operations.receiver_execution_scopes import ReceiverScopeUnavailable
        storage = _ExecutionScopeStorage(self._connection)
        derived = storage.derive(record.identity)
        if derived.scopes:
            raise ReceiverScopeUnavailable("receiver scope evidence is unavailable")
        return self._insert_scoped_request(record, storage, derived)

    def _admit_request(self, record, *, lifecycle_guard):
        """Private semantic-admission coupling; a held guard alone is no grant."""
        from .receiver_execution_scopes import _ExecutionScopeStorage
        storage = _ExecutionScopeStorage(self._connection)
        storage.guard(record.identity.workspace_id, lifecycle_guard)
        derived = storage.derive(record.identity)
        return self._insert_scoped_request(record, storage, derived)

    def _insert_scoped_request(self, record, storage, derived):
        claim = record.claim
        self._connection.execute(
            """
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint,
               claim_worker_id, claim_generation, claimed_at, lease_expires_at,
               receiver_scope_count, receiver_scope_digest)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.identity.request_id,
                record.identity.workspace_id,
                record.identity.session_id,
                record.identity.plan_id,
                record.status.value,
                record.requested_by,
                encode_postgres_timestamp(record.requested_at),
                record.approval_request_id,
                record.approval_decision_id,
                record.idempotency.key,
                record.idempotency.intent_fingerprint,
                None if claim is None else claim.worker_id,
                None if claim is None else claim.generation,
                None
                if claim is None
                else encode_postgres_timestamp(claim.claimed_at),
                None
                if claim is None
                else encode_postgres_timestamp(claim.lease_expires_at),
                len(derived.scopes),
                derived.source_digest,
            ),
        )
        storage.persist(record.identity, derived)
        return record

    def receiver_scope_evidence(self, workspace_id, requested_scopes, guard):
        """Bounded internal history accounting under the existing workspace L."""
        from .receiver_execution_scopes import read_receiver_scope_evidence
        return read_receiver_scope_evidence(self._connection, workspace_id, requested_scopes, guard)

    def _receiver_execution_material(self, identity, guard):
        from .receiver_execution_scopes import _ExecutionScopeStorage
        from .configuration_evidence import _active_read
        storage = _ExecutionScopeStorage(self._connection, _active_read(self._connection))
        storage.guard(identity.workspace_id, guard)
        return storage.verify(identity)

    def _require_empty_receiver_scope(self, request_id):
        from .receiver_execution_scopes import _ExecutionScopeStorage
        from control_plane_kit_operations.receiver_execution_scopes import ReceiverScopeUnavailable
        request = self.get_request(request_id)
        _, derived = _ExecutionScopeStorage(self._connection).verify(request.identity)
        if derived.scopes:
            raise ReceiverScopeUnavailable("receiver scope evidence is unavailable")

    def _receiver_acceptance_evidence(self, origins):
        """Read original acceptance facts; current membership is graph-owned.

        One bounded C1 reader accounts for the entire requested receipt set.
        This neither locks requests/runs nor mutates or renews any permission.
        """
        from dataclasses import replace
        from .receiver_execution_scopes import _ExecutionScopeStorage, _ACTION, _columns, _decode
        from .activity_history import _action_record
        from .receiver_lifecycle_store import _BIND_COLUMNS
        from .temporal import decode_postgres_cursor_timestamp
        from control_plane_kit_operations.advancement import _require_complete_success
        from control_plane_kit_operations.revision_history import historical_advancement
        from control_plane_kit_operations.receiver_lifecycle import (
            ReceiverBinding, ReceiverLifecycleStorageError, _require, _receiver_scope, _text,
            derive_receiver_bindings,
        )

        from .configuration_evidence import _active_read
        reader = _ExecutionScopeStorage(self._connection, _active_read(self._connection))
        receipts, requests, result = {}, {}, []
        try:
            for origin in origins:
                key = (origin.workspace_id, origin.first_accepted_session_id, origin.first_accepted_action_id)
                _require(all(type(value) is str and value for value in key))
                if key not in receipts:
                    rows = reader.transport.read("cpk_operation_actions", _columns(_ACTION,
                        json_columns=("payload",), ceilings={"payload": 65536}),
                        "action_id=%s AND session_id=%s", (key[2], key[1]), point=True, cache=True,
                        phase=("acceptance-action", (key[2], key[1])))
                    _require(len(rows) == 1)
                    action = _action_record(_decode(rows[0], _ACTION, json_columns=("payload",),
                        int_columns=("ordinal",), time_columns=("created_at",)))
                    payload = action.payload
                    request_id, run_id = payload["execution_request_id"], payload["run_id"]
                    from .configuration_cleanup_phase_read_bounds import _phase_require
                    for role, child in (("request", (request_id,)), ("run", (run_id,)),
                            ("plan", (payload["plan_id"],)), ("runs", (request_id,)),
                            ("events", (run_id,)), ("advancement-actions", (action.session_id, run_id))):
                        _phase_require(self._connection, "acceptance-action", (key[2], key[1]), role, child)
                    for locator in (request_id, run_id, payload["plan_id"]):
                        _text(locator)
                    request_key = (origin.workspace_id, request_id)
                    if request_key not in requests:
                        request = reader.request(*request_key)
                        original, _ = reader.verify(request.identity)
                        requests[request_key] = request, original, reader.runs(request)
                    request, (plan, base, desired), runs = requests[request_key]
                    _require(request.identity.session_id == action.session_id == origin.first_accepted_session_id
                             and request.identity.plan_id == plan.plan_id == payload["plan_id"])
                    matching = tuple(item for item in runs if item.run_id == run_id)
                    _require(len(matching) == 1)
                    run = matching[0]
                    events = reader.events(run_id)
                    _require(tuple(event.ordinal for event in events) == tuple(range(1, len(events) + 1))
                             and len({event.event_id for event in events}) == len(events)
                             and all(event.run_id == run_id for event in events))
                    accepted = tuple(event for event in events if event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED)
                    actions = reader.actions(action.session_id, run_id, "advance-current-graph")
                    _require(len(accepted) == len(actions) == 1 and actions[0] == action)
                    event = accepted[0]
                    normalized_event = replace(event, occurred_at=decode_postgres_cursor_timestamp(
                        encode_postgres_timestamp(event.occurred_at)))
                    normalized_action = replace(action, created_at=decode_postgres_cursor_timestamp(
                        encode_postgres_timestamp(action.created_at)))
                    receipt = historical_advancement(workspace_id=origin.workspace_id,
                        session_id=action.session_id, plan_id=plan.plan_id,
                        plan=dict(base_graph_id=plan.base_graph_id, base_realized_projection_id=base.projection_id,
                            desired_graph_id=plan.desired_graph_id, desired_realized_projection_id=desired.projection_id,
                            desired_graph_revision=plan.desired_graph_revision),
                        request_id=request_id, run_id=run_id, projection_digest=desired.projection_digest,
                        events=(normalized_event,), actions=(normalized_action,))
                    _require(receipt["state"] == "accepted")
                    _require_complete_success(plan.plan, run,
                        tuple(item for item in events if item.ordinal < event.ordinal))
                    expected = derive_receiver_bindings(origin.workspace_id, desired.source_authored_graph_id,
                        desired.projection_id, desired.graph_descriptor)
                    _require(bool(expected))
                    rows = reader.transport.read("cpk_graph_receiver_bindings", _columns(_BIND_COLUMNS),
                        "workspace_id=%s AND graph_id=%s AND realized_projection_id=%s",
                        (origin.workspace_id, desired.source_authored_graph_id, desired.projection_id),
                        order="node_id,provider_socket_name", maximum=len(expected), cache=True,
                        phase=("bindings", (origin.workspace_id, desired.source_authored_graph_id, desired.projection_id)))
                    actual = tuple(ReceiverBinding(*_decode(row, _BIND_COLUMNS)) for row in rows)
                    _require(actual == expected)
                    from control_plane_kit_operations._configuration_acceptance import _PUBLICATION_SCOPE
                    publication = _PUBLICATION_SCOPE.get()
                    if publication is not None:
                        publication._observe_receiver_receipt(reader.transport.configuration_read, action, request, run, desired)
                    receipts[key] = (action, event, plan, run, desired, actual)
                facts = receipts[key]
                _require(any(_receiver_scope(binding) == _receiver_scope(origin) for binding in facts[-1]))
                result.append(facts)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError):
            raise ReceiverLifecycleStorageError("receiver acceptance evidence is unavailable") from None
        return tuple(result)

    def lock_admission_idempotency(
        self,
        workspace_id: str,
        idempotency_key: str,
    ) -> None:
        """Serialize execution admission before the request row exists."""

        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"execution-admission:{workspace_id}:{idempotency_key}",),
        )

    def add_command_invocation(self, record: ExecutionInvocationRecord) -> ExecutionInvocationRecord:
        """Retain an owner-decided row; caller owns locks, decision and commit."""
        from psycopg import IntegrityError
        _invocation_record(record)
        _require_invocation_owners(self, record, fresh=True)
        try:
            self._connection.execute("INSERT INTO cpk_execution_command_invocations ("
                + ", ".join(_INVOCATION_COLUMNS) + ") VALUES ("
                + ", ".join("%s" for _ in _INVOCATION_COLUMNS) + ")", _invocation_values(record))
        except IntegrityError:
            raise ExecutionInvocationConflict(record.invocation_id) from None
        return record

    def command_invocation(self, command: ExecutionCommandReference, invocation_id: str, *, for_update=False):
        _invocation_reference(command)
        _invocation_id(invocation_id)
        if type(for_update) is not bool:
            raise ExecutionInvocationConflict()
        row = self._connection.execute(_INVOCATION_SELECT
            + " WHERE run_id=%s AND command_key=%s AND invocation_id=%s"
            + (" FOR UPDATE" if for_update else ""),
            (command.run_id, command.idempotency_key, invocation_id)).fetchone()
        if row is None:
            return None
        record = _invocation_row(row)
        if record.command != command:
            raise ExecutionInvocationConflict(invocation_id)
        _require_invocation_owners(self, record)
        return record

    def command_invocation_for_slot(self, command: ExecutionCommandReference, *,
            activity_id, kind, authorization_id=None):
        """Non-locking exact unique-slot lookup; no new admission or repair."""
        import re
        _invocation_reference(command)
        if (type(activity_id) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", activity_id) is None
                or type(kind) is not ExecutionInvocationKind):
            raise ExecutionInvocationConflict()
        if kind is ExecutionInvocationKind.SECRET_RESOLUTION:
            if (type(authorization_id) is not str
                    or re.fullmatch(r"suse_[0-9a-f]{64}", authorization_id) is None):
                raise ExecutionInvocationConflict()
            slot = authorization_id
        else:
            if authorization_id is not None:
                raise ExecutionInvocationConflict()
            slot = "singleton"
        parent = self.command_receipt_for_idempotency(command.run_id, command.idempotency_key)
        if parent is None or parent.intent_fingerprint != command.intent_fingerprint:
            raise ExecutionInvocationConflict()
        row = self._connection.execute(_INVOCATION_SELECT
            + " WHERE run_id=%s AND command_key=%s AND activity_id=%s AND kind=%s AND slot_key=%s",
            (command.run_id, command.idempotency_key, activity_id, kind.value, slot)).fetchone()
        if row is None:
            return None
        record = _invocation_row(row)
        if record.command != command:
            raise ExecutionInvocationConflict(record.invocation_id)
        _require_invocation_owners(self, record)
        return record

    def original_final_invocation(self, identity, *, original_event_id, request_fingerprint, kind):
        """Bounded original candidates; malformed history never becomes absence."""
        from control_plane_kit_core.operations import EffectAttemptIdentity
        if (type(identity) is not EffectAttemptIdentity or kind not in (
                ExecutionInvocationKind.SIGNED_HEALTH_TRANSPORT, ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
                or type(kind) is not ExecutionInvocationKind):
            raise ExecutionInvocationConflict()
        try:
            EffectAttemptIdentity(identity.run_id, identity.activity_id, identity.attempt)
            _invocation_id(request_fingerprint)
            if type(original_event_id) is not str or not original_event_id or len(original_event_id) > 512:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ExecutionInvocationConflict() from None
        rows = self._connection.execute(_INVOCATION_SELECT
            + " WHERE run_id=%s AND activity_id=%s AND attempt=%s AND kind=%s LIMIT 2",
            (identity.run_id.value, identity.activity_id, identity.attempt, kind.value)).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise ExecutionInvocationConflict()
        record = _invocation_row(rows[0])
        if (record.original_event_id != original_event_id
                or record.request_fingerprint != request_fingerprint):
            raise ExecutionInvocationConflict(record.invocation_id)
        _require_invocation_owners(self, record)
        return record

    def replace_command_invocation(self, expected: ExecutionInvocationRecord,
            replacement: ExecutionInvocationRecord) -> bool:
        """CAS one planned decision or pending classification; never a permit."""
        _invocation_record(expected)
        _invocation_record(replacement)
        try:
            if expected.disposition is ExecutionInvocationDisposition.PLANNED:
                valid = expected.decided(replacement.decision, replacement.decision_at)
            elif expected.outcome is ExecutionInvocationOutcome.PENDING:
                valid = expected.completed(replacement.outcome, replacement.completed_at,
                    code=replacement.completion_code)
            else:
                valid = None
        except (OperationsRecordError, ValueError, TypeError):
            valid = None
        if valid != replacement:
            raise ExecutionInvocationConflict(expected.invocation_id)
        current = self.command_invocation(expected.command, expected.invocation_id, for_update=True)
        if current == replacement:
            return False
        if current != expected:
            raise ExecutionInvocationConflict(expected.invocation_id)
        _require_invocation_owners(self, replacement,
            fresh=expected.disposition is ExecutionInvocationDisposition.PLANNED)
        from psycopg.types.json import Jsonb
        row = self._connection.execute("UPDATE cpk_execution_command_invocations SET "
            "disposition=%s, decision_at=%s, outcome=%s, completed_at=%s, descriptor=%s "
            "WHERE invocation_id=%s AND descriptor=%s RETURNING invocation_id",
            (replacement.disposition.value, _encode_optional_timestamp(replacement.decision_at),
                None if replacement.outcome is None else replacement.outcome.value,
                _encode_optional_timestamp(replacement.completed_at), Jsonb(replacement.descriptor()),
                expected.invocation_id, Jsonb(expected.descriptor()))).fetchone()
        if row is not None:
            if row != (replacement.invocation_id,):
                raise ExecutionInvocationConflict(expected.invocation_id)
            return True
        if self.command_invocation(expected.command, expected.invocation_id) != replacement:
            raise ExecutionInvocationConflict(expected.invocation_id)
        return False

    def command_invocations(self, command: ExecutionCommandReference, *, limit=100, cursor=None):
        _invocation_reference(command)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ExecutionInvocationConflict()
        if cursor is not None:
            if type(cursor) is not ExecutionInvocationCursor or cursor.command != command:
                raise ExecutionInvocationConflict()
            cursor.__post_init__()
        parent = self.command_receipt_for_idempotency(command.run_id, command.idempotency_key)
        if parent is None or parent.intent_fingerprint != command.intent_fingerprint or parent.managed_intent is None:
            raise ExecutionInvocationConflict()
        parameters = (command.run_id, command.idempotency_key)
        condition = " WHERE run_id=%s AND command_key=%s"
        if cursor is not None:
            condition += ' AND invocation_id COLLATE "C">%s'
            parameters += (cursor.after_invocation_id,)
        rows = self._connection.execute(_INVOCATION_SELECT + condition
            + ' ORDER BY invocation_id COLLATE "C" LIMIT %s', parameters + (limit + 1,)).fetchall()
        records = tuple(_invocation_row(row) for row in rows)
        for record in records:
            if record.command != command:
                raise ExecutionInvocationConflict(record.invocation_id)
            _require_invocation_owners(self, record)
        items = records[:limit]
        next_cursor = (ExecutionInvocationCursor(command, items[-1].invocation_id)
            if len(records) > limit else None)
        return ExecutionInvocationPage(command, items, next_cursor)

    def lock_command_idempotency(
        self,
        run_id: str,
        idempotency_key: str,
    ) -> None:
        """Serialize one run-scoped coordinator command before receipt lookup."""

        _require_run_id(run_id)
        _require_command_key(idempotency_key)
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            read.query("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"execution-command:{run_id}:{idempotency_key}",), records=1, octets=1, cells=1)
            return
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"execution-command:{run_id}:{idempotency_key}",),
        )

    def add_command_receipt(
        self,
        record: ExecutionCommandReceiptRecord,
    ) -> ExecutionCommandReceiptRecord:
        if type(record) is not ExecutionCommandReceiptRecord:
            raise OperationsRecordError("execution command receipt must be typed")
        sql = """
            INSERT INTO cpk_execution_command_receipts
              (run_id, idempotency_key, intent_fingerprint, worker_id,
               authority_scopes, claim_generation, max_effects, admitted_at,
               initial_run, receipt_status, completed_at, result, managed_intent)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb,
                    %s, %s, %s::jsonb, %s::jsonb)
            """
        params = (
            record.run_id,
            record.idempotency_key,
            record.intent_fingerprint,
            record.worker_id,
            _json([scope.value for scope in record.authority_scopes]),
            record.claim_generation,
            canonical_positive_decimal(record.max_effects),
            encode_postgres_timestamp(record.admitted_at),
            _json(_run_descriptor(record.initial_run)),
            record.status.value,
            _encode_optional_timestamp(record.completed_at),
            None if record.result is None else _json(_result_descriptor(record.result)),
            None if record.managed_intent is None else _json(record.managed_intent.descriptor()),
        )
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            rows = read.query(sql + " RETURNING 1", params, records=1, octets=1, cells=1)
            if rows != [(1,)]:
                raise OperationsRecordError("execution command receipt is unavailable")
        else:
            self._connection.execute(sql, params)
        return record

    def command_receipt_for_idempotency(
        self,
        run_id: str,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ) -> ExecutionCommandReceiptRecord | None:
        _require_run_id(run_id)
        _require_command_key(idempotency_key)
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            params = (run_id, idempotency_key)
            where = "run_id=%s AND idempotency_key=%s"
            if for_update:
                read.query("SELECT 1 FROM cpk_execution_command_receipts WHERE " + where + " FOR UPDATE",
                    params, records=1, octets=1, cells=1)
            names = ("run_id", "idempotency_key", "intent_fingerprint", "worker_id", "authority_scopes",
                "claim_generation", "max_effects", "admitted_at", "initial_run", "receipt_status", "completed_at", "result", "managed_intent")
            columns = tuple((name, "json" if name in ("authority_scopes", "initial_run", "result", "managed_intent")
                else "time" if name in ("admitted_at", "completed_at") else "int" if name == "claim_generation"
                else "text", 65536) for name in names)
            rows = read.bounded_rows("cpk_execution_command_receipts", columns, where, params)
            return _command_receipt(rows[0]) if rows else None
        lock = "FOR UPDATE" if for_update else ""
        row = self._connection.execute(
            f"""
            SELECT run_id, idempotency_key, intent_fingerprint, worker_id,
                   authority_scopes, claim_generation, max_effects, admitted_at,
                   initial_run, receipt_status, completed_at, result, managed_intent
            FROM cpk_execution_command_receipts
            WHERE run_id = %s AND idempotency_key = %s
            {lock}
            """,
            (run_id, idempotency_key),
        ).fetchone()
        return None if row is None else _command_receipt(row)

    def complete_command_receipt(
        self,
        run_id: str,
        idempotency_key: str,
        *,
        intent_fingerprint: str,
        completed_at: str,
        result: ExecutionCommandResultRecord,
    ) -> ExecutionCommandReceiptRecord | None:
        _require_run_id(run_id)
        _require_command_key(idempotency_key)
        if type(result) is not ExecutionCommandResultRecord:
            raise OperationsRecordError("execution command result must be typed")
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            names = ("run_id", "idempotency_key", "intent_fingerprint", "worker_id", "authority_scopes",
                "claim_generation", "max_effects", "admitted_at", "initial_run", "receipt_status", "completed_at", "result", "managed_intent")
            valid = " AND ".join(f"({name} IS NULL OR octet_length({name}::text)<=65536)" for name in names)
            projection = ",".join(f"CASE WHEN {valid} THEN {name}::text END" for name in names)
            rows = read.query("WITH updated AS (UPDATE cpk_execution_command_receipts "
                "SET receipt_status='completed', completed_at=%s, result=%s::jsonb "
                "WHERE run_id=%s AND idempotency_key=%s AND intent_fingerprint=%s "
                "AND receipt_status='incomplete' AND completed_at IS NULL AND result IS NULL "
                "RETURNING *) SELECT " + projection + f",({valid}) FROM updated",
                (encode_postgres_timestamp(completed_at), _json(_result_descriptor(result)),
                    run_id, idempotency_key, intent_fingerprint), records=1, octets=13 * 65536 + 1, cells=14)
            if not rows:
                return None
            if rows[0][-1] is not True:
                raise OperationsRecordError("execution command receipt is unavailable")
            values = list(rows[0][:-1])
            for index in (4, 8, 11, 12):
                values[index] = None if values[index] is None else json.loads(values[index])
            for index in (7, 10):
                values[index] = None if values[index] is None else datetime.fromisoformat(values[index])
            values[5] = int(values[5])
            return _command_receipt(tuple(values))
        row = self._connection.execute(
            """
            UPDATE cpk_execution_command_receipts
            SET receipt_status = 'completed', completed_at = %s,
                result = %s::jsonb
            WHERE run_id = %s
              AND idempotency_key = %s
              AND intent_fingerprint = %s
              AND receipt_status = 'incomplete'
              AND completed_at IS NULL
              AND result IS NULL
            RETURNING run_id, idempotency_key, intent_fingerprint, worker_id,
                      authority_scopes, claim_generation, max_effects, admitted_at,
                      initial_run, receipt_status, completed_at, result, managed_intent
            """,
            (
                encode_postgres_timestamp(completed_at),
                _json(_result_descriptor(result)),
                run_id,
                idempotency_key,
                intent_fingerprint,
            ),
        ).fetchone()
        return None if row is None else _command_receipt(row)

    def get_request(self, request_id: str) -> ExecutionRequestRecord:
        from .configuration_cleanup_phase_read_bounds import _phase_context
        _phase_context(self._connection)
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            return self._configuration_request(read, request_id)
        row = self._connection.execute(
            """
            SELECT request_id, workspace_id, session_id, plan_id, status,
                   requested_by, requested_at, approval_request_id,
                   approval_decision_id, idempotency_key, intent_fingerprint,
                   claim_worker_id, claim_generation, claimed_at, lease_expires_at
            FROM cpk_execution_requests
            WHERE request_id = %s
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing execution request {request_id!r}")
        return _execution_request(row)

    def request_for_idempotency(
        self,
        workspace_id: str,
        idempotency_key: str,
    ) -> ExecutionRequestRecord | None:
        row = self._connection.execute(
            """
            SELECT request_id, workspace_id, session_id, plan_id, status,
                   requested_by, requested_at, approval_request_id,
                   approval_decision_id, idempotency_key, intent_fingerprint,
                   claim_worker_id, claim_generation, claimed_at, lease_expires_at
            FROM cpk_execution_requests
            WHERE workspace_id = %s AND idempotency_key = %s
            """,
            (workspace_id, idempotency_key),
        ).fetchone()
        return None if row is None else _execution_request(row)

    def claim_request(
        self,
        request_id: str,
        worker_id: str,
        lease_duration_seconds: int,
    ) -> ExecutionRequestRecord | None:
        if (type(lease_duration_seconds) is not int
                or not 1 <= lease_duration_seconds <= 3600):
            raise OperationsRecordError("lease duration is invalid")
        self._require_empty_receiver_scope(request_id)
        return self._claim_request(request_id, worker_id, lease_duration_seconds)

    def _claim_request(self, request_id, worker_id, lease_duration_seconds):
        if (
            type(lease_duration_seconds) is not int
            or not 1 <= lease_duration_seconds <= 3600
        ):
            raise OperationsRecordError("lease duration is invalid")
        row = self._connection.execute(
            """
            SELECT request_id, workspace_id, session_id, plan_id, status,
                   requested_by, requested_at, approval_request_id,
                   approval_decision_id, idempotency_key, intent_fingerprint,
                   claim_worker_id, claim_generation, claimed_at, lease_expires_at
            FROM cpk_execution_requests
            WHERE request_id = %s
            FOR UPDATE
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing execution request {request_id!r}")
        current = _execution_request(row)
        if current.status is not ExecutionRequestStatus.QUEUED:
            return None
        updated = self._connection.execute(
            """
            WITH observed AS (
              SELECT clock_timestamp() AS observed_at
            )
            UPDATE cpk_execution_requests
            SET status = 'claimed', claim_worker_id = %s,
                claim_generation = 1,
                claimed_at = observed.observed_at,
                lease_expires_at = observed.observed_at
                  + (%s * interval '1 second')
            FROM observed
            WHERE request_id = %s AND status = 'queued'
            RETURNING request_id, workspace_id, session_id, plan_id, status,
                      requested_by, requested_at, approval_request_id,
                      approval_decision_id, idempotency_key, intent_fingerprint,
                      claim_worker_id, claim_generation, claimed_at,
                      lease_expires_at
            """,
            (
                worker_id,
                lease_duration_seconds,
                request_id,
            ),
        ).fetchone()
        return None if updated is None else _execution_request(updated)

    def get_request_for_update(self, request_id: str) -> ExecutionRequestRecord:
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            return self._configuration_request(read, request_id, for_update=True)
        row = self._connection.execute(
            """
            SELECT request_id, workspace_id, session_id, plan_id, status,
                   requested_by, requested_at, approval_request_id,
                   approval_decision_id, idempotency_key, intent_fingerprint,
                   claim_worker_id, claim_generation, claimed_at,
                   lease_expires_at
            FROM cpk_execution_requests
            WHERE request_id = %s
            FOR UPDATE
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError("missing execution request")
        return _execution_request(row)

    def observe_request_lease_for_update(
        self,
        request_id: str,
    ) -> _ExecutionLeaseObservation:
        request = self.get_request_for_update(request_id)
        if request.claim is None:
            raise OperationsRecordError(
                "execution request does not have an active lease"
            )
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            from datetime import datetime
            rows = read.query("WITH observed AS (SELECT clock_timestamp() AS at) "
                "SELECT observed.at::text, request.lease_expires_at <= observed.at "
                "FROM cpk_execution_requests request CROSS JOIN observed WHERE request.request_id=%s",
                (request_id,), records=1, octets=65, cells=2)
            if not rows:
                raise KeyError("missing execution request")
            return _ExecutionLeaseObservation(request, decode_postgres_timestamp(datetime.fromisoformat(rows[0][0])), rows[0][1])
        observed = self._connection.execute(
            """
            WITH observed AS (
              SELECT clock_timestamp() AS observed_at
            )
            SELECT observed.observed_at,
                   request.lease_expires_at <= observed.observed_at AS expired
            FROM cpk_execution_requests AS request
            CROSS JOIN observed
            WHERE request.request_id = %s
            """,
            (request_id,),
        ).fetchone()
        if observed is None:
            raise KeyError("missing execution request")
        return _ExecutionLeaseObservation(
            request=request,
            observed_at=decode_postgres_timestamp(observed[0]),
            expired=observed[1],
        )

    def get_latest_run_for_request_for_update(
        self,
        request_id: str,
    ) -> ActivityRunRecord:
        return self._latest_run_for_request(request_id, for_update=True)

    def get_latest_run_for_request(self, request_id: str) -> ActivityRunRecord:
        """Bounded locator; the caller must hold the request before preparing runs."""
        return self._latest_run_for_request(request_id, for_update=False)

    def _latest_run_for_request(self, request_id: str, *, for_update: bool) -> ActivityRunRecord:
        _recovery_request_id(request_id)
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            return self._configuration_run(read, "request_id=%s", (request_id,),
                for_update=for_update, order="attempt DESC")
        suffix = " FOR UPDATE" if for_update else ""
        row = self._connection.execute(
            f"""
            SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs
            WHERE request_id = %s
            ORDER BY attempt DESC
            LIMIT 1{suffix}
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError("missing activity run")
        return _activity_run(row)

    def get_run_for_request_for_update(
        self,
        request_id: str,
        run_id: str,
    ) -> ActivityRunRecord:
        _recovery_request_id(request_id)
        _require_run_id(run_id)
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            return self._configuration_run(read, "request_id=%s AND run_id=%s", (request_id, run_id), for_update=True)
        row = self._connection.execute(
            """
            SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs
            WHERE request_id = %s AND run_id = %s
            FOR UPDATE
            """,
            (request_id, run_id),
        ).fetchone()
        if row is None:
            raise KeyError("activity run was not found for request")
        return _activity_run(row)

    def rotate_request_claim(
        self,
        request_id: str,
        *,
        expected_fence: ExecutionLeaseFence,
        replacement_fence: ExecutionLeaseFence,
        observed_at: str,
        lease_duration_seconds: int,
    ) -> ExecutionRequestRecord | None:
        _recovery_request_id(request_id)
        _recovery_fence_pair(expected_fence, replacement_fence)
        _recovery_observed_at(observed_at)
        if (type(lease_duration_seconds) is not int
                or not 1 <= lease_duration_seconds <= 3600):
            raise OperationsRecordError("recovery lease duration is invalid")
        self._require_empty_receiver_scope(request_id)
        return self._rotate_request_claim(request_id, expected_fence=expected_fence,
            replacement_fence=replacement_fence, observed_at=observed_at,
            lease_duration_seconds=lease_duration_seconds)

    def _rotate_request_claim(self, request_id, *, expected_fence, replacement_fence,
                              observed_at, lease_duration_seconds):
        _recovery_request_id(request_id)
        _recovery_fence_pair(expected_fence, replacement_fence)
        encoded_observed_at = _recovery_observed_at(observed_at)
        if (
            type(lease_duration_seconds) is not int
            or not 1 <= lease_duration_seconds <= 3600
        ):
            raise OperationsRecordError("recovery lease duration is invalid")
        row = self._connection.execute(
            """
            UPDATE cpk_execution_requests
            SET claim_worker_id = %s,
                claim_generation = %s,
                claimed_at = %s,
                lease_expires_at = %s + (%s * interval '1 second')
            WHERE request_id = %s
              AND status = 'claimed'
              AND claim_worker_id = %s
              AND claim_generation = %s
            RETURNING request_id, workspace_id, session_id, plan_id, status,
                      requested_by, requested_at, approval_request_id,
                      approval_decision_id, idempotency_key, intent_fingerprint,
                      claim_worker_id, claim_generation, claimed_at,
                      lease_expires_at
            """,
            (
                replacement_fence.worker_id,
                replacement_fence.generation,
                encoded_observed_at,
                encoded_observed_at,
                lease_duration_seconds,
                request_id,
                expected_fence.worker_id,
                expected_fence.generation,
            ),
        ).fetchone()
        return None if row is None else _execution_request(row)

    def abandon_request_claim(
        self,
        request_id: str,
        *,
        expected_fence: ExecutionLeaseFence,
        observed_at: str,
    ) -> ExecutionRequestRecord | None:
        _recovery_request_id(request_id)
        if type(expected_fence) is not ExecutionLeaseFence:
            raise OperationsRecordError("recovery claim fence is invalid")
        encoded_observed_at = _recovery_observed_at(observed_at)
        row = self._connection.execute(
            """
            UPDATE cpk_execution_requests
            SET status = 'abandoned',
                claim_worker_id = NULL,
                claim_generation = NULL,
                claimed_at = NULL,
                lease_expires_at = NULL
            WHERE request_id = %s
              AND status = 'claimed'
              AND claim_worker_id = %s
              AND claim_generation = %s
              AND lease_expires_at <= %s
            RETURNING request_id, workspace_id, session_id, plan_id, status,
                      requested_by, requested_at, approval_request_id,
                      approval_decision_id, idempotency_key, intent_fingerprint,
                      claim_worker_id, claim_generation, claimed_at,
                      lease_expires_at
            """,
            (
                request_id,
                expected_fence.worker_id,
                expected_fence.generation,
                encoded_observed_at,
            ),
        ).fetchone()
        return None if row is None else _execution_request(row)

    def add_run(self, record: ActivityRunRecord) -> ActivityRunRecord:
        self._require_empty_receiver_scope(record.admission.request_id)
        return self._add_run(record)

    def _add_run(self, record: ActivityRunRecord) -> ActivityRunRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, prior_run_id, status,
               created_at, started_at, settled_at, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                record.run_id,
                record.plan_id,
                record.admission.request_id,
                record.retry.attempt,
                record.retry.prior_run_id,
                record.status.value,
                encode_postgres_timestamp(record.created_at),
                _encode_optional_timestamp(record.started_at),
                _encode_optional_timestamp(record.settled_at),
                _json(record.metadata.descriptor()),
            ),
        )
        return record

    def get_run(self, run_id: str) -> ActivityRunRecord:
        from .configuration_cleanup_phase_read_bounds import _phase_context
        _phase_context(self._connection)
        _require_run_id(run_id)
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            return self._configuration_run(read, "run_id=%s", (run_id,))
        row = self._connection.execute(
            """
            SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs
            WHERE run_id = %s
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing activity run {run_id!r}")
        return _activity_run(row)

    def get_run_for_update(self, run_id: str) -> ActivityRunRecord:
        _require_run_id(run_id)
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            return self._configuration_run(read, "run_id=%s", (run_id,), for_update=True)
        row = self._connection.execute(
            """
            SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs
            WHERE run_id = %s
            FOR UPDATE
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing activity run {run_id!r}")
        return _activity_run(row)

    def compare_and_set_run_status(
        self,
        run_id: str,
        *,
        expected: ActivityRunStatus,
        replacement: ActivityRunStatus,
        started_at: str | None = None,
        settled_at: str | None = None,
    ) -> ActivityRunRecord | None:
        if replacement in (ActivityRunStatus.CLAIMED, ActivityRunStatus.RUNNING,
                            ActivityRunStatus.COMPENSATING):
            self._require_empty_receiver_scope(self.get_run(run_id).admission.request_id)
        return self._compare_and_set_run_status(run_id, expected=expected, replacement=replacement,
            started_at=started_at, settled_at=settled_at)

    def _compare_and_set_run_status(self, run_id, *, expected, replacement,
                                    started_at=None, settled_at=None):
        _require_run_id(run_id)
        encoded_started_at = _encode_optional_timestamp(started_at)
        encoded_settled_at = _encode_optional_timestamp(settled_at)
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            names = ("run_id", "plan_id", "request_id", "attempt", "prior_run_id", "status",
                "created_at", "started_at", "settled_at", "metadata")
            ceilings = tuple(65536 if name == "metadata" else 2048 for name in names)
            valid = " AND ".join(
                f"({name} IS NULL OR octet_length({name}::text)<={ceiling})"
                for name, ceiling in zip(names, ceilings))
            projection = ",".join(f"CASE WHEN {valid} THEN {name}::text END" for name in names)
            rows = read.query("WITH updated AS (UPDATE cpk_activity_runs "
                "SET status=%s, started_at=COALESCE(%s,started_at), "
                "settled_at=COALESCE(settled_at,%s) "
                "WHERE run_id=%s AND status=%s AND settled_at IS NULL "
                "RETURNING run_id,plan_id,request_id,attempt,prior_run_id,status,"
                "created_at,started_at,settled_at,metadata) SELECT " + projection +
                f",({valid}) FROM updated",
                (replacement.value, encoded_started_at, encoded_settled_at, run_id, expected.value),
                records=1, octets=sum(ceilings) + 1, cells=len(names) + 1)
            if not rows:
                return None
            if rows[0][-1] is not True:
                raise OperationsRecordError("activity run is unavailable")
            values = list(rows[0][:-1])
            values[3] = int(values[3])
            for index in (6, 7, 8):
                values[index] = None if values[index] is None else datetime.fromisoformat(values[index])
            values[9] = json.loads(values[9])
            return _activity_run(tuple(values))
        row = self._connection.execute(
            """
            UPDATE cpk_activity_runs
            SET status = %s,
                started_at = COALESCE(%s, started_at),
                settled_at = COALESCE(settled_at, %s)
            WHERE run_id = %s
              AND status = %s
              AND settled_at IS NULL
            RETURNING run_id, plan_id, request_id, attempt, prior_run_id, status,
                      created_at, started_at, settled_at, metadata
            """,
            (
                replacement.value,
                encoded_started_at,
                encoded_settled_at,
                run_id,
                expected.value,
            ),
        ).fetchone()
        return None if row is None else _activity_run(row)

    def runs_for_request(self, request_id: str) -> tuple[ActivityRunRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs
            WHERE request_id = %s
            ORDER BY attempt ASC, run_id ASC
            """,
            (request_id,),
        ).fetchall()
        return tuple(_activity_run(row) for row in rows)

    def overview_runs(self, plan_id: str) -> tuple[ActivityRunRecord, ...] | None:
        """Return up to 100 records, or unknown on bounded overflow evidence."""
        rows = self._connection.execute(
            """
            WITH bounded AS MATERIALIZED (
                SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                       created_at, started_at, settled_at, metadata
                FROM cpk_activity_runs WHERE plan_id = %s
                ORDER BY attempt, run_id LIMIT 101
            )
            SELECT bounded.*, (SELECT count(*) > 100 FROM bounded)
            FROM bounded ORDER BY attempt, run_id LIMIT 100
            """,
            (plan_id,),
        ).fetchall()
        if rows and rows[0][10]:
            return None
        return tuple(_activity_run(row[:10]) for row in rows)

    def overview_receipts(
        self, run_id: str,
    ) -> tuple[ExecutionCommandReceiptRecord, ...]:
        """Prefer unresolved admissions, otherwise the newest two completions.

        Key order only stabilizes the bounded result; equal completion times
        must remain ambiguous to the projection.
        """
        _require_run_id(run_id)
        rows = self._connection.execute(
            """
            SELECT run_id, idempotency_key, intent_fingerprint, worker_id,
                   authority_scopes, claim_generation, max_effects, admitted_at,
                   initial_run, receipt_status, completed_at, result, managed_intent
            FROM cpk_execution_command_receipts AS receipt
            WHERE receipt.run_id = %s
              AND (
                receipt.receipt_status = 'incomplete'
                OR NOT EXISTS (
                    SELECT 1 FROM cpk_execution_command_receipts AS unresolved
                    WHERE unresolved.run_id = receipt.run_id
                      AND unresolved.receipt_status = 'incomplete'
                )
              )
            ORDER BY completed_at DESC NULLS FIRST, admitted_at DESC, idempotency_key
            LIMIT 2
            """,
            (run_id,),
        ).fetchall()
        return tuple(_command_receipt(row) for row in rows)

    def runs_for_plan(self, plan_id: str) -> tuple[ActivityRunRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs
            WHERE plan_id = %s
            ORDER BY created_at ASC, run_id ASC
            """,
            (plan_id,),
        ).fetchall()
        return tuple(_activity_run(row) for row in rows)

    def run_page(self, request: ReadPageRequest) -> ReadPage[ActivityRunRecord]:
        if request.collection is not ReadCollection.PLAN_RUNS:
            raise ReadPageError("run page request is incongruent")
        cursor = request.cursor
        if cursor is not None:
            _require_page_run_id(cursor.item_id)
        seek = ""
        parameters: tuple[object, ...]
        if cursor is None:
            parameters = (
                request.scope.workspace_id,
                request.scope.plan_id,
                request.limit + 1,
            )
        else:
            seek = "AND (run.created_at, run.run_id) > (%s, %s)"
            parameters = (
                request.scope.workspace_id,
                request.scope.plan_id,
                encode_postgres_cursor_timestamp(cursor.instant),
                cursor.item_id,
                request.limit + 1,
            )
        rows = self._connection.execute(
            f"""
            SELECT run.run_id, run.plan_id, run.request_id, run.attempt,
                   run.prior_run_id, run.status, run.created_at, run.started_at,
                   run.settled_at, run.metadata
            FROM cpk_activity_runs AS run
            JOIN cpk_execution_requests AS request
              ON request.request_id = run.request_id
             AND request.plan_id = run.plan_id
            JOIN cpk_activity_plans AS plan
              ON plan.plan_id = run.plan_id
             AND plan.session_id = request.session_id
            JOIN cpk_operation_sessions AS session
              ON session.session_id = plan.session_id
             AND session.workspace_id = request.workspace_id
            WHERE request.workspace_id = %s
              AND run.plan_id = %s
              {seek}
            ORDER BY run.created_at ASC, run.run_id ASC
            LIMIT %s
            """,
            parameters,
        ).fetchall()
        candidates = tuple(
            ReadPageCandidate(
                item=_activity_run(row),
                cursor_after_item=TemporalReadCursor(
                    ReadCollection.PLAN_RUNS,
                    request.scope,
                    decode_postgres_cursor_timestamp(row[6]),
                    row[0],
                ),
            )
            for row in rows
        )
        return ReadPage.from_candidates(request, candidates)

    def add_event(self, record: ActivityEventRecord) -> ActivityEventRecord:
        return self._insert_event(record)

    def _add_advancement_event(self, record, prepared_receipt):
        return self._insert_event(record, prepared_receipt)

    def _insert_event(self, record, prepared_receipt=None):
        locators = (None,) * 4
        if record.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED:
            from control_plane_kit_operations._configuration_acceptance import _require_prepared_advancement
            _require_prepared_advancement(prepared_receipt, self._connection,
                record.evidence.descriptor().get("workspace_id"), after_cas=True)
            if record != prepared_receipt.event:
                raise OperationsRecordError("advancement event differs from original owner")
            locators = (prepared_receipt.workspace.workspace_id, prepared_receipt.request.identity.request_id,
                prepared_receipt.plan.plan_id, prepared_receipt.plan.desired_graph_revision)
        elif prepared_receipt is not None:
            raise OperationsRecordError("prepared advancement requires original event")
        payload = {
            "activity_id": record.activity_id,
            "evidence": record.evidence.descriptor(),
            "failure": None
            if record.failure is None
            else {
                "category": record.failure.category.value,
                "code": record.failure.code,
                "message": record.failure.message,
                "details": record.failure.details.descriptor(),
            },
            "recovery": (
                None
                if record.recovery is None
                else record.recovery.descriptor()
            ),
        }
        query = """
            INSERT INTO cpk_activity_events
              (event_id, run_id, ordinal, event_type, occurred_at, payload,
               advancement_workspace_id, advancement_request_id, advancement_plan_id, advancement_revision)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
            """
        values = (
                record.event_id,
                record.run_id,
                record.ordinal,
                record.kind.value,
                encode_postgres_timestamp(record.occurred_at),
                _json(payload),
            ) + locators
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            read.query(query + " RETURNING 1", values, records=1, octets=1, cells=1)
        else:
            self._connection.execute(query, values)
        return record

    def get_event(self, event_id: str) -> ActivityEventRecord:
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            columns = (("event_id", "text", 2048), ("run_id", "text", 2048),
                ("ordinal", "int", 32), ("event_type", "text", 128), ("occurred_at", "time", 64),
                ("payload", "json", 16384))
            rows = read.bounded_rows("cpk_activity_events", columns, "event_id=%s", (event_id,))
            if not rows:
                raise KeyError("missing activity event")
            return _activity_event(rows[0])
        row = self._connection.execute(
            """
            SELECT event_id, run_id, ordinal, event_type, occurred_at, payload
            FROM cpk_activity_events
            WHERE event_id = %s
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing activity event {event_id!r}")
        return _activity_event(row)

    def next_event_ordinal(self, run_id: str) -> int:
        _require_run_id(run_id)
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            locked = read.query("SELECT 1 FROM cpk_activity_runs WHERE run_id=%s FOR UPDATE",
                (run_id,), records=1, octets=1, cells=1)
            if not locked:
                raise KeyError("missing activity run")
            return read.query("SELECT COALESCE(MAX(ordinal),0)+1 FROM cpk_activity_events WHERE run_id=%s",
                (run_id,), records=1, octets=20, cells=1)[0][0]
        locked = self._connection.execute(
            "SELECT run_id FROM cpk_activity_runs WHERE run_id = %s FOR UPDATE",
            (run_id,),
        ).fetchone()
        if locked is None:
            raise KeyError(f"missing activity run {run_id!r}")
        row = self._connection.execute(
            """
            SELECT COALESCE(MAX(ordinal), 0) + 1
            FROM cpk_activity_events
            WHERE run_id = %s
            """,
            (run_id,),
        ).fetchone()
        return int(row[0])

    def events_for_run(self, run_id: str) -> tuple[ActivityEventRecord, ...]:
        _require_run_id(run_id)
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            from .receiver_execution_scopes import _ExecutionScopeStorage
            return _ExecutionScopeStorage(self._connection, read).events(run_id)
        rows = self._connection.execute(
            """
            SELECT event_id, run_id, ordinal, event_type, occurred_at, payload
            FROM cpk_activity_events
            WHERE run_id = %s
            ORDER BY ordinal ASC
            """,
            (run_id,),
        ).fetchall()
        return tuple(_activity_event(row) for row in rows)

    def event_page(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[ActivityEventRecord]:
        if request.collection is not ReadCollection.RUN_EVENTS:
            raise ReadPageError("event page request is incongruent")
        _require_page_run_id(request.scope.run_id)
        cursor = request.cursor
        parameters: tuple[object, ...]
        seek = ""
        if cursor is None:
            parameters = (request.scope.run_id, request.limit + 1)
        else:
            seek = "AND (ordinal, event_id) > (%s, %s)"
            parameters = (
                request.scope.run_id,
                cursor.ordinal,
                cursor.item_id,
                request.limit + 1,
            )
        rows = self._connection.execute(
            f"""
            SELECT event_id, run_id, ordinal, event_type, occurred_at, payload
            FROM cpk_activity_events
            WHERE run_id = %s
              {seek}
            ORDER BY ordinal ASC, event_id ASC
            LIMIT %s
            """,
            parameters,
        ).fetchall()
        candidates = tuple(
            ReadPageCandidate(
                item=_activity_event(row),
                cursor_after_item=OrdinalReadCursor(
                    ReadCollection.RUN_EVENTS,
                    request.scope,
                    row[2],
                    row[0],
                ),
            )
            for row in rows
        )
        return ReadPage.from_candidates(request, candidates)


def _execution_request(row: tuple[Any, ...]) -> ExecutionRequestRecord:
    claim = (
        None
        if row[11] is None
        else ClaimIdentity(
            worker_id=row[11],
            generation=row[12],
            claimed_at=decode_postgres_timestamp(row[13]),
            lease_expires_at=decode_postgres_timestamp(row[14]),
        )
    )
    return ExecutionRequestRecord(
        identity=ExecutionRequestIdentity(
            request_id=row[0],
            workspace_id=row[1],
            session_id=row[2],
            plan_id=row[3],
        ),
        status=ExecutionRequestStatus(row[4]),
        requested_by=row[5],
        requested_at=decode_postgres_timestamp(row[6]),
        approval_request_id=row[7],
        approval_decision_id=row[8],
        idempotency=ExecutionIdempotency(
            key=row[9],
            intent_fingerprint=row[10],
        ),
        claim=claim,
    )


def _activity_run(row: tuple[Any, ...]) -> ActivityRunRecord:
    return ActivityRunRecord(
        run_id=row[0],
        plan_id=row[1],
        admission=AdmittedRun(row[2]),
        retry=RetryIdentity(row[3], row[4]),
        status=ActivityRunStatus(row[5]),
        created_at=decode_postgres_timestamp(row[6]),
        started_at=_decode_optional_timestamp(row[7]),
        settled_at=_decode_optional_timestamp(row[8]),
        metadata=BoundedEvidence.from_mapping(row[9]),
    )


_RUN_DESCRIPTOR_KEYS = frozenset(
    {
        "run_id",
        "plan_id",
        "request_id",
        "attempt",
        "prior_run_id",
        "status",
        "created_at",
        "started_at",
        "settled_at",
        "metadata",
    }
)
_RESULT_DESCRIPTOR_KEYS = frozenset(
    {"run", "status", "effects_attempted", "activity_id"}
)


def _run_descriptor(record: ActivityRunRecord) -> dict[str, object]:
    return {
        "run_id": record.run_id,
        "plan_id": record.plan_id,
        "request_id": record.admission.request_id,
        "attempt": record.retry.attempt,
        "prior_run_id": record.retry.prior_run_id,
        "status": record.status.value,
        "created_at": record.created_at,
        "started_at": record.started_at,
        "settled_at": record.settled_at,
        "metadata": record.metadata.descriptor(),
    }


def _run_from_descriptor(value: object) -> ActivityRunRecord:
    if type(value) is not dict or frozenset(value) != _RUN_DESCRIPTOR_KEYS:
        raise OperationsRecordError("persisted execution command run is malformed")
    metadata = value["metadata"]
    if type(metadata) is not dict:
        raise OperationsRecordError("persisted execution command metadata is malformed")
    try:
        return ActivityRunRecord(
            run_id=value["run_id"],
            plan_id=value["plan_id"],
            admission=AdmittedRun(value["request_id"]),
            retry=RetryIdentity(value["attempt"], value["prior_run_id"]),
            status=ActivityRunStatus(value["status"]),
            created_at=value["created_at"],
            started_at=value["started_at"],
            settled_at=value["settled_at"],
            metadata=BoundedEvidence.from_mapping(metadata),
        )
    except (TypeError, ValueError):
        raise OperationsRecordError(
            "persisted execution command run is malformed"
        ) from None


def _result_descriptor(record: ExecutionCommandResultRecord) -> dict[str, object]:
    return {
        "run": _run_descriptor(record.run),
        "status": record.status.value,
        "effects_attempted": record.effects_attempted,
        "activity_id": record.activity_id,
    }


def _result_from_descriptor(value: object) -> ExecutionCommandResultRecord:
    if type(value) is not dict or frozenset(value) != _RESULT_DESCRIPTOR_KEYS:
        raise OperationsRecordError("persisted execution command result is malformed")
    try:
        return ExecutionCommandResultRecord(
            run=_run_from_descriptor(value["run"]),
            status=CoordinatorStatus(value["status"]),
            effects_attempted=value["effects_attempted"],
            activity_id=value["activity_id"],
        )
    except (TypeError, ValueError):
        raise OperationsRecordError(
            "persisted execution command result is malformed"
        ) from None


def _command_receipt(row: tuple[Any, ...]) -> ExecutionCommandReceiptRecord:
    scopes = row[4]
    if type(scopes) is not list:
        raise OperationsRecordError("persisted execution command scopes are malformed")
    try:
        return ExecutionCommandReceiptRecord(
            run_id=row[0],
            idempotency_key=row[1],
            intent_fingerprint=row[2],
            worker_id=row[3],
            authority_scopes=tuple(PolicyScope(value) for value in scopes),
            claim_generation=row[5],
            max_effects=positive_int_from_canonical_decimal(row[6]),
            admitted_at=decode_postgres_timestamp(row[7]),
            initial_run=_run_from_descriptor(row[8]),
            status=ExecutionCommandReceiptStatus(row[9]),
            completed_at=_decode_optional_timestamp(row[10]),
            result=None if row[11] is None else _result_from_descriptor(row[11]),
            managed_intent=None if row[12] is None else ManagedExecutionCommandIntent.from_descriptor(row[12]),
        )
    except (TypeError, ValueError):
        raise OperationsRecordError(
            "persisted execution command receipt is malformed"
        ) from None


def _activity_event(row: tuple[Any, ...]) -> ActivityEventRecord:
    payload = row[5]
    if not isinstance(payload, dict):
        raise ValueError("persisted activity event payload must be an object")
    evidence = payload.get("evidence", {})
    if not isinstance(evidence, dict):
        raise ValueError("persisted activity event evidence must be an object")
    return ActivityEventRecord(
        event_id=row[0],
        run_id=row[1],
        ordinal=row[2],
        kind=ActivityEventKind(row[3]),
        occurred_at=decode_postgres_timestamp(row[4]),
        activity_id=payload.get("activity_id"),
        evidence=BoundedEvidence.from_mapping(evidence),
        failure=_failure_evidence(payload.get("failure")),
        recovery=_recovery_evidence(payload.get("recovery")),
    )


_RECOVERY_EVIDENCE_KEYS = frozenset(
    {
        "decision",
        "retained_run_id",
        "prior_fence",
        "replacement_fence",
    }
)
_RECOVERY_FENCE_KEYS = frozenset({"worker_id", "generation"})


def _recovery_evidence(
    value: object,
) -> ExecutionLeaseRecoveryEvidence | None:
    if value is None:
        return None
    malformed = False
    decoded = None
    try:
        if type(value) is not dict or frozenset(value) != _RECOVERY_EVIDENCE_KEYS:
            raise ValueError("recovery evidence shape is invalid")
        replacement_value = value["replacement_fence"]
        decoded = ExecutionLeaseRecoveryEvidence(
            decision_kind=RecoveryDecisionKind(value["decision"]),
            retained_run_id=RunId(value["retained_run_id"]),
            prior_fence=_recovery_fence(value["prior_fence"]),
            replacement_fence=(
                None
                if replacement_value is None
                else _recovery_fence(replacement_value)
            ),
        )
    except ValueError:
        malformed = True
    if malformed or decoded is None:
        raise OperationsRecordError(
            "persisted recovery evidence is malformed"
        ) from None
    return decoded


def _recovery_fence(value: object) -> ExecutionLeaseFence:
    if type(value) is not dict or frozenset(value) != _RECOVERY_FENCE_KEYS:
        raise ValueError("recovery fence shape is invalid")
    return ExecutionLeaseFence(
        worker_id=value["worker_id"],
        generation=value["generation"],
    )


def _failure_evidence(value: object) -> FailureEvidence | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("persisted activity failure must be an object")
    if not {"category", "code", "message"} <= value.keys():
        raise ValueError("persisted activity failure is malformed")
    details = value.get("details", {})
    if not isinstance(details, dict):
        raise ValueError("persisted activity failure details must be an object")
    return FailureEvidence(
        category=FailureCategory(value["category"]),
        code=value["code"],
        message=value["message"],
        details=BoundedEvidence.from_mapping(details),
    )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _encode_optional_timestamp(value: str | None) -> object:
    return None if value is None else encode_postgres_timestamp(value)


def _decode_optional_timestamp(value: object) -> str | None:
    return None if value is None else decode_postgres_timestamp(value)


def _recovery_request_id(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
    ):
        raise OperationsRecordError("recovery request identity is invalid")


def _recovery_observed_at(value: object) -> object:
    if type(value) is not str:
        raise OperationsRecordError("recovery observation time is invalid")
    try:
        return encode_postgres_timestamp(value)
    except ValueError:
        pass
    raise OperationsRecordError("recovery observation time is invalid")


def _recovery_fence_pair(
    expected: object,
    replacement: object,
) -> None:
    if (
        type(expected) is not ExecutionLeaseFence
        or type(replacement) is not ExecutionLeaseFence
        or expected.generation >= 2**63 - 1
        or replacement.generation != expected.generation + 1
    ):
        raise OperationsRecordError("recovery claim fence is invalid")


def _require_run_id(value: object) -> None:
    try:
        RunId(value)  # type: ignore[arg-type]
    except ValueError:
        pass
    else:
        return
    raise OperationsRecordError("run_id is malformed")


def _require_command_key(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 200
        or any(ord(character) < 32 for character in value)
    ):
        raise OperationsRecordError("execution command key is malformed")


def _require_page_run_id(value: object) -> None:
    try:
        RunId(value)  # type: ignore[arg-type]
    except ValueError:
        pass
    else:
        return
    raise ReadPageError("run page identity is malformed")


_INVOCATION_COLUMNS = ("invocation_id", "version", "run_id", "command_key", "parent_fingerprint",
    "workspace_id", "activity_id", "kind", "slot_key", "target_fingerprint", "attempt",
    "original_event_id", "request_fingerprint", "authorization_id", "disposition", "decision_at",
    "outcome", "completed_at", "descriptor")
_INVOCATION_SELECT = "SELECT " + ", ".join(
    name if name in ("version", "attempt", "decision_at", "completed_at") else
    "CASE WHEN octet_length(descriptor::text)<=8192 THEN descriptor ELSE NULL END" if name == "descriptor" else
    f"CASE WHEN octet_length({name})<=2048 THEN {name} ELSE NULL END" for name in _INVOCATION_COLUMNS
) + " FROM cpk_execution_command_invocations"


def _invocation_reference(command):
    if type(command) is not ExecutionCommandReference:
        raise ExecutionInvocationConflict()
    try:
        command.__post_init__()
    except ValueError:
        raise ExecutionInvocationConflict() from None


def _invocation_id(value):
    import re
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ExecutionInvocationConflict()


def _invocation_record(record):
    if type(record) is not ExecutionInvocationRecord:
        raise ExecutionInvocationConflict()
    try:
        record.__post_init__()
    except (OperationsRecordError, ValueError, TypeError):
        raise ExecutionInvocationConflict() from None


def _invocation_values(record):
    from psycopg.types.json import Jsonb
    return (record.invocation_id, record.version, record.command.run_id, record.command.idempotency_key,
        record.command.intent_fingerprint, record.workspace_id, record.activity_id, record.kind.value,
        record.slot_key, record.target_fingerprint, record.attempt, record.original_event_id,
        record.request_fingerprint, record.authorization_id, record.disposition.value,
        _encode_optional_timestamp(record.decision_at), None if record.outcome is None else record.outcome.value,
        _encode_optional_timestamp(record.completed_at), Jsonb(record.descriptor()))


def _invocation_row(row):
    try:
        if type(row) is not tuple or len(row) != len(_INVOCATION_COLUMNS):
            raise OperationsRecordError("invocation row is malformed")
        record = ExecutionInvocationRecord.from_descriptor(row[-1])
        if row[:-1] != _invocation_values(record)[:-1]:
            raise OperationsRecordError("invocation row is incongruent")
        return record
    except (OperationsRecordError, ValueError, TypeError):
        raise ExecutionInvocationConflict() from None


def _require_invocation_owners(store, record, *, fresh=False):
    """Check immutable joins; semantic permission remains with the admitting owner."""
    from control_plane_kit_core.operations import EffectAttemptIdentity
    from control_plane_kit_core.planning import ActivityId
    from .activity_history import PostgresActivityHistoryStore
    from .effect_attempt_intent_store import EffectAttemptIntentStore
    from .effect_attempt_store import EffectAttemptStore
    from .secret_provider_store import SecretUseAuthorizationStore
    try:
        parent = store.command_receipt_for_idempotency(record.command.run_id, record.command.idempotency_key)
        if (parent is None or parent.intent_fingerprint != record.command.intent_fingerprint
                or parent.managed_intent is None or parent.managed_intent.workspace_id != record.workspace_id
                or (fresh and parent.status is not ExecutionCommandReceiptStatus.INCOMPLETE)):
            raise ExecutionInvocationConflict(record.invocation_id)
        run = store.get_run(record.command.run_id)
        request = store.get_request(run.admission.request_id)
        if (request.identity.workspace_id != record.workspace_id
                or parent.initial_run.admission.request_id != request.identity.request_id
                or parent.initial_run.plan_id != run.plan_id):
            raise ExecutionInvocationConflict(record.invocation_id)
        plan = PostgresActivityHistoryStore(store._connection).get_plan(run.plan_id)
        plan.plan.activity(ActivityId(record.activity_id))
        if record.attempt is not None:
            identity = EffectAttemptIdentity(RunId(record.command.run_id), record.activity_id, record.attempt)
            intent = EffectAttemptIntentStore(store._connection).get(identity)
            attempt = EffectAttemptStore(store._connection).get(identity)
            if (intent.original_start_event != attempt.original_start_event
                    or intent.request_fingerprint != record.request_fingerprint
                    or attempt.original_start_event.event_id != record.original_event_id
                    or intent.intent.source.workspace_id != record.workspace_id
                    or intent.intent.source.plan_id != run.plan_id):
                raise ExecutionInvocationConflict(record.invocation_id)
        if record.kind is ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH:
            from .runtime_authority_store import RuntimeAuthorityStore
            from control_plane_kit_operations.prepared_runtime_invocations import prepared_runtime_invocation_profile
            runtime = RuntimeAuthorityStore(store._connection).get_registration(
                record.workspace_id, intent.intent.authority_ref, record.runtime_registration_id)
            profile = prepared_runtime_invocation_profile(intent.intent,
                original_event_id=record.original_event_id, runtime_authority=runtime)
            if profile.target_fingerprint != record.target_fingerprint:
                raise ExecutionInvocationConflict(record.invocation_id)
        if record.kind is ExecutionInvocationKind.SECRET_RESOLUTION:
            authorized = SecretUseAuthorizationStore(store._connection).get(record.workspace_id, record.authorization_id)
            linked = record.original_event_id is not None and authorized.effect_id == record.original_event_id
            if not linked and authorized.effect_id is None and record.attempt is not None:
                # Signed health uses its existing operation identity, not the
                # generic runtime effect_id slot. Reuse its retained join.
                from control_plane_kit_operations.health_effect_preparations import health_effect_attempt_wire_id
                from .health_effect_preparation_store import HealthEffectPreparationStore
                preparation = HealthEffectPreparationStore(store._connection).get(identity)
                linked = (authorized.operation_id == health_effect_attempt_wire_id(identity)
                    and authorized.authorization_id in (preparation.transit_authorization_id,
                        preparation.workload_authorization_id)
                    and preparation.original_event_id == record.original_event_id
                    and preparation.request_fingerprint == record.request_fingerprint)
            if (authorized.run_id != record.command.run_id or authorized.activity_id != record.activity_id
                    or not linked or authorized.session_id != request.identity.session_id
                    or authorized.actor_subject != parent.managed_intent.actor.subject_id
                    or secret_invocation_target_fingerprint(authorized) != record.target_fingerprint):
                raise ExecutionInvocationConflict(record.invocation_id)
    except (KeyError, OperationsRecordError, ValueError, TypeError, AttributeError):
        raise ExecutionInvocationConflict(record.invocation_id) from None


def validate_execution_invocation_rows(connection):
    """Bounded exact-data verification for the final schema, never repair."""
    from .current_data_validation import CurrentRowDrift
    store, after = PostgresExecutionStore(connection), None
    try:
        while True:
            rows = connection.execute(_INVOCATION_SELECT
                + ("" if after is None else " WHERE invocation_id>%s")
                + ' ORDER BY invocation_id COLLATE "C" NULLS FIRST LIMIT 100',
                () if after is None else (after,)).fetchall()
            for row in rows:
                record = _invocation_row(row)
                _require_invocation_owners(store, record)
                after = record.invocation_id
            if len(rows) < 100:
                return
    except (ExecutionInvocationConflict, OperationsRecordError, ValueError, TypeError):
        raise CurrentRowDrift from None
