"""Source-live cpk-server acceptance against a real durable secrets provider."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.request import urlopen

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

from cpk_server_hosted_activity import (
    AUTHORIZATION,
    LOCAL_DOCKER_AUTHORITY_REF,
    HostedWorkflow,
    _assert_activity_mentions,
    _assert_no_node_containers,
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


def main() -> int:
    base_url = _required_env("CPK_HOSTED_ACTIVITY_BASE_URL").rstrip("/")
    server_container = _required_env("CPK_HOSTED_ACTIVITY_SERVER_CONTAINER")
    provider_container = _required_env("CPK_SECRET_PROVIDER_CONTAINER")
    servers_repo = Path(_required_env("CPK_HOSTED_ACTIVITY_SERVERS_REPO"))
    operations_database_url = _required_env("CPK_OPERATIONS_DATABASE_URL")

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

    _restart_provider(provider_container)
    deployed = success.run_approved_transition(
        title="Secret provider source-live deploy",
        graph=_postgres_graph(postgres_document, SUCCESS_WORKSPACE),
        current_graph_id=current_graph_id,
    )
    _assert_activity_mentions(success, deployed.run_id, "postgres")
    _assert_provider_and_operations_correlation(
        provider_container=provider_container,
        operations_database_url=operations_database_url,
        workspace_id=SUCCESS_WORKSPACE,
    )
    _assert_activity_is_secret_free(success)
    _disconnect_runtime_networks(
        success.server_container,
        workspace_id=SUCCESS_WORKSPACE,
    )
    removed = success.run_approved_transition(
        title="Secret provider source-live teardown",
        graph=DeploymentGraph(SUCCESS_WORKSPACE),
        current_graph_id=deployed.current_graph_id,
        expected_desired_graph_id=deployed.desired_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(success, removed.run_id, "postgres")
    _assert_no_node_containers(SUCCESS_WORKSPACE, "postgres")

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
        run_id = _prepare_run(
            workflow,
            title=f"Denied secret use {workspace_id}",
            graph=_postgres_graph(postgres_document, workspace_id),
            current_graph_id=current_graph_id,
        )
        terminal = _execute_until_terminal(workflow, run_id)
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


def _postgres_graph(postgres_document: Any, workspace_id: str) -> DeploymentGraph:
    product = postgres_document.product
    postgres = instantiate_product(
        product,
        "postgres",
        ProductInstanceConfiguration.from_contract(product.runtime_contract),
    )
    return compile_topology(
        DeploymentTopology(
            workspace_id,
            DockerRuntime(
                runtime_id="docker",
                network_name=f"control-plane-kit-{workspace_id}-docker",
                authority_ref=RuntimeAuthorityReference(LOCAL_DOCKER_AUTHORITY_REF),
                children=(postgres,),
            ),
        )
    )


def _prepare_run(
    workflow: HostedWorkflow,
    *,
    title: str,
    graph: DeploymentGraph,
    current_graph_id: str,
) -> str:
    session_id = workflow.start_session(title)
    desired_graph_id = workflow.set_desired_graph(
        session_id=session_id,
        graph=graph,
        title=title,
        expected_desired_graph_id=None,
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
    return run_id


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
    docker.from_env().containers.get(container_id).restart(timeout=10)
    _wait_provider_ready()


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


def _provider_audit_rows(
    provider_container: str,
    workspace_id: str,
) -> list[dict[str, str]]:
    script = (
        "import json,sqlite3,sys;"
        "c=sqlite3.connect('/var/lib/cpk-secrets/secrets.sqlite3');"
        "rows=c.execute("
        "\"SELECT correlation_id,outcome,intent,caller_subject "
        "FROM audit_records WHERE workspace_id=? ORDER BY rowid\","
        "(sys.argv[1],)).fetchall();"
        "print(json.dumps([dict(zip("
        "('correlation_id','outcome','intent','caller_subject'),r)) for r in rows]))"
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
    with psycopg.connect(operations_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT correlation_id
                FROM cpk_secret_use_authorizations
                WHERE workspace_id = %s AND use_intent = %s
                """,
                (workspace_id, POSTGRES_INTENT),
            )
            operations_correlations = {str(row[0]) for row in cursor.fetchall()}
    provider_correlations = {row["correlation_id"] for row in provider_rows}
    if not provider_correlations.issubset(operations_correlations):
        raise RuntimeError("operations/provider secret-use correlation diverged")


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
