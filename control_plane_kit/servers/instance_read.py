"""FastAPI adapter for read-only control-plane instance routes."""

from __future__ import annotations

from control_plane_kit.read_services import InstanceReadService, ReadModelError
from control_plane_kit.servers._fastapi import require_fastapi


def create_instance_read_app(
    service: InstanceReadService,
    *,
    token: str = "",
    api_prefix: str = "",
):
    """Return a FastAPI app exposing read-only instance views.

    The adapter is transport only. All projection, redaction, and workspace
    invariant work belongs to ``InstanceReadService``.
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

    @app.get(f"{prefix}/workspaces/{{workspace_id}}", dependencies=[Depends(require_read_token)])
    def workspace(workspace_id: str) -> dict[str, object]:
        try:
            return service.workspace(workspace_id).descriptor()
        except ReadModelError as exc:
            raise _http_error(exc, HTTPException) from exc

    @app.get(f"{prefix}/workspaces/{{workspace_id}}/graphs/current", dependencies=[Depends(require_read_token)])
    def current_graph(workspace_id: str) -> dict[str, object]:
        try:
            return service.current_graph(workspace_id).descriptor()
        except ReadModelError as exc:
            raise _http_error(exc, HTTPException) from exc

    @app.get(f"{prefix}/workspaces/{{workspace_id}}/graphs/desired", dependencies=[Depends(require_read_token)])
    def desired_graph(workspace_id: str) -> dict[str, object]:
        try:
            return service.desired_graph(workspace_id).descriptor()
        except ReadModelError as exc:
            raise _http_error(exc, HTTPException) from exc

    @app.get(f"{prefix}/workspaces/{{workspace_id}}/operator-graph", dependencies=[Depends(require_read_token)])
    def operator_graph(workspace_id: str, pointer: str = "current") -> dict[str, object]:
        try:
            return service.operator_graph(workspace_id, pointer=pointer).descriptor()
        except ReadModelError as exc:
            raise _http_error(exc, HTTPException) from exc

    @app.get(f"{prefix}/workspaces/{{workspace_id}}/activity", dependencies=[Depends(require_read_token)])
    def activity_timeline(workspace_id: str, limit: int = 50) -> dict[str, object]:
        try:
            return service.activity_timeline(workspace_id, limit=limit).descriptor()
        except ReadModelError as exc:
            raise _http_error(exc, HTTPException) from exc

    @app.get(
        f"{prefix}/workspaces/{{workspace_id}}/sessions",
        dependencies=[Depends(require_read_token)],
    )
    def open_sessions(
        workspace_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, object]:
        try:
            return service.open_sessions(
                workspace_id,
                limit=limit,
                offset=offset,
            ).descriptor()
        except ReadModelError as exc:
            raise _http_error(exc, HTTPException) from exc

    @app.get(
        f"{prefix}/workspaces/{{workspace_id}}/sessions/{{session_id}}",
        dependencies=[Depends(require_read_token)],
    )
    def session_detail(
        workspace_id: str,
        session_id: str,
        limit: int = 50,
    ) -> dict[str, object]:
        try:
            return service.session_detail(
                workspace_id,
                session_id,
                limit=limit,
            ).descriptor()
        except ReadModelError as exc:
            raise _http_error(exc, HTTPException) from exc

    @app.get(
        f"{prefix}/workspaces/{{workspace_id}}/plans/{{plan_id}}",
        dependencies=[Depends(require_read_token)],
    )
    def plan_detail(
        workspace_id: str,
        plan_id: str,
        limit: int = 50,
    ) -> dict[str, object]:
        try:
            return service.plan_detail(
                workspace_id,
                plan_id,
                limit=limit,
            ).descriptor()
        except ReadModelError as exc:
            raise _http_error(exc, HTTPException) from exc

    @app.get(
        f"{prefix}/workspaces/{{workspace_id}}/approvals/pending",
        dependencies=[Depends(require_read_token)],
    )
    def pending_approvals(
        workspace_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, object]:
        try:
            return service.pending_approvals(
                workspace_id,
                limit=limit,
                offset=offset,
            ).descriptor()
        except ReadModelError as exc:
            raise _http_error(exc, HTTPException) from exc

    @app.get(f"{prefix}/workspaces/{{workspace_id}}/observed-state", dependencies=[Depends(require_read_token)])
    def observed_state(workspace_id: str) -> dict[str, object]:
        try:
            return service.observed_state(workspace_id).descriptor()
        except ReadModelError as exc:
            raise _http_error(exc, HTTPException) from exc

    @app.get(f"{prefix}/workspaces/{{workspace_id}}/control-surface", dependencies=[Depends(require_read_token)])
    def control_surface(workspace_id: str, pointer: str = "current") -> dict[str, object]:
        try:
            return service.control_surface(workspace_id, pointer=pointer).descriptor()
        except ReadModelError as exc:
            raise _http_error(exc, HTTPException) from exc

    return app


def _http_error(error: ReadModelError, http_exception):
    message = str(error)
    status_code = 400
    if message.startswith(("missing workspace", "missing session", "missing plan")):
        status_code = 404
    elif "store is not configured" in message:
        status_code = 503
    elif (
        "references missing graph truth" in message
        or "references graph truth outside workspace" in message
        or "invalid recovery graph truth" in message
    ):
        status_code = 409
    return http_exception(status_code=status_code, detail=message)
