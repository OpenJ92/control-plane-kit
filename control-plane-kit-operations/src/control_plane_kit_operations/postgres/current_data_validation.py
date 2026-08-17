"""Bounded semantic validation for retained current-schema rows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from control_plane_kit_operations.postgres.temporal import (
    decode_postgres_timestamp,
    encode_postgres_timestamp,
)
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    OperationsRecordError,
    RealizedGraphProjectionRecord,
)


class _Connection(Protocol):
    def execute(self, query: str, params: tuple[object, ...] = ()) -> Any: ...


_BATCH_SIZE = 64
_TEXT_TRANSPORT_BYTES = 2_048
_GRAPH_DESCRIPTOR_TRANSPORT_BYTES = 1_048_576
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
       )
     )
     OR (
       workspace.desired_graph_id IS NOT NULL
       AND (
         desired_graph.workspace_id IS DISTINCT FROM workspace.workspace_id
         OR desired_projection.workspace_id IS DISTINCT FROM workspace.workspace_id
         OR desired_projection.source_authored_graph_id
              IS DISTINCT FROM workspace.desired_graph_id
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
)
"""

_VERIFY_APPROVAL_SUBJECTS = """
SELECT NOT EXISTS (
  SELECT 1
  FROM cpk_approval_requests AS approvals
  LEFT JOIN cpk_gateway_key_rotations AS rotations
    ON rotations.rotation_id = approvals.rotation_id
  WHERE
    (approvals.review_digest COLLATE "C") !~ '^[0-9a-f]{64}$'
    OR CASE
      WHEN (approvals.subject_kind COLLATE "C") = 'activity-plan' THEN NOT (
        approvals.plan_id IS NOT NULL
        AND approvals.rotation_id IS NULL
        AND octet_length(approvals.plan_id) BETWEEN 1 AND 200
        AND (approvals.plan_id COLLATE "C") ~ '^[A-Za-z0-9]'
        AND (approvals.plan_id COLLATE "C") !~ '[^A-Za-z0-9._:-]'
        AND approvals.subject_payload = jsonb_build_object(
          'kind', 'activity-plan', 'plan_id', approvals.plan_id
        )
        AND (approvals.review_digest COLLATE "C") = encode(
          sha256(convert_to('activity-plan:' || approvals.plan_id, 'UTF8')),
          'hex'
        )
      )
      WHEN (approvals.subject_kind COLLATE "C") =
           'gateway-key-rotation' THEN NOT (
        approvals.plan_id IS NULL
        AND approvals.rotation_id IS NOT NULL
        AND rotations.rotation_id IS NOT NULL
        AND octet_length(rotations.rotation_id) BETWEEN 1 AND 200
        AND (rotations.rotation_id COLLATE "C") ~ '^[A-Za-z0-9]'
        AND (rotations.rotation_id COLLATE "C") !~ '[^A-Za-z0-9._:-]'
        AND octet_length(rotations.workspace_id) BETWEEN 1 AND 200
        AND (rotations.workspace_id COLLATE "C") ~ '^[A-Za-z0-9]'
        AND (rotations.workspace_id COLLATE "C") !~ '[^A-Za-z0-9._:-]'
        AND octet_length(rotations.gateway_node_id) BETWEEN 1 AND 200
        AND (rotations.gateway_node_id COLLATE "C") ~ '^[A-Za-z0-9]'
        AND (rotations.gateway_node_id COLLATE "C") !~ '[^A-Za-z0-9._:-]'
        AND octet_length(rotations.issuer) BETWEEN 1 AND 200
        AND (rotations.issuer COLLATE "C") ~ '^[A-Za-z0-9]'
        AND (rotations.issuer COLLATE "C") !~ '[^A-Za-z0-9._:-]'
        AND octet_length(rotations.old_key_id) BETWEEN 1 AND 200
        AND (rotations.old_key_id COLLATE "C") ~ '^[A-Za-z0-9]'
        AND (rotations.old_key_id COLLATE "C") !~ '[^A-Za-z0-9._:-]'
        AND (rotations.purpose COLLATE "C") IN (
          'gateway-probe', 'workload-node-control',
          'workload-node-control-surface-read'
        )
        AND rotations.maximum_grant_lifetime_seconds BETWEEN 1 AND 300
        AND rotations.clock_skew_seconds BETWEEN 0 AND 60
        AND (rotations.intent_fingerprint COLLATE "C") ~ '^[0-9a-f]{64}$'
        AND approvals.subject_payload = jsonb_build_object(
          'kind', 'gateway-key-rotation',
          'rotation_id', rotations.rotation_id,
          'workspace_id', rotations.workspace_id,
          'gateway_node_id', rotations.gateway_node_id,
          'purpose', rotations.purpose,
          'issuer', rotations.issuer,
          'old_key_id', rotations.old_key_id,
          'overlap_verifier_roles', jsonb_build_array('old', 'new'),
          'retirement_verifier_roles', jsonb_build_array('new'),
          'maximum_grant_lifetime_seconds',
            rotations.maximum_grant_lifetime_seconds,
          'clock_skew_seconds', rotations.clock_skew_seconds,
          'rotation_intent_digest', rotations.intent_fingerprint
        )
        AND (approvals.review_digest COLLATE "C") = encode(
          sha256(convert_to(
            '{"clock_skew_seconds":' || rotations.clock_skew_seconds::text ||
            ',"gateway_node_id":' || to_jsonb(rotations.gateway_node_id)::text ||
            ',"issuer":' || to_jsonb(rotations.issuer)::text ||
            ',"kind":"gateway-key-rotation"' ||
            ',"maximum_grant_lifetime_seconds":' ||
              rotations.maximum_grant_lifetime_seconds::text ||
            ',"old_key_id":' || to_jsonb(rotations.old_key_id)::text ||
            ',"overlap_verifier_roles":["old","new"]' ||
            ',"purpose":' || to_jsonb(rotations.purpose)::text ||
            ',"retirement_verifier_roles":["new"]' ||
            ',"rotation_id":' || to_jsonb(rotations.rotation_id)::text ||
            ',"rotation_intent_digest":' ||
              to_jsonb(rotations.intent_fingerprint)::text ||
            ',"workspace_id":' || to_jsonb(rotations.workspace_id)::text || '}',
            'UTF8'
          )),
          'hex'
        )
      )
      ELSE true
    END
)
"""


def validate_current_rows(connection: _Connection) -> None:
    try:
        _scan(connection)
        from control_plane_kit_operations.postgres.effect_attempt_store import (
            _validate_current_rows as validate_effect_attempt_rows,
        )
        from control_plane_kit_operations.postgres.effect_outcome_store import (
            _validate_current_rows as validate_effect_outcome_rows,
        )

        validate_effect_attempt_rows(connection)
        validate_effect_outcome_rows(connection)
    except (TypeError, ValueError, OperationsRecordError):
        raise CurrentRowDrift from None
    rows = connection.execute(_VERIFY_REFERENCES).fetchall()
    if rows != [(True,)]:
        raise CurrentRowDrift
    approval_rows = connection.execute(_VERIFY_APPROVAL_SUBJECTS).fetchall()
    if approval_rows != [(True,)]:
        raise CurrentRowDrift


class CurrentRowDrift(Exception):
    pass


def _scan(
    connection: _Connection,
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
            _projection_is_exact(connection, expected)
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


__all__ = []
