"""Caller-transactional ingress context and atomic, bounded result completion."""

from psycopg.types.json import Jsonb

from control_plane_kit_core.operations import ActivityEventKind
from control_plane_kit_core.planning import ActivityId, AllocatePublicIngress, RemovePublicIngress
from control_plane_kit_core.secrets import SecretUseIntent
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationDisposition as Disposition, ExecutionInvocationKind as Kind,
    ExecutionInvocationOutcome as Outcome, ExecutionInvocationRecord,
)
from control_plane_kit_operations.ingress_invocations import (
    IngressInvocationContextRecord, IngressInvocationResultRecord, INGRESS_KINDS,
    validate_ingress_result, require, _canonical,
)
from control_plane_kit_operations.records import ExecutionCommandReceiptStatus, OperationsRecordError
from .temporal import encode_postgres_timestamp, decode_postgres_timestamp

_CONTEXT_COLUMNS = (
    ("context_id", "hash", 64), ("version", "int", 1), ("run_id", "text", 2048),
    ("command_key", "command", 800), ("parent_fingerprint", "hash", 64),
    ("workspace_id", "text", 2048), ("ingress_id", "text", 2048),
    ("activity_id", "identifier", 200), ("original_event_id", "text", 2048),
    ("ingress_registration_id", "text", 2048), ("created_at", "time", 0), ("descriptor", "json", 32768),
)
_RESULT_COLUMNS = (("invocation_id", "hash", 64), ("context_id", "hash", 64),
    ("recorded_at", "time", 0), ("descriptor", "json", 8192))


def bounded_rows(connection, table, columns, where, parameters, *, limit=2, order=None, lock=None):
    """Private static-query projection: invalid rows remain bounded sentinels.

    Identifiers/predicates come only from Operations source, never request data.
    Values, including malformed timestamps, are guarded before psycopg transfer.
    """
    projection = []
    for name, kind, maximum in columns:
        nullable = kind.endswith("?")
        kind = kind.rstrip("?")
        if kind == "int":
            valid = f"{name} IS NOT NULL"
        elif kind == "time":
            valid = (f"{name} IS NOT NULL AND isfinite({name}) AND "
                f"{name}>='0001-01-01 00:00:00+00'::timestamptz AND "
                f"{name}<'10000-01-01 00:00:00+00'::timestamptz")
        elif kind == "json":
            valid = f"jsonb_typeof({name})='object' AND octet_length({name}::text)<={maximum}"
        else:
            valid = f"octet_length({name}) BETWEEN 1 AND {maximum}"
            if kind == "hash":
                valid += f" AND ({name} COLLATE \"C\") ~ '^[0-9a-f]{{64}}$'"
            elif kind == "identifier":
                valid += f" AND ({name} COLLATE \"C\") ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{0,199}}$'"
            elif kind == "command":
                valid += f" AND char_length({name})<=200"
        if nullable:
            valid = f"{name} IS NULL OR ({valid})"
        valid = f"COALESCE(({valid}), false)"
        projection.extend((valid, f"CASE WHEN {valid} THEN {name} ELSE NULL END"))
    sql = f"SELECT {', '.join(projection)} FROM {table} WHERE {where}"
    if order is not None:
        sql += " ORDER BY " + order
    sql += " LIMIT %s"
    if lock is not None:
        require(lock in ("SHARE", "UPDATE"))
        sql += " FOR " + lock
    rows = connection.execute(sql, (*parameters, limit)).fetchall()
    result = []
    for row in rows:
        require(len(row) == 2 * len(columns) and all(flag is True for flag in row[::2]))
        result.append(tuple(row[1::2]))
    return result


