"""Drive recursive cpk-server acceptance over the parent HTTP/MCP surface."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import time
from typing import Any
from urllib.request import urlopen

import docker
from docker.errors import APIError, NotFound

from control_plane_kit_core.algebra import (
    DeploymentTopology,
    DockerRuntime,
    SocketConnection,
)
from control_plane_kit_core.products import (
    ProductDescriptorCodec,
    ProductInstanceConfiguration,
    instantiate_product,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, compile_topology
from control_plane_kit_core.policies import PolicyScope

from cpk_server_hosted_activity import (
    _clock,
    _http,
    _mcp_read,
    _mcp_tool,
    _required_env,
    _wait_ready,
)


WORKSPACE_ID = "recursive-cpk-server"
WORKER_ID = "recursive-worker"


def main() -> int:
    base_url = _required_env("CPK_RECURSIVE_BASE_URL").rstrip("/")
    parent_container = _required_env("CPK_RECURSIVE_PARENT_CONTAINER")
    servers_repo = Path(_required_env("CPK_RECURSIVE_SERVERS_REPO"))

    _wait_ready(base_url)
    cpk_document = _product_document(servers_repo, "cpk_server")
    postgres_document = _product_document(servers_repo, "postgres_server")
    graph = _recursive_graph(cpk_document, postgres_document)

    workspace = _http(
        base_url,
        "POST",
        "/workspaces",
        {
            "workspace_id": WORKSPACE_ID,
            "name": "Recursive cpk-server acceptance",
            "actor_id": "operator-a",
            "idempotency_key": f"{WORKSPACE_ID}:workspace",
        },
    )
    current_graph_id = str(workspace["workspace"]["current_graph_id"])

    _import_product(base_url, "postgres", postgres_document)
    _import_product(base_url, "cpk-server", cpk_document)
    if os.environ.get("CPK_RECURSIVE_REGISTER_PULL_AUTHORITY") == "docker-config":
        _http(
            base_url,
            "POST",
            f"/workspaces/{WORKSPACE_ID}/image-pull-authorities",
            {
                "registry": "ghcr.io",
                "repository": "openj92/control-plane-kit-servers",
                "credential_reference": "secret://docker-config/ghcr.io",
                "actor_id": "operator-a",
                "admitted_at": _clock(),
                "idempotency_key": f"{WORKSPACE_ID}:pull-authority:ghcr",
            },
        )

    session = _http(
        base_url,
        "POST",
        f"/workspaces/{WORKSPACE_ID}/sessions",
        {
            "actor_id": "operator-a",
            "title": "Recursive cpk-server deployment",
            "idempotency_key": f"{WORKSPACE_ID}:session",
        },
    )
    session_id = str(session["session_id"])

    desired = _http(
        base_url,
        "POST",
        f"/workspaces/{WORKSPACE_ID}/graphs/desired",
        {
            "session_id": session_id,
            "actor_id": "operator-a",
            "graph": DEFAULT_GRAPH_CODEC.encode(graph),
            "expected_desired_graph_id": None,
            "idempotency_key": f"{WORKSPACE_ID}:desired",
        },
    )
    desired_graph_id = str(desired["desired_graph_id"])

    planned = _mcp_tool(
        base_url,
        "command.deployment.plan",
        {
            "workspace_id": WORKSPACE_ID,
            "session_id": session_id,
            "actor_id": "operator-a",
            "expected_current_graph_id": current_graph_id,
            "expected_desired_graph_id": desired_graph_id,
            "idempotency_key": f"{WORKSPACE_ID}:plan",
        },
    )
    plan_id = str(planned["plan_id"])
    if not planned.get("ready_for_execution", False):
        raise RuntimeError(f"recursive plan was not approval-ready: {planned}")

    requested = _http(
        base_url,
        "POST",
        f"/workspaces/{WORKSPACE_ID}/plans/{plan_id}/approval",
        {
            "session_id": session_id,
            "actor_id": "operator-a",
            "actor_scopes": [PolicyScope.PLAN_REQUEST.value],
            "idempotency_key": f"{WORKSPACE_ID}:approval-request",
        },
    )
    approval_id = str(requested["request_id"])
    _assert_approval_visible(base_url, approval_id, plan_id)

    _mcp_tool(
        base_url,
        "command.approval.decide",
        {
            "session_id": session_id,
            "request_id": approval_id,
            "actor_id": "manager-a",
            "actor_scopes": [requested["required_scope"]],
            "decision": "approved",
            "idempotency_key": f"{WORKSPACE_ID}:approval-decision",
        },
    )

    admitted = _http(
        base_url,
        "POST",
        f"/workspaces/{WORKSPACE_ID}/plans/{plan_id}/admission",
        {
            "session_id": session_id,
            "approval_request_id": approval_id,
            "actor_id": "operator-a",
            "actor_scopes": [PolicyScope.PLAN_EXECUTE.value],
            "idempotency_key": f"{WORKSPACE_ID}:admit",
            "readiness": [],
        },
    )
    request_id = str(admitted["execution_request_id"])
    claimed = _http(
        base_url,
        "POST",
        f"/workspaces/{WORKSPACE_ID}/runs/{request_id}/claim",
        {
            "worker_id": WORKER_ID,
            "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
            "lease_expires_at": "2026-07-22T12:00:00Z",
            "idempotency_key": f"{WORKSPACE_ID}:claim",
        },
    )
    run_id = str(claimed["run_id"])

    _http(
        base_url,
        "POST",
        f"/workspaces/{WORKSPACE_ID}/runs/{run_id}/start",
        {
            "worker_id": WORKER_ID,
            "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
            "idempotency_key": f"{WORKSPACE_ID}:start",
        },
    )

    _execute_to_completion(base_url, parent_container, run_id)
    _assert_parent_observations(base_url, run_id)
    _assert_child_health()

    advanced = _http(
        base_url,
        "POST",
        f"/workspaces/{WORKSPACE_ID}/runs/{run_id}/advance-current-graph",
        {
            "plan_id": plan_id,
            "expected_current_graph_id": current_graph_id,
            "desired_graph_id": desired_graph_id,
            "worker_id": WORKER_ID,
            "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
            "idempotency_key": f"{WORKSPACE_ID}:advance",
        },
    )
    if advanced["to_graph_id"] != desired_graph_id:
        raise RuntimeError(f"current graph did not advance: {advanced}")

    current = _http(base_url, "GET", f"/workspaces/{WORKSPACE_ID}/graphs/current")
    if current["graph_id"] != desired_graph_id:
        raise RuntimeError(f"current graph readback mismatch: {current}")

    print("recursive cpk-server Docker activity smoke passed")
    return 0


def _import_product(base_url: str, label: str, document: Any) -> None:
    _http(
        base_url,
        "POST",
        f"/workspaces/{WORKSPACE_ID}/products/import",
        {
            "descriptor_document": json.loads(document.content.decode("utf-8")),
            "actor_id": "operator-a",
            "imported_at": _clock(),
            "idempotency_key": f"{WORKSPACE_ID}:import:{label}",
        },
    )


def _assert_approval_visible(base_url: str, approval_id: str, plan_id: str) -> None:
    pending = _mcp_read(
        base_url,
        "read.pending-approvals",
        {"workspace_id": WORKSPACE_ID, "limit": 10, "offset": 0},
    )
    if approval_id not in {item["request_id"] for item in pending["items"]}:
        raise RuntimeError("recursive approval request was not visible")
    detail = _mcp_read(
        base_url,
        "read.approval-detail",
        {"workspace_id": WORKSPACE_ID, "approval_id": approval_id},
    )
    if detail["plan"]["plan_id"] != plan_id:
        raise RuntimeError("recursive approval detail exposed the wrong plan")


def _execute_to_completion(base_url: str, parent_container: str, run_id: str) -> None:
    for attempt in range(140):
        _sync_runtime_networks(parent_container)
        result = _mcp_tool(
            base_url,
            "command.deployment.execute",
            {
                "run_id": run_id,
                "worker_id": WORKER_ID,
                "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
                "idempotency_key": f"{WORKSPACE_ID}:execute:{attempt}",
                "max_effects": 1,
            },
        )
        _sync_runtime_networks(parent_container)
        if result["coordinator_status"] == "completed":
            return
        if result["coordinator_status"] in {"failed", "unsupported", "uncertain", "blocked"}:
            timeline = _http(base_url, "GET", f"/workspaces/{WORKSPACE_ID}/activity")
            raise RuntimeError(f"recursive execution stopped with {result}; timeline={timeline}")
    raise RuntimeError("recursive activity execution did not complete")


def _assert_parent_observations(base_url: str, run_id: str) -> None:
    timeline = _mcp_read(
        base_url,
        "read.activity",
        {"workspace_id": WORKSPACE_ID, "limit": 200},
    )
    events = _events_for_run(timeline, run_id)
    _assert_step_evidence(
        events,
        node_id="child-postgres",
        action="created",
        image_contains="docker.io/library/postgres@sha256:",
    )
    _assert_health_evidence(
        events,
        node_id="child-postgres",
        capability="postgres",
        expected_checks={"select-one"},
    )
    _assert_step_evidence(
        events,
        node_id="child-cpk",
        action="created",
        image_contains="ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:",
    )
    _assert_health_evidence(
        events,
        node_id="child-cpk",
        capability="http",
        expected_checks={"live", "ready"},
    )


def _events_for_run(timeline: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    for session in timeline.get("sessions", []):
        for plan in session.get("plans", []):
            for run in plan.get("runs", []):
                if run.get("run_id") == run_id:
                    events = run.get("events")
                    if isinstance(events, list):
                        return events
    raise RuntimeError(f"parent activity timeline did not expose run {run_id}")


def _assert_step_evidence(
    events: list[dict[str, Any]],
    *,
    node_id: str,
    action: str,
    image_contains: str,
) -> None:
    for event in events:
        if event.get("event_type") != "step_succeeded":
            continue
        payload = event.get("payload", {})
        if payload.get("node_id") != node_id:
            continue
        if payload.get("action") != action:
            continue
        if image_contains not in str(payload.get("image", "")):
            raise RuntimeError(f"parent recorded wrong image evidence for {node_id}: {payload}")
        if not str(payload.get("container", "")).startswith(
            f"cpk-node-{WORKSPACE_ID}-{node_id}-"
        ):
            raise RuntimeError(f"parent recorded wrong container evidence for {node_id}: {payload}")
        return
    raise RuntimeError(f"parent did not record {action} evidence for {node_id}")


def _assert_health_evidence(
    events: list[dict[str, Any]],
    *,
    node_id: str,
    capability: str,
    expected_checks: set[str],
) -> None:
    observed: set[str] = set()
    for event in events:
        if event.get("event_type") != "step_succeeded":
            continue
        payload = event.get("payload", {})
        if payload.get("node_id") != node_id:
            continue
        if payload.get("action") != "verified-healthy":
            continue
        for check in payload.get("checks", []):
            identity = check.get("identity", {})
            if check.get("capability") != capability:
                continue
            if check.get("outcome") != "passed":
                raise RuntimeError(f"parent recorded failed health evidence for {node_id}: {check}")
            observed.add(str(identity.get("check_id")))
    missing = expected_checks.difference(observed)
    if missing:
        raise RuntimeError(f"parent did not record health evidence for {node_id}: {missing}")


def _sync_runtime_networks(parent_container: str) -> None:
    client = docker.from_env()
    controller_container = socket.gethostname()
    for network in client.networks.list():
        name = network.name
        if not name.startswith(f"cpk-net-{WORKSPACE_ID}"):
            continue
        for container in (parent_container, controller_container):
            try:
                network.connect(container)
            except APIError as error:
                if "already exists" in str(error).lower():
                    continue
                raise
            except NotFound:
                continue


def _assert_child_health() -> None:
    _assert_json("http://child-cpk:8080/health/live", {"status": "live"})
    ready = _json("http://child-cpk:8080/health/ready")
    if ready.get("status") != "ready":
        raise RuntimeError(f"child cpk-server is not ready: {ready}")
    if ready.get("runtime_interpreters") != "none":
        raise RuntimeError(f"child cpk-server should remain opaque: {ready}")


def _assert_json(url: str, expected: dict[str, object]) -> None:
    value = _json(url)
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise RuntimeError(f"unexpected response from {url}: {value}")


def _json(url: str) -> dict[str, Any]:
    for _ in range(30):
        try:
            with urlopen(url, timeout=5) as response:
                data = response.read(1024 * 1024)
            decoded = json.loads(data.decode("utf-8"))
            if isinstance(decoded, dict):
                return decoded
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"could not read JSON from {url}")


def _recursive_graph(cpk_document: Any, postgres_document: Any) -> DeploymentGraph:
    cpk = cpk_document.product
    postgres = postgres_document.product
    child_cpk = instantiate_product(
        cpk,
        "child-cpk",
        ProductInstanceConfiguration.from_contract(cpk.runtime_contract),
    )
    child_postgres = instantiate_product(
        postgres,
        "child-postgres",
        ProductInstanceConfiguration.from_contract(postgres.runtime_contract),
    )
    return compile_topology(
        DeploymentTopology(
            WORKSPACE_ID,
            DockerRuntime(
                runtime_id="docker",
                network_name=f"control-plane-kit-{WORKSPACE_ID}-docker",
                children=(
                    child_postgres,
                    child_cpk,
                    SocketConnection(
                        "child-postgres",
                        "postgres",
                        "child-cpk",
                        "workplace-store",
                    ),
                    SocketConnection(
                        "child-postgres",
                        "postgres",
                        "child-cpk",
                        "activity-history-store",
                    ),
                    SocketConnection(
                        "child-postgres",
                        "postgres",
                        "child-cpk",
                        "observer-state-store",
                    ),
                    SocketConnection(
                        "child-postgres",
                        "postgres",
                        "child-cpk",
                        "graph-topology-store",
                    ),
                ),
            ),
        )
    )


def _product_document(servers_repo: Path, product_name: str) -> Any:
    return ProductDescriptorCodec().decode_document(
        (servers_repo / "products" / product_name / "product.cpk.json").read_bytes()
    )


if __name__ == "__main__":
    raise SystemExit(main())
