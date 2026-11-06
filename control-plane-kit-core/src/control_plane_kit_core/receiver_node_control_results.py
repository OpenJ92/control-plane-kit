"""Explicit originating-request correlation over the existing command outcome sum."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from control_plane_kit_core._node_control_public_wire import canonical_json_bytes
from control_plane_kit_core.node_control import (
    NodeControlResult, NodeControlResultCodec, NodeControlReadStateSucceeded,
    NodeControlTransitionSucceeded, NodeControlRejected, NodeControlFailed,
    NodeControlOperation, NodeControlResultStatus, ControlPlaneResultCodec, MAX_NODE_CONTROL_PAYLOAD_BYTES,
)
from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclaration
from control_plane_kit_core.receiver_node_control import (
    ReceiverNodeControlContractError, ReceiverNodeControlRequest, ReceiverNodeControlRequestCodec,
    ReceiverNodeControlRequestDigest, _INPUT_ERRORS, _ERROR, _require, _digest,
    _bounded, _canonical_document, _declaration,
)

_OUTCOMES = (NodeControlReadStateSucceeded, NodeControlTransitionSucceeded, NodeControlRejected, NodeControlFailed)
_CORRELATION_KEYS = frozenset({"profile", "request_digest"})


class ReceiverNodeControlResultProfile(StrEnum):
    V2 = "workload-node-control-result.v2"


def _context(request, declaration) -> NodeControlResultCodec:
    ReceiverNodeControlRequestCodec().encode(request)
    _declaration(declaration)
    _require(request.declaration_identity == declaration.identity()
             and request.target.provider_socket_name == declaration.surface.provider_socket_name)
    variable = next((v for v in declaration.surface.variables if v.variable_name == request.variable_name), None)
    _require(variable is not None)
    _require(request.command_codec is variable.contract_for(request.operation).command_codec)
    return NodeControlResultCodec(variable)


def _outcome_document(request, codec: NodeControlResultCodec, outcome: NodeControlResult) -> dict[str, object]:
    _require(type(outcome) in _OUTCOMES)
    _require(outcome.request_id == request.request_id and outcome.operation is request.operation)
    document = codec.encode(outcome)
    # Historical encode projects values; decode revalidates even forged nested
    # state/version/evidence. Keep the outcome algebra at its canonical owner.
    _require(codec.decode(document) == outcome)
    return document


def _document(value) -> dict[str, object]:
    codec = _context(value.request, value.declaration)
    return {**_outcome_document(value.request, codec, value.outcome),
            "profile": ReceiverNodeControlResultProfile.V2.value,
            "request_digest": value.request.canonical_digest().value}


@dataclass(frozen=True, slots=True, repr=False)
class ReceiverNodeControlResult:
    """Product of an actual validated request, its declaration and one outcome.

    Producers supply the request they processed; consumers independently retain
    that expected request. The digest correlates content, not responder identity
    or execution. Invalid requests cannot manufacture semantic results.
    """
    request: ReceiverNodeControlRequest
    declaration: WorkloadNodeControlSurfaceDeclaration
    outcome: NodeControlResult

    def __post_init__(self) -> None:
        try:
            _bounded(_document(self), MAX_NODE_CONTROL_PAYLOAD_BYTES)
            return
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure

    @property
    def profile(self) -> ReceiverNodeControlResultProfile:
        return ReceiverNodeControlResultProfile.V2

    @property
    def request_id(self) -> str:
        return self.request.request_id

    @property
    def request_digest(self) -> ReceiverNodeControlRequestDigest:
        return self.request.canonical_digest()

    @property
    def operation(self) -> NodeControlOperation:
        return self.outcome.operation

    @property
    def status(self) -> NodeControlResultStatus:
        return self.outcome.status

    @property
    def codec(self) -> ControlPlaneResultCodec:
        return self.outcome.codec

    def descriptor(self) -> dict[str, object]:
        try:
            return _document(replace(self))
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.descriptor())


class ReceiverNodeControlResultCodec:
    """Decode only against an independently retained originating request.

    result(outcome) is the producer operation. decode never supplies a missing
    emitter digest from this codec's expected context.
    """
    def __init__(self, request: ReceiverNodeControlRequest, declaration: WorkloadNodeControlSurfaceDeclaration) -> None:
        try:
            _context(request, declaration)
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        else:
            self._request = request
            self._declaration = declaration
            return
        raise failure

    def result(self, outcome: NodeControlResult) -> ReceiverNodeControlResult:
        try:
            return ReceiverNodeControlResult(self._request, self._declaration, outcome)
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure

    def encode(self, result: ReceiverNodeControlResult) -> dict[str, object]:
        try:
            _context(self._request, self._declaration)
            _require(type(result) is ReceiverNodeControlResult)
            rebuilt = replace(result)
            _require(rebuilt.request == self._request and rebuilt.declaration == self._declaration)
            # Python equality identifies bool/number states that JCS distinguishes.
            _require(rebuilt.request.canonical_digest() == self._request.canonical_digest())
            return _document(rebuilt)
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure

    def decode(self, document: object) -> ReceiverNodeControlResult:
        try:
            codec = _context(self._request, self._declaration)
            _require(type(document) is dict and all(type(key) is str for key in document))
            _bounded(document, MAX_NODE_CONTROL_PAYLOAD_BYTES)
            _require(type(document["profile"]) is str and document["profile"] == ReceiverNodeControlResultProfile.V2.value)
            _digest(document["request_digest"])
            _require(document["request_digest"] == self._request.canonical_digest().value)
            _require(type(document["request_id"]) is str and document["request_id"] == self._request.request_id)
            _require(type(document["operation"]) is str and document["operation"] == self._request.operation.value)
            # Received correlation is required and compared before outcome decode.
            outcome = codec.decode({key: value for key, value in document.items() if key not in _CORRELATION_KEYS})
            return ReceiverNodeControlResult(self._request, self._declaration, outcome)
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure

    def encode_canonical_bytes(self, result: ReceiverNodeControlResult) -> bytes:
        return canonical_json_bytes(self.encode(result))

    def decode_canonical_bytes(self, raw: bytes) -> ReceiverNodeControlResult:
        try:
            return self.decode(_canonical_document(raw, MAX_NODE_CONTROL_PAYLOAD_BYTES))
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure


__all__ = ["ReceiverNodeControlResultProfile", "ReceiverNodeControlResult", "ReceiverNodeControlResultCodec"]
