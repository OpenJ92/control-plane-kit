"""Small live demo for the Roadmap 0006 read interfaces.

The demo is intentionally narrow: it starts the real FastAPI read routes over
real Postgres-backed stores with deterministic seed data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import psycopg

from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    CapabilityName,
    DataBlock,
    DeploymentRecipe,
    DockerPostgresImplementation,
    DockerRuntime,
    InstanceReadService,
    PlanOnlyImplementation,
    Protocol,
    ProxyBlock,
    ProviderSocket,
    RequirementSocket,
    SocketConnection,
    compile_recipe,
    create_instance_read_app,
)
from control_plane_kit.stores import (
    GraphVersionRecord,
    ObservationRecord,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    PostgresStoreBundle,
    WorkspaceLifecycle,
    WorkspaceRecord,
    install_schema,
)


DEMO_WORKSPACE_ID = "demo-workspace"
DEMO_TOKEN = "demo-token"
DEMO_RESET_SQL = """
TRUNCATE TABLE
  cpk_activity_events,
  cpk_activity_runs,
  cpk_approval_decisions,
  cpk_approval_requests,
  cpk_activity_plans,
  cpk_operation_actions,
  cpk_operation_sessions,
  cpk_graph_versions,
  cpk_workspaces,
  cpk_observations,
  cpk_instances,
  cpk_secret_references
