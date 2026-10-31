"""V3 surface descriptions bound to an independently supplied receiver request."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from control_plane_kit_core._node_control_public_wire import canonical_json_bytes
from control_plane_kit_core.node_control import (
    MAX_NODE_CONTROL_VARIABLES_PER_SURFACE, NodeControlCanonicalization,
    NodeControlGraphReference, NodeControlGraphReferenceRole,
)
from control_plane_kit_core.node_control_surface_reads import (
    NodeControlSurfaceReadKind, WorkloadNodeControlSurfaceDeclaration,
    WorkloadNodeControlSurfaceDeclarationCodec, WorkloadNodeControlSurfaceDeclarationIdentity,
)
from control_plane_kit_core.node_control_surface_read_results import (
    MAX_NODE_CONTROL_SURFACE_CAPABILITIES_RESULT_BYTES, NodeControlSurfaceRegistryCoverage,
    _coverage_key, _status_maximum, _declared_variable_names,
    _validate_installed_variable_names, _derive_registry_coverage,
)
from control_plane_kit_core.receiver_control_surface_reads import (
    ReceiverControlSurfaceReadContractError, ReceiverControlSurfaceReadRequest,
    ReceiverControlSurfaceReadRequestDigest,
    _ERROR, _INPUT_ERRORS, _require, _bounded, _closed, _canonical_document,
    _validate_local, _local_mismatch,
)

_COMMON_KEYS = frozenset({"profile", "canonicalization", "request_id", "request_digest",
                          "declaration_identity", "kind"})


class ReceiverControlSurfaceReadResultProfile(StrEnum):
    V3 = "workload-node-control-surface-read-result.v3"


def _validate_context(request, declaration) -> None:
    _validate_local(request, request.target, declaration, request.kind)
    _require(_local_mismatch(request, request.target, declaration, request.kind) is None)


def _installed(declaration, names) -> None:
    _require(type(names) is tuple)
    for name in names:
        _require(type(name) is NodeControlGraphReference
                 and type(name.value) is str and name.role is NodeControlGraphReferenceRole.VARIABLE)
        _require(replace(name) == name)
    _validate_installed_variable_names(declaration, names)


def _common_document(request, declaration) -> dict[str, object]:
    return {"profile": ReceiverControlSurfaceReadResultProfile.V3.value,
            "canonicalization": NodeControlCanonicalization.JCS_RFC8785_V1.value,
            "request_id": request.request_id, "request_digest": request.canonical_digest().value,
            "declaration_identity": declaration.identity().value, "kind": request.kind.value}


def _document(result) -> dict[str, object]:
    common = _common_document(result.request, result.declaration)
    if type(result) is ReceiverControlSurfaceCapabilitiesResult:
        return common | {"declaration": result.declaration.descriptor()}
    return common | {"installed_variable_names": [name.value for name in result.installed_variable_names],
                     _coverage_key(result.declaration): result.registry_coverage.value}


@dataclass(frozen=True, slots=True, repr=False)
class ReceiverControlSurfaceCapabilitiesResult:
    request: ReceiverControlSurfaceReadRequest
    declaration: WorkloadNodeControlSurfaceDeclaration

    def __post_init__(self) -> None:
        try:
            _validate_context(self.request, self.declaration)
            _require(self.request.kind is NodeControlSurfaceReadKind.CAPABILITIES)
            _bounded(_document(self), MAX_NODE_CONTROL_SURFACE_CAPABILITIES_RESULT_BYTES)
            return
        except _INPUT_ERRORS:
            failure = ReceiverControlSurfaceReadContractError(_ERROR)
        raise failure

    @property
    def profile(self) -> ReceiverControlSurfaceReadResultProfile:
        return ReceiverControlSurfaceReadResultProfile.V3

    @property
    def canonicalization(self) -> NodeControlCanonicalization:
        return NodeControlCanonicalization.JCS_RFC8785_V1

    @property
    def request_id(self) -> str:
        return self.request.request_id

    @property
    def request_digest(self) -> ReceiverControlSurfaceReadRequestDigest:
        return self.request.canonical_digest()

    @property
    def kind(self) -> NodeControlSurfaceReadKind:
        return NodeControlSurfaceReadKind.CAPABILITIES

    @property
    def declaration_identity(self) -> WorkloadNodeControlSurfaceDeclarationIdentity:
        return self.declaration.identity()

    def descriptor(self) -> dict[str, object]:
        return ReceiverControlSurfaceReadResultCodec(self.request, self.declaration).encode(self)

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.descriptor())


@dataclass(frozen=True, slots=True, repr=False)
class ReceiverControlSurfaceStatusResult:
    request: ReceiverControlSurfaceReadRequest
    declaration: WorkloadNodeControlSurfaceDeclaration
    installed_variable_names: tuple[NodeControlGraphReference, ...]

    def __post_init__(self) -> None:
        try:
            _validate_context(self.request, self.declaration)
            _require(self.request.kind is NodeControlSurfaceReadKind.STATUS)
            _installed(self.declaration, self.installed_variable_names)
            _bounded(_document(self), _status_maximum(self.declaration))
            return
        except _INPUT_ERRORS:
            failure = ReceiverControlSurfaceReadContractError(_ERROR)
        raise failure

    @property
    def profile(self) -> ReceiverControlSurfaceReadResultProfile:
        return ReceiverControlSurfaceReadResultProfile.V3

    @property
    def canonicalization(self) -> NodeControlCanonicalization:
        return NodeControlCanonicalization.JCS_RFC8785_V1

    @property
    def request_id(self) -> str:
        return self.request.request_id

    @property
    def request_digest(self) -> ReceiverControlSurfaceReadRequestDigest:
        return self.request.canonical_digest()

    @property
    def kind(self) -> NodeControlSurfaceReadKind:
        return NodeControlSurfaceReadKind.STATUS

    @property
    def declaration_identity(self) -> WorkloadNodeControlSurfaceDeclarationIdentity:
        return self.declaration.identity()

    @property
    def registry_coverage(self) -> NodeControlSurfaceRegistryCoverage:
        return _derive_registry_coverage(self.declaration, self.installed_variable_names)

    def descriptor(self) -> dict[str, object]:
        return ReceiverControlSurfaceReadResultCodec(self.request, self.declaration).encode(self)

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.descriptor())


ReceiverControlSurfaceReadResult = ReceiverControlSurfaceCapabilitiesResult | ReceiverControlSurfaceStatusResult
_RESULT_TYPES = (ReceiverControlSurfaceCapabilitiesResult, ReceiverControlSurfaceStatusResult)


@dataclass(frozen=True, slots=True, repr=False)
class ReceiverControlSurfaceReadResultCodec:
    request: ReceiverControlSurfaceReadRequest
    declaration: WorkloadNodeControlSurfaceDeclaration

    def __post_init__(self) -> None:
        try:
            _validate_context(self.request, self.declaration)
            return
        except _INPUT_ERRORS:
            failure = ReceiverControlSurfaceReadContractError(_ERROR)
        raise failure

    def capabilities_result(self) -> ReceiverControlSurfaceCapabilitiesResult:
        return ReceiverControlSurfaceCapabilitiesResult(self.request, self.declaration)

    def status_result(self, installed_variable_names: tuple[NodeControlGraphReference, ...]) -> ReceiverControlSurfaceStatusResult:
        return ReceiverControlSurfaceStatusResult(self.request, self.declaration, installed_variable_names)

    def _global_maximum(self) -> int:
        if self.request.kind is NodeControlSurfaceReadKind.CAPABILITIES:
            return MAX_NODE_CONTROL_SURFACE_CAPABILITIES_RESULT_BYTES
        return _status_maximum(self.declaration)

    def _context_maximum(self) -> int:
        if self.request.kind is NodeControlSurfaceReadKind.CAPABILITIES:
            complete = self.capabilities_result()
        else:
            complete = self.status_result(_declared_variable_names(self.declaration))
        return len(canonical_json_bytes(_document(complete)))

    def encode(self, result: ReceiverControlSurfaceReadResult) -> dict[str, object]:
        try:
            _validate_context(self.request, self.declaration)
            _require(type(result) in _RESULT_TYPES)
            value = replace(result)
            _require(value.request == self.request and value.declaration == self.declaration)
            return _document(value)
        except _INPUT_ERRORS:
            failure = ReceiverControlSurfaceReadContractError(_ERROR)
        raise failure

    def decode(self, document: object) -> ReceiverControlSurfaceReadResult:
        try:
            _validate_context(self.request, self.declaration)
            _bounded(document, self._global_maximum())
            capabilities = self.request.kind is NodeControlSurfaceReadKind.CAPABILITIES
            keys = _COMMON_KEYS | ({"declaration"} if capabilities else
                                   {"installed_variable_names", _coverage_key(self.declaration)})
            value = _closed(document, keys, self._context_maximum())
            expected = _common_document(self.request, self.declaration)
            _require(all(type(value[key]) is str and value[key] == expected[key] for key in _COMMON_KEYS))
            if capabilities:
                decoded = WorkloadNodeControlSurfaceDeclarationCodec().decode(value["declaration"])
                _require(decoded == self.declaration)
                return self.capabilities_result()
            names = value["installed_variable_names"]
            _require(type(names) is list and len(names) <= MAX_NODE_CONTROL_VARIABLES_PER_SURFACE)
            _require(all(type(name) is str for name in names))
            installed = tuple(NodeControlGraphReference(NodeControlGraphReferenceRole.VARIABLE, name) for name in names)
            result = self.status_result(installed)
            coverage = value[_coverage_key(self.declaration)]
            _require(type(coverage) is str and coverage == result.registry_coverage.value)
            return result
        except _INPUT_ERRORS:
            failure = ReceiverControlSurfaceReadContractError(_ERROR)
        raise failure

    def encode_canonical_bytes(self, result: ReceiverControlSurfaceReadResult) -> bytes:
        return canonical_json_bytes(self.encode(result))

    def decode_canonical_bytes(self, raw: bytes) -> ReceiverControlSurfaceReadResult:
        try:
            _validate_context(self.request, self.declaration)
            # The actual declaration/subset envelope tightens the unchanged global cap.
            return self.decode(_canonical_document(raw, min(self._global_maximum(), self._context_maximum())))
        except _INPUT_ERRORS:
            failure = ReceiverControlSurfaceReadContractError(_ERROR)
        raise failure


__all__ = ["ReceiverControlSurfaceReadResultProfile", "ReceiverControlSurfaceCapabilitiesResult",
           "ReceiverControlSurfaceStatusResult", "ReceiverControlSurfaceReadResult", "ReceiverControlSurfaceReadResultCodec"]
