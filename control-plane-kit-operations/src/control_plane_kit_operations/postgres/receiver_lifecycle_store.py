"""Internal graph-owned receiver persistence in the caller's transaction."""

from dataclasses import astuple, fields
from datetime import datetime
import json

from psycopg import InterfaceError, OperationalError

from control_plane_kit_operations.receiver_lifecycle import (
    ReceiverBinding, ReceiverIntroduction, ReceiverLifecycleStorageConflict,
    ReceiverLifecycleStorageError, _require, _text, derive_receiver_bindings,
)
from control_plane_kit_operations.records import GraphVersionRecord, RealizedGraphProjectionRecord
from control_plane_kit_operations.postgres.temporal import decode_postgres_timestamp


_INTRO = "cpk_graph_receiver_introductions"
_BIND = "cpk_graph_receiver_bindings"
_INTRO_COLUMNS = tuple(field.name for field in fields(ReceiverIntroduction))
_BIND_COLUMNS = tuple(field.name for field in fields(ReceiverBinding))
_GRAPH_COLUMNS = ("graph_id", "workspace_id", "version", "graph_descriptor", "created_by", "created_at", "metadata")
_PROJECTION_COLUMNS = ("projection_id", "workspace_id", "source_authored_graph_id", "projection_kind",
                       "projection_key", "projection_digest", "graph_descriptor", "created_by", "created_at")
_JSON = frozenset(("graph_descriptor", "metadata"))
_NON_TEXT = _JSON | {"version", "created_at"}


def _select(table, columns):
    # Bound transport before material enters Python, including corrupt retained
    # rows. Predicate/keyset parameters are independently bounded by callers.
    checks = [f"({name} IS NULL OR octet_length({name}{'::text' if name in _JSON else ''}) <= "
              f"{1048576 if name in _JSON else 2048})" for name in columns
              if name not in _NON_TEXT or name in _JSON]
    valid = " AND ".join(checks)
    return "SELECT " + ", ".join(f"CASE WHEN {valid} THEN {name} ELSE NULL END" for name in columns) + \
        f", ({valid}) FROM {table} "


def _decode(row, constructor):
    _require(row is not None and row[-1] is True)
    try:
        return constructor(*row[:-1])
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
        failure = ReceiverLifecycleStorageError("receiver storage is unavailable")
    raise failure


def _conflict():
    raise ReceiverLifecycleStorageConflict("receiver identity is unavailable")


def _validate_receiver_origin_action(origin, action, graph, projection):
    """Shared pure attribution law; return a draft revision needing a witness."""
    from control_plane_kit_core.operations.commands import OperatorCommandKind
    from control_plane_kit_operations.receiver_lifecycle import ReceiverLifecycleExpectation

    _require(action.action_id == origin.introducing_action_id
             and action.session_id == origin.introducing_session_id)
    payload = dict(action.payload)
    _require(payload.get("workspace_id") == origin.workspace_id)
    _require(type(payload.get("receiver_lifecycle")) is dict)
    ReceiverLifecycleExpectation(**payload.pop("receiver_lifecycle"))
    _require(graph.workspace_id == projection.workspace_id == origin.workspace_id
             and projection.source_authored_graph_id == graph.graph_id)
    if action.action_type is OperatorCommandKind.SET_DESIRED_GRAPH:
        _require(set(payload) == {"workspace_id", "previous_desired_graph_id", "desired_graph_id",
            "desired_realized_projection_id", "desired_graph_revision", "product_references"})
        _require(payload["desired_graph_id"] == graph.graph_id
                 and payload["desired_realized_projection_id"] == projection.projection_id
                 and origin.introducing_draft_id is None)
        _require(projection == RealizedGraphProjectionRecord.identity_for_authored(authored_record=graph))
    elif action.action_type in (OperatorCommandKind.CREATE_DESIRED_TOPOLOGY_DRAFT,
                                OperatorCommandKind.REVISE_DESIRED_TOPOLOGY_DRAFT):
        _require(set(payload) == {"workspace_id", "draft_id", "revision", "graph_id"})
        _require(payload["graph_id"] == graph.graph_id
                 and payload["draft_id"] == origin.introducing_draft_id)
        _require(type(payload["revision"]) is int and 0 < payload["revision"] <= 9_223_372_036_854_775_807)
        _require(projection == RealizedGraphProjectionRecord.identity_for_authored(authored_record=graph))
        return payload["revision"]
    elif action.action_type is OperatorCommandKind.PUBLISH_DESIRED_REALIZED_PROJECTION:
        _require(set(payload) == {"workspace_id", "authored_graph_id", "previous_realized_projection_id",
            "desired_realized_projection_id", "desired_realized_projection_digest", "desired_graph_revision",
            "projection_kind", "projection_key", "source_operation_id", "source_operation_version"})
        _require(payload["authored_graph_id"] == graph.graph_id
                 and payload["desired_realized_projection_id"] == projection.projection_id
                 and payload["desired_realized_projection_digest"] == projection.projection_digest
                 and origin.introducing_draft_id is None)
    else:
        _require(False)
    return None


