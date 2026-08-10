"""Closed V1 interpreter for retained graph identity lineage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from control_plane_kit_operations.postgres.migrations import SchemaMigrationError
from control_plane_kit_operations.postgres.temporal import (
    decode_postgres_timestamp,
    encode_postgres_timestamp,
)
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    RealizedGraphProjectionRecord,
)


class _Connection(Protocol):
    def execute(self, query: str, params: tuple[object, ...] = ()) -> Any: ...


_BATCH_SIZE = 64
_TEXT_TRANSPORT_BYTES = 2_048
_GRAPH_DESCRIPTOR_TRANSPORT_BYTES = 1_048_576
_LOCKS = """
LOCK TABLE cpk_workspaces IN ACCESS EXCLUSIVE MODE;
LOCK TABLE cpk_graph_versions IN ACCESS EXCLUSIVE MODE;
LOCK TABLE cpk_realized_graph_projections IN ACCESS EXCLUSIVE MODE;
LOCK TABLE cpk_activity_plans IN ACCESS EXCLUSIVE MODE;
"""
_SELECT_BATCH = """
WITH referenced(graph_id) AS (
  SELECT current_graph_id FROM cpk_workspaces WHERE current_graph_id IS NOT NULL
  UNION
  SELECT desired_graph_id FROM cpk_workspaces WHERE desired_graph_id IS NOT NULL
  UNION
  SELECT base_graph_id FROM cpk_activity_plans
  UNION
  SELECT desired_graph_id FROM cpk_activity_plans
)
SELECT octet_length(graph.graph_id) BETWEEN 1 AND %s,
       CASE WHEN octet_length(graph.graph_id) BETWEEN 1 AND %s
            THEN graph.graph_id ELSE NULL END,
       octet_length(graph.workspace_id) BETWEEN 1 AND %s,
       CASE WHEN octet_length(graph.workspace_id) BETWEEN 1 AND %s
            THEN graph.workspace_id ELSE NULL END,
       graph.version > 0,
       CASE WHEN graph.version > 0 THEN graph.version ELSE NULL END,
       jsonb_typeof(graph.graph_descriptor) = 'object'
         AND octet_length(graph.graph_descriptor::text) <= %s,
       CASE WHEN jsonb_typeof(graph.graph_descriptor) = 'object'
                   AND octet_length(graph.graph_descriptor::text) <= %s
            THEN graph.graph_descriptor ELSE NULL END,
       octet_length(graph.created_by) BETWEEN 1 AND %s,
       CASE WHEN octet_length(graph.created_by) BETWEEN 1 AND %s
            THEN graph.created_by ELSE NULL END,
       graph.created_at
FROM cpk_graph_versions AS graph
JOIN referenced ON referenced.graph_id = graph.graph_id
WHERE graph.graph_id > %s
ORDER BY graph.graph_id
LIMIT %s
"""
_OBSERVE_PROJECTION = """
SELECT count(*)::bigint,
       count(*) FILTER (
         WHERE projection_id = %s
           AND workspace_id = %s
           AND source_authored_graph_id = %s
           AND projection_kind = %s
           AND projection_key = %s
           AND projection_digest = %s
           AND graph_descriptor = %s
           AND created_by = %s
           AND created_at = %s
       )::bigint
FROM cpk_realized_graph_projections
WHERE projection_id = %s
   OR (
     workspace_id = %s
     AND source_authored_graph_id = %s
     AND projection_kind = %s
     AND projection_key = %s
   )
