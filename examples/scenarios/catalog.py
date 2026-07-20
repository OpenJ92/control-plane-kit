"""Graph-transition scenarios of increasing structural complexity."""

from __future__ import annotations

from dataclasses import replace

from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DataBlock,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerPostgresImplementation,
    DockerRuntime,
    PlanOnlyImplementation,
    Protocol,
    ProviderSocket,
    RequirementSocket,
    RuntimeContext,
    RuntimeKind,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.core.planning import (
    ReconcileNode,
    ReconcileRuntime,
    RemoveNodeResource,
    RemoveRuntimeResource,
    ReviewChange,
    RiskLevel,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    WaitForHealthy,
)
from control_plane_kit.servers import (
    hello_server_block,
    http_active_router_block,
)
from control_plane_kit.core.topology import DeploymentGraph
from examples.http_block_compositions import (
    multiplexer_recipe,
    rate_limiter_recipe,
    weighted_balancer_recipe,
)
from examples.router_runtime import router_graph
from examples.scenarios.model import (
    DependencyExpectation,
    OperationExpectation,
    PlanningScenario,
    ScenarioExpectation,
)


def planning_scenarios() -> tuple[PlanningScenario, ...]:
    """Return the canonical cross-roadmap planning scenario corpus."""

    return (
        fresh_deployment(),
        backend_switch(),
        scale_out_behind_load_balancer(),
        insert_rate_limiter(),
        add_request_observer(),
        move_service_between_runtimes(),
        switch_database_endpoint(),
        partial_scale_in(),
        full_teardown(),
        no_change(),
        unsupported_implementation_transition(),
    )


def fresh_deployment() -> PlanningScenario:
    desired = _docker_graph(
        "fresh-deployment",
        hello_server_block("api", message="Hello from API"),
    )
    start_runtime = _op(StartRuntime, "docker")
    start_api = _op(StartNode, "api")
    healthy_api = _op(WaitForHealthy, "api")
    return _scenario(
        "fresh-deployment",
        "Start a fresh application",
        DeploymentGraph("empty"),
        desired,
        operations=(start_runtime, start_api, healthy_api),
        dependencies=(
            _dependency(start_runtime, start_api),
            _dependency(start_api, healthy_api),
        ),
        max_risk=RiskLevel.MEDIUM,
    )


def backend_switch() -> PlanningScenario:
    return _scenario(
        "backend-switch",
        "Switch an active backend",
        router_graph("api-v1"),
        router_graph("api-v2"),
        operations=(
            _op(ReconcileNode, "api-router"),
        ),
        max_risk=RiskLevel.MEDIUM,
    )


def scale_out_behind_load_balancer() -> PlanningScenario:
    desired = compile_recipe(weighted_balancer_recipe())
    current = _retain(desired, "scale-out-current", nodes=("app-a",))
    start_app = _op(StartNode, "app-b")
    healthy_app = _op(WaitForHealthy, "app-b")
    start_balancer = _op(StartNode, "balancer")
    healthy_balancer = _op(WaitForHealthy, "balancer")
    return _scenario(
        "scale-out-load-balancer",
        "Scale an application behind a load balancer",
        current,
        desired,
        operations=(
            start_app,
            healthy_app,
            start_balancer,
            healthy_balancer,
            _op(ReconcileRuntime, "docker"),
        ),
        dependencies=(
            _dependency(start_app, healthy_app),
            _dependency(start_balancer, healthy_balancer),
            _dependency(healthy_app, start_balancer),
        ),
        max_risk=RiskLevel.MEDIUM,
    )


def insert_rate_limiter() -> PlanningScenario:
    desired = compile_recipe(rate_limiter_recipe())
    current = _retain(desired, "rate-limiter-current", nodes=("app",))
    start = _op(StartNode, "limiter")
    healthy = _op(WaitForHealthy, "limiter")
    return _scenario(
        "insert-rate-limiter",
        "Insert a rate limiter",
        current,
        desired,
        operations=(
            start,
            healthy,
            _op(ReconcileRuntime, "docker"),
        ),
        dependencies=(
            _dependency(start, healthy),
        ),
        max_risk=RiskLevel.MEDIUM,
    )


