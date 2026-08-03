"""Postgres store for immutable observed runtime evidence."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from control_plane_kit_core.probe_intents import (
    EndpointContext,
    ProbeKind,
    ProbeOutcome,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.records import (
    BoundedEvidence,
    ObservationFreshness,
    ObservationRecord,
    ObservationStatus,
)


class PostgresObservedStateStore:
    """Postgres-backed observed state store.

    The store records immutable observations. "Current observed state" is a
    read projection over those rows, not a mutable replacement for graph truth.
    """

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def put(self, record: ObservationRecord) -> ObservationRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_observations
              (observation_id, workspace_id, subject_id, status, observed_at,
               evidence, freshness, graph_id, probe_kind, probe_outcome,
               endpoint_context)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.observation_id,
                record.workspace_id,
                record.subject_id,
                record.status.value,
                record.observed_at,
                Jsonb(record.evidence.descriptor()),
                record.freshness.value,
                record.graph_id,
                None if record.probe_kind is None else record.probe_kind.value,
                None if record.probe_outcome is None else record.probe_outcome.value,
                None
                if record.endpoint_context is None
                else record.endpoint_context.value,
            ),
        )
        return record

    def latest(self, workspace_id: str, subject_id: str) -> ObservationRecord | None:
        row = self._connection.execute(
            """
            SELECT observation_id, workspace_id, subject_id, status, observed_at,
                   evidence, freshness, graph_id, probe_kind, probe_outcome,
                   endpoint_context
            FROM cpk_observations
            WHERE workspace_id = %s AND subject_id = %s
            ORDER BY observed_at DESC, observation_id DESC
            LIMIT 1
            """,
            (workspace_id, subject_id),
        ).fetchone()
        return None if row is None else _observation_record(row)

    def latest_for_workspace(self, workspace_id: str) -> tuple[ObservationRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT DISTINCT ON (subject_id)
                   observation_id, workspace_id, subject_id, status, observed_at,
                   evidence, freshness, graph_id, probe_kind, probe_outcome,
                   endpoint_context
            FROM cpk_observations
            WHERE workspace_id = %s
            ORDER BY subject_id ASC, observed_at DESC, observation_id DESC
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(_observation_record(row) for row in rows)

    def history(
        self,
        workspace_id: str,
        subject_id: str,
    ) -> tuple[ObservationRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT observation_id, workspace_id, subject_id, status, observed_at,
                   evidence, freshness, graph_id, probe_kind, probe_outcome,
                   endpoint_context
            FROM cpk_observations
            WHERE workspace_id = %s AND subject_id = %s
            ORDER BY observed_at ASC, observation_id ASC
            """,
            (workspace_id, subject_id),
        ).fetchall()
        return tuple(_observation_record(row) for row in rows)


def _observation_record(row: tuple[Any, ...]) -> ObservationRecord:
    return ObservationRecord(
        observation_id=row[0],
        workspace_id=row[1],
        subject_id=row[2],
        status=ObservationStatus(row[3]),
        observed_at=row[4],
        evidence=BoundedEvidence.from_mapping(row[5]),
        freshness=ObservationFreshness(row[6]),
        graph_id=row[7],
        probe_kind=None if row[8] is None else ProbeKind(row[8]),
        probe_outcome=None if row[9] is None else ProbeOutcome(row[9]),
        endpoint_context=None if row[10] is None else EndpointContext(row[10]),
    )
