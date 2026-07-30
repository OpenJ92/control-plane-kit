"""Drive recursive cpk-server acceptance through an ephemeral Docker TLS authority."""

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
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_core.topology import compile_topology
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.verification import VerificationContract

from cpk_server_hosted_activity import (
    HostedWorkflow,
    _clock,
    _http,
    _mcp_read,
    _mcp_tool,
    _required_env,
    _wait_ready,
)


PARENT_WORKSPACE_ID = "recursive-cpk-server-tls-parent"
PARENT_WORKER_ID = "recursive-tls-parent-worker"
CHILD_WORKSPACE_ID = "recursive-cpk-server-tls-child"
CHILD_WORKER_ID = "recursive-tls-child-worker"
CHILD_AUTHORITY_REF = "ephemeral-docker-tls"
MAX_FAMILY_SIZE = 10


def main() -> int:
    parent_url = _required_env("CPK_RECURSIVE_TLS_BASE_URL").rstrip("/")
    parent_container = _required_env("CPK_RECURSIVE_TLS_PARENT_CONTAINER")
    dind_container = _required_env("CPK_RECURSIVE_TLS_DOCKER_AUTHORITY_CONTAINER")
    docker_endpoint = _required_env("CPK_RECURSIVE_TLS_DOCKER_ENDPOINT")
    servers_repo = Path(_required_env("CPK_RECURSIVE_TLS_SERVERS_REPO"))
    family_size = _recursive_family_size()

    _wait_ready(parent_url)

    parent = HostedWorkflow(
        parent_url,
        workspace_id=PARENT_WORKSPACE_ID,
        worker_id=PARENT_WORKER_ID,
        server_container=parent_container,
    )
    parent_current = parent.create_workspace(name="Recursive TLS parent cpk-server")
    _register_pull_authority_if_requested(parent)

    child_cpk = _product_document_with_secret_deliveries(
        servers_repo / "products" / "cpk_server" / "product.docker.cpk.json",
        identity_name="cpk-server-docker-tls-harness",
        extra_deliveries=(
            {
                "kind": "environment",
                "environment_name": "CPK_PRODUCT_SECRET_RESOLVER",
                "reference_id": "secret://control-plane-kit/child/product-secret-resolver",
                "intent": "application.control-token",
            },
            {
                "kind": "environment",
                "environment_name": "CPK_PRODUCT_SECRET_VALUES_JSON",
                "reference_id": "secret://control-plane-kit/child/product-secret-values-json",
                "intent": "application.control-token",
            },
            {
                "kind": "environment",
                "environment_name": "CPK_DOCKER_AUTH_CONFIG_JSON",
                "reference_id": "secret://control-plane-kit/child/docker-auth-config-json",
                "intent": "oci.pull-credential",
            },
            {
                "kind": "environment",
                "environment_name": "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER",
                "reference_id": (
                    "secret://control-plane-kit/child/"
                    "image-pull-credential-resolver"
                ),
                "intent": "oci.pull-credential",
            },
        ),
    )
    parent_postgres = _product_document(servers_repo, "postgres_server")
    parent.import_product("child-cpk", child_cpk)
    parent.import_product("child-postgres", parent_postgres)

    parent_result = parent.run_approved_transition(
        title="Recursive TLS child cpk-server",
        graph=_cpk_with_postgres_graph(
            workspace_id=PARENT_WORKSPACE_ID,
            runtime_id="docker",
            cpk_node_id="child-cpk",
            postgres_node_id="child-postgres",
            cpk_document=child_cpk,
            postgres_document=parent_postgres,
        ),
        current_graph_id=parent_current,
    )
    _sync_outer_networks(parent_container, dind_container, PARENT_WORKSPACE_ID)
    child_container = _find_runtime_container(PARENT_WORKSPACE_ID, "child-cpk")
    _assert_child_ready("http://child-cpk:8080/health/ready")

    child = HostedWorkflow(
        "http://child-cpk:8080",
        workspace_id=CHILD_WORKSPACE_ID,
        worker_id=CHILD_WORKER_ID,
        server_container=child_container,
    )
    child_current = child.create_workspace(name="Recursive TLS child operator")
    _register_remote_docker_tls_authority(child.base_url, docker_endpoint)
    _assert_runtime_authority_visible(child.base_url)

    grandchild_cpk = _product_document_without_verification(
        servers_repo / "products" / "cpk_server" / "product.cpk.json",
        identity_name="cpk-server-no-health-tls-harness",
    )
    grandchild_postgres = _product_document_without_verification(
        servers_repo / "products" / "postgres_server" / "product.cpk.json",
        identity_name="postgres-server-no-health-tls-harness",
    )
    child.import_product("grandchild-cpk", grandchild_cpk)
    child.import_product("grandchild-postgres", grandchild_postgres)
    _register_child_pull_authority_if_requested(child)

    child_result = child.run_approved_transition(
        title=f"Recursive TLS cpk-server family x{family_size}",
        graph=_cpk_family_with_postgres_graph(
            workspace_id=CHILD_WORKSPACE_ID,
            runtime_id="remote-docker",
            cpk_document=grandchild_cpk,
            postgres_document=grandchild_postgres,
            family_size=family_size,
            authority_ref=RuntimeAuthorityReference(CHILD_AUTHORITY_REF),
        ),
        current_graph_id=child_current,
    )
    for index in range(1, family_size + 1):
        _assert_activity_mentions(child.base_url, child_result.run_id, f"grandchild-cpk-{index}")
        _assert_activity_mentions(
            child.base_url,
            child_result.run_id,
            f"grandchild-postgres-{index}",
        )
    _assert_parent_mentions(parent.base_url, parent_result.run_id, "child-cpk")

    print(f"recursive TLS cpk-server activity smoke passed with family_size={family_size}")
    return 0


