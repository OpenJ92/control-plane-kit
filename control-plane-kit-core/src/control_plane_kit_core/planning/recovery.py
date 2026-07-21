"""Pure recovery planning over graph and activity-plan values.

Recovery is a fresh transition toward a known graph, not an inverse replay of
historical runtime effects. This module attaches reviewable assessment data to
canonical ``ActivityPlan`` values and never calls a runtime interpreter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from control_plane_kit_core.planning.activity_plan import (
    ActivityImpact,
    ActivityPlan,
    AddSocketConnection,
    DestroyDataResource,
    PlannedActivity,
    ReconcileNode,
    ReconcileRuntime,
    RemoveNodeResource,
    RemoveRuntimeResource,
    RemoveSocketConnection,
    ReviewChange,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    SwitchSocketConnection,
    WaitForHealthy,
)
from control_plane_kit_core.planning.codec import DEFAULT_ACTIVITY_PLAN_CODEC
from control_plane_kit_core.planning.compiler import compile_activity_plan
from control_plane_kit_core.topology.diff import diff_graphs
from control_plane_kit_core.topology.graph import DeploymentGraph
from control_plane_kit_core.topology.validation import ValidatedGraph, validate_graph

if TYPE_CHECKING:
    from control_plane_kit_core.policies import ApprovalPolicy, ApprovalRequirement


RECOVERY_CANDIDATE_SCHEMA = "control-plane-kit.recovery-candidate"
RECOVERY_CANDIDATE_VERSION = 1


class RecoveryMode(StrEnum):
    """Closed forms of recovery request supported by the pure planner."""

    REVERSE_TRANSITION = "reverse-transition"
    RECONSTRUCTION = "reconstruction"


class RecoveryDisposition(StrEnum):
    """How much confidence graph structure alone provides for one activity."""

    TOPOLOGY_CANDIDATE = "topology-candidate"
    COMPENSATION_REQUIRED = "compensation-required"
    MANUAL_REVIEW_REQUIRED = "manual-review-required"


class RecoveryLimitationCode(StrEnum):
    """Closed operator-visible reasons recovery is not guaranteed rollback."""

    GRAPH_STATE_ONLY = "graph-state-only"
    SOURCE_STATE_UNKNOWN = "source-state-unknown"
    ADAPTER_COMPENSATION_REQUIRED = "adapter-compensation-required"
    DESTRUCTIVE_ACTIVITY = "destructive-activity"
    REVIEW_BLOCKER = "review-blocker"


@dataclass(frozen=True)
class RecoveryActivityAssessment:
    """Recovery meaning attached to one activity in the canonical plan."""

    activity_id: str
    disposition: RecoveryDisposition
    note: str

    def __post_init__(self) -> None:
        if not isinstance(self.activity_id, str) or not self.activity_id.strip():
            raise ValueError("recovery assessment activity id must be non-empty text")
        if not isinstance(self.disposition, RecoveryDisposition):
            raise TypeError("recovery assessment disposition must be RecoveryDisposition")
        if not isinstance(self.note, str) or not self.note.strip():
            raise ValueError("recovery assessment note must be non-empty text")

    def descriptor(self) -> dict[str, str]:
        return {
            "activity_id": self.activity_id,
            "disposition": self.disposition.value,
            "note": self.note,
        }


@dataclass(frozen=True)
class RecoveryLimitation:
    """A bounded statement of what the recovery candidate cannot prove."""

    code: RecoveryLimitationCode
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, RecoveryLimitationCode):
            raise TypeError("recovery limitation code must be RecoveryLimitationCode")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("recovery limitation message must be non-empty text")

    def descriptor(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message}


@dataclass(frozen=True)
class RecoveryCandidate:
    """Canonical activity plan plus explicit recovery limitations."""

    mode: RecoveryMode
    source_graph_name: str | None
    target_graph_name: str
    plan: ActivityPlan
    approval: "ApprovalRequirement"
    assessments: tuple[RecoveryActivityAssessment, ...]
    limitations: tuple[RecoveryLimitation, ...]

    def __post_init__(self) -> None:
        from control_plane_kit_core.policies import ApprovalRequirement

        if not isinstance(self.mode, RecoveryMode):
            raise TypeError("recovery candidate mode must be RecoveryMode")
        if self.source_graph_name is not None and (
            not isinstance(self.source_graph_name, str)
            or not self.source_graph_name.strip()
        ):
            raise ValueError("source graph name must be non-empty text when present")
        if not isinstance(self.target_graph_name, str) or not self.target_graph_name.strip():
            raise ValueError("target graph name must be non-empty text")
        if not isinstance(self.plan, ActivityPlan):
            raise TypeError("recovery candidate plan must be ActivityPlan")
        if not isinstance(self.approval, ApprovalRequirement):
            raise TypeError("recovery candidate approval must be ApprovalRequirement")
        if not all(
            isinstance(value, RecoveryActivityAssessment)
            for value in self.assessments
        ):
            raise TypeError("recovery assessments must be RecoveryActivityAssessment")
        if not all(isinstance(value, RecoveryLimitation) for value in self.limitations):
            raise TypeError("recovery limitations must be RecoveryLimitation")

    @property
    def requires_manual_review(self) -> bool:
        return any(
            assessment.disposition is RecoveryDisposition.MANUAL_REVIEW_REQUIRED
            for assessment in self.assessments
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "schema": RECOVERY_CANDIDATE_SCHEMA,
            "version": RECOVERY_CANDIDATE_VERSION,
            "mode": self.mode.value,
            "source_graph_name": self.source_graph_name,
            "target_graph_name": self.target_graph_name,
            "plan": DEFAULT_ACTIVITY_PLAN_CODEC.encode(self.plan),
            "approval": self.approval.descriptor(),
            "requires_manual_review": self.requires_manual_review,
            "assessments": [value.descriptor() for value in self.assessments],
            "limitations": [value.descriptor() for value in self.limitations],
        }


def plan_recovery_transition(
    current: ValidatedGraph,
    target: ValidatedGraph,
    *,
    approval_policy: "ApprovalPolicy | None" = None,
) -> RecoveryCandidate:
    """Plan ``current -> target`` as recovery without claiming inverse effects."""

    _require_validated(current, "current")
    _require_validated(target, "target")
    plan = compile_activity_plan(diff_graphs(current, target))
    return _candidate(
        mode=RecoveryMode.REVERSE_TRANSITION,
        source_graph_name=current.graph.name,
        target_graph_name=target.graph.name,
        plan=plan,
        approval_policy=approval_policy,
    )


def plan_reconstruction(
    target: ValidatedGraph,
    *,
    approval_policy: "ApprovalPolicy | None" = None,
) -> RecoveryCandidate:
    """Plan ``null -> target`` from an explicitly empty structural baseline."""

    _require_validated(target, "target")
    empty = validate_graph(
        DeploymentGraph(f"empty:{target.graph.name}"),
        codec=target.codec,
    )
    plan = compile_activity_plan(diff_graphs(empty, target))
    return _candidate(
        mode=RecoveryMode.RECONSTRUCTION,
        source_graph_name=None,
        target_graph_name=target.graph.name,
        plan=plan,
        approval_policy=approval_policy,
    )


def _candidate(
    *,
    mode: RecoveryMode,
    source_graph_name: str | None,
    target_graph_name: str,
    plan: ActivityPlan,
    approval_policy: "ApprovalPolicy | None",
) -> RecoveryCandidate:
    from control_plane_kit_core.policies import ApprovalPolicy

    assessments = tuple(_assess(activity) for activity in plan.activities)
    limitations = [_graph_state_only()]
    if mode is RecoveryMode.RECONSTRUCTION:
        limitations.append(
            RecoveryLimitation(
                RecoveryLimitationCode.SOURCE_STATE_UNKNOWN,
                "reconstruction starts from an empty topology assumption and does not "
                "prove that external resources are absent",
            )
        )
    if any(
        value.disposition is RecoveryDisposition.COMPENSATION_REQUIRED
        for value in assessments
    ):
        limitations.append(
            RecoveryLimitation(
                RecoveryLimitationCode.ADAPTER_COMPENSATION_REQUIRED,
                "one or more activities require runtime-adapter recovery or "
                "compensation semantics before execution",
            )
        )
    if any(activity.impact is ActivityImpact.DESTRUCTIVE for activity in plan.activities):
        limitations.append(
            RecoveryLimitation(
                RecoveryLimitationCode.DESTRUCTIVE_ACTIVITY,
                "the recovery transition itself contains destructive activities",
            )
        )
    if any(
        value.disposition is RecoveryDisposition.MANUAL_REVIEW_REQUIRED
        for value in assessments
    ):
        limitations.append(
            RecoveryLimitation(
                RecoveryLimitationCode.REVIEW_BLOCKER,
                "the graph compiler could not produce an executable activity for every "
                "structural change",
            )
        )
    policy = approval_policy or ApprovalPolicy()
    return RecoveryCandidate(
        mode=mode,
        source_graph_name=source_graph_name,
        target_graph_name=target_graph_name,
        plan=plan,
        approval=policy.requirement_for(plan),
        assessments=assessments,
        limitations=tuple(limitations),
    )


def _assess(activity: PlannedActivity) -> RecoveryActivityAssessment:
    activity_id = activity.activity_id.value
    match activity.operation:
        case ReviewChange():
            return RecoveryActivityAssessment(
                activity_id,
                RecoveryDisposition.MANUAL_REVIEW_REQUIRED,
                "the structural change is unsupported or ambiguous",
            )
        case StartNode() | StartRuntime() | ReconcileNode() | ReconcileRuntime():
            return RecoveryActivityAssessment(
                activity_id,
                RecoveryDisposition.COMPENSATION_REQUIRED,
                "topology can request this activity, but an adapter must define external "
                "state recovery semantics",
            )
        case StopNode() | StopRuntime():
            return RecoveryActivityAssessment(
                activity_id,
                RecoveryDisposition.COMPENSATION_REQUIRED,
                "stopping a resource cannot prove that its data or prior effects are "
                "recoverable",
            )
        case RemoveNodeResource() | RemoveRuntimeResource():
            return RecoveryActivityAssessment(
                activity_id,
                RecoveryDisposition.COMPENSATION_REQUIRED,
                "removing ephemeral compute requires reconstruction rather than "
                "assuming that stop preserved a reusable resource",
            )
        case DestroyDataResource():
            return RecoveryActivityAssessment(
                activity_id,
                RecoveryDisposition.MANUAL_REVIEW_REQUIRED,
                "explicit data destruction is not structurally reversible",
            )
        case (
            AddSocketConnection()
            | SwitchSocketConnection()
            | RemoveSocketConnection()
            | WaitForHealthy()
        ):
            return RecoveryActivityAssessment(
                activity_id,
                RecoveryDisposition.TOPOLOGY_CANDIDATE,
                "the activity is structurally representable but still requires observed "
                "runtime validation",
            )
    raise TypeError(f"unsupported canonical activity {type(activity).__name__}")


def _graph_state_only() -> RecoveryLimitation:
    return RecoveryLimitation(
        RecoveryLimitationCode.GRAPH_STATE_ONLY,
        "the candidate is derived from desired graph structure, not proof of real-world "
        "effect reversal",
    )


def _require_validated(value: object, label: str) -> None:
    if not isinstance(value, ValidatedGraph):
        raise TypeError(f"{label} must be ValidatedGraph")
    value.require_valid()
