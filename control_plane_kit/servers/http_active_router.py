"""FastAPI HTTP active-target router block server."""

from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.control_routes import DEFAULT_CONTROL_PREFIX
from control_plane_kit.servers._fastapi import require_fastapi
from control_plane_kit.servers.block_control import BlockControlState, add_block_control_routes

AsyncClientFactory = Callable[[], Any]


class RouterForwardingError(RuntimeError):
    """Raised when the router cannot complete a downstream request."""


def create_http_active_router_app(
    state: BlockControlState | None = None,
    *,
    block_id: str = "http-active-router",
    token: str = "",
    control_prefix: str = DEFAULT_CONTROL_PREFIX,
    client_factory: AsyncClientFactory | None = None,
):
    """Return a FastAPI app that forwards HTTP traffic to the active target.

    Non-control requests are forwarded to ``state.active_target``. Control routes
    are provided by ``add_block_control_routes`` and mutate ``state.targets`` and
    ``state.active_target``.
    """

    _Depends, FastAPI, _Header, HTTPException, Request = require_fastapi()
    from fastapi import Response

    app_state = state or BlockControlState(
        block_id,
        capabilities=(
            CapabilityName.HEALTH_CHECKABLE,
            CapabilityName.TARGET_MUTABLE,
            CapabilityName.SWITCHABLE,
            CapabilityName.DRAINABLE,
        ),
    )
    app = FastAPI(title=f"Control Plane Kit HTTP Router: {app_state.block_id}", version="0.1.0")
    add_block_control_routes(app, app_state, token=token, control_prefix=control_prefix)
    _add_router_control_routes(app, app_state, token=token, control_prefix=control_prefix)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def forward(path: str, request: Request):
        if not app_state.active_target:
            raise HTTPException(status_code=503, detail="no active target")
        target_base = app_state.targets.get(app_state.active_target)
        if target_base is None:
            raise HTTPException(status_code=503, detail="active target is not registered")
        target_url = _target_url(target_base, path, str(request.url.query))
        headers = _forward_headers(dict(request.headers))
        body = await request.body()
        try:
            response = await _forward_request(
                client_factory=client_factory,
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
            )
        except RouterForwardingError as exc:
            raise HTTPException(status_code=502, detail="active target request failed") from exc
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=_response_headers(dict(response.headers)),
            media_type=response.headers.get("content-type"),
        )

    return app


def _add_router_control_routes(app, state: BlockControlState, *, token: str, control_prefix: str):
    Depends, _FastAPI, Header, HTTPException, _Request = require_fastapi()
    prefix = control_prefix.rstrip("/")

    def require_control_token(
        authorization: str | None = Header(default=None),
        x_control_plane_token: str | None = Header(default=None),
    ) -> None:
        if not token:
            return
        expected = f"Bearer {token}"
        if authorization == expected or x_control_plane_token == token:
            return
        raise HTTPException(status_code=401, detail="unauthorized")

    def require_router(router_id: str) -> None:
        if router_id != state.block_id:
            raise HTTPException(status_code=404, detail="unknown router")

    @app.put(f"{prefix}/routers/{{router_id}}/targets/{{target_id}}", dependencies=[Depends(require_control_token)])
    def set_router_target(router_id: str, target_id: str, payload: dict[str, str]) -> dict[str, object]:
        require_router(router_id)
        try:
            return state.set_target(target_id, payload.get("url", ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete(f"{prefix}/routers/{{router_id}}/targets/{{target_id}}", dependencies=[Depends(require_control_token)])
    def remove_router_target(router_id: str, target_id: str) -> dict[str, object]:
        require_router(router_id)
        try:
            return state.remove_target(target_id)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail="unknown target") from exc

    @app.post(f"{prefix}/routers/{{router_id}}/active-target", dependencies=[Depends(require_control_token)])
    def set_router_active_target(router_id: str, payload: dict[str, str]) -> dict[str, str]:
        require_router(router_id)
        try:
            return state.set_active_target(payload.get("target_id", ""))
        except KeyError as exc:
            raise HTTPException(status_code=400, detail="unknown target") from exc

    return app


def _target_url(target_base: str, path: str, query: str) -> str:
    base = target_base if target_base.endswith("/") else f"{target_base}/"
    url = urljoin(base, path)
    if query:
        return f"{url}?{query}"
    return url


def _forward_headers(headers: dict[str, str]) -> dict[str, str]:
    blocked = {"host", "content-length"}
    return {key: value for key, value in headers.items() if key.lower() not in blocked}


def _response_headers(headers: dict[str, str]) -> dict[str, str]:
    blocked = {"content-length", "transfer-encoding", "connection", "content-encoding"}
    return {key: value for key, value in headers.items() if key.lower() not in blocked}


async def _forward_request(
    *,
    client_factory: AsyncClientFactory | None,
    method: str,
    url: str,
    headers: dict[str, str],
    content: bytes,
):
    if client_factory is None:
        import httpx

        client_factory = httpx.AsyncClient
    try:
        async with client_factory() as client:
            return await client.request(method, url, headers=headers, content=content)
    except Exception as exc:
        raise RouterForwardingError from exc
