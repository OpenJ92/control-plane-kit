"""Prepare durable remote-Docker TLS custody through public cpk-server APIs."""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.products import ProductDescriptorCodec
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_core.verification import VerificationContract

from cpk_server_hosted_activity import (
    AUTHORIZATION,
    HostedWorkflow,
    _assert_activity_mentions,
    _assert_runtime_activity_mentions,
    _clock,
    _http,
    _mcp,
    _product_document,
    _single_hello_graph,
)


PROVIDER_ID = "control-plane-kit"
PROVIDER_ENDPOINT_REFERENCE = "source-live-secrets"
PROVIDER_CREDENTIAL_REFERENCE = "secret://bootstrap/provider/client-token"
AUTHORITY_REF = "source-live-remote-docker-tls"
CA_REFERENCE = "secret://control-plane-kit/docker-tls/ca"
CERTIFICATE_REFERENCE = "secret://control-plane-kit/docker-tls/cert"
KEY_REFERENCE = "secret://control-plane-kit/docker-tls/key"
GHCR_PULL_CREDENTIAL_REFERENCE = "secret://control-plane-kit/oci-pull/ghcr"
CA_INTENT = "docker.remote-tls.ca-certificate"
CERTIFICATE_INTENT = "docker.remote-tls.client-certificate"
KEY_INTENT = "docker.remote-tls.client-key"
OCI_PULL_CREDENTIAL_INTENT = "oci.pull-credential"
APPLICATION_TOKEN_INTENT = "application.control-token"
PROVIDER_BASE_URL = "http://cpk-secrets:8081"
STATE_FILENAME = "remote-tls-graph-state.json"
DENIAL_CASES = (
    "wrong-workspace",
    "wrong-intent",
    "revoked-version",
    "provider-unavailable",
)
TLS_SECRETS = (
    (CA_REFERENCE, CA_INTENT, "ca.pem"),
    (CERTIFICATE_REFERENCE, CERTIFICATE_INTENT, "cert.pem"),
    (KEY_REFERENCE, KEY_INTENT, "key.pem"),
)
CUSTODY_SECRETS = (
    *TLS_SECRETS,
    (
        GHCR_PULL_CREDENTIAL_REFERENCE,
        OCI_PULL_CREDENTIAL_INTENT,
        "ghcr-pull-credential.json",
    ),
)


def main() -> int:
    base_url = _required_env("CPK_HOSTED_ACTIVITY_BASE_URL").rstrip("/")
    workspace_id = _required_env("CPK_HOSTED_ACTIVITY_WORKSPACE_ID")
    server_container = _required_env("CPK_HOSTED_ACTIVITY_SERVER_CONTAINER")
    endpoint = _required_env("CPK_REMOTE_DOCKER_TLS_ENDPOINT")
    bootstrap_dir = Path(_required_env("CPK_SECRET_PROVIDER_BOOTSTRAP_DIR"))
    provider_token_file = Path(_required_env("CPK_SECRET_PROVIDER_TOKEN_FILE"))
    state_file = Path(_required_env("CPK_REMOTE_TLS_STATE_DIR")) / STATE_FILENAME
    servers_repo = Path(_required_env("CPK_HOSTED_ACTIVITY_SERVERS_REPO"))
    phase = _required_env("CPK_REMOTE_TLS_PHASE")

    workflow = HostedWorkflow(
        base_url,
        workspace_id=workspace_id,
        worker_id="remote-tls-source-live-worker",
        server_container=server_container,
    )
    workflow.wait_ready()
    if phase == "deploy":
        _deploy_initial_graph(
            workflow,
            endpoint=endpoint,
            bootstrap_dir=bootstrap_dir,
            provider_token_file=provider_token_file,
            servers_repo=servers_repo,
            state_file=state_file,
        )
    elif phase == "resume":
        _resume_update_and_teardown(
            workflow,
            servers_repo=servers_repo,
            state_file=state_file,
        )
    elif phase == "deny":
        denial_case = _required_env("CPK_REMOTE_TLS_DENIAL_CASE")
        denial_action = _required_env("CPK_REMOTE_TLS_DENIAL_ACTION")
        if denial_case not in DENIAL_CASES:
            raise RuntimeError("CPK_REMOTE_TLS_DENIAL_CASE is unsupported")
        denial_state_file = (
            state_file.parent / f"remote-tls-denial-{denial_case}.json"
        )
        if denial_action == "prepare":
            _prepare_denial(
                workflow,
                denial_case=denial_case,
                endpoint=endpoint,
                bootstrap_dir=bootstrap_dir,
                provider_token_file=provider_token_file,
                servers_repo=servers_repo,
                state_file=denial_state_file,
            )
        elif denial_action == "execute":
            _execute_denial(
                workflow,
                denial_case=denial_case,
                bootstrap_dir=bootstrap_dir,
                state_file=denial_state_file,
            )
        else:
            raise RuntimeError(
                "CPK_REMOTE_TLS_DENIAL_ACTION must be prepare or execute"
            )
    else:
        raise RuntimeError("CPK_REMOTE_TLS_PHASE must be deploy, resume, or deny")
    return 0