class IngressInvocationStore:
    def __init__(self, connection):
        self._connection = connection

    def context_for_command_activity(self, command, activity_id):
        rows = bounded_rows(self._connection, "cpk_ingress_invocation_contexts", _CONTEXT_COLUMNS,
            "run_id=%s AND command_key=%s AND activity_id=%s",
            (command.run_id, command.idempotency_key, activity_id))
        require(len(rows) <= 1)
        if not rows:
            return None
        record = self._context_row(rows[0])
        require(record.command == command)
        return record

    def context_for_original_event(self, workspace_id, run_id, event_id):
        rows = bounded_rows(self._connection, "cpk_ingress_invocation_contexts", _CONTEXT_COLUMNS,
            "workspace_id=%s AND run_id=%s AND original_event_id=%s", (workspace_id, run_id, event_id))
        require(len(rows) <= 1)
        return None if not rows else self._context_row(rows[0])

    def _context_row(self, row):
        record = IngressInvocationContextRecord.from_descriptor(row[-1])
        require(row == _context_values(record))
        return record

    def add_context(self, record):
        self.require_owners(record, fresh=True)
        existing = self.context_for_command_activity(record.command, record.activity_id)
        if existing is not None:
            require(existing == record)
            return existing
        names = ", ".join(column[0] for column in _CONTEXT_COLUMNS)
        values = _context_values(record)
        self._connection.execute(f"INSERT INTO cpk_ingress_invocation_contexts ({names}) VALUES ("
            + ", ".join("%s" for _ in values) + ")", values[:-1] + (Jsonb(values[-1]),))
        require(self.context_for_command_activity(record.command, record.activity_id) == record)
        return record

    def require_owners(self, record, *, fresh=False):
        from .stores import PostgresStoreBundle
        require(type(record) is IngressInvocationContextRecord)
        record.__post_init__()
        stores = PostgresStoreBundle(self._connection)
        parent = stores.execution.command_receipt_for_idempotency(record.command.run_id, record.command.idempotency_key)
        run = stores.execution.get_run(record.command.run_id)
        request = stores.execution.get_request(run.admission.request_id)
        plan = stores.activity_history.get_plan(record.plan_id)
        event = stores.execution.get_event(record.original_event_id)
        registration = stores.ingress_authorities.get_registration(record.workspace_id,
            record.ingress.authority_ref, record.ingress_registration_id)
        authorization = stores.secret_use_authorizations.get(record.workspace_id, record.authorization_id)
        require(parent is not None and parent.intent_fingerprint == record.command.intent_fingerprint
            and parent.managed_intent is not None and parent.managed_intent.workspace_id == record.workspace_id
            and (not fresh or parent.status is ExecutionCommandReceiptStatus.INCOMPLETE)
            and parent.initial_run.admission.request_id == run.admission.request_id == request.identity.request_id
            and parent.initial_run.plan_id == run.plan_id == request.identity.plan_id == record.plan_id
            and request.identity.workspace_id == record.workspace_id
            and plan.session_id == request.identity.session_id
            and (plan.base_realized_projection_id, plan.desired_realized_projection_id)
                == (record.base_projection_id, record.desired_projection_id)
            and event.run_id == record.command.run_id and event.activity_id == record.activity_id
            and event.kind is ActivityEventKind.STEP_STARTED and event.occurred_at == record.created_at
            and registration.authority == record.authority
            and authorization.run_id == record.command.run_id and authorization.activity_id == record.activity_id
            and authorization.effect_id == record.original_event_id
            and authorization.operation_id == request.identity.request_id
            and authorization.session_id == plan.session_id and authorization.probe_id is None
            and authorization.actor_subject == parent.managed_intent.actor.subject_id
            and authorization.requested_at == record.created_at
            and authorization.reference == record.authority.api_token_ref
            and authorization.intent is SecretUseIntent.CLOUDFLARE_API_TOKEN
            and record.custody_grant.actor_subject == authorization.actor_subject
            and record.custody_grant.operation_id == request.identity.request_id
            and record.custody_grant.session_id == plan.session_id)
        operation = plan.plan.activity(ActivityId(record.activity_id)).operation
        require(type(operation) is (AllocatePublicIngress if record.operation == "allocate" else RemovePublicIngress)
            and operation.target.ingress_id == record.ingress_id)
        base = stores.realized_graphs.get(record.base_projection_id)
        desired = stores.realized_graphs.get(record.desired_projection_id)
        require(base.workspace_id == desired.workspace_id == record.workspace_id
            and base.source_authored_graph_id == plan.base_graph_id
            and desired.source_authored_graph_id == plan.desired_graph_id)
        graph = DEFAULT_GRAPH_CODEC.decode((desired if record.operation == "allocate" else base).graph_descriptor)
        require(tuple(ingress for ingress in graph.public_ingresses if ingress.ingress_id == record.ingress_id)
            == (record.ingress,))
        if record.original_secret is not None:
            original = record.original_secret
            require(stores.generated_ingress_secrets.get_by_source(workspace_id=record.workspace_id,
                purpose=original.purpose, source_run_id=original.source_run_id,
                source_activity_id=original.source_activity_id, source_event_id=original.source_event_id) == original)
        return authorization

    def invocations(self, context):
        from .execution import _INVOCATION_COLUMNS, _invocation_row
        hashes = ("invocation_id", "parent_fingerprint", "target_fingerprint", "request_fingerprint")
        nullable = ("attempt", "original_event_id", "request_fingerprint", "authorization_id",
            "decision_at", "outcome", "completed_at")
        columns = []
        for name in _INVOCATION_COLUMNS:
            kind, maximum = ("json", 8192) if name == "descriptor" else (
                ("int", 0) if name in ("version", "attempt") else
                ("time", 0) if name in ("decision_at", "completed_at") else
                ("hash", 64) if name in hashes else
                ("command", 800) if name == "command_key" else
                ("identifier", 200) if name in ("activity_id", "authorization_id") else
                ("text", 64) if name in ("kind", "disposition", "outcome") else ("text", 2048))
            columns.append((name, kind + ("?" if name in nullable else ""), maximum))
        rows = bounded_rows(self._connection, "cpk_execution_command_invocations", columns,
            "run_id=%s AND command_key=%s AND activity_id=%s",
            (context.command.run_id, context.command.idempotency_key, context.activity_id),
            limit=4, order='invocation_id COLLATE "C"')
        require(len(rows) <= 3)
        records = tuple(_invocation_row(row) for row in rows)
        for record in records:
            self.require_invocation(context, record)
        by_kind = {record.kind: record for record in records}
        require(len(by_kind) == len(records))
        ordered = tuple(by_kind[kind] for kind in context.sequence if kind in by_kind)
        require(tuple(record.kind for record in ordered) == context.sequence[:len(ordered)])
        require(all(record.disposition is Disposition.ADMITTED and record.outcome is Outcome.RETURNED
            for record in ordered[:-1]))
        return ordered

    def require_invocation(self, context, record):
        require(type(record) is ExecutionInvocationRecord and record.kind in context.sequence
            and record.command == context.command and record.workspace_id == context.workspace_id
            and record.activity_id == context.activity_id
            and (record.attempt, record.original_event_id, record.request_fingerprint) == (None, None, None)
            and record.disposition is not Disposition.PLANNED
            and record.authorization_id == (context.authorization_id if record.kind is Kind.SECRET_RESOLUTION else None)
            and record.target_fingerprint == context.target_fingerprint(record.kind))

    def results_for_context(self, context):
        rows = bounded_rows(self._connection, "cpk_ingress_invocation_results", _RESULT_COLUMNS,
            "context_id=%s", (context.context_id,), limit=3, order='invocation_id COLLATE "C"')
        require(len(rows) <= 2)
        records = tuple(IngressInvocationResultRecord(row[0], row[1], decode_postgres_timestamp(row[2]),
            _canonical(row[3], 8192)) for row in rows)
        invocations = {record.invocation_id: record for record in self.invocations(context)}
        for record in records:
            require(record.invocation_id in invocations)
            invocation = invocations[record.invocation_id]
            validate_ingress_result(context, invocation.kind, invocation.outcome, record.descriptor())
            require(invocation.completed_at == record.recorded_at)
        require({record.invocation_id for record in records} == {invocation.invocation_id
            for invocation in invocations.values() if invocation.kind in INGRESS_KINDS
            and invocation.disposition is Disposition.ADMITTED and invocation.outcome is not Outcome.PENDING})
        return records

    def add_result(self, context, expected, outcome, payload, recorded_at):
        from .execution import PostgresExecutionStore
        execution = PostgresExecutionStore(self._connection)
        self.require_owners(context)
        self.require_invocation(context, expected)
        require(expected.disposition is Disposition.ADMITTED and expected.outcome is Outcome.PENDING)
        current = execution.command_invocation(context.command, expected.invocation_id, for_update=True)
        require(current is not None)
        existing = next((value for value in self.results_for_context(context)
            if value.invocation_id == current.invocation_id), None)
        if current.outcome is not Outcome.PENDING:
            require(existing is not None and current == expected.completed(outcome, current.completed_at)
                and existing.descriptor() == payload)
            return existing
        require(current == expected and existing is None)
        result = IngressInvocationResultRecord.create(context, current, outcome, payload, recorded_at)
        self._connection.execute("INSERT INTO cpk_ingress_invocation_results "
            "(invocation_id, context_id, recorded_at, descriptor) VALUES (%s,%s,%s,%s)",
            (result.invocation_id, result.context_id, encode_postgres_timestamp(result.recorded_at), Jsonb(result.descriptor())))
        require(execution.replace_command_invocation(current, current.completed(outcome, recorded_at)))
        require(result in self.results_for_context(context))
        return result


