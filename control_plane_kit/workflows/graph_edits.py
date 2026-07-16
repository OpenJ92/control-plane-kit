"""Typed data language for desired-topology replacement commands."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from collections.abc import Callable
from uuid import uuid4

from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.stores import (
    GraphVersionRecord,
    GraphTopologyStore,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionStatus,
    PostgresUnitOfWork,
)
from control_plane_kit.workflows.commands import (
    IdempotencyKey,
    InvalidOperationCommand,
)


@dataclass(frozen=True)
class SetDesiredGraph:
    """Replace one workspace's desired topology from an expected pointer."""

    session_id: str
    workspace_id: str
    actor_id: str
    graph: DeploymentGraph
    expected_desired_graph_id: str | None
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _required("session_id", self.session_id)
        _required("workspace_id", self.workspace_id)
        _required("actor_id", self.actor_id)
        if not isinstance(self.graph, DeploymentGraph):
            raise InvalidOperationCommand("graph must be a DeploymentGraph")
        _required("graph.name", self.graph.name)
        if self.expected_desired_graph_id is not None:
            _required("expected_desired_graph_id", self.expected_desired_graph_id)

    def descriptor(self) -> dict[str, object]:
        """Describe intent safely; the shared codec owns durable graph encoding."""

        return {
            "command": "set_desired_graph",
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "expected_desired_graph_id": self.expected_desired_graph_id,
            "idempotency_key": self.idempotency_key.value,
            "graph": {
                "name": self.graph.name,
                "runtime_ids": sorted(self.graph.runtimes),
                "node_ids": sorted(self.graph.nodes),
                "edge_ids": sorted(self.graph.edges),
            },
        }


@dataclass(frozen=True)
class DesiredGraphEditResult:
    """Durable evidence returned after one desired-graph command."""

    workspace_id: str
    previous_desired_graph_id: str | None
    graph_version: GraphVersionRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        _required("workspace_id", self.workspace_id)
        if self.graph_version.workspace_id != self.workspace_id:
            raise InvalidOperationCommand(
                "graph version workspace must match result workspace"
            )
        if self.action.action_type is not OperationActionKind.SET_DESIRED_GRAPH:
            raise InvalidOperationCommand(
                "desired graph result requires SET_DESIRED_GRAPH action evidence"
            )
        evidence = self.action.payload
        if evidence.get("workspace_id") != self.workspace_id:
            raise InvalidOperationCommand("action evidence workspace must match result workspace")
        if evidence.get("desired_graph_id") != self.graph_version.graph_id:
            raise InvalidOperationCommand("action evidence graph must match graph version")
        if evidence.get("previous_desired_graph_id") != self.previous_desired_graph_id:
            raise InvalidOperationCommand("action evidence previous pointer must match result")
        if self.action.actor_id != self.graph_version.created_by:
            raise InvalidOperationCommand("graph creator must match action actor")

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "previous_desired_graph_id": self.previous_desired_graph_id,
            "desired_graph_id": self.graph_version.graph_id,
            "desired_graph_version": self.graph_version.version,
            "action_id": self.action.action_id,
            "action_ordinal": self.action.ordinal,
            "replayed": self.replayed,
        }


DesiredGraphEdit = SetDesiredGraph


class DesiredGraphCommandError(RuntimeError):
    """Base error for desired-graph command interpretation."""


class DesiredGraphWorkspaceNotFound(DesiredGraphCommandError):
    """Raised when command workspace truth does not exist."""


class DesiredGraphSessionConflict(DesiredGraphCommandError):
    """Raised when the operation session cannot own this graph edit."""


class StaleDesiredGraph(DesiredGraphCommandError):
    """Raised when an operator submits against an obsolete desired pointer."""


class DesiredGraphIdempotencyConflict(DesiredGraphCommandError):
    """Raised when one idempotency key is reused for different graph intent."""


Clock = Callable[[], str]
IdFactory = Callable[[], str]
UnitOfWorkFactory = Callable[[], PostgresUnitOfWork]


def _uuid() -> str:
    return uuid4().hex


