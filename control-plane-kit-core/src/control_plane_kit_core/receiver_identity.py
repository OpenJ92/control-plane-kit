"""Pure logical receiver scope, independent of selected graph authority."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re

from control_plane_kit_core._node_control_public_wire import (
    canonical_json_bytes, identifier_violation, reference_violation,
)
from control_plane_kit_core.node_control import (
    NodeControlContractError, NodeControlGraphReference, NodeControlGraphReferenceRole,
)

# Exact closed canonical maxima: four 128-byte references + 32 hex characters;
# two 128-byte graph identifiers. JSON field/delimiter overhead is 91 and 52.
MAX_NODE_CONTROL_RECEIVER_TARGET_BYTES = 635
MAX_NODE_CONTROL_AUTHORITY_CONTEXT_BYTES = 308
_ERROR = "receiver identity is invalid"
_INPUT_ERRORS = (ValueError, TypeError, KeyError, AttributeError, RecursionError)
_SCOPE = (
    ("workspace_id", NodeControlGraphReferenceRole.WORKSPACE),
    ("runtime_id", NodeControlGraphReferenceRole.RUNTIME),
    ("node_id", NodeControlGraphReferenceRole.NODE),
    ("provider_socket_name", NodeControlGraphReferenceRole.PROVIDER_SOCKET),
)


class ReceiverIdentityError(NodeControlContractError):
    """Categorical refusal without input material or retained exception cause."""


def _require(condition: bool) -> None:
    if condition is not True:
        raise ValueError


def _object(value: object, keys: set[str]) -> dict:
    _require(type(value) is dict and all(type(key) is str for key in value) and set(value) == keys)
    return value


def _identifier(value: object) -> None:
    _require(type(value) is str and identifier_violation(value) is None)


def _reference(value: object, role: NodeControlGraphReferenceRole) -> None:
    _require(type(value) is NodeControlGraphReference and value.role is role)
    _identifier(value.value)
    _require(NodeControlGraphReference(role, value.value) == value)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError


def _read_document(raw: bytes, maximum: int) -> object:
    _require(type(raw) is bytes and 1 <= len(raw) <= maximum)
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)


@dataclass(frozen=True, order=True, slots=True, repr=False)
class NodeControlReceiverTarget:
    """A scoped logical endpoint, not proof of current authority or installation."""

    workspace_id: NodeControlGraphReference
    runtime_id: NodeControlGraphReference
    node_id: NodeControlGraphReference
    provider_socket_name: NodeControlGraphReference
    receiver_id: str

    def __post_init__(self) -> None:
        try:
            for name, role in _SCOPE:
                _reference(getattr(self, name), role)
            _require(type(self.receiver_id) is str and re.fullmatch(r"[0-9a-f]{32}", self.receiver_id) is not None)
            return
        except _INPUT_ERRORS:
            failure = ReceiverIdentityError(_ERROR)
        raise failure

    def descriptor(self) -> dict[str, str]:
        return {**{name: getattr(self, name).value for name, _ in _SCOPE}, "receiver_id": self.receiver_id}


@dataclass(frozen=True, order=True, slots=True, repr=False)
class NodeControlAuthorityContext:
    """Selected graph references; the Operations owner establishes their authority."""

    authored_graph_id: str
    realized_projection_id: str

    def __post_init__(self) -> None:
        try:
            _identifier(self.authored_graph_id)
            _identifier(self.realized_projection_id)
            return
        except _INPUT_ERRORS:
            failure = ReceiverIdentityError(_ERROR)
        raise failure

    def descriptor(self) -> dict[str, str]:
        return {"authored_graph_id": self.authored_graph_id, "realized_projection_id": self.realized_projection_id}


class NodeControlReceiverTargetCodec:
    """Strict bounded value decoding and canonical encoding; no profile fallback."""

    def encode(self, target: NodeControlReceiverTarget) -> dict[str, str]:
        try:
            _require(type(target) is NodeControlReceiverTarget)
            return replace(target).descriptor()
        except _INPUT_ERRORS:
            failure = ReceiverIdentityError(_ERROR)
        raise failure

    def decode(self, document: object) -> NodeControlReceiverTarget:
        try:
            value = _object(document, {name for name, _ in _SCOPE} | {"receiver_id"})
            for name, _ in _SCOPE:
                _identifier(value[name])
            return NodeControlReceiverTarget(
                *(NodeControlGraphReference(role, value[name]) for name, role in _SCOPE), value["receiver_id"])
        except _INPUT_ERRORS:
            failure = ReceiverIdentityError(_ERROR)
        raise failure

    def encode_bytes(self, target: NodeControlReceiverTarget) -> bytes:
        return canonical_json_bytes(self.encode(target))

    def decode_bytes(self, raw: bytes) -> NodeControlReceiverTarget:
        try:
            return self.decode(_read_document(raw, MAX_NODE_CONTROL_RECEIVER_TARGET_BYTES))
        except _INPUT_ERRORS:
            failure = ReceiverIdentityError(_ERROR)
        raise failure


class NodeControlAuthorityContextCodec:
    """Closed graph-context value codec, not an admission or membership check."""

    def encode(self, context: NodeControlAuthorityContext) -> dict[str, str]:
        try:
            _require(type(context) is NodeControlAuthorityContext)
            return replace(context).descriptor()
        except _INPUT_ERRORS:
            failure = ReceiverIdentityError(_ERROR)
        raise failure

    def decode(self, document: object) -> NodeControlAuthorityContext:
        try:
            value = _object(document, {"authored_graph_id", "realized_projection_id"})
            return NodeControlAuthorityContext(value["authored_graph_id"], value["realized_projection_id"])
        except _INPUT_ERRORS:
            failure = ReceiverIdentityError(_ERROR)
        raise failure

    def encode_bytes(self, context: NodeControlAuthorityContext) -> bytes:
        return canonical_json_bytes(self.encode(context))

    def decode_bytes(self, raw: bytes) -> NodeControlAuthorityContext:
        try:
            return self.decode(_read_document(raw, MAX_NODE_CONTROL_AUTHORITY_CONTEXT_BYTES))
        except _INPUT_ERRORS:
            failure = ReceiverIdentityError(_ERROR)
        raise failure


def receiver_node_control_audience(target: NodeControlReceiverTarget) -> str:
    """Derive routing text; independent exact target verification is still required."""
    try:
        checked = NodeControlReceiverTargetCodec().encode(target)
        audience = f"workload:{checked['node_id']}:{checked['provider_socket_name']}"
        _require(reference_violation(audience) is None)
        return audience
    except _INPUT_ERRORS:
        failure = ReceiverIdentityError(_ERROR)
    raise failure


__all__ = [
    "MAX_NODE_CONTROL_RECEIVER_TARGET_BYTES", "MAX_NODE_CONTROL_AUTHORITY_CONTEXT_BYTES",
    "ReceiverIdentityError", "NodeControlReceiverTarget", "NodeControlAuthorityContext",
    "NodeControlReceiverTargetCodec", "NodeControlAuthorityContextCodec", "receiver_node_control_audience",
]