def _recursive_family_size() -> int:
    raw = os.environ.get("CPK_RECURSIVE_TLS_FAMILY_SIZE", "1")
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError("CPK_RECURSIVE_TLS_FAMILY_SIZE must be an integer") from error
    if not 1 <= value <= MAX_FAMILY_SIZE:
        raise RuntimeError(
            "CPK_RECURSIVE_TLS_FAMILY_SIZE must be between "
            f"1 and {MAX_FAMILY_SIZE}"
        )
    return value


def _register_pull_authority_if_requested(workflow: HostedWorkflow) -> None:
    if os.environ.get("CPK_RECURSIVE_TLS_REGISTER_PULL_AUTHORITY") == "docker-config":
        workflow.register_ghcr_pull_authority_from_docker_config()


def _register_child_pull_authority_if_requested(workflow: HostedWorkflow) -> None:
    if (
        os.environ.get("CPK_RECURSIVE_TLS_REGISTER_CHILD_PULL_AUTHORITY")
        == "docker-config"
    ):
        workflow.register_ghcr_pull_authority_from_docker_config()


def _register_remote_docker_tls_authority(base_url: str, endpoint: str) -> None:
    _mcp_tool(
        base_url,
        "command.runtime-authority.register",
        {
            "workspace_id": CHILD_WORKSPACE_ID,
            "authority_ref": CHILD_AUTHORITY_REF,
            "runtime_kind": "docker",
            "authority": {
                "kind": "remote-docker-tls",
                "endpoint": endpoint,
                "ca_certificate": "secret://control-plane-kit/docker-tls/ca",
                "client_certificate": "secret://control-plane-kit/docker-tls/cert",
                "client_key": "secret://control-plane-kit/docker-tls/key",
            },
            "actor_id": "operator-a",
            "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_REGISTER.value],
            "admitted_at": _clock(),
            "idempotency_key": f"{CHILD_WORKSPACE_ID}:runtime-authority",
        },
    )


def _assert_runtime_authority_visible(base_url: str) -> None:
    listed = _mcp_read(
        base_url,
        "read.runtime-authorities",
        {
            "workspace_id": CHILD_WORKSPACE_ID,
            "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_READ.value],
        },
    )
    items = listed.get("items", [])
    if CHILD_AUTHORITY_REF not in {item.get("authority_ref") for item in items}:
        raise RuntimeError(f"child runtime authority was not visible: {listed}")
    detail = _mcp_read(
        base_url,
        "read.runtime-authority-detail",
        {
            "workspace_id": CHILD_WORKSPACE_ID,
            "authority_ref": CHILD_AUTHORITY_REF,
            "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_READ.value],
        },
    )
    rendered = json.dumps(detail, sort_keys=True).lower()
    for forbidden in ("begin private key", "docker-tls/ca", "docker-tls/cert", "docker-tls/key"):
        if forbidden in rendered:
            raise RuntimeError(f"runtime authority readback leaked secret material: {detail}")