def _deploy_initial_graph(
    workflow: HostedWorkflow,
    *,
    endpoint: str,
    bootstrap_dir: Path,
    provider_token_file: Path,
    servers_repo: Path,
    state_file: Path,
) -> None:
    current_graph_id = workflow.create_workspace(
        name="Remote Docker TLS durable-custody graph"
    )
    provider_registration_id = _register_provider(workflow)
    for reference, intent, value_file in CUSTODY_SECRETS:
        _register_reference(
            workflow,
            provider_registration_id=provider_registration_id,
            reference=reference,
            intent=intent,
        )
        _provider_write_secret(
            workspace_id=workflow.workspace_id,
            reference=reference,
            intent=intent,
            value_file=bootstrap_dir / value_file,
            provider_token_file=provider_token_file,
        )
    _register_runtime_authority(workflow, endpoint=endpoint)
    workflow.register_ghcr_pull_authority(
        credential_reference=GHCR_PULL_CREDENTIAL_REFERENCE,
    )
    hello_document = _verification_free_hello_document(servers_repo)
    workflow.import_product("remote-hello", hello_document)
    graph = _single_hello_graph(
        hello_document,
        workspace_id=workflow.workspace_id,
        authority_ref=RuntimeAuthorityReference(AUTHORITY_REF),
        message="Hello from remote Docker TLS blue",
    )
    deployed = workflow.run_approved_transition(
        title="Remote Docker TLS blue deployment",
        graph=graph,
        current_graph_id=current_graph_id,
        sync_runtime_networks=False,
    )
    _assert_runtime_activity_mentions(workflow, deployed.run_id, "docker")
    _assert_activity_mentions(workflow, deployed.run_id, "hello")
    _assert_public_metadata_is_secret_free(workflow)
    _write_state(
        state_file,
        {
            "current_graph_id": deployed.current_graph_id,
            "desired_graph_id": deployed.desired_graph_id,
            "initial_run_id": deployed.run_id,
        },
    )
    print("cpk-server remote Docker TLS durable-custody deployment passed")


