from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_operations.records import GraphVersionRecord


def execution_graph(name, *, runtime_id="runtime-a", node_ids=()):
    """Actual target material for fixtures whose plans name a runtime or node."""
    from control_plane_kit_core.algebra import BlockSockets, BlockSpec
    from control_plane_kit_core.topology import Node, RuntimeRecord
    from control_plane_kit_core.types import BlockFamily, RuntimeKind
    return DeploymentGraph(name, nodes={node_id: Node(node_id, BlockFamily.APPLICATION,
        BlockSpec(node_id), "container-server", runtime_id, BlockSockets()) for node_id in node_ids},
        runtimes={runtime_id: RuntimeRecord(runtime_id, RuntimeKind.DOCKER, children=node_ids)})


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
    graphs: dict[str, DeploymentGraph] | None = None,
) -> dict[str, str]:
    projection_ids: dict[str, str] = {}
    for version, graph_id in enumerate(graph_ids, start=1):
        stores.graphs.save(
            GraphVersionRecord.from_graph(
                graph_id=graph_id,
                workspace_id=workspace_id,
                version=version,
                graph=DeploymentGraph(graph_id) if graphs is None else graphs[graph_id],
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
