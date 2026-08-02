"""Postgres stores for workspace truth and graph versions."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from psycopg.types.json import Jsonb

from control_plane_kit_core.types import WorkspaceLifecycle
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)


class RealizedGraphProjectionConflict(ValueError):
    """Raised when one projection identity is reused for different material."""


class PostgresWorkspaceStore:
    """Postgres-backed workspace truth store."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def create(self, record: WorkspaceRecord) -> WorkspaceRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_workspaces
              (workspace_id, name, lifecycle, current_graph_id, desired_graph_id, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                record.workspace_id,
                record.name,
                record.lifecycle.value,
                record.current_graph_id,
                record.desired_graph_id,
                Jsonb(record.metadata),
            ),
        )
        return record

    def get(self, workspace_id: str) -> WorkspaceRecord:
        return self._get(workspace_id, for_update=False)

    def get_for_update(self, workspace_id: str) -> WorkspaceRecord:
        return self._get(workspace_id, for_update=True)

    def set_lifecycle(
        self,
        workspace_id: str,
        lifecycle: WorkspaceLifecycle,
    ) -> WorkspaceRecord:
        record = replace(self.get(workspace_id), lifecycle=lifecycle)
        self._connection.execute(
            "UPDATE cpk_workspaces SET lifecycle = %s WHERE workspace_id = %s",
            (lifecycle.value, workspace_id),
        )
        return record

    def set_current_graph(self, workspace_id: str, graph_id: str) -> WorkspaceRecord:
        record = replace(self.get(workspace_id), current_graph_id=graph_id)
        self._connection.execute(
            "UPDATE cpk_workspaces SET current_graph_id = %s WHERE workspace_id = %s",
            (graph_id, workspace_id),
        )
        return record

    def compare_and_set_current_graph(
        self,
        workspace_id: str,
        *,
        expected_graph_id: str,
        replacement_graph_id: str,
    ) -> WorkspaceRecord | None:
        row = self._connection.execute(
            """
            UPDATE cpk_workspaces
            SET current_graph_id = %s
            WHERE workspace_id = %s AND current_graph_id = %s
            RETURNING workspace_id, name, lifecycle, current_graph_id,
                      desired_graph_id, metadata
            """,
            (replacement_graph_id, workspace_id, expected_graph_id),
        ).fetchone()
        if row is None:
            return None
        return _workspace_record(row)

    def set_desired_graph(self, workspace_id: str, graph_id: str) -> WorkspaceRecord:
        record = replace(self.get(workspace_id), desired_graph_id=graph_id)
        self._connection.execute(
            "UPDATE cpk_workspaces SET desired_graph_id = %s WHERE workspace_id = %s",
            (graph_id, workspace_id),
        )
        return record

    def _get(self, workspace_id: str, *, for_update: bool) -> WorkspaceRecord:
        lock = " FOR UPDATE" if for_update else ""
        row = self._connection.execute(
            f"""
            SELECT workspace_id, name, lifecycle, current_graph_id, desired_graph_id, metadata
            FROM cpk_workspaces WHERE workspace_id = %s{lock}
            """,
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing workspace {workspace_id!r}")
        return _workspace_record(row)


class PostgresGraphTopologyStore:
    """Postgres-backed immutable graph topology-version store."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def save(self, record: GraphVersionRecord) -> GraphVersionRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_graph_versions
              (graph_id, workspace_id, version, graph_descriptor, created_by, created_at, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.graph_id,
                record.workspace_id,
                record.version,
                Jsonb(record.graph_descriptor),
                record.created_by,
                record.created_at,
                Jsonb(record.metadata),
            ),
        )
        return record

    def get(self, graph_id: str) -> GraphVersionRecord:
        row = self._connection.execute(
            """
            SELECT graph_id, workspace_id, version, graph_descriptor, created_by, created_at, metadata
            FROM cpk_graph_versions WHERE graph_id = %s
            """,
            (graph_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing graph {graph_id!r}")
        return _graph_record(row)

    def latest_for_workspace(self, workspace_id: str) -> GraphVersionRecord | None:
        row = self._connection.execute(
            """
            SELECT graph_id, workspace_id, version, graph_descriptor, created_by, created_at, metadata
            FROM cpk_graph_versions
            WHERE workspace_id = %s
            ORDER BY version DESC
            LIMIT 1
            """,
            (workspace_id,),
        ).fetchone()
        if row is None:
            return None
        return _graph_record(row)

    def next_version_for_workspace(self, workspace_id: str) -> int:
        row = self._connection.execute(
            """
            SELECT COALESCE(MAX(version), 0) + 1
            FROM cpk_graph_versions
            WHERE workspace_id = %s
            """,
            (workspace_id,),
        ).fetchone()
        return int(row[0])


class PostgresRealizedGraphProjectionStore:
    """Postgres-backed immutable authored-to-realized graph lineage."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def save(
        self,
        record: RealizedGraphProjectionRecord,
    ) -> RealizedGraphProjectionRecord:
        source = self._connection.execute(
            """
            SELECT workspace_id
            FROM cpk_graph_versions
            WHERE graph_id = %s
            """,
            (record.source_authored_graph_id,),
        ).fetchone()
        if source is None or source[0] != record.workspace_id:
            raise RealizedGraphProjectionConflict(
                "realized projection source is not an authored graph in its workspace"
            )
        inserted = self._connection.execute(
            """
            INSERT INTO cpk_realized_graph_projections
              (projection_id, workspace_id, source_authored_graph_id,
               projection_kind, projection_key, projection_digest,
               graph_descriptor, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING projection_id, workspace_id, source_authored_graph_id,
                      projection_kind, projection_key, projection_digest,
                      graph_descriptor, created_by, created_at
            """,
            (
                record.projection_id,
                record.workspace_id,
                record.source_authored_graph_id,
                record.projection_kind.value,
                record.projection_key,
                record.projection_digest,
                Jsonb(record.graph_descriptor),
                record.created_by,
                record.created_at,
            ),
        ).fetchone()
        if inserted is not None:
            return _realized_graph_projection_record(inserted)
        existing = self._by_identity(
            workspace_id=record.workspace_id,
            source_authored_graph_id=record.source_authored_graph_id,
            projection_kind=record.projection_kind,
            projection_key=record.projection_key,
        )
        if existing is None or existing.projection_digest != record.projection_digest:
            raise RealizedGraphProjectionConflict(
                "realized projection identity is already bound to different material"
            )
        return existing

    def get(self, projection_id: str) -> RealizedGraphProjectionRecord:
        row = self._connection.execute(
            """
            SELECT projection_id, workspace_id, source_authored_graph_id,
                   projection_kind, projection_key, projection_digest,
                   graph_descriptor, created_by, created_at
            FROM cpk_realized_graph_projections
            WHERE projection_id = %s
            """,
            (projection_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing realized graph projection {projection_id!r}")
        return _realized_graph_projection_record(row)

    def _by_identity(
        self,
        *,
        workspace_id: str,
        source_authored_graph_id: str,
        projection_kind: RealizedGraphProjectionKind,
        projection_key: str,
    ) -> RealizedGraphProjectionRecord | None:
        row = self._connection.execute(
            """
            SELECT projection_id, workspace_id, source_authored_graph_id,
                   projection_kind, projection_key, projection_digest,
                   graph_descriptor, created_by, created_at
            FROM cpk_realized_graph_projections
            WHERE workspace_id = %s
              AND source_authored_graph_id = %s
              AND projection_kind = %s
              AND projection_key = %s
            """,
            (
                workspace_id,
                source_authored_graph_id,
                projection_kind.value,
                projection_key,
            ),
        ).fetchone()
        if row is None:
            return None
        return _realized_graph_projection_record(row)


def _workspace_record(row: tuple[Any, ...]) -> WorkspaceRecord:
    return WorkspaceRecord(
        workspace_id=row[0],
        name=row[1],
        lifecycle=WorkspaceLifecycle(row[2]),
        current_graph_id=row[3],
        desired_graph_id=row[4],
        metadata=row[5],
    )


def _graph_record(row: tuple[Any, ...]) -> GraphVersionRecord:
    return GraphVersionRecord(
        graph_id=row[0],
        workspace_id=row[1],
        version=row[2],
        graph_descriptor=row[3],
        created_by=row[4],
        created_at=row[5],
        metadata=row[6],
    )


def _realized_graph_projection_record(
    row: tuple[Any, ...],
) -> RealizedGraphProjectionRecord:
    return RealizedGraphProjectionRecord(
        projection_id=row[0],
        workspace_id=row[1],
        source_authored_graph_id=row[2],
        projection_kind=RealizedGraphProjectionKind(row[3]),
        projection_key=row[4],
        projection_digest=row[5],
        graph_descriptor=row[6],
        created_by=row[7],
        created_at=row[8],
    )
