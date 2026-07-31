"""Prepare durable remote-Docker TLS custody through public cpk-server APIs."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from control_plane_kit_core.policies import PolicyScope

from cpk_server_hosted_activity import (
    AUTHORIZATION,
    HostedWorkflow,
    _clock,
    _http,
    _mcp,
)


PROVIDER_ID = "control-plane-kit"
PROVIDER_ENDPOINT_REFERENCE = "source-live-secrets"
PROVIDER_CREDENTIAL_REFERENCE = "secret://bootstrap/provider/client-token"
AUTHORITY_REF = "source-live-remote-docker-tls"
CA_REFERENCE = "secret://control-plane-kit/docker-tls/ca"
CERTIFICATE_REFERENCE = "secret://control-plane-kit/docker-tls/cert"
KEY_REFERENCE = "secret://control-plane-kit/docker-tls/key"
CA_INTENT = "docker.remote-tls.ca-certificate"
CERTIFICATE_INTENT = "docker.remote-tls.client-certificate"
KEY_INTENT = "docker.remote-tls.client-key"
PROVIDER_BASE_URL = "http://cpk-secrets:8081"
TLS_SECRETS = (
    (CA_REFERENCE, CA_INTENT, "ca.pem"),
    (CERTIFICATE_REFERENCE, CERTIFICATE_INTENT, "cert.pem"),
    (KEY_REFERENCE, KEY_INTENT, "key.pem"),
)


def main() -> int:
    base_url = _required_env("CPK_HOSTED_ACTIVITY_BASE_URL").rstrip("/")
    workspace_id = _required_env("CPK_HOSTED_ACTIVITY_WORKSPACE_ID")
    server_container = _required_env("CPK_HOSTED_ACTIVITY_SERVER_CONTAINER")
    endpoint = _required_env("CPK_REMOTE_DOCKER_TLS_ENDPOINT")
    bootstrap_dir = Path(_required_env("CPK_SECRET_PROVIDER_BOOTSTRAP_DIR"))
    provider_token_file = Path(_required_env("CPK_SECRET_PROVIDER_TOKEN_FILE"))

    workflow = HostedWorkflow(
        base_url,
        workspace_id=workspace_id,
        worker_id="remote-tls-source-live-worker",
        server_container=server_container,
    )
    workflow.wait_ready()
    workflow.create_workspace(name="Remote Docker TLS custody foundation")

    provider_registration_id = _register_provider(workflow)
    for reference, intent, value_file in TLS_SECRETS:
        _register_reference(
            workflow,
            provider_registration_id=provider_registration_id,
            reference=reference,
            intent=intent,
        )
        _provider_write_secret(
            workspace_id=workspace_id,
            reference=reference,
            intent=intent,
            value_file=bootstrap_dir / value_file,
            provider_token_file=provider_token_file,
        )

    _register_runtime_authority(workflow, endpoint=endpoint)
    _assert_public_metadata_is_secret_free(workflow)
    print("cpk-server remote Docker TLS durable-custody foundation passed")
    return 0


def _register_provider(workflow: HostedWorkflow) -> str:
    response = _http(
        workflow.base_url,
        "POST",
        f"/workspaces/{workflow.workspace_id}/secret-providers",
        {
            "provider_id": PROVIDER_ID,
            "provider_kind": "control-plane-kit-secrets",
            "display_name": "Remote Docker TLS source-live custody",
            "endpoint_reference": PROVIDER_ENDPOINT_REFERENCE,
            "credential_reference": PROVIDER_CREDENTIAL_REFERENCE,
            "allowed_reference_prefixes": [
                "secret://control-plane-kit/docker-tls"
            ],
            "allowed_intents": [
                CA_INTENT,
                CERTIFICATE_INTENT,
                KEY_INTENT,
            ],
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
) -> None:
    _http(
        workflow.base_url,
        "POST",
        f"/workspaces/{workflow.workspace_id}/secret-references",
        {
            "reference": reference,
            "provider_registration_id": provider_registration_id,
            "allowed_intents": [intent],
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
    expected_references = {reference for reference, _, _ in TLS_SECRETS}
    if {item.get("reference_id") for item in reference_items} != expected_references:
        raise RuntimeError("public reference readback omitted admitted TLS reference")
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
    encoded_reference = base64.urlsafe_b64encode(reference.encode("utf-8"))
    secret_id = f"cpk1_{encoded_reference.rstrip(b'=').decode('ascii')}"
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


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is required")
    return value.strip()


if __name__ == "__main__":
    raise SystemExit(main())