def _cpk_with_postgres_graph(
    *,
    workspace_id: str,
    runtime_id: str,
    cpk_node_id: str,
    postgres_node_id: str,
    cpk_document: Any,
    postgres_document: Any,
    authority_ref: RuntimeAuthorityReference | None = None,
) -> DeploymentGraph:
    cpk = instantiate_product(
        cpk_document.product,
        cpk_node_id,
        ProductInstanceConfiguration.from_contract(cpk_document.product.runtime_contract),
    )
    postgres = instantiate_product(
        postgres_document.product,
        postgres_node_id,
        ProductInstanceConfiguration.from_contract(postgres_document.product.runtime_contract),
    )
    return compile_topology(
        DeploymentTopology(
            workspace_id,
            DockerRuntime(
                runtime_id=runtime_id,
                network_name=f"control-plane-kit-{workspace_id}-{runtime_id}",
                authority_ref=authority_ref,
                children=(
                    postgres,
                    cpk,
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


def _cpk_family_with_postgres_graph(
    *,
    workspace_id: str,
    runtime_id: str,
    cpk_document: Any,
    postgres_document: Any,
    family_size: int,
    authority_ref: RuntimeAuthorityReference,
) -> DeploymentGraph:
    children: list[Any] = []
    for index in range(1, family_size + 1):
        cpk_node_id = f"grandchild-cpk-{index}"
        postgres_node_id = f"grandchild-postgres-{index}"
        children.extend(
            (
                instantiate_product(
                    postgres_document.product,
                    postgres_node_id,
                    ProductInstanceConfiguration.from_contract(
                        postgres_document.product.runtime_contract
                    ),
                ),
                instantiate_product(
                    cpk_document.product,
                    cpk_node_id,
                    ProductInstanceConfiguration.from_contract(
                        cpk_document.product.runtime_contract
                    ),
                ),
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
            )
        )
    return compile_topology(
        DeploymentTopology(
            workspace_id,
            DockerRuntime(
                runtime_id=runtime_id,
                network_name=f"control-plane-kit-{workspace_id}-{runtime_id}",
                authority_ref=authority_ref,
                children=tuple(children),
            ),
        )
    )


def _product_document(servers_repo: Path, product_name: str) -> Any:
    return ProductDescriptorCodec().decode_document(
        (servers_repo / "products" / product_name / "product.cpk.json").read_bytes()
    )


def _product_document_without_verification(
    descriptor_path: Path,
    *,
    identity_name: str,
) -> Any:
    codec = ProductDescriptorCodec()
    document = codec.decode_document(descriptor_path.read_bytes())
    product = document.product
    contract = replace(product.runtime_contract, verification=VerificationContract())
    return codec.encode_document(
        replace(
            product,
            identity=ProductIdentity(
                product.identity.namespace,
                identity_name,
                product.identity.contract_revision,
            ),
            runtime_contract=contract,
            description=(
                product.description
                + " Harness-only descriptor variant used for nested Docker TLS "
                "execution where outer-network health probing is intentionally "
                "unavailable."
            ),
        )
    )


def _product_document_with_secret_deliveries(
    descriptor_path: Path,
    *,
    identity_name: str,
    extra_deliveries: tuple[dict[str, str], ...],
) -> Any:
    codec = ProductDescriptorCodec()
    document = codec.decode_document(descriptor_path.read_bytes())
    product = document.product
    added = tuple(
        SecretEnvironmentDelivery(
            environment_name=delivery["environment_name"],
            reference=SecretReference(delivery["reference_id"]),
            intent=SecretUseIntent(delivery["intent"]),
        )
        for delivery in extra_deliveries
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
                identity_name,
                product.identity.contract_revision,
            ),
            runtime_contract=contract,
            description=(
                product.description
                + " Harness-only descriptor variant that receives a child-local "
                "secret resolver through explicit secret deliveries."
            ),
        )
    )


def _sync_outer_networks(
    parent_container: str,
    dind_container: str,
    workspace_id: str,
) -> None:
    client = docker.from_env()
    controller_container = socket.gethostname()
    for network in client.networks.list():
        name = network.name
        if not name.startswith(f"cpk-net-{workspace_id}"):
            continue
        for container, aliases in (
            (parent_container, None),
            (controller_container, None),
            (dind_container, ["docker"]),
        ):
            try:
                if aliases:
                    network.connect(container, aliases=aliases)
                else:
                    network.connect(container)
            except APIError as error:
                if "already exists" in str(error).lower():
                    continue
                raise
            except NotFound:
                continue


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


def _assert_child_ready(url: str) -> None:
    for _ in range(30):
        try:
            with urlopen(url, timeout=5) as response:
                ready = json.loads(response.read(1024 * 1024).decode("utf-8"))
            if ready.get("status") == "ready":
                if ready.get("runtime_interpreters") != "docker":
                    raise RuntimeError(f"child cpk-server is not Docker-capable: {ready}")
                return
        except Exception:
            time.sleep(1)
    raise RuntimeError("child cpk-server did not become ready")


def _assert_activity_mentions(base_url: str, run_id: str, node_id: str) -> None:
    timeline = _mcp_read(
        base_url,
        "read.activity",
        {"workspace_id": CHILD_WORKSPACE_ID, "limit": 200},
    )
    events = _events_for_run(timeline, run_id)
    for event in events:
        payload = event.get("payload", {})
        if payload.get("node_id") == node_id and event.get("event_type") == "step_succeeded":
            return
    raise RuntimeError(f"activity timeline did not record successful step for {node_id}")


def _assert_parent_mentions(base_url: str, run_id: str, node_id: str) -> None:
    timeline = _mcp_read(
        base_url,
        "read.activity",
        {"workspace_id": PARENT_WORKSPACE_ID, "limit": 200},
    )
    events = _events_for_run(timeline, run_id)
    for event in events:
        payload = event.get("payload", {})
        if payload.get("node_id") == node_id and event.get("event_type") == "step_succeeded":
            return
    raise RuntimeError(f"parent activity timeline did not record successful step for {node_id}")


def _events_for_run(timeline: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    for session in timeline.get("sessions", []):
        for plan in session.get("plans", []):
            for run in plan.get("runs", []):
                if run.get("run_id") == run_id:
                    events = run.get("events")
                    if isinstance(events, list):
                        return events
    raise RuntimeError(f"activity timeline did not expose run {run_id}")


if __name__ == "__main__":
    raise SystemExit(main())
