"""Authorized, complete public receiver material from one committed snapshot.

This projection carries recorded facts, never cached admission permission or
provider health. Logical JSON bounds exclude HTTP and MCP transport envelopes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from contextlib import AbstractContextManager
import json
from types import MappingProxyType
from typing import Protocol

from control_plane_kit_core.configuration import ConfigurationArtifact
from control_plane_kit_core.identity import PrincipalKind, TrustedCommandContext
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.receiver_configuration import select_receiver_node_control_configuration_artifact
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.read_pages import RevisionReadScope, WorkspaceReadScope
from control_plane_kit_operations.receiver_lifecycle import (
    ReceiverBinding, ReceiverIntroduction, ReceiverLifecycleExpectation, _receiver_scope,
)
from control_plane_kit_operations.records import GraphVersionRecord, RealizedGraphProjectionRecord
from .errors import ReadModelError


_ERRORS = {
    "malformed": (400, "receiver authoring query is malformed"),
    "forbidden": (403, "receiver authoring context is forbidden"),
    "missing": (404, "receiver authoring source was not found"),
    "stale": (409, "receiver authoring context is stale"),
    "unavailable": (409, "receiver authoring context is unavailable"),
}


class ReceiverAuthoringContextError(ReadModelError):
    """Closed categorical failure without retained inputs or causal exceptions."""

    def __init__(self, category: str = "unavailable"):
        self.status, message = _ERRORS[category]
        self.category = category
        super().__init__(message)


def _body_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ReceiverAuthoringContextQuery:
    workspace_id: str
    expected: ReceiverLifecycleExpectation | None = None
    pending_draft: Mapping[str, object] | None = None

    def __post_init__(self):
        valid = False
        try:
            WorkspaceReadScope(self.workspace_id)
            if len(self.workspace_id.encode("utf-8")) > 2048:
                raise ValueError
            if self.expected is not None:
                if type(self.expected) is not ReceiverLifecycleExpectation:
                    raise ValueError
                replace(self.expected)
            if self.pending_draft is not None:
                if not isinstance(self.pending_draft, Mapping) or set(self.pending_draft) != {
                    "draft_id", "expected_head_revision",
                }:
                    raise ValueError
                pending = dict(self.pending_draft)
                RevisionReadScope(self.workspace_id, pending["draft_id"], pending["expected_head_revision"])
                if len(pending["draft_id"].encode("utf-8")) > 2048:
                    raise ValueError
                object.__setattr__(self, "pending_draft", MappingProxyType(pending))
            valid = len(_body_bytes(self.descriptor())) <= 16384
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            pass
        if not valid:
            raise ReceiverAuthoringContextError("malformed")

    def descriptor(self):
        return {"workspace_id": self.workspace_id,
                "expected": None if self.expected is None else self.expected.descriptor(),
                "pending_draft": None if self.pending_draft is None else dict(self.pending_draft)}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]):
        result = None
        try:
            if type(value) is not dict or not {"workspace_id"} <= set(value) <= {
                "workspace_id", "expected", "pending_draft",
            }:
                raise ValueError
            if len(_body_bytes(value)) > 16384:
                raise ValueError
            expected = value.get("expected")
            if expected is not None:
                names = {item.name for item in fields(ReceiverLifecycleExpectation)}
                if type(expected) is not dict or set(expected) != names:
                    raise ValueError
                expected = ReceiverLifecycleExpectation(**expected)
            result = cls(value["workspace_id"], expected, value.get("pending_draft"))
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            pass
        if result is None:
            raise ReceiverAuthoringContextError("malformed")
        return result


@dataclass(frozen=True, slots=True)
class ReceiverAuthoringMaterial:
    """Bounded graph-owned records, already validated for exact source membership."""

    graph: GraphVersionRecord
    projection: RealizedGraphProjectionRecord | None
    bindings: tuple[ReceiverBinding, ...]


class ReceiverAuthoringSnapshot(Protocol):
    """One read-only snapshot; all selectors share one transport budget."""

    def pins(self, workspace_id: str) -> ReceiverLifecycleExpectation: ...
    def draft_head(self, workspace_id: str, draft_id: str, revision: int) -> str: ...
    def material(self, workspace_id: str, graph_id: str,
                 projection_id: str | None) -> ReceiverAuthoringMaterial: ...
    def origin(self, workspace_id: str, receiver_id: str) -> ReceiverIntroduction: ...


class ReceiverAuthoringSnapshotUnitOfWork(Protocol):
    def read_snapshot(self) -> AbstractContextManager[ReceiverAuthoringSnapshot]: ...


ReceiverAuthoringSnapshotFactory = Callable[[], ReceiverAuthoringSnapshotUnitOfWork]


@dataclass(frozen=True, slots=True)
class _Receiver:
    binding: ReceiverBinding
    configuration_artifact: ConfigurationArtifact
    origin: ReceiverIntroduction
    lifecycle: str

    def descriptor(self):
        return {
            "binding": {item.name: getattr(self.binding, item.name) for item in fields(ReceiverBinding)},
            "configuration_artifact": self.configuration_artifact.descriptor(),
            "origin": {name: getattr(self.origin, name) for name in (
                "introducing_graph_id", "introducing_realized_projection_id", "introducing_action_id",
                "introducing_session_id", "introducing_draft_id", "first_accepted_action_id",
                "first_accepted_session_id",
            )},
            "lifecycle": self.lifecycle,
        }


@dataclass(frozen=True, slots=True)
class _Source:
    graph_id: str
    realized_projection_id: str | None
    receivers: tuple[_Receiver, ...]

    def descriptor(self):
        return {"graph_id": self.graph_id, "realized_projection_id": self.realized_projection_id,
                "receivers": [item.descriptor() for item in self.receivers]}


@dataclass(frozen=True, slots=True)
class _DraftSource:
    draft_id: str
    head_revision: int
    source: _Source

    def descriptor(self):
        return {"draft_id": self.draft_id, "head_revision": self.head_revision, **self.source.descriptor()}


@dataclass(frozen=True, slots=True)
class ReceiverAuthoringContext:
    """Detached immutable public material; a later command must recheck its pins."""

    workspace_id: str
    expectation: ReceiverLifecycleExpectation
    current: _Source
    desired: _Source | None
    pending_draft: _DraftSource | None

    def descriptor(self):
        return {"profile": "receiver-authoring-context.v1", "workspace_id": self.workspace_id,
                "expectation": self.expectation.descriptor(), "current": self.current.descriptor(),
                "desired": None if self.desired is None else self.desired.descriptor(),
                "pending_draft": None if self.pending_draft is None else self.pending_draft.descriptor()}


class ReceiverAuthoringContextReadService:
    def __init__(self, snapshot_factory: ReceiverAuthoringSnapshotFactory):
        self._snapshot_factory = snapshot_factory

    def read(self, query: ReceiverAuthoringContextQuery, *, context: TrustedCommandContext):
        if type(query) is not ReceiverAuthoringContextQuery:
            raise ReceiverAuthoringContextError("malformed")
        authorized = False
        try:
            if type(context) is TrustedCommandContext:
                checked = replace(context)
                authorized = (checked.workspace_id == query.workspace_id
                    and checked.principal.identity.kind is PrincipalKind.OPERATOR
                    and {PolicyScope.INSTANCE_WORKSPACE_READ, PolicyScope.DELEGATION_KEY_READ}
                    <= set(checked.granted_scopes))
        except (ValueError, TypeError, AttributeError):
            pass
        if not authorized:
            raise ReceiverAuthoringContextError("forbidden")
        category = "unavailable"
        try:
            # Revalidate a detached request before asking the factory for a UoW.
            query = replace(query)
            with self._snapshot_factory().read_snapshot() as snapshot:
                result = self._project(snapshot, query)
                if len(_body_bytes(result.descriptor())) > 1048576:
                    raise ReceiverAuthoringContextError()
            return result
        except Exception as error:
            # No SQL, decoder, or factory exception survives the public boundary.
            if type(error) is ReceiverAuthoringContextError:
                category = error.category
        raise ReceiverAuthoringContextError(category)

    @staticmethod
    def _project(snapshot: ReceiverAuthoringSnapshot, query: ReceiverAuthoringContextQuery):
        workspace = query.workspace_id
        pins = snapshot.pins(workspace)
        if query.expected is not None and query.expected != pins:
            raise ReceiverAuthoringContextError("stale")
        current = snapshot.material(workspace, pins.current_graph_id, pins.current_realized_projection_id)
        desired = None if pins.desired_graph_id is None else snapshot.material(
            workspace, pins.desired_graph_id, pins.desired_realized_projection_id)
        pending = None
        if query.pending_draft is not None:
            draft = query.pending_draft
            graph_id = snapshot.draft_head(workspace, draft["draft_id"], draft["expected_head_revision"])
            pending = snapshot.material(workspace, graph_id, None)
        materials = tuple(item for item in (current, desired, pending) if item is not None)
        if sum(len(item.bindings) for item in materials) > 64:
            raise ReceiverAuthoringContextError()
        current_scopes = {_receiver_scope(item) for item in current.bindings}
        origins = {}

        def source(material, *, is_current=False):
            rows = []
            graph = None if material.projection is None else DEFAULT_GRAPH_CODEC.decode(material.projection.graph_descriptor)
            for binding in material.bindings:
                origin = origins.get(binding.receiver_id)
                if origin is None:
                    origin = snapshot.origin(workspace, binding.receiver_id)
                    origins[binding.receiver_id] = origin
                scope = _receiver_scope(binding)
                if (_receiver_scope(origin) != scope or origin.retired_action_id is not None
                        or (is_current and origin.first_accepted_action_id is None)
                        or (origin.first_accepted_action_id is not None and scope not in current_scopes)):
                    raise ReceiverAuthoringContextError()
                node = graph.node(binding.node_id)
                artifact = select_receiver_node_control_configuration_artifact(
                    artifacts=node.configuration_artifacts,
                    environment=node.public_environment + node.socket_environment,
                    control_surfaces=node.block_spec.control_surfaces)
                if artifact.content_digest != binding.selected_configuration_digest:
                    raise ReceiverAuthoringContextError()
                rows.append(_Receiver(binding, artifact, origin,
                    "current" if origin.first_accepted_action_id is not None else "pending"))
            return _Source(material.graph.graph_id,
                None if material.projection is None else material.projection.projection_id, tuple(rows))

        current_source = source(current, is_current=True)
        desired_source = None if desired is None else source(desired)
        draft_source = None if pending is None else _DraftSource(
            query.pending_draft["draft_id"], query.pending_draft["expected_head_revision"], source(pending))
        return ReceiverAuthoringContext(workspace, pins, current_source, desired_source, draft_source)