class _ReceiverAuthoringSnapshot:
    """Fixed graph selectors sharing a pre-fetch transport ledger and snapshot.

    SQL relation/column fragments below are private constants, never caller
    inputs. Length probes precede text transport. Material guards repeat both
    cell and aggregate bounds in SQL; no rejected cell crosses the cursor.
    """

    def __init__(self, connection):
        self._connection = connection
        self._statements = 1024
        self._rows_left = 4096
        self._bytes_left = 8_388_608
        self._graphs = {}
        self._materials = {}

    def _reserve(self, rows, size):
        _require(self._statements > 0 and rows <= self._rows_left and size <= self._bytes_left)
        self._statements -= 1
        self._rows_left -= rows
        self._bytes_left -= size

    def _fetch(self, relation, columns, predicate, params, *, maximum=1, order="1", caps=None):
        caps = caps or (2048,) * len(columns)
        _require(len(columns) <= 32 and len(caps) == len(columns))
        limit = maximum + 1
        selection = ",".join(f"{column}::text AS v{n}" for n, column in enumerate(columns))
        candidate = f"SELECT {selection} FROM {relation} WHERE {predicate} ORDER BY {order} LIMIT {limit}"
        lengths = ",".join(f"octet_length(v{n})" for n in range(len(columns)))
        # PostgreSQL octet_length returns int4: at most ten decimal bytes per
        # probe scalar; 512 bytes also covers all 32 null/int cells per row.
        self._reserve(limit, limit * 512)
        probes = self._connection.execute(
            f"WITH candidate AS ({candidate}) SELECT {lengths} FROM candidate", params,
        ).fetchall()
        probe_bytes = sum(len(str(value).encode("utf-8")) for row in probes for value in row if value is not None)
        self._rows_left += limit - len(probes)
        self._bytes_left += limit * 512 - probe_bytes
        _require(len(probes) <= maximum)
        if not probes:
            return ()
        _require(all(value is None or 0 <= value <= caps[n]
                     for row in probes for n, value in enumerate(row)))
        totals = [sum(row[n] or 0 for row in probes) for n in range(len(columns))]
        checks = [f"count(*) OVER () = {len(probes)}"]
        for n, cap in enumerate(caps):
            checks.extend((f"(v{n} IS NULL OR octet_length(v{n}) <= {cap})",
                           f"coalesce(sum(octet_length(v{n})) OVER (),0) <= {totals[n]}"))
        guard = " AND ".join(checks)
        safe = ",".join(f"CASE WHEN valid THEN v{n} END" for n in range(len(columns)))
        # Reserve the entire bounded result before the material query, including
        # its boolean guards. The transaction's snapshot makes probes stable.
        reserved = sum(totals) + limit
        self._reserve(limit, reserved)
        rows = self._connection.execute(
            f"WITH candidate AS ({candidate}), guarded AS (SELECT *,({guard}) AS valid FROM candidate) "
            f"SELECT {safe},valid FROM guarded", params,
        ).fetchall()
        actual = sum(1 if type(value) is bool else len(value.encode("utf-8"))
                     for row in rows for value in row if value is not None)
        self._rows_left += limit - len(rows)
        self._bytes_left += reserved - actual
        _require(len(rows) == len(probes) and all(row[-1] is True for row in rows))
        return tuple(row[:-1] for row in rows)

    def _one(self, *args, missing="unavailable", **kwargs):
        from control_plane_kit_operations.read_services.receiver_authoring_context import ReceiverAuthoringContextError
        rows = self._fetch(*args, **kwargs)
        if not rows:
            raise ReceiverAuthoringContextError(missing)
        return rows[0]

    def _witness(self, relation, predicate, params):
        self._reserve(1, 1)
        row = self._connection.execute(
            f"SELECT EXISTS(SELECT 1 FROM {relation} WHERE {predicate})", params,
        ).fetchone()
        _require(row == (True,))

    def pins(self, workspace_id):
        from control_plane_kit_operations.receiver_lifecycle import ReceiverLifecycleExpectation
        row = self._one("cpk_workspaces", ("current_graph_id", "current_realized_projection_id",
            "desired_graph_id", "desired_realized_projection_id", "desired_graph_revision"),
            "workspace_id=%s", (workspace_id,), missing="missing")
        return ReceiverLifecycleExpectation(*row[:4], int(row[4]))

    def draft_head(self, workspace_id, draft_id, revision):
        from control_plane_kit_operations.read_services.receiver_authoring_context import ReceiverAuthoringContextError
        head, deleted = self._one("cpk_desired_topology_drafts", ("head_revision", "deleted_at"),
            "workspace_id=%s AND draft_id=%s", (workspace_id, draft_id), missing="missing")
        if deleted is not None:
            raise ReceiverAuthoringContextError("missing")
        if int(head) != revision:
            raise ReceiverAuthoringContextError("stale")
        return self._one("cpk_desired_topology_draft_revisions", ("graph_id",),
            "workspace_id=%s AND draft_id=%s AND revision=%s", (workspace_id, draft_id, revision))[0]

    def material(self, workspace_id, graph_id, projection_id):
        from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
        from control_plane_kit_operations.read_services.receiver_authoring_context import ReceiverAuthoringMaterial
        from control_plane_kit_operations.records import RealizedGraphProjectionKind
        key = (workspace_id, graph_id, projection_id)
        if key in self._materials:
            return self._materials[key]
        graph = self._graphs.get((workspace_id, graph_id))
        if graph is None:
            graph_values = list(self._one("cpk_graph_versions", _GRAPH_COLUMNS[:-1],
                "workspace_id=%s AND graph_id=%s", (workspace_id, graph_id),
                caps=(2048, 2048, 2048, 1048576, 2048, 2048)))
            graph_values[2] = int(graph_values[2])
            graph_values[3] = json.loads(graph_values[3])
            graph_values[5] = decode_postgres_timestamp(datetime.fromisoformat(graph_values[5]))
            graph = GraphVersionRecord(*graph_values)
            _require(DEFAULT_GRAPH_CODEC.encode(DEFAULT_GRAPH_CODEC.decode(graph.graph_descriptor))
                     == graph.graph_descriptor)
            self._graphs[(workspace_id, graph_id)] = graph
        predicate = "workspace_id=%s AND source_authored_graph_id=%s AND "
        params = (workspace_id, graph_id)
        if projection_id is None:
            predicate += "projection_kind='identity' AND projection_key='identity'"
        else:
            predicate += "projection_id=%s"
            params += (projection_id,)
        projected = self._fetch("cpk_realized_graph_projections", _PROJECTION_COLUMNS, predicate, params,
            caps=(2048, 2048, 2048, 2048, 2048, 2048, 1048576, 2048, 2048))
        if not projected:
            # Legacy receiver-free drafts have no persisted identity projection.
            # Validate their graph but do not invent or save a projection ID.
            _require(projection_id is None)
            _require(not derive_receiver_bindings(workspace_id, graph_id, "unassigned", graph.graph_descriptor))
            result = ReceiverAuthoringMaterial(graph, None, ())
        else:
            values = list(projected[0])
            values[3] = RealizedGraphProjectionKind(values[3])
            values[6] = json.loads(values[6])
            values[8] = decode_postgres_timestamp(datetime.fromisoformat(values[8]))
            projection = RealizedGraphProjectionRecord(*values)
            if projection.projection_kind is RealizedGraphProjectionKind.IDENTITY:
                _require(projection == RealizedGraphProjectionRecord.identity_for_authored(authored_record=graph))
            expected = derive_receiver_bindings(workspace_id, graph_id, projection.projection_id,
                                               projection.graph_descriptor)
            _require(len(expected) <= 64)
            rows = self._fetch(_BIND, _BIND_COLUMNS,
                "workspace_id=%s AND graph_id=%s AND realized_projection_id=%s",
                (workspace_id, graph_id, projection.projection_id), maximum=64,
                order="node_id,provider_socket_name")
            bindings = tuple(sorted((ReceiverBinding(*row) for row in rows),
                                    key=lambda item: (item.node_id, item.provider_socket_name)))
            _require(bindings == expected)
            result = ReceiverAuthoringMaterial(graph, projection, bindings)
            self._materials[(workspace_id, graph_id, projection.projection_id)] = result
            if projection.projection_kind is RealizedGraphProjectionKind.IDENTITY:
                self._materials[(workspace_id, graph_id, None)] = result
        self._materials[key] = result
        return result

    def origin(self, workspace_id, receiver_id):
        from control_plane_kit_core.operations.commands import OperatorCommandKind
        from control_plane_kit_operations.receiver_lifecycle import _receiver_scope
        from control_plane_kit_operations.records import OperationActionRecord
        origin = ReceiverIntroduction(*self._one(_INTRO, _INTRO_COLUMNS,
            "workspace_id=%s AND receiver_id=%s", (workspace_id, receiver_id)))
        original = self.material(workspace_id, origin.introducing_graph_id,
                                 origin.introducing_realized_projection_id)
        _require(any(_receiver_scope(item) == _receiver_scope(origin) for item in original.bindings))
        values = list(self._one("cpk_operation_actions a JOIN cpk_operation_sessions s ON s.session_id=a.session_id",
            ("a.action_id", "a.session_id", "a.ordinal", "a.action_type", "a.actor_id", "a.payload",
             "a.created_at", "a.idempotency_key", "a.intent_fingerprint"),
            "a.action_id=%s AND a.session_id=%s AND s.workspace_id=%s",
            (origin.introducing_action_id, origin.introducing_session_id, workspace_id),
            caps=(2048, 2048, 2048, 2048, 2048, 65536, 2048, 2048, 2048)))
        values[2] = int(values[2])
        values[3] = OperatorCommandKind(values[3])
        values[5] = json.loads(values[5])
        values[6] = decode_postgres_timestamp(datetime.fromisoformat(values[6]))
        revision = _validate_receiver_origin_action(origin, OperationActionRecord(*values),
                                                    original.graph, original.projection)
        if revision is not None:
            self._witness("cpk_desired_topology_draft_revisions",
                "workspace_id=%s AND draft_id=%s AND revision=%s AND graph_id=%s",
                (workspace_id, origin.introducing_draft_id, revision, origin.introducing_graph_id))
        if origin.first_accepted_action_id is not None:
            self._witness("cpk_operation_actions a JOIN cpk_operation_sessions s ON s.session_id=a.session_id",
                "a.action_id=%s AND a.session_id=%s AND s.workspace_id=%s",
                (origin.first_accepted_action_id, origin.first_accepted_session_id, workspace_id))
        return origin


