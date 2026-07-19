"""Bounded authenticated FastAPI boundary for webhook delivery."""

import hmac
import json
from collections.abc import Callable, Mapping

from control_plane_kit.webhook.language import (
    WebhookAuthority,
    WebhookDeliveryIdentity,
    webhook_authority_from_descriptor,
    webhook_intent_from_descriptor,
)
from control_plane_kit.webhook.service import (
    ClaimWebhook,
    DispatchWebhook,
    EnqueueWebhook,
    RecoverWebhook,
    ReleaseWebhookClaim,
    WebhookAuthorizationError,
    WebhookCommandConflict,
    WebhookCommandResult,
    WebhookDeliveryService,
    WebhookStateConflict,
    webhook_delivery_descriptor,
)


MAX_WEBHOOK_API_REQUEST_BYTES = 1_500_000
MAX_WEBHOOK_API_RESPONSE_BYTES = 262_144


def _require_fastapi():
    """Load the optional HTTP server dependency only when this adapter is used."""

    try:
        from fastapi import Depends, FastAPI, Header, HTTPException, Request
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Webhook HTTP adapters require the optional 'server' extra: "
            "pip install control-plane-kit[server]"
        ) from error
    return Depends, FastAPI, Header, HTTPException, Request


def create_webhook_delivery_app(
    service: WebhookDeliveryService,
    *,
    identity_attestation_token: str,
    readiness: Callable[[], bool],
):
    """Mount the canonical application service without owning persistence."""

    if not isinstance(service, WebhookDeliveryService):
        raise TypeError("webhook app requires WebhookDeliveryService")
    if not identity_attestation_token or len(identity_attestation_token.encode()) > 4_096:
        raise ValueError("webhook identity attestation token is required")
    Depends, FastAPI, Header, HTTPException, Request = _require_fastapi()
    app = FastAPI(title="control-plane-kit webhook delivery")

    def authority(
        x_cpk_identity_attestation: str | None = Header(default=None),
        x_cpk_authenticated_subject: str | None = Header(default=None),
        x_cpk_authenticated_workspace: str | None = Header(default=None),
        x_cpk_webhook_scopes: str | None = Header(default=None),
    ) -> WebhookAuthority:
        supplied = x_cpk_identity_attestation or ""
        if len(supplied.encode()) > 4_096 or not hmac.compare_digest(
            supplied,
            identity_attestation_token,
        ):
            raise HTTPException(status_code=401, detail="unauthorized")
        if not all(
            (
                x_cpk_authenticated_subject,
                x_cpk_authenticated_workspace,
                x_cpk_webhook_scopes,
            )
        ):
            raise HTTPException(status_code=400, detail="invalid webhook identity")
        if any(
            len(value.encode()) > limit
            for value, limit in (
                (x_cpk_authenticated_subject, 128),
                (x_cpk_authenticated_workspace, 128),
                (x_cpk_webhook_scopes, 1_024),
            )
        ):
            raise HTTPException(status_code=400, detail="invalid webhook identity")
        try:
            return webhook_authority_from_descriptor(
                {
                    "actor_id": x_cpk_authenticated_subject,
                    "workspace_id": x_cpk_authenticated_workspace,
                    "scopes": x_cpk_webhook_scopes.split(","),
                }
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="invalid webhook identity") from error

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/health/ready")
    def ready():
        try:
            available = readiness()
        except Exception:
            available = False
        if not available:
            raise HTTPException(status_code=503, detail="webhook delivery is not ready")
        return {"status": "ready"}

    @app.post("/__deploy/webhooks")
    async def enqueue(request: Request, caller=Depends(authority)):
        value = await _body(request, HTTPException)
        try:
            command = EnqueueWebhook(webhook_intent_from_descriptor(value), caller)
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="invalid webhook enqueue command") from error
        return _execute(service, command, HTTPException)

    @app.post("/__deploy/webhooks/{delivery_id}/claims")
    async def claim(delivery_id: str, request: Request, caller=Depends(authority)):
        value = await _body(request, HTTPException)
        _exact(value, {"command_id", "worker_id", "lease_seconds"}, HTTPException)
        try:
            command = ClaimWebhook(
                _text(value, "command_id"),
                WebhookDeliveryIdentity(caller.workspace_id, delivery_id),
                _text(value, "worker_id"),
                _integer(value, "lease_seconds"),
                caller,
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="invalid webhook claim command") from error
        return _execute(service, command, HTTPException)

    @app.post("/__deploy/webhooks/{delivery_id}/release")
    async def release(delivery_id: str, request: Request, caller=Depends(authority)):
        value = await _body(request, HTTPException)
        _exact(value, {"command_id", "claim_id", "worker_id"}, HTTPException)
        try:
            command = ReleaseWebhookClaim(
                _text(value, "command_id"),
                WebhookDeliveryIdentity(caller.workspace_id, delivery_id),
                _text(value, "claim_id"),
                _text(value, "worker_id"),
                caller,
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="invalid webhook release command") from error
        return _execute(service, command, HTTPException)

    @app.post("/__deploy/webhooks/{delivery_id}/dispatch")
    async def dispatch(delivery_id: str, request: Request, caller=Depends(authority)):
        value = await _body(request, HTTPException)
        _exact(value, {"command_id", "claim_id", "worker_id"}, HTTPException)
        try:
            command = DispatchWebhook(
                _text(value, "command_id"),
                WebhookDeliveryIdentity(caller.workspace_id, delivery_id),
                _text(value, "claim_id"),
                _text(value, "worker_id"),
                caller,
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="invalid webhook dispatch command") from error
        return _execute(service, command, HTTPException)

    @app.post("/__deploy/webhooks/{delivery_id}/recovery")
    async def recover(delivery_id: str, request: Request, caller=Depends(authority)):
        value = await _body(request, HTTPException)
        _exact(value, {"command_id"}, HTTPException)
        try:
            command = RecoverWebhook(
                _text(value, "command_id"),
                WebhookDeliveryIdentity(caller.workspace_id, delivery_id),
                caller,
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="invalid webhook recovery command") from error
        return _execute(service, command, HTTPException)

    @app.get("/__deploy/webhooks/{delivery_id}")
    def read(delivery_id: str, caller=Depends(authority)):
        try:
            result = service.read(
                WebhookDeliveryIdentity(caller.workspace_id, delivery_id),
                caller,
            )
        except WebhookAuthorizationError as error:
            raise HTTPException(status_code=403, detail="webhook operation denied") from error
        except WebhookStateConflict as error:
            raise HTTPException(status_code=404, detail="webhook delivery not found") from error
        return _bounded_response(
            {
                **result.descriptor(),
            },
            HTTPException,
        )

    return app


