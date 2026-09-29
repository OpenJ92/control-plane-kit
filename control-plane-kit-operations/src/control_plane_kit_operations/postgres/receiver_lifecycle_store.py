"""Internal graph-owned receiver persistence in the caller's transaction."""

from dataclasses import astuple, fields

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


class _ReceiverStorage:
    def __init__(self, connection):
        self.connection = connection

    def guard(self, owner, guard, workspace):
        _require(owner.owns_receiver_lifecycle(guard, workspace))
        try:
            row = self.connection.execute("SELECT txid_current()").fetchone()
        except (InterfaceError, OperationalError):
            failure = ReceiverLifecycleStorageError("receiver storage is unavailable")
        else:
            _require(row == (guard._transaction_id,))
            return
        raise failure

    def introduction(self, workspace, receiver):
        _text(workspace)
        _text(receiver)
        row = self.connection.execute(_select(_INTRO, _INTRO_COLUMNS) +
            "WHERE workspace_id=%s AND receiver_id=%s", (workspace, receiver)).fetchone()
        return None if row is None else _decode(row, ReceiverIntroduction)

    def material(self, workspace, graph_id, projection_id, *, graph=None, projection=None):
        for value in (workspace, graph_id, projection_id):
            _text(value)
        authored_row = self.connection.execute(_select("cpk_graph_versions", _GRAPH_COLUMNS) +
            "WHERE workspace_id=%s AND graph_id=%s", (workspace, graph_id)).fetchone()
        projected_row = self.connection.execute(_select("cpk_realized_graph_projections", _PROJECTION_COLUMNS) +
            "WHERE workspace_id=%s AND projection_id=%s AND source_authored_graph_id=%s",
            (workspace, projection_id, graph_id)).fetchone()

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
        rows = self.connection.execute(_select(_BIND, _BIND_COLUMNS) +
            "WHERE workspace_id=%s AND graph_id=%s AND realized_projection_id=%s "
            "ORDER BY node_id,provider_socket_name LIMIT %s",
            (workspace, graph_id, projection_id, len(expected) + 1)).fetchall()
        actual = tuple(sorted((_decode(row, ReceiverBinding) for row in rows),
                              key=lambda item: (item.node_id, item.provider_socket_name)))
        _require(actual == expected)
        return actual

    def witness(self, workspace, action_id, session_id):
        _text(action_id)
        _text(session_id)
        row = self.connection.execute(
            "SELECT EXISTS (SELECT 1 FROM cpk_operation_actions a "
            "JOIN cpk_operation_sessions s ON s.session_id=a.session_id "
            "WHERE a.action_id=%s AND a.session_id=%s AND s.workspace_id=%s)",
            (action_id, session_id, workspace),
        ).fetchone()
        _require(row == (True,))

    def reserve(self, owner, graph, projection, *, action_id, session_id, draft_id, lifecycle_guard):
        self.guard(owner, lifecycle_guard, graph.workspace_id)
        bindings = self.material(graph.workspace_id, graph.graph_id, projection.projection_id,
                                 graph=graph, projection=projection)
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
        row = self.connection.execute(
            f"UPDATE {_INTRO} SET {prefix}_action_id=%s,{prefix}_session_id=%s "
            f"WHERE workspace_id=%s AND receiver_id=%s AND {prefix}_action_id IS NULL "
            f"AND {prefix}_session_id IS NULL RETURNING receiver_id",
            (action_id, session_id, workspace, receiver),
        ).fetchone()
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
