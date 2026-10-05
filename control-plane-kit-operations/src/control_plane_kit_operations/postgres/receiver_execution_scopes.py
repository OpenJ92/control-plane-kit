"""Execution-owned receiver coverage and bounded original-history reads.

All identifiers in SQL templates below are package constants. Caller values are
parameters. No method commits, repairs retained rows, or performs runtime I/O.
"""

from datetime import datetime, timezone
from dataclasses import replace
import json

from control_plane_kit_operations.receiver_execution_scopes import (
    MAX_CANDIDATE_ROWS, MAX_DOCUMENT_BYTES, MAX_EVENTS, MAX_EFFECT_ROWS,
    MAX_REQUESTS, MAX_RUNS, MAX_SCOPES, MAX_VALUE_BYTES,
    ExecutionReceiverScope, ReceiverScopeCapacity, ReceiverScopeUnavailable,
    _capacity, _require, _text, derive_execution_receiver_scopes,
)


class _Transport:
    """Reserve before every fetch; account only the frozen returned-value bytes.

Length probes contain at most 32 int4/null cells and reserve 512 bytes per row.
The guarded second read returns text/bytea plus one fixed validity scalar. A
changed row can never turn a small probe into an unbounded transported value.
    """

    def __init__(self, connection, configuration_read=None):
        self.connection = connection
        self.configuration_read = configuration_read
        self.used = 0
        self.cache = {}

    @property
    def remaining(self):
        if self.configuration_read is not None:
            return max(0, min(MAX_VALUE_BYTES - self.used,
                16 * 1024 * 1024 - self.configuration_read.used.accounted_bytes))
        return MAX_VALUE_BYTES - self.used

    def reserve(self, amount):
        _capacity(type(amount) is int and 0 <= amount <= self.remaining)
        self.used += amount

    def charge(self, reserved, rows):
        actual = sum(0 if value is None else len(value) if isinstance(value, (bytes, memoryview))
                     else 1 if type(value) is bool else len(str(value).encode("utf-8"))
                     for row in rows for value in row)
        _require(actual <= reserved)
        self.used -= reserved - actual

    def read(self, table, columns, where, params, *, order="", maximum=1,
             point=False, cache=False, page=False, unique=False, phase=None):
        """Read a complete point or prefix after row-count and size observation.

Each column is (SQL expression, maximum bytes). Expressions explicitly cast
scalars/JSON/times to text, preserving bytea. Decoding happens after transport.
        """
        if phase is not None:
            from .configuration_cleanup_phase_read_bounds import _phase_context, _phase_columns, _phase_rows
            issued = _phase_context(self.connection)
            if issued is not None:
                from .configuration_evidence import _Unavailable
                if self.configuration_read is None:
                    raise _Unavailable
                _phase_context(self.connection, accounting=self.configuration_read.accounting)
            if point:
                narrowed = _phase_columns(self.connection, *phase,
                    tuple((name, "text", cap) for name, cap in columns))
                columns = tuple((name, cap) for name, _, cap in narrowed)
            elif self.configuration_read is not None:
                rows = _phase_rows(self.configuration_read, *phase, maximum=maximum, text=True)
                if rows is not None:
                    return rows
        key = (table, columns, where, params, order, maximum, point, page, unique)
        _require(0 < len(columns) <= 32)
        if cache and key in self.cache:
            return self.cache[key]
        if self.configuration_read is not None:
            # Original-material verification keeps its decoder and authority
            # laws, while B1 supplies the same command's transport accounting.
            declared = tuple((expression, "bytes" if not expression.endswith("::text") else "text", cap)
                for expression, cap in columns)
            rows = self.configuration_read.bounded_rows(table, declared, where, params,
                maximum=1 if point else maximum, order=order, point=point or page)
            if unique and len(rows) > (1 if point else maximum):
                raise ReceiverScopeUnavailable("receiver scope evidence is unavailable")
            if cache:
                self.cache[key] = rows
            return rows
        _require(0 < len(columns) <= 32)
        nominal = 1 if point else maximum if page else maximum + 1
        limit = min(nominal, self.remaining // 512)
        _capacity(limit > 0)
        reserved = limit * 512
        self.reserve(reserved)
        suffix = " FROM " + table + " WHERE " + where
        if order:
            suffix += " ORDER BY " + order
        suffix += " LIMIT %s"
        lengths = self.connection.execute(
            "SELECT " + ",".join(f"octet_length({expression})" for expression, _ in columns)
            + suffix, (*params, limit),
        ).fetchall()
        self.charge(reserved, lengths)
        if unique and limit == nominal and len(lengths) == nominal:
            raise ReceiverScopeUnavailable("receiver scope evidence is unavailable")
        _capacity(point or len(lengths) < limit or (page and limit == nominal))
        if not lengths:
            result = ()
        else:
            bounds = []
            for index, (_, ceiling) in enumerate(columns):
                sizes = [row[index] for row in lengths if row[index] is not None]
                _require(all(type(size) is int and 0 <= size <= ceiling for size in sizes))
                bounds.append(max(sizes) if sizes else None)
            # One explicitly reserved sentinel detects prefix growth between
            # observations. Unique-key point reads have proven cardinality one.
            fetch_limit = 1 if point else len(lengths) if page else len(lengths) + 1
            reserved = fetch_limit * (sum(size or 0 for size in bounds) + 1)
            self.reserve(reserved)
            checks = [f"{expression} IS NULL" if size is None else
                      f"({expression} IS NULL OR octet_length({expression})<={size})"
                      for (expression, _), size in zip(columns, bounds)]
            valid = " AND ".join(checks)
            rows = self.connection.execute(
                "SELECT " + ",".join(f"CASE WHEN {valid} THEN {expression} END"
                                       for expression, _ in columns)
                + f",({valid})" + suffix, (*params, fetch_limit),
            ).fetchall()
            self.charge(reserved, rows)
            _require(len(rows) == len(lengths) and all(row[-1] is True for row in rows))
            result = tuple(tuple(row[:-1]) for row in rows)
        if cache:
            self.cache[key] = result
        return result


def _columns(names, *, json_columns=(), byte_columns=(), ceilings=None):
    limits = ceilings or {}
    return tuple((name if name in byte_columns else name + "::text",
                  limits.get(name, MAX_DOCUMENT_BYTES if name in json_columns else 2048))
                 for name in names)


def _decode(row, names, *, json_columns=(), int_columns=(), time_columns=()):
    values = list(row)
    for index, name in enumerate(names):
        value = values[index]
        if value is None:
            continue
        if name in json_columns:
            values[index] = json.loads(value)
        elif name in int_columns:
            values[index] = int(value)
        elif name in time_columns:
            values[index] = datetime.fromisoformat(value)
    return tuple(values)


_PLAN = ("plan_id", "session_id", "base_graph_id", "desired_graph_id",
         "base_realized_projection_id", "desired_realized_projection_id",
         "desired_graph_revision", "status", "created_at", "payload")
_GRAPH = ("graph_id", "workspace_id", "version", "graph_descriptor", "created_by", "created_at", "metadata")
_PROJECTION = ("projection_id", "workspace_id", "source_authored_graph_id", "projection_kind",
               "projection_key", "projection_digest", "graph_descriptor", "created_by", "created_at")
_SCOPE = ("request_id", "workspace_id", "scope_ordinal", "scope_kind", "runtime_id", "node_id")
_REQUEST = ("request_id", "workspace_id", "session_id", "plan_id", "status", "requested_by",
            "requested_at", "approval_request_id", "approval_decision_id", "idempotency_key",
            "intent_fingerprint", "claim_worker_id", "claim_generation", "claimed_at", "lease_expires_at")
_RUN = ("run_id", "plan_id", "request_id", "attempt", "prior_run_id", "status",
        "created_at", "started_at", "settled_at", "metadata")
_EVENT = ("event_id", "run_id", "ordinal", "event_type", "occurred_at", "payload")
_ACTION = ("action_id", "session_id", "ordinal", "action_type", "actor_id", "payload",
           "created_at", "idempotency_key", "intent_fingerprint")


class _ExecutionScopeStorage:
    def __init__(self, connection, configuration_read=None):
        self.connection = connection
        self.transport = _Transport(connection, configuration_read)
        self.run_count = 0
        self.event_count = 0
        self.effect_count = 0
        self.event_rows = {}

    def guard(self, workspace_id, guard):
        from .graph_store import WorkspaceLifecycleGuard
        _require(type(guard) is WorkspaceLifecycleGuard)
        _require(guard.workspace_id == workspace_id and guard._owner._connection is self.connection)
        _require(guard._owner.owns_receiver_lifecycle(guard, workspace_id))
        if self.transport.configuration_read is None:
            self.transport.reserve(512)
            row = self.connection.execute("SELECT txid_current()").fetchone()
            self.transport.charge(512, (row,))
        else:
            rows = self.transport.configuration_read.query("SELECT txid_current()", (),
                records=1, octets=20, cells=1)
            _require(len(rows) == 1)
            row = rows[0]
        _require(row == (guard._transaction_id,))

    def originals(self, identity):
        from .activity_history import _plan_record
        from .graph_store import _graph_record, _realized_graph_projection_record
        from control_plane_kit_operations.records import RealizedGraphProjectionRecord
        from .configuration_cleanup_read_ceilings import _cleanup_original_limits

        for value in (identity.request_id, identity.workspace_id, identity.session_id, identity.plan_id):
            _text(value)
        sessions = self.transport.read("cpk_operation_sessions", _columns(("workspace_id",)),
            "session_id=%s", (identity.session_id,), point=True, cache=True)
        _require(sessions == ((identity.workspace_id,),))
        from .configuration_cleanup_phase_read_bounds import _phase_require
        for parent in ("request", "scopes"):
            _phase_require(self.connection, parent, (identity.request_id,), "plan", (identity.plan_id,))
        rows = self.transport.read("cpk_activity_plans", _columns(_PLAN, json_columns=("payload",),
            ceilings=_cleanup_original_limits(self.connection, "plan", identity.plan_id)),
            "plan_id=%s", (identity.plan_id,), point=True, cache=True, phase=("plan", (identity.plan_id,)))
        _require(len(rows) == 1)
        plan = _plan_record(_decode(rows[0], _PLAN, json_columns=("payload",),
            int_columns=("desired_graph_revision",), time_columns=("created_at",)))
        projections = []
        for side in ("base", "desired"):
            graph_id = getattr(plan, side + "_graph_id")
            _text(graph_id)
            _phase_require(self.connection, "plan", (plan.plan_id,), "graph", (graph_id,))
            authored = self.transport.read("cpk_graph_versions",
                _columns(_GRAPH, json_columns=("graph_descriptor", "metadata"),
                    ceilings=_cleanup_original_limits(self.connection, "graph", graph_id)),
                "graph_id=%s AND workspace_id=%s", (graph_id, identity.workspace_id), point=True, cache=True,
                phase=("graph", (graph_id,)))
            _require(len(authored) == 1)
            graph = _graph_record(_decode(authored[0], _GRAPH,
                json_columns=("graph_descriptor", "metadata"), int_columns=("version",), time_columns=("created_at",)))
            projection_id = getattr(plan, side + "_realized_projection_id")
            if projection_id is None:
                # Legacy lookup means the original authored identity projection,
                # never today's selected projection and never a persistence write.
                expected = RealizedGraphProjectionRecord.identity_for_authored(authored_record=graph)
                projection_id = expected.projection_id
            else:
                expected = None
            _text(projection_id)
            _phase_require(self.connection, "plan", (plan.plan_id,), "projection", (projection_id,))
            rows = self.transport.read("cpk_realized_graph_projections",
                _columns(_PROJECTION, json_columns=("graph_descriptor",),
                    ceilings=_cleanup_original_limits(self.connection, "projection", projection_id)),
                "projection_id=%s AND workspace_id=%s AND source_authored_graph_id=%s",
                (projection_id, identity.workspace_id, graph_id), point=True, cache=True,
                phase=("projection", (projection_id,)))
            if not rows and expected is not None:
                projection = expected
            else:
                _require(len(rows) == 1)
                projection = _realized_graph_projection_record(_decode(rows[0], _PROJECTION,
                    json_columns=("graph_descriptor",), time_columns=("created_at",)))
                if expected is not None:
                    _require(projection == expected)
            projections.append(projection)
        return plan, *projections

    def derive(self, identity):
        return derive_execution_receiver_scopes(identity, *self.originals(identity))

    def persist(self, identity, derived):
        for ordinal, scope in enumerate(derived.scopes):
            self.connection.execute(
                "INSERT INTO cpk_execution_receiver_scopes "
                "(request_id,workspace_id,scope_ordinal,scope_kind,runtime_id,node_id) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (identity.request_id, identity.workspace_id, ordinal, scope.scope_kind, scope.runtime_id, scope.node_id))

    def verify(self, identity):
        original = self.originals(identity)
        derived = derive_execution_receiver_scopes(identity, *original)
        headers = self.transport.read("cpk_execution_requests",
            _columns(("workspace_id", "session_id", "plan_id", "receiver_scope_count", "receiver_scope_digest")),
            "request_id=%s", (identity.request_id,), point=True)
        _require(headers == ((identity.workspace_id, identity.session_id, identity.plan_id,
                              str(len(derived.scopes)), derived.source_digest),))
        rows = self.transport.read("cpk_execution_receiver_scopes", _columns(_SCOPE),
            "request_id=%s", (identity.request_id,), order="scope_ordinal", maximum=MAX_SCOPES,
            phase=("scopes", (identity.request_id,)))
        expected = tuple((identity.request_id, identity.workspace_id, str(index), scope.scope_kind,
                          scope.runtime_id, scope.node_id) for index, scope in enumerate(derived.scopes))
        _require(rows == expected)
        return original, derived

    def candidates(self, workspace_id, requested):
        _text(workspace_id)
        _require(type(requested) is tuple and all(type(scope) is ExecutionReceiverScope for scope in requested))
        scopes = set(requested)
        runtime_wide = {scope.runtime_id for scope in scopes if scope.node_id is None}
        scopes = {scope for scope in scopes if scope.node_id is None or scope.runtime_id not in runtime_wide}
        _capacity(len(scopes) <= MAX_SCOPES)
        prefixes = set()
        for scope in scopes:
            prefixes.add((scope.runtime_id, "runtime", None))
            prefixes.add((scope.runtime_id, "all-nodes" if scope.node_id is None else "node", scope.node_id))
        found, raw = set(), 0
        for runtime_id, kind, node_id in sorted(prefixes):
            where = "workspace_id=%s AND runtime_id=%s AND scope_kind="
            params = [workspace_id, runtime_id]
            if kind == "runtime":
                where += "'runtime'"
            else:
                where += "'node'"
                if kind == "node":
                    where += " AND node_id=%s"
                    params.append(node_id)
            limit = min(MAX_CANDIDATE_ROWS - raw + 1, self.transport.remaining // 4096)
            if kind != "all-nodes":
                limit = min(limit, MAX_REQUESTS + 1)
            if self.transport.configuration_read is not None:
                from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
                used = self.transport.configuration_read.used
                row_bytes = ConfigurationEvidenceFootprint(1, 4096, 3, 0).accounted_bytes
                statement_bytes = ConfigurationEvidenceFootprint(0, 0, 0, 1).accounted_bytes
                # The same ledger reserves markers/records as well as values.
                # A full shortened prefix still refuses below, before dedup.
                limit = min(limit, max(0, 4096 - used.records),
                    max(0, (16 * 1024 * 1024 - used.accounted_bytes - statement_bytes) // row_bytes))
            _capacity(limit > 0)
            reserved = limit * 4096
            self.transport.reserve(reserved)
            valid = " AND ".join(f"octet_length({name})<=2048" for name in ("request_id", "workspace_id", "runtime_id"))
            node_bytes = "0"
            if kind != "runtime":
                valid += " AND (node_id IS NULL OR octet_length(node_id)<=2048)"
                node_bytes = "octet_length(COALESCE(node_id,''))"
            valid += f" AND octet_length(request_id)+octet_length(workspace_id)+octet_length(runtime_id)+{node_bytes}<=1024"
            # Candidate discovery needs only these identities. Keep projection
            # and guards within the lookup index keys; runtime scopes imply a
            # zero-length NULL node. Full retained scope truth, including that
            # node and ordinal/kind, is checked by verify() before classification.
            query = "SELECT " + ",".join(f"CASE WHEN {valid} THEN {name}::text END" for name in ("request_id", "workspace_id"))
            query += f",({valid}) FROM cpk_execution_receiver_scopes WHERE {where} LIMIT %s"
            if self.transport.configuration_read is None:
                rows = self.connection.execute(query, (*params, limit)).fetchall()
            else:
                rows = self.transport.configuration_read.query(query, (*params, limit),
                    records=limit, octets=limit * 4096, cells=3)
            self.transport.charge(reserved, rows)
            # Any full prefix refuses before interpretation; a shorter page
            # contains every matching row, irrespective of retrieval order.
            # The cap bounds returned rows, not PostgreSQL's internal scan work.
            _capacity(len(rows) < limit)
            raw += len(rows)
            _capacity(raw <= MAX_CANDIDATE_ROWS)
            for row in rows:
                _require(row[-1] is True and row[1] == workspace_id)
                found.add(row[0])
                _capacity(len(found) <= MAX_REQUESTS)
        return tuple(sorted(found))

    def request(self, workspace_id, request_id):
        from .execution import _execution_request
        rows = self.transport.read("cpk_execution_requests", _columns(_REQUEST),
            "request_id=%s AND workspace_id=%s", (request_id, workspace_id), point=True,
            phase=("request", (request_id,)))
        _require(len(rows) == 1)
        return _execution_request(_decode(rows[0], _REQUEST, int_columns=("claim_generation",),
            time_columns=("requested_at", "claimed_at", "lease_expires_at")))

    def runs(self, request):
        from .execution import _activity_run
        from control_plane_kit_operations.revision_history import validate_retry_predecessor
        rows = self.transport.read("cpk_activity_runs", _columns(_RUN, json_columns=("metadata",),
            ceilings={"metadata": 65536}), "request_id=%s", (request.identity.request_id,),
            order="attempt", maximum=MAX_RUNS - self.run_count, phase=("runs", (request.identity.request_id,)))
        self.run_count += len(rows)
        result = tuple(_activity_run(_decode(row, _RUN, json_columns=("metadata",),
            int_columns=("attempt",), time_columns=("created_at", "started_at", "settled_at"))) for row in rows)
        _require(all(run.plan_id == request.identity.plan_id for run in result))
        _require(tuple(run.retry.attempt for run in result) == tuple(range(1, len(result) + 1)))
        by_id = {run.run_id: run for run in result}
        for run in result:
            _require(run.admission.request_id == request.identity.request_id)
            prior = by_id.get(run.retry.prior_run_id)
            validate_retry_predecessor(prior_run_id=run.retry.prior_run_id, attempt=run.retry.attempt,
                plan_id=run.plan_id, request_id=request.identity.request_id, created_at=run.created_at,
                prior=None if prior is None else (prior.run_id, prior.plan_id, prior.admission.request_id,
                    prior.retry.attempt, prior.created_at))
        return result

    def events(self, run_id):
        from .execution import _activity_event
        rows = self.transport.read("cpk_activity_events", _columns(_EVENT, json_columns=("payload",),
            ceilings={"payload": 65536}), "run_id=%s", (run_id,),
            order="ordinal", maximum=MAX_EVENTS - self.event_count, phase=("events", (run_id,)))
        self.event_count += len(rows)
        decoded = tuple(_decode(row, _EVENT, json_columns=("payload",),
            int_columns=("ordinal",), time_columns=("occurred_at",)) for row in rows)
        self.event_rows.update((row[0], row) for row in decoded)
        return tuple(_activity_event(row) for row in decoded)

    def actions(self, session_id, run_id, kind):
        from .activity_history import _action_record
        _require(kind in ("cancel-run", "advance-current-graph"))
        rows = self.transport.read("cpk_operation_actions", _columns(_ACTION, json_columns=("payload",),
            ceilings={"payload": 65536}),
            "session_id=%s AND payload->>'run_id'=%s AND action_type='" + kind + "'",
            (session_id, run_id), order="action_id", maximum=1, unique=True,
            phase=("advancement-actions", (session_id, run_id)) if kind == "advance-current-graph" else None)
        return tuple(_action_record(_decode(row, _ACTION, json_columns=("payload",),
            int_columns=("ordinal",), time_columns=("created_at",))) for row in rows)

    def effects(self, request, original, run, events):
        from . import effect_attempt_store as attempts_owner
        from . import effect_attempt_intent_store as intents_owner
        from . import effect_outcome_store as outcomes_owner
        from control_plane_kit_core.operations import EffectAttemptStatus
        from control_plane_kit_core.planning import (
            AddSocketConnection, SwitchSocketConnection, RemoveSocketConnection,
            AllocatePublicIngress, RemovePublicIngress, project_activity_journal,
        )
        from control_plane_kit_operations.activity_journal import activity_journal_events

        event_by_id = {event.event_id: event for event in events}
        project_activity_journal(original[0].plan, activity_journal_events(events))
        attempt_names = attempts_owner._COLUMN_NAMES
        rows = self.transport.read("cpk_effect_attempts", _columns(attempt_names),
            "run_id=%s", (run.run_id,), order="activity_id,attempt", maximum=MAX_EFFECT_ROWS - self.effect_count)
        self.effect_count += len(rows)
        attempts = []
        for raw in rows:
            row = _decode(raw, attempt_names, int_columns=("attempt", "fence_generation", "prior_attempt",
                "original_event_ordinal", "latest_event_ordinal"))
            attempts.append(attempts_owner._record_from_events(row, event_by_id[row[15]], event_by_id[row[18]]))

        # This independent PK-prefix read is mandatory even when there are no
        # attempts. An attempt-driven join cannot discover orphan intent rows.
        intent_names = intents_owner._COLUMN_NAMES
        rows = self.transport.read("cpk_effect_attempt_intents",
            _columns(intent_names, byte_columns=("preimage",), ceilings={"preimage": MAX_DOCUMENT_BYTES}),
            "run_id=%s", (run.run_id,), order="activity_id,attempt", maximum=MAX_EFFECT_ROWS - self.effect_count)
        self.effect_count += len(rows)
        intents = []
        for raw in rows:
            row = _decode(raw, intent_names, int_columns=("attempt", "original_event_ordinal"))
            event = self.event_rows[row[6]]
            record = intents_owner._decode_row((*row, event[3], event[4].astimezone(timezone.utc).replace(tzinfo=None), event[5]))
            source = record.intent.source
            _require((record.workspace_id, record.request_id, source.workspace_id, source.request_id,
                      source.run_id.value, source.plan_id, source.base_graph_id, source.desired_graph_id) ==
                     (request.identity.workspace_id, request.identity.request_id, request.identity.workspace_id,
                      request.identity.request_id, run.run_id, original[0].plan_id,
                      original[0].base_graph_id, original[0].desired_graph_id))
            intents.append(record)
        by_attempt = {attempt.state.identity: attempt for attempt in attempts}
        by_intent = {intent.identity: intent for intent in intents}
        _require(len(by_attempt) == len(attempts) and len(by_intent) == len(intents) and set(by_attempt) == set(by_intent))
        starts = {event.event_id for event in events if event.kind.value in (
            "step_started", "step_compensation_started", "step_observation_restarted")}
        attempt_starts = {attempt.original_start_event.event_id for attempt in attempts}
        _require(attempt_starts <= starts)
        activities = {activity.activity_id.value: activity for activity in original[0].plan.activities}
        for event_id in starts - attempt_starts:
            event = event_by_id[event_id]
            activity = activities.get(event.activity_id)
            _require(activity is not None and event.kind.value == "step_started"
                     and type(activity.operation) in (AddSocketConnection, SwitchSocketConnection,
                         RemoveSocketConnection, AllocatePublicIngress, RemovePublicIngress)
                     and "effect_attempt" not in event.evidence.descriptor())
        for identity, attempt in by_attempt.items():
            intent = by_intent[identity]
            _require(intent.original_start_event == attempt.original_start_event
                     and intent.request_fingerprint == attempt.state.request_fingerprint)

        outcome_names = outcomes_owner._COLUMN_NAMES
        rows = self.transport.read("cpk_effect_attempt_outcomes",
            _columns(outcome_names, byte_columns=("preimage",), ceilings={"preimage": 8192}),
            "run_id=%s", (run.run_id,), order="activity_id,attempt", maximum=len(attempts), unique=True)
        outcomes = []
        for raw in rows:
            row = _decode(raw, outcome_names, int_columns=("attempt", "fence_generation", "prior_attempt",
                "original_event_ordinal", "direct_event_ordinal", "observation_count"))
            _require((row[3], row[4]) == (request.identity.workspace_id, request.identity.request_id))
            memberships = self.outcome_memberships(row, outcomes_owner)
            outcome = outcomes_owner._record_from_events(row, outcomes_owner._decode_preimage(row[6], row[5]),
                memberships, event_by_id[row[15]], event_by_id[row[18]])
            identity = outcome.attempt.state.identity
            _require(identity in by_attempt)
            current = by_attempt[identity]
            _require(current.original_start_event == outcome.attempt.original_start_event
                     and current.state.request_fingerprint == outcome.attempt.state.request_fingerprint)
            if current.state.recovery_decision is None:
                _require(current == outcome.attempt)
            else:
                _require(current.state.recovery_decision.uncertain_fingerprint == outcome.attempt.state.outcome_fingerprint)
            outcomes_owner._require_intent_membership(outcome.outcome, outcome.attempt, outcome.endpoint_observations,
                row[3], row[4], by_intent[identity])
            outcomes.append(outcome)
        by_outcome = {outcome.attempt.state.identity: outcome for outcome in outcomes}
        for identity, attempt in by_attempt.items():
            if attempt.state.status is not EffectAttemptStatus.STARTED and attempt.state.recovery_decision is None:
                _require(identity in by_outcome)
        return tuple(attempts), tuple(intents), tuple(outcomes)

    def outcome_memberships(self, outcome_row, owner):
        count = outcome_row[21]
        _require(type(count) is int and 0 <= count <= 8192)
        names = ("position", "observation_count", "observation_id")
        rows = self.transport.read("cpk_effect_attempt_outcome_observations", _columns(names),
            "run_id=%s AND activity_id=%s AND attempt=%s", tuple(outcome_row[:3]), order="position", maximum=count, unique=True)
        _require(len(rows) == count)
        result = []
        for row in rows:
            membership = _decode(row, names, int_columns=("position", "observation_count"))
            observation_names = owner._OBSERVATION_COLUMNS
            observations = self.transport.read("cpk_observations",
                _columns(observation_names, json_columns=("evidence",), ceilings={"evidence": 8192}),
                "observation_id=%s AND workspace_id=%s", (membership[2], outcome_row[3]), point=True, cache=True)
            _require(len(observations) == 1)
            observation = _decode(observations[0], observation_names,
                json_columns=("evidence",), time_columns=("observed_at",))
            result.append((*membership[:2], *observation))
        return tuple(result)

    def compensations(self, request, original, run, events, attempts, intents, outcomes):
        from . import failed_run_compensation_store as owner
        from .failed_run_compensation_attempt_store import _decode as decode_binding
        from .activity_history import _action_record
        from control_plane_kit_core.operations import ActivityEventKind, EffectAttemptIdentity, EffectAttemptStatus
        from control_plane_kit_core.planning import Compensate
        names = ("program_id", "workspace_id", "request_id", "run_id", "plan_id", "session_id",
                 "action_id", "event_id", "actor_id", "reason", "source_failure", "authority_reference_fingerprint",
                 "command_fingerprint", "evidence_fingerprint", "program_fingerprint", "program_preimage", "created_at")
        rows = self.transport.read("cpk_failed_run_compensations",
            _columns(names, json_columns=("source_failure",), byte_columns=("program_preimage",),
                ceilings={"source_failure": 65536, "program_preimage": MAX_DOCUMENT_BYTES}),
            "run_id=%s", (run.run_id,), point=True)
        if not rows:
            _require(not any(attempt.original_start_event.kind is ActivityEventKind.STEP_COMPENSATION_STARTED for attempt in attempts))
            _require(not any(event.kind is ActivityEventKind.RUN_COMPENSATION_STARTED for event in events))
            return (), ()
        row = _decode(rows[0], names, json_columns=("source_failure",), time_columns=("created_at",))
        _require(row[1:6] == (request.identity.workspace_id, request.identity.request_id, run.run_id,
                             original[0].plan_id, request.identity.session_id))
        steps = ("position", "source_run_id", "source_activity_id", "source_attempt", "source_request_fingerprint",
                 "source_outcome_fingerprint", "source_completion_event_id", "source_completion_ordinal", "operation", "material_source")
        raw_steps = self.transport.read("cpk_failed_run_compensation_steps", _columns(steps, json_columns=("operation",)),
            "program_id=%s", (row[0],), order="position", maximum=MAX_EFFECT_ROWS - self.effect_count)
        self.effect_count += len(raw_steps)
        decoded_steps = tuple(_decode(step, steps, json_columns=("operation",),
            int_columns=("position", "source_attempt", "source_completion_ordinal")) for step in raw_steps)
        record, program = owner._records_from_rows(row, decoded_steps)
        event_by_id = {event.event_id: event for event in events}
        event = event_by_id[record.event_id]
        _require(event.kind is ActivityEventKind.RUN_COMPENSATION_STARTED
                 and event.occurred_at == record.created_at
                 and event.evidence.descriptor() == {"program_id": program.program_id, "program_fingerprint": program.fingerprint()})
        action_rows = self.transport.read("cpk_operation_actions", _columns(_ACTION, json_columns=("payload",),
            ceilings={"payload": 65536}), "action_id=%s", (record.action_id,), point=True)
        _require(len(action_rows) == 1)
        action = _action_record(_decode(action_rows[0], _ACTION, json_columns=("payload",),
            int_columns=("ordinal",), time_columns=("created_at",)))
        _require(action.session_id == record.session_id and action.created_at == record.created_at
                 and action.intent_fingerprint == record.command_fingerprint and action.actor_id == record.actor_id
                 and action.action_type.value == "begin-compensation"
                 and action.payload == {"decision": "begin-compensation", "program_id": program.program_id,
                    "program_fingerprint": program.fingerprint(), "run_id": run.run_id, "event_id": event.event_id})
        binding_names = ("program_id", "position", "source_run_id", "source_activity_id", "source_attempt",
                         "inverse_run_id", "inverse_activity_id", "inverse_attempt")
        rows = self.transport.read("cpk_failed_run_compensation_attempt_bindings", _columns(binding_names),
            "program_id=%s", (program.program_id,), order="position", maximum=MAX_EFFECT_ROWS - self.effect_count)
        self.effect_count += len(rows)
        bindings = tuple(decode_binding(_decode(binding, binding_names,
            int_columns=("position", "source_attempt", "inverse_attempt"))) for binding in rows)
        by_attempt = {attempt.state.identity: attempt for attempt in attempts}
        by_intent = {intent.identity: intent for intent in intents}
        by_outcome = {outcome.attempt.state.identity: outcome for outcome in outcomes}
        activities = {activity.activity_id.value: activity for activity in original[0].plan.activities}
        for step in program.steps:
            source = step.source_effect
            activity = activities.get(source.attempt_identity.activity_id)
            _require(activity is not None and type(activity.compensation) is Compensate
                     and step.operation == activity.compensation.operation
                     and step.material_source == activity.compensation.material_source)
            attempt = by_attempt[source.attempt_identity]
            outcome = by_outcome[source.attempt_identity]
            _require(attempt.state.status is EffectAttemptStatus.SUCCEEDED and attempt == outcome.attempt
                     and attempt.latest_transition_event.kind is ActivityEventKind.STEP_SUCCEEDED
                     and source.request_fingerprint == attempt.state.request_fingerprint
                     and source.outcome_fingerprint == attempt.state.outcome_fingerprint
                     and source.completion_event_id == attempt.latest_transition_event.event_id
                     and source.completion_ordinal == attempt.latest_transition_event.ordinal)
        for binding in bindings:
            _require(1 <= binding.position <= len(program.steps))
            step = program.steps[binding.position - 1]
            source = step.source_effect.attempt_identity
            expected = EffectAttemptIdentity(source.run_id, source.activity_id, source.attempt + 1)
            attempt = by_attempt[binding.inverse_attempt]
            _require(binding.source_attempt == source and binding.inverse_attempt == expected
                     and attempt.state.prior_attempt == source
                     and attempt.original_start_event.kind is ActivityEventKind.STEP_COMPENSATION_STARTED
                     and by_intent[expected].intent == replace(by_intent[source].intent, operation=step.operation))
        _require({binding.inverse_attempt for binding in bindings} ==
                 {attempt.state.identity for attempt in attempts
                  if attempt.original_start_event.kind is ActivityEventKind.STEP_COMPENSATION_STARTED})
        return ((record, program),), bindings

    def evidence(self, workspace_id, requested_scopes, guard):
        from control_plane_kit_core.operations import ActivityEventKind, ActivityRunStatus
        from control_plane_kit_operations.receiver_execution_scopes import ReceiverScopeEvidence, _RequestEvidence, _RunEvidence
        from control_plane_kit_operations.revision_history import historical_advancement
        from control_plane_kit_operations.advancement import _require_complete_success
        from control_plane_kit_operations.lifecycle import _require_historical_cancellation_envelope
        from control_plane_kit_operations._execution_lease_recovery_support import _historical_recovery_journal
        from .temporal import decode_postgres_cursor_timestamp, encode_postgres_timestamp
        self.guard(workspace_id, guard)
        candidates = []
        for request_id in self.candidates(workspace_id, requested_scopes):
            request = self.request(workspace_id, request_id)
            original, _ = self.verify(request.identity)
            retained = []
            plan, base, desired = original
            for run in self.runs(request):
                events = self.events(run.run_id)
                attempts, intents, outcomes = self.effects(request, original, run, events)
                programs, bindings = self.compensations(request, original, run, events, attempts, intents, outcomes)
                if run.status is ActivityRunStatus.CANCELLED:
                    _require(bool(events) and events[-1].kind is ActivityEventKind.RUN_CANCELLED)
                    historical = _historical_recovery_journal(events)
                    _require(historical is not None)
                    _require(all(encode_postgres_timestamp(left.occurred_at) <= encode_postgres_timestamp(right.occurred_at)
                                 for left, right in zip(events, events[1:])))
                    _require_historical_cancellation_envelope(run, historical[0])
                forward = {activity.activity_id.value: activity.operation for activity in plan.plan.activities}
                inverse_ids = {binding.inverse_attempt for binding in bindings}
                for intent in intents:
                    if intent.identity not in inverse_ids:
                        _require(intent.intent.operation == forward[intent.identity.activity_id])
                # The existing historical receipt language uses six-digit UTC
                # instants. Normalize both sides without consulting today's lease.
                accepted_events = tuple(replace(event, occurred_at=decode_postgres_cursor_timestamp(
                    encode_postgres_timestamp(event.occurred_at))) for event in events
                    if event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED)
                accepted_actions = tuple(replace(action, created_at=decode_postgres_cursor_timestamp(
                    encode_postgres_timestamp(action.created_at))) for action in
                    self.actions(request.identity.session_id, run.run_id, "advance-current-graph"))
                advancement = historical_advancement(workspace_id=workspace_id, session_id=request.identity.session_id,
                    plan_id=plan.plan_id, plan={"base_graph_id": plan.base_graph_id,
                        "base_realized_projection_id": base.projection_id, "desired_graph_id": plan.desired_graph_id,
                        "desired_realized_projection_id": desired.projection_id, "desired_graph_revision": plan.desired_graph_revision},
                    request_id=request_id, run_id=run.run_id, projection_digest=desired.projection_digest,
                    events=accepted_events, actions=accepted_actions)
                _require(advancement["state"] != "unavailable")
                if advancement["state"] == "accepted":
                    # Reuse the owner's success law on the journal that existed
                    # immediately before this historical acceptance. The receipt
                    # itself was appended afterward and is not a success event.
                    accepted_ordinal = accepted_events[0].ordinal
                    _require_complete_success(plan.plan, run,
                        tuple(event for event in events if event.ordinal < accepted_ordinal))
                retained.append(_RunEvidence(run, events, attempts, intents, outcomes, programs, bindings,
                    self.actions(request.identity.session_id, run.run_id, "cancel-run"), advancement))
            known_attempts = {attempt.state.identity for item in retained for attempt in item.attempts}
            _require(all(attempt.state.prior_attempt is None or attempt.state.prior_attempt in known_attempts
                         for item in retained for attempt in item.attempts))
            candidates.append(_RequestEvidence(request, original, tuple(retained)))
        return ReceiverScopeEvidence("complete", tuple(candidates))


def read_receiver_scope_evidence(connection, workspace_id, requested_scopes, guard):
    from psycopg import Error
    from control_plane_kit_operations.advancement import CurrentGraphAdvancementError
    from control_plane_kit_operations.receiver_execution_scopes import ReceiverScopeEvidence
    from .configuration_evidence import _active_read
    try:
        return _ExecutionScopeStorage(connection, _active_read(connection)).evidence(workspace_id, requested_scopes, guard)
    except ReceiverScopeCapacity:
        return ReceiverScopeEvidence("capacity")
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError, RecursionError, Error, CurrentGraphAdvancementError):
        return ReceiverScopeEvidence("unavailable")


def _require_nonaffecting_intent(connection, record):
    from control_plane_kit_operations.receiver_execution_scopes import _effect_receiver_scope
    from control_plane_kit_core.operations import ActivityEventKind
    reader = _ExecutionScopeStorage(connection)
    request = reader.request(record.workspace_id, record.request_id)
    original, _ = reader.verify(request.identity)
    rows = reader.transport.read("cpk_activity_runs", _columns(("request_id", "plan_id")),
        "run_id=%s", (record.identity.run_id.value,), point=True)
    _require(rows == ((request.identity.request_id, request.identity.plan_id),))
    scope, _, _ = _effect_receiver_scope(request.identity, original, record.intent,
        compensation=record.original_start_event.kind is ActivityEventKind.STEP_COMPENSATION_STARTED)
    _require(scope is None)


def validate_current_rows(connection):
    """Walk every request with an independent whole-original per-request budget."""
    from control_plane_kit_operations.records import ExecutionRequestIdentity
    last = ""
    while True:
        # Keyset batches are an iteration mechanism, not the online lifetime
        # candidate ceiling. Every identity receives its own complete verifier.
        batch = _Transport(connection).read("cpk_execution_requests",
            _columns(("request_id", "workspace_id", "session_id", "plan_id")),
            "request_id>%s", (last,), order="request_id", maximum=64, page=True)
        for row in batch:
            _ExecutionScopeStorage(connection).verify(ExecutionRequestIdentity(*row))
        if len(batch) < 64:
            return
        last = batch[-1][0]