def _resume_update_and_teardown(
    workflow: HostedWorkflow,
    *,
    servers_repo: Path,
    state_file: Path,
) -> None:
    state = _read_state(state_file)
    current_graph_id = str(state["current_graph_id"])
    desired_graph_id = str(state["desired_graph_id"])
    if workflow.read_current_graph_id() != current_graph_id:
        raise RuntimeError("cpk-server restart lost current graph truth")
    _assert_public_metadata_is_secret_free(workflow)

    hello_document = _verification_free_hello_document(servers_repo)
    graph = _single_hello_graph(
        hello_document,
        workspace_id=workflow.workspace_id,
        authority_ref=RuntimeAuthorityReference(AUTHORITY_REF),
        message="Hello from remote Docker TLS green",
    )
    updated = workflow.run_approved_transition(
        title="Remote Docker TLS green update after restart",
        graph=graph,
        current_graph_id=current_graph_id,
        expected_desired_graph_id=desired_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, updated.run_id, "hello")
    removed = workflow.run_approved_transition(
        title="Remote Docker TLS teardown after restart",
        graph=DeploymentGraph(workflow.workspace_id),
        current_graph_id=updated.current_graph_id,
        expected_desired_graph_id=updated.desired_graph_id,
        sync_runtime_networks=False,
    )
    _assert_activity_mentions(workflow, removed.run_id, "hello")
    _write_state(
        state_file,
        {
            **state,
            "updated_graph_id": updated.current_graph_id,
            "updated_run_id": updated.run_id,
            "teardown_graph_id": removed.current_graph_id,
            "teardown_run_id": removed.run_id,
        },
    )
    print("cpk-server remote Docker TLS restart/update/teardown passed")


@dataclass(frozen=True)
class PreparedDenialRun:
    run_id: str
    plan_id: str
    current_graph_id: str
    desired_graph_id: str


def _prepare_denial(
    workflow: HostedWorkflow,
    *,
    denial_case: str,
    endpoint: str,
    bootstrap_dir: Path,
    provider_token_file: Path,
    servers_repo: Path,
    state_file: Path,
) -> None:
    current_graph_id = workflow.create_workspace(
        name=f"Remote Docker TLS denial {denial_case}"
    )
    if denial_case == "wrong-workspace":
        source = HostedWorkflow(
            workflow.base_url,
            workspace_id=f"{workflow.workspace_id}-source",
            worker_id=workflow.worker_id,
            server_container=workflow.server_container,
            worker_authorization=workflow.worker_authorization,
        )
        source.create_workspace(name="Remote Docker TLS wrong-workspace source")
        _register_custody_material(
            source,
            bootstrap_dir=bootstrap_dir,
            provider_token_file=provider_token_file,
        )
    else:
        reference_intents = {}
        provider_intents = tuple(intent for _, intent, _ in CUSTODY_SECRETS)
        if denial_case == "wrong-intent":
            reference_intents[CERTIFICATE_REFERENCE] = (
                APPLICATION_TOKEN_INTENT,
            )
            provider_intents = (*provider_intents, APPLICATION_TOKEN_INTENT)
        _register_custody_material(
            workflow,
            bootstrap_dir=bootstrap_dir,
            provider_token_file=provider_token_file,
            provider_intents=provider_intents,
            reference_intents=reference_intents,
        )

    _register_runtime_authority(workflow, endpoint=endpoint)
    workflow.register_ghcr_pull_authority(
        credential_reference=GHCR_PULL_CREDENTIAL_REFERENCE,
    )
    hello_document = _verification_free_hello_document(servers_repo)
    workflow.import_product("remote-hello", hello_document)
    graph = _single_hello_graph(
        hello_document,
        workspace_id=workflow.workspace_id,
        authority_ref=RuntimeAuthorityReference(AUTHORITY_REF),
        message=f"Remote Docker TLS denied {denial_case}",
    )
    prepared = _prepare_denied_run(
        workflow,
        title=f"Remote Docker TLS denied {denial_case}",
        graph=graph,
        current_graph_id=current_graph_id,
    )
    if denial_case == "revoked-version":
        _provider_revoke_secret(
            workspace_id=workflow.workspace_id,
            reference=CA_REFERENCE,
            provider_token_file=provider_token_file,
            correlation_id=f"{workflow.workspace_id}:revoked-version:setup",
        )
    _write_state(
        state_file,
        {
            "denial_case": denial_case,
            "run_id": prepared.run_id,
            "plan_id": prepared.plan_id,
            "current_graph_id": prepared.current_graph_id,
            "desired_graph_id": prepared.desired_graph_id,
        },
    )
    print(f"cpk-server remote Docker TLS denial prepared: {denial_case}")


