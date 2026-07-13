"""FastAPI adapter for common deploy block control routes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from control_plane_kit.capabilities import CapabilityName, capability_named
from control_plane_kit.control_routes import DEFAULT_CONTROL_PREFIX
from control_plane_kit.servers._fastapi import require_fastapi

StatusProvider = Callable[[], Mapping[str, object]]
LogProvider = Callable[[], list[str]]


@dataclass
class BlockControlState:
    """Mutable control state for one running block server.

    This state object deliberately contains only protocol state: capabilities,
    status, logs, downstream targets, active target, and observer endpoints. The
    application data path belongs to the concrete block server, not to this
    reusable control adapter.
    """

    block_id: str
    capabilities: tuple[CapabilityName, ...] = ()
    targets: dict[str, str] = field(default_factory=dict)
    active_target: str = ""
    observers: dict[str, str] = field(default_factory=dict)
    status_provider: StatusProvider | None = None
    log_provider: LogProvider | None = None

    def capabilities_payload(self) -> dict[str, object]:
        """Return JSON-friendly capability descriptors."""

        return {
            "block_id": self.block_id,
            "capabilities": [
                capability_named(capability).as_descriptor()
                for capability in self.capabilities
            ],
        }

    def health_payload(self) -> dict[str, str]:
        """Return a small health payload for control-plane callers."""

        return {"status": "ok", "block_id": self.block_id}

    def status_payload(self) -> dict[str, object]:
        """Return runtime status using the provider when present."""

        if self.status_provider is not None:
            return dict(self.status_provider())
        return {
            "block_id": self.block_id,
            "active_target": self.active_target,
            "target_count": len(self.targets),
            "observer_count": len(self.observers),
        }

    def logs_payload(self) -> dict[str, object]:
        """Return log lines using the provider when present."""

        if self.log_provider is None:
            return {"block_id": self.block_id, "lines": []}
        return {"block_id": self.block_id, "lines": self.log_provider()}

    def targets_payload(self) -> dict[str, object]:
        """Return downstream targets in deterministic order."""

        return {
            "block_id": self.block_id,
            "active_target": self.active_target,
            "targets": dict(sorted(self.targets.items())),
        }

    def replace_targets(self, targets: Mapping[str, str]) -> dict[str, object]:
        """Replace downstream targets and clear stale active target state."""

        self.targets = dict(targets)
        if self.active_target and self.active_target not in self.targets:
            self.active_target = ""
        return self.targets_payload()

    def set_target(self, target_id: str, url: str) -> dict[str, object]:
        """Add or replace one downstream target without changing the active target."""

        if not target_id:
            raise ValueError("target_id is required")
        if not url:
            raise ValueError("url is required")
        self.targets[target_id] = url
        return self.targets_payload()

    def remove_target(self, target_id: str) -> dict[str, object]:
        """Remove one downstream target and clear active target if needed."""

        if target_id not in self.targets:
            raise KeyError("unknown target")
        del self.targets[target_id]
        if self.active_target == target_id:
            self.active_target = ""
        return self.targets_payload()

    def set_active_target(self, target_id: str) -> dict[str, str]:
        """Set the active target or raise ``KeyError`` for unknown targets."""

        if target_id not in self.targets:
            raise KeyError("unknown target")
        self.active_target = target_id
        return {"block_id": self.block_id, "active_target": self.active_target}

    def drain_target(self, target_id: str) -> dict[str, str]:
        """Return drain intent or raise ``KeyError`` for unknown targets."""

        if target_id not in self.targets:
            raise KeyError("unknown target")
        return {"block_id": self.block_id, "draining_target": target_id}

    def observers_payload(self) -> dict[str, object]:
        """Return observer side-channel targets in deterministic order."""

        return {"block_id": self.block_id, "observers": dict(sorted(self.observers.items()))}

    def replace_observers(self, observers: Mapping[str, str]) -> dict[str, object]:
        """Replace observer side-channel targets."""

        self.observers = dict(observers)
        return self.observers_payload()


def add_block_control_routes(
    app,
    state: BlockControlState,
    *,
    token: str = "",
    control_prefix: str = DEFAULT_CONTROL_PREFIX,
):
    """Attach common block control routes to an existing FastAPI app."""

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

    @app.get(f"{prefix}/capabilities", dependencies=[Depends(require_control_token)])
    def capabilities() -> dict[str, object]:
        return state.capabilities_payload()

    @app.get(f"{prefix}/health", dependencies=[Depends(require_control_token)])
    def health() -> dict[str, str]:
        return state.health_payload()

    @app.get(f"{prefix}/status", dependencies=[Depends(require_control_token)])
    def status() -> dict[str, object]:
        return state.status_payload()

    @app.get(f"{prefix}/logs", dependencies=[Depends(require_control_token)])
    def logs() -> dict[str, object]:
        return state.logs_payload()

    @app.get(f"{prefix}/targets", dependencies=[Depends(require_control_token)])
    def targets() -> dict[str, object]:
        return state.targets_payload()

    @app.post(f"{prefix}/targets", dependencies=[Depends(require_control_token)])
    def replace_targets(targets: dict[str, str]) -> dict[str, object]:
        return state.replace_targets(targets)

    @app.post(f"{prefix}/active-target", dependencies=[Depends(require_control_token)])
    def active_target(payload: dict[str, str]) -> dict[str, str]:
        try:
            return state.set_active_target(payload.get("target_id", ""))
        except KeyError as exc:
            raise HTTPException(status_code=400, detail="unknown target") from exc

    @app.post(f"{prefix}/drain-target", dependencies=[Depends(require_control_token)])
    def drain_target(payload: dict[str, str]) -> dict[str, str]:
        try:
            return state.drain_target(payload.get("target_id", ""))
        except KeyError as exc:
            raise HTTPException(status_code=400, detail="unknown target") from exc

    @app.get(f"{prefix}/observers", dependencies=[Depends(require_control_token)])
    def observers() -> dict[str, object]:
        return state.observers_payload()

    @app.post(f"{prefix}/observers", dependencies=[Depends(require_control_token)])
    def replace_observers(observers: dict[str, str]) -> dict[str, object]:
        return state.replace_observers(observers)

    return app


def create_block_control_app(
    state: BlockControlState,
    *,
    token: str = "",
    control_prefix: str = DEFAULT_CONTROL_PREFIX,
):
    """Return a FastAPI app implementing common block control routes.

    When ``token`` is non-empty, all protocol routes require either
    ``Authorization: Bearer <token>`` or ``X-Control-Plane-Token: <token>``.
    The adapter only defines control routes; application traffic should be
    mounted or implemented separately by concrete block servers.
    """

    _Depends, FastAPI, _Header, _HTTPException, _Request = require_fastapi()
    app = FastAPI(title=f"Control Plane Kit Block: {state.block_id}", version="0.1.0")
    return add_block_control_routes(
        app,
        state,
        token=token,
        control_prefix=control_prefix,
    )
