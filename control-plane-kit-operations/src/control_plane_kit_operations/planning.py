"""Planning-stage operation commands."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.planning import compile_activity_plan
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    GraphDescriptorCodec,
    GraphDescriptorError,
    GraphValidationError,
    diff_graphs,
    validate_graph,
)
from control_plane_kit_operations.graph_authoring import (
    GraphAuthoringError,
    SetDesiredGraphCommand,
    SetDesiredGraphResult,
    set_desired_graph_in_unit_of_work,
)
from control_plane_kit_operations.records import (
    ActivityPlanRecord,
    ActivityPlanStatus,
    OperationActionRecord,
    OperationSessionStatus,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
)


class DesiredGraphCommandError(RuntimeError):
    """Base error for desired-graph command interpretation."""


class DesiredGraphIdempotencyConflict(DesiredGraphCommandError):
    """Raised when one idempotency key is reused for different graph intent."""


class DesiredGraphSessionConflict(DesiredGraphCommandError):
    """Raised when an operation session cannot own this graph edit."""


class DesiredGraphWorkspaceNotFound(DesiredGraphCommandError):
    """Raised when command workspace truth does not exist."""


class StaleDesiredGraph(DesiredGraphCommandError):
    """Raised when desired graph truth changed since the operator read it."""


class ActivityPlanningError(RuntimeError):
    """Base error for transactional activity-plan derivation."""


class ActivityPlanningGraphInvalid(ActivityPlanningError):
    """Raised when durable graph data cannot enter the typed planning pipeline."""


class ActivityPlanningGraphStateConflict(ActivityPlanningError):
    """Raised when graph pointers are absent, stale, or cross-workspace truth."""


class ActivityPlanningIdempotencyConflict(ActivityPlanningError):
    """Raised when one idempotency key is reused for different planning intent."""


class ActivityPlanningSessionConflict(ActivityPlanningError):
    """Raised when an operation session cannot own planning intent."""


class ActivityPlanningWorkspaceNotFound(ActivityPlanningError):
    """Raised when workspace truth does not exist."""


@dataclass(frozen=True)
class SetDesiredGraph:
    """Replace one workspace's desired topology through an operation session."""

    session_id: str
    workspace_id: str
    actor_id: str
    graph: DeploymentGraph
    expected_desired_graph_id: str | None
    idempotency_key: IdempotencyKey
    expected_desired_realized_projection_id: str | None = None
    expected_desired_graph_revision: int = 0

    def __post_init__(self) -> None:
        _required_text(self.session_id, "session_id")
        _required_text(self.workspace_id, "workspace_id")
        _required_text(self.actor_id, "actor_id")
        if not isinstance(self.graph, DeploymentGraph):
            raise InvalidOperationCommand("graph must be DeploymentGraph")
        if self.expected_desired_graph_id is not None:
            _required_text(
                self.expected_desired_graph_id,
                "expected_desired_graph_id",
            )
        if self.expected_desired_realized_projection_id is not None:
            _required_text(
                self.expected_desired_realized_projection_id,
                "expected_desired_realized_projection_id",
            )
        if (
            type(self.expected_desired_graph_revision) is not int
            or self.expected_desired_graph_revision < 0
        ):
            raise InvalidOperationCommand(
                "expected_desired_graph_revision must be nonnegative"
            )
        _require_idempotency_key(self.idempotency_key)

    def descriptor(self) -> dict[str, object]:
        return {
            "command": OperatorCommandKind.SET_DESIRED_GRAPH.value,
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "expected_desired_graph_id": self.expected_desired_graph_id,
            "expected_desired_realized_projection_id": (
                self.expected_desired_realized_projection_id
            ),
            "expected_desired_graph_revision": self.expected_desired_graph_revision,
            "idempotency_key": self.idempotency_key.value,
            "graph": _graph_summary(self.graph),
        }


