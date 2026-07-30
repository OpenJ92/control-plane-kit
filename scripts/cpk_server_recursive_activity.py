"""Drive recursive cpk-server acceptance over the parent HTTP/MCP surface."""

from __future__ import annotations

import json
import os
from dataclasses import replace
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
    ProductIdentity,
    instantiate_product,
)
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, compile_topology
from control_plane_kit_core.policies import PolicyScope

from cpk_server_hosted_activity import (
    HostedWorkflow,
    _clock,
    _http,
    _mcp_read,
    _mcp_tool,
    _required_env,
    _wait_ready,
)


WORKSPACE_ID = "recursive-cpk-server"
WORKER_ID = "recursive-worker"
LOCAL_CHAIN_AUTHORITY_REF = "local-docker"
MAX_LOCAL_CHAIN_DEPTH = 10


def main() -> int:
    base_url = _required_env("CPK_RECURSIVE_BASE_URL").rstrip("/")
    parent_container = _required_env("CPK_RECURSIVE_PARENT_CONTAINER")
    servers_repo = Path(_required_env("CPK_RECURSIVE_SERVERS_REPO"))
    chain_depth = _local_chain_depth()

    _wait_ready(base_url)
    cpk_document = _chain_cpk_document(servers_repo)
    postgres_document = _product_document(servers_repo, "postgres_server")
    graph = _recursive_graph(
        cpk_document,
        postgres_document,
        authority_ref=RuntimeAuthorityReference(LOCAL_CHAIN_AUTHORITY_REF),
    )

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

    _register_local_docker_authority(base_url, WORKSPACE_ID)
    _register_local_docker_delivery(base_url, WORKSPACE_ID)
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
    _assert_child_health(expect_runtime_interpreters="docker" if chain_depth > 1 else "none")

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
    if chain_depth > 1:
        child_container = _find_runtime_container(WORKSPACE_ID, "child-cpk")
        _sync_runtime_networks(parent_container, child_container)
        _run_local_chain(
            base_url="http://child-cpk:8080",
            server_container=child_container,
            servers_repo=servers_repo,
            remaining_depth=chain_depth - 1,
            level=2,
        )

    print(f"recursive cpk-server Docker activity smoke passed with chain_depth={chain_depth}")
    return 0


def _local_chain_depth() -> int:
    raw = os.environ.get("CPK_RECURSIVE_LOCAL_CHAIN_DEPTH", "1")
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError("CPK_RECURSIVE_LOCAL_CHAIN_DEPTH must be an integer") from error
    if not 1 <= value <= MAX_LOCAL_CHAIN_DEPTH:
        raise RuntimeError(
            "CPK_RECURSIVE_LOCAL_CHAIN_DEPTH must be between "
            f"1 and {MAX_LOCAL_CHAIN_DEPTH}"
        )
    return value


