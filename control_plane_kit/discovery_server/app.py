"""Bounded FastAPI transport for the canonical discovery registry service."""

import hmac
import json
from collections.abc import Callable, Mapping

from control_plane_kit.discovery import (
    DeregisterDiscoveryInstance,
    DiscoveryCommand,
    ExpireDiscoveryLeases,
    HeartbeatDiscoveryInstance,
    RegisterDiscoveryInstance,
    discovery_authority_from_descriptor,
    discovery_command_from_descriptor,
)
from control_plane_kit.discovery_registry import (
    DiscoveryConflict,
    DiscoveryDenied,
    DiscoveryMissing,
    DiscoveryRegistryService,
)
from control_plane_kit.servers._fastapi import require_fastapi


MAX_DISCOVERY_REQUEST_BYTES = 16_384
MAX_DISCOVERY_RESPONSE_BYTES = 524_288


def create_service_discovery_app(
    service: DiscoveryRegistryService,
    *,
    identity_attestation_token: str,
    readiness: Callable[[], bool],
):
    """Mount the canonical registry service behind a bounded HTTP adapter."""

    if not isinstance(service, DiscoveryRegistryService):
        raise TypeError("service-discovery app requires DiscoveryRegistryService")
    if not identity_attestation_token:
        raise ValueError("service-discovery identity attestation token is required")
    Depends, FastAPI, Header, HTTPException, Request = require_fastapi()
    app = FastAPI(title="control-plane-kit service discovery")

    def authority(
        x_cpk_identity_attestation: str | None = Header(default=None),
        x_cpk_authenticated_subject: str | None = Header(default=None),
        x_cpk_authenticated_workspace: str | None = Header(default=None),
        x_cpk_discovery_scopes: str | None = Header(default=None),
        x_cpk_discovery_service: str | None = Header(default=None),
        x_cpk_discovery_instance: str | None = Header(default=None),
    ):
        supplied = x_cpk_identity_attestation or ""
        if not hmac.compare_digest(supplied, identity_attestation_token):
            raise HTTPException(status_code=401, detail="unauthorized")
        if not all(
            (x_cpk_authenticated_subject, x_cpk_authenticated_workspace, x_cpk_discovery_scopes)
        ):
            raise HTTPException(status_code=400, detail="invalid discovery identity")
        try:
            return discovery_authority_from_descriptor(
                {
                    "actor_id": x_cpk_authenticated_subject,
                    "workspace_id": x_cpk_authenticated_workspace,
                    "scopes": x_cpk_discovery_scopes.split(","),
                    "subject_service_id": x_cpk_discovery_service,
                    "subject_instance_id": x_cpk_discovery_instance,
                }
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="invalid discovery identity") from error

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/health/ready")
    def ready():
        try:
            is_ready = readiness()
        except Exception:
            is_ready = False
        if not is_ready:
            raise HTTPException(status_code=503, detail="discovery registry is not ready")
        return {"status": "ready"}

    @app.post("/__deploy/discovery/registrations")
    async def register(request: Request, caller=Depends(authority)):
        command = await _command(request, HTTPException)
        if not isinstance(command, RegisterDiscoveryInstance):
            raise HTTPException(status_code=400, detail="register route requires register command")
        return _execute(service, command, caller, HTTPException)

    @app.post("/__deploy/discovery/registrations/{instance_id}/heartbeat")
    async def heartbeat(instance_id: str, request: Request, caller=Depends(authority)):
        command = await _command(request, HTTPException)
        if not isinstance(command, HeartbeatDiscoveryInstance):
            raise HTTPException(status_code=400, detail="heartbeat route requires heartbeat command")
        if command.identity.instance_id != instance_id:
            raise HTTPException(status_code=409, detail="discovery route identity mismatch")
        return _execute(service, command, caller, HTTPException)

    @app.post("/__deploy/discovery/registrations/{instance_id}/deregister")
    async def deregister(instance_id: str, request: Request, caller=Depends(authority)):
        command = await _command(request, HTTPException)
        if not isinstance(command, DeregisterDiscoveryInstance):
            raise HTTPException(status_code=400, detail="deregister route requires deregister command")
        if command.identity.instance_id != instance_id:
            raise HTTPException(status_code=409, detail="discovery route identity mismatch")
        return _execute(service, command, caller, HTTPException)

    @app.get("/__deploy/discovery/services/{service_id}")
    def resolve(
        service_id: str,
        command_id: str,
        workspace_id: str,
        observed_at: str,
        limit: int = 100,
        caller=Depends(authority),
    ):
        try:
            command = discovery_command_from_descriptor(
                {
                    "variant": "resolve",
                    "command_id": command_id,
                    "workspace_id": workspace_id,
                    "service_id": service_id,
                    "observed_at": observed_at,
                    "limit": limit,
                }
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="invalid discovery resolve request") from error
        return _execute(service, command, caller, HTTPException)

    @app.post("/__deploy/discovery/expiry")
    async def expire(request: Request, caller=Depends(authority)):
        command = await _command(request, HTTPException)
        if not isinstance(command, ExpireDiscoveryLeases):
            raise HTTPException(status_code=400, detail="expiry route requires expire command")
        return _execute(service, command, caller, HTTPException)

    return app


async def _command(request, http_exception) -> DiscoveryCommand:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError as error:
            raise http_exception(status_code=400, detail="invalid content length") from error
        if length < 0 or length > MAX_DISCOVERY_REQUEST_BYTES:
            raise http_exception(status_code=413, detail="request entity too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_DISCOVERY_REQUEST_BYTES:
            raise http_exception(status_code=413, detail="request entity too large")
        chunks.append(chunk)
    try:
        value = json.loads(b"".join(chunks))
        if not isinstance(value, Mapping):
            raise ValueError("command must be an object")
        return discovery_command_from_descriptor(value)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise http_exception(status_code=400, detail="invalid discovery command") from error


def _execute(service, command, authority, http_exception) -> dict[str, object]:
    try:
        result = service.execute(command, authority)
    except DiscoveryDenied as error:
        raise http_exception(status_code=403, detail="discovery operation denied") from error
    except DiscoveryMissing as error:
        raise http_exception(status_code=404, detail="discovery registration not found") from error
    except DiscoveryConflict as error:
        raise http_exception(status_code=409, detail="discovery operation conflicts") from error
    response = {"result": result.descriptor(), "replayed": result.replayed}
    if len(json.dumps(response, sort_keys=True, separators=(",", ":")).encode()) > (
        MAX_DISCOVERY_RESPONSE_BYTES
    ):
        raise http_exception(status_code=500, detail="discovery response exceeds bound")
    return response
