"""Bounded live inventory under current, exact durable workspace grants."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from hashlib import sha256
import json

from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind
from control_plane_kit_core.types import WorkspaceLifecycle
from control_plane_kit_operations.workspace_permissions import _identity, _text


class WorkspaceInventoryError(ValueError):
    """Fixed public failure without persisted policy or provider details."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


@dataclass(frozen=True)
class WorkspaceInventoryCursor:
    """Opaque seek input, not a signature or a grant of authority."""

    installation_id: str
    identity: PrincipalIdentity
    after_workspace_id: str

    def __post_init__(self):
        _text(self.installation_id)
        _identity(self.identity)
        _text(self.after_workspace_id)
        if "\x00" in self.after_workspace_id:
            raise ValueError("workspace cursor anchor is malformed")

    def _scope(self):
        return sha256(_json({"installation_id": self.installation_id,
            "identity": self.identity.descriptor(), "profile": "durable-operator-workspaces-v1"})).hexdigest()

    def encode(self):
        payload = {"v": 1, "scope": self._scope(), "after": self.after_workspace_id}
        return base64.urlsafe_b64encode(_json(payload)).rstrip(b"=").decode("ascii")

    @classmethod
    def decode(cls, token, *, installation_id, identity):
        invalid = False
        try:
            if type(token) is not str or not 1 <= len(token) <= 8192:
                raise ValueError
            raw = base64.b64decode(token.encode("ascii") + b"=" * (-len(token) % 4),
                                   altchars=b"-_", validate=True)
            value = json.loads(raw)
            if (type(value) is not dict or set(value) != {"v", "scope", "after"}
                    or type(value["v"]) is not int or value["v"] != 1):
                raise ValueError
            cursor = cls(installation_id, identity, value["after"])
            if value["scope"] != cursor._scope() or cursor.encode() != token:
                raise ValueError
        except (ValueError, TypeError, UnicodeError, RecursionError):
            invalid = True
        if invalid:
            raise WorkspaceInventoryError(400, "Workspace list cursor is malformed")
        return cursor


@dataclass(frozen=True)
class WorkspaceInventoryItem:
    workspace_id: str
    name: str
    lifecycle: WorkspaceLifecycle

    def __post_init__(self):
        _text(self.workspace_id)
        if (type(self.name) is not str or not self.name or len(self.name) > 513
                or type(self.lifecycle) is not WorkspaceLifecycle):
            raise ValueError("workspace summary is malformed")

    def descriptor(self):
        return {"workspace_id": self.workspace_id,
            "name": self.name if len(self.name) <= 512 else self.name[:511] + "…",
            "lifecycle": self.lifecycle.value}


@dataclass(frozen=True)
class WorkspaceInventoryPage:
    items: tuple[WorkspaceInventoryItem, ...]
    next_cursor: str | None

    def descriptor(self):
        return {"items": [item.descriptor() for item in self.items], "next_cursor": self.next_cursor}


class WorkspaceInventoryReadService:
    """Uses durable policy alone; static grants and administrator status add no rows."""

    def __init__(self, unit_of_work_factory, *, installation_id):
        _text(installation_id)
        self._unit_of_work_factory = unit_of_work_factory
        self.installation_id = installation_id

    def read(self, principal, *, limit=25, after=None):
        if (type(principal) is not AuthenticatedPrincipal
                or principal.identity.kind is not PrincipalKind.OPERATOR):
            raise WorkspaceInventoryError(403, "Workspace list access is denied")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise WorkspaceInventoryError(400, "Workspace list limit is malformed")
        cursor = None if after is None else WorkspaceInventoryCursor.decode(after,
            installation_id=self.installation_id, identity=principal.identity)
        failed = False
        try:
            with self._unit_of_work_factory() as unit_of_work:
                store = unit_of_work.stores.workspace_permissions
                store.require_installation(self.installation_id)
                rows, anchored = store.readable_workspaces(self.installation_id, principal.identity,
                    limit=limit + 1, after=None if cursor is None else cursor.after_workspace_id)
                items = tuple(WorkspaceInventoryItem(grant.workspace_id, name, WorkspaceLifecycle(lifecycle))
                              for grant, name, lifecycle in rows)
        except Exception:
            failed = True
        if failed:
            raise WorkspaceInventoryError(503, "Workspace list unavailable")
        if not anchored:
            raise WorkspaceInventoryError(409, "Workspace list changed; refresh the first page")
        next_cursor = None
        if len(items) > limit:
            next_cursor = WorkspaceInventoryCursor(self.installation_id, principal.identity,
                                                    items[limit - 1].workspace_id).encode()
        return WorkspaceInventoryPage(items[:limit], next_cursor)
