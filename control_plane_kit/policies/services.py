"""Pure policy helpers.

Policies answer whether something is allowed.  They do not persist records,
call control routes, start runtimes, or mutate graph topology.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PolicyDecision:
    """A policy result with an inspectable reason."""

    allowed: bool
    reason: str
    required_scope: str | None = None

    @classmethod
    def allow(cls, reason: str) -> "PolicyDecision":
        return cls(allowed=True, reason=reason)

    @classmethod
    def deny(cls, reason: str, *, required_scope: str | None = None) -> "PolicyDecision":
        return cls(allowed=False, reason=reason, required_scope=required_scope)


class HubAccessPolicy:
    """Checks whether an actor can access Hub-level instance registry actions."""

    def can_register_instance(self, actor_scopes: Iterable[str]) -> PolicyDecision:
        return _require_scope(actor_scopes, "hub:instance:create")

    def can_view_instances(self, actor_scopes: Iterable[str]) -> PolicyDecision:
        return _require_scope(actor_scopes, "hub:instance:read")


class InstanceAccessPolicy:
    """Checks whether an actor can access one control-plane instance."""

    def can_read_workspace(self, actor_scopes: Iterable[str]) -> PolicyDecision:
        return _require_scope(actor_scopes, "instance:workspace:read")

    def can_edit_workspace(self, actor_scopes: Iterable[str]) -> PolicyDecision:
        return _require_scope(actor_scopes, "instance:workspace:edit")


class ApprovalPolicy:
    """Checks approval authority for plans and destructive plans."""

    def can_approve_plan(self, actor_scopes: Iterable[str], *, destructive: bool = False) -> PolicyDecision:
        if destructive:
            return _require_scope(actor_scopes, "plan:approve-destructive")
        return _require_scope(actor_scopes, "plan:approve")


class DestructiveActivityPolicy:
    """Classifies activities that need stronger approval."""

    destructive_activity_types = frozenset(
        {
            "destroy_runtime",
            "delete_retained_state",
            "drop_database",
            "rotate_secret_discarding_old",
            "switch_production_traffic",
            "delete_history",
        }
    )

    def classify(self, activity_type: str) -> PolicyDecision:
        if activity_type in self.destructive_activity_types:
            return PolicyDecision.deny(
                f"{activity_type} requires destructive approval",
                required_scope="plan:approve-destructive",
            )
        return PolicyDecision.allow(f"{activity_type} is not classified as destructive")


def _require_scope(actor_scopes: Iterable[str], required: str) -> PolicyDecision:
    scopes = set(actor_scopes)
    if required in scopes:
        return PolicyDecision.allow(f"scope {required!r} is present")
    return PolicyDecision.deny(f"scope {required!r} is missing", required_scope=required)
