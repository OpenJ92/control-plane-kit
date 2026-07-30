"""Source-live cpk-server acceptance against a real durable secrets provider."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import docker
import psycopg

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.products import (
    ProductInstanceConfiguration,
    instantiate_product,
)
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.topology import DeploymentGraph, compile_topology
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.verification import VerificationPolicy

from cpk_server_hosted_activity import (
    AUTHORIZATION,
    LOCAL_DOCKER_AUTHORITY_REF,
    HostedWorkflow,
    _assert_activity_mentions,
    _assert_no_node_containers,
    _assert_runtime_activity_mentions,
    _bootstrap_workspace,
    _clock,
    _disconnect_runtime_networks,
    _http,
    _mcp_read,
    _mcp_tool,
    _product_document,
    _sync_runtime_networks,
)


PROVIDER_ID = "control-plane-kit"
PROVIDER_ENDPOINT_REFERENCE = "source-live-secrets"
PROVIDER_CREDENTIAL_REFERENCE = "secret://bootstrap/provider/client-token"
WRONG_PROVIDER_CREDENTIAL_REFERENCE = "secret://bootstrap/provider/wrong-token"
POSTGRES_PASSWORD_REFERENCE = "secret://control-plane-kit/postgres/password"
POSTGRES_INTENT = "postgres.password"
APPLICATION_TOKEN_INTENT = "application.control-token"
WORKER_AUTHORIZATION = "Bearer worker-present"
NO_SECRET_WORKER_AUTHORIZATION = "Bearer worker-no-secret"
SUCCESS_WORKSPACE = "workspace-secret-provider-live"
REVOKED_BEFORE_USE_WORKSPACE = "workspace-secret-revoked-before-use"
CONCURRENT_WORKSPACES = (
    "workspace-secret-concurrent-a",
    "workspace-secret-concurrent-b",
    "workspace-secret-concurrent-c",
)
PROVIDER_BASE_URL = "http://cpk-secrets:8081"
CONTAINER_RESTART_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class PreparedRun:
    run_id: str
    plan_id: str
    current_graph_id: str
    desired_graph_id: str


def main() -> int:
    base_url = _required_env("CPK_HOSTED_ACTIVITY_BASE_URL").rstrip("/")
    server_container = _required_env("CPK_HOSTED_ACTIVITY_SERVER_CONTAINER")
    provider_container = _required_env("CPK_SECRET_PROVIDER_CONTAINER")
    servers_repo = Path(_required_env("CPK_HOSTED_ACTIVITY_SERVERS_REPO"))
    operations_database_url = _required_env("CPK_OPERATIONS_DATABASE_URL")
    provider_token_file = Path(_required_env("CPK_SECRET_PROVIDER_TOKEN_FILE"))
    bootstrap_dir = Path(_required_env("CPK_SECRET_PROVIDER_BOOTSTRAP_DIR"))

    cpk_server_document = _product_document(servers_repo, "cpk_server")
    postgres_document = _product_document(servers_repo, "postgres_server")
    success = _workflow(
        base_url,
        server_container,
        workspace_id=SUCCESS_WORKSPACE,
        worker_id="hosted-worker",
        worker_authorization=WORKER_AUTHORIZATION,
    )
    success.wait_ready()
    current_graph_id = _bootstrap_workspace(
        success,
        name="Secret provider source-live success",
        product_documents={"postgres": postgres_document},
        register_runtime_authority=True,
        register_runtime_delivery=False,
    )
    _register_provider_and_reference(success)
    _assert_provider_metadata_is_secret_free(success)
    _provider_write_secret(
        workspace_id=SUCCESS_WORKSPACE,
        value_file=bootstrap_dir / "postgres-password",
        provider_token_file=provider_token_file,
        correlation_id="source-live-bootstrap-write-a",
    )

    _restart_provider(provider_container)
    _restart_cpk_server(
        server_container,
        success,
        ready_policy=_verification_policy(cpk_server_document, "ready"),
    )
    deployed_a = success.run_approved_transition(
        title="Secret provider source-live deploy A",
        graph=_postgres_graph(
            postgres_document,
            SUCCESS_WORKSPACE,
            node_ids=("postgres-a",),
        ),
        current_graph_id=current_graph_id,
    )
    _assert_activity_mentions(success, deployed_a.run_id, "postgres-a")
    _assert_provider_and_operations_correlation(
        provider_container=provider_container,
        operations_database_url=operations_database_url,
        workspace_id=SUCCESS_WORKSPACE,
    )
    first_resolution = _latest_successful_resolution(
        provider_container,
        SUCCESS_WORKSPACE,
    )
    version_a = first_resolution["version_id"]

    version_b = _provider_rotate_secret(
        workspace_id=SUCCESS_WORKSPACE,
        value_file=bootstrap_dir / "postgres-password-v2",
        provider_token_file=provider_token_file,
        correlation_id="source-live-rotate-b",
    )
    if version_b == version_a:
        raise RuntimeError("secret rotation did not create a new version")
    replayed_version = _provider_resolve_metadata(
        workspace_id=SUCCESS_WORKSPACE,
        provider_token_file=provider_token_file,
        caller_subject=first_resolution["caller_subject"],
        correlation_id=first_resolution["correlation_id"],
    )
    if replayed_version != version_a:
        raise RuntimeError("exact provider correlation did not remain pinned")

    deployed_b = success.run_approved_transition(
        title="Secret provider source-live deploy B",
        graph=_postgres_graph(
            postgres_document,
            SUCCESS_WORKSPACE,
            node_ids=("postgres-a", "postgres-b"),
        ),
        current_graph_id=deployed_a.current_graph_id,
        expected_desired_graph_id=deployed_a.desired_graph_id,
    )
    _assert_activity_mentions(success, deployed_b.run_id, "postgres-b")
    _assert_run_resolved_version(
        provider_container=provider_container,
        operations_database_url=operations_database_url,
        workspace_id=SUCCESS_WORKSPACE,
        run_id=deployed_b.run_id,
        expected_version_id=version_b,
    )
    _assert_provider_version_history(
        provider_container,
        workspace_id=SUCCESS_WORKSPACE,
        expected_versions={version_a, version_b},
    )
    _assert_activity_is_secret_free(success)
    _disconnect_runtime_networks(
        success.server_container,
        workspace_id=SUCCESS_WORKSPACE,
    )
    removed = success.run_approved_transition(
        title="Secret provider source-live teardown",
        graph=DeploymentGraph(SUCCESS_WORKSPACE),
        current_graph_id=deployed_b.current_graph_id,
        expected_desired_graph_id=deployed_b.desired_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(success, removed.run_id, "postgres-a")
    _assert_activity_mentions(success, removed.run_id, "postgres-b")
    _assert_no_node_containers(SUCCESS_WORKSPACE, "postgres-a")
    _assert_no_node_containers(SUCCESS_WORKSPACE, "postgres-b")

    _assert_resolution_revoke_race(
        provider_container=provider_container,
        operations_database_url=operations_database_url,
        workspace_id=SUCCESS_WORKSPACE,
        provider_token_file=provider_token_file,
        first_resolution=first_resolution,
    )
    _run_revoked_before_use(
        base_url=base_url,
        server_container=server_container,
        provider_container=provider_container,
        postgres_document=postgres_document,
        provider_token_file=provider_token_file,
        value_file=bootstrap_dir / "postgres-revoked",
    )
    _run_concurrent_workspace_uses(
        base_url=base_url,
        server_container=server_container,
        provider_container=provider_container,
        postgres_document=postgres_document,
        operations_database_url=operations_database_url,
        provider_token_file=provider_token_file,
        bootstrap_dir=bootstrap_dir,
    )

    _run_denial_matrix(
        base_url=base_url,
        server_container=server_container,
        provider_container=provider_container,
        postgres_document=postgres_document,
    )
    print("cpk-server durable secret-provider source-live acceptance passed")
    return 0


def _run_denial_matrix(
    *,
    base_url: str,
    server_container: str,
    provider_container: str,
    postgres_document: Any,
) -> None:
    _run_denied_case(
        base_url=base_url,
        server_container=server_container,
        provider_container=provider_container,
        postgres_document=postgres_document,
        workspace_id="workspace-secret-denied-scope",
        worker_id="hosted-worker-no-secret",
        worker_authorization=NO_SECRET_WORKER_AUTHORIZATION,
        expected_provider_io=False,
    )

    source = _workflow(
        base_url,
        server_container,
        workspace_id="workspace-secret-wrong-source",
        worker_id="hosted-worker",
        worker_authorization=WORKER_AUTHORIZATION,
    )
    _bootstrap_workspace(
        source,
        name="Wrong workspace source",
        product_documents={},
        register_runtime_authority=False,
        register_runtime_delivery=False,
    )
    _register_provider_and_reference(source)
    _run_denied_case(
        base_url=base_url,
        server_container=server_container,
        provider_container=provider_container,
        postgres_document=postgres_document,
        workspace_id="workspace-secret-wrong-target",
        worker_id="hosted-worker",
        worker_authorization=WORKER_AUTHORIZATION,
        register_provider=False,
        expected_provider_io=False,
    )

    _run_denied_case(
        base_url=base_url,
        server_container=server_container,
        provider_container=provider_container,
        postgres_document=postgres_document,
        workspace_id="workspace-secret-wrong-intent",
        worker_id="hosted-worker",
        worker_authorization=WORKER_AUTHORIZATION,
        reference_intents=(APPLICATION_TOKEN_INTENT,),
        provider_intents=(APPLICATION_TOKEN_INTENT, POSTGRES_INTENT),
        expected_provider_io=False,
    )
    _run_denied_case(
        base_url=base_url,
        server_container=server_container,
        provider_container=provider_container,
        postgres_document=postgres_document,
        workspace_id="workspace-secret-revoked-provider",
        worker_id="hosted-worker",
        worker_authorization=WORKER_AUTHORIZATION,
        revoke_provider=True,
        expected_provider_io=False,
    )
    _run_denied_case(
        base_url=base_url,
        server_container=server_container,
        provider_container=provider_container,
        postgres_document=postgres_document,
        workspace_id="workspace-secret-revoked-reference",
        worker_id="hosted-worker",
        worker_authorization=WORKER_AUTHORIZATION,
        revoke_reference=True,
        expected_provider_io=False,
    )
    _run_denied_case(
        base_url=base_url,
        server_container=server_container,
        provider_container=provider_container,
        postgres_document=postgres_document,
        workspace_id="workspace-secret-missing",
        worker_id="hosted-worker",
        worker_authorization=WORKER_AUTHORIZATION,
        expected_provider_io=True,
    )
    _run_denied_case(
        base_url=base_url,
        server_container=server_container,
        provider_container=provider_container,
        postgres_document=postgres_document,
        workspace_id="workspace-secret-wrong-credential",
        worker_id="hosted-worker",
        worker_authorization=WORKER_AUTHORIZATION,
        credential_reference=WRONG_PROVIDER_CREDENTIAL_REFERENCE,
        expected_provider_io=True,
    )
    _run_denied_case(
        base_url=base_url,
        server_container=server_container,
        provider_container=provider_container,
        postgres_document=postgres_document,
        workspace_id="workspace-secret-unavailable",
        worker_id="hosted-worker",
        worker_authorization=WORKER_AUTHORIZATION,
        stop_provider=True,
        expected_provider_io=False,
    )


def _run_revoked_before_use(
    *,
    base_url: str,
    server_container: str,
    provider_container: str,
    postgres_document: Any,
    provider_token_file: Path,
    value_file: Path,
) -> None:
    workspace_id = REVOKED_BEFORE_USE_WORKSPACE
    workflow = _workflow(
        base_url,
        server_container,
        workspace_id=workspace_id,
        worker_id="hosted-worker",
        worker_authorization=WORKER_AUTHORIZATION,
    )
    current_graph_id = _bootstrap_workspace(
        workflow,
        name="Secret provider revoked before use",
        product_documents={"postgres": postgres_document},
        register_runtime_authority=True,
        register_runtime_delivery=False,
    )
    _register_provider_and_reference(workflow)
    _provider_write_secret(
        workspace_id=workspace_id,
        value_file=value_file,
        provider_token_file=provider_token_file,
        correlation_id=f"{workspace_id}:write",
    )
    runtime_only = workflow.run_approved_transition(
        title="Create empty runtime before revoked use",
        graph=_postgres_graph(postgres_document, workspace_id, node_ids=()),
        current_graph_id=current_graph_id,
    )
    before = _workspace_runtime_resources(workspace_id)
    _provider_revoke_secret(
        workspace_id=workspace_id,
        provider_token_file=provider_token_file,
        correlation_id=f"{workspace_id}:revoke",
    )
    prepared = _prepare_run(
        workflow,
        title="Reject revoked secret before Postgres mutation",
        graph=_postgres_graph(postgres_document, workspace_id),
        current_graph_id=runtime_only.current_graph_id,
        expected_desired_graph_id=runtime_only.desired_graph_id,
    )
    terminal = _execute_until_terminal(workflow, prepared.run_id)
    if terminal.get("coordinator_status") not in {
        "failed",
        "unsupported",
        "uncertain",
        "blocked",
    }:
        raise RuntimeError("revoked provider secret did not stop node realization")
    after = _workspace_runtime_resources(workspace_id)
    if after != before:
        raise RuntimeError("revoked secret use mutated Docker resources")
    _assert_no_node_containers(workspace_id, "postgres")
    rows = _provider_audit_rows(provider_container, workspace_id)
    if not any(
        row["outcome"] == "revoked" and row["intent"] == POSTGRES_INTENT
        for row in rows
    ):
        raise RuntimeError("revoked use did not produce bounded provider audit evidence")
    _disconnect_runtime_networks(server_container, workspace_id=workspace_id)
    cleaned = workflow.run_approved_transition(
        title="Remove empty runtime after revoked use",
        graph=DeploymentGraph(workspace_id),
        current_graph_id=runtime_only.current_graph_id,
        expected_desired_graph_id=prepared.desired_graph_id,
        sync_runtime_networks=False,
    )
    _assert_runtime_activity_mentions(workflow, cleaned.run_id, "docker")


def _run_concurrent_workspace_uses(
    *,
    base_url: str,
    server_container: str,
    provider_container: str,
    postgres_document: Any,
    operations_database_url: str,
    provider_token_file: Path,
    bootstrap_dir: Path,
) -> None:
    workflows: dict[str, HostedWorkflow] = {}
    current_graph_ids: dict[str, str] = {}
    for index, workspace_id in enumerate(CONCURRENT_WORKSPACES, start=1):
        workflow = _workflow(
            base_url,
            server_container,
            workspace_id=workspace_id,
            worker_id="hosted-worker",
            worker_authorization=WORKER_AUTHORIZATION,
        )
        workflows[workspace_id] = workflow
        current_graph_ids[workspace_id] = _bootstrap_workspace(
            workflow,
            name=f"Concurrent secret workspace {index}",
            product_documents={"postgres": postgres_document},
            register_runtime_authority=True,
            register_runtime_delivery=False,
        )
        _register_provider_and_reference(workflow)
        _provider_write_secret(
            workspace_id=workspace_id,
            value_file=bootstrap_dir / f"postgres-concurrent-{index}",
            provider_token_file=provider_token_file,
            correlation_id=f"{workspace_id}:write",
        )

    def deploy(workspace_id: str):
        return workflows[workspace_id].run_approved_transition(
            title=f"Concurrent provider deployment {workspace_id}",
            graph=_postgres_graph(postgres_document, workspace_id),
            current_graph_id=current_graph_ids[workspace_id],
        )

    with ThreadPoolExecutor(max_workers=len(CONCURRENT_WORKSPACES)) as executor:
        futures = {
            workspace_id: executor.submit(deploy, workspace_id)
            for workspace_id in CONCURRENT_WORKSPACES
        }
        deployed = {
            workspace_id: future.result()
            for workspace_id, future in futures.items()
        }

    versions: set[str] = set()
    for workspace_id, transition in deployed.items():
        _assert_activity_mentions(
            workflows[workspace_id],
            transition.run_id,
            "postgres",
        )
        _assert_provider_and_operations_correlation(
            provider_container=provider_container,
            operations_database_url=operations_database_url,
            workspace_id=workspace_id,
        )
        resolved = _latest_successful_resolution(provider_container, workspace_id)
        versions.add(resolved["version_id"])
    if len(versions) != len(CONCURRENT_WORKSPACES):
        raise RuntimeError("concurrent workspaces did not preserve version isolation")

    for workspace_id, transition in deployed.items():
        workflow = workflows[workspace_id]
        _disconnect_runtime_networks(server_container, workspace_id=workspace_id)
        removed = workflow.run_approved_transition(
            title=f"Concurrent provider teardown {workspace_id}",
            graph=DeploymentGraph(workspace_id),
            current_graph_id=transition.current_graph_id,
            expected_desired_graph_id=transition.desired_graph_id,
            sync_runtime_networks=False,
        )
        _assert_activity_mentions(workflow, removed.run_id, "postgres")
        _assert_no_node_containers(workspace_id, "postgres")
        _assert_activity_is_secret_free(workflow)


def _assert_resolution_revoke_race(
    *,
    provider_container: str,
    operations_database_url: str,
    workspace_id: str,
    provider_token_file: Path,
    first_resolution: dict[str, str],
) -> None:
    operations_correlations = _operations_correlations(
        operations_database_url,
        workspace_id=workspace_id,
    )
    correlation_id = first_resolution["correlation_id"]
    if correlation_id not in operations_correlations:
        raise RuntimeError("race correlation was not admitted by operations")
    barrier = threading.Barrier(2)

    def replay() -> tuple[int, dict[str, Any]]:
        barrier.wait()
        return _provider_request(
            method="POST",
            path=_provider_secret_path(workspace_id, suffix="/resolve"),
            provider_token_file=provider_token_file,
            payload={
                "intent": POSTGRES_INTENT,
                "caller_subject": first_resolution["caller_subject"],
                "correlation_id": correlation_id,
            },
        )

    def revoke() -> tuple[int, dict[str, Any]]:
        barrier.wait()
        return _provider_request(
            method="POST",
            path=_provider_secret_path(workspace_id, suffix="/revoke"),
            provider_token_file=provider_token_file,
            payload={
                "caller_subject": "source-live-race",
                "correlation_id": f"{workspace_id}:race-revoke",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        replay_future = executor.submit(replay)
        revoke_future = executor.submit(revoke)
        replay_status, replay_payload = replay_future.result()
        revoke_status, _ = revoke_future.result()

    if revoke_status != 200:
        raise RuntimeError("provider revoke race did not complete")
    if replay_status == 200:
        metadata = replay_payload.get("metadata", {})
        if metadata.get("version_id") != first_resolution["version_id"]:
            raise RuntimeError("winning replay changed its pinned version")
    elif replay_status == 409:
        detail = replay_payload.get("detail", {})
        if detail.get("code") != "secret-revoked":
            raise RuntimeError("losing replay did not fail as revoked")
    else:
        raise RuntimeError("provider resolve/revoke race had an invalid outcome")
    rows = _provider_audit_rows(provider_container, workspace_id)
    matching = [row for row in rows if row["correlation_id"] == correlation_id]
    if not matching or matching[-1]["outcome"] not in {"resolved", "revoked"}:
        raise RuntimeError("provider race audit did not preserve transaction outcome")


def _run_denied_case(
    *,
    base_url: str,
    server_container: str,
    provider_container: str,
    postgres_document: Any,
    workspace_id: str,
    worker_id: str,
    worker_authorization: str,
    register_provider: bool = True,
    provider_intents: tuple[str, ...] = (POSTGRES_INTENT,),
    reference_intents: tuple[str, ...] = (POSTGRES_INTENT,),
    credential_reference: str = PROVIDER_CREDENTIAL_REFERENCE,
    revoke_provider: bool = False,
    revoke_reference: bool = False,
    stop_provider: bool = False,
    expected_provider_io: bool,
) -> None:
    workflow = _workflow(
        base_url,
        server_container,
        workspace_id=workspace_id,
        worker_id=worker_id,
        worker_authorization=worker_authorization,
    )
    current_graph_id = _bootstrap_workspace(
        workflow,
        name=f"Secret provider denied case {workspace_id}",
        product_documents={"postgres": postgres_document},
        register_runtime_authority=True,
        register_runtime_delivery=False,
    )
    provider_registration_id = None
    reference_registration_id = None
    if register_provider:
        provider_registration_id, reference_registration_id = (
            _register_provider_and_reference(
                workflow,
                provider_intents=provider_intents,
                reference_intents=reference_intents,
                credential_reference=credential_reference,
            )
        )
    if revoke_provider:
        if provider_registration_id is None:
            raise RuntimeError("provider revocation requires a registration")
        _revoke_provider(workflow)
    if revoke_reference:
        if reference_registration_id is None:
            raise RuntimeError("reference revocation requires a registration")
        _revoke_reference(workflow, reference_registration_id)

    audit_before = _provider_audit_count(provider_container, workspace_id)
    if stop_provider:
        docker.from_env().containers.get(provider_container).stop(timeout=10)
    try:
        prepared = _prepare_run(
            workflow,
            title=f"Denied secret use {workspace_id}",
            graph=_postgres_graph(postgres_document, workspace_id),
            current_graph_id=current_graph_id,
        )
        terminal = _execute_until_terminal(workflow, prepared.run_id)
        if terminal.get("coordinator_status") not in {
            "failed",
            "unsupported",
            "uncertain",
            "blocked",
        }:
            raise RuntimeError(
                f"secret denial did not stop execution for {workspace_id}: {terminal}"
            )
    finally:
        if stop_provider:
            docker.from_env().containers.get(provider_container).start()
            _wait_provider_ready()

    _assert_no_node_containers(workspace_id, "postgres")
    audit_after = _provider_audit_count(provider_container, workspace_id)
    if expected_provider_io:
        if audit_after <= audit_before and credential_reference == PROVIDER_CREDENTIAL_REFERENCE:
            raise RuntimeError(
                f"expected bounded provider IO was not audited for {workspace_id}"
            )
    elif audit_after != audit_before:
        raise RuntimeError(
            f"denied secret use reached provider IO for {workspace_id}"
        )


def _workflow(
    base_url: str,
    server_container: str,
    *,
    workspace_id: str,
    worker_id: str,
    worker_authorization: str,
) -> HostedWorkflow:
    return HostedWorkflow(
        base_url,
        workspace_id=workspace_id,
        worker_id=worker_id,
        server_container=server_container,
        worker_authorization=worker_authorization,
    )


def _register_provider_and_reference(
    workflow: HostedWorkflow,
    *,
    provider_intents: tuple[str, ...] = (POSTGRES_INTENT,),
    reference_intents: tuple[str, ...] = (POSTGRES_INTENT,),
    credential_reference: str = PROVIDER_CREDENTIAL_REFERENCE,
) -> tuple[str, str]:
    provider = _http(
        workflow.base_url,
        "POST",
        f"/workspaces/{workflow.workspace_id}/secret-providers",
        {
            "provider_id": PROVIDER_ID,
            "provider_kind": "control-plane-kit-secrets",
            "display_name": "Source-live durable secrets",
            "endpoint_reference": PROVIDER_ENDPOINT_REFERENCE,
            "credential_reference": credential_reference,
            "allowed_reference_prefixes": [POSTGRES_PASSWORD_REFERENCE],
            "allowed_intents": list(provider_intents),
            "admitted_at": _clock(),
            "metadata": {"acceptance": "source-live"},
            "idempotency_key": f"{workflow.workspace_id}:secret-provider",
        },
    )
    provider_registration_id = str(provider["registration_id"])
    reference = _http(
        workflow.base_url,
        "POST",
        f"/workspaces/{workflow.workspace_id}/secret-references",
        {
            "reference": POSTGRES_PASSWORD_REFERENCE,
            "provider_registration_id": provider_registration_id,
            "allowed_intents": list(reference_intents),
            "admitted_at": _clock(),
            "metadata": {"acceptance": "source-live"},
            "idempotency_key": f"{workflow.workspace_id}:secret-reference",
        },
    )
    return provider_registration_id, str(reference["registration_id"])


def _revoke_provider(workflow: HostedWorkflow) -> None:
    _http(
        workflow.base_url,
        "POST",
        f"/workspaces/{workflow.workspace_id}/secret-providers/{PROVIDER_ID}/revoke",
        {
            "revoked_at": _clock(),
            "idempotency_key": f"{workflow.workspace_id}:secret-provider:revoke",
        },
    )


def _revoke_reference(workflow: HostedWorkflow, registration_id: str) -> None:
    _http(
        workflow.base_url,
        "POST",
        (
            f"/workspaces/{workflow.workspace_id}/secret-references/"
            f"{registration_id}/revoke"
        ),
        {
            "revoked_at": _clock(),
            "idempotency_key": f"{workflow.workspace_id}:secret-reference:revoke",
        },
    )


def _assert_provider_metadata_is_secret_free(workflow: HostedWorkflow) -> None:
    providers = _mcp_read(
        workflow.base_url,
        "read.secret-providers",
        {"workspace_id": workflow.workspace_id, "limit": 10, "offset": 0},
    )
    references = _mcp_read(
        workflow.base_url,
        "read.secret-references",
        {"workspace_id": workflow.workspace_id, "limit": 10, "offset": 0},
    )
    rendered = json.dumps(
        {"providers": providers, "references": references},
        separators=(",", ":"),
        sort_keys=True,
    ).lower()
    for forbidden_key in (
        "value_base64",
        "ciphertext",
        "private_key",
        "access_token",
    ):
        if forbidden_key in rendered:
            raise RuntimeError("secret provider metadata exposed secret material")


def _postgres_graph(
    postgres_document: Any,
    workspace_id: str,
    *,
    node_ids: tuple[str, ...] = ("postgres",),
) -> DeploymentGraph:
    product = postgres_document.product
    postgres_nodes = tuple(
        instantiate_product(
            product,
            node_id,
            ProductInstanceConfiguration.from_contract(product.runtime_contract),
        )
        for node_id in node_ids
    )
    return compile_topology(
        DeploymentTopology(
            workspace_id,
            DockerRuntime(
                runtime_id="docker",
                network_name=f"control-plane-kit-{workspace_id}-docker",
                authority_ref=RuntimeAuthorityReference(LOCAL_DOCKER_AUTHORITY_REF),
                children=postgres_nodes,
            ),
        )
    )


def _prepare_run(
    workflow: HostedWorkflow,
    *,
    title: str,
    graph: DeploymentGraph,
    current_graph_id: str,
    expected_desired_graph_id: str | None = None,
) -> PreparedRun:
    session_id = workflow.start_session(title)
    desired_graph_id = workflow.set_desired_graph(
        session_id=session_id,
        graph=graph,
        title=title,
        expected_desired_graph_id=expected_desired_graph_id,
    )
    plan_id = workflow.plan_transition(
        session_id=session_id,
        title=title,
        current_graph_id=current_graph_id,
        desired_graph_id=desired_graph_id,
    )
    approval = workflow.request_approval(
        session_id=session_id,
        title=title,
        plan_id=plan_id,
    )
    approval_id = str(approval["request_id"])
    workflow.assert_approval_visible(approval_id, plan_id)
    workflow.approve(session_id=session_id, title=title, approval=approval)
    request_id = workflow.admit(
        session_id=session_id,
        title=title,
        plan_id=plan_id,
        approval_id=approval_id,
    )
    run_id = workflow.claim(title=title, request_id=request_id)
    workflow.start_run(title=title, run_id=run_id)
    return PreparedRun(
        run_id=run_id,
        plan_id=plan_id,
        current_graph_id=current_graph_id,
        desired_graph_id=desired_graph_id,
    )


def _execute_until_terminal(
    workflow: HostedWorkflow,
    run_id: str,
) -> dict[str, Any]:
    for attempt in range(40):
        _sync_runtime_networks(
            workflow.server_container,
            workspace_id=workflow.workspace_id,
        )
        result = _mcp_tool(
            workflow.base_url,
            "command.deployment.execute",
            {
                "workspace_id": workflow.workspace_id,
                "run_id": run_id,
                "worker_id": workflow.worker_id,
                "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
                "idempotency_key": (
                    f"{workflow.workspace_id}:denied-execute:{attempt}"
                ),
                "max_effects": 1,
            },
            timeout=60,
            authorization=workflow.worker_authorization,
        )
        if result["coordinator_status"] in {
            "completed",
            "failed",
            "unsupported",
            "uncertain",
            "blocked",
        }:
            return result
    raise RuntimeError("denied source-live run did not reach a terminal state")


def _restart_provider(container_id: str) -> None:
    _restart_container(container_id)
    _wait_provider_ready()


def _restart_cpk_server(
    container_id: str,
    workflow: HostedWorkflow,
    *,
    ready_policy: VerificationPolicy,
) -> None:
    _restart_container(container_id)
    workflow.wait_ready(policy=ready_policy)
    if workflow.read_current_graph_id() == "":
        raise RuntimeError("cpk-server restart did not restore workspace state")


def _restart_container(container_id: str) -> None:
    container = docker.from_env().containers.get(container_id)
    container.reload()
    previous_started_at = str(container.attrs["State"]["StartedAt"])
    container.stop(timeout=10)
    container.reload()
    if container.attrs["State"]["Running"]:
        raise RuntimeError("container did not stop before restart")
    container.start()
    deadline = time.monotonic() + CONTAINER_RESTART_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        container.reload()
        state = container.attrs["State"]
        if state["Running"] and str(state["StartedAt"]) != previous_started_at:
            return
        time.sleep(0.25)
    raise RuntimeError("container did not cross the requested restart boundary")


def _verification_policy(
    product_document: Any,
    check_id: str,
) -> VerificationPolicy:
    for check in product_document.product.runtime_contract.verification.checks:
        if check.check_id == check_id:
            return check.policy
    raise RuntimeError(f"product verification check is missing: {check_id}")


def _wait_provider_ready() -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urlopen("http://cpk-secrets:8081/health/ready", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("secret provider did not become ready")


def _provider_write_secret(
    *,
    workspace_id: str,
    value_file: Path,
    provider_token_file: Path,
    correlation_id: str,
) -> str:
    status, payload = _provider_request(
        method="POST",
        path=_provider_secret_path(workspace_id),
        provider_token_file=provider_token_file,
        payload={
            "value_base64": base64.b64encode(value_file.read_bytes()).decode("ascii"),
            "labels": {"intent": POSTGRES_INTENT},
            "caller_subject": "source-live-bootstrap",
            "correlation_id": correlation_id,
        },
    )
    if status != 200 or payload.get("outcome") != "stored":
        raise RuntimeError("provider fixture write failed")
    return _metadata_version(payload)


def _provider_rotate_secret(
    *,
    workspace_id: str,
    value_file: Path,
    provider_token_file: Path,
    correlation_id: str,
) -> str:
    status, payload = _provider_request(
        method="POST",
        path=_provider_secret_path(workspace_id, suffix="/rotate"),
        provider_token_file=provider_token_file,
        payload={
            "value_base64": base64.b64encode(value_file.read_bytes()).decode("ascii"),
            "labels": {"intent": POSTGRES_INTENT},
            "caller_subject": "source-live-rotation",
            "correlation_id": correlation_id,
        },
    )
    if status != 200 or payload.get("outcome") != "rotated":
        raise RuntimeError("provider fixture rotation failed")
    return _metadata_version(payload)


def _provider_revoke_secret(
    *,
    workspace_id: str,
    provider_token_file: Path,
    correlation_id: str,
) -> None:
    status, payload = _provider_request(
        method="POST",
        path=_provider_secret_path(workspace_id, suffix="/revoke"),
        provider_token_file=provider_token_file,
        payload={
            "caller_subject": "source-live-revocation",
            "correlation_id": correlation_id,
        },
    )
    if status != 200 or payload.get("outcome") != "revoked":
        raise RuntimeError("provider fixture revocation failed")


def _provider_resolve_metadata(
    *,
    workspace_id: str,
    provider_token_file: Path,
    caller_subject: str,
    correlation_id: str,
) -> str:
    status, payload = _provider_request(
        method="POST",
        path=_provider_secret_path(workspace_id, suffix="/resolve"),
        provider_token_file=provider_token_file,
        payload={
            "intent": POSTGRES_INTENT,
            "caller_subject": caller_subject,
            "correlation_id": correlation_id,
        },
    )
    if status != 200 or payload.get("outcome") != "resolved":
        raise RuntimeError("provider correlation replay failed")
    payload.pop("value_base64", None)
    return _metadata_version(payload)


def _provider_request(
    *,
    method: str,
    path: str,
    provider_token_file: Path,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    request = Request(
        f"{PROVIDER_BASE_URL}{path}",
        method=method,
        headers={
            "Authorization": (
                f"Bearer {provider_token_file.read_text(encoding='utf-8').strip()}"
            ),
            "Content-Type": "application/json",
        },
        data=json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        ),
    )
    try:
        with urlopen(request, timeout=20) as response:
            status = response.status
            decoded = json.loads(response.read())
    except HTTPError as error:
        status = error.code
        decoded = json.loads(error.read())
    if not isinstance(decoded, dict):
        raise RuntimeError("provider response was malformed")
    return status, decoded


def _provider_secret_path(workspace_id: str, *, suffix: str = "") -> str:
    encoded = base64.urlsafe_b64encode(POSTGRES_PASSWORD_REFERENCE.encode("utf-8"))
    secret_id = f"cpk1_{encoded.rstrip(b'=').decode('ascii')}"
    return f"/v1/workspaces/{workspace_id}/secrets/{secret_id}{suffix}"


def _metadata_version(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError("provider response omitted secret metadata")
    version_id = metadata.get("version_id")
    if not isinstance(version_id, str) or not version_id:
        raise RuntimeError("provider response omitted secret version identity")
    return version_id


def _workspace_runtime_resources(workspace_id: str) -> tuple[tuple[str, ...], ...]:
    client = docker.from_env()
    label = f"org.openj92.cpk.workspace={workspace_id}"
    return (
        tuple(
            sorted(
                container.id
                for container in client.containers.list(
                    all=True,
                    filters={"label": label},
                )
            )
        ),
        tuple(
            sorted(
                network.id
                for network in client.networks.list(filters={"label": label})
            )
        ),
        tuple(
            sorted(
                volume.id
                for volume in client.volumes.list(filters={"label": label})
            )
        ),
    )


def _provider_audit_rows(
    provider_container: str,
    workspace_id: str,
) -> list[dict[str, str]]:
    script = (
        "import json,sqlite3,sys;"
        "c=sqlite3.connect('/var/lib/cpk-secrets/secrets.sqlite3');"
        "rows=c.execute("
        "\"SELECT correlation_id,outcome,intent,caller_subject,"
        "version_id,secret_id,code "
        "FROM audit_records WHERE workspace_id=? ORDER BY rowid\","
        "(sys.argv[1],)).fetchall();"
        "print(json.dumps([dict(zip("
        "('correlation_id','outcome','intent','caller_subject',"
        "'version_id','secret_id','code'),r)) for r in rows]))"
    )
    result = docker.from_env().containers.get(provider_container).exec_run(
        ["python", "-c", script, workspace_id]
    )
    if result.exit_code != 0:
        raise RuntimeError("provider audit evidence was unavailable")
    decoded = json.loads(result.output.decode("utf-8"))
    if not isinstance(decoded, list):
        raise RuntimeError("provider audit evidence was malformed")
    return decoded


def _provider_audit_count(provider_container: str, workspace_id: str) -> int:
    return len(_provider_audit_rows(provider_container, workspace_id))


def _latest_successful_resolution(
    provider_container: str,
    workspace_id: str,
) -> dict[str, str]:
    rows = [
        row
        for row in _provider_audit_rows(provider_container, workspace_id)
        if row["outcome"] == "resolved"
        and row["intent"] == POSTGRES_INTENT
        and row["version_id"]
    ]
    if not rows:
        raise RuntimeError("provider had no successful Postgres resolution")
    return rows[-1]


def _assert_provider_version_history(
    provider_container: str,
    *,
    workspace_id: str,
    expected_versions: set[str],
) -> None:
    script = (
        "import json,sqlite3,sys;"
        "c=sqlite3.connect('/var/lib/cpk-secrets/secrets.sqlite3');"
        "rows=c.execute("
        "\"SELECT version_id,status FROM secret_versions "
        "WHERE workspace_id=? ORDER BY version_number\","
        "(sys.argv[1],)).fetchall();"
        "print(json.dumps(rows))"
    )
    result = docker.from_env().containers.get(provider_container).exec_run(
        ["python", "-c", script, workspace_id]
    )
    if result.exit_code != 0:
        raise RuntimeError("provider version history was unavailable")
    rows = json.loads(result.output.decode("utf-8"))
    versions = {str(row[0]) for row in rows}
    if not expected_versions.issubset(versions):
        raise RuntimeError("provider did not retain rotated version history")


def _operations_correlations(
    operations_database_url: str,
    *,
    workspace_id: str,
    run_id: str | None = None,
) -> set[str]:
    query = """
        SELECT correlation_id
        FROM cpk_secret_use_authorizations
        WHERE workspace_id = %s AND use_intent = %s
    """
    parameters: list[str] = [workspace_id, POSTGRES_INTENT]
    if run_id is not None:
        query += " AND run_id = %s"
        parameters.append(run_id)
    with psycopg.connect(operations_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, tuple(parameters))
            return {str(row[0]) for row in cursor.fetchall()}


def _assert_provider_and_operations_correlation(
    *,
    provider_container: str,
    operations_database_url: str,
    workspace_id: str,
) -> None:
    provider_rows = [
        row
        for row in _provider_audit_rows(provider_container, workspace_id)
        if row["outcome"] == "resolved" and row["intent"] == POSTGRES_INTENT
    ]
    if not provider_rows:
        raise RuntimeError("provider did not audit successful Postgres resolution")
    operations_correlations = _operations_correlations(
        operations_database_url,
        workspace_id=workspace_id,
    )
    provider_correlations = {row["correlation_id"] for row in provider_rows}
    if not provider_correlations.issubset(operations_correlations):
        raise RuntimeError("operations/provider secret-use correlation diverged")


def _assert_run_resolved_version(
    *,
    provider_container: str,
    operations_database_url: str,
    workspace_id: str,
    run_id: str,
    expected_version_id: str,
) -> None:
    correlations = _operations_correlations(
        operations_database_url,
        workspace_id=workspace_id,
        run_id=run_id,
    )
    rows = [
        row
        for row in _provider_audit_rows(provider_container, workspace_id)
        if row["correlation_id"] in correlations
        and row["outcome"] == "resolved"
        and row["intent"] == POSTGRES_INTENT
    ]
    if not rows or expected_version_id not in {row["version_id"] for row in rows}:
        raise RuntimeError("runtime effect did not resolve the expected provider version")


def _assert_activity_is_secret_free(workflow: HostedWorkflow) -> None:
    rendered = json.dumps(
        workflow.read_activity(limit=400),
        separators=(",", ":"),
        sort_keys=True,
    ).lower()
    for forbidden_key in (
        "value_base64",
        "ciphertext",
        "private_key",
        "access_token",
    ):
        if forbidden_key in rendered:
            raise RuntimeError("activity readback exposed secret material")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
