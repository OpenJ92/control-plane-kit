"""Graph-owned receiver facts and material derivation; not admission authority."""

from dataclasses import dataclass
import json
import re

from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfigurationCodec, select_receiver_node_control_configuration_artifact,
)
from control_plane_kit_core.receiver_identity import NodeControlReceiverTargetCodec
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_core.wrapper_configuration import (
    MAX_WRAPPER_CONFIGURATION_BYTES, WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT,
)


class ReceiverLifecycleStorageError(ValueError):
    """Bounded material or storage-integrity refusal without caller data."""


class ReceiverLifecycleStorageConflict(ReceiverLifecycleStorageError):
    """A reserved identity or immutable witness cannot be replaced."""


def _require(condition):
    if condition is not True:
        raise ReceiverLifecycleStorageError("receiver storage is unavailable")


def _text(value):
    _require(type(value) is str and bool(value.strip()) and len(value.encode("utf-8")) <= 2048)


def _scope(record):
    NodeControlReceiverTargetCodec().decode({name: getattr(record, name) for name in (
        "workspace_id", "runtime_id", "node_id", "provider_socket_name", "receiver_id",
    )})


@dataclass(frozen=True, slots=True)
class ReceiverIntroduction:
    workspace_id: str
    receiver_id: str
    runtime_id: str
    node_id: str
    provider_socket_name: str
    introducing_graph_id: str
    introducing_realized_projection_id: str
    introducing_action_id: str
    introducing_session_id: str
    introducing_draft_id: str | None = None
    first_accepted_action_id: str | None = None
    first_accepted_session_id: str | None = None
    retired_action_id: str | None = None
    retired_session_id: str | None = None

    def __post_init__(self):
        _scope(self)
        for name in ("introducing_graph_id", "introducing_realized_projection_id",
                     "introducing_action_id", "introducing_session_id"):
            _text(getattr(self, name))
        for name in ("introducing_draft_id", "first_accepted_action_id", "first_accepted_session_id",
                     "retired_action_id", "retired_session_id"):
            if getattr(self, name) is not None:
                _text(getattr(self, name))
        _require((self.first_accepted_action_id is None) == (self.first_accepted_session_id is None))
        _require((self.retired_action_id is None) == (self.retired_session_id is None))
        _require(self.retired_action_id is None or (self.first_accepted_action_id is not None
                                                  and self.retired_action_id != self.first_accepted_action_id))


@dataclass(frozen=True, slots=True)
class ReceiverBinding:
    workspace_id: str
    graph_id: str
    realized_projection_id: str
    runtime_id: str
    node_id: str
    provider_socket_name: str
    receiver_id: str
    selected_configuration_digest: str
    declaration_identity: str

    def __post_init__(self):
        _scope(self)
        _text(self.graph_id)
        _text(self.realized_projection_id)
        for value in (self.selected_configuration_digest, self.declaration_identity):
            _require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def derive_receiver_bindings(workspace_id, graph_id, projection_id, descriptor):
    """Index explicit selected V2 material, never infer authority from its presence.

    Other application artifacts are not searched. C owns the admission/profile
    decision; this function checks the representation C supplies and readers use.
    """
    try:
        graph = DEFAULT_GRAPH_CODEC.decode(descriptor)
        result, receiver_ids = [], set()
        for node in graph.nodes.values():
            environment = node.public_environment + node.socket_environment
            slots = tuple(item for item in environment
                          if item.name == WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT)
            if not slots:
                continue
            _require(len(slots) == 1)
            artifacts = tuple(item for item in node.configuration_artifacts if item.target_path == slots[0].value)
            _require(len(artifacts) == 1)
            raw = artifacts[0].content.encode("utf-8")
            _require(len(raw) <= MAX_WRAPPER_CONFIGURATION_BYTES)
            document = json.loads(raw, object_pairs_hook=_unique_object)
            _require(type(document) is dict)
            if document.get("profile") != "workload-node-control-configuration.v2":
                continue
            artifact = select_receiver_node_control_configuration_artifact(
                artifacts=node.configuration_artifacts, environment=environment,
                control_surfaces=node.block_spec.control_surfaces,
            )
            configured = ReceiverNodeControlConfigurationCodec().decode_bytes(artifact.content.encode("utf-8"))
            target = configured.target
            _require((target.workspace_id.value, target.runtime_id.value, target.node_id.value,
                      target.provider_socket_name.value) ==
                     (workspace_id, node.runtime_id, node.node_id, configured.declaration.surface.provider_socket_name.value))
            _require(target.receiver_id not in receiver_ids)
            receiver_ids.add(target.receiver_id)
            result.append(ReceiverBinding(workspace_id, graph_id, projection_id,
                node.runtime_id, node.node_id, target.provider_socket_name.value,
                target.receiver_id, artifact.content_digest, configured.declaration.identity().value))
        return tuple(sorted(result, key=lambda item: (item.node_id, item.provider_socket_name)))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        failure = ReceiverLifecycleStorageError("receiver storage is unavailable")
    raise failure