def _run_local_chain(
    *,
    base_url: str,
    server_container: str,
    servers_repo: Path,
    remaining_depth: int,
    level: int,
) -> None:
    workflow = HostedWorkflow(
        base_url,
        workspace_id=f"recursive-cpk-server-local-chain-{level}",
        worker_id=f"recursive-local-chain-worker-{level}",
        server_container=server_container,
    )
    workflow.wait_ready()
    current_graph_id = workflow.create_workspace(
        name=f"Recursive local chain level {level}"
    )
    _register_local_docker_authority(workflow.base_url, workflow.workspace_id)
    _register_local_docker_delivery(workflow.base_url, workflow.workspace_id)
    if os.environ.get("CPK_RECURSIVE_REGISTER_PULL_AUTHORITY") == "docker-config":
        workflow.register_ghcr_pull_authority_from_docker_config()

    cpk_document = _chain_cpk_document(servers_repo)
    postgres_document = _product_document(servers_repo, "postgres_server")
    workflow.import_product(f"chain-cpk-{level}", cpk_document)
    workflow.import_product(f"chain-postgres-{level}", postgres_document)
    cpk_node_id = f"chain-cpk-{level}"
    postgres_node_id = f"chain-postgres-{level}"
    result = workflow.run_approved_transition(
        title=f"Recursive local chain level {level}",
        graph=_recursive_graph(
            cpk_document,
            postgres_document,
            workspace_id=workflow.workspace_id,
            cpk_node_id=cpk_node_id,
            postgres_node_id=postgres_node_id,
            authority_ref=RuntimeAuthorityReference(LOCAL_CHAIN_AUTHORITY_REF),
        ),
        current_graph_id=current_graph_id,
    )
    _assert_activity_step(workflow.base_url, workflow.workspace_id, result.run_id, cpk_node_id)
    if remaining_depth <= 1:
        return
    child_container = _find_runtime_container(workflow.workspace_id, cpk_node_id)
    _sync_runtime_networks(server_container, child_container)
    _run_local_chain(
        base_url=f"http://{cpk_node_id}:8080",
        server_container=child_container,
        servers_repo=servers_repo,
        remaining_depth=remaining_depth - 1,
        level=level + 1,
    )


def _register_local_docker_authority(base_url: str, workspace_id: str) -> None:
    _mcp_tool(
        base_url,
        "command.runtime-authority.register",
        {
            "workspace_id": workspace_id,
            "authority_ref": LOCAL_CHAIN_AUTHORITY_REF,
            "runtime_kind": "docker",
            "authority": {"kind": "local-docker-socket"},
            "actor_id": "operator-a",
            "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_REGISTER.value],
            "admitted_at": _clock(),
            "idempotency_key": f"{workspace_id}:runtime-authority:local-docker",
        },
    )


def _register_local_docker_delivery(base_url: str, workspace_id: str) -> None:
    _mcp_tool(
        base_url,
        "command.runtime-authority-delivery.register",
        {
            "workspace_id": workspace_id,
            "delivery": {
                "authority_ref": {"reference_id": LOCAL_CHAIN_AUTHORITY_REF},
                "delivery_kind": "local-docker-socket-mount",
                "secret_references": [],
            },
            "actor_id": "operator-a",
            "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER.value],
            "admitted_at": _clock(),
            "idempotency_key": f"{workspace_id}:runtime-authority-delivery:local-docker",
        },
    )


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


def _sync_runtime_networks(*containers: str) -> None:
    client = docker.from_env()
    controller_container = socket.gethostname()
    for network in client.networks.list():
        name = network.name
        if not name.startswith("cpk-net-recursive-cpk-server"):
            continue
        for container in (controller_container, *containers):
            try:
                network.connect(container)
            except APIError as error:
                if "already exists" in str(error).lower():
                    continue
                raise
            except NotFound:
                continue


def _assert_child_health(*, expect_runtime_interpreters: str) -> None:
    _assert_json("http://child-cpk:8080/health/live", {"status": "live"})
    ready = _json("http://child-cpk:8080/health/ready")
    if ready.get("status") != "ready":
        raise RuntimeError(f"child cpk-server is not ready: {ready}")
    if ready.get("runtime_interpreters") != expect_runtime_interpreters:
        raise RuntimeError(
            "child cpk-server reported unexpected runtime interpreters: "
            f"{ready}"
        )


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


