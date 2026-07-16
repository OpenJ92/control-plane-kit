"""Canonical durable descriptors for the closed activity-plan algebra."""

from __future__ import annotations

import json
from collections.abc import Mapping

from control_plane_kit.activity_plan import (
    ActivityDependency,
    ActivityId,
    ActivityImpact,
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
from control_plane_kit.graph_changes import DiffSubject, FieldSubject, StructuralField
from control_plane_kit.validation import EdgeSubject, GraphSubject, NodeSubject, RuntimeSubject


ACTIVITY_PLAN_SCHEMA = "control-plane-kit.activity-plan"
ACTIVITY_PLAN_VERSION = 1


class ActivityPlanDescriptorError(ValueError):
    """Base error for the durable activity-plan descriptor boundary."""


class MalformedActivityPlanDescriptor(ActivityPlanDescriptorError):
    """Raised when descriptor data has the wrong shape or primitive value."""


class UnknownActivityPlanVariant(ActivityPlanDescriptorError):
    """Raised when a descriptor names an unknown closed algebra variant."""


class LossyActivityPlanDescriptor(ActivityPlanDescriptorError):
    """Raised when a descriptor contains data the typed algebra cannot preserve."""


class ActivityPlanDescriptorCodec:
    """Encode and decode the one versioned activity-plan descriptor language."""

    def encode(self, plan: ActivityPlan) -> dict[str, object]:
        if not isinstance(plan, ActivityPlan):
            raise MalformedActivityPlanDescriptor("encode requires ActivityPlan")
        return {
            "schema": ACTIVITY_PLAN_SCHEMA,
            "version": ACTIVITY_PLAN_VERSION,
            "activities": [self._encode_activity(activity) for activity in plan.activities],
        }

    def dumps(self, plan: ActivityPlan) -> str:
        """Render deterministic compact JSON for hashing, logs, and persistence."""

        return json.dumps(self.encode(plan), sort_keys=True, separators=(",", ":"))

    def decode(self, descriptor: Mapping[str, object]) -> ActivityPlan:
        top = _mapping(descriptor, "activity plan")
        if _text(top, "schema") != ACTIVITY_PLAN_SCHEMA:
            raise UnknownActivityPlanVariant("unknown activity plan schema")
        version = top.get("version")
        if type(version) is not int:
            raise MalformedActivityPlanDescriptor("activity plan version must be an integer")
        if version != ACTIVITY_PLAN_VERSION:
            raise UnknownActivityPlanVariant(f"unsupported activity plan version {version!r}")
        plan = ActivityPlan(
            tuple(
                self._decode_activity(_mapping(value, "activity"))
                for value in _list(top.get("activities"), "activities")
            )
        )
        if self.encode(plan) != _json_value(top):
            raise LossyActivityPlanDescriptor(
                "activity plan descriptor does not round-trip through the typed codec"
            )
        return plan

    def _encode_activity(self, activity: PlannedActivity) -> dict[str, object]:
        return {
            "activity_id": activity.activity_id.value,
            "operation": self._encode_operation(activity.operation),
            "dependencies": [
                dependency.predecessor.value for dependency in activity.dependencies
            ],
            "risk": activity.risk.value,
            "impact": activity.impact.value,
        }

    def _decode_activity(self, descriptor: Mapping[str, object]) -> PlannedActivity:
        try:
            risk = RiskLevel(_text(descriptor, "risk"))
            impact = ActivityImpact(_text(descriptor, "impact"))
        except ValueError as error:
            raise UnknownActivityPlanVariant(str(error)) from error
        return PlannedActivity(
            activity_id=ActivityId(_text(descriptor, "activity_id")),
            operation=self._decode_operation(_mapping(descriptor.get("operation"), "operation")),
            dependencies=tuple(
                ActivityDependency(ActivityId(_primitive_text(value, "dependency")))
                for value in _list(descriptor.get("dependencies"), "dependencies")
            ),
            risk=risk,
            impact=impact,
        )

    def _encode_operation(self, operation: object) -> dict[str, object]:
        match operation:
            case StartNode(target=target):
                return _targeted("start-node", target)
            case StopNode(target=target):
                return _targeted("stop-node", target)
            case WaitForHealthy(target=target):
                return _targeted("wait-for-healthy", target)
            case AddSocketConnection(target=target):
                return _targeted("add-socket-connection", target)
            case SwitchSocketConnection(target=target):
                return _targeted("switch-socket-connection", target)
            case RemoveSocketConnection(target=target):
                return _targeted("remove-socket-connection", target)
            case ReconcileNode(target=target):
                return _targeted("reconcile-node", target)
            case ReconcileRuntime(target=target):
                return _targeted("reconcile-runtime", target)
            case StartRuntime(target=target):
                return _targeted("start-runtime", target)
            case StopRuntime(target=target):
                return _targeted("stop-runtime", target)
            case ReviewChange(target=ChangeTarget(subject=subject), reason=reason):
                return {
                    "kind": "review-change",
                    "target": {"kind": "change", "subject": subject.descriptor()},
                    "reason": reason.value,
                }
            case _:
                raise MalformedActivityPlanDescriptor("unknown typed activity operation")

    def _decode_operation(self, descriptor: Mapping[str, object]) -> object:
        kind = _text(descriptor, "kind")
        target = _mapping(descriptor.get("target"), "operation.target")
        match kind:
            case "start-node":
                return StartNode(_node_target(target))
            case "stop-node":
                return StopNode(_node_target(target))
            case "wait-for-healthy":
                return WaitForHealthy(_node_target(target))
            case "add-socket-connection":
                return AddSocketConnection(_socket_target(target))
            case "switch-socket-connection":
                return SwitchSocketConnection(_socket_target(target))
            case "remove-socket-connection":
                return RemoveSocketConnection(_socket_target(target))
            case "reconcile-node":
                return ReconcileNode(_node_target(target))
            case "reconcile-runtime":
                return ReconcileRuntime(_runtime_target(target))
            case "start-runtime":
                return StartRuntime(_runtime_target(target))
            case "stop-runtime":
                return StopRuntime(_runtime_target(target))
            case "review-change":
                _require_kind(target, "change")
                try:
                    reason = ReviewReason(_text(descriptor, "reason"))
                except ValueError as error:
                    raise UnknownActivityPlanVariant(str(error)) from error
                return ReviewChange(
                    ChangeTarget(
                        _decode_subject(
                            _mapping(target.get("subject"), "change subject")
                        )
                    ),
                    reason,
                )
            case _:
                raise UnknownActivityPlanVariant(f"unknown activity operation {kind!r}")


def _targeted(kind: str, target: object) -> dict[str, object]:
    match target:
        case NodeTarget(node_id=node_id):
            value = {"kind": "node", "node_id": node_id}
        case RuntimeTarget(runtime_id=runtime_id):
            value = {"kind": "runtime", "runtime_id": runtime_id}
        case SocketConnectionTarget(edge_id=edge_id):
            value = {"kind": "socket-connection", "edge_id": edge_id}
        case _:
            raise MalformedActivityPlanDescriptor("unknown typed activity target")
    return {"kind": kind, "target": value}


def _node_target(value: Mapping[str, object]) -> NodeTarget:
    _require_kind(value, "node")
    return NodeTarget(_text(value, "node_id"))


def _runtime_target(value: Mapping[str, object]) -> RuntimeTarget:
    _require_kind(value, "runtime")
    return RuntimeTarget(_text(value, "runtime_id"))


def _socket_target(value: Mapping[str, object]) -> SocketConnectionTarget:
    _require_kind(value, "socket-connection")
    return SocketConnectionTarget(_text(value, "edge_id"))


def _require_kind(value: Mapping[str, object], expected: str) -> None:
    actual = _text(value, "kind")
    if actual != expected:
        raise MalformedActivityPlanDescriptor(
            f"expected {expected!r} target, got {actual!r}"
        )


def _decode_subject(value: Mapping[str, object]) -> DiffSubject:
    kind = value.get("kind")
    if kind == "graph":
        return GraphSubject()
    if kind == "runtime":
        return RuntimeSubject(_text(value, "runtime_id"))
    if kind == "node":
        return NodeSubject(_text(value, "node_id"))
    if kind == "edge":
        return EdgeSubject(_text(value, "edge_id"))
    if "field" in value:
        try:
            field = StructuralField(_text(value, "field"))
        except ValueError as error:
            raise UnknownActivityPlanVariant(str(error)) from error
        owner = _decode_subject(_mapping(value.get("owner"), "field owner"))
        if not isinstance(owner, (GraphSubject, RuntimeSubject, NodeSubject)):
            raise MalformedActivityPlanDescriptor("field owner must be graph, runtime, or node")
        key = value.get("key")
        if key is not None and not isinstance(key, str):
            raise MalformedActivityPlanDescriptor("field key must be text")
        return FieldSubject(owner, field, key)
    raise UnknownActivityPlanVariant(f"unknown change subject {kind!r}")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise MalformedActivityPlanDescriptor(f"{name} must be an object with text keys")
    return value


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise MalformedActivityPlanDescriptor(f"{name} must be a list")
    return value


def _text(value: Mapping[str, object], key: str) -> str:
    return _primitive_text(value.get(key), key)


def _primitive_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MalformedActivityPlanDescriptor(f"{name} must be non-empty text")
    return value


def _json_value(value: object) -> object:
    try:
        return json.loads(json.dumps(value, sort_keys=True))
    except (TypeError, ValueError) as error:
        raise MalformedActivityPlanDescriptor("descriptor must contain JSON values") from error


DEFAULT_ACTIVITY_PLAN_CODEC = ActivityPlanDescriptorCodec()