class _ReceiverStorage:
    def __init__(self, connection):
        self.connection = connection

    def guard(self, owner, guard, workspace):
        _require(owner.owns_receiver_lifecycle(guard, workspace))
        try:
            from .configuration_evidence import _active_read
            read = _active_read(self.connection)
            row = (self.connection.execute("SELECT txid_current()").fetchone() if read is None else
                read.query("SELECT txid_current()", (), records=1, octets=20, cells=1)[0])
        except (InterfaceError, OperationalError):
            failure = ReceiverLifecycleStorageError("receiver storage is unavailable")
        else:
            _require(row == (guard._transaction_id,))
            return
        raise failure

    def introduction(self, workspace, receiver):
        _text(workspace)
        _text(receiver)
        from .configuration_evidence import _active_read
        from .configuration_cleanup_phase_read_bounds import _phase_columns
        columns = _phase_columns(self.connection, "introduction", (workspace, receiver),
            tuple((name, "text", 2048) for name in _INTRO_COLUMNS))
        read = _active_read(self.connection)
        if read is None:
            row = self.connection.execute(_select(_INTRO, _INTRO_COLUMNS) +
                "WHERE workspace_id=%s AND receiver_id=%s", (workspace, receiver)).fetchone()
        else:
            rows = read.bounded_rows(_INTRO, columns,
                "workspace_id=%s AND receiver_id=%s", (workspace, receiver))
            row = (*rows[0], True) if rows else None
        result = None if row is None else _decode(row, ReceiverIntroduction)
        if result is not None:
            from .configuration_cleanup_phase_read_bounds import _phase_require
            for role, child in (("origin-action", (result.introducing_action_id, result.introducing_session_id, workspace)),
                    ("graph", (result.introducing_graph_id,)), ("projection", (result.introducing_realized_projection_id,)),
                    ("raw-graph", (workspace, result.introducing_graph_id)),
                    ("raw-projection", (workspace, result.introducing_realized_projection_id, result.introducing_graph_id)),
                    ("bindings", (workspace, result.introducing_graph_id, result.introducing_realized_projection_id))):
                _phase_require(self.connection, "introduction", (workspace, receiver), role, child)
            if result.first_accepted_action_id is not None:
                _phase_require(self.connection, "introduction", (workspace, receiver), "acceptance-action",
                    (result.first_accepted_action_id, result.first_accepted_session_id))
        return result

    def material(self, workspace, graph_id, projection_id, *, graph=None, projection=None):
        for value in (workspace, graph_id, projection_id):
            _text(value)
        from .configuration_evidence import _active_read
        from .configuration_cleanup_phase_read_bounds import _phase_context, _phase_columns
        _phase_context(self.connection)
        read = _active_read(self.connection)
        if read is None:
            authored_row = self.connection.execute(_select("cpk_graph_versions", _GRAPH_COLUMNS) +
                "WHERE workspace_id=%s AND graph_id=%s", (workspace, graph_id)).fetchone()
            projected_row = self.connection.execute(_select("cpk_realized_graph_projections", _PROJECTION_COLUMNS) +
                "WHERE workspace_id=%s AND projection_id=%s AND source_authored_graph_id=%s",
                (workspace, projection_id, graph_id)).fetchone()
        else:
            def columns(names):
                return tuple((name, "json" if name in _JSON else "int" if name == "version"
                    else "time" if name == "created_at" else "text", 1048576 if name in _JSON else 2048)
                    for name in names)
            authored_rows = read.bounded_rows("cpk_graph_versions",
                _phase_columns(self.connection, "raw-graph", (workspace, graph_id), columns(_GRAPH_COLUMNS)),
                "workspace_id=%s AND graph_id=%s", (workspace, graph_id))
            projected_rows = read.bounded_rows("cpk_realized_graph_projections",
                _phase_columns(self.connection, "raw-projection", (workspace, projection_id, graph_id), columns(_PROJECTION_COLUMNS)),
                "workspace_id=%s AND projection_id=%s AND source_authored_graph_id=%s", (workspace, projection_id, graph_id))
            authored_row = (*authored_rows[0], True) if authored_rows else None
            projected_row = (*projected_rows[0], True) if projected_rows else None

        def authored(*values):
            values = list(values)
            values[5] = decode_postgres_timestamp(values[5])
            return GraphVersionRecord(*values)

        def projected(*values):
            from control_plane_kit_operations.records import RealizedGraphProjectionKind
            values = list(values)
            values[3] = RealizedGraphProjectionKind(values[3])
            values[8] = decode_postgres_timestamp(values[8])
            return RealizedGraphProjectionRecord(*values)

        stored_graph = _decode(authored_row, authored)
        stored_projection = _decode(projected_row, projected)
        if graph is not None:
            _require(type(graph) is GraphVersionRecord and graph == stored_graph)
        if projection is not None:
            _require(type(projection) is RealizedGraphProjectionRecord and projection == stored_projection)
        return derive_receiver_bindings(workspace, graph_id, projection_id, stored_projection.graph_descriptor)

    def bindings(self, workspace, graph_id, projection_id):
        expected = self.material(workspace, graph_id, projection_id)
        from .configuration_evidence import _active_read
        read = _active_read(self.connection)
        if read is None:
            rows = self.connection.execute(_select(_BIND, _BIND_COLUMNS) +
                "WHERE workspace_id=%s AND graph_id=%s AND realized_projection_id=%s "
                "ORDER BY node_id,provider_socket_name LIMIT %s",
                (workspace, graph_id, projection_id, len(expected) + 1)).fetchall()
        else:
            from .configuration_cleanup_phase_read_bounds import _phase_rows
            bounded = _phase_rows(read, "bindings", (workspace, graph_id, projection_id), maximum=len(expected))
            if bounded is None:
                bounded = read.bounded_rows(_BIND,
                tuple((name, "text", 2048) for name in _BIND_COLUMNS),
                "workspace_id=%s AND graph_id=%s AND realized_projection_id=%s", (workspace, graph_id, projection_id),
                maximum=len(expected), point=False, order="node_id,provider_socket_name")
            rows = tuple((*row, True) for row in bounded)
        actual = tuple(sorted((_decode(row, ReceiverBinding) for row in rows),
                              key=lambda item: (item.node_id, item.provider_socket_name)))
        _require(actual == expected)
        from control_plane_kit_operations._configuration_acceptance import _PUBLICATION_SCOPE
        publication = _PUBLICATION_SCOPE.get()
        if publication is not None:
            publication._observe_publication_collection(read, "bindings", (workspace, graph_id, projection_id),
                tuple((item.node_id, item.provider_socket_name) for item in actual))
        from .configuration_cleanup_phase_read_bounds import _phase_require
        for binding in actual:
            _phase_require(self.connection, "bindings", (workspace, graph_id, projection_id),
                "introduction", (workspace, binding.receiver_id))
        return actual

    def witness(self, workspace, action_id, session_id):
        _text(action_id)
        _text(session_id)
        sql = ("SELECT EXISTS (SELECT 1 FROM cpk_operation_actions a "
            "JOIN cpk_operation_sessions s ON s.session_id=a.session_id "
            "WHERE a.action_id=%s AND a.session_id=%s AND s.workspace_id=%s)")
        params = (action_id, session_id, workspace)
        from .configuration_evidence import _active_read
        read = _active_read(self.connection)
        if read is None:
            row = self.connection.execute(sql, params).fetchone()
        else:
            rows = read.query(sql, params, records=1, octets=1, cells=1, identities=2)
            _require(len(rows) == 1)
            row = rows[0]
        _require(row == (True,))

    def reserve(self, owner, graph, projection, *, action_id, session_id, draft_id, lifecycle_guard,
                new_receiver_ids=None):
        self.guard(owner, lifecycle_guard, graph.workspace_id)
        bindings = self.material(graph.workspace_id, graph.graph_id, projection.projection_id,
                                 graph=graph, projection=projection)
        if new_receiver_ids is not None:
            _require(type(new_receiver_ids) is tuple
                     and new_receiver_ids == tuple(sorted(set(new_receiver_ids)))
                     and set(new_receiver_ids) <= {item.receiver_id for item in bindings})
            bindings = tuple(item for item in bindings if item.receiver_id in new_receiver_ids)
        self.witness(graph.workspace_id, action_id, session_id)
        if draft_id is not None:
            _text(draft_id)
            _require(self.connection.execute(
                "SELECT EXISTS (SELECT 1 FROM cpk_desired_topology_draft_revisions "
                "WHERE workspace_id=%s AND draft_id=%s AND graph_id=%s)",
                (graph.workspace_id, draft_id, graph.graph_id)).fetchone() == (True,))
        result = []
        # Global receiver uniqueness can conflict across different workspace
        # guards: every caller reserves the same total ID order.
        for binding in sorted(bindings, key=lambda item: item.receiver_id):
            introduced = ReceiverIntroduction(binding.workspace_id, binding.receiver_id,
                binding.runtime_id, binding.node_id, binding.provider_socket_name,
                binding.graph_id, binding.realized_projection_id, action_id, session_id, draft_id)
            self.connection.execute(
                f"INSERT INTO {_INTRO} ({','.join(_INTRO_COLUMNS)}) VALUES "
                f"({','.join(['%s'] * len(_INTRO_COLUMNS))}) ON CONFLICT (receiver_id) DO NOTHING",
                astuple(introduced),
            )
            original = self.introduction(binding.workspace_id, binding.receiver_id)
            if original is None or astuple(original)[:10] != astuple(introduced)[:10]:
                _conflict()
            result.append(original)
        return tuple(result)

    def persist(self, owner, graph, projection, *, lifecycle_guard):
        self.guard(owner, lifecycle_guard, graph.workspace_id)
        bindings = self.material(graph.workspace_id, graph.graph_id, projection.projection_id,
                                 graph=graph, projection=projection)
        for binding in bindings:
            origin = self.introduction(binding.workspace_id, binding.receiver_id)
            _require(origin is not None and (origin.runtime_id, origin.node_id, origin.provider_socket_name) ==
                     (binding.runtime_id, binding.node_id, binding.provider_socket_name))
            self.connection.execute(
                f"INSERT INTO {_BIND} ({','.join(_BIND_COLUMNS)}) VALUES "
                f"({','.join(['%s'] * len(_BIND_COLUMNS))}) ON CONFLICT DO NOTHING", astuple(binding),
            )
        return self.bindings(graph.workspace_id, graph.graph_id, projection.projection_id)

    def record_witness(self, owner, workspace, receiver, *, action_id, session_id, lifecycle_guard, retirement):
        self.guard(owner, lifecycle_guard, workspace)
        self.witness(workspace, action_id, session_id)
        origin = self.introduction(workspace, receiver)
        if origin is None:
            _conflict()
        prefix = "retired" if retirement else "first_accepted"
        current = (getattr(origin, prefix + "_action_id"), getattr(origin, prefix + "_session_id"))
        if current == (action_id, session_id):
            return origin
        if current != (None, None):
            _conflict()
        if retirement and (origin.first_accepted_action_id is None or origin.first_accepted_action_id == action_id):
            _conflict()
        if not retirement and origin.retired_action_id is not None:
            _conflict()
        sql = (f"UPDATE {_INTRO} SET {prefix}_action_id=%s,{prefix}_session_id=%s "
            f"WHERE workspace_id=%s AND receiver_id=%s AND {prefix}_action_id IS NULL "
            f"AND {prefix}_session_id IS NULL RETURNING receiver_id")
        params = (action_id, session_id, workspace, receiver)
        from .configuration_evidence import _active_read
        read = _active_read(self.connection)
        if read is None:
            row = self.connection.execute(sql, params).fetchone()
        else:
            rows = read.query(sql, params, records=1, octets=32, cells=1)
            row = rows[0] if rows else None
        if row != (receiver,):
            _conflict()
        return self.introduction(workspace, receiver)


