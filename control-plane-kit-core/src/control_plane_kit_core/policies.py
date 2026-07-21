"""Pure policy decisions for deployment topology and activity planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from control_plane_kit_core.planning import (
    ActivityImpact,
    ActivityOperation,
    ActivityPlan,
    DestroyDataResource,
    RemoveNodeResource,
    RemoveRuntimeResource,
    RiskLevel,
    SwitchSocketConnection,
)
from control_plane_kit_core.types import WorkspaceLifecycle


class PolicyScope(StrEnum):
    """Closed authority scopes used by pure policy decisions."""

    HUB_INSTANCE_CREATE = "hub:instance:create"
    HUB_INSTANCE_READ = "hub:instance:read"
    INSTANCE_WORKSPACE_READ = "instance:workspace:read"
    INSTANCE_WORKSPACE_EDIT = "instance:workspace:edit"
    PLAN_REQUEST = "plan:request"
    PLAN_APPROVE = "plan:approve"
    PLAN_APPROVE_DESTRUCTIVE = "plan:approve-destructive"


@dataclass(frozen=True, order=True)
class PolicyDecision:
    """A bounded policy result with no effect authority."""

    allowed: bool
    reason: str
    required_scope: PolicyScope | None = None

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise TypeError("policy decision allowed flag must be bool")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("policy decision reason must be a nonempty string")
        if self.required_scope is not None and not isinstance(
            self.required_scope,
            PolicyScope,
        ):
            raise TypeError("policy decision required scope must be PolicyScope")

    @classmethod
    def allow(cls, reason: str) -> "PolicyDecision":
        return cls(allowed=True, reason=reason)

    @classmethod
    def deny(
        cls,
        reason: str,
        *,
        required_scope: PolicyScope | None = None,
    ) -> "PolicyDecision":
        return cls(allowed=False, reason=reason, required_scope=required_scope)

    def descriptor(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "required_scope": (
                None if self.required_scope is None else self.required_scope.value
            ),
        }


class HubAccessPolicy:
    """Checks whether an actor can access Hub-level instance registry actions."""

    def can_register_instance(
        self,
        actor_scopes: Iterable[PolicyScope],
    ) -> PolicyDecision:
        return _require_scope(actor_scopes, PolicyScope.HUB_INSTANCE_CREATE)

    def can_view_instances(
        self,
        actor_scopes: Iterable[PolicyScope],
    ) -> PolicyDecision:
        return _require_scope(actor_scopes, PolicyScope.HUB_INSTANCE_READ)


class InstanceAccessPolicy:
    """Checks whether an actor can access one control-plane instance."""

    def can_read_workspace(
        self,
        actor_scopes: Iterable[PolicyScope],
    ) -> PolicyDecision:
        return _require_scope(actor_scopes, PolicyScope.INSTANCE_WORKSPACE_READ)

    def can_edit_workspace(
        self,
        actor_scopes: Iterable[PolicyScope],
    ) -> PolicyDecision:
        return _require_scope(actor_scopes, PolicyScope.INSTANCE_WORKSPACE_EDIT)


@dataclass(frozen=True, order=True)
class ApprovalRequirement:
    """Policy-derived authority and risk evidence for one canonical plan."""

    required_scope: PolicyScope
    max_risk: RiskLevel
    destructive: bool

    def __post_init__(self) -> None:
        if not isinstance(self.required_scope, PolicyScope):
            raise TypeError("approval requirement scope must be PolicyScope")
        if not isinstance(self.max_risk, RiskLevel):
            raise TypeError("approval requirement risk must be RiskLevel")
        if type(self.destructive) is not bool:
            raise TypeError("approval requirement destructive flag must be bool")

    def descriptor(self) -> dict[str, object]:
        return {
            "required_scope": self.required_scope.value,
            "max_risk": self.max_risk.value,
            "destructive": self.destructive,
        }


class ApprovalPolicy:
    """Checks request and approval authority for canonical activity plans."""

    def can_request_plan(
        self,
        actor_scopes: Iterable[PolicyScope],
    ) -> PolicyDecision:
        return _require_scope(actor_scopes, PolicyScope.PLAN_REQUEST)

    def can_approve_plan(
        self,
        actor_scopes: Iterable[PolicyScope],
        *,
        destructive: bool = False,
    ) -> PolicyDecision:
        required = (
            PolicyScope.PLAN_APPROVE_DESTRUCTIVE
            if destructive
            else PolicyScope.PLAN_APPROVE
        )
        return _require_scope(actor_scopes, required)

    def requirement_for(self, plan: ActivityPlan) -> ApprovalRequirement:
        """Derive immutable approval evidence from canonical plan data."""

        if not isinstance(plan, ActivityPlan):
            raise TypeError("approval requirement requires ActivityPlan")
        max_risk = max(
            (activity.risk for activity in plan.activities),
            key=_RISK_ORDER.__getitem__,
            default=RiskLevel.INFORMATIONAL,
        )
        destructive = any(
            activity.impact is ActivityImpact.DESTRUCTIVE
            for activity in plan.activities
        )
        return ApprovalRequirement(
            required_scope=(
                PolicyScope.PLAN_APPROVE_DESTRUCTIVE
                if destructive
                else PolicyScope.PLAN_APPROVE
            ),
            max_risk=max_risk,
            destructive=destructive,
        )


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
        if not isinstance(activity_type, str) or not activity_type:
            raise TypeError("activity type must be a nonempty string")
        if activity_type in self.destructive_activity_types:
            return PolicyDecision.deny(
                f"{activity_type} requires destructive approval",
                required_scope=PolicyScope.PLAN_APPROVE_DESTRUCTIVE,
            )
        return PolicyDecision.allow(
            f"{activity_type} is not classified as destructive",
        )

    def classify_operation(self, operation: ActivityOperation) -> PolicyDecision:
        activity_type = _activity_type_for_operation(operation)
        return self.classify(activity_type)


@dataclass(frozen=True, order=True)
class LifecycleRetention:
    """What remains available in one workspace lifecycle state."""

    lifecycle: WorkspaceLifecycle
    keeps_workspace_record: bool
    keeps_graph_history: bool
    keeps_activity_history: bool
    keeps_observed_state: bool
    keeps_runtime_resources: bool
    destructive: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.lifecycle, WorkspaceLifecycle):
            raise TypeError("lifecycle retention requires WorkspaceLifecycle")
        for field_name in (
            "keeps_workspace_record",
            "keeps_graph_history",
            "keeps_activity_history",
            "keeps_observed_state",
            "keeps_runtime_resources",
            "destructive",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} must be bool")

    def descriptor(self) -> dict[str, object]:
        return {
            "lifecycle": self.lifecycle.value,
            "keeps_workspace_record": self.keeps_workspace_record,
            "keeps_graph_history": self.keeps_graph_history,
            "keeps_activity_history": self.keeps_activity_history,
            "keeps_observed_state": self.keeps_observed_state,
            "keeps_runtime_resources": self.keeps_runtime_resources,
            "destructive": self.destructive,
        }


_RETENTION: dict[WorkspaceLifecycle, LifecycleRetention] = {
    WorkspaceLifecycle.CREATED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.CREATED,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=True,
        keeps_runtime_resources=False,
    ),
    WorkspaceLifecycle.RUNNING: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.RUNNING,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=True,
        keeps_runtime_resources=True,
    ),
    WorkspaceLifecycle.PAUSED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.PAUSED,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=True,
        keeps_runtime_resources=True,
    ),
    WorkspaceLifecycle.STOPPED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.STOPPED,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=True,
        keeps_runtime_resources=False,
    ),
    WorkspaceLifecycle.ARCHIVED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.ARCHIVED,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=False,
        keeps_runtime_resources=False,
    ),
    WorkspaceLifecycle.DECONSTRUCTED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.DECONSTRUCTED,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=False,
        keeps_runtime_resources=False,
    ),
    WorkspaceLifecycle.DELETED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.DELETED,
        keeps_workspace_record=False,
        keeps_graph_history=False,
        keeps_activity_history=False,
        keeps_observed_state=False,
        keeps_runtime_resources=False,
        destructive=True,
    ),
    WorkspaceLifecycle.FAILED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.FAILED,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=True,
        keeps_runtime_resources=True,
    ),
}


def retention_for(lifecycle: WorkspaceLifecycle) -> LifecycleRetention:
    """Return the retention law for one closed workspace lifecycle state."""

    if not isinstance(lifecycle, WorkspaceLifecycle):
        raise TypeError("retention lifecycle must be WorkspaceLifecycle")
    return _RETENTION[lifecycle]


def _require_scope(
    actor_scopes: Iterable[PolicyScope],
    required: PolicyScope,
) -> PolicyDecision:
    scopes = set(actor_scopes)
    if not all(isinstance(scope, PolicyScope) for scope in scopes):
        raise TypeError("actor scopes must be PolicyScope values")
    if required in scopes:
        return PolicyDecision.allow(f"scope {required.value!r} is present")
    return PolicyDecision.deny(
        f"scope {required.value!r} is missing",
        required_scope=required,
    )


def _activity_type_for_operation(operation: ActivityOperation) -> str:
    match operation:
        case DestroyDataResource():
            return "drop_database"
        case RemoveNodeResource() | RemoveRuntimeResource():
            return "delete_retained_state"
        case SwitchSocketConnection():
            return "switch_production_traffic"
        case _:
            return _snake_case(operation.__class__.__name__)


def _snake_case(value: str) -> str:
    result: list[str] = []
    for index, character in enumerate(value):
        if character.isupper() and index:
            result.append("_")
        result.append(character.lower())
    return "".join(result)


_RISK_ORDER = {
    RiskLevel.INFORMATIONAL: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}
