"""Seeded OCI product ACTIVITY proof through extracted operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import subprocess
from pathlib import Path
from urllib.request import urlopen

import psycopg

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime, SocketConnection
from control_plane_kit_core.planning import ActivityPlan
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.products import (
    ContainerServerProduct,
    ProductDescriptorCodec,
    ProductInstanceConfiguration,
    instantiate_product,
)
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, compile_topology
from control_plane_kit_interpreters.docker import DockerRuntimeInterpreter, DockerSdkClient
from control_plane_kit_operations.admission import ExecutionAdmissionCommandService
from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService
from control_plane_kit_operations.approvals import ApprovalCommandService
from control_plane_kit_operations.coordinator import ExecutionCoordinator, RuntimeInterpreterDispatcher
from control_plane_kit_operations.cpk_server import CpkServerOperationsApplication, cpk_server_services
from control_plane_kit_operations.lifecycle import RunLifecycleCommandService
from control_plane_kit_operations.planning import ActivityPlanningCommandService, DesiredGraphCommandService
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.products import ProductRegistrationService
from control_plane_kit_operations.workflows import OperationCommandService
from control_plane_kit_operations.workspaces import WorkspaceCommandService


WORKER = "activity-worker"


@dataclass(frozen=True)
class RouteRequest:
    surface: str
    route_id: str
    service_role: object
    path_parameters: dict[str, str]
    payload: dict[str, object]


class GeneratedIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.next = 0

    def __call__(self) -> str:
        self.next += 1
        return f"{self.prefix}-{self.next}"


def main() -> int:
    database_url = _required_env("CPK_ACTIVITY_DATABASE_URL")
    servers_repo = Path(_required_env("CPK_ACTIVITY_SERVERS_REPO"))
    with psycopg.connect(database_url, autocommit=True) as connection:
        install_schema(connection)
        connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    products = {
        name: _product(servers_repo, name)
        for name in (
            "hello_server",
            "http_active_router",
            "http_multiplexer",
            "postgres_server",
        )
    }
    app = _application(database_url)

    _run_transition(
        app,
        workspace_id="activity-live-basic",
        title="Hello deployment",
        products=products,
        desired=_graph(
            "activity-live-basic",
            (_block(products["hello_server"], "hello"),),
        ),
    )
    _assert_body("http://hello:8000/", "Hello, world!\n")

    router_graph = _graph(
        "activity-live-router",
        (
            _block(products["hello_server"], "blue"),
            _block(products["http_active_router"], "router"),
            SocketConnection("blue", "internal", "router", "active"),
        ),
    )
    router_state = _run_transition(
        app,
        workspace_id="activity-live-router",
        title="Router deployment",
        products=products,
        desired=router_graph,
    )
    _assert_body("http://router:8000/", "Hello, world!\n")

    mux_graph = _graph(
        "activity-live-multiplexer",
        (
            _block(products["hello_server"], "primary"),
            _block(products["hello_server"], "observer"),
            _block(products["http_multiplexer"], "multiplexer"),
            SocketConnection("primary", "internal", "multiplexer", "primary"),
            SocketConnection("observer", "internal", "multiplexer", "observer-a"),
        ),
    )
    _run_transition(
        app,
        workspace_id="activity-live-multiplexer",
        title="Multiplexer deployment",
        products=products,
        desired=mux_graph,
    )
    _assert_body("http://multiplexer:8000/", "Hello, world!\n")

    green_graph = _graph(
        "activity-live-router",
        (
            _block(products["hello_server"], "blue"),
            _block(products["hello_server"], "green"),
            _block(products["http_active_router"], "router"),
            SocketConnection("green", "internal", "router", "active"),
        ),
    )
    updated = _run_transition(
        app,
        workspace_id="activity-live-router",
        title="Router transition",
        products=products,
        desired=green_graph,
        current_graph_id=router_state.current_graph_id,
        expected_desired_graph_id=router_state.desired_graph_id,
    )
    _assert_body("http://router:8000/", "Hello, world!\n")

    _disconnect_controller_runtime_networks()
    _run_transition(
        app,
        workspace_id="activity-live-router",
        title="Router teardown",
        products=products,
        desired=DeploymentGraph("activity-live-router-empty"),
        current_graph_id=updated.current_graph_id,
        expected_desired_graph_id=updated.desired_graph_id,
        connect_controller=False,
    )

    print("seeded ACTIVITY scenarios passed")
    return 0


@dataclass(frozen=True)
class TransitionState:
    current_graph_id: str
    desired_graph_id: str


def _application(database_url: str) -> CpkServerOperationsApplication:
    def unit_of_work() -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    lifecycle = RunLifecycleCommandService(
        unit_of_work,
        clock=_clock,
        id_factory=GeneratedIds("lifecycle"),
    )
    adapter = RuntimeInterpreterDispatcher(
        {
            RuntimeKind.DOCKER: DockerRuntimeInterpreter(DockerSdkClient()),
        }
    )
    return CpkServerOperationsApplication(
        cpk_server_services(
            unit_of_work_factory=unit_of_work,
            planning=ActivityPlanningCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=GeneratedIds("plan"),
            ),
            workspaces=WorkspaceCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=GeneratedIds("workspace"),
            ),
            products=ProductRegistrationService(unit_of_work),
            desired_graphs=DesiredGraphCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=GeneratedIds("desired"),
            ),
            approval=ApprovalCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=GeneratedIds("approval"),
            ),
            admission=ExecutionAdmissionCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=GeneratedIds("admission"),
            ),
            lifecycle=lifecycle,
            operations=OperationCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=GeneratedIds("session"),
            ),
            execution=ExecutionCoordinator(
                unit_of_work,
                lifecycle=lifecycle,
                adapter=adapter,
                clock=_clock,
                id_factory=GeneratedIds("execution"),
            ),
            advancement=CurrentGraphAdvancementCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=GeneratedIds("advance"),
            ),
            clock=lambda: datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
        )
    )


def _run_transition(
    app: CpkServerOperationsApplication,
    *,
    workspace_id: str,
    title: str,
    products: dict[str, ContainerServerProduct],
    desired: DeploymentGraph,
    current_graph_id: str | None = None,
    expected_desired_graph_id: str | None = None,
    connect_controller: bool = True,
) -> TransitionState:
    if current_graph_id is None:
        workspace = _handle(app, "http", "planning", "command.workspace.create", {}, {
            "workspace_id": workspace_id,
            "name": workspace_id,
            "actor_id": "operator-a",
            "idempotency_key": f"{workspace_id}:create",
        })
        current_graph_id = str(workspace["workspace"]["current_graph_id"])
        for key, product in products.items():
            document = ProductDescriptorCodec().encode_document(product)
            _handle(app, "http", "planning", "command.product.import", {"workspace_id": workspace_id}, {
                "descriptor_document": json.loads(document.content.decode("utf-8")),
                "actor_id": "operator-a",
                "imported_at": _clock(),
                "idempotency_key": f"{workspace_id}:import:{key}",
            })
    session = _handle(app, "http", "lifecycle", "command.operation-session.start", {"workspace_id": workspace_id}, {
        "actor_id": "operator-a",
        "title": title,
        "idempotency_key": f"{workspace_id}:{title}:session",
    })
    session_id = str(session["session_id"])
    desired_result = _handle(app, "http", "planning", "command.desired-graph.set", {"workspace_id": workspace_id}, {
        "session_id": session_id,
        "actor_id": "operator-a",
        "graph": DEFAULT_GRAPH_CODEC.encode(desired),
        "expected_desired_graph_id": expected_desired_graph_id,
        "idempotency_key": f"{workspace_id}:{title}:desired",
    })
    desired_graph_id = str(desired_result["desired_graph_id"])
    planned = _handle(app, "mcp", "planning", "command.deployment.plan", {}, {
        "workspace_id": workspace_id,
        "session_id": session_id,
        "actor_id": "operator-a",
        "expected_current_graph_id": current_graph_id,
        "expected_desired_graph_id": desired_graph_id,
        "idempotency_key": f"{workspace_id}:{title}:plan",
    })
    plan_id = str(planned["plan_id"])
    if not planned.get("ready_for_approval", True):
        raise RuntimeError(f"plan was not approval-ready: {planned}")
    requested = _handle(app, "http", "approval", "command.approval.request", {"workspace_id": workspace_id, "plan_id": plan_id}, {
        "session_id": session_id,
        "actor_id": "operator-a",
        "actor_scopes": [PolicyScope.PLAN_REQUEST.value],
        "idempotency_key": f"{workspace_id}:{title}:approval-request",
    })
    approval_id = str(requested["request_id"])
    pending = _handle(app, "http", "reads", "read.pending-approvals", {"workspace_id": workspace_id}, {"limit": 10, "offset": 0})
    if approval_id not in {item["request_id"] for item in pending["items"]}:
        raise RuntimeError("approval request was not visible in pending queue")
    detail = _handle(app, "mcp", "reads", "read.approval-detail", {}, {"workspace_id": workspace_id, "approval_id": approval_id})
    if detail["plan"]["plan_id"] != plan_id:
        raise RuntimeError("approval detail did not expose the planned graph transition")
    _handle(app, "mcp", "approval", "command.approval.decide", {}, {
        "session_id": session_id,
        "request_id": approval_id,
        "actor_id": "manager-a",
        "actor_scopes": [requested["required_scope"]],
        "decision": "approved",
        "idempotency_key": f"{workspace_id}:{title}:approval-decision",
    })
    admitted = _handle(app, "http", "admission", "command.deployment.admit", {"workspace_id": workspace_id, "plan_id": plan_id}, {
        "session_id": session_id,
        "approval_request_id": approval_id,
        "actor_id": "operator-a",
        "actor_scopes": [PolicyScope.PLAN_EXECUTE.value],
        "idempotency_key": f"{workspace_id}:{title}:admit",
        "readiness": [],
    })
    request_id = str(admitted["execution_request_id"])
    claimed = _handle(app, "http", "lifecycle", "command.run.claim", {"workspace_id": workspace_id, "run_id": request_id}, {
        "worker_id": WORKER,
        "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
        "lease_expires_at": "2026-07-22T12:00:00Z",
        "idempotency_key": f"{workspace_id}:{title}:claim",
    })
    run_id = str(claimed["run_id"])
    _handle(app, "http", "execution", "command.run.start", {"workspace_id": workspace_id, "run_id": run_id}, {
        "worker_id": WORKER,
        "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
        "idempotency_key": f"{workspace_id}:{title}:start",
    })
    _execute_to_completion(
        app,
        workspace_id,
        run_id,
        title,
        connect_controller=connect_controller,
    )
    advanced = _handle(app, "http", "lifecycle", "command.graph.advance-current", {"workspace_id": workspace_id, "run_id": run_id}, {
        "plan_id": plan_id,
        "expected_current_graph_id": current_graph_id,
        "desired_graph_id": desired_graph_id,
        "worker_id": WORKER,
        "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
        "idempotency_key": f"{workspace_id}:{title}:advance",
    })
    return TransitionState(str(advanced["to_graph_id"]), desired_graph_id)


def _execute_to_completion(
    app: CpkServerOperationsApplication,
    workspace_id: str,
    run_id: str,
    title: str,
    connect_controller: bool = True,
) -> None:
    for attempt in range(80):
        if connect_controller:
            _sync_controller_runtime_networks()
        result = _handle(app, "mcp", "execution", "command.deployment.execute", {}, {
            "run_id": run_id,
            "worker_id": WORKER,
            "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
            "idempotency_key": f"{run_id}:execute:{attempt}",
            "max_effects": 1,
        })
        if connect_controller:
            _sync_controller_runtime_networks()
        if result["coordinator_status"] == "completed":
            return
        if result["coordinator_status"] in {"failed", "unsupported", "uncertain", "blocked"}:
            timeline = _handle(
                app,
                "http",
                "reads",
                "read.activity",
                {"workspace_id": workspace_id},
                {"limit": 50},
            )
            raise RuntimeError(f"{title} stopped with {result}; timeline={timeline}")
    raise RuntimeError(f"{title} did not complete")


def _sync_controller_runtime_networks() -> None:
    controller = _required_env("CPK_ACTIVITY_CONTROLLER")
    networks = subprocess.run(
        ["docker", "network", "ls", "--format", "{{.Name}}"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.splitlines()
    for network in networks:
        if network.startswith("cpk-net-activity-live-"):
            _connect_controller_to_network(controller, network)


def _connect_controller_to_network(controller: str, network: str) -> None:
    result = subprocess.run(
        ["docker", "network", "connect", network, controller],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    if result.returncode == 0:
        return
    if "already exists" in result.stderr.lower():
        return
    raise RuntimeError(
        f"failed to connect controller to {network}: {result.stderr.strip()}"
    )


def _disconnect_controller_runtime_networks() -> None:
    controller = _required_env("CPK_ACTIVITY_CONTROLLER")
    networks = subprocess.run(
        ["docker", "network", "ls", "--format", "{{.Name}}"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.splitlines()
    for network in networks:
        if network.startswith("cpk-net-activity-live-"):
            _disconnect_controller_from_network(controller, network)


def _disconnect_controller_from_network(controller: str, network: str) -> None:
    result = subprocess.run(
        ["docker", "network", "disconnect", network, controller],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    if result.returncode == 0:
        return
    lowered = result.stderr.lower()
    if "not connected" in lowered or "no such" in lowered:
        return
    raise RuntimeError(
        f"failed to disconnect controller from {network}: {result.stderr.strip()}"
    )


def _handle(app, surface: str, role: str, route_id: str, path: dict[str, str], payload: dict[str, object]):  # noqa: ANN001
    from control_plane_kit_core.operations import ControlPlaneServiceRole

    return app.handle(
        RouteRequest(surface, route_id, ControlPlaneServiceRole(role), path, payload)
    )


def _graph(name: str, children: tuple[object, ...]) -> DeploymentGraph:
    return compile_topology(
        DeploymentTopology(
            name,
            DockerRuntime(
                runtime_id="docker",
                network_name=f"control-plane-kit-{name}-docker",
                children=children,
            ),
        )
    )


def _block(product: ContainerServerProduct, role_id: str):  # noqa: ANN201
    return instantiate_product(
        product,
        role_id,
        ProductInstanceConfiguration.from_contract(product.runtime_contract),
    )


def _product(servers_repo: Path, name: str) -> ContainerServerProduct:
    path = servers_repo / "products" / name / "product.cpk.json"
    return ProductDescriptorCodec().decode_document(path.read_bytes()).product


def _assert_body(url: str, expected: str) -> None:
    with urlopen(url, timeout=5) as response:
        body = response.read(1024).decode("utf-8")
    if body != expected:
        raise RuntimeError(f"unexpected response from {url}: {body!r}")


def _clock() -> str:
    return "2026-07-22T10:00:00Z"


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
