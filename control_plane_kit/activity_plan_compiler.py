"""Pure interpreter from typed structural graph differences to activity plans."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from control_plane_kit.activity_plan import (
    ActivityDependency,
    ActivityId,
    ActivityImpact,
    ActivityOperation,
    ActivityPlan,
    AddSocketConnection,
    ChangeTarget,
    NodeTarget,
    PlannedActivity,
    ReconcileNode,
    ReconcileRuntime,
    RemoveSocketConnection,
    ReviewChange,
    ReviewReason,
    RiskLevel,
    RuntimeTarget,
    SocketConnectionTarget,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    SwitchSocketConnection,
    WaitForHealthy,
)
from control_plane_kit.graph_changes import (
    AddedChange,
    AmbiguousChange,
    EdgeValue,
    DiffSubject,
    FieldSubject,
    GraphDiff,
    ModifiedChange,
    NodeValue,
    RemovedChange,
    RuntimeValue,
    StructuralChange,
    StructuralField,
    TextValue,
    UnsupportedChange,
)
from control_plane_kit.validation import (
    EdgeSubject,
    GraphSubject,
    NodeSubject,
    RuntimeSubject,
)


@dataclass
class _ActivityDraft:
    activity_id: ActivityId
    operation: ActivityOperation
    risk: RiskLevel
    impact: ActivityImpact
    dependencies: set[ActivityId] = field(default_factory=set)

    def finish(self) -> PlannedActivity:
        return PlannedActivity(
            activity_id=self.activity_id,
            operation=self.operation,
            dependencies=tuple(
                ActivityDependency(predecessor)
                for predecessor in sorted(self.dependencies)
            ),
            risk=self.risk,
            impact=self.impact,
        )


def compile_activity_plan(diff: GraphDiff) -> ActivityPlan:
    """Compile intended structural changes without persistence or runtime effects."""

    if not isinstance(diff, GraphDiff):
        raise TypeError("compile_activity_plan requires GraphDiff")

    drafts: list[_ActivityDraft] = []
    start_runtime: dict[str, _ActivityDraft] = {}
    stop_runtime: dict[str, _ActivityDraft] = {}
    start_node: dict[str, _ActivityDraft] = {}
    healthy_node: dict[str, _ActivityDraft] = {}
    stop_node: dict[str, _ActivityDraft] = {}
    reconcile_node: dict[str, _ActivityDraft] = {}
    removed_node_runtime: dict[str, str] = {}
    removed_edges: dict[str, tuple[_ActivityDraft, EdgeValue]] = {}
    node_reconciliations: dict[str, list[StructuralChange]] = {}
    runtime_reconciliations: dict[str, list[StructuralChange]] = {}

    for change in diff.changes:
        match _reconciliation_owner(change):
            case NodeSubject(node_id=node_id):
                node_reconciliations.setdefault(node_id, []).append(change)
                continue
            case RuntimeSubject(runtime_id=runtime_id):
                runtime_reconciliations.setdefault(runtime_id, []).append(change)
                continue
        created = _compile_change(change)
        drafts.extend(created)
        for draft in created:
            match draft.operation:
                case StartRuntime(target=RuntimeTarget(runtime_id=runtime_id)):
                    start_runtime[runtime_id] = draft
                case StopRuntime(target=RuntimeTarget(runtime_id=runtime_id)):
                    stop_runtime[runtime_id] = draft
                case StartNode(target=NodeTarget(node_id=node_id)):
                    start_node[node_id] = draft
                case WaitForHealthy(target=NodeTarget(node_id=node_id)):
                    healthy_node[node_id] = draft
                case StopNode(target=NodeTarget(node_id=node_id)):
                    stop_node[node_id] = draft
                    if isinstance(change, RemovedChange) and isinstance(
                        change.before, NodeValue
                    ):
                        removed_node_runtime[node_id] = change.before.node.runtime_id
                case RemoveSocketConnection(
                    target=SocketConnectionTarget(edge_id=edge_id)
                ) if isinstance(change, RemovedChange) and isinstance(
                    change.before, EdgeValue
                ):
                    removed_edges[edge_id] = (draft, change.before)

    for node_id, changes in sorted(node_reconciliations.items()):
        draft = _reconcile_node(tuple(changes), node_id)
        drafts.append(draft)
        reconcile_node[node_id] = draft
    for runtime_id, changes in sorted(runtime_reconciliations.items()):
        drafts.append(_reconcile_runtime(tuple(changes), runtime_id))

    for change in diff.changes:
        _add_dependencies(
            change,
            drafts,
            start_runtime=start_runtime,
            stop_runtime=stop_runtime,
            start_node=start_node,
            healthy_node=healthy_node,
            stop_node=stop_node,
            removed_node_runtime=removed_node_runtime,
            removed_edges=removed_edges,
        )
    for node_id, changes in node_reconciliations.items():
        reconcile = reconcile_node[node_id]
        for change in changes:
            match change:
                case ModifiedChange(
                    subject=FieldSubject(field=StructuralField.RUNTIME_MEMBERSHIP),
                    before=TextValue(value=before_runtime),
                    after=TextValue(value=after_runtime),
                ):
                    if runtime_start := start_runtime.get(after_runtime):
                        reconcile.dependencies.add(runtime_start.activity_id)
                    if runtime_stop := stop_runtime.get(before_runtime):
                        runtime_stop.dependencies.add(reconcile.activity_id)

    return ActivityPlan(tuple(draft.finish() for draft in drafts))


def _compile_change(change: StructuralChange) -> tuple[_ActivityDraft, ...]:
    match change:
        case AddedChange(
            subject=RuntimeSubject(runtime_id=runtime_id),
            after=RuntimeValue(),
        ):
            return (
                _draft(
                    change,
                    "start-runtime",
                    StartRuntime(RuntimeTarget(runtime_id)),
                    RiskLevel.LOW,
                    ActivityImpact.NON_DESTRUCTIVE,
                ),
            )
        case RemovedChange(
            subject=RuntimeSubject(runtime_id=runtime_id),
            before=RuntimeValue(),
        ):
            return (
                _draft(
                    change,
                    "stop-runtime",
                    StopRuntime(RuntimeTarget(runtime_id)),
                    RiskLevel.CRITICAL,
                    ActivityImpact.DESTRUCTIVE,
                ),
            )
        case AddedChange(
            subject=NodeSubject(node_id=node_id),
            after=NodeValue(),
        ):
            start = _draft(
                change,
                "start-node",
                StartNode(NodeTarget(node_id)),
                RiskLevel.LOW,
                ActivityImpact.NON_DESTRUCTIVE,
            )
            healthy = _draft(
                change,
                "wait-healthy",
                WaitForHealthy(NodeTarget(node_id)),
                RiskLevel.MEDIUM,
                ActivityImpact.NON_DESTRUCTIVE,
            )
            healthy.dependencies.add(start.activity_id)
            return start, healthy
        case RemovedChange(
            subject=NodeSubject(node_id=node_id),
            before=NodeValue(),
        ):
            return (
                _draft(
                    change,
                    "stop-node",
                    StopNode(NodeTarget(node_id)),
                    RiskLevel.HIGH,
                    ActivityImpact.DESTRUCTIVE,
                ),
            )
        case AddedChange(subject=EdgeSubject(edge_id=edge_id), after=EdgeValue()):
            return (
                _draft(
                    change,
                    "add-connection",
                    AddSocketConnection(SocketConnectionTarget(edge_id)),
                    RiskLevel.MEDIUM,
                    ActivityImpact.NON_DESTRUCTIVE,
                ),
            )
        case ModifiedChange(subject=EdgeSubject(edge_id=edge_id), after=EdgeValue()):
            return (
                _draft(
                    change,
                    "switch-connection",
                    SwitchSocketConnection(SocketConnectionTarget(edge_id)),
                    RiskLevel.HIGH,
                    ActivityImpact.DISRUPTIVE,
                ),
            )
        case RemovedChange(subject=EdgeSubject(edge_id=edge_id), before=EdgeValue()):
            return (
                _draft(
                    change,
                    "remove-connection",
                    RemoveSocketConnection(SocketConnectionTarget(edge_id)),
                    RiskLevel.HIGH,
                    ActivityImpact.DISRUPTIVE,
                ),
            )
        case ModifiedChange(subject=FieldSubject(owner=GraphSubject())):
            return ()
        case UnsupportedChange(subject=subject):
            return (_review(change, subject, ReviewReason.UNSUPPORTED_CHANGE),)
        case AmbiguousChange(subject=subject):
            return (_review(change, subject, ReviewReason.AMBIGUOUS_CHANGE),)
        case _:
            return (_review(change, change.subject, ReviewReason.UNSUPPORTED_CHANGE),)


def _add_dependencies(
    change: StructuralChange,
    drafts: list[_ActivityDraft],
    *,
    start_runtime: dict[str, _ActivityDraft],
    stop_runtime: dict[str, _ActivityDraft],
    start_node: dict[str, _ActivityDraft],
    healthy_node: dict[str, _ActivityDraft],
    stop_node: dict[str, _ActivityDraft],
    removed_node_runtime: dict[str, str],
    removed_edges: dict[str, tuple[_ActivityDraft, EdgeValue]],
) -> None:
    matching = [draft for draft in drafts if _change_token(change) in draft.activity_id.value]
    match change:
        case AddedChange(subject=NodeSubject(node_id=node_id), after=NodeValue(node=node)):
            if runtime := start_runtime.get(node.runtime_id):
                start_node[node_id].dependencies.add(runtime.activity_id)
        case AddedChange(subject=EdgeSubject(), after=EdgeValue(edge=edge)):
            for node_id in (edge.provider_role, edge.consumer_role):
                if healthy := healthy_node.get(node_id):
                    matching[0].dependencies.add(healthy.activity_id)
        case ModifiedChange(subject=EdgeSubject(), after=EdgeValue(edge=edge)):
            for node_id in (edge.provider_role, edge.consumer_role):
                if healthy := healthy_node.get(node_id):
                    matching[0].dependencies.add(healthy.activity_id)
        case RemovedChange(subject=NodeSubject(node_id=node_id), before=NodeValue()):
            for remove, edge_value in removed_edges.values():
                edge = edge_value.edge
                if node_id in (edge.provider_role, edge.consumer_role):
                    stop_node[node_id].dependencies.add(remove.activity_id)
        case RemovedChange(
            subject=RuntimeSubject(runtime_id=runtime_id),
        ):
            runtime_stop = stop_runtime[runtime_id]
            for node_id, node_runtime_id in removed_node_runtime.items():
                if node_runtime_id == runtime_id:
                    runtime_stop.dependencies.add(stop_node[node_id].activity_id)


def _reconciliation_owner(change: StructuralChange) -> NodeSubject | RuntimeSubject | None:
    if not isinstance(change, (AddedChange, ModifiedChange, RemovedChange)):
        return None
    if not isinstance(change.subject, FieldSubject):
        return None
    if isinstance(change.subject.owner, (NodeSubject, RuntimeSubject)):
        return change.subject.owner
    return None


def _reconcile_node(changes: tuple[StructuralChange, ...], node_id: str) -> _ActivityDraft:
    return _draft_many(
        changes,
        "reconcile-node",
        ReconcileNode(NodeTarget(node_id)),
        RiskLevel.MEDIUM,
        ActivityImpact.DISRUPTIVE,
    )


def _reconcile_runtime(
    changes: tuple[StructuralChange, ...], runtime_id: str
) -> _ActivityDraft:
    return _draft_many(
        changes,
        "reconcile-runtime",
        ReconcileRuntime(RuntimeTarget(runtime_id)),
        RiskLevel.MEDIUM,
        ActivityImpact.DISRUPTIVE,
    )


def _review(
    change: StructuralChange,
    subject: DiffSubject,
    reason: ReviewReason,
) -> _ActivityDraft:
    return _draft(
        change,
        "review-change",
        ReviewChange(ChangeTarget(subject), reason),
        RiskLevel.HIGH,
        ActivityImpact.NON_DESTRUCTIVE,
    )


def _draft(
    change: StructuralChange,
    label: str,
    operation: ActivityOperation,
    risk: RiskLevel,
    impact: ActivityImpact,
) -> _ActivityDraft:
    return _ActivityDraft(
        ActivityId(f"{label}:{_change_token(change)}"),
        operation,
        risk,
        impact,
    )


def _draft_many(
    changes: tuple[StructuralChange, ...],
    label: str,
    operation: ActivityOperation,
    risk: RiskLevel,
    impact: ActivityImpact,
) -> _ActivityDraft:
    return _ActivityDraft(
        ActivityId(f"{label}:{_changes_token(changes)}"),
        operation,
        risk,
        impact,
    )


def _change_token(change: StructuralChange) -> str:
    rendered = json.dumps(change.descriptor(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def _changes_token(changes: tuple[StructuralChange, ...]) -> str:
    rendered = json.dumps(
        [change.descriptor() for change in changes],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]
