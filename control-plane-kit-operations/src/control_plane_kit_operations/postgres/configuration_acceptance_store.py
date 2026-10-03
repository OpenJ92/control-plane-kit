"""Original accepted membership in the advancement owner's transaction."""
from hashlib import sha256
from dataclasses import replace
from functools import wraps
import json

import rfc8785

from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations._configuration_acceptance import _PreparedAdvancementReceipt, _require_prepared_advancement, _history_records
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.revision_history import historical_advancement
from control_plane_kit_operations.records import OperationsRecordError
from .configuration_evidence import _EvidenceRead, _Unavailable, _Capacity
from .activity_history import PostgresActivityHistoryStore, _action_record
from .execution import PostgresExecutionStore, _activity_event
from .graph_store import PostgresGraphTopologyStore, PostgresRealizedGraphProjectionStore, PostgresWorkspaceStore


_EMPTY_DIGEST = sha256(rfc8785.dumps([])).hexdigest()
_HEADER = ("workspace_id", "pinned_revision", "graph_id", "projection_id", "projection_digest",
    "action_id", "event_id", "run_id", "request_id", "plan_id", "slot_count", "slot_digest")
_ACTION = ("action_id", "session_id", "ordinal", "action_type", "actor_id", "payload", "created_at",
    "idempotency_key", "intent_fingerprint")
_EVENT = ("event_id", "run_id", "ordinal", "event_type", "occurred_at", "payload")
_LOCATOR = ("advancement_workspace_id", "advancement_request_id", "advancement_plan_id", "advancement_revision")


def _columns(names):
    return tuple((name, "json" if name == "payload" else "time" if name in ("created_at", "occurred_at")
        else "int" if name in ("ordinal", "pinned_revision", "advancement_revision", "slot_count") else "text",
        65536 if name == "payload" else 2048) for name in names)