@dataclass(frozen=True)
class DesiredGraphEditResult:
    """Durable desired-graph and operation-action evidence."""

    workspace_id: str
    previous_desired_graph_id: str | None
    graph_version_id: str
    graph_version: int
    action: OperationActionRecord
    desired_realized_projection_id: str
    desired_graph_revision: int
    replayed: bool = False

    def __post_init__(self) -> None:
        _required_text(self.workspace_id, "workspace_id")
        _required_text(self.graph_version_id, "graph_version_id")
        _required_text(
            self.desired_realized_projection_id,
            "desired_realized_projection_id",
        )
        if type(self.graph_version) is not int or self.graph_version < 1:
            raise InvalidOperationCommand("graph_version must be a positive integer")
        if type(self.desired_graph_revision) is not int or self.desired_graph_revision < 1:
            raise InvalidOperationCommand("desired_graph_revision must be positive")
        if self.action.action_type is not OperatorCommandKind.SET_DESIRED_GRAPH:
            raise InvalidOperationCommand(
                "desired graph result requires SET_DESIRED_GRAPH action evidence"
            )
        evidence = self.action.payload
        if evidence.get("workspace_id") != self.workspace_id:
            raise InvalidOperationCommand("action evidence workspace must match result")
        if evidence.get("desired_graph_id") != self.graph_version_id:
            raise InvalidOperationCommand("action evidence graph must match result")
        if (
            evidence.get("desired_realized_projection_id")
            != self.desired_realized_projection_id
        ):
            raise InvalidOperationCommand(
                "action evidence realized projection must match result"
            )
        if evidence.get("desired_graph_revision") != self.desired_graph_revision:
            raise InvalidOperationCommand(
                "action evidence desired revision must match result"
            )
        if evidence.get("previous_desired_graph_id") != self.previous_desired_graph_id:
            raise InvalidOperationCommand(
                "action evidence previous pointer must match result"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "previous_desired_graph_id": self.previous_desired_graph_id,
            "desired_graph_id": self.graph_version_id,
            "desired_graph_version": self.graph_version,
            "desired_realized_projection_id": self.desired_realized_projection_id,
            "desired_graph_revision": self.desired_graph_revision,
            "action_id": self.action.action_id,
            "action_ordinal": self.action.ordinal,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class RequestActivityPlan:
    """Request a plan for the graph pointers observed by the operator."""

    session_id: str
    workspace_id: str
    actor_id: str
    expected_current_graph_id: str
    expected_desired_graph_id: str
    idempotency_key: IdempotencyKey
    expected_current_realized_projection_id: str | None = None
    expected_desired_realized_projection_id: str | None = None
    expected_desired_graph_revision: int | None = None

    def __post_init__(self) -> None:
        _required_text(self.session_id, "session_id")
        _required_text(self.workspace_id, "workspace_id")
        _required_text(self.actor_id, "actor_id")
        _required_text(self.expected_current_graph_id, "expected_current_graph_id")
        _required_text(self.expected_desired_graph_id, "expected_desired_graph_id")
        for value, field_name in (
            (
                self.expected_current_realized_projection_id,
                "expected_current_realized_projection_id",
            ),
            (
                self.expected_desired_realized_projection_id,
                "expected_desired_realized_projection_id",
            ),
        ):
            if value is not None:
                _required_text(value, field_name)
        if (
            self.expected_desired_graph_revision is not None
            and (
                type(self.expected_desired_graph_revision) is not int
                or self.expected_desired_graph_revision < 0
            )
        ):
            raise InvalidOperationCommand(
                "expected_desired_graph_revision must be nonnegative"
            )
        _require_idempotency_key(self.idempotency_key)

    def descriptor(self) -> dict[str, object]:
        return {
            "command": OperatorCommandKind.REQUEST_ACTIVITY_PLAN.value,
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "expected_current_graph_id": self.expected_current_graph_id,
            "expected_desired_graph_id": self.expected_desired_graph_id,
            "expected_current_realized_projection_id": (
                self.expected_current_realized_projection_id
            ),
            "expected_desired_realized_projection_id": (
                self.expected_desired_realized_projection_id
            ),
            "expected_desired_graph_revision": self.expected_desired_graph_revision,
            "idempotency_key": self.idempotency_key.value,
        }


@dataclass(frozen=True)
class ActivityPlanningResult:
    """Durable plan and operation-action evidence from one command."""

    plan_record: ActivityPlanRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        if self.action.action_type is not OperatorCommandKind.REQUEST_ACTIVITY_PLAN:
            raise InvalidOperationCommand(
                "activity planning result requires REQUEST_ACTIVITY_PLAN action evidence"
            )
        if self.action.session_id != self.plan_record.session_id:
            raise InvalidOperationCommand("plan and action must belong to one session")
        evidence = self.action.payload
        if evidence.get("plan_id") != self.plan_record.plan_id:
            raise InvalidOperationCommand("action evidence must reference the plan")
        if evidence.get("base_graph_id") != self.plan_record.base_graph_id:
            raise InvalidOperationCommand("action evidence must reference base graph")
        if evidence.get("desired_graph_id") != self.plan_record.desired_graph_id:
            raise InvalidOperationCommand("action evidence must reference desired graph")
        if (
            evidence.get("base_realized_projection_id")
            != self.plan_record.base_realized_projection_id
        ):
            raise InvalidOperationCommand(
                "action evidence must reference base realized projection"
            )
        if (
            evidence.get("desired_realized_projection_id")
            != self.plan_record.desired_realized_projection_id
        ):
            raise InvalidOperationCommand(
                "action evidence must reference desired realized projection"
            )
        if (
            evidence.get("desired_graph_revision")
            != self.plan_record.desired_graph_revision
        ):
            raise InvalidOperationCommand(
                "action evidence must reference desired graph revision"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_record.plan_id,
            "session_id": self.plan_record.session_id,
            "base_graph_id": self.plan_record.base_graph_id,
            "desired_graph_id": self.plan_record.desired_graph_id,
            "base_realized_projection_id": (
                self.plan_record.base_realized_projection_id
            ),
            "desired_realized_projection_id": (
                self.plan_record.desired_realized_projection_id
            ),
            "desired_graph_revision": self.plan_record.desired_graph_revision,
            "ready_for_execution": self.plan_record.plan.ready_for_execution,
            "activity_count": len(self.plan_record.plan.activities),
            "action_id": self.action.action_id,
            "action_ordinal": self.action.ordinal,
            "replayed": self.replayed,
        }


class DesiredGraphCommandService:
    """Persist desired graph edits with operation action evidence."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], str],
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_factory = id_factory

    def execute(self, command: SetDesiredGraph) -> DesiredGraphEditResult:
        fingerprint = _desired_graph_fingerprint(command)
        with self._unit_of_work_factory() as unit_of_work:
            history = unit_of_work.stores.activity_history
            history.lock_action_idempotency(
                command.session_id,
                command.idempotency_key.value,
            )
            existing = history.action_for_idempotency(
                command.session_id,
                command.idempotency_key.value,
            )
            if existing is not None:
                result = _desired_graph_replay(unit_of_work, existing, fingerprint)
                unit_of_work.commit()
                return result
            session = _desired_session(unit_of_work, command)
            if session.status is not OperationSessionStatus.OPEN:
                raise DesiredGraphSessionConflict("operation session is not open")
            created_at = self._clock()
            try:
                graph_result = set_desired_graph_in_unit_of_work(
                    unit_of_work,
                    SetDesiredGraphCommand(
                        workspace_id=command.workspace_id,
                        actor_id=command.actor_id,
                        graph=command.graph,
                        expected_desired_graph_id=command.expected_desired_graph_id,
                        expected_desired_realized_projection_id=(
                            command.expected_desired_realized_projection_id
                        ),
                        expected_desired_graph_revision=(
                            command.expected_desired_graph_revision
                        ),
                    ),
                    graph_id=self._id_factory(),
                    created_at=created_at,
                )
            except KeyError as error:
                raise DesiredGraphWorkspaceNotFound("workspace was not found") from error
            except GraphAuthoringError as error:
                if "stale desired graph" in str(error):
                    raise StaleDesiredGraph(str(error)) from error
                raise DesiredGraphCommandError(str(error)) from error
            action = OperationActionRecord(
                action_id=self._id_factory(),
                session_id=command.session_id,
                ordinal=unit_of_work.stores.activity_history.next_action_ordinal(
                    command.session_id
                ),
                action_type=OperatorCommandKind.SET_DESIRED_GRAPH,
                actor_id=command.actor_id,
                payload={
                    "workspace_id": command.workspace_id,
                    "previous_desired_graph_id": command.expected_desired_graph_id,
                    "desired_graph_id": graph_result.graph_version.graph_id,
                    "desired_realized_projection_id": (
                        graph_result.realized_projection.projection_id
                    ),
                    "desired_graph_revision": (
                        graph_result.workspace.desired_graph_revision
                    ),
                    "product_references": [
                        reference.descriptor()
                        for reference in graph_result.product_references
                    ],
                },
                created_at=created_at,
                idempotency_key=command.idempotency_key.value,
                intent_fingerprint=fingerprint,
            )
            unit_of_work.stores.activity_history.add_action(action)
            unit_of_work.commit()
            return DesiredGraphEditResult(
                workspace_id=command.workspace_id,
                previous_desired_graph_id=command.expected_desired_graph_id,
                graph_version_id=graph_result.graph_version.graph_id,
                graph_version=graph_result.graph_version.version,
                action=action,
                desired_realized_projection_id=(
                    graph_result.realized_projection.projection_id
                ),
                desired_graph_revision=graph_result.workspace.desired_graph_revision,
            )


class ActivityPlanningCommandService:
    """Derive and persist one plan inside one Postgres transaction."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], str],
        id_factory: Callable[[], str],
        graph_codec: GraphDescriptorCodec = DEFAULT_GRAPH_CODEC,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_factory = id_factory
        self._graph_codec = graph_codec

    def execute(self, command: RequestActivityPlan) -> ActivityPlanningResult:
        fingerprint = _activity_plan_fingerprint(command)
        with self._unit_of_work_factory() as unit_of_work:
            history = unit_of_work.stores.activity_history
            history.lock_action_idempotency(
                command.session_id,
                command.idempotency_key.value,
            )
            existing = history.action_for_idempotency(
                command.session_id,
                command.idempotency_key.value,
            )
            if existing is not None:
                result = _activity_plan_replay(unit_of_work, existing, fingerprint)
                unit_of_work.commit()
                return result
            try:
                session = history.get_session_for_update(command.session_id)
            except KeyError as error:
                raise ActivityPlanningSessionConflict(
                    "operation session was not found"
                ) from error
            if session.workspace_id != command.workspace_id:
                raise ActivityPlanningSessionConflict(
                    "operation session and plan must belong to one workspace"
                )
            if session.status is not OperationSessionStatus.OPEN:
                raise ActivityPlanningSessionConflict("operation session is not open")
            try:
                workspace = unit_of_work.stores.workspaces.get_for_update(
                    command.workspace_id
                )
            except KeyError as error:
                raise ActivityPlanningWorkspaceNotFound(
                    "workspace was not found"
                ) from error
            expected_desired_revision = (
                workspace.desired_graph_revision
                if command.expected_desired_graph_revision is None
                else command.expected_desired_graph_revision
            )
            if (
                workspace.current_graph_id != command.expected_current_graph_id
                or workspace.desired_graph_id != command.expected_desired_graph_id
                or workspace.desired_graph_revision != expected_desired_revision
            ):
                raise ActivityPlanningGraphStateConflict(
                    "workspace graph pointers changed"
                )
            expected_current_projection_id = (
                command.expected_current_realized_projection_id
                or unit_of_work.stores.realized_graphs.identity_for_authored(
                    command.workspace_id,
                    command.expected_current_graph_id,
                ).projection_id
            )
            expected_desired_projection_id = (
                command.expected_desired_realized_projection_id
                or unit_of_work.stores.realized_graphs.identity_for_authored(
                    command.workspace_id,
                    command.expected_desired_graph_id,
                ).projection_id
            )
            if (
                workspace.current_realized_projection_id
                != expected_current_projection_id
                or workspace.desired_realized_projection_id
                != expected_desired_projection_id
            ):
                raise ActivityPlanningGraphStateConflict(
                    "workspace graph pointers changed"
                )
            current_record = _projection_record(
                unit_of_work,
                expected_current_projection_id,
                command.expected_current_graph_id,
                command.workspace_id,
            )
            desired_record = _projection_record(
                unit_of_work,
                expected_desired_projection_id,
                command.expected_desired_graph_id,
                command.workspace_id,
            )
            try:
                current = validate_graph(
                    self._graph_codec.decode(current_record.graph_descriptor),
                    codec=self._graph_codec,
                )
                current.require_valid()
                desired = validate_graph(
                    self._graph_codec.decode(desired_record.graph_descriptor),
                    codec=self._graph_codec,
                )
                desired.require_valid()
                plan = compile_activity_plan(diff_graphs(current, desired))
            except (GraphDescriptorError, GraphValidationError) as error:
                raise ActivityPlanningGraphInvalid(str(error)) from error
            created_at = self._clock()
            plan_record = ActivityPlanRecord(
                plan_id=self._id_factory(),
                session_id=command.session_id,
                base_graph_id=command.expected_current_graph_id,
                desired_graph_id=command.expected_desired_graph_id,
                status=ActivityPlanStatus.PLANNED,
                created_at=created_at,
                plan=plan,
                base_realized_projection_id=current_record.projection_id,
                desired_realized_projection_id=desired_record.projection_id,
                desired_graph_revision=expected_desired_revision,
            )
            unit_of_work.stores.activity_history.add_plan(plan_record)
            action = OperationActionRecord(
                action_id=self._id_factory(),
                session_id=command.session_id,
                ordinal=unit_of_work.stores.activity_history.next_action_ordinal(
                    command.session_id
                ),
                action_type=OperatorCommandKind.REQUEST_ACTIVITY_PLAN,
                actor_id=command.actor_id,
                payload={
                    "workspace_id": command.workspace_id,
                    "plan_id": plan_record.plan_id,
                    "base_graph_id": plan_record.base_graph_id,
                    "desired_graph_id": plan_record.desired_graph_id,
                    "base_realized_projection_id": (
                        plan_record.base_realized_projection_id
                    ),
                    "desired_realized_projection_id": (
                        plan_record.desired_realized_projection_id
                    ),
                    "desired_graph_revision": plan_record.desired_graph_revision,
                    "ready_for_execution": plan.ready_for_execution,
                    "activity_count": len(plan.activities),
                },
                created_at=created_at,
                idempotency_key=command.idempotency_key.value,
                intent_fingerprint=fingerprint,
            )
            unit_of_work.stores.activity_history.add_action(action)
            unit_of_work.commit()
            return ActivityPlanningResult(plan_record, action)


def _desired_session(unit_of_work: Any, command: SetDesiredGraph) -> Any:
    try:
        session = unit_of_work.stores.activity_history.get_session_for_update(
            command.session_id
        )
    except KeyError as error:
        raise DesiredGraphSessionConflict("operation session was not found") from error
    if session.workspace_id != command.workspace_id:
        raise DesiredGraphSessionConflict(
            "operation session and desired graph must belong to one workspace"
        )
    return session


def _desired_graph_replay(
    unit_of_work: Any,
    action: OperationActionRecord,
    fingerprint: str,
) -> DesiredGraphEditResult:
    if action.action_type is not OperatorCommandKind.SET_DESIRED_GRAPH:
        raise DesiredGraphIdempotencyConflict(
            "idempotency key was already used for another operation action"
        )
    if action.intent_fingerprint != fingerprint:
        raise DesiredGraphIdempotencyConflict(
            "idempotency key was already used for different desired graph intent"
        )
    graph_id = action.payload.get("desired_graph_id")
    workspace_id = action.payload.get("workspace_id")
    previous = action.payload.get("previous_desired_graph_id")
    projection_id = action.payload.get("desired_realized_projection_id")
    desired_revision = action.payload.get("desired_graph_revision")
    if not isinstance(graph_id, str) or not isinstance(workspace_id, str):
        raise DesiredGraphCommandError("desired graph action evidence is incomplete")
    if previous is not None and not isinstance(previous, str):
        raise DesiredGraphCommandError("desired graph action has invalid previous pointer")
    if not isinstance(projection_id, str) or type(desired_revision) is not int:
        raise DesiredGraphCommandError("desired graph lineage evidence is incomplete")
    graph = unit_of_work.stores.graphs.get(graph_id)
    return DesiredGraphEditResult(
        workspace_id=workspace_id,
        previous_desired_graph_id=previous,
        graph_version_id=graph.graph_id,
        graph_version=graph.version,
        action=action,
        desired_realized_projection_id=projection_id,
        desired_graph_revision=desired_revision,
        replayed=True,
    )


def _activity_plan_replay(
    unit_of_work: Any,
    action: OperationActionRecord,
    fingerprint: str,
) -> ActivityPlanningResult:
    if action.action_type is not OperatorCommandKind.REQUEST_ACTIVITY_PLAN:
        raise ActivityPlanningIdempotencyConflict(
            "idempotency key was already used for another operation action"
        )
    if action.intent_fingerprint != fingerprint:
        raise ActivityPlanningIdempotencyConflict(
            "idempotency key was already used for different planning intent"
        )
    plan_id = action.payload.get("plan_id")
    if not isinstance(plan_id, str):
        raise ActivityPlanningError("planning action evidence is incomplete")
    return ActivityPlanningResult(
        unit_of_work.stores.activity_history.get_plan(plan_id),
        action,
        replayed=True,
    )


def _projection_record(
    unit_of_work: Any,
    projection_id: str,
    authored_graph_id: str,
    workspace_id: str,
) -> Any:
    try:
        record = unit_of_work.stores.realized_graphs.get(projection_id)
    except KeyError as error:
        raise ActivityPlanningGraphStateConflict(
            f"workspace realized pointer {projection_id!r} has no graph truth"
        ) from error
    if (
        record.workspace_id != workspace_id
        or record.source_authored_graph_id != authored_graph_id
    ):
        raise ActivityPlanningGraphStateConflict(
            "realized graph projection does not match workspace authored truth"
        )
    return record


def _desired_graph_fingerprint(command: SetDesiredGraph) -> str:
    return _fingerprint(
        {
            "command": OperatorCommandKind.SET_DESIRED_GRAPH.value,
            "session_id": command.session_id,
            "workspace_id": command.workspace_id,
            "actor_id": command.actor_id,
            "expected_desired_graph_id": command.expected_desired_graph_id,
            "expected_desired_realized_projection_id": (
                command.expected_desired_realized_projection_id
            ),
            "expected_desired_graph_revision": command.expected_desired_graph_revision,
            "graph": DEFAULT_GRAPH_CODEC.encode(command.graph),
        }
    )


def _activity_plan_fingerprint(command: RequestActivityPlan) -> str:
    return _fingerprint(command.descriptor())


def _fingerprint(value: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise InvalidOperationCommand("command intent must be JSON serializable") from error
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _graph_summary(graph: DeploymentGraph) -> dict[str, object]:
    return {
        "name": graph.name,
        "runtime_ids": sorted(graph.runtimes),
        "node_ids": sorted(graph.nodes),
        "edge_ids": sorted(graph.edges),
    }


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{field} must not be empty")


def _require_idempotency_key(value: IdempotencyKey) -> None:
    if not isinstance(value, IdempotencyKey):
        raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")