def validate_current_rows(connection):
    """Verify retained receiver-owned material, never classify generic history.

    A wholly absent continuation index is not discoverable here. C owns atomic
    initial completeness; explicit member reads still rederive the requested set.
    """
    storage = _ReceiverStorage(connection)
    last = ("", "", "")
    while True:
        rows = connection.execute(
            "WITH indexed(workspace_id,graph_id,projection_id) AS ("
            "SELECT workspace_id,graph_id,realized_projection_id FROM cpk_graph_receiver_bindings "
            "UNION SELECT workspace_id,introducing_graph_id,introducing_realized_projection_id "
            "FROM cpk_graph_receiver_introductions) "
            "SELECT CASE WHEN octet_length(workspace_id)<=2048 THEN workspace_id END,"
            "CASE WHEN octet_length(graph_id)<=2048 THEN graph_id END,"
            "CASE WHEN octet_length(projection_id)<=2048 THEN projection_id END "
            "FROM indexed WHERE (workspace_id,graph_id,projection_id)>(%s,%s,%s) "
            "ORDER BY workspace_id,graph_id,projection_id LIMIT 64",
            last,
        ).fetchall()
        _require(len(rows) <= 64)
        for workspace, graph, projection in rows:
            storage.bindings(workspace, graph, projection)
            last = workspace, graph, projection
        if len(rows) < 64:
            break
    last = ("", "")
    while True:
        rows = connection.execute(_select(_INTRO, _INTRO_COLUMNS) +
            "WHERE (workspace_id,receiver_id)>(%s,%s) ORDER BY workspace_id,receiver_id LIMIT 64", last).fetchall()
        _require(len(rows) <= 64)
        for row in rows:
            origin = _decode(row, ReceiverIntroduction)
            bindings = storage.bindings(origin.workspace_id, origin.introducing_graph_id,
                                         origin.introducing_realized_projection_id)
            _require(any(item.receiver_id == origin.receiver_id and
                         (item.runtime_id, item.node_id, item.provider_socket_name) ==
                         (origin.runtime_id, origin.node_id, origin.provider_socket_name) for item in bindings))
            last = origin.workspace_id, origin.receiver_id
        if len(rows) < 64:
            return
