"""Pure bounded results for workload node-control surface reads.

These values prove exact request and declaration binding plus structural
registry coverage. They do not authenticate, parse HTTP, inspect a live
registry, invoke workload variables, or perform external effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core._node_control_public_wire import (
    NodeControlCanonicalDomainError,
    canonical_json_bytes,
)
from control_plane_kit_core.node_control import (
    MAX_NODE_CONTROL_VARIABLES_PER_SURFACE,
    NodeControlCanonicalization,
    NodeControlContractError,
    NodeControlGraphReference,
    NodeControlGraphReferenceRole,
)
from control_plane_kit_core.node_control_surface_reads import (
    NodeControlSurfaceReadContractError,
    NodeControlSurfaceReadKind,
    NodeControlSurfaceReadRequest,
    NodeControlSurfaceReadRequestDigest,
    WorkloadNodeControlSurfaceDeclaration,
    WorkloadNodeControlSurfaceDeclarationCodec,
    WorkloadNodeControlSurfaceDeclarationIdentity,
)


MAX_NODE_CONTROL_SURFACE_CAPABILITIES_RESULT_BYTES = 16_902
MAX_NODE_CONTROL_SURFACE_STATUS_RESULT_BYTES = 4_811

_CAPABILITIES_KEYS = frozenset(
    {
        "profile",
        "canonicalization",
        "request_id",
        "request_digest",
        "kind",
        "declaration_identity",
        "declaration",
    }
)
_STATUS_KEYS = frozenset(
    {
        "profile",
        "canonicalization",
        "request_id",
        "request_digest",
        "kind",
        "declaration_identity",
        "installed_variable_names",
        "registry_coverage",
    }
)


class NodeControlSurfaceReadResultProfile(StrEnum):
    """Versioned identity for capability and status result variants."""

    V1 = "workload-node-control-surface-read-result.v1"


class NodeControlSurfaceRegistryCoverage(StrEnum):
    """Structural coverage of one declared surface by installed variables."""

    NONE = "none"
    PARTIAL = "partial"
    COMPLETE = "complete"


@dataclass(frozen=True, order=True)
class NodeControlSurfaceCapabilitiesResult:
    """Exact declared capabilities returned for one capabilities request."""

    request: NodeControlSurfaceReadRequest = field(repr=False)
    declaration: WorkloadNodeControlSurfaceDeclaration = field(repr=False)

    def __post_init__(self) -> None:
        _validate_context(self.request, self.declaration)
        if self.request.kind is not NodeControlSurfaceReadKind.CAPABILITIES:
            raise NodeControlSurfaceReadContractError(
                "surface-read capability result kind requires a capabilities request"
            )
        _bounded_canonical_bytes(
            self.descriptor(),
            MAX_NODE_CONTROL_SURFACE_CAPABILITIES_RESULT_BYTES,
            "surface-read capability result",
        )

    @property
    def profile(self) -> NodeControlSurfaceReadResultProfile:
        return NodeControlSurfaceReadResultProfile.V1

    @property
    def canonicalization(self) -> NodeControlCanonicalization:
        return NodeControlCanonicalization.JCS_RFC8785_V1

    @property
    def request_id(self) -> str:
        return self.request.request_id

    @property
    def request_digest(self) -> NodeControlSurfaceReadRequestDigest:
        return self.request.canonical_digest()

    @property
    def kind(self) -> NodeControlSurfaceReadKind:
        return NodeControlSurfaceReadKind.CAPABILITIES

    @property
    def declaration_identity(
        self,
    ) -> WorkloadNodeControlSurfaceDeclarationIdentity:
        return self.declaration.identity()

    def descriptor(self) -> dict[str, object]:
        return {
            "profile": self.profile.value,
            "canonicalization": self.canonicalization.value,
            "request_id": self.request_id,
            "request_digest": self.request_digest.value,
            "kind": self.kind.value,
            "declaration_identity": self.declaration_identity.value,
            "declaration": self.declaration.descriptor(),
        }

    def canonical_bytes(self) -> bytes:
        return _bounded_canonical_bytes(
            self.descriptor(),
            MAX_NODE_CONTROL_SURFACE_CAPABILITIES_RESULT_BYTES,
            "surface-read capability result",
        )


@dataclass(frozen=True, order=True)
class NodeControlSurfaceStatusResult:
    """Canonical installed subset for one exact status request."""

    request: NodeControlSurfaceReadRequest = field(repr=False)
    declaration: WorkloadNodeControlSurfaceDeclaration = field(repr=False)
    installed_variable_names: tuple[NodeControlGraphReference, ...] = field(
        repr=False
    )

    def __post_init__(self) -> None:
        _validate_context(self.request, self.declaration)
        if self.request.kind is not NodeControlSurfaceReadKind.STATUS:
            raise NodeControlSurfaceReadContractError(
                "surface-read status result kind requires a status request"
            )
        _validate_installed_variable_names(
            self.declaration,
            self.installed_variable_names,
        )
        _bounded_canonical_bytes(
            self.descriptor(),
            MAX_NODE_CONTROL_SURFACE_STATUS_RESULT_BYTES,
            "surface-read status result",
        )

    @property
    def profile(self) -> NodeControlSurfaceReadResultProfile:
        return NodeControlSurfaceReadResultProfile.V1

    @property
    def canonicalization(self) -> NodeControlCanonicalization:
        return NodeControlCanonicalization.JCS_RFC8785_V1

    @property
    def request_id(self) -> str:
        return self.request.request_id

    @property
    def request_digest(self) -> NodeControlSurfaceReadRequestDigest:
        return self.request.canonical_digest()

    @property
    def kind(self) -> NodeControlSurfaceReadKind:
        return NodeControlSurfaceReadKind.STATUS

    @property
    def declaration_identity(
        self,
    ) -> WorkloadNodeControlSurfaceDeclarationIdentity:
        return self.declaration.identity()

    @property
    def registry_coverage(self) -> NodeControlSurfaceRegistryCoverage:
        return _derive_registry_coverage(
            self.declaration,
            self.installed_variable_names,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "profile": self.profile.value,
            "canonicalization": self.canonicalization.value,
            "request_id": self.request_id,
            "request_digest": self.request_digest.value,
            "kind": self.kind.value,
            "declaration_identity": self.declaration_identity.value,
            "installed_variable_names": [
                name.value for name in self.installed_variable_names
            ],
            "registry_coverage": self.registry_coverage.value,
        }

    def canonical_bytes(self) -> bytes:
        return _bounded_canonical_bytes(
            self.descriptor(),
            MAX_NODE_CONTROL_SURFACE_STATUS_RESULT_BYTES,
            "surface-read status result",
        )


NodeControlSurfaceReadResult = (
    NodeControlSurfaceCapabilitiesResult | NodeControlSurfaceStatusResult
)
_RESULT_TYPES = (
    NodeControlSurfaceCapabilitiesResult,
    NodeControlSurfaceStatusResult,
)


class NodeControlSurfaceReadResultCodec:
    """Strict result codec and factory bound to one request and declaration."""

    def __init__(
        self,
        request: NodeControlSurfaceReadRequest,
        declaration: WorkloadNodeControlSurfaceDeclaration,
    ) -> None:
        _validate_context(request, declaration)
        self._request = request
        self._declaration = declaration
        self._context_maximum = self._derive_context_maximum()

    def capabilities_result(self) -> NodeControlSurfaceCapabilitiesResult:
        return NodeControlSurfaceCapabilitiesResult(
            self._request,
            self._declaration,
        )

    def status_result(
        self,
        installed_variable_names: tuple[NodeControlGraphReference, ...],
    ) -> NodeControlSurfaceStatusResult:
        return NodeControlSurfaceStatusResult(
            self._request,
            self._declaration,
            installed_variable_names,
        )

    def encode(self, result: NodeControlSurfaceReadResult) -> dict[str, object]:
        if not isinstance(result, _RESULT_TYPES):
            raise NodeControlSurfaceReadContractError(
                "encode requires NodeControlSurfaceReadResult"
            )
        self._validate_result_context(result)
        return result.descriptor()

    def decode(
        self,
        descriptor: Mapping[str, object],
    ) -> NodeControlSurfaceReadResult:
        mapping = _mapping(descriptor, "surface-read result")
        maximum = self._global_maximum()
        encoded = _bounded_canonical_bytes(
            mapping,
            maximum,
            "surface-read result",
        )
        if len(encoded) > self._context_maximum:
            raise NodeControlSurfaceReadContractError(
                "surface-read result context aggregate exceeds the bound"
            )

        if self._request.kind is NodeControlSurfaceReadKind.CAPABILITIES:
            _require_exact_keys(
                mapping,
                _CAPABILITIES_KEYS,
                "surface-read capability result",
            )
        else:
            _require_exact_keys(
                mapping,
                _STATUS_KEYS,
                "surface-read status result",
            )
        self._validate_common_claims(mapping)

        if self._request.kind is NodeControlSurfaceReadKind.CAPABILITIES:
            raw_declaration = _mapping(
                mapping.get("declaration"),
                "surface-read result declaration",
            )
            try:
                decoded = WorkloadNodeControlSurfaceDeclarationCodec().decode(
                    raw_declaration
                )
            except NodeControlContractError:
                pass
            else:
                if decoded != self._declaration:
                    raise NodeControlSurfaceReadContractError(
                        "surface-read result declaration does not match expected declaration"
                    )
                return self.capabilities_result()
            raise NodeControlSurfaceReadContractError(
                "surface-read result declaration is malformed"
            )

        raw_names = mapping.get("installed_variable_names")
        if not isinstance(raw_names, list):
            raise NodeControlSurfaceReadContractError(
                "surface-read result installed variable names must be a list"
            )
        if len(raw_names) > MAX_NODE_CONTROL_VARIABLES_PER_SURFACE:
            raise NodeControlSurfaceReadContractError(
                "surface-read result contains too many installed variable names"
            )
        try:
            installed = tuple(
                NodeControlGraphReference(
                    NodeControlGraphReferenceRole.VARIABLE,
                    _list_text(value),
                )
                for value in raw_names
            )
        except NodeControlContractError:
            pass
        else:
            _validate_installed_variable_names(
                self._declaration,
                installed,
            )
            coverage = _enum(
                NodeControlSurfaceRegistryCoverage,
                mapping.get("registry_coverage"),
                "surface-read result registry coverage",
            )
            expected_coverage = _derive_registry_coverage(
                self._declaration,
                installed,
            )
            if coverage is not expected_coverage:
                raise NodeControlSurfaceReadContractError(
                    "surface-read result registry coverage is contradictory"
                )
            return self.status_result(installed)
        raise NodeControlSurfaceReadContractError(
            "surface-read result installed variable names are malformed"
        )

    def _derive_context_maximum(self) -> int:
        if self._request.kind is NodeControlSurfaceReadKind.CAPABILITIES:
            return len(self.capabilities_result().canonical_bytes())
        complete = NodeControlSurfaceStatusResult(
            self._request,
            self._declaration,
            _declared_variable_names(self._declaration),
        )
        return len(complete.canonical_bytes())

    def _global_maximum(self) -> int:
        if self._request.kind is NodeControlSurfaceReadKind.CAPABILITIES:
            return MAX_NODE_CONTROL_SURFACE_CAPABILITIES_RESULT_BYTES
        return MAX_NODE_CONTROL_SURFACE_STATUS_RESULT_BYTES

    def _validate_result_context(
        self,
        result: NodeControlSurfaceReadResult,
    ) -> None:
        if result.kind is not self._request.kind:
            raise NodeControlSurfaceReadContractError(
                "surface-read result kind does not match expected request"
            )
        if result.request != self._request:
            raise NodeControlSurfaceReadContractError(
                "surface-read result request does not match expected request"
            )
        if result.declaration != self._declaration:
            raise NodeControlSurfaceReadContractError(
                "surface-read result declaration does not match expected declaration"
            )

    def _validate_common_claims(self, mapping: Mapping[str, object]) -> None:
        profile = _enum(
            NodeControlSurfaceReadResultProfile,
            mapping.get("profile"),
            "surface-read result profile",
        )
        if profile is not NodeControlSurfaceReadResultProfile.V1:
            raise NodeControlSurfaceReadContractError(
                "surface-read result profile is unknown"
            )
        canonicalization = _enum(
            NodeControlCanonicalization,
            mapping.get("canonicalization"),
            "surface-read result canonicalization",
        )
        if canonicalization is not NodeControlCanonicalization.JCS_RFC8785_V1:
            raise NodeControlSurfaceReadContractError(
                "surface-read result canonicalization is unknown"
            )
        kind = _enum(
            NodeControlSurfaceReadKind,
            mapping.get("kind"),
            "surface-read result kind",
        )
        if kind is not self._request.kind:
            raise NodeControlSurfaceReadContractError(
                "surface-read result kind does not match expected request"
            )
        if _text(mapping, "request_id") != self._request.request_id:
            raise NodeControlSurfaceReadContractError(
                "surface-read result request does not match expected request"
            )
        if (
            _text(mapping, "request_digest")
            != self._request.canonical_digest().value
        ):
            raise NodeControlSurfaceReadContractError(
                "surface-read result request does not match expected request"
            )
        if (
            _text(mapping, "declaration_identity")
            != self._declaration.identity().value
        ):
            raise NodeControlSurfaceReadContractError(
                "surface-read result declaration does not match expected declaration"
            )


def _validate_context(
    request: object,
    declaration: object,
) -> None:
    if not isinstance(request, NodeControlSurfaceReadRequest):
        raise NodeControlSurfaceReadContractError(
            "surface-read result requires NodeControlSurfaceReadRequest"
        )
    if not isinstance(declaration, WorkloadNodeControlSurfaceDeclaration):
        raise NodeControlSurfaceReadContractError(
            "surface-read result requires WorkloadNodeControlSurfaceDeclaration"
        )
    if request.declaration_identity != declaration.identity():
        raise NodeControlSurfaceReadContractError(
            "surface-read result declaration does not match request"
        )
    if (
        request.target.provider_socket_name
        != declaration.surface.provider_socket_name
    ):
        raise NodeControlSurfaceReadContractError(
            "surface-read result declaration socket does not match request"
        )


def _declared_variable_names(
    declaration: WorkloadNodeControlSurfaceDeclaration,
) -> tuple[NodeControlGraphReference, ...]:
    return tuple(
        variable.variable_name for variable in declaration.surface.variables
    )


def _validate_installed_variable_names(
    declaration: WorkloadNodeControlSurfaceDeclaration,
    installed: tuple[NodeControlGraphReference, ...],
) -> None:
    if not isinstance(installed, tuple):
        raise NodeControlSurfaceReadContractError(
            "surface-read installed variable names must be a tuple"
        )
    if len(installed) > MAX_NODE_CONTROL_VARIABLES_PER_SURFACE:
        raise NodeControlSurfaceReadContractError(
            "surface-read result contains too many installed variable names"
        )
    if not all(
        isinstance(name, NodeControlGraphReference)
        and name.role is NodeControlGraphReferenceRole.VARIABLE
        for name in installed
    ):
        raise NodeControlSurfaceReadContractError(
            "surface-read installed variable names must be variable references"
        )
    if installed != tuple(sorted(installed)):
        raise NodeControlSurfaceReadContractError(
            "surface-read installed variable names must be canonical"
        )
    values = tuple(name.value for name in installed)
    if len(set(values)) != len(values):
        raise NodeControlSurfaceReadContractError(
            "surface-read installed variable names must be unique"
        )
    declared = _declared_variable_names(declaration)
    if any(name not in declared for name in installed):
        raise NodeControlSurfaceReadContractError(
            "surface-read installed variable name is undeclared"
        )


def _derive_registry_coverage(
    declaration: WorkloadNodeControlSurfaceDeclaration,
    installed: tuple[NodeControlGraphReference, ...],
) -> NodeControlSurfaceRegistryCoverage:
    if not installed:
        return NodeControlSurfaceRegistryCoverage.NONE
    if installed == _declared_variable_names(declaration):
        return NodeControlSurfaceRegistryCoverage.COMPLETE
    return NodeControlSurfaceRegistryCoverage.PARTIAL


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise NodeControlSurfaceReadContractError(f"{name} must be an object")
    return value


def _bounded_canonical_bytes(value: object, maximum: int, name: str) -> bytes:
    try:
        encoded = canonical_json_bytes(value)
    except NodeControlCanonicalDomainError:
        pass
    else:
        if len(encoded) <= maximum:
            return encoded
        raise NodeControlSurfaceReadContractError(
            f"{name} aggregate exceeds the public bound"
        )
    raise NodeControlSurfaceReadContractError(
        f"{name} is outside the canonical JSON domain"
    )


def _require_exact_keys(
    mapping: Mapping[str, object],
    expected: frozenset[str],
    name: str,
) -> None:
    if set(mapping) != expected:
        raise NodeControlSurfaceReadContractError(
            f"{name} must contain the exact public fields"
        )


def _text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise NodeControlSurfaceReadContractError(
            "surface-read result field must be text"
        )
    return value


def _list_text(value: object) -> str:
    if not isinstance(value, str):
        raise NodeControlSurfaceReadContractError(
            "surface-read result installed variable name must be text"
        )
    return value


def _enum(enum_type, value: object, name: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        pass
    raise NodeControlSurfaceReadContractError(f"{name} is unknown")


__all__ = [
    "MAX_NODE_CONTROL_SURFACE_CAPABILITIES_RESULT_BYTES",
    "MAX_NODE_CONTROL_SURFACE_STATUS_RESULT_BYTES",
    "NodeControlSurfaceCapabilitiesResult",
    "NodeControlSurfaceReadResult",
    "NodeControlSurfaceReadResultCodec",
    "NodeControlSurfaceReadResultProfile",
    "NodeControlSurfaceRegistryCoverage",
    "NodeControlSurfaceStatusResult",
]