def _recursive_graph(
    cpk_document: Any,
    postgres_document: Any,
    *,
    workspace_id: str = WORKSPACE_ID,
    cpk_node_id: str = "child-cpk",
    postgres_node_id: str = "child-postgres",
    authority_ref: RuntimeAuthorityReference | None = None,
) -> DeploymentGraph:
    cpk = cpk_document.product
    postgres = postgres_document.product
    child_cpk = instantiate_product(
        cpk,
        cpk_node_id,
        ProductInstanceConfiguration.from_contract(cpk.runtime_contract),
    )
    child_postgres = instantiate_product(
        postgres,
        postgres_node_id,
        ProductInstanceConfiguration.from_contract(postgres.runtime_contract),
    )
    return compile_topology(
        DeploymentTopology(
            workspace_id,
            DockerRuntime(
                runtime_id="docker",
                network_name=f"control-plane-kit-{workspace_id}-docker",
                authority_ref=authority_ref,
                children=(
                    child_postgres,
                    child_cpk,
                    SocketConnection(
                        postgres_node_id,
                        "postgres",
                        cpk_node_id,
                        "workplace-store",
                    ),
                    SocketConnection(
                        postgres_node_id,
                        "postgres",
                        cpk_node_id,
                        "activity-history-store",
                    ),
                    SocketConnection(
                        postgres_node_id,
                        "postgres",
                        cpk_node_id,
                        "observer-state-store",
                    ),
                    SocketConnection(
                        postgres_node_id,
                        "postgres",
                        cpk_node_id,
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


def _chain_cpk_document(servers_repo: Path) -> Any:
    codec = ProductDescriptorCodec()
    document = codec.decode_document(
        (servers_repo / "products" / "cpk_server" / "product.docker.cpk.json").read_bytes()
    )
    product = document.product
    added = (
        SecretEnvironmentDelivery(
            environment_name="CPK_DOCKER_AUTH_CONFIG_JSON",
            reference=SecretReference(
                "secret://control-plane-kit/child/docker-auth-config-json"
            ),
            intent=SecretUseIntent.OCI_PULL_CREDENTIAL,
        ),
        SecretEnvironmentDelivery(
            environment_name="CPK_IMAGE_PULL_CREDENTIAL_RESOLVER",
            reference=SecretReference(
                "secret://control-plane-kit/child/image-pull-credential-resolver"
            ),
            intent=SecretUseIntent.OCI_PULL_CREDENTIAL,
        ),
        SecretEnvironmentDelivery(
            environment_name="CPK_PRODUCT_MATERIAL_RESOLVER",
            reference=SecretReference(
                "secret://control-plane-kit/child/product-secret-resolver"
            ),
            intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
        ),
        SecretEnvironmentDelivery(
            environment_name="CPK_PRODUCT_SECRET_VALUES_JSON",
            reference=SecretReference(
                "secret://control-plane-kit/child/product-secret-values-json"
            ),
            intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
        ),
    )
    contract = replace(
        product.runtime_contract,
        secret_deliveries=product.runtime_contract.secret_deliveries + added,
    )
    return codec.encode_document(
        replace(
            product,
            identity=ProductIdentity(
                product.identity.namespace,
                "cpk-server-docker-local-chain-harness",
                product.identity.contract_revision,
            ),
            runtime_contract=contract,
            description=(
                product.description
                + " Harness-only descriptor variant for bounded local recursive "
                "chain acceptance."
            ),
        )
    )


def _find_runtime_container(workspace_id: str, node_id: str) -> str:
    client = docker.from_env()
    filters = {
        "label": [
            f"org.openj92.cpk.workspace={workspace_id}",
            f"org.openj92.cpk.node={node_id}",
        ]
    }
    matches = client.containers.list(all=True, filters=filters)
    if len(matches) != 1:
        names = [container.name for container in matches]
        raise RuntimeError(f"expected one container for {workspace_id}/{node_id}: {names}")
    return str(matches[0].name)


def _assert_activity_step(
    base_url: str,
    workspace_id: str,
    run_id: str,
    node_id: str,
) -> None:
    timeline = _mcp_read(
        base_url,
        "read.activity",
        {"workspace_id": workspace_id, "limit": 200},
    )
    events = _events_for_run(timeline, run_id)
    for event in events:
        payload = event.get("payload", {})
        if payload.get("node_id") == node_id and event.get("event_type") == "step_succeeded":
            return
    raise RuntimeError(f"activity timeline did not record successful step for {node_id}")


if __name__ == "__main__":
    raise SystemExit(main())
