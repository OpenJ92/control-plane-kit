"""Closed successor health outcomes correlated to an exact admitted request."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from control_plane_kit_core._node_control_public_wire import canonical_json_bytes
from control_plane_kit_core.node_control import NodeControlCanonicalization
from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclaration
from control_plane_kit_core.node_health_read_results import MAX_NODE_HEALTH_READ_RESULT_BYTES, NodeHealthReadOutcome
from control_plane_kit_core.receiver_health_reads import (
    ReceiverHealthReadContractError, ReceiverHealthReadRequest,
    _ERROR, _INPUT_ERRORS, _require, _bounded, _closed, _enum, _canonical_document,
    _validate_local, _local_mismatch,
)

_RESULT_KEYS = frozenset({"profile", "canonicalization", "request_id", "request_digest",
                          "declaration_identity", "kind", "outcome"})


class ReceiverHealthReadResultProfile(StrEnum):
    V2 = "workload-node-health-read-result.v2"


def _validate_context(request: ReceiverHealthReadRequest, declaration: WorkloadNodeControlSurfaceDeclaration) -> None:
    _validate_local(request, request.target, declaration, request.kind)
    _require(_local_mismatch(request, request.target, declaration, request.kind) is None)


def _context_bound(request: ReceiverHealthReadRequest) -> int:
    return MAX_NODE_HEALTH_READ_RESULT_BYTES - (128 - len(request.request_id)) - (9 - len(request.kind.value))


def _document(result) -> dict[str, object]:
    return {"profile": ReceiverHealthReadResultProfile.V2.value,
            "canonicalization": NodeControlCanonicalization.JCS_RFC8785_V1.value,
            "request_id": result.request.request_id, "request_digest": result.request.canonical_digest().value,
            "declaration_identity": result.declaration.identity().value,
            "kind": result.request.kind.value, "outcome": result.outcome.value}


@dataclass(frozen=True, slots=True, repr=False)
class ReceiverHealthReadResult:
    request: ReceiverHealthReadRequest
    declaration: WorkloadNodeControlSurfaceDeclaration
    outcome: NodeHealthReadOutcome

    def __post_init__(self) -> None:
        try:
            _validate_context(self.request, self.declaration)
            _require(type(self.outcome) is NodeHealthReadOutcome)
            _bounded(_document(self), _context_bound(self.request))
            return
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure

    @property
    def profile(self) -> ReceiverHealthReadResultProfile:
        return ReceiverHealthReadResultProfile.V2

    @property
    def canonicalization(self) -> NodeControlCanonicalization:
        return NodeControlCanonicalization.JCS_RFC8785_V1

    def descriptor(self) -> dict[str, object]:
        return ReceiverHealthReadResultCodec(self.request, self.declaration).encode(self)

    def canonical_bytes(self) -> bytes:
        return ReceiverHealthReadResultCodec(self.request, self.declaration).encode_canonical_bytes(self)


@dataclass(frozen=True, slots=True, repr=False)
class ReceiverHealthReadResultCodec:
    request: ReceiverHealthReadRequest
    declaration: WorkloadNodeControlSurfaceDeclaration

    def __post_init__(self) -> None:
        try:
            _validate_context(self.request, self.declaration)
            return
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure

    def encode(self, result: ReceiverHealthReadResult) -> dict[str, object]:
        try:
            _validate_context(self.request, self.declaration)
            _require(type(result) is ReceiverHealthReadResult)
            value = replace(result)
            _require(value.request == self.request and value.declaration == self.declaration)
            return _document(value)
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure

    def decode(self, document: object) -> ReceiverHealthReadResult:
        try:
            _validate_context(self.request, self.declaration)
            value = _closed(document, _RESULT_KEYS, _context_bound(self.request))
            # Every correlation field is independently derived, never learned from the payload.
            expected = _document(ReceiverHealthReadResult(self.request, self.declaration, NodeHealthReadOutcome.UNKNOWN))
            _require(all(type(value[name]) is str and value[name] == expected[name]
                         for name in _RESULT_KEYS - {"outcome"}))
            return ReceiverHealthReadResult(self.request, self.declaration, _enum(NodeHealthReadOutcome, value["outcome"]))
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure

    def encode_canonical_bytes(self, result: ReceiverHealthReadResult) -> bytes:
        return canonical_json_bytes(self.encode(result))

    def decode_canonical_bytes(self, raw: bytes) -> ReceiverHealthReadResult:
        try:
            _validate_context(self.request, self.declaration)
            return self.decode(_canonical_document(raw, _context_bound(self.request)))
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure


__all__ = ["ReceiverHealthReadResultProfile", "ReceiverHealthReadResult", "ReceiverHealthReadResultCodec"]