def _execute_denial(
    workflow: HostedWorkflow,
    *,
    denial_case: str,
    bootstrap_dir: Path,
    state_file: Path,
) -> None:
    state = _read_state(state_file)
    if state.get("denial_case") != denial_case:
        raise RuntimeError("remote Docker TLS denial state does not match case")
    terminal = _execute_until_terminal(workflow, str(state["run_id"]))
    if terminal.get("coordinator_status") not in {
        "failed",
        "unsupported",
        "uncertain",
        "blocked",
    }:
        raise RuntimeError("remote Docker TLS denial did not stop execution")
    if workflow.read_current_graph_id() != str(state["current_graph_id"]):
        raise RuntimeError("denied remote Docker TLS run advanced current graph")
    timeline = _http(
        workflow.base_url,
        "GET",
        f"/workspaces/{workflow.workspace_id}/activity",
    )
    rendered = json.dumps(
        {"terminal": terminal, "timeline": timeline},
        separators=(",", ":"),
        sort_keys=True,
    )
    expected_code = {
        "wrong-workspace": "secret.use-not-authorized",
        "wrong-intent": "secret.use-not-authorized",
        "revoked-version": "docker.runtime-authority-secret-denied",
        "provider-unavailable": "docker.runtime-authority-uncertain",
    }[denial_case]
    if expected_code not in rendered:
        raise RuntimeError(
            f"remote Docker TLS denial omitted bounded code {expected_code}"
        )
    lowered = rendered.lower()
    for forbidden in ("begin certificate", "begin private key", "value_base64"):
        if forbidden in lowered:
            raise RuntimeError("remote Docker TLS denial exposed secret material")
    credential = json.loads(
        (bootstrap_dir / "ghcr-pull-credential.json").read_text(encoding="utf-8")
    )
    token = credential.get("password")
    if isinstance(token, str) and token and token in rendered:
        raise RuntimeError("remote Docker TLS denial exposed OCI credential")
    print(f"cpk-server remote Docker TLS denial passed: {denial_case}")


