from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_operations.records import GraphVersionRecord


_HISTORICAL_GRAPH_LINEAGE_CONSTRAINT_SPECS = (
    (
        "cpk_realized_graph_projections",
        "cpk_realized_graph_projection_workspace_identity",
        "UNIQUE (projection_id, workspace_id)",
    ),
    (
        "cpk_realized_graph_projections",
        "cpk_realized_graph_projection_source_identity",
        "UNIQUE (projection_id, source_authored_graph_id)",
    ),
    (
        "cpk_workspaces",
        "cpk_workspaces_current_realized_projection_fk",
        "FOREIGN KEY (current_realized_projection_id, workspace_id) "
        "REFERENCES cpk_realized_graph_projections(projection_id, workspace_id)",
    ),
    (
        "cpk_workspaces",
        "cpk_workspaces_desired_realized_projection_fk",
        "FOREIGN KEY (desired_realized_projection_id, workspace_id) "
        "REFERENCES cpk_realized_graph_projections(projection_id, workspace_id)",
    ),
    (
        "cpk_workspaces",
        "cpk_workspaces_current_projection_source_fk",
        "FOREIGN KEY (current_realized_projection_id, current_graph_id) "
        "REFERENCES cpk_realized_graph_projections"
        "(projection_id, source_authored_graph_id)",
    ),
    (
        "cpk_workspaces",
        "cpk_workspaces_desired_projection_source_fk",
        "FOREIGN KEY (desired_realized_projection_id, desired_graph_id) "
        "REFERENCES cpk_realized_graph_projections"
        "(projection_id, source_authored_graph_id)",
    ),
    (
        "cpk_workspaces",
        "cpk_workspaces_current_lineage_check",
        "CHECK ((current_graph_id IS NULL) = "
        "(current_realized_projection_id IS NULL))",
    ),
    (
        "cpk_workspaces",
        "cpk_workspaces_desired_lineage_check",
        "CHECK ((desired_graph_id IS NULL) = "
        "(desired_realized_projection_id IS NULL))",
    ),
    (
        "cpk_activity_plans",
        "cpk_activity_plans_base_projection_source_fk",
        "FOREIGN KEY (base_realized_projection_id, base_graph_id) "
        "REFERENCES cpk_realized_graph_projections"
        "(projection_id, source_authored_graph_id)",
    ),
    (
        "cpk_activity_plans",
        "cpk_activity_plans_desired_projection_source_fk",
        "FOREIGN KEY (desired_realized_projection_id, desired_graph_id) "
        "REFERENCES cpk_realized_graph_projections"
        "(projection_id, source_authored_graph_id)",
    ),
)


def seed_historical_graph_lineage_constraints(connection: Any) -> None:
    for table, name, ddl in _HISTORICAL_GRAPH_LINEAGE_CONSTRAINT_SPECS:
        count = connection.execute(
            "SELECT count(*) FROM pg_constraint AS owned_constraint "
            "JOIN pg_class AS relation ON relation.oid=owned_constraint.conrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=current_schema() "
            "AND relation.relname=%s AND owned_constraint.conname=%s",
            (table, name),
        ).fetchone()
        if count == (1,):
            continue
        if count != (0,):
            raise AssertionError("unexpected historical lineage constraint cardinality")
        connection.execute(
            sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} {}").format(
                sql.Identifier(table),
                sql.Identifier(name),
                sql.SQL(ddl),
            )
        )


def seed_authored_graphs(
    connection: Any,
    *,
    workspace_id: str,
    graph_ids: tuple[str, ...],
    created_by: str = "operator-a",
) -> None:
    for version, graph_id in enumerate(graph_ids, start=1):
        connection.execute(
            "INSERT INTO cpk_graph_versions "
            "(graph_id, workspace_id, version, graph_descriptor, created_by, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                graph_id,
                workspace_id,
                version,
                Jsonb(DEFAULT_GRAPH_CODEC.encode(DeploymentGraph(graph_id))),
                created_by,
                datetime(2026, 8, 10, 0, 0, version, tzinfo=timezone.utc),
            ),
        )


def seed_identity_graphs(
    stores: Any,
    *,
    workspace_id: str,
    graph_ids: tuple[str, ...],
    created_by: str = "operator-a",
) -> dict[str, str]:
    projection_ids: dict[str, str] = {}
    for version, graph_id in enumerate(graph_ids, start=1):
        stores.graphs.save(
            GraphVersionRecord.from_graph(
                graph_id=graph_id,
                workspace_id=workspace_id,
                version=version,
                graph=DeploymentGraph(graph_id),
                created_by=created_by,
                created_at=f"2026-07-22T10:00:{version:02d}Z",
            )
        )
        projection = stores.realized_graphs.identity_for_authored(
            workspace_id,
            graph_id,
        )
        stores.realized_graphs.save(projection)
        projection_ids[graph_id] = projection.projection_id
    return projection_ids
