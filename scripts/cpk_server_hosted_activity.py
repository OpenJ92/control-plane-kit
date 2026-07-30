"""Drive hosted cpk-server ACTIVITY acceptance over HTTP and MCP."""

from __future__ import annotations

import json
import http.client
import os
import ssl
from dataclasses import dataclass, replace
from pathlib import Path
import socket
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import jwt

from control_plane_kit_core.gateway_delegation import (
    DelegatedGatewayProbeGrant,
    GatewayProbeCommandKind,
    GatewayProbeRequest,
)
from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime, SocketConnection
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    NamedPublicIngress,
    PublicIngressTarget,
)
from control_plane_kit_core.products import (
    ProductDescriptorCodec,
    ProductInstanceConfiguration,
    instantiate_product,
)
from control_plane_kit_core.runtime_effects import GatewayTargetId
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, compile_topology
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.verification import VerificationContract, VerificationPolicy


DEFAULT_WORKSPACE_ID = "cpk-hosted-activity-basic"
WORKSPACE_ID = DEFAULT_WORKSPACE_ID
WORKSPACE_IDS = (
    "workspace-a-router",
    "workspace-b-multiplexer",
    "workspace-c-postgres",
    "workspace-d-negative-cleanup",
)
WORKER_ID = "hosted-worker"
AUTHORIZATION = "Bearer present"
WORKER_AUTHORIZATION = "Bearer worker-present"
LOCAL_DOCKER_AUTHORITY_REF = "local-docker"
OPENJ92_INGRESS_AUTHORITY_REF = "openj92-cloudflare"
PUBLIC_GATEWAY_HOSTNAME = "cpk-gateway-001.openj92.dev"
PUBLIC_GATEWAY_READY_ATTEMPTS = 60
PUBLIC_GATEWAY_READY_RETRY_SECONDS = 2
GATEWAY_PROBE_ISSUER = "urn:control-plane-kit:source-live"
GATEWAY_PROBE_KEY_ID = "source-live-gateway-key"


@dataclass(frozen=True)
class HostedTransitionResult:
    current_graph_id: str
    desired_graph_id: str
    plan_id: str
    approval_id: str
    run_id: str