def _closed_evidence(method):
    @wraps(method)
    def read(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (_Capacity, _Unavailable):
            raise
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            raise _Unavailable from None
    return read


class ConfigurationAcceptanceStore:
    def __init__(self, connection):
        self._connection = connection
        self._issued = None

    def _require_issued(self, prepared):
        if (self._issued is not prepared or prepared.stores.configuration_acceptance is not self
                or prepared.stores.connection is not self._connection):
            raise OperationsRecordError("advancement requires owner-issued preparation")

    def _bind_records(self, prepared, event, action):
        self._require_issued(prepared)
        if prepared.event is not None or prepared.action is not None:
            raise OperationsRecordError("advancement original records are already bound")
        prepared.stores.graphs._require_receiver_lifecycle(prepared.guard, prepared.workspace.workspace_id)
        prepared._validate_records(event, action)
        bound = replace(prepared, event=event, action=action)
        self._issued = bound
        return bound

    def _originals(self, read, action_id, event_id):
        action_names = _ACTION + _LOCATOR + ("advancement_run_id",)
        event_names = _EVENT + _LOCATOR
        actions = read.bounded_rows("cpk_operation_actions", _columns(action_names), "action_id=%s", (action_id,))
        events = read.bounded_rows("cpk_activity_events", _columns(event_names), "event_id=%s", (event_id,))
        if len(actions) != 1 or len(events) != 1:
            raise _Unavailable
        payload = events[0][5]
        if type(payload) is not dict or set(payload) != {"activity_id", "evidence", "failure", "recovery"}:
            raise _Unavailable
        return _action_record(actions[0][:9]), _activity_event(events[0][:6]), actions[0][9:], events[0][6:]

    def _zero_projection(self, graph_id, projection_id, workspace_id):
        graph = PostgresGraphTopologyStore(self._connection).get(graph_id)
        projection = PostgresRealizedGraphProjectionStore(self._connection).get(projection_id)
        if (graph.workspace_id != workspace_id or projection.workspace_id != workspace_id
                or projection.source_authored_graph_id != graph_id):
            raise _Unavailable
        material = DEFAULT_GRAPH_CODEC.decode(projection.graph_descriptor)
        # This initial B2 slice supports the reviewed runtime-only zero-slot
        # path. Never turn unimplemented node material into an empty manifest.
        if material.nodes:
            raise _Unavailable
        return projection

    @_closed_evidence
    def _receipt(self, workspace_id, revision):
        read = _EvidenceRead(self._connection)
        rows = read.bounded_rows("cpk_configuration_acceptances", _columns(_HEADER),
            "workspace_id=%s AND pinned_revision=%s", (workspace_id, revision))
        if len(rows) != 1:
            raise _Unavailable
        header = dict(zip(_HEADER, rows[0]))
        if header["slot_count"] != 0 or header["slot_digest"] != _EMPTY_DIGEST:
            raise _Unavailable
        slots = read.bounded_rows("cpk_configuration_accepted_slots",
            (("runtime_id", "text", 2048), ("node_id", "text", 2048), ("artifact_id", "text", 2048)),
            "workspace_id=%s AND pinned_revision=%s", (workspace_id, revision),
            maximum=256, point=False, order="runtime_id,node_id,artifact_id")
        if slots:
            raise _Unavailable
        action, event, action_locator, event_locator = self._originals(read, header["action_id"], header["event_id"])
        expected = (workspace_id, header["request_id"], header["plan_id"], revision)
        if event_locator != expected or action_locator != expected + (header["run_id"],):
            raise _Unavailable
        execution, history = PostgresExecutionStore(self._connection), PostgresActivityHistoryStore(self._connection)
        run = execution.get_run(header["run_id"])
        request = execution.get_request(header["request_id"])
        plan = history.get_plan(header["plan_id"])
        session = history.get_session(plan.session_id)
        if (run.admission.request_id != request.identity.request_id or run.plan_id != plan.plan_id
                or request.identity.workspace_id != workspace_id or request.identity.plan_id != plan.plan_id
                or request.identity.session_id != plan.session_id or session.workspace_id != workspace_id
                or plan.desired_graph_revision != revision or plan.desired_graph_id != header["graph_id"]
                or plan.desired_realized_projection_id != header["projection_id"]):
            raise _Unavailable
        projection = self._zero_projection(header["graph_id"], header["projection_id"], workspace_id)
        if projection.projection_digest != header["projection_digest"]:
            raise _Unavailable
        historical_event, historical_action = _history_records(event, action)
        receipt = historical_advancement(workspace_id=workspace_id, session_id=plan.session_id,
            plan_id=plan.plan_id, plan={name: getattr(plan, name) for name in (
                "base_graph_id", "base_realized_projection_id", "desired_graph_id",
                "desired_realized_projection_id", "desired_graph_revision")},
            request_id=request.identity.request_id, run_id=run.run_id,
            projection_digest=projection.projection_digest, events=(historical_event,), actions=(historical_action,))
        if receipt["state"] != "accepted":
            raise _Unavailable
        return header, action, event

    def _latest(self, read, *, action, workspace_id):
        table, key, kind, value = (("cpk_operation_actions", "action_id", "action_type", "advance-current-graph")
            if action else ("cpk_activity_events", "event_id", "event_type", "current_graph_advanced"))
        names = (key, *_LOCATOR[:3], "advancement_run_id" if action else "run_id")
        selected = ",".join(f"CASE WHEN octet_length({name}) BETWEEN 1 AND 2048 THEN {name} END" for name in names)
        rows = read.query(f"SELECT {selected},advancement_revision FROM {table} "
            f"WHERE advancement_workspace_id=%s AND {kind}=%s ORDER BY advancement_revision DESC,{key} LIMIT 2",
            (workspace_id, value), records=2, octets=2 * (5 * 2048 + 20), cells=6)
        if any(any(value is None for value in row) or type(row[-1]) is not int or row[-1] < 0 for row in rows):
            raise _Unavailable
        if len(rows) == 2 and rows[0][-1] == rows[1][-1]:
            raise _Unavailable
        return rows[0] if rows else None

    def _current_zero(self, workspace):
        read = _EvidenceRead(self._connection)
        action = self._latest(read, action=True, workspace_id=workspace.workspace_id)
        event = self._latest(read, action=False, workspace_id=workspace.workspace_id)
        if action is None and event is None:
            origin = PostgresWorkspaceStore(self._connection)._require_workspace_initialization(workspace.workspace_id)
            if (workspace.current_graph_id, workspace.current_realized_projection_id) != (
                    origin.initial_graph_id, origin.initial_projection_id):
                raise _Unavailable
            return
        if action is None or event is None or action[1:] != event[1:]:
            raise _Unavailable
        header, _, _ = self._receipt(workspace.workspace_id, action[-1])
        if (header["action_id"], header["event_id"], header["graph_id"], header["projection_id"]) != (
                action[0], event[0], workspace.current_graph_id, workspace.current_realized_projection_id):
            raise _Unavailable

    @_closed_evidence
    def _prepare(self, stores, workspace, request, run, plan, guard, current_projection, desired_projection):
        from control_plane_kit_operations.advancement import _require_complete_success
        stores.graphs._require_receiver_lifecycle(guard, workspace.workspace_id)
        if (stores.connection is not self._connection or request.identity.workspace_id != workspace.workspace_id
                or request.identity.plan_id != plan.plan_id or run.admission.request_id != request.identity.request_id
                or run.plan_id != plan.plan_id or request.identity.session_id != plan.session_id
                or (workspace.current_graph_id, workspace.current_realized_projection_id,
                    workspace.desired_graph_id, workspace.desired_realized_projection_id, workspace.desired_graph_revision)
                != (plan.base_graph_id, plan.base_realized_projection_id, plan.desired_graph_id,
                    plan.desired_realized_projection_id, plan.desired_graph_revision)):
            raise _Unavailable
        _require_complete_success(plan.plan, run, stores.execution.events_for_run(run.run_id))
        self._current_zero(workspace)
        if (self._zero_projection(plan.base_graph_id, plan.base_realized_projection_id, workspace.workspace_id) != current_projection
                or self._zero_projection(plan.desired_graph_id, plan.desired_realized_projection_id, workspace.workspace_id) != desired_projection):
            raise _Unavailable
        prepared = _PreparedAdvancementReceipt(stores, guard, workspace, request, run, plan,
            current_projection, desired_projection)
        self._issued = prepared
        return prepared

    def _insert(self, prepared):
        workspace_id = prepared.workspace.workspace_id
        _require_prepared_advancement(prepared, self._connection, workspace_id, after_cas=True)
        values = (workspace_id, prepared.plan.desired_graph_revision, prepared.plan.desired_graph_id,
            prepared.desired_projection.projection_id, prepared.desired_projection.projection_digest,
            prepared.action.action_id, prepared.event.event_id, prepared.run.run_id,
            prepared.request.identity.request_id, prepared.plan.plan_id, 0, _EMPTY_DIGEST)
        _EvidenceRead(self._connection).query("INSERT INTO cpk_configuration_acceptances (" + ",".join(_HEADER)
            + ") VALUES (" + ",".join("%s" for _ in _HEADER) + ") RETURNING 1",
            values, records=1, octets=1, cells=1)
        # Verify actual stored bytes as well as the pre-CAS consumer preflight.
        header, action, event = self._receipt(workspace_id, prepared.plan.desired_graph_revision)
        if tuple(header[name] for name in _HEADER) != values or action != prepared.action or event != prepared.event:
            raise _Unavailable

    def _preflight(self, prepared):
        """Admit the generated pair and publication envelope before first write."""
        from control_plane_kit_operations.configuration_preparation import (
            ConfigurationEvidenceFootprint, ConfigurationCapacityDecision, configuration_evidence_capacity,
        )
        read = _EvidenceRead(self._connection)
        action, event = prepared.action, prepared.event
        texts = (action.action_id, action.session_id, action.actor_id, action.idempotency_key,
            action.intent_fingerprint, event.event_id, event.run_id, prepared.workspace.workspace_id,
            prepared.request.identity.request_id, prepared.plan.plan_id, prepared.plan.desired_graph_id,
            prepared.desired_projection.projection_id, prepared.desired_projection.projection_digest)
        if any(type(value) is not str or not 1 <= len(value.encode("utf-8")) <= 2048 for value in texts):
            raise _Capacity
        payload = {"activity_id": event.activity_id, "evidence": event.evidence.descriptor(),
            "failure": None, "recovery": None}
        # Consumers measure PostgreSQL JSON text, which is not JCS size.
        sizes = read.query("SELECT octet_length((%s::jsonb)::text),octet_length((%s::jsonb)::text)",
            (json.dumps(action.payload), json.dumps(payload)), records=1, octets=24, cells=2)
        if any(type(size) is not int or not 1 <= size <= 65536 for size in sizes[0]):
            raise _Capacity
        # Runtime-only publication rechecks <=1MiB each graph descriptor, graph
        # metadata, projection and plan, plus bounded owner rows, pair and header.
        # Reserve a conservative
        # 8MiB value envelope plus the row/scalar/statement cost for the remaining
        # CAS, private guard checks, writes and full consumer verification. This
        # checks future capacity; actual queries continue charging the same ledger.
        future = ConfigurationEvidenceFootprint(512, 8 * 1024 * 1024, 8192, 128)
        if configuration_evidence_capacity(read.used.plus(future)) is not ConfigurationCapacityDecision.WITHIN_LIMITS:
            raise _Capacity

    def _verify_replay(self, result):
        header, action, event = self._receipt(result.workspace_id, result.desired_graph_revision)
        if action != result.action or event != result.event:
            raise _Unavailable


def validate_configuration_advancement_rows(connection):
    """Bounded original-side scans; no backfill, adoption or repair."""
    store = ConfigurationAcceptanceStore(connection)
    for table, key, kind, value in (("cpk_operation_actions", "action_id", "action_type", "advance-current-graph"),
            ("cpk_activity_events", "event_id", "event_type", "current_graph_advanced")):
        cursor = ""
        while True:
            rows = _EvidenceRead(connection, standalone=True).bounded_rows(table,
                _columns((key, "advancement_workspace_id", "advancement_revision")),
                f"{key}>%s AND {kind}=%s", (cursor, value), maximum=32, order=key)
            if not rows:
                break
            for identity, workspace_id, revision in rows:
                with _configuration_accounting(("configuration-verification", table, identity)):
                    header, _, _ = store._receipt(workspace_id, revision)
                    if header[key] != identity:
                        raise _Unavailable
            cursor = rows[-1][0]
    # Also discover headers independently: a header pointing at unrelated
    # history must not evade the original-kind scans above.
    cursor = ("", -1)
    previous_workspace = None
    while True:
        rows = _EvidenceRead(connection, standalone=True).bounded_rows("cpk_configuration_acceptances",
            _columns(("workspace_id", "pinned_revision")), "(workspace_id,pinned_revision)>(%s,%s)",
            cursor, maximum=32, order="workspace_id,pinned_revision")
        if not rows:
            return
        for workspace_id, revision in rows:
            with _configuration_accounting(("configuration-verification", workspace_id, revision)):
                store._receipt(workspace_id, revision)
                if workspace_id != previous_workspace:
                    store._current_zero(PostgresWorkspaceStore(connection).get(workspace_id))
                    previous_workspace = workspace_id
        cursor = rows[-1]