def add_request_observer() -> PlanningScenario:
    desired = compile_recipe(multiplexer_recipe())
    current = _retain(desired, "observer-current", nodes=("primary",))
    start_observer = _op(StartNode, "observer")
    healthy_observer = _op(WaitForHealthy, "observer")
    start_mux = _op(StartNode, "multiplexer")
    healthy_mux = _op(WaitForHealthy, "multiplexer")
    return _scenario(
        "add-request-observer",
        "Add an HTTP request observer",
        current,
        desired,
        operations=(
            start_observer,
            healthy_observer,
            start_mux,
            healthy_mux,
            _op(ReconcileRuntime, "docker"),
        ),
        dependencies=(
            _dependency(start_observer, healthy_observer),
            _dependency(start_mux, healthy_mux),
            _dependency(healthy_observer, start_mux),
        ),
        max_risk=RiskLevel.MEDIUM,
    )


def move_service_between_runtimes() -> PlanningScenario:
    current = _runtime_move_graph("runtime-move-current", source="runtime-a")
    desired = _runtime_move_graph("runtime-move-desired", source="runtime-b")
    return _scenario(
        "move-service-runtime",
        "Move a service between runtimes",
        current,
        desired,
        operations=(
            _op(ReconcileNode, "worker"),
            _op(ReconcileRuntime, "runtime-a"),
            _op(ReconcileRuntime, "runtime-b"),
        ),
        max_risk=RiskLevel.MEDIUM,
    )


def switch_database_endpoint() -> PlanningScenario:
    current = _database_graph(
        "database-current",
        active_database_id="postgres-a",
    )
    desired = _database_graph(
        "database-desired",
        active_database_id="postgres-b",
    )
    return _scenario(
        "switch-database-endpoint",
        "Switch between pre-provisioned database endpoints",
        current,
        desired,
        operations=(
            _op(ReconcileNode, "api"),
        ),
        max_risk=RiskLevel.MEDIUM,
    )


def partial_scale_in() -> PlanningScenario:
    current = router_graph("api-v2")
    desired = _retain(
        current,
        "partial-scale-in",
        nodes=("api-v2", "api-router"),
        edges=("api-router.active",),
    )
    return _scenario(
        "partial-scale-in",
        "Remove an inactive backend",
        current,
        desired,
        operations=(
            _op(StopNode, "api-v1"),
            _op(RemoveNodeResource, "api-v1"),
            _op(ReconcileRuntime, "docker"),
        ),
        max_risk=RiskLevel.HIGH,
    )


def full_teardown() -> PlanningScenario:
    current = compile_recipe(rate_limiter_recipe())
    stop_app = _op(StopNode, "app")
    stop_limiter = _op(StopNode, "limiter")
    remove_app = _op(RemoveNodeResource, "app")
    remove_limiter = _op(RemoveNodeResource, "limiter")
    stop_runtime = _op(StopRuntime, "docker")
    remove_runtime = _op(RemoveRuntimeResource, "docker")
    return _scenario(
        "full-teardown",
        "Tear down a deployment",
        current,
        DeploymentGraph("empty"),
        operations=(
            stop_app,
            stop_limiter,
            remove_app,
            remove_limiter,
            stop_runtime,
            remove_runtime,
        ),
        dependencies=(
            _dependency(stop_app, remove_app),
            _dependency(stop_limiter, remove_limiter),
            _dependency(remove_app, stop_runtime),
            _dependency(remove_limiter, stop_runtime),
            _dependency(stop_runtime, remove_runtime),
        ),
        max_risk=RiskLevel.HIGH,
    )


def no_change() -> PlanningScenario:
    graph = router_graph("api-v1")
    return _scenario(
        "no-change",
        "Keep an unchanged deployment",
        graph,
        graph,
        operations=(),
        max_risk=RiskLevel.INFORMATIONAL,
    )


def unsupported_implementation_transition() -> PlanningScenario:
    current = _implementation_graph("implementation-current", kind="docker")
    desired = _implementation_graph("implementation-desired", kind="plan-only")
    return _scenario(
        "unsupported-implementation-transition",
        "Review an implementation-kind transition",
        current,
        desired,
        operations=(
            _op(ReviewChange, "api"),
            _op(ReconcileNode, "api"),
        ),
        max_risk=RiskLevel.HIGH,
        ready_for_execution=False,
    )