class HostedWorkflow:
    """Public HTTP/MCP workflow driver for hosted cpk-server acceptance."""

    def __init__(
        self,
        base_url: str,
        *,
        workspace_id: str,
        worker_id: str,
        server_container: str,
        worker_authorization: str = WORKER_AUTHORIZATION,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.workspace_id = workspace_id
        self.worker_id = worker_id
        self.server_container = server_container
        self.worker_authorization = worker_authorization

    def wait_ready(self, *, policy: VerificationPolicy | None = None) -> None:
        _wait_ready(self.base_url, policy=policy)

    def create_workspace(self, *, name: str, actor_id: str = "operator-a") -> str:
        workspace = _http(
            self.base_url,
            "POST",
            "/workspaces",
            {
                "workspace_id": self.workspace_id,
                "name": name,
                "actor_id": actor_id,
                "idempotency_key": f"{self.workspace_id}:workspace",
            },
        )
        return str(workspace["workspace"]["current_graph_id"])

    def import_product(self, label: str, product_document: Any) -> None:
        _http(
            self.base_url,
            "POST",
            f"/workspaces/{self.workspace_id}/products/import",
            {
                "descriptor_document": json.loads(product_document.content.decode("utf-8")),
                "actor_id": "operator-a",
                "imported_at": _clock(),
                "idempotency_key": f"{self.workspace_id}:import:{label}",
            },
        )

    def register_ghcr_pull_authority_from_docker_config(self) -> None:
        _http(
            self.base_url,
            "POST",
            f"/workspaces/{self.workspace_id}/image-pull-authorities",
            {
                "registry": "ghcr.io",
                "repository": "openj92/control-plane-kit-servers",
                "credential_reference": "secret://docker-config/ghcr.io",
                "actor_id": "operator-a",
                "admitted_at": _clock(),
                "idempotency_key": f"{self.workspace_id}:pull-authority:ghcr",
            },
        )

    def register_local_docker_authority(self) -> None:
        _mcp_tool(
            self.base_url,
            "command.runtime-authority.register",
            {
                "workspace_id": self.workspace_id,
                "authority_ref": LOCAL_DOCKER_AUTHORITY_REF,
                "runtime_kind": "docker",
                "authority": {"kind": "local-docker-socket"},
                "actor_id": "operator-a",
                "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_REGISTER.value],
                "admitted_at": _clock(),
                "idempotency_key": f"{self.workspace_id}:runtime-authority:local-docker",
            },
        )

    def register_local_docker_delivery(self) -> None:
        _mcp_tool(
            self.base_url,
            "command.runtime-authority-delivery.register",
            {
                "workspace_id": self.workspace_id,
                "delivery": {
                    "authority_ref": {"reference_id": LOCAL_DOCKER_AUTHORITY_REF},
                    "delivery_kind": "local-docker-socket-mount",
                    "secret_references": [],
                },
                "actor_id": "operator-a",
                "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER.value],
                "admitted_at": _clock(),
                "idempotency_key": (
                    f"{self.workspace_id}:runtime-authority-delivery:local-docker"
                ),
            },
        )

    def register_cloudflare_ingress_authority(self) -> None:
        _mcp_tool(
            self.base_url,
            "command.ingress-authority.register",
            {
                "workspace_id": self.workspace_id,
                "authority_ref": OPENJ92_INGRESS_AUTHORITY_REF,
                "authority": {
                    "provider_kind": "cloudflare",
                    "account_id": _required_env("OPENJ92_CLOUDFLARE_ACCOUNT_ID"),
                    "zone_id": _required_env("OPENJ92_CLOUDFLARE_ZONE_ID"),
                    "zone_name": os.environ.get("OPENJ92_CLOUDFLARE_ZONE", "openj92.dev"),
                    "api_token_ref": "secret://cloudflare/openj92/api-token",
                    "allowed_hostname_pattern": "cpk-gateway-*.openj92.dev",
                },
                "actor_id": "operator-a",
                "actor_scopes": [PolicyScope.INGRESS_AUTHORITY_REGISTER.value],
                "admitted_at": _clock(),
                "idempotency_key": (
                    f"{self.workspace_id}:ingress-authority:openj92-cloudflare"
                ),
            },
        )
        detail = _mcp_read(
            self.base_url,
            "read.ingress-authority-detail",
            {
                "workspace_id": self.workspace_id,
                "authority_ref": OPENJ92_INGRESS_AUTHORITY_REF,
                "actor_scopes": [PolicyScope.INGRESS_AUTHORITY_READ.value],
            },
        )
        authority = detail.get("ingress_authority", {})
        if authority.get("authority_ref") != OPENJ92_INGRESS_AUTHORITY_REF:
            raise RuntimeError("registered ingress authority was not readable")

    def start_session(self, title: str) -> str:
        session = _http(
            self.base_url,
            "POST",
            f"/workspaces/{self.workspace_id}/sessions",
            {
                "actor_id": "operator-a",
                "title": title,
                "idempotency_key": f"{self.workspace_id}:{title}:session",
            },
        )
        return str(session["session_id"])

    def set_desired_graph(
        self,
        *,
        session_id: str,
        graph: DeploymentGraph,
        title: str,
        expected_desired_graph_id: str | None,
    ) -> str:
        desired = _http(
            self.base_url,
            "POST",
            f"/workspaces/{self.workspace_id}/graphs/desired",
            {
                "session_id": session_id,
                "actor_id": "operator-a",
                "graph": DEFAULT_GRAPH_CODEC.encode(graph),
                "expected_desired_graph_id": expected_desired_graph_id,
                "idempotency_key": f"{self.workspace_id}:{title}:desired",
            },
        )
        return str(desired["desired_graph_id"])

    def plan_transition(
        self,
        *,
        session_id: str,
        title: str,
        current_graph_id: str,
        desired_graph_id: str,
    ) -> str:
        planned = _mcp_tool(
            self.base_url,
            "command.deployment.plan",
            {
                "workspace_id": self.workspace_id,
                "session_id": session_id,
                "actor_id": "operator-a",
                "expected_current_graph_id": current_graph_id,
                "expected_desired_graph_id": desired_graph_id,
                "idempotency_key": f"{self.workspace_id}:{title}:plan",
            },
        )
        if not planned.get("ready_for_execution", False):
            raise RuntimeError(f"plan was not approval-ready: {planned}")
        return str(planned["plan_id"])

    def request_approval(self, *, session_id: str, title: str, plan_id: str) -> dict[str, Any]:
        return _http(
            self.base_url,
            "POST",
            f"/workspaces/{self.workspace_id}/plans/{plan_id}/approval",
            {
                "session_id": session_id,
                "actor_id": "operator-a",
                "actor_scopes": [PolicyScope.PLAN_REQUEST.value],
                "idempotency_key": f"{self.workspace_id}:{title}:approval-request",
            },
        )

    def assert_approval_visible(self, approval_id: str, plan_id: str) -> None:
        pending = _mcp_read(
            self.base_url,
            "read.pending-approvals",
            {"workspace_id": self.workspace_id, "limit": 10, "offset": 0},
        )
        if approval_id not in {item["request_id"] for item in pending["items"]}:
            raise RuntimeError("approval request was not visible in pending queue")
        detail = _mcp_read(
            self.base_url,
            "read.approval-detail",
            {"workspace_id": self.workspace_id, "approval_id": approval_id},
        )
        if detail["plan"]["plan_id"] != plan_id:
            raise RuntimeError("approval detail did not expose the planned transition")

    def approve(self, *, session_id: str, title: str, approval: dict[str, Any]) -> None:
        _mcp_tool(
            self.base_url,
            "command.approval.decide",
            {
                "workspace_id": self.workspace_id,
                "session_id": session_id,
                "request_id": str(approval["request_id"]),
                "actor_id": "manager-a",
                "actor_scopes": [approval["required_scope"]],
                "decision": "approved",
                "idempotency_key": f"{self.workspace_id}:{title}:approval-decision",
            },
        )

    def admit(
        self,
        *,
        session_id: str,
        title: str,
        plan_id: str,
        approval_id: str,
    ) -> str:
        admitted = _http(
            self.base_url,
            "POST",
            f"/workspaces/{self.workspace_id}/plans/{plan_id}/admission",
            {
                "session_id": session_id,
                "approval_request_id": approval_id,
                "actor_id": "operator-a",
                "actor_scopes": [PolicyScope.PLAN_EXECUTE.value],
                "idempotency_key": f"{self.workspace_id}:{title}:admit",
                "readiness": [],
            },
        )
        return str(admitted["execution_request_id"])

    def claim(self, *, title: str, request_id: str) -> str:
        claimed = _http(
            self.base_url,
            "POST",
            f"/workspaces/{self.workspace_id}/runs/{request_id}/claim",
            {
                "worker_id": self.worker_id,
                "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
                "lease_expires_at": "2026-07-22T12:00:00Z",
                "idempotency_key": f"{self.workspace_id}:{title}:claim",
            },
            extra_headers={"Authorization": self.worker_authorization},
        )
        return str(claimed["run_id"])

    def start_run(self, *, title: str, run_id: str) -> None:
        _http(
            self.base_url,
            "POST",
            f"/workspaces/{self.workspace_id}/runs/{run_id}/start",
            {
                "worker_id": self.worker_id,
                "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
                "idempotency_key": f"{self.workspace_id}:{title}:start",
            },
            extra_headers={"Authorization": self.worker_authorization},
        )

    def execute_to_completion(
        self,
        run_id: str,
        *,
        sync_runtime_networks: bool = True,
    ) -> None:
        _execute_to_completion(
            self.base_url,
            self.server_container,
            run_id,
            workspace_id=self.workspace_id,
            worker_id=self.worker_id,
            sync_runtime_networks=sync_runtime_networks,
            worker_authorization=self.worker_authorization,
        )

    def advance_current_graph(
        self,
        *,
        title: str,
        run_id: str,
        plan_id: str,
        current_graph_id: str,
        desired_graph_id: str,
    ) -> str:
        advanced = _http(
            self.base_url,
            "POST",
            f"/workspaces/{self.workspace_id}/runs/{run_id}/advance-current-graph",
            {
                "plan_id": plan_id,
                "expected_current_graph_id": current_graph_id,
                "desired_graph_id": desired_graph_id,
                "worker_id": self.worker_id,
                "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
                "idempotency_key": f"{self.workspace_id}:{title}:advance",
            },
            extra_headers={"Authorization": self.worker_authorization},
        )
        return str(advanced["to_graph_id"])

    def read_current_graph_id(self) -> str:
        current = _http(self.base_url, "GET", f"/workspaces/{self.workspace_id}/graphs/current")
        return str(current["graph_id"])

    def read_activity(self, *, limit: int = 200) -> dict[str, Any]:
        return _mcp_read(
            self.base_url,
            "read.activity",
            {"workspace_id": self.workspace_id, "limit": limit},
        )

    def request_gateway_probe_http(
        self,
        *,
        request_id: str,
        expected_current_graph_id: str,
        gateway_node_id: str,
        kind: str,
        target_id: str,
        path: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, object] = {
            "request_id": request_id,
            "expected_current_graph_id": expected_current_graph_id,
            "kind": kind,
            "target_id": target_id,
            "actor_id": "operator-a",
            "actor_scopes": [PolicyScope.GATEWAY_PROBE_USE.value],
        }
        if path is not None:
            payload["path"] = path
        return _http(
            self.base_url,
            "POST",
            f"/workspaces/{self.workspace_id}/gateways/{gateway_node_id}/probes",
            payload,
        )

    def request_gateway_probe_mcp(
        self,
        *,
        request_id: str,
        expected_current_graph_id: str,
        gateway_node_id: str,
        kind: str,
        target_id: str,
        path: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, object] = {
            "workspace_id": self.workspace_id,
            "request_id": request_id,
            "expected_current_graph_id": expected_current_graph_id,
            "gateway_node_id": gateway_node_id,
            "kind": kind,
            "target_id": target_id,
            "actor_id": "operator-a",
            "actor_scopes": [PolicyScope.GATEWAY_PROBE_USE.value],
        }
        if path is not None:
            payload["path"] = path
        return _mcp_tool(
            self.base_url,
            "command.gateway-probe.request",
            payload,
        )

    def read_gateway_probe_detail(self, probe_id: str) -> dict[str, Any]:
        return _mcp_read(
            self.base_url,
            "read.gateway-probe-detail",
            {
                "workspace_id": self.workspace_id,
                "probe_id": probe_id,
            },
        )

    def run_approved_transition(
        self,
        *,
        title: str,
        graph: DeploymentGraph,
        current_graph_id: str,
        expected_desired_graph_id: str | None = None,
        sync_runtime_networks: bool = True,
    ) -> HostedTransitionResult:
        session_id = self.start_session(title)
        desired_graph_id = self.set_desired_graph(
            session_id=session_id,
            graph=graph,
            title=title,
            expected_desired_graph_id=expected_desired_graph_id,
        )
        plan_id = self.plan_transition(
            session_id=session_id,
            title=title,
            current_graph_id=current_graph_id,
            desired_graph_id=desired_graph_id,
        )
        approval = self.request_approval(
            session_id=session_id,
            title=title,
            plan_id=plan_id,
        )
        approval_id = str(approval["request_id"])
        self.assert_approval_visible(approval_id, plan_id)
        self.approve(session_id=session_id, title=title, approval=approval)
        request_id = self.admit(
            session_id=session_id,
            title=title,
            plan_id=plan_id,
            approval_id=approval_id,
        )
        run_id = self.claim(title=title, request_id=request_id)
        self.start_run(title=title, run_id=run_id)
        self.execute_to_completion(run_id, sync_runtime_networks=sync_runtime_networks)
        advanced_graph_id = self.advance_current_graph(
            title=title,
            run_id=run_id,
            plan_id=plan_id,
            current_graph_id=current_graph_id,
            desired_graph_id=desired_graph_id,
        )
        if advanced_graph_id != desired_graph_id:
            raise RuntimeError(
                f"current graph did not advance: {advanced_graph_id} != {desired_graph_id}"
            )
        readback_graph_id = self.read_current_graph_id()
        if readback_graph_id != desired_graph_id:
            raise RuntimeError(f"current graph readback mismatch: {readback_graph_id}")
        return HostedTransitionResult(
            current_graph_id=advanced_graph_id,
            desired_graph_id=desired_graph_id,
            plan_id=plan_id,
            approval_id=approval_id,
            run_id=run_id,
        )


def main() -> int:
    base_url = _required_env("CPK_HOSTED_ACTIVITY_BASE_URL").rstrip("/")
    server_container = _required_env("CPK_HOSTED_ACTIVITY_SERVER_CONTAINER")
    servers_repo = Path(_required_env("CPK_HOSTED_ACTIVITY_SERVERS_REPO"))
    scenario = os.environ.get("CPK_HOSTED_ACTIVITY_SCENARIO", "single-hello")
    workflow = _workflow_for(
        base_url,
        server_container=server_container,
        workspace_id=os.environ.get(
            "CPK_HOSTED_ACTIVITY_WORKSPACE_ID",
            DEFAULT_WORKSPACE_ID,
        ),
    )

    workflow.wait_ready()

    if scenario == "single-hello":
        _run_single_hello(workflow, servers_repo)
    elif scenario == "router-transition":
        _run_router_transition(workflow, servers_repo)
    elif scenario == "workspace-a-router-transition":
        _run_router_transition(
            _workflow_for(
                base_url,
                server_container=server_container,
                workspace_id="workspace-a-router",
            ),
            servers_repo,
        )
    elif scenario == "workspace-b-multiplexer-observer":
        _run_multiplexer_observer(
            _workflow_for(
                base_url,
                server_container=server_container,
                workspace_id="workspace-b-multiplexer",
            ),
            servers_repo,
        )
    elif scenario == "workspace-c-postgres-retained-data":
        _run_postgres_retained_data(
            _workflow_for(
                base_url,
                server_container=server_container,
                workspace_id="workspace-c-postgres",
            ),
            servers_repo,
        )
    elif scenario == "seeded-stress-public-ingress":
        _run_seeded_stress_public_ingress(base_url, server_container, servers_repo)
    elif scenario == "multi-workspace-foundation":
        _run_multi_workspace_foundation(base_url, server_container, servers_repo)
    elif scenario == "public-gateway-ingress":
        _run_public_gateway_ingress(workflow, servers_repo)
    elif scenario == "public-gateway-toggle":
        _run_public_gateway_toggle(workflow, servers_repo)
    elif scenario == "authenticated-gateway-private":
        _run_authenticated_gateway_private(workflow, servers_repo)
    else:
        raise RuntimeError(f"unknown hosted activity scenario: {scenario}")

    print(f"hosted cpk-server Docker activity smoke passed: {scenario}")
    return 0


def _run_single_hello(workflow: HostedWorkflow, servers_repo: Path) -> None:
    product_document = _product_document(servers_repo, "hello_server")
    graph = _single_hello_graph(
        product_document,
        workspace_id=workflow.workspace_id,
        authority_ref=RuntimeAuthorityReference(LOCAL_DOCKER_AUTHORITY_REF),
    )

    current_graph_id = _bootstrap_workspace(
        workflow,
        name="Hosted activity smoke",
        product_documents={"hello": product_document},
        register_runtime_authority=True,
        register_runtime_delivery=True,
    )

    workflow.run_approved_transition(
        title="Hosted hello deployment",
        graph=graph,
        current_graph_id=current_graph_id,
    )
    _assert_body("http://hello:8000/", "Hello, world!\n")


def _run_router_transition(workflow: HostedWorkflow, servers_repo: Path) -> None:
    hello_document = _product_document(servers_repo, "hello_server")
    router_document = _product_document(servers_repo, "http_active_router")
    gateway_document = _product_document(servers_repo, "cpk_local_gateway")
    cloudflared_document = _product_document(servers_repo, "cloudflared_connector")

    current_graph_id = _bootstrap_workspace(
        workflow,
        name="Hosted router transition",
        product_documents={
            "hello": hello_document,
            "router": router_document,
            "gateway": gateway_document,
            "cloudflared": cloudflared_document,
        },
        register_runtime_authority=True,
        register_runtime_delivery=True,
    )
    workflow.register_cloudflare_ingress_authority()

    blue_graph = _router_graph(
        hello_document,
        router_document,
        gateway_document,
        cloudflared_document,
        workspace_id=workflow.workspace_id,
        active_hello_role="hello-blue",
        message="Hello from blue",
        authority_ref=RuntimeAuthorityReference(LOCAL_DOCKER_AUTHORITY_REF),
        public_hostname=PUBLIC_GATEWAY_HOSTNAME,
    )
    blue = workflow.run_approved_transition(
        title="Hosted router blue",
        graph=blue_graph,
        current_graph_id=current_graph_id,
    )
    _assert_activity_mentions(workflow, blue.run_id, "hello-blue")
    _assert_activity_mentions(workflow, blue.run_id, "router")
    _assert_activity_mentions(workflow, blue.run_id, "gateway")
    _assert_activity_mentions(workflow, blue.run_id, "cloudflared-gateway")
    _wait_public_gateway_ready(PUBLIC_GATEWAY_HOSTNAME)
    _assert_public_gateway_http_probe(PUBLIC_GATEWAY_HOSTNAME, "router.internal")
    _assert_body("http://router:8000/", "Hello from blue\n")

    green_graph = _router_graph(
        hello_document,
        router_document,
        gateway_document,
        cloudflared_document,
        workspace_id=workflow.workspace_id,
        active_hello_role="hello-green",
        message="Hello from green",
        authority_ref=RuntimeAuthorityReference(LOCAL_DOCKER_AUTHORITY_REF),
        public_hostname=PUBLIC_GATEWAY_HOSTNAME,
    )
    green = workflow.run_approved_transition(
        title="Hosted router green",
        graph=green_graph,
        current_graph_id=blue.current_graph_id,
        expected_desired_graph_id=blue.desired_graph_id,
    )
    _assert_activity_mentions(workflow, green.run_id, "hello-green")
    _assert_activity_mentions(workflow, green.run_id, "router")
    _wait_public_gateway_ready(PUBLIC_GATEWAY_HOSTNAME)
    _assert_public_gateway_http_probe(PUBLIC_GATEWAY_HOSTNAME, "router.internal")
    _assert_body("http://router:8000/", "Hello from green\n")
    _disconnect_runtime_networks(workflow.server_container, workspace_id=workflow.workspace_id)
    removed = workflow.run_approved_transition(
        title="Hosted router teardown",
        graph=DeploymentGraph(workflow.workspace_id),
        current_graph_id=green.current_graph_id,
        expected_desired_graph_id=green.desired_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, removed.run_id, "cloudflared-gateway")
    _assert_no_runtime_networks(workflow.workspace_id)


def _run_multi_workspace_foundation(
    base_url: str,
    server_container: str,
    servers_repo: Path,
) -> None:
    documents = {
        "hello": _product_document(servers_repo, "hello_server"),
        "router": _product_document(servers_repo, "http_active_router"),
        "multiplexer": _product_document(servers_repo, "http_multiplexer"),
        "postgres": _product_document(servers_repo, "postgres_server"),
    }
    workspace_products = {
        "workspace-a-router": {
            "hello": documents["hello"],
            "router": documents["router"],
        },
        "workspace-b-multiplexer": {
            "hello": documents["hello"],
            "multiplexer": documents["multiplexer"],
        },
        "workspace-c-postgres": {"postgres": documents["postgres"]},
        "workspace-d-negative-cleanup": {
            "hello": documents["hello"],
            "router": documents["router"],
            "multiplexer": documents["multiplexer"],
            "postgres": documents["postgres"],
        },
    }
    for workspace_id in WORKSPACE_IDS:
        workflow = _workflow_for(
            base_url,
            server_container=server_container,
            workspace_id=workspace_id,
        )
        _bootstrap_workspace(
            workflow,
            name=f"Hosted seeded stress {workspace_id}",
            product_documents=workspace_products[workspace_id],
            register_runtime_authority=True,
            register_runtime_delivery=True,
        )


def _run_seeded_stress_public_ingress(
    base_url: str,
    server_container: str,
    servers_repo: Path,
) -> None:
    _run_router_transition(
        _workflow_for(
            base_url,
            server_container=server_container,
            workspace_id="workspace-a-router",
        ),
        servers_repo,
    )
    _run_multiplexer_observer(
        _workflow_for(
            base_url,
            server_container=server_container,
            workspace_id="workspace-b-multiplexer",
        ),
        servers_repo,
    )
    _run_postgres_retained_data(
        _workflow_for(
            base_url,
            server_container=server_container,
            workspace_id="workspace-c-postgres",
        ),
        servers_repo,
    )
    _run_negative_cleanup(
        _workflow_for(
            base_url,
            server_container=server_container,
            workspace_id="workspace-d-negative-cleanup",
        ),
        servers_repo,
    )


def _run_multiplexer_observer(workflow: HostedWorkflow, servers_repo: Path) -> None:
    hello_document = _product_document(servers_repo, "hello_server")
    multiplexer_document = _product_document(servers_repo, "http_multiplexer")
    gateway_document = _product_document(servers_repo, "cpk_local_gateway")
    cloudflared_document = _product_document(servers_repo, "cloudflared_connector")
    graph = _multiplexer_graph(
        hello_document,
        multiplexer_document,
        gateway_document,
        cloudflared_document,
        workspace_id=workflow.workspace_id,
        authority_ref=RuntimeAuthorityReference(LOCAL_DOCKER_AUTHORITY_REF),
        public_hostname=PUBLIC_GATEWAY_HOSTNAME,
    )

    current_graph_id = _bootstrap_workspace(
        workflow,
        name="Hosted multiplexer observer",
        product_documents={
            "hello": hello_document,
            "multiplexer": multiplexer_document,
            "gateway": gateway_document,
            "cloudflared": cloudflared_document,
        },
        register_runtime_authority=True,
        register_runtime_delivery=True,
    )
    workflow.register_cloudflare_ingress_authority()
    result = workflow.run_approved_transition(
        title="Hosted multiplexer observer",
        graph=graph,
        current_graph_id=current_graph_id,
    )
    _assert_activity_mentions(workflow, result.run_id, "hello-primary")
    _assert_activity_mentions(workflow, result.run_id, "hello-observer")
    _assert_activity_mentions(workflow, result.run_id, "multiplexer")
    _assert_activity_mentions(workflow, result.run_id, "gateway")
    _assert_activity_mentions(workflow, result.run_id, "cloudflared-gateway")
    _wait_public_gateway_ready(PUBLIC_GATEWAY_HOSTNAME)
    _assert_public_gateway_http_probe(PUBLIC_GATEWAY_HOSTNAME, "multiplexer.internal")
    _assert_body("http://multiplexer:8000/", "Primary response\n")
    _assert_observer_receipt("http://hello-observer:8000/observations/requests")
    _disconnect_runtime_networks(workflow.server_container, workspace_id=workflow.workspace_id)
    removed = workflow.run_approved_transition(
        title="Hosted multiplexer teardown",
        graph=DeploymentGraph(workflow.workspace_id),
        current_graph_id=result.current_graph_id,
        expected_desired_graph_id=result.desired_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, removed.run_id, "cloudflared-gateway")
    _assert_no_runtime_networks(workflow.workspace_id)


def _run_postgres_retained_data(workflow: HostedWorkflow, servers_repo: Path) -> None:
    gateway_document = _product_document(servers_repo, "cpk_local_gateway")
    postgres_document = _product_document(servers_repo, "postgres_server")
    cloudflared_document = _product_document(servers_repo, "cloudflared_connector")
    graph = _postgres_graph(
        gateway_document,
        postgres_document,
        cloudflared_document,
        workspace_id=workflow.workspace_id,
        authority_ref=RuntimeAuthorityReference(LOCAL_DOCKER_AUTHORITY_REF),
        public_hostname=PUBLIC_GATEWAY_HOSTNAME,
    )

    current_graph_id = _bootstrap_workspace(
        workflow,
        name="Hosted postgres retained data",
        product_documents={
            "gateway": gateway_document,
            "postgres": postgres_document,
            "cloudflared": cloudflared_document,
        },
        register_runtime_authority=True,
        register_runtime_delivery=True,
    )
    workflow.register_cloudflare_ingress_authority()
    deployed = workflow.run_approved_transition(
        title="Hosted postgres deploy",
        graph=graph,
        current_graph_id=current_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, deployed.run_id, "gateway")
    _assert_activity_mentions(workflow, deployed.run_id, "postgres")
    _assert_activity_mentions(workflow, deployed.run_id, "cloudflared-gateway")
    _wait_public_gateway_ready(PUBLIC_GATEWAY_HOSTNAME)
    _assert_public_gateway_postgres_query_ready(PUBLIC_GATEWAY_HOSTNAME)
    _assert_gateway_postgres_query_ready(workflow.workspace_id, "gateway")
    retained_volumes = _retained_data_volumes(workflow.workspace_id, "postgres")
    if not retained_volumes:
        raise RuntimeError("postgres retained-data volume was not materialized")

    removed = workflow.run_approved_transition(
        title="Hosted postgres teardown",
        graph=DeploymentGraph(workflow.workspace_id),
        current_graph_id=deployed.current_graph_id,
        expected_desired_graph_id=deployed.desired_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, removed.run_id, "postgres")
    _assert_no_node_containers(workflow.workspace_id, "postgres")
    _assert_no_runtime_networks(workflow.workspace_id)
    _assert_retained_volumes_still_exist(retained_volumes)
    _assert_secret_absent_from_activity(
        workflow,
        "cpk-postgres-smoke-password",
    )


def _run_negative_cleanup(workflow: HostedWorkflow, servers_repo: Path) -> None:
    gateway_document = _product_document(servers_repo, "cpk_local_gateway")
    hello_document = _product_document(servers_repo, "hello_server")
    cloudflared_document = _product_document(servers_repo, "cloudflared_connector")
    graph = _public_gateway_ingress_graph(
        gateway_document,
        hello_document,
        cloudflared_document,
        workspace_id=workflow.workspace_id,
        authority_ref=RuntimeAuthorityReference(LOCAL_DOCKER_AUTHORITY_REF),
    )

    current_graph_id = _bootstrap_workspace(
        workflow,
        name="Hosted negative cleanup",
        product_documents={
            "gateway": gateway_document,
            "hello": hello_document,
            "cloudflared": cloudflared_document,
        },
        register_runtime_authority=True,
        register_runtime_delivery=True,
    )
    workflow.register_cloudflare_ingress_authority()
    deployed = workflow.run_approved_transition(
        title="Hosted negative cleanup deploy",
        graph=graph,
        current_graph_id=current_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, deployed.run_id, "gateway")
    _assert_activity_mentions(workflow, deployed.run_id, "hello")
    _assert_activity_mentions(workflow, deployed.run_id, "cloudflared-gateway")
    _wait_public_gateway_ready(PUBLIC_GATEWAY_HOSTNAME)
    _assert_public_gateway_private_probe(PUBLIC_GATEWAY_HOSTNAME)

    removed = workflow.run_approved_transition(
        title="Hosted negative cleanup teardown",
        graph=DeploymentGraph(workflow.workspace_id),
        current_graph_id=deployed.current_graph_id,
        expected_desired_graph_id=deployed.desired_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, removed.run_id, "cloudflared-gateway")
    _assert_public_gateway_unreachable(PUBLIC_GATEWAY_HOSTNAME)
    _assert_no_runtime_networks(workflow.workspace_id)
    _assert_secret_absent_from_activity(
        workflow,
        _required_env("OPENJ92_CLOUDFLARE_API_TOKEN"),
    )


def _run_public_gateway_ingress(workflow: HostedWorkflow, servers_repo: Path) -> None:
    gateway_document = _product_document(servers_repo, "cpk_local_gateway")
    hello_document = _product_document(servers_repo, "hello_server")
    cloudflared_document = _product_document(servers_repo, "cloudflared_connector")
    graph = _public_gateway_ingress_graph(
        gateway_document,
        hello_document,
        cloudflared_document,
        workspace_id=workflow.workspace_id,
        authority_ref=RuntimeAuthorityReference(LOCAL_DOCKER_AUTHORITY_REF),
    )

    current_graph_id = _bootstrap_workspace(
        workflow,
        name="Hosted public gateway ingress",
        product_documents={
            "gateway": gateway_document,
            "hello": hello_document,
            "cloudflared": cloudflared_document,
        },
        register_runtime_authority=True,
        register_runtime_delivery=True,
    )
    workflow.register_cloudflare_ingress_authority()
    deployed = workflow.run_approved_transition(
        title="Hosted public gateway ingress",
        graph=graph,
        current_graph_id=current_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, deployed.run_id, "gateway")
    _assert_activity_mentions(workflow, deployed.run_id, "hello")
    _assert_activity_mentions(workflow, deployed.run_id, "cloudflared-gateway")
    _wait_public_gateway_ready(PUBLIC_GATEWAY_HOSTNAME)
    _assert_public_gateway_private_probe(PUBLIC_GATEWAY_HOSTNAME)
    _assert_secret_absent_from_activity(
        workflow,
        _required_env("OPENJ92_CLOUDFLARE_API_TOKEN"),
    )
    removed = workflow.run_approved_transition(
        title="Hosted public gateway ingress teardown",
        graph=DeploymentGraph(workflow.workspace_id),
        current_graph_id=deployed.current_graph_id,
        expected_desired_graph_id=deployed.desired_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, removed.run_id, "cloudflared-gateway")
    _assert_no_runtime_networks(workflow.workspace_id)


def _run_public_gateway_toggle(workflow: HostedWorkflow, servers_repo: Path) -> None:
    gateway_document = _product_document(servers_repo, "cpk_local_gateway")
    hello_document = _product_document(servers_repo, "hello_server")
    cloudflared_document = _product_document(servers_repo, "cloudflared_connector")

    public_graph = _public_gateway_ingress_graph(
        gateway_document,
        hello_document,
        cloudflared_document,
        workspace_id=workflow.workspace_id,
        authority_ref=RuntimeAuthorityReference(LOCAL_DOCKER_AUTHORITY_REF),
    )
    private_graph = _single_hello_graph(
        hello_document,
        workspace_id=workflow.workspace_id,
        authority_ref=RuntimeAuthorityReference(LOCAL_DOCKER_AUTHORITY_REF),
        message="Hello through public ingress",
    )

    current_graph_id = _bootstrap_workspace(
        workflow,
        name="Hosted public gateway toggle",
        product_documents={
            "gateway": gateway_document,
            "hello": hello_document,
            "cloudflared": cloudflared_document,
        },
        register_runtime_authority=True,
        register_runtime_delivery=True,
    )
    workflow.register_cloudflare_ingress_authority()

    public_on = workflow.run_approved_transition(
        title="Hosted public gateway toggle on",
        graph=public_graph,
        current_graph_id=current_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, public_on.run_id, "gateway")
    _assert_activity_mentions(workflow, public_on.run_id, "hello")
    _assert_activity_mentions(workflow, public_on.run_id, "cloudflared-gateway")
    _wait_public_gateway_ready(PUBLIC_GATEWAY_HOSTNAME)
    _assert_public_gateway_private_probe(PUBLIC_GATEWAY_HOSTNAME)

    public_off = workflow.run_approved_transition(
        title="Hosted public gateway toggle off",
        graph=private_graph,
        current_graph_id=public_on.current_graph_id,
        expected_desired_graph_id=public_on.desired_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, public_off.run_id, "cloudflared-gateway")
    _assert_public_gateway_unreachable(PUBLIC_GATEWAY_HOSTNAME)
    _sync_runtime_networks(workflow.server_container, workspace_id=workflow.workspace_id)
    _assert_body("http://hello:8000/", "Hello through public ingress\n")

    public_on_again = workflow.run_approved_transition(
        title="Hosted public gateway toggle on again",
        graph=public_graph,
        current_graph_id=public_off.current_graph_id,
        expected_desired_graph_id=public_off.desired_graph_id,
        sync_runtime_networks=True,
    )
    _assert_activity_mentions(workflow, public_on_again.run_id, "gateway")
    _assert_activity_mentions(workflow, public_on_again.run_id, "cloudflared-gateway")
    _wait_public_gateway_ready(PUBLIC_GATEWAY_HOSTNAME)
    _assert_public_gateway_private_probe(PUBLIC_GATEWAY_HOSTNAME)

    _disconnect_runtime_networks(workflow.server_container, workspace_id=workflow.workspace_id)
    removed = workflow.run_approved_transition(
        title="Hosted public gateway toggle teardown",
        graph=DeploymentGraph(workflow.workspace_id),
        current_graph_id=public_on_again.current_graph_id,
        expected_desired_graph_id=public_on_again.desired_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, removed.run_id, "cloudflared-gateway")
    _assert_no_runtime_networks(workflow.workspace_id)


def _run_authenticated_gateway_private(
    workflow: HostedWorkflow,
    servers_repo: Path,
) -> None:
    gateway_document = _product_document(servers_repo, "cpk_local_gateway")
    hello_document = _product_document(servers_repo, "hello_server")
    postgres_document = _product_document(servers_repo, "postgres_server")

    graph = _authenticated_gateway_private_graph(
        gateway_document,
        hello_document,
        postgres_document,
        workspace_id=workflow.workspace_id,
        authority_ref=RuntimeAuthorityReference(LOCAL_DOCKER_AUTHORITY_REF),
    )
    current_graph_id = _bootstrap_workspace(
        workflow,
        name="Hosted authenticated gateway private",
        product_documents={
            "gateway": gateway_document,
            "hello": hello_document,
            "postgres": postgres_document,
        },
        register_runtime_authority=True,
        register_runtime_delivery=True,
    )
    deployed = workflow.run_approved_transition(
        title="Hosted authenticated gateway private deploy",
        graph=graph,
        current_graph_id=current_graph_id,
        sync_runtime_networks=True,
    )
    _assert_activity_mentions(workflow, deployed.run_id, "gateway")
    _assert_activity_mentions(workflow, deployed.run_id, "hello")
    _assert_activity_mentions(workflow, deployed.run_id, "postgres")
    _sync_runtime_networks(workflow.server_container, workspace_id=workflow.workspace_id)

    http_probe = workflow.request_gateway_probe_http(
        request_id=f"{workflow.workspace_id}:gateway-probe:http",
        expected_current_graph_id=deployed.current_graph_id,
        gateway_node_id="gateway",
        kind="http-status",
        target_id="hello.internal",
        path="/",
    )
    _assert_gateway_probe_succeeded(http_probe, target_id="hello.internal")
    postgres_probe = workflow.request_gateway_probe_mcp(
        request_id=f"{workflow.workspace_id}:gateway-probe:postgres",
        expected_current_graph_id=deployed.current_graph_id,
        gateway_node_id="gateway",
        kind="postgres-select-one",
        target_id="postgres.postgres",
    )
    _assert_gateway_probe_succeeded(postgres_probe, target_id="postgres.postgres")
    _assert_gateway_probe_detail_matches(
        workflow,
        http_probe,
        expected_target_id="hello.internal",
    )
    _assert_gateway_probe_detail_matches(
        workflow,
        postgres_probe,
        expected_target_id="postgres.postgres",
    )
    _assert_gateway_rejects_unauthenticated_without_target_io()
    _assert_gateway_rejects_replay_while_alive(workflow.workspace_id)
    _assert_secret_absent_from_activity(
        workflow,
        _required_env("CPK_GATEWAY_PROBE_PRIVATE_KEY_PEM"),
    )

    _disconnect_runtime_networks(
        workflow.server_container,
        workspace_id=workflow.workspace_id,
    )
    removed = workflow.run_approved_transition(
        title="Hosted authenticated gateway private teardown",
        graph=DeploymentGraph(workflow.workspace_id),
        current_graph_id=deployed.current_graph_id,
        expected_desired_graph_id=deployed.desired_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, removed.run_id, "gateway")
    _assert_no_runtime_networks(workflow.workspace_id)


def _workflow_for(
    base_url: str,
    *,
    server_container: str,
    workspace_id: str,
) -> HostedWorkflow:
    return HostedWorkflow(
        base_url,
        workspace_id=workspace_id,
        worker_id=WORKER_ID,
        server_container=server_container,
    )


def _bootstrap_workspace(
    workflow: HostedWorkflow,
    *,
    name: str,
    product_documents: dict[str, Any],
    register_runtime_authority: bool,
    register_runtime_delivery: bool,
) -> str:
    current_graph_id = workflow.create_workspace(name=name)
    _register_pull_authority_if_requested(workflow)
    if register_runtime_authority:
        workflow.register_local_docker_authority()
    if register_runtime_delivery:
        workflow.register_local_docker_delivery()
    for label, document in product_documents.items():
        workflow.import_product(label, document)
    return current_graph_id


def _register_pull_authority_if_requested(workflow: HostedWorkflow) -> None:
    if os.environ.get("CPK_HOSTED_ACTIVITY_REGISTER_PULL_AUTHORITY") == "docker-config":
        workflow.register_ghcr_pull_authority_from_docker_config()


def _assert_activity_mentions(
    workflow: HostedWorkflow,
    run_id: str,
    node_id: str,
) -> None:
    events = _events_for_run(workflow.read_activity(limit=200), run_id)
    for event in events:
        payload = event.get("payload", {})
        if payload.get("node_id") == node_id and event.get("event_type") == "step_succeeded":
            return
    raise RuntimeError(f"activity timeline did not record successful step for {node_id}")


def _assert_runtime_activity_mentions(
    workflow: HostedWorkflow,
    run_id: str,
    runtime_id: str,
) -> None:
    events = _events_for_run(workflow.read_activity(limit=200), run_id)
    for event in events:
        payload = event.get("payload", {})
        if (
            payload.get("runtime_id") == runtime_id
            and event.get("event_type") == "step_succeeded"
        ):
            return
    raise RuntimeError(
        f"activity timeline did not record successful runtime step for {runtime_id}"
    )


def _events_for_run(timeline: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    for session in timeline.get("sessions", []):
        for plan in session.get("plans", []):
            for run in plan.get("runs", []):
                if run.get("run_id") == run_id:
                    events = run.get("events")
                    if isinstance(events, list):
                        return events
    raise RuntimeError(f"activity timeline did not expose run {run_id}")


def _assert_gateway_probe_succeeded(
    response: dict[str, Any],
    *,
    target_id: str,
) -> None:
    probe = response.get("gateway_probe")
    if not isinstance(probe, dict):
        raise RuntimeError(f"gateway probe response did not include attempt: {response}")
    if (
        probe.get("status") != "succeeded"
        or probe.get("target_id") != target_id
        or probe.get("result_code") != "probe-succeeded"
    ):
        raise RuntimeError(f"gateway probe did not succeed: {response}")
    encoded = json.dumps(response, sort_keys=True).lower()
    if any(forbidden in encoded for forbidden in ("private key", "secret://", "password")):
        raise RuntimeError("gateway probe response leaked secret-shaped material")


def _assert_gateway_probe_detail_matches(
    workflow: HostedWorkflow,
    response: dict[str, Any],
    *,
    expected_target_id: str,
) -> None:
    attempt = response.get("gateway_probe")
    if not isinstance(attempt, dict):
        raise RuntimeError(f"gateway probe response did not include attempt: {response}")
    detail = workflow.read_gateway_probe_detail(str(attempt["probe_id"]))
    probe = detail.get("gateway_probe")
    if not isinstance(probe, dict) or probe.get("target_id") != expected_target_id:
        raise RuntimeError(f"gateway probe detail mismatch: {detail}")
    if probe.get("request_digest") != attempt.get("request_digest"):
        raise RuntimeError(f"gateway probe detail lost request digest: {detail}")


def _assert_gateway_rejects_unauthenticated_without_target_io() -> None:
    before = _hello_observation_count()
    body = _gateway_probe_body(
        GatewayProbeRequest(
            GatewayProbeCommandKind.HTTP_STATUS,
            GatewayTargetId("hello.internal"),
            "/",
        )
    )
    missing = _direct_gateway_probe(body)
    forged = _direct_gateway_probe(
        body,
        authorization=(
            "CPK-Gateway "
            + _signed_gateway_capability(
                Ed25519PrivateKey.generate(),
                _direct_gateway_grant(
                    GatewayProbeRequest(
                        GatewayProbeCommandKind.HTTP_STATUS,
                        GatewayTargetId("hello.internal"),
                        "/",
                    ),
                    jti="forged-jti",
                ),
            )
        ),
    )
    after = _hello_observation_count()
    if missing.status != 401 or forged.status != 401:
        raise RuntimeError(
            f"unauthenticated/forged gateway calls were not rejected: "
            f"missing={missing.status} forged={forged.status}"
        )
    if after != before:
        raise RuntimeError("rejected gateway call reached private target")


def _assert_gateway_rejects_replay_while_alive(workspace_id: str) -> None:
    private_key = serialization.load_pem_private_key(
        _required_env("CPK_GATEWAY_PROBE_PRIVATE_KEY_PEM").encode("ascii"),
        password=None,
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise RuntimeError("gateway test private key is not Ed25519")
    request = GatewayProbeRequest(
        GatewayProbeCommandKind.HTTP_STATUS,
        GatewayTargetId("hello.internal"),
        "/",
    )
    token = _signed_gateway_capability(
        private_key,
        _direct_gateway_grant(request, jti=f"direct-replay-{int(time.time())}"),
    )
    first = _direct_gateway_probe(
        _gateway_probe_body(request),
        authorization=f"CPK-Gateway {token}",
    )
    second = _direct_gateway_probe(
        _gateway_probe_body(request),
        authorization=f"CPK-Gateway {token}",
    )
    if first.status != 200 or second.status != 409:
        raise RuntimeError(
            f"gateway replay was not rejected while alive: "
            f"first={first.status} second={second.status}"
        )


def _execute_to_completion(
    base_url: str,
    server_container: str,
    run_id: str,
    *,
    workspace_id: str = WORKSPACE_ID,
    worker_id: str = WORKER_ID,
    sync_runtime_networks: bool = True,
    worker_authorization: str = WORKER_AUTHORIZATION,
) -> None:
    for attempt in range(80):
        if sync_runtime_networks:
            _sync_runtime_networks(server_container, workspace_id=workspace_id)
        result = _mcp_tool(
            base_url,
            "command.deployment.execute",
            {
                "workspace_id": workspace_id,
                "run_id": run_id,
                "worker_id": worker_id,
                "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
                "idempotency_key": f"{workspace_id}:execute:{attempt}",
                "max_effects": 1,
            },
            timeout=60,
            authorization=worker_authorization,
        )
        if sync_runtime_networks:
            _sync_runtime_networks(server_container, workspace_id=workspace_id)
        if result["coordinator_status"] == "completed":
            return
        if result["coordinator_status"] in {"failed", "unsupported", "uncertain", "blocked"}:
            timeline = _http(base_url, "GET", f"/workspaces/{workspace_id}/activity")
            raise RuntimeError(f"execution stopped with {result}; timeline={timeline}")
    raise RuntimeError("hosted activity execution did not complete")


def _sync_runtime_networks(server_container: str, *, workspace_id: str = WORKSPACE_ID) -> None:
    import docker
    from docker.errors import APIError, NotFound

    client = docker.from_env()
    controller_container = socket.gethostname()
    for network in client.networks.list():
        name = network.name
        if not name.startswith(f"cpk-net-{workspace_id}"):
            continue
        for container in (server_container, controller_container):
            try:
                network.connect(container)
            except APIError as error:
                if "already exists" in str(error).lower():
                    continue
                raise
            except NotFound:
                continue


def _disconnect_runtime_networks(
    server_container: str,
    *,
    workspace_id: str = WORKSPACE_ID,
) -> None:
    import docker
    from docker.errors import APIError, NotFound

    client = docker.from_env()
    controller_container = socket.gethostname()
    for network in client.networks.list():
        name = network.name
        if not name.startswith(f"cpk-net-{workspace_id}"):
            continue
        for container in (server_container, controller_container):
            try:
                network.disconnect(container, force=True)
            except APIError as error:
                message = str(error).lower()
                if "not connected" in message or "no such container" in message:
                    continue
                raise
            except NotFound:
                continue


def _wait_ready(
    base_url: str,
    *,
    policy: VerificationPolicy | None = None,
) -> None:
    maximum_attempts = 30 if policy is None else policy.maximum_attempts
    request_timeout = 30 if policy is None else policy.timeout_seconds
    interval_seconds = 1.0 if policy is None else policy.interval_seconds
    for attempt in range(1, maximum_attempts + 1):
        if attempt > 1:
            time.sleep(interval_seconds)
        try:
            ready = _http(
                base_url,
                "GET",
                "/health/ready",
                authorize=False,
                timeout=request_timeout,
            )
        except Exception:
            continue
        if ready.get("status") == "ready":
            if ready.get("runtime_interpreters") != "docker":
                raise RuntimeError(f"cpk-server did not boot with Docker runtime: {ready}")
            return
    raise RuntimeError("cpk-server did not become ready")


def _single_hello_graph(
    product_document: Any,
    *,
    workspace_id: str = WORKSPACE_ID,
    authority_ref: RuntimeAuthorityReference | None = None,
    message: str | None = None,
) -> DeploymentGraph:
    product = product_document.product
    configuration = ProductInstanceConfiguration.from_contract(product.runtime_contract)
    if message is not None:
        configuration = _with_public_environment(
            configuration,
            {"HELLO_MESSAGE": message},
        )
    block = instantiate_product(
        product,
        "hello",
        configuration,
    )
    return compile_topology(
        DeploymentTopology(
            workspace_id,
            DockerRuntime(
                runtime_id="docker",
                network_name=f"control-plane-kit-{workspace_id}-docker",
                authority_ref=authority_ref,
                children=(block,),
            ),
        )
    )


def _router_graph(
    hello_document: Any,
    router_document: Any,
    gateway_document: Any | None = None,
    cloudflared_document: Any | None = None,
    *,
    workspace_id: str,
    active_hello_role: str,
    message: str,
    authority_ref: RuntimeAuthorityReference | None = None,
    public_hostname: str | None = None,
) -> DeploymentGraph:
    hello_product = hello_document.product
    router_product = router_document.product
    hello = instantiate_product(
        hello_product,
        active_hello_role,
        _with_public_environment(
            ProductInstanceConfiguration.from_contract(hello_product.runtime_contract),
            {"HELLO_MESSAGE": message},
        ),
    )
    router = instantiate_product(
        router_product,
        "router",
        ProductInstanceConfiguration.from_contract(router_product.runtime_contract),
    )
    children: tuple[object, ...] = (
        hello,
        router,
        SocketConnection(
            active_hello_role,
            "internal",
            "router",
            "active",
        ),
    )
    public_ingresses: tuple[NamedPublicIngress, ...] = ()
    if public_hostname is not None:
        if gateway_document is None or cloudflared_document is None:
            raise RuntimeError("public router graph requires gateway and cloudflared products")
        gateway, cloudflared, ingress = _public_gateway_overlay(
            gateway_document,
            cloudflared_document,
            workspace_id=workspace_id,
            target_node_id="gateway",
            target_provider_socket="control",
            connector_node_id="cloudflared-gateway",
            public_hostname=public_hostname,
        )
        children = children + (
            gateway,
            cloudflared,
            SocketConnection("router", "internal", "gateway", "target-http"),
        )
        public_ingresses = (ingress,)
    return compile_topology(
        DeploymentTopology(
            workspace_id,
            DockerRuntime(
                runtime_id="docker",
                network_name=f"control-plane-kit-{workspace_id}-docker",
                authority_ref=authority_ref,
                children=children,
            ),
            public_ingresses=public_ingresses,
        )
    )


def _multiplexer_graph(
    hello_document: Any,
    multiplexer_document: Any,
    gateway_document: Any | None = None,
    cloudflared_document: Any | None = None,
    *,
    workspace_id: str,
    authority_ref: RuntimeAuthorityReference | None = None,
    public_hostname: str | None = None,
) -> DeploymentGraph:
    hello_product = hello_document.product
    multiplexer_product = multiplexer_document.product
    primary = instantiate_product(
        hello_product,
        "hello-primary",
        _with_public_environment(
            ProductInstanceConfiguration.from_contract(hello_product.runtime_contract),
            {"HELLO_MESSAGE": "Primary response"},
        ),
    )
    observer = instantiate_product(
        hello_product,
        "hello-observer",
        _with_public_environment(
            ProductInstanceConfiguration.from_contract(hello_product.runtime_contract),
            {"HELLO_MESSAGE": "Observer response"},
        ),
    )
    multiplexer = instantiate_product(
        multiplexer_product,
        "multiplexer",
        ProductInstanceConfiguration.from_contract(multiplexer_product.runtime_contract),
    )
    children: tuple[object, ...] = (
        primary,
        observer,
        multiplexer,
        SocketConnection("hello-primary", "internal", "multiplexer", "primary"),
        SocketConnection(
            "hello-observer",
            "internal",
            "multiplexer",
            "observer-a",
        ),
    )
    public_ingresses: tuple[NamedPublicIngress, ...] = ()
    if public_hostname is not None:
        if gateway_document is None or cloudflared_document is None:
            raise RuntimeError("public multiplexer graph requires gateway and cloudflared products")
        gateway, cloudflared, ingress = _public_gateway_overlay(
            gateway_document,
            cloudflared_document,
            workspace_id=workspace_id,
            target_node_id="gateway",
            target_provider_socket="control",
            connector_node_id="cloudflared-gateway",
            public_hostname=public_hostname,
        )
        children = children + (
            gateway,
            cloudflared,
            SocketConnection("multiplexer", "internal", "gateway", "target-http"),
        )
        public_ingresses = (ingress,)
    return compile_topology(
        DeploymentTopology(
            workspace_id,
            DockerRuntime(
                runtime_id="docker",
                network_name=f"control-plane-kit-{workspace_id}-docker",
                authority_ref=authority_ref,
                children=children,
            ),
            public_ingresses=public_ingresses,
        )
    )


def _postgres_graph(
    gateway_document: Any,
    postgres_document: Any,
    cloudflared_document: Any | None = None,
    *,
    workspace_id: str,
    authority_ref: RuntimeAuthorityReference | None = None,
    public_hostname: str | None = None,
) -> DeploymentGraph:
    gateway_product = gateway_document.product
    postgres_product = postgres_document.product
    gateway = _configured_gateway_instance(
        gateway_product,
        "gateway",
        workspace_id=workspace_id,
    )
    gateway = replace(
        gateway,
        spec=replace(gateway.spec, verification=VerificationContract()),
    )
    postgres = instantiate_product(
        postgres_product,
        "postgres",
        ProductInstanceConfiguration.from_contract(postgres_product.runtime_contract),
    )
    postgres = replace(
        postgres,
        spec=replace(postgres.spec, verification=VerificationContract()),
    )
    children: tuple[object, ...] = (
        gateway,
        postgres,
        SocketConnection(
            "postgres",
            "postgres",
            "gateway",
            "target-postgres",
        ),
    )
    public_ingresses: tuple[NamedPublicIngress, ...] = ()
    if public_hostname is not None:
        if cloudflared_document is None:
            raise RuntimeError("public postgres graph requires cloudflared product")
        cloudflared_product = cloudflared_document.product
        cloudflared = instantiate_product(
            cloudflared_product,
            "cloudflared-gateway",
            ProductInstanceConfiguration.from_contract(
                cloudflared_product.runtime_contract
            ),
        )
        children = children + (cloudflared,)
        public_ingresses = (
            _named_public_gateway_ingress(
                target_node_id="gateway",
                target_provider_socket="control",
                connector_node_id="cloudflared-gateway",
                public_hostname=public_hostname,
            ),
        )
    return compile_topology(
        DeploymentTopology(
            workspace_id,
            DockerRuntime(
                runtime_id="docker",
                network_name=f"control-plane-kit-{workspace_id}-docker",
                authority_ref=authority_ref,
                children=children,
            ),
            public_ingresses=public_ingresses,
        )
    )


def _public_gateway_ingress_graph(
    gateway_document: Any,
    hello_document: Any,
    cloudflared_document: Any,
    *,
    workspace_id: str,
    authority_ref: RuntimeAuthorityReference | None = None,
) -> DeploymentGraph:
    hello_product = hello_document.product
    hello = instantiate_product(
        hello_product,
        "hello",
        _with_public_environment(
            ProductInstanceConfiguration.from_contract(hello_product.runtime_contract),
            {"HELLO_MESSAGE": "Hello through public ingress"},
        ),
    )
    gateway, cloudflared, ingress = _public_gateway_overlay(
        gateway_document,
        cloudflared_document,
        workspace_id=workspace_id,
        target_node_id="gateway",
        target_provider_socket="control",
        connector_node_id="cloudflared-gateway",
        public_hostname=PUBLIC_GATEWAY_HOSTNAME,
    )
    return compile_topology(
        DeploymentTopology(
            workspace_id,
            DockerRuntime(
                runtime_id="docker",
                network_name=f"control-plane-kit-{workspace_id}-docker",
                authority_ref=authority_ref,
                children=(
                    gateway,
                    hello,
                    cloudflared,
                    SocketConnection("hello", "internal", "gateway", "target-http"),
                ),
            ),
            public_ingresses=(ingress,),
        )
    )


def _public_gateway_overlay(
    gateway_document: Any,
    cloudflared_document: Any,
    *,
    workspace_id: str,
    target_node_id: str,
    target_provider_socket: str,
    connector_node_id: str,
    public_hostname: str,
) -> tuple[object, object, NamedPublicIngress]:
    gateway_product = gateway_document.product
    cloudflared_product = cloudflared_document.product
    gateway = _configured_gateway_instance(
        gateway_product,
        target_node_id,
        workspace_id=workspace_id,
    )
    cloudflared = instantiate_product(
        cloudflared_product,
        connector_node_id,
        ProductInstanceConfiguration.from_contract(cloudflared_product.runtime_contract),
    )
    return (
        gateway,
        cloudflared,
        _named_public_gateway_ingress(
            target_node_id=target_node_id,
            target_provider_socket=target_provider_socket,
            connector_node_id=connector_node_id,
            public_hostname=public_hostname,
        ),
    )


def _authenticated_gateway_private_graph(
    gateway_document: Any,
    hello_document: Any,
    postgres_document: Any,
    *,
    workspace_id: str,
    authority_ref: RuntimeAuthorityReference | None = None,
) -> DeploymentGraph:
    gateway_product = gateway_document.product
    hello_product = hello_document.product
    postgres_product = postgres_document.product
    gateway = _configured_gateway_instance(
        gateway_product,
        "gateway",
        workspace_id=workspace_id,
    )
    gateway = replace(
        gateway,
        spec=replace(gateway.spec, verification=VerificationContract()),
    )
    hello = instantiate_product(
        hello_product,
        "hello",
        _with_public_environment(
            ProductInstanceConfiguration.from_contract(hello_product.runtime_contract),
            {"HELLO_MESSAGE": "Hello through authenticated gateway"},
        ),
    )
    postgres = instantiate_product(
        postgres_product,
        "postgres",
        ProductInstanceConfiguration.from_contract(postgres_product.runtime_contract),
    )
    postgres = replace(
        postgres,
        spec=replace(postgres.spec, verification=VerificationContract()),
    )
    return compile_topology(
        DeploymentTopology(
            workspace_id,
            DockerRuntime(
                runtime_id="docker",
                network_name=f"control-plane-kit-{workspace_id}-docker",
                authority_ref=authority_ref,
                children=(
                    gateway,
                    hello,
                    postgres,
                    SocketConnection("hello", "internal", "gateway", "target-http"),
                    SocketConnection(
                        "postgres",
                        "postgres",
                        "gateway",
                        "target-postgres",
                    ),
                ),
            ),
        )
    )


def _configured_gateway_instance(
    gateway_product: Any,
    node_id: str,
    *,
    workspace_id: str,
) -> Any:
    audience = f"gateway:{workspace_id}:{node_id}"
    return instantiate_product(
        gateway_product,
        node_id,
        _with_public_environment(
            ProductInstanceConfiguration.from_contract(gateway_product.runtime_contract),
            {
                "CPK_GATEWAY_PROBE_VERIFIER": "ed25519",
                "CPK_GATEWAY_PROBE_ISSUER": GATEWAY_PROBE_ISSUER,
                "CPK_GATEWAY_PROBE_AUDIENCE": audience,
                "CPK_GATEWAY_PROBE_NODE_ID": node_id,
                "CPK_GATEWAY_PROBE_VERIFICATION_KEYS_JSON": _required_env(
                    "CPK_GATEWAY_PROBE_PUBLIC_KEYS_JSON"
                ),
            },
        ),
    )


def _named_public_gateway_ingress(
    *,
    target_node_id: str,
    target_provider_socket: str,
    connector_node_id: str,
    public_hostname: str,
) -> NamedPublicIngress:
    return NamedPublicIngress(
        ingress_id="gateway-public",
        authority_ref=IngressAuthorityReference(
            OPENJ92_INGRESS_AUTHORITY_REF,
        ),
        target=PublicIngressTarget(target_node_id, target_provider_socket),
        connector_node_id=connector_node_id,
        hostname=public_hostname,
    )


def _with_public_environment(
    configuration: ProductInstanceConfiguration,
    replacements: dict[str, str],
) -> ProductInstanceConfiguration:
    bindings = {binding.name: binding for binding in configuration.public_environment}
    for name, value in replacements.items():
        if name not in bindings:
            raise RuntimeError(f"product does not declare public environment {name}")
        bindings[name] = PublicStaticEnvironmentBinding(name, value)
    return ProductInstanceConfiguration(
        public_environment=tuple(bindings.values()),
        configuration_artifacts=configuration.configuration_artifacts,
        secret_deliveries=configuration.secret_deliveries,
    )


def _product_document(servers_repo: Path, product_name: str) -> Any:
    return ProductDescriptorCodec().decode_document(
        (servers_repo / "products" / product_name / "product.cpk.json").read_bytes()
    )


def _mcp_tool(
    base_url: str,
    name: str,
    arguments: dict[str, object],
    *,
    timeout: int = 10,
    authorization: str | None = None,
) -> dict[str, Any]:
    return _mcp(
        base_url,
        "tools/call",
        name,
        arguments,
        timeout=timeout,
        authorization=authorization,
    )


def _mcp_read(base_url: str, name: str, arguments: dict[str, object]) -> dict[str, Any]:
    return _mcp(base_url, "resources/read", name, arguments)


def _mcp(
    base_url: str,
    method: str,
    name: str,
    arguments: dict[str, object],
    *,
    timeout: int = 10,
    authorization: str | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "MCP-Protocol-Version": "2025-06-18",
        "Mcp-Method": method,
    }
    if authorization is not None:
        headers["Authorization"] = authorization
    response = _http(
        base_url,
        "POST",
        "/mcp",
        {
            "jsonrpc": "2.0",
            "id": f"{name}:1",
            "method": method,
            "params": {"name": name, "arguments": arguments},
        },
        extra_headers=headers,
        timeout=timeout,
    )
    if "error" in response:
        raise RuntimeError(f"MCP {name} failed: {response}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"MCP {name} returned non-object result: {response}")
    return result


def _http(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    *,
    authorize: bool = True,
    extra_headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if authorize:
        headers["Authorization"] = AUTHORIZATION
    if extra_headers:
        headers.update(extra_headers)
    request = Request(
        f"{base_url}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(1024 * 1024)
    except HTTPError as error:
        detail = error.read(8192).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {method} {path} failed {error.code}: {detail}") from error
    decoded = json.loads(data.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError(f"HTTP {method} {path} returned non-object JSON")
    return decoded


def _assert_body(url: str, expected: str) -> None:
    with urlopen(url, timeout=5) as response:
        body = response.read(1024).decode("utf-8")
    if body != expected:
        raise RuntimeError(f"unexpected response from {url}: {body!r}")


def _assert_observer_receipt(url: str) -> None:
    with urlopen(url, timeout=5) as response:
        payload = json.loads(response.read(16_384).decode("utf-8"))
    encoded = json.dumps(payload, sort_keys=True)
    if any(forbidden in encoded.lower() for forbidden in ("headers", "body", "secret")):
        raise RuntimeError(f"observer receipt leaked forbidden material: {encoded}")
    requests = payload.get("requests")
    if not isinstance(requests, list) or not requests:
        raise RuntimeError(f"observer receipt did not include requests: {payload}")
    for observed in requests:
        if observed == {"method": "GET", "path": "/"}:
            return
    raise RuntimeError(f"observer did not record copied GET / request: {payload}")


def _assert_gateway_postgres_query_ready(workspace_id: str, node_id: str) -> None:
    container = _single_docker_container(workspace_id, node_id)
    script = """
import json
from urllib.request import Request, urlopen

payload = json.dumps({
    "kind": "postgres-select-one",
    "target_id": "postgres.postgres",
}).encode("utf-8")
request = Request(
    "http://127.0.0.1:8000/cpk/probes",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urlopen(request, timeout=5) as response:
    decoded = json.loads(response.read(16384).decode("utf-8"))
if decoded.get("outcome") != "passed":
    raise SystemExit(json.dumps(decoded, sort_keys=True))
print(json.dumps(decoded, sort_keys=True))
"""
    last_output = ""
    for _ in range(30):
        result = container.exec_run(["python", "-c", script])
        last_output = result.output.decode("utf-8", errors="replace")[:2048]
        if result.exit_code == 0:
            if any(
                forbidden in last_output.lower()
                for forbidden in (
                    "cpk-postgres-smoke-password",
                    "secret://",
                    "password=",
                )
            ):
                raise RuntimeError("gateway postgres probe leaked secret material")
            return
        time.sleep(1)
    raise RuntimeError(f"gateway postgres readiness failed: {last_output}")


def _wait_public_gateway_ready(hostname: str) -> None:
    last_error = "not attempted"
    for attempt in range(PUBLIC_GATEWAY_READY_ATTEMPTS):
        try:
            response = _public_https_json(
                hostname,
                "GET",
                "/health/ready",
                timeout=5,
            )
            if response.status == 200:
                body = json.loads(response.body.decode("utf-8"))
                if body.get("status") == "ready":
                    return
                last_error = f"status={response.status} body={body!r}"
            else:
                last_error = f"status={response.status} body={response.body[:256]!r}"
        except Exception as error:
            last_error = f"{type(error).__name__}: {error}"
        if attempt < PUBLIC_GATEWAY_READY_ATTEMPTS - 1:
            time.sleep(PUBLIC_GATEWAY_READY_RETRY_SECONDS)
    raise RuntimeError(
        f"public gateway did not become ready: {hostname}; last_error={last_error}"
    )


def _assert_public_gateway_private_probe(hostname: str) -> None:
    _assert_public_gateway_http_probe(hostname, "hello.internal")


def _assert_public_gateway_http_probe(hostname: str, target_id: str) -> None:
    response = _public_https_json(
        hostname,
        "POST",
        "/cpk/probes",
        body=json.dumps(
            {"kind": "http-status", "target_id": target_id, "path": "/"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
        timeout=10,
    )
    payload = json.loads(response.body.decode("utf-8"))
    if payload.get("outcome") != "passed" or payload.get("status") != 200:
        raise RuntimeError(f"public gateway private probe failed: {payload!r}")
    encoded = json.dumps(payload).lower()
    if "secret" in encoded or "token" in encoded or "password" in encoded:
        raise RuntimeError("public probe response leaked secret-shaped material")


def _assert_public_gateway_postgres_query_ready(hostname: str) -> None:
    response = _public_https_json(
        hostname,
        "POST",
        "/cpk/probes",
        body=json.dumps(
            {"kind": "postgres-select-one", "target_id": "postgres.postgres"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
        timeout=10,
    )
    payload = json.loads(response.body.decode("utf-8"))
    if payload.get("outcome") != "passed" or payload.get("probe") != "postgres-select-one":
        raise RuntimeError(f"public gateway postgres probe failed: {payload!r}")
    encoded = json.dumps(payload).lower()
    if "secret" in encoded or "token" in encoded or "password" in encoded:
        raise RuntimeError("public postgres probe response leaked secret-shaped material")


def _assert_public_gateway_unreachable(hostname: str) -> None:
    last_success: str | None = None
    for _ in range(30):
        try:
            response = _public_https_json(
                hostname,
                "GET",
                "/health/ready",
                timeout=5,
            )
            if response.status == 200:
                body = json.loads(response.body.decode("utf-8"))
                if body.get("status") == "ready":
                    last_success = repr(body)
                    time.sleep(2)
                    continue
            return
        except Exception:
            return
    raise RuntimeError(
        f"public gateway remained reachable after overlay removal: {last_success}"
    )


def _direct_gateway_probe(
    body: bytes,
    *,
    authorization: str | None = None,
) -> _PublicHttpsResponse:
    headers = {"Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    request = Request(
        "http://gateway:8000/cpk/probes",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return _PublicHttpsResponse(
                status=int(response.status),
                body=response.read(16_384),
            )
    except HTTPError as error:
        return _PublicHttpsResponse(
            status=int(error.code),
            body=error.read(16_384),
        )


def _hello_observation_count() -> int:
    try:
        with urlopen("http://hello:8000/observations/requests", timeout=5) as response:
            payload = json.loads(response.read(16_384).decode("utf-8"))
    except Exception as error:
        raise RuntimeError(f"hello observation read failed: {type(error).__name__}") from error
    requests = payload.get("requests")
    if not isinstance(requests, list):
        raise RuntimeError(f"hello observation payload was malformed: {payload}")
    return len(requests)


def _gateway_probe_body(request: GatewayProbeRequest) -> bytes:
    return json.dumps(
        request.descriptor(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _direct_gateway_grant(
    request: GatewayProbeRequest,
    *,
    jti: str,
) -> DelegatedGatewayProbeGrant:
    now = int(time.time())
    workspace_id = os.environ.get(
        "CPK_HOSTED_ACTIVITY_WORKSPACE_ID",
        DEFAULT_WORKSPACE_ID,
    )
    return DelegatedGatewayProbeGrant(
        issuer=GATEWAY_PROBE_ISSUER,
        key_id=GATEWAY_PROBE_KEY_ID,
        audience=f"gateway:{workspace_id}:gateway",
        workspace_id=workspace_id,
        operation_id="direct-adversarial-diagnostic",
        request_id=f"direct:{jti}",
        gateway_node_id="gateway",
        probe_kind=request.kind,
        target_id=request.target_id,
        request_digest=request.canonical_digest(),
        issued_at=now - 1,
        expires_at=now + 60,
        jti=jti,
    )


def _signed_gateway_capability(
    private_key: Ed25519PrivateKey,
    grant: DelegatedGatewayProbeGrant,
) -> str:
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(
        {
            "iss": grant.issuer,
            "aud": grant.audience,
            "iat": grant.issued_at,
            "exp": grant.expires_at,
            "jti": grant.jti,
            "gateway_probe": grant.descriptor(),
        },
        private_pem,
        algorithm="EdDSA",
        headers={
            "kid": grant.key_id,
            "typ": "CPK-GATEWAY-PROBE+JWT",
        },
    )


@dataclass(frozen=True)
class _PublicHttpsResponse:
    status: int
    body: bytes


def _public_https_json(
    hostname: str,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    timeout: float,
) -> _PublicHttpsResponse:
    address = _public_ingress_address(hostname)
    return _https_request_to_address(
        address,
        hostname=hostname,
        method=method,
        path=path,
        body=body,
        headers={"Accept": "application/json"},
        timeout=timeout,
    )


def _https_request_to_address(
    address: str,
    *,
    hostname: str,
    method: str,
    path: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout: float,
) -> _PublicHttpsResponse:
    context = ssl.create_default_context()
    with socket.create_connection((address, 443), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=hostname) as tls:
            connection = http.client.HTTPSConnection(hostname, timeout=timeout)
            connection.sock = tls
            try:
                headers = {"Host": hostname, **headers}
                if body is not None:
                    headers["Content-Type"] = "application/json"
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                return _PublicHttpsResponse(response.status, response.read(16_384))
            finally:
                connection.close()


def _public_ingress_address(hostname: str) -> str:
    try:
        return socket.gethostbyname(hostname)
    except OSError:
        pass
    query = (
        f"/dns-query?name={hostname}&type=A"
    )
    response = _https_request_to_address(
        "1.1.1.1",
        hostname="cloudflare-dns.com",
        method="GET",
        path=query,
        body=None,
        headers={"Accept": "application/dns-json"},
        timeout=5,
    )
    payload = json.loads(response.body.decode("utf-8"))
    answers = payload.get("Answer")
    if not isinstance(answers, list):
        raise RuntimeError(f"public DNS did not expose A records for {hostname}")
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        data = answer.get("data")
        if isinstance(data, str) and _is_ipv4_address(data):
            return data
    raise RuntimeError(f"public DNS did not expose usable A records for {hostname}")


def _is_ipv4_address(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


def _retained_data_volumes(workspace_id: str, node_id: str) -> list[str]:
    volumes = []
    for volume in _docker_client().volumes.list(
        filters={
            "label": [
                f"org.openj92.cpk.workspace={workspace_id}",
                f"org.openj92.cpk.node={node_id}",
                "org.openj92.cpk.volume.kind=retained-data",
            ]
        }
    ):
        labels = volume.attrs.get("Labels") or {}
        if labels.get("org.openj92.cpk.workspace") == workspace_id and labels.get(
            "org.openj92.cpk.node"
        ) == node_id:
            volumes.append(volume.name)
    return sorted(volumes)


def _assert_retained_volumes_still_exist(volume_names: list[str]) -> None:
    client = _docker_client()
    missing = []
    for volume_name in volume_names:
        try:
            client.volumes.get(volume_name)
        except Exception:
            missing.append(volume_name)
    if missing:
        raise RuntimeError(f"retained postgres volumes were removed: {missing}")


def _assert_no_node_containers(workspace_id: str, node_id: str) -> None:
    containers = _docker_client().containers.list(
        all=True,
        filters={
            "label": [
                f"org.openj92.cpk.workspace={workspace_id}",
                f"org.openj92.cpk.node={node_id}",
            ]
        },
    )
    if containers:
        raise RuntimeError(
            "postgres compute container still exists after teardown: "
            + ", ".join(container.name for container in containers)
        )


def _assert_no_runtime_networks(workspace_id: str) -> None:
    networks = _docker_client().networks.list(
        filters={
            "label": [
                f"org.openj92.cpk.workspace={workspace_id}",
                "org.openj92.cpk.kind=runtime-network",
            ]
        }
    )
    if networks:
        raise RuntimeError(
            "postgres runtime network still exists after teardown: "
            + ", ".join(network.name for network in networks)
        )


def _assert_secret_absent_from_activity(workflow: HostedWorkflow, secret_value: str) -> None:
    encoded = json.dumps(workflow.read_activity(limit=200), sort_keys=True)
    if secret_value in encoded:
        raise RuntimeError("postgres secret value leaked into activity readback")


def _single_docker_container(workspace_id: str, node_id: str):
    containers = _docker_client().containers.list(
        all=True,
        filters={
            "label": [
                f"org.openj92.cpk.workspace={workspace_id}",
                f"org.openj92.cpk.node={node_id}",
            ]
        },
    )
    if len(containers) != 1:
        raise RuntimeError(
            f"expected one owned container for {workspace_id}/{node_id}, found "
            f"{len(containers)}"
        )
    return containers[0]


def _docker_client():
    import docker

    return docker.from_env()


def _clock() -> str:
    return "2026-07-22T10:00:00Z"


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
