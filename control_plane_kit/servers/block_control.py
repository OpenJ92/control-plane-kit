"""FastAPI adapter for common deploy block control routes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from control_plane_kit.capabilities import CapabilityName, capability_named
from control_plane_kit.contracts import RuntimeContract, RuntimeMapVariable, RuntimeValueVariable
from control_plane_kit.control_routes import DEFAULT_CONTROL_PREFIX
from control_plane_kit.servers._fastapi import require_fastapi

StatusProvider = Callable[[], Mapping[str, object]]
LogProvider = Callable[[], list[str]]


class BlockControlRuntime(RuntimeContract):
    """Runtime contract for common block-control mutable state."""

    targets = RuntimeMapVariable("targets")
    active_target = RuntimeValueVariable("active_target")
    observers = RuntimeMapVariable("observers")


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
    runtime: BlockControlRuntime = field(init=False)

    def __post_init__(self) -> None:
        self.runtime = BlockControlRuntime.from_mapping({
            "targets": self.targets,
            "active_target": self.active_target,
            "observers": self.observers,
        })

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
            "active_target": self.runtime.get("active_target") or "",
            "target_count": len(self.runtime.get("targets") or {}),
            "observer_count": len(self.runtime.get("observers") or {}),
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
            "active_target": self.runtime.get("active_target") or "",
            "targets": dict(sorted((self.runtime.get("targets") or {}).items())),
        }

    def replace_targets(self, targets: Mapping[str, str]) -> dict[str, object]:
        """Replace downstream targets and clear stale active target state."""

        next_targets = dict(targets)
        active_target = self.runtime.get("active_target") or ""
        patch: dict[str, object] = {"targets": next_targets}
        if active_target and active_target not in next_targets:
            patch["active_target"] = ""
        self.runtime.apply_patch(patch)
        self.targets = dict(self.runtime.get("targets") or {})
        self.active_target = str(self.runtime.get("active_target") or "")
        return self.targets_payload()

    def set_active_target(self, target_id: str) -> dict[str, str]:
        """Set the active target or raise ``KeyError`` for unknown targets."""

        targets = self.runtime.get("targets") or {}
        if target_id not in targets:
            raise KeyError("unknown target")
        self.runtime.apply_patch({"active_target": target_id})
        self.active_target = str(self.runtime.get("active_target") or "")
        return {"block_id": self.block_id, "active_target": self.active_target}

    def drain_target(self, target_id: str) -> dict[str, str]:
        """Return drain intent or raise ``KeyError`` for unknown targets."""

        if target_id not in (self.runtime.get("targets") or {}):
            raise KeyError("unknown target")
        return {"block_id": self.block_id, "draining_target": target_id}

    def observers_payload(self) -> dict[str, object]:
        """Return observer side-channel targets in deterministic order."""

        return {"block_id": self.block_id, "observers": dict(sorted((self.runtime.get("observers") or {}).items()))}

    def replace_observers(self, observers: Mapping[str, str]) -> dict[str, object]:
        """Replace observer side-channel targets."""

        self.runtime.apply_patch({"observers": dict(observers)})
        self.observers = dict(self.runtime.get("observers") or {})
        return self.observers_payload()


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

    Depends, FastAPI, Header, HTTPException, _Request = require_fastapi()
    prefix = control_prefix.rstrip("/")
    app = FastAPI(title=f"Control Plane Kit Block: {state.block_id}", version="0.1.0")

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