CASCADE;
"""


@dataclass(frozen=True)
class DemoSettings:
    """Runtime values for the local read-interface demo."""

    database_url: str
    token: str = DEMO_TOKEN
    reset: bool = True

    @classmethod
    def from_environment(cls) -> "DemoSettings":
        database_url = os.environ.get("CPK_DEMO_DATABASE_URL")
        if not database_url:
            raise RuntimeError("CPK_DEMO_DATABASE_URL is required")
        return cls(
            database_url=database_url,
            token=os.environ.get("CPK_DEMO_TOKEN", DEMO_TOKEN),
            reset=os.environ.get("CPK_DEMO_RESET", "true").lower() != "false",
        )


def create_demo_app(settings: DemoSettings):
    """Create a seeded FastAPI app for local read-interface exploration."""

    connection = psycopg.connect(settings.database_url, autocommit=True)
    install_schema(connection)
    if settings.reset:
        connection.execute(DEMO_RESET_SQL)
    stores = PostgresStoreBundle(connection)
    seed_demo_data(stores)
    service = InstanceReadService(
        workspace_store=stores.workspace,
        graph_topology_store=stores.graph_topology,
        activity_history_store=stores.activity_history,
        observed_state_store=stores.observed_state,
    )
    app = create_instance_read_app(service, token=settings.token)
    app.state.demo_connection = connection
    return app


def seed_demo_data(stores: PostgresStoreBundle) -> None:
    """Seed one workspace with enough topology to exercise every read route."""

    stores.workspace.create(
        WorkspaceRecord(
            workspace_id=DEMO_WORKSPACE_ID,
            name="Roadmap 0006 Read Demo",
            lifecycle=WorkspaceLifecycle.RUNNING,
            metadata={"purpose": "local-read-demo"},
        )
    )
    current_graph = _current_graph()
    desired_graph = _desired_graph()
    stores.graph_topology.save(
        GraphVersionRecord.from_graph(
            graph_id="demo-current",
            workspace_id=DEMO_WORKSPACE_ID,
            version=1,
            graph=current_graph,
            created_by="demo",
            created_at="2026-07-15T00:00:00Z",
            metadata={"kind": "current"},
        )
    )
    stores.graph_topology.save(
        GraphVersionRecord.from_graph(
            graph_id="demo-desired",
            workspace_id=DEMO_WORKSPACE_ID,
            version=2,
            graph=desired_graph,
            created_by="demo",
            created_at="2026-07-15T00:05:00Z",
            metadata={"kind": "desired"},
        )
    )
    stores.workspace.set_current_graph(DEMO_WORKSPACE_ID, "demo-current")
    stores.workspace.set_desired_graph(DEMO_WORKSPACE_ID, "demo-desired")
    stores.activity_history.add_session(
        OperationSessionRecord(
            session_id="demo-session-1",
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id="operator",
            title="Inspect read interfaces",
            status=OperationSessionStatus.OPEN,
            created_at="2026-07-15T00:10:00Z",
        )
    )
    stores.activity_history.add_action(
        OperationActionRecord(
            action_id="demo-action-1",
            session_id="demo-session-1",
            ordinal=1,
            action_type=OperationActionKind.INSPECT_CONTROL_SURFACE,
            actor_id="operator",
            payload={"node_id": "api-router"},
            created_at="2026-07-15T00:11:00Z",
        )
    )
    stores.observed_state.put(
        ObservationRecord(
            observation_id="demo-observation-router",
            workspace_id=DEMO_WORKSPACE_ID,
            subject_id="api-router",
            status="healthy",
            observed_at="2026-07-15T00:12:00Z",
            payload={"source": "demo-seed"},
        )
    )
    stores.observed_state.put(
        ObservationRecord(
            observation_id="demo-observation-api",
            workspace_id=DEMO_WORKSPACE_ID,
            subject_id="api",
            status="unknown",
            observed_at="2026-07-15T00:12:30Z",
            payload={"source": "demo-seed"},
            stale=True,
        )
    )


def _current_graph():
    postgres = DataBlock(
        spec=BlockSpec("postgres", display_name="Demo Postgres"),
        implementation=DockerPostgresImplementation(database="cpk"),
        sockets=BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
    )
    api = ApplicationBlock(
        spec=BlockSpec("api", display_name="Demo API"),
        implementation=PlanOnlyImplementation(kind="demo-api", output_urls={"internal": "http://api:8080"}),
        sockets=BlockSockets(
            requirements=(RequirementSocket("database", Protocol.POSTGRES, ("DATABASE_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    router = ProxyBlock(
        spec=BlockSpec(
            "api-router",
            display_name="API Router",
            capabilities=(CapabilityName.HEALTH_CHECKABLE, CapabilityName.SWITCHABLE),
        ),
        implementation=PlanOnlyImplementation(kind="demo-router", output_urls={"internal": "http://router:8080"}),
        sockets=BlockSockets(
            requirements=(RequirementSocket("active", Protocol.HTTP, ("ACTIVE_TARGET_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    return compile_recipe(
        DeploymentRecipe(
            "read-demo-current",
            DockerRuntime(
                children=(
                    postgres,
                    api,
                    router,
                    SocketConnection("postgres", "internal", "api", "database"),
                    SocketConnection("api", "internal", "api-router", "active"),
                )
            ),
        )
    )


def _desired_graph():
    return compile_recipe(
        DeploymentRecipe(
            "read-demo-desired",
            DockerRuntime(
                children=(
                    DataBlock(
                        spec=BlockSpec("postgres", display_name="Demo Postgres"),
                        implementation=DockerPostgresImplementation(database="cpk"),
                        sockets=BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
                    ),
                    ApplicationBlock(
                        spec=BlockSpec("api-v2", display_name="Demo API v2"),
                        implementation=PlanOnlyImplementation(
                            kind="demo-api",
                            output_urls={"internal": "http://api-v2:8080"},
                        ),
                        sockets=BlockSockets(
                            requirements=(RequirementSocket("database", Protocol.POSTGRES, ("DATABASE_URL",)),),
                            providers=(ProviderSocket("internal", Protocol.HTTP),),
                        ),
                    ),
                    ProxyBlock(
                        spec=BlockSpec(
                            "api-router",
                            display_name="API Router",
                            capabilities=(CapabilityName.HEALTH_CHECKABLE, CapabilityName.SWITCHABLE),
                        ),
                        implementation=PlanOnlyImplementation(
                            kind="demo-router",
                            output_urls={"internal": "http://router:8080"},
                        ),
                        sockets=BlockSockets(
                            requirements=(RequirementSocket("active", Protocol.HTTP, ("ACTIVE_TARGET_URL",)),),
                            providers=(ProviderSocket("internal", Protocol.HTTP),),
                        ),
                    ),
                    SocketConnection("postgres", "internal", "api-v2", "database"),
                    SocketConnection("api-v2", "internal", "api-router", "active"),
                )
            ),
        )
    )


def create_app_from_environment():
    """Uvicorn factory for the Docker demo server."""

    return create_demo_app(DemoSettings.from_environment())
