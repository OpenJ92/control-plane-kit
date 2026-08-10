from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_operations.postgres.schema import (
    _POSTGRES_SCHEMA_V17_CONSTRAINTS,
)
from control_plane_kit_operations.records import GraphVersionRecord


_HISTORICAL_GRAPH_LINEAGE_CONSTRAINTS = frozenset(
    {
        (
            "cpk_realized_graph_projections",
            "cpk_realized_graph_projection_workspace_identity",
        ),
        (
            "cpk_realized_graph_projections",
            "cpk_realized_graph_projection_source_identity",
        ),
        ("cpk_workspaces", "cpk_workspaces_current_realized_projection_fk"),
        ("cpk_workspaces", "cpk_workspaces_desired_realized_projection_fk"),
        ("cpk_workspaces", "cpk_workspaces_current_projection_source_fk"),
        ("cpk_workspaces", "cpk_workspaces_desired_projection_source_fk"),
        ("cpk_workspaces", "cpk_workspaces_current_lineage_check"),
        ("cpk_workspaces", "cpk_workspaces_desired_lineage_check"),
        (
            "cpk_activity_plans",
            "cpk_activity_plans_base_projection_source_fk",
        ),
        (
            "cpk_activity_plans",
            "cpk_activity_plans_desired_projection_source_fk",
        ),
    }
)
_V17_GRAPH_LINEAGE_CONSTRAINTS = frozenset(
    {
        ("cpk_workspaces", "cpk_workspaces_desired_graph_revision_check"),
        (
            "cpk_activity_plans",
            "cpk_activity_plans_desired_graph_revision_check",
        ),
    }
)


def seed_historical_graph_lineage_constraints(connection: Any) -> None:
    inventory = tuple(_POSTGRES_SCHEMA_V17_CONSTRAINTS)
    identities = tuple((table, name) for table, name, *_rest in inventory)
    specifications = {
        (table, name): (ddl, definition)
        for table, name, _kind, ddl, definition in inventory
    }
    accepted_identities = (
        _HISTORICAL_GRAPH_LINEAGE_CONSTRAINTS
        | _V17_GRAPH_LINEAGE_CONSTRAINTS
    )
    if (
        len(inventory) != len(accepted_identities)
        or len(specifications) != len(inventory)
        or set(identities) != accepted_identities
    ):
        raise AssertionError("unexpected graph lineage constraint inventory")
    for table, name in sorted(_HISTORICAL_GRAPH_LINEAGE_CONSTRAINTS):
        ddl, _definition = specifications[(table, name)]
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