class DesiredGraphCommandService:
    """Persist one desired-topology replacement in one Postgres transaction."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        clock: Clock,
        id_factory: IdFactory = _uuid,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_factory = id_factory

    def execute(self, command: SetDesiredGraph) -> DesiredGraphEditResult:
        fingerprint = _graph_fingerprint(command)
        with self._unit_of_work_factory() as work:
            stores = work.stores
            try:
                workspace = stores.workspace.get_for_update(command.workspace_id)
            except KeyError as error:
                raise DesiredGraphWorkspaceNotFound(
                    f"workspace {command.workspace_id!r} does not exist"
                ) from error

            try:
                session = stores.activity_history.get_session(command.session_id)
            except KeyError as error:
                raise DesiredGraphSessionConflict(
                    f"operation session {command.session_id!r} does not exist"
                ) from error
            if session.workspace_id != command.workspace_id:
                raise DesiredGraphSessionConflict(
                    "operation session and desired graph must belong to the same workspace"
                )
            replay = stores.activity_history.action_for_idempotency(
                command.session_id, command.idempotency_key.value
            )
            if replay is not None:
                return _desired_graph_replay(stores.graph_topology, replay, fingerprint)

            if session.status is not OperationSessionStatus.OPEN:
                raise DesiredGraphSessionConflict(
                    f"operation session {command.session_id!r} is {session.status.value}, not open"
                )

            if workspace.desired_graph_id != command.expected_desired_graph_id:
                raise StaleDesiredGraph(
                    "desired graph pointer changed since the operator loaded the workspace"
                )

            ordinal = stores.activity_history.next_action_ordinal(command.session_id)
            replay = stores.activity_history.action_for_idempotency(
                command.session_id, command.idempotency_key.value
            )
            if replay is not None:
                return _desired_graph_replay(stores.graph_topology, replay, fingerprint)

            graph_record = GraphVersionRecord.from_graph(
                graph_id=self._id_factory(),
                workspace_id=command.workspace_id,
                version=stores.graph_topology.next_version_for_workspace(command.workspace_id),
                graph=command.graph,
                created_by=command.actor_id,
                created_at=self._clock(),
            )
            stores.graph_topology.save(graph_record)
            stores.workspace.set_desired_graph(command.workspace_id, graph_record.graph_id)
            action = stores.activity_history.add_action(
                OperationActionRecord(
                    action_id=self._id_factory(),
                    session_id=command.session_id,
                    ordinal=ordinal,
                    action_type=OperationActionKind.SET_DESIRED_GRAPH,
                    actor_id=command.actor_id,
                    payload={
                        "workspace_id": command.workspace_id,
                        "previous_desired_graph_id": workspace.desired_graph_id,
                        "desired_graph_id": graph_record.graph_id,
                    },
                    created_at=self._clock(),
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            work.commit()
            return DesiredGraphEditResult(
                workspace_id=command.workspace_id,
                previous_desired_graph_id=workspace.desired_graph_id,
                graph_version=graph_record,
                action=action,
            )


def _graph_fingerprint(command: SetDesiredGraph) -> str:
    intent = {
        "workspace_id": command.workspace_id,
        "session_id": command.session_id,
        "actor_id": command.actor_id,
        "expected_desired_graph_id": command.expected_desired_graph_id,
        "graph": command.graph.descriptor(),
    }
    encoded = json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _desired_graph_replay(
    graph_store: GraphTopologyStore,
    action: OperationActionRecord,
    fingerprint: str,
) -> DesiredGraphEditResult:
    if action.action_type is not OperationActionKind.SET_DESIRED_GRAPH:
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
    if not isinstance(graph_id, str) or not isinstance(workspace_id, str):
        raise DesiredGraphCommandError("desired graph action evidence is incomplete")
    if previous is not None and not isinstance(previous, str):
        raise DesiredGraphCommandError("desired graph action has invalid previous pointer evidence")
    try:
        graph_record = graph_store.get(graph_id)
    except (AttributeError, KeyError) as error:
        raise DesiredGraphCommandError(
            "desired graph action references missing graph truth"
        ) from error
    return DesiredGraphEditResult(
        workspace_id=workspace_id,
        previous_desired_graph_id=previous,
        graph_version=graph_record,
        action=action,
        replayed=True,
    )


def _required(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{name} must not be empty")