def _scenario(
    scenario_id: str,
    title: str,
    current: DeploymentGraph,
    desired: DeploymentGraph,
    *,
    operations: tuple[OperationExpectation, ...],
    dependencies: tuple[DependencyExpectation, ...] = (),
    max_risk: RiskLevel,
    ready_for_execution: bool = True,
) -> PlanningScenario:
    return PlanningScenario(
        scenario_id=scenario_id,
        title=title,
        approval_comment=f"Review the {title.lower()} plan before execution.",
        current_graph=current,
        desired_graph=desired,
        expectation=ScenarioExpectation(
            operations=operations,
            required_dependencies=dependencies,
            max_risk=max_risk,
            ready_for_execution=ready_for_execution,
        ),
    )


def _op(operation_type: type[object], target_id: str) -> OperationExpectation:
    return OperationExpectation(operation_type, target_id)


def _dependency(
    predecessor: OperationExpectation,
    successor: OperationExpectation,
) -> DependencyExpectation:
    return DependencyExpectation(predecessor, successor)


def _docker_graph(name: str, *children: object) -> DeploymentGraph:
    return compile_recipe(
        DeploymentRecipe(name, DockerRuntime(children=tuple(children)))
    )


def _retain(
    graph: DeploymentGraph,
    name: str,
    *,
    nodes: tuple[str, ...],
    edges: tuple[str, ...] = (),
) -> DeploymentGraph:
    retained_nodes = {node_id: graph.nodes[node_id] for node_id in nodes}
    retained_edges = {edge_id: graph.edges[edge_id] for edge_id in edges}
    retained_runtimes = {
        runtime_id: replace(
            runtime,
            children=tuple(
                child for child in runtime.children if child in retained_nodes
            ),
        )
        for runtime_id, runtime in graph.runtimes.items()
    }
    return DeploymentGraph(name, retained_nodes, retained_edges, retained_runtimes)


def _runtime_move_graph(name: str, *, source: str) -> DeploymentGraph:
    worker = ApplicationBlock(
        BlockSpec("worker", "Worker"),
        PlanOnlyImplementation("worker", {"internal": "http://worker"}),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    runtime_a = RuntimeContext(
        "runtime-a",
        RuntimeKind.DRY_RUN,
        children=(worker,) if source == "runtime-a" else (),
    )
    runtime_b = RuntimeContext(
        "runtime-b",
        RuntimeKind.DRY_RUN,
        children=(worker,) if source == "runtime-b" else (),
    )
    root = RuntimeContext(
        "deployment",
        RuntimeKind.DRY_RUN,
        children=(runtime_a, runtime_b),
    )
    return compile_recipe(DeploymentRecipe(name, root))


def _database_graph(
    name: str,
    *,
    active_database_id: str,
) -> DeploymentGraph:
    api = ApplicationBlock(
        BlockSpec("api", "API"),
        DockerImageImplementation("api:latest", ports={"internal": 8000}),
        BlockSockets(
            requirements=(
                RequirementSocket(
                    "DATABASE_URL",
                    Protocol.POSTGRES,
                    ("DATABASE_URL",),
                ),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    databases = tuple(
        DataBlock(
            BlockSpec(database_id, f"Postgres {database_id[-1].upper()}"),
            DockerPostgresImplementation(database="app"),
            BlockSockets(
                providers=(ProviderSocket("internal", Protocol.POSTGRES),)
            ),
        )
        for database_id in ("postgres-a", "postgres-b")
    )
    return _docker_graph(
        name,
        api,
        *databases,
        SocketConnection(
            active_database_id,
            "internal",
            "api",
            "DATABASE_URL",
            edge_id="api.database",
        ),
    )


def _implementation_graph(name: str, *, kind: str) -> DeploymentGraph:
    implementation = (
        DockerImageImplementation("api:latest", ports={"internal": 8000})
        if kind == "docker"
        else PlanOnlyImplementation("plan-only", {"internal": "http://api:8000"})
    )
    api = ApplicationBlock(
        BlockSpec("api", "API"),
        implementation,
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    return _docker_graph(name, api)