def _prepare_denied_run(
    workflow: HostedWorkflow,
    *,
    title: str,
    graph: DeploymentGraph,
    current_graph_id: str,
) -> PreparedDenialRun:
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
    return PreparedDenialRun(
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
        result = _mcp(
            workflow.base_url,
            "tools/call",
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
            timeout=90,
            authorization=workflow.worker_authorization,
        )
        if result.get("coordinator_status") in {
            "completed",
            "failed",
            "unsupported",
            "uncertain",
            "blocked",
        }:
            return result
    raise RuntimeError("remote Docker TLS denial did not reach a terminal state")


def _register_custody_material(
    workflow: HostedWorkflow,
    *,
    bootstrap_dir: Path,
    provider_token_file: Path,
    provider_intents: tuple[str, ...] | None = None,
    reference_intents: dict[str, tuple[str, ...]] | None = None,
) -> None:
    provider_registration_id = _register_provider(
        workflow,
        allowed_intents=provider_intents,
    )
    overrides = reference_intents or {}
    for reference, intent, value_file in CUSTODY_SECRETS:
        _register_reference(
            workflow,
            provider_registration_id=provider_registration_id,
            reference=reference,
            intent=intent,
            allowed_intents=overrides.get(reference),
        )
        _provider_write_secret(
            workspace_id=workflow.workspace_id,
            reference=reference,
            intent=intent,
            value_file=bootstrap_dir / value_file,
            provider_token_file=provider_token_file,
        )


def _verification_free_hello_document(servers_repo: Path) -> Any:
    seeded = _product_document(servers_repo, "hello_server")
    product = replace(
        seeded.product,
        runtime_contract=replace(
            seeded.product.runtime_contract,
            verification=VerificationContract(),
        ),
        description=(
            "Source-live remote Docker TLS harness product. Semantic verification "
            "is omitted because the parent cpk-server is outside the nested daemon network."
        ),
    )
    return ProductDescriptorCodec().encode_document(product)


def _write_state(path: Path, state: dict[str, object]) -> None:
    path.write_text(
        json.dumps(state, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def _read_state(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("remote Docker TLS state is malformed")
    return value


def _register_provider(
    workflow: HostedWorkflow,
    *,
    allowed_intents: tuple[str, ...] | None = None,
) -> str:
    intents = allowed_intents or tuple(
        intent for _, intent, _ in CUSTODY_SECRETS
    )
    response = _http(
        workflow.base_url,
        "POST",
        f"/workspaces/{workflow.workspace_id}/secret-providers",
        {
            "provider_id": PROVIDER_ID,
            "provider_kind": "control-plane-kit-secrets",
            "display_name": "Remote Docker TLS and OCI pull source-live custody",
            "endpoint_reference": PROVIDER_ENDPOINT_REFERENCE,
            "credential_reference": PROVIDER_CREDENTIAL_REFERENCE,
            "allowed_reference_prefixes": [
                "secret://control-plane-kit/docker-tls",
                "secret://control-plane-kit/oci-pull",
            ],
            "allowed_intents": list(intents),
            "admitted_at": _clock(),
            "metadata": {"acceptance": "remote-docker-tls-source-live"},
            "idempotency_key": f"{workflow.workspace_id}:secret-provider",
        },
    )
    return str(response["registration_id"])


def _register_reference(
    workflow: HostedWorkflow,
    *,
    provider_registration_id: str,
    reference: str,
    intent: str,
    allowed_intents: tuple[str, ...] | None = None,
) -> None:
    _http(
        workflow.base_url,
        "POST",
        f"/workspaces/{workflow.workspace_id}/secret-references",
        {
            "reference": reference,
            "provider_registration_id": provider_registration_id,
            "allowed_intents": list(allowed_intents or (intent,)),
            "admitted_at": _clock(),
            "metadata": {"acceptance": "remote-docker-tls-source-live"},
            "idempotency_key": (
                f"{workflow.workspace_id}:secret-reference:"
                f"{intent.rsplit('.', maxsplit=1)[-1]}"
            ),
        },
    )


def _register_runtime_authority(
    workflow: HostedWorkflow,
    *,
    endpoint: str,
) -> None:
    _mcp(
        workflow.base_url,
        "tools/call",
        "command.runtime-authority.register",
        {
            "workspace_id": workflow.workspace_id,
            "authority_ref": AUTHORITY_REF,
            "runtime_kind": "docker",
            "authority": {
                "kind": "remote-docker-tls",
                "endpoint": endpoint,
                "ca_certificate": CA_REFERENCE,
                "client_certificate": CERTIFICATE_REFERENCE,
                "client_key": KEY_REFERENCE,
            },
            "actor_id": "operator-a",
            "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_REGISTER.value],
            "admitted_at": _clock(),
            "idempotency_key": f"{workflow.workspace_id}:runtime-authority",
        },
        authorization=AUTHORIZATION,
    )


def _assert_public_metadata_is_secret_free(workflow: HostedWorkflow) -> None:
    providers = _mcp(
        workflow.base_url,
        "resources/read",
        "read.secret-providers",
        {"workspace_id": workflow.workspace_id, "limit": 10, "offset": 0},
        authorization=AUTHORIZATION,
    )
    references = _mcp(
        workflow.base_url,
        "resources/read",
        "read.secret-references",
        {"workspace_id": workflow.workspace_id, "limit": 10, "offset": 0},
        authorization=AUTHORIZATION,
    )
    authorities = _mcp(
        workflow.base_url,
        "resources/read",
        "read.runtime-authorities",
        {
            "workspace_id": workflow.workspace_id,
            "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_READ.value],
        },
        authorization=AUTHORIZATION,
    )
    authority_detail = _mcp(
        workflow.base_url,
        "resources/read",
        "read.runtime-authority-detail",
        {
            "workspace_id": workflow.workspace_id,
            "authority_ref": AUTHORITY_REF,
            "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_READ.value],
        },
        authorization=AUTHORIZATION,
    )
    provider_items = providers.get("items", [])
    if {item.get("provider_id") for item in provider_items} != {PROVIDER_ID}:
        raise RuntimeError("public provider readback omitted admitted provider")
    reference_items = references.get("items", [])
    expected_references = {reference for reference, _, _ in CUSTODY_SECRETS}
    if {item.get("reference_id") for item in reference_items} != expected_references:
        raise RuntimeError("public reference readback omitted admitted custody reference")
    authority_items = authorities.get("items", [])
    if {item.get("authority_ref") for item in authority_items} != {AUTHORITY_REF}:
        raise RuntimeError("public authority readback omitted remote Docker authority")
    if authority_detail.get("runtime_authority", {}).get("authority_ref") != AUTHORITY_REF:
        raise RuntimeError("public authority detail omitted remote Docker authority")
    rendered = json.dumps(
        {
            "providers": providers,
            "references": references,
            "authorities": authorities,
            "authority_detail": authority_detail,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).lower()
    for forbidden in (
        "begin certificate",
        "begin private key",
        "value_base64",
        "ciphertext",
    ):
        if forbidden in rendered:
            raise RuntimeError("public runtime-authority metadata exposed TLS material")


def _provider_write_secret(
    *,
    workspace_id: str,
    reference: str,
    intent: str,
    value_file: Path,
    provider_token_file: Path,
) -> None:
    secret_id = _provider_secret_id(reference)
    request = Request(
        f"{PROVIDER_BASE_URL}/v1/workspaces/{workspace_id}/secrets/{secret_id}",
        method="POST",
        headers={
            "Authorization": (
                f"Bearer {provider_token_file.read_text(encoding='utf-8').strip()}"
            ),
            "Content-Type": "application/json",
        },
        data=json.dumps(
            {
                "value_base64": base64.b64encode(value_file.read_bytes()).decode(
                    "ascii"
                ),
                "intent": intent,
                "labels": {"intent": intent},
                "caller_subject": "remote-tls-source-live-bootstrap",
                "correlation_id": f"{workspace_id}:{intent}:write",
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
    )
    try:
        with urlopen(request, timeout=20) as response:
            status = response.status
            payload: Any = json.loads(response.read())
    except HTTPError as error:
        status = error.code
        payload = json.loads(error.read())
    if status != 200 or not isinstance(payload, dict) or payload.get("outcome") != "stored":
        raise RuntimeError("provider TLS fixture write failed")


def _provider_revoke_secret(
    *,
    workspace_id: str,
    reference: str,
    provider_token_file: Path,
    correlation_id: str,
) -> None:
    request = Request(
        (
            f"{PROVIDER_BASE_URL}/v1/workspaces/{workspace_id}/secrets/"
            f"{_provider_secret_id(reference)}/revoke"
        ),
        method="POST",
        headers={
            "Authorization": (
                f"Bearer {provider_token_file.read_text(encoding='utf-8').strip()}"
            ),
            "Content-Type": "application/json",
        },
        data=json.dumps(
            {
                "caller_subject": "remote-tls-source-live-bootstrap",
                "correlation_id": correlation_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
    )
    try:
        with urlopen(request, timeout=20) as response:
            status = response.status
            payload: Any = json.loads(response.read())
    except HTTPError as error:
        status = error.code
        payload = json.loads(error.read())
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError("provider TLS fixture revocation failed")


def _provider_secret_id(reference: str) -> str:
    encoded_reference = base64.urlsafe_b64encode(reference.encode("utf-8"))
    return f"cpk1_{encoded_reference.rstrip(b'=').decode('ascii')}"


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is required")
    return value.strip()


if __name__ == "__main__":
    raise SystemExit(main())
