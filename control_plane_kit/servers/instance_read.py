"""FastAPI adapter for control-plane instance read routes."""

from __future__ import annotations

from control_plane_kit.read_services import InstanceReadService
from control_plane_kit.servers._fastapi import require_fastapi


def create_instance_read_app(
    read_service: InstanceReadService,
    *,
    token: str = "",
    api_prefix: str = "/instances",
):
    """Return a FastAPI app exposing read-only instance projections.

    The adapter is intentionally thin: route handlers delegate to
    `InstanceReadService` and return read-model descriptors. It does not own
    transactions, call live block control routes, or mutate durable stores.
    """

    Depends, FastAPI, Header, HTTPException, _Request = require_fastapi()
    prefix = api_prefix.rstrip("/")
    app = FastAPI(title="Control Plane Kit Instance Read API", version="0.1.0")

    def require_read_token(
        authorization: str | None = Header(default=None),
        x_control_plane_token: str | None = Header(default=None),
    ) -> None:
        if not token:
            return
        expected = f"Bearer {token}"
        if authorization == expected or x_control_plane_token == token:
            return
        raise HTTPException(status_code=401, detail="unauthorized")

    def not_found(exc: KeyError) -> HTTPException:
        return HTTPException(status_code=404, detail=str(exc))

    @app.get(f"{prefix}/{{workspace_id}}/workspace", dependencies=[Depends(require_read_token)])
    def workspace(workspace_id: str) -> dict[str, object]:
        try:
            return read_service.workspace(workspace_id).descriptor()
        except KeyError as exc:
            raise not_found(exc) from exc

    @app.get(f"{prefix}/{{workspace_id}}/graphs/current", dependencies=[Depends(require_read_token)])
    def current_graph(workspace_id: str) -> dict[str, object]:
        try:
            graph = read_service.current_graph(workspace_id)
        except KeyError as exc:
            raise not_found(exc) from exc
        if graph is None:
            raise HTTPException(status_code=404, detail="current graph not assigned")
        return graph.descriptor()

    @app.get(f"{prefix}/{{workspace_id}}/graphs/desired", dependencies=[Depends(require_read_token)])
    def desired_graph(workspace_id: str) -> dict[str, object]:
        try:
            graph = read_service.desired_graph(workspace_id)
        except KeyError as exc:
            raise not_found(exc) from exc
        if graph is None:
            raise HTTPException(status_code=404, detail="desired graph not assigned")
        return graph.descriptor()

    @app.get(f"{prefix}/{{workspace_id}}/activity", dependencies=[Depends(require_read_token)])
    def activity(workspace_id: str, limit: int = 50) -> dict[str, object]:
        try:
            return read_service.activity_timeline(workspace_id, limit=limit).descriptor()
        except KeyError as exc:
            raise not_found(exc) from exc

    @app.get(f"{prefix}/{{workspace_id}}/observed-state", dependencies=[Depends(require_read_token)])
    def observed_state(workspace_id: str, limit: int = 100) -> dict[str, object]:
        try:
            return read_service.observed_state(workspace_id, limit=limit).descriptor()
        except KeyError as exc:
            raise not_found(exc) from exc

    @app.get(f"{prefix}/{{workspace_id}}/control-surface", dependencies=[Depends(require_read_token)])
    def control_surface(workspace_id: str) -> dict[str, object]:
        try:
            surface = read_service.control_surface(workspace_id)
        except KeyError as exc:
            raise not_found(exc) from exc
        if surface is None:
            raise HTTPException(status_code=404, detail="current graph not assigned")
        return surface.descriptor()

    return app