async def _body(request, http_exception) -> Mapping[str, object]:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError as error:
            raise http_exception(status_code=400, detail="invalid content length") from error
        if length < 0 or length > MAX_WEBHOOK_API_REQUEST_BYTES:
            raise http_exception(status_code=413, detail="request entity too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_WEBHOOK_API_REQUEST_BYTES:
            raise http_exception(status_code=413, detail="request entity too large")
        chunks.append(chunk)
    try:
        value = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise http_exception(status_code=400, detail="invalid webhook command") from error
    if not isinstance(value, Mapping):
        raise http_exception(status_code=400, detail="invalid webhook command")
    return value


def _execute(service, command, http_exception) -> dict[str, object]:
    try:
        result = service.execute(command)
    except WebhookAuthorizationError as error:
        raise http_exception(status_code=403, detail="webhook operation denied") from error
    except WebhookCommandConflict as error:
        raise http_exception(status_code=409, detail="webhook command conflicts") from error
    except WebhookStateConflict as error:
        raise http_exception(status_code=409, detail="webhook state conflicts") from error
    return _bounded_response(_command_result_descriptor(result), http_exception)


def _command_result_descriptor(result: WebhookCommandResult) -> dict[str, object]:
    return {
        "delivery": webhook_delivery_descriptor(result.state),
        "replayed": result.replayed,
        "external_effect_attempted": result.external_effect_attempted,
    }


def _bounded_response(value: dict[str, object], http_exception) -> dict[str, object]:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_WEBHOOK_API_RESPONSE_BYTES:
        raise http_exception(status_code=500, detail="webhook response exceeds bound")
    return value


def _exact(value: Mapping[str, object], keys: set[str], http_exception) -> None:
    if set(value) != keys:
        raise http_exception(status_code=400, detail="invalid webhook command shape")


def _text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise TypeError(f"webhook {key} must be text")
    return item


def _integer(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if type(item) is not int:
        raise TypeError(f"webhook {key} must be an integer")
    return item