def _context_values(record):
    return (record.context_id, record.version, record.command.run_id, record.command.idempotency_key,
        record.command.intent_fingerprint, record.workspace_id, record.ingress_id, record.activity_id,
        record.original_event_id, record.ingress_registration_id, encode_postgres_timestamp(record.created_at), record.descriptor())


def require_ingress_invocation_owner(connection, record, *, fresh=False):
    """Return whether the exact ingress owner applies, rejecting mixed sources."""
    store = IngressInvocationStore(connection)
    context = store.context_for_command_activity(record.command, record.activity_id)
    if context is None:
        require(record.kind not in INGRESS_KINDS)
        return False
    store.require_owners(context, fresh=fresh)
    store.require_invocation(context, record)
    return True


def validate_ingress_invocation_rows(connection):
    from .current_data_validation import CurrentRowDrift
    store, after = IngressInvocationStore(connection), None
    try:
        while True:
            rows = bounded_rows(connection, "cpk_ingress_invocation_contexts", _CONTEXT_COLUMNS,
                "true" if after is None else 'context_id COLLATE "C">%s',
                () if after is None else (after,), limit=100, order='context_id COLLATE "C"')
            for row in rows:
                context = store._context_row(row)
                store.require_owners(context)
                store.invocations(context)
                store.results_for_context(context)
                after = context.context_id
            if len(rows) < 100:
                return
    except (KeyError, ValueError, TypeError, AttributeError):
        raise CurrentRowDrift from None