"""
_INSERT_PROJECTION = """
INSERT INTO cpk_realized_graph_projections (
  projection_id, workspace_id, source_authored_graph_id, projection_kind,
  projection_key, projection_digest, graph_descriptor, created_by, created_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""
_VERIFY_REFERENCES = """
SELECT NOT EXISTS (
  SELECT 1
  FROM cpk_workspaces AS workspace
  LEFT JOIN cpk_graph_versions AS current_graph
    ON current_graph.graph_id = workspace.current_graph_id
  LEFT JOIN cpk_realized_graph_projections AS current_projection
    ON current_projection.projection_id = workspace.current_realized_projection_id
  LEFT JOIN cpk_graph_versions AS desired_graph
    ON desired_graph.graph_id = workspace.desired_graph_id
  LEFT JOIN cpk_realized_graph_projections AS desired_projection
    ON desired_projection.projection_id = workspace.desired_realized_projection_id
  WHERE workspace.desired_graph_revision < 0
     OR (workspace.current_graph_id IS NULL) <>
          (workspace.current_realized_projection_id IS NULL)
     OR (workspace.desired_graph_id IS NULL) <>
          (workspace.desired_realized_projection_id IS NULL)
     OR (
       workspace.current_graph_id IS NOT NULL
       AND (
         current_graph.workspace_id IS DISTINCT FROM workspace.workspace_id
         OR current_projection.workspace_id IS DISTINCT FROM workspace.workspace_id
         OR current_projection.source_authored_graph_id
              IS DISTINCT FROM workspace.current_graph_id
         OR current_projection.projection_kind IS DISTINCT FROM 'identity'
         OR current_projection.projection_key IS DISTINCT FROM 'identity'
       )
     )
     OR (
       workspace.desired_graph_id IS NOT NULL
       AND (
         desired_graph.workspace_id IS DISTINCT FROM workspace.workspace_id
         OR desired_projection.workspace_id IS DISTINCT FROM workspace.workspace_id
         OR desired_projection.source_authored_graph_id
              IS DISTINCT FROM workspace.desired_graph_id
         OR desired_projection.projection_kind IS DISTINCT FROM 'identity'
         OR desired_projection.projection_key IS DISTINCT FROM 'identity'
       )
     )
) AND NOT EXISTS (
  SELECT 1
  FROM cpk_activity_plans AS plan
  LEFT JOIN cpk_operation_sessions AS session
    ON session.session_id = plan.session_id
  LEFT JOIN cpk_graph_versions AS base_graph
    ON base_graph.graph_id = plan.base_graph_id
  LEFT JOIN cpk_realized_graph_projections AS base_projection
    ON base_projection.projection_id = plan.base_realized_projection_id
  LEFT JOIN cpk_graph_versions AS desired_graph
    ON desired_graph.graph_id = plan.desired_graph_id
  LEFT JOIN cpk_realized_graph_projections AS desired_projection
    ON desired_projection.projection_id = plan.desired_realized_projection_id
  WHERE session.session_id IS NULL
     OR plan.desired_graph_revision < 0
     OR plan.base_realized_projection_id IS NULL
     OR plan.desired_realized_projection_id IS NULL
     OR base_graph.workspace_id IS DISTINCT FROM session.workspace_id
     OR desired_graph.workspace_id IS DISTINCT FROM session.workspace_id
     OR base_projection.workspace_id IS DISTINCT FROM session.workspace_id
     OR desired_projection.workspace_id IS DISTINCT FROM session.workspace_id
     OR base_projection.source_authored_graph_id IS DISTINCT FROM plan.base_graph_id
     OR desired_projection.source_authored_graph_id
          IS DISTINCT FROM plan.desired_graph_id
     OR base_projection.projection_kind IS DISTINCT FROM 'identity'
     OR base_projection.projection_key IS DISTINCT FROM 'identity'
     OR desired_projection.projection_kind IS DISTINCT FROM 'identity'
     OR desired_projection.projection_key IS DISTINCT FROM 'identity'
)
"""


def backfill_graph_lineage_v1(connection: _Connection) -> None:
    """Validate all retained lineage, then materialize only missing identities."""

    failed = False
    try:
        _scan(connection, insert_missing=False)
        _scan(connection, insert_missing=True)
    except Exception:
        failed = True
    if failed:
        _raise_backfill_failure()


def verify_graph_lineage_v1(connection: _Connection) -> None:
    """Observe exact current graph lineage under a stable relation lock set."""

    failed = False
    try:
        lock_graph_lineage_v1(connection)
        _scan(connection, insert_missing=False, require_present=True)
        rows = connection.execute(_VERIFY_REFERENCES).fetchall()
        if rows != [(True,)]:
            raise ValueError("reference mismatch")
    except Exception:
        failed = True
    if failed:
        raise SchemaMigrationError("graph lineage schema is not current")


def lock_graph_lineage_v1(connection: _Connection) -> None:
    """Acquire the closed graph-lineage relation set in canonical order."""

    try:
        connection.execute(_LOCKS)
    except Exception:
        raise SchemaMigrationError("graph lineage schema is not current") from None


def _scan(
    connection: _Connection,
    *,
    insert_missing: bool,
    require_present: bool = False,
) -> None:
    last_graph_id = ""
    while True:
        rows = connection.execute(
            _SELECT_BATCH,
            (
                _TEXT_TRANSPORT_BYTES,
                _TEXT_TRANSPORT_BYTES,
                _TEXT_TRANSPORT_BYTES,
                _TEXT_TRANSPORT_BYTES,
                _GRAPH_DESCRIPTOR_TRANSPORT_BYTES,
                _GRAPH_DESCRIPTOR_TRANSPORT_BYTES,
                _TEXT_TRANSPORT_BYTES,
                _TEXT_TRANSPORT_BYTES,
                last_graph_id,
                _BATCH_SIZE,
            ),
        ).fetchall()
        if len(rows) > _BATCH_SIZE:
            raise ValueError("batch overflow")
        if not rows:
            return
        for row in rows:
            authored = _decode_authored(row)
            expected = RealizedGraphProjectionRecord.identity_for_authored(
                authored_record=authored
            )
            exists = _projection_is_exact(connection, expected)
            if require_present and not exists:
                raise ValueError("projection missing")
            if insert_missing and not exists:
                connection.execute(_INSERT_PROJECTION, _projection_parameters(expected))
                if not _projection_is_exact(connection, expected):
                    raise ValueError("projection insert mismatch")
            last_graph_id = authored.graph_id
        if len(rows) < _BATCH_SIZE:
            return


def _decode_authored(row: object) -> GraphVersionRecord:
    if type(row) not in (tuple, list) or len(row) != 11:
        raise ValueError("row shape")
    (
        graph_id_ok,
        graph_id,
        workspace_id_ok,
        workspace_id,
        version_ok,
        version,
        descriptor_ok,
        descriptor,
        created_by_ok,
        created_by,
        created_at,
    ) = row
    if (
        graph_id_ok is not True
        or workspace_id_ok is not True
        or version_ok is not True
        or descriptor_ok is not True
        or created_by_ok is not True
        or type(graph_id) is not str
        or type(workspace_id) is not str
        or type(version) is not int
        or not isinstance(descriptor, Mapping)
        or type(created_by) is not str
    ):
        raise ValueError("row values")
    return GraphVersionRecord(
        graph_id=graph_id,
        workspace_id=workspace_id,
        version=version,
        graph_descriptor=descriptor,
        created_by=created_by,
        created_at=decode_postgres_timestamp(created_at),
    )


def _projection_is_exact(
    connection: _Connection,
    expected: RealizedGraphProjectionRecord,
) -> bool:
    values = _projection_parameters(expected)
    row = connection.execute(
        _OBSERVE_PROJECTION,
        (*values, expected.projection_id, expected.workspace_id,
         expected.source_authored_graph_id, expected.projection_kind.value,
         expected.projection_key),
    ).fetchone()
    if type(row) not in (tuple, list) or len(row) != 2:
        raise ValueError("projection observation")
    count, exact_count = row
    if count == 0 and exact_count == 0:
        return False
    if count == 1 and exact_count == 1:
        return True
    raise ValueError("projection collision")


def _projection_parameters(
    record: RealizedGraphProjectionRecord,
) -> tuple[object, ...]:
    return (
        record.projection_id,
        record.workspace_id,
        record.source_authored_graph_id,
        record.projection_kind.value,
        record.projection_key,
        record.projection_digest,
        Jsonb(record.graph_descriptor),
        record.created_by,
        encode_postgres_timestamp(record.created_at),
    )


def _raise_backfill_failure() -> None:
    raise SchemaMigrationError("graph lineage compatibility is not accepted")


__all__ = []
