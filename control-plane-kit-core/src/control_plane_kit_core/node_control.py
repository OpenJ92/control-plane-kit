"""Pure authenticated workload node-control contract language.

The values in this module describe exact workload authority and typed variable
transitions. They do not authenticate callers, sign grants, retain replay
state, dispatch commands, acquire locks, or mutate workload state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math
import re
from typing import Mapping

from control_plane_kit_core.capabilities import CapabilityName
from control_plane_kit_core.control_routes import ControlRouteSetName


MAX_NODE_CONTROL_STATE_ITEMS = 128
MAX_NODE_CONTROL_PAYLOAD_BYTES = 16_384
MAX_NODE_CONTROL_EVIDENCE_ITEMS = 32
MAX_WORKLOAD_NODE_CONTROL_GRANT_LIFETIME_SECONDS = 300

_MAX_IDENTIFIER = 128
_MAX_REFERENCE = 256
_MAX_DESCRIPTION = 512
_MAX_SAFE_INTEGER = 2**53 - 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_HOST_PORT = re.compile(r"^[^/:\s]+:\d{1,5}$")
_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
_SECRET_MARKERS = (
    "authorization:",
    "bearer ",
    "credential=",
    "password=",
    "private key",
    "secret=",
    "signature=",
    "sg.",
    "sk-",
    "token=",
)

_TARGET_KEYS = frozenset(
    {"workspace_id", "graph_revision", "node_id", "provider_socket_name"}
)
_PRECONDITION_KEYS = frozenset({"expected_version"})
_STATE_COMMON_KEYS = frozenset({"kind"})
_SCALAR_STATE_KEYS = frozenset({"kind", "value"})
_MAP_STATE_KEYS = frozenset({"kind", "entries"})
_WEIGHTED_STATE_KEYS = frozenset({"kind", "targets", "weights"})
_PAYLOAD_KEYS = frozenset({"codec", "state"})
_REQUEST_KEYS = frozenset(
    {
        "target",
        "variable_name",
        "operation",
        "request_id",
        "idempotency_key",
        "command_codec",
        "precondition",
        "payload",
    }
)
_GRANT_KEYS = frozenset(
    {
        "issuer",
        "key_id",
        "audience",
        "target",
        "variable_name",
        "operation",
        "command_codec",
        "request_id",
        "idempotency_key",
        "request_digest",
        "issued_at",
        "not_before",
        "expires_at",
        "jti",
    }
)
_EVIDENCE_KEYS = frozenset({"code"})
_RESULT_KEYS = frozenset(
    {"request_id", "status", "codec", "version", "payload", "evidence"}
)
_VARIABLE_KEYS = frozenset(
    {
        "variable_name",
        "kind",
        "state_codec",
        "command_codec",
        "result_codec",
        "route_set",
        "capability",
        "description",
    }
)


class NodeControlContractError(ValueError):
    """Raised when workload node-control contract material is malformed."""


class NodeControlOperation(StrEnum):
    """Closed semantic operations available at a workload variable boundary."""

    READ_STATE = "read-state"
    APPLY_COMMAND = "apply-command"


class ControlPlaneVariableKind(StrEnum):
    """Closed first-adopter state shapes exposed by control-plane variables."""

    SCALAR = "scalar"
    MAP = "map"
    WEIGHTED_ROUTING = "weighted-routing"


class ControlPlaneStateCodec(StrEnum):
    """Versioned identities for bounded state descriptors."""

    SCALAR_V1 = "control.scalar.v1"
    MAP_V1 = "control.map.v1"
    WEIGHTED_ROUTING_V1 = "control.weighted-routing.v1"


class ControlPlaneCommandCodec(StrEnum):
    """Versioned identities for closed semantic variable commands."""

    REPLACE_SCALAR_V1 = "control.replace-scalar.v1"
    REPLACE_MAP_V1 = "control.replace-map.v1"
    REPLACE_WEIGHTED_ROUTING_V1 = "control.replace-weighted-routing.v1"


class ControlPlaneResultCodec(StrEnum):
    """Versioned identities for bounded read and transition results."""

    STATE_V1 = "control.state.v1"
    TRANSITION_V1 = "control.transition.v1"


class NodeControlResultStatus(StrEnum):
    """Closed workload command outcomes."""

    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"


class NodeControlEvidenceCode(StrEnum):
    """Secret-free evidence classes returned by workload variables."""

    APPLIED = "applied"
    NO_CHANGE = "no-change"
    PRECONDITION_FAILED = "precondition-failed"
    INVALID_COMMAND = "invalid-command"
    NOT_AUTHORIZED = "not-authorized"
    INTERNAL_FAILURE = "internal-failure"


class WorkloadNodeControlGrantVerificationCode(StrEnum):
    """Bounded reasons why workload end-to-end authority was rejected."""

    GRANT_TYPE_MISMATCH = "grant-type-mismatch"
    ISSUER_MISMATCH = "issuer-mismatch"
    AUDIENCE_MISMATCH = "audience-mismatch"
    WORKSPACE_MISMATCH = "workspace-mismatch"
    REVISION_MISMATCH = "revision-mismatch"
    NODE_MISMATCH = "node-mismatch"
    SOCKET_MISMATCH = "socket-mismatch"
    VARIABLE_MISMATCH = "variable-mismatch"
    COMMAND_MISMATCH = "command-mismatch"
    REQUEST_MISMATCH = "request-mismatch"
    TEMPORALLY_INVALID = "temporally-invalid"


@dataclass(frozen=True, order=True)
class NodeControlTarget:
    """Graph-bound workload control destination without an endpoint value."""

    workspace_id: str
    graph_revision: str
    node_id: str
    provider_socket_name: str

    def __post_init__(self) -> None:
        _validate_identifier(self.workspace_id, "target workspace_id")
        _validate_identifier(self.graph_revision, "target graph_revision")
        _validate_identifier(self.node_id, "target node_id")
        _validate_identifier(
            self.provider_socket_name,
            "target provider_socket_name",
        )

    def descriptor(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "graph_revision": self.graph_revision,
            "node_id": self.node_id,
            "provider_socket_name": self.provider_socket_name,
        }


@dataclass(frozen=True, order=True)
class ControlPlaneTransitionPrecondition:
    """Expected complete-state version for one atomic transition."""

    expected_version: int

    def __post_init__(self) -> None:
        _validate_version(self.expected_version, "expected_version")

    def descriptor(self) -> dict[str, int]:
        return {"expected_version": self.expected_version}


@dataclass(frozen=True, order=True)
class ScalarControlState:
    """One bounded public scalar state value."""

    value: str | int | float | bool | None

    def __post_init__(self) -> None:
        _validate_scalar(self.value, "scalar state")

    def descriptor(self) -> dict[str, object]:
        return {"kind": ControlPlaneVariableKind.SCALAR.value, "value": self.value}


@dataclass(frozen=True, order=True)
class MapControlState:
    """One immutable bounded map of public scalar state values."""

    entries: tuple[tuple[str, str | int | float | bool | None], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple):
            raise NodeControlContractError("map state entries must be a tuple")
        if len(self.entries) > MAX_NODE_CONTROL_STATE_ITEMS:
            raise NodeControlContractError("map state has too many entries")
        normalized: list[tuple[str, str | int | float | bool | None]] = []
        for entry in self.entries:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise NodeControlContractError(
                    "map state entries must be key/value tuples"
                )
            key, value = entry
            _validate_identifier(key, "map state key")
            _validate_scalar(value, f"map state value for {key}")
            normalized.append((key, value))
        keys = tuple(key for key, _ in normalized)
        if len(set(keys)) != len(keys):
            raise NodeControlContractError("map state keys must be unique")
        object.__setattr__(self, "entries", tuple(sorted(normalized)))

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": ControlPlaneVariableKind.MAP.value,
            "entries": dict(self.entries),
        }


@dataclass(frozen=True, order=True)
class WeightedRoutingControlState:
    """One atomic graph-target set and corresponding finite weight snapshot."""

    targets: tuple[str, ...]
    weights: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.targets, tuple) or not self.targets:
            raise NodeControlContractError(
                "weighted routing targets must be a nonempty tuple"
            )
        if len(self.targets) > MAX_NODE_CONTROL_STATE_ITEMS:
            raise NodeControlContractError(
                "weighted routing state has too many targets"
            )
        for target in self.targets:
            _validate_identifier(target, "weighted routing target")
        if len(set(self.targets)) != len(self.targets):
            raise NodeControlContractError(
                "weighted routing targets must be unique"
            )
        if not isinstance(self.weights, tuple):
            raise NodeControlContractError(
                "weighted routing weights must be a tuple"
            )
        normalized_weights: list[tuple[str, float]] = []
        for entry in self.weights:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise NodeControlContractError(
                    "weighted routing weights must be target/weight tuples"
                )
            target, weight = entry
            _validate_identifier(target, "weighted routing weight target")
            if (
                type(weight) not in (int, float)
                or not math.isfinite(weight)
                or weight < 0
            ):
                raise NodeControlContractError(
                    "weighted routing weights must be finite and nonnegative"
                )
            normalized_weights.append((target, float(weight)))
        weight_targets = tuple(target for target, _ in normalized_weights)
        if len(set(weight_targets)) != len(weight_targets):
            raise NodeControlContractError(
                "weighted routing weight targets must be unique"
            )
        if set(weight_targets) != set(self.targets):
            raise NodeControlContractError(
                "weighted routing targets and weight targets must match"
            )
        if not any(weight > 0 for _, weight in normalized_weights):
            raise NodeControlContractError(
                "weighted routing state requires a positive weight"
            )
        object.__setattr__(self, "targets", tuple(sorted(self.targets)))
        object.__setattr__(self, "weights", tuple(sorted(normalized_weights)))

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": ControlPlaneVariableKind.WEIGHTED_ROUTING.value,
            "targets": list(self.targets),
            "weights": dict(self.weights),
        }


ControlStateValue = ScalarControlState | MapControlState | WeightedRoutingControlState


@dataclass(frozen=True, order=True)
class NodeControlPayload:
    """Bounded typed command payload; never arbitrary HTTP request material."""

    codec: ControlPlaneCommandCodec
    state: ControlStateValue

    def __post_init__(self) -> None:
        if not isinstance(self.codec, ControlPlaneCommandCodec):
            raise NodeControlContractError("node-control payload codec is unknown")
        expected = _COMMAND_STATE_TYPES[self.codec]
        if not isinstance(self.state, expected):
            raise NodeControlContractError(
                "node-control payload state does not match command codec"
            )
        _validate_descriptor_size(self.descriptor(), "node-control payload")

    def descriptor(self) -> dict[str, object]:
        return {"codec": self.codec.value, "state": self.state.descriptor()}


@dataclass(frozen=True, order=True)
class NodeControlRequestDigest:
    """Canonical digest of one complete bounded node-control request."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _DIGEST.fullmatch(self.value):
            raise NodeControlContractError(
                "node-control request digest must be 64 lowercase hex characters"
            )


@dataclass(frozen=True, order=True)
class NodeControlCommandRequest:
    """One exact semantic read or state-transition request."""

    target: NodeControlTarget
    variable_name: str
    operation: NodeControlOperation
    request_id: str
    idempotency_key: str
    command_codec: ControlPlaneCommandCodec | None = None
    precondition: ControlPlaneTransitionPrecondition | None = None
    payload: NodeControlPayload | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, NodeControlTarget):
            raise NodeControlContractError(
                "node-control request target must be NodeControlTarget"
            )
        _validate_identifier(self.variable_name, "node-control variable_name")
        if not isinstance(self.operation, NodeControlOperation):
            raise NodeControlContractError("node-control operation is unknown")
        _validate_identifier(self.request_id, "node-control request_id")
        _validate_identifier(
            self.idempotency_key,
            "node-control idempotency_key",
        )
        if self.operation is NodeControlOperation.READ_STATE:
            if any(
                value is not None
                for value in (self.command_codec, self.precondition, self.payload)
            ):
                raise NodeControlContractError(
                    "read-state request must not carry command material"
                )
        elif (
            not isinstance(self.command_codec, ControlPlaneCommandCodec)
            or not isinstance(self.precondition, ControlPlaneTransitionPrecondition)
            or not isinstance(self.payload, NodeControlPayload)
        ):
            raise NodeControlContractError(
                "apply-command request requires codec, precondition, and payload"
            )
        elif self.payload.codec is not self.command_codec:
            raise NodeControlContractError(
                "request command codec must match payload codec"
            )
        _validate_descriptor_size(self.descriptor(), "node-control request")

    def descriptor(self) -> dict[str, object]:
        return {
            "target": self.target.descriptor(),
            "variable_name": self.variable_name,
            "operation": self.operation.value,
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "command_codec": (
                self.command_codec.value if self.command_codec is not None else None
            ),
            "precondition": (
                self.precondition.descriptor()
                if self.precondition is not None
                else None
            ),
            "payload": self.payload.descriptor() if self.payload is not None else None,
        }

    def canonical_digest(self) -> NodeControlRequestDigest:
        return NodeControlRequestDigest(_canonical_digest(self.descriptor()))


class NodeControlCommandRequestCodec:
    """Strict codec for canonical semantic node-control requests."""

    def encode(self, request: NodeControlCommandRequest) -> dict[str, object]:
        if not isinstance(request, NodeControlCommandRequest):
            raise NodeControlContractError(
                "encode requires NodeControlCommandRequest"
            )
        return request.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> NodeControlCommandRequest:
        mapping = _mapping(descriptor, "node-control request")
        _require_keys(mapping, _REQUEST_KEYS, "node-control request")
        target = _decode_target(mapping.get("target"))
        operation = _enum(
            NodeControlOperation,
            mapping.get("operation"),
            "node-control operation",
        )
        command_codec = _optional_enum(
            ControlPlaneCommandCodec,
            mapping.get("command_codec"),
            "node-control command codec",
        )
        raw_precondition = mapping.get("precondition")
        precondition = (
            None
            if raw_precondition is None
            else _decode_precondition(raw_precondition)
        )
        raw_payload = mapping.get("payload")
        payload = None if raw_payload is None else _decode_payload(raw_payload)
        return NodeControlCommandRequest(
            target=target,
            variable_name=_text(mapping, "variable_name"),
            operation=operation,
            request_id=_text(mapping, "request_id"),
            idempotency_key=_text(mapping, "idempotency_key"),
            command_codec=command_codec,
            precondition=precondition,
            payload=payload,
        )


@dataclass(frozen=True, order=True)
class DelegatedWorkloadNodeControlGrant:
    """Unsigned exact end-to-end authority for one workload request."""

    issuer: str
    key_id: str
    audience: str
    target: NodeControlTarget
    variable_name: str
    operation: NodeControlOperation
    command_codec: ControlPlaneCommandCodec | None
    request_id: str
    idempotency_key: str
    request_digest: NodeControlRequestDigest
    issued_at: int
    not_before: int
    expires_at: int
    jti: str

    def __post_init__(self) -> None:
        _validate_reference(self.issuer, "workload grant issuer")
        _validate_identifier(self.key_id, "workload grant key_id")
        _validate_reference(self.audience, "workload grant audience")
        if not isinstance(self.target, NodeControlTarget):
            raise NodeControlContractError(
                "workload grant target must be NodeControlTarget"
            )
        _validate_identifier(self.variable_name, "workload grant variable_name")
        if not isinstance(self.operation, NodeControlOperation):
            raise NodeControlContractError("workload grant operation is unknown")
        if self.operation is NodeControlOperation.READ_STATE:
            if self.command_codec is not None:
                raise NodeControlContractError(
                    "read-state workload grant must not carry command codec"
                )
        elif not isinstance(self.command_codec, ControlPlaneCommandCodec):
            raise NodeControlContractError(
                "apply-command workload grant requires command codec"
            )
        _validate_identifier(self.request_id, "workload grant request_id")
        _validate_identifier(
            self.idempotency_key,
            "workload grant idempotency_key",
        )
        if not isinstance(self.request_digest, NodeControlRequestDigest):
            raise NodeControlContractError(
                "workload grant request digest must be NodeControlRequestDigest"
            )
        for value, name in (
            (self.issued_at, "workload grant issued_at"),
            (self.not_before, "workload grant not_before"),
            (self.expires_at, "workload grant expires_at"),
        ):
            _validate_epoch(value, name)
        if self.not_before < self.issued_at:
            raise NodeControlContractError(
                "workload grant not_before must not precede issued_at"
            )
        if self.expires_at <= self.not_before:
            raise NodeControlContractError(
                "workload grant expires_at must follow not_before"
            )
        if (
            self.expires_at - self.issued_at
            > MAX_WORKLOAD_NODE_CONTROL_GRANT_LIFETIME_SECONDS
        ):
            raise NodeControlContractError(
                "workload grant lifetime must not exceed 300 seconds"
            )
        _validate_identifier(self.jti, "workload grant jti")

    def descriptor(self) -> dict[str, object]:
        return {
            "issuer": self.issuer,
            "key_id": self.key_id,
            "audience": self.audience,
            "target": self.target.descriptor(),
            "variable_name": self.variable_name,
            "operation": self.operation.value,
            "command_codec": (
                self.command_codec.value if self.command_codec is not None else None
            ),
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "request_digest": self.request_digest.value,
            "issued_at": self.issued_at,
            "not_before": self.not_before,
            "expires_at": self.expires_at,
            "jti": self.jti,
        }


class DelegatedWorkloadNodeControlGrantCodec:
    """Strict descriptor codec for unsigned workload end-to-end grants."""

    def encode(
        self,
        grant: DelegatedWorkloadNodeControlGrant,
    ) -> dict[str, object]:
        if not isinstance(grant, DelegatedWorkloadNodeControlGrant):
            raise NodeControlContractError(
                "encode requires DelegatedWorkloadNodeControlGrant"
            )
        return grant.descriptor()

    def decode(
        self,
        descriptor: Mapping[str, object],
    ) -> DelegatedWorkloadNodeControlGrant:
        mapping = _mapping(descriptor, "delegated workload node-control grant")
        _require_keys(
            mapping,
            _GRANT_KEYS,
            "delegated workload node-control grant",
        )
        return DelegatedWorkloadNodeControlGrant(
            issuer=_text(mapping, "issuer"),
            key_id=_text(mapping, "key_id"),
            audience=_text(mapping, "audience"),
            target=_decode_target(mapping.get("target")),
            variable_name=_text(mapping, "variable_name"),
            operation=_enum(
                NodeControlOperation,
                mapping.get("operation"),
                "workload grant operation",
            ),
            command_codec=_optional_enum(
                ControlPlaneCommandCodec,
                mapping.get("command_codec"),
                "workload grant command codec",
            ),
            request_id=_text(mapping, "request_id"),
            idempotency_key=_text(mapping, "idempotency_key"),
            request_digest=NodeControlRequestDigest(
                _text(mapping, "request_digest")
            ),
            issued_at=_integer(mapping, "issued_at"),
            not_before=_integer(mapping, "not_before"),
            expires_at=_integer(mapping, "expires_at"),
            jti=_text(mapping, "jti"),
        )


@dataclass(frozen=True)
class WorkloadNodeControlGrantVerificationResult:
    """Secret-free pure result of exact workload grant claim comparison."""

    is_accepted: bool
    code: WorkloadNodeControlGrantVerificationCode | None = None

    def __post_init__(self) -> None:
        if type(self.is_accepted) is not bool:
            raise NodeControlContractError(
                "workload grant verification accepted value must be boolean"
            )
        if self.is_accepted and self.code is not None:
            raise NodeControlContractError(
                "accepted workload grant verification has no rejection code"
            )
        if not self.is_accepted and not isinstance(
            self.code,
            WorkloadNodeControlGrantVerificationCode,
        ):
            raise NodeControlContractError(
                "rejected workload grant verification requires bounded code"
            )

    @classmethod
    def allow(cls) -> "WorkloadNodeControlGrantVerificationResult":
        return cls(True)

    @classmethod
    def reject(
        cls,
        code: WorkloadNodeControlGrantVerificationCode,
    ) -> "WorkloadNodeControlGrantVerificationResult":
        return cls(False, code)

    def descriptor(self) -> dict[str, object]:
        return {
            "accepted": self.is_accepted,
            "code": self.code.value if self.code is not None else None,
        }


def verify_workload_node_control_grant(
    grant: object,
    request: NodeControlCommandRequest,
    *,
    expected_issuer: str,
    expected_audience: str,
    now: int,
) -> WorkloadNodeControlGrantVerificationResult:
    """Compare unsigned grant claims to one exact request without crypto or IO."""

    if not isinstance(request, NodeControlCommandRequest):
        raise NodeControlContractError(
            "workload grant verification requires NodeControlCommandRequest"
        )
    _validate_reference(expected_issuer, "expected workload grant issuer")
    _validate_reference(expected_audience, "expected workload grant audience")
    _validate_epoch(now, "workload grant verification time")
    if not isinstance(grant, DelegatedWorkloadNodeControlGrant):
        return WorkloadNodeControlGrantVerificationResult.reject(
            WorkloadNodeControlGrantVerificationCode.GRANT_TYPE_MISMATCH
        )
    if grant.issuer != expected_issuer:
        return WorkloadNodeControlGrantVerificationResult.reject(
            WorkloadNodeControlGrantVerificationCode.ISSUER_MISMATCH
        )
    if grant.audience != expected_audience:
        return WorkloadNodeControlGrantVerificationResult.reject(
            WorkloadNodeControlGrantVerificationCode.AUDIENCE_MISMATCH
        )
    if now < grant.not_before or now >= grant.expires_at:
        return WorkloadNodeControlGrantVerificationResult.reject(
            WorkloadNodeControlGrantVerificationCode.TEMPORALLY_INVALID
        )
    if grant.target.workspace_id != request.target.workspace_id:
        return WorkloadNodeControlGrantVerificationResult.reject(
            WorkloadNodeControlGrantVerificationCode.WORKSPACE_MISMATCH
        )
    if grant.target.graph_revision != request.target.graph_revision:
        return WorkloadNodeControlGrantVerificationResult.reject(
            WorkloadNodeControlGrantVerificationCode.REVISION_MISMATCH
        )
    if grant.target.node_id != request.target.node_id:
        return WorkloadNodeControlGrantVerificationResult.reject(
            WorkloadNodeControlGrantVerificationCode.NODE_MISMATCH
        )
    if grant.target.provider_socket_name != request.target.provider_socket_name:
        return WorkloadNodeControlGrantVerificationResult.reject(
            WorkloadNodeControlGrantVerificationCode.SOCKET_MISMATCH
        )
    if grant.variable_name != request.variable_name:
        return WorkloadNodeControlGrantVerificationResult.reject(
            WorkloadNodeControlGrantVerificationCode.VARIABLE_MISMATCH
        )
    if (
        grant.operation is not request.operation
        or grant.command_codec is not request.command_codec
    ):
        return WorkloadNodeControlGrantVerificationResult.reject(
            WorkloadNodeControlGrantVerificationCode.COMMAND_MISMATCH
        )
    if (
        grant.request_id != request.request_id
        or grant.idempotency_key != request.idempotency_key
        or grant.request_digest != request.canonical_digest()
    ):
        return WorkloadNodeControlGrantVerificationResult.reject(
            WorkloadNodeControlGrantVerificationCode.REQUEST_MISMATCH
        )
    return WorkloadNodeControlGrantVerificationResult.allow()


@dataclass(frozen=True, order=True)
class NodeControlEvidence:
    """One closed evidence code with no free-form provider diagnostics."""

    code: NodeControlEvidenceCode

    def __post_init__(self) -> None:
        if not isinstance(self.code, NodeControlEvidenceCode):
            raise NodeControlContractError("node-control evidence code is unknown")

    def descriptor(self) -> dict[str, str]:
        return {"code": self.code.value}


@dataclass(frozen=True, order=True)
class NodeControlResult:
    """Bounded success/failure result with optional public state and evidence."""

    request_id: str
    status: NodeControlResultStatus
    codec: ControlPlaneResultCodec
    version: int
    payload: ControlStateValue | None
    evidence: tuple[NodeControlEvidence, ...] = ()

    def __post_init__(self) -> None:
        _validate_identifier(self.request_id, "node-control result request_id")
        if not isinstance(self.status, NodeControlResultStatus):
            raise NodeControlContractError("node-control result status is unknown")
        if not isinstance(self.codec, ControlPlaneResultCodec):
            raise NodeControlContractError("node-control result codec is unknown")
        _validate_version(self.version, "node-control result version")
        if self.payload is not None and not isinstance(
            self.payload,
            (ScalarControlState, MapControlState, WeightedRoutingControlState),
        ):
            raise NodeControlContractError(
                "node-control result payload must be bounded state"
            )
        if self.codec is ControlPlaneResultCodec.STATE_V1:
            if self.status is NodeControlResultStatus.SUCCEEDED and self.payload is None:
                raise NodeControlContractError(
                    "successful state result requires state payload"
                )
        elif self.payload is not None:
            raise NodeControlContractError(
                "transition result must not carry state payload"
            )
        if not isinstance(self.evidence, tuple):
            raise NodeControlContractError("node-control result evidence must be a tuple")
        if len(self.evidence) > MAX_NODE_CONTROL_EVIDENCE_ITEMS:
            raise NodeControlContractError(
                "node-control result has too many evidence items"
            )
        if not all(isinstance(item, NodeControlEvidence) for item in self.evidence):
            raise NodeControlContractError(
                "node-control result evidence must be NodeControlEvidence values"
            )
        if self.status is not NodeControlResultStatus.SUCCEEDED and not self.evidence:
            raise NodeControlContractError(
                "rejected or failed node-control result requires evidence"
            )
        _validate_descriptor_size(self.descriptor(), "node-control result")

    def descriptor(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "status": self.status.value,
            "codec": self.codec.value,
            "version": self.version,
            "payload": self.payload.descriptor() if self.payload is not None else None,
            "evidence": [item.descriptor() for item in self.evidence],
        }


class NodeControlResultCodec:
    """Strict codec for bounded node-control outcomes."""

    def encode(self, result: NodeControlResult) -> dict[str, object]:
        if not isinstance(result, NodeControlResult):
            raise NodeControlContractError("encode requires NodeControlResult")
        return result.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> NodeControlResult:
        mapping = _mapping(descriptor, "node-control result")
        _require_keys(mapping, _RESULT_KEYS, "node-control result")
        raw_payload = mapping.get("payload")
        payload = None if raw_payload is None else _decode_state(raw_payload)
        raw_evidence = mapping.get("evidence")
        if not isinstance(raw_evidence, list):
            raise NodeControlContractError(
                "node-control result evidence must be a list"
            )
        evidence: list[NodeControlEvidence] = []
        for raw_item in raw_evidence:
            item = _mapping(raw_item, "node-control evidence")
            _require_keys(item, _EVIDENCE_KEYS, "node-control evidence")
            evidence.append(
                NodeControlEvidence(
                    _enum(
                        NodeControlEvidenceCode,
                        item.get("code"),
                        "node-control evidence code",
                    )
                )
            )
        return NodeControlResult(
            request_id=_text(mapping, "request_id"),
            status=_enum(
                NodeControlResultStatus,
                mapping.get("status"),
                "node-control result status",
            ),
            codec=_enum(
                ControlPlaneResultCodec,
                mapping.get("codec"),
                "node-control result codec",
            ),
            version=_integer(mapping, "version"),
            payload=payload,
            evidence=tuple(evidence),
        )


@dataclass(frozen=True, order=True)
class ControlPlaneVariableDescriptor:
    """Closed public contract for one typed workload control variable."""

    variable_name: str
    kind: ControlPlaneVariableKind
    state_codec: ControlPlaneStateCodec
    command_codec: ControlPlaneCommandCodec
    result_codec: ControlPlaneResultCodec
    description: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.variable_name, "control-plane variable_name")
        if not isinstance(self.kind, ControlPlaneVariableKind):
            raise NodeControlContractError("control-plane variable kind is unknown")
        if not isinstance(self.state_codec, ControlPlaneStateCodec):
            raise NodeControlContractError("control-plane state codec is unknown")
        if not isinstance(self.command_codec, ControlPlaneCommandCodec):
            raise NodeControlContractError("control-plane command codec is unknown")
        if not isinstance(self.result_codec, ControlPlaneResultCodec):
            raise NodeControlContractError("control-plane result codec is unknown")
        expected = _VARIABLE_CODECS[self.kind]
        if (self.state_codec, self.command_codec) != expected:
            raise NodeControlContractError(
                "control-plane variable kind and codecs do not match"
            )
        if self.description is not None:
            _validate_public_text(
                self.description,
                "control-plane variable description",
                max_length=_MAX_DESCRIPTION,
            )

    @property
    def route_set(self) -> ControlRouteSetName:
        return ControlRouteSetName.NODE_CONTROL

    @property
    def capability(self) -> CapabilityName:
        return CapabilityName.NODE_CONTROLLABLE

    def descriptor(self) -> dict[str, object]:
        return {
            "variable_name": self.variable_name,
            "kind": self.kind.value,
            "state_codec": self.state_codec.value,
            "command_codec": self.command_codec.value,
            "result_codec": self.result_codec.value,
            "route_set": self.route_set.value,
            "capability": self.capability.value,
            "description": self.description,
        }


class ControlPlaneVariableDescriptorCodec:
    """Strict codec for typed control-plane variable declarations."""

    def encode(self, variable: ControlPlaneVariableDescriptor) -> dict[str, object]:
        if not isinstance(variable, ControlPlaneVariableDescriptor):
            raise NodeControlContractError(
                "encode requires ControlPlaneVariableDescriptor"
            )
        return variable.descriptor()

    def decode(
        self,
        descriptor: Mapping[str, object],
    ) -> ControlPlaneVariableDescriptor:
        mapping = _mapping(descriptor, "control-plane variable descriptor")
        _require_keys(
            mapping,
            _VARIABLE_KEYS,
            "control-plane variable descriptor",
        )
        if mapping.get("route_set") != ControlRouteSetName.NODE_CONTROL.value:
            raise NodeControlContractError(
                "control-plane variable route_set is not canonical"
            )
        if mapping.get("capability") != CapabilityName.NODE_CONTROLLABLE.value:
            raise NodeControlContractError(
                "control-plane variable capability is not canonical"
            )
        raw_description = mapping.get("description")
        if raw_description is not None and not isinstance(raw_description, str):
            raise NodeControlContractError(
                "control-plane variable description must be text or null"
            )
        return ControlPlaneVariableDescriptor(
            variable_name=_text(mapping, "variable_name"),
            kind=_enum(
                ControlPlaneVariableKind,
                mapping.get("kind"),
                "control-plane variable kind",
            ),
            state_codec=_enum(
                ControlPlaneStateCodec,
                mapping.get("state_codec"),
                "control-plane state codec",
            ),
            command_codec=_enum(
                ControlPlaneCommandCodec,
                mapping.get("command_codec"),
                "control-plane command codec",
            ),
            result_codec=_enum(
                ControlPlaneResultCodec,
                mapping.get("result_codec"),
                "control-plane result codec",
            ),
            description=raw_description,
        )


_COMMAND_STATE_TYPES = {
    ControlPlaneCommandCodec.REPLACE_SCALAR_V1: ScalarControlState,
    ControlPlaneCommandCodec.REPLACE_MAP_V1: MapControlState,
    ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1: WeightedRoutingControlState,
}
_VARIABLE_CODECS = {
    ControlPlaneVariableKind.SCALAR: (
        ControlPlaneStateCodec.SCALAR_V1,
        ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
    ),
    ControlPlaneVariableKind.MAP: (
        ControlPlaneStateCodec.MAP_V1,
        ControlPlaneCommandCodec.REPLACE_MAP_V1,
    ),
    ControlPlaneVariableKind.WEIGHTED_ROUTING: (
        ControlPlaneStateCodec.WEIGHTED_ROUTING_V1,
        ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1,
    ),
}


def _decode_target(value: object) -> NodeControlTarget:
    mapping = _mapping(value, "node-control target")
    _require_keys(mapping, _TARGET_KEYS, "node-control target")
    return NodeControlTarget(
        workspace_id=_text(mapping, "workspace_id"),
        graph_revision=_text(mapping, "graph_revision"),
        node_id=_text(mapping, "node_id"),
        provider_socket_name=_text(mapping, "provider_socket_name"),
    )


def _decode_precondition(value: object) -> ControlPlaneTransitionPrecondition:
    mapping = _mapping(value, "node-control transition precondition")
    _require_keys(
        mapping,
        _PRECONDITION_KEYS,
        "node-control transition precondition",
    )
    return ControlPlaneTransitionPrecondition(
        expected_version=_integer(mapping, "expected_version")
    )


def _decode_payload(value: object) -> NodeControlPayload:
    mapping = _mapping(value, "node-control payload")
    _require_keys(mapping, _PAYLOAD_KEYS, "node-control payload")
    return NodeControlPayload(
        codec=_enum(
            ControlPlaneCommandCodec,
            mapping.get("codec"),
            "node-control payload codec",
        ),
        state=_decode_state(mapping.get("state")),
    )


def _decode_state(value: object) -> ControlStateValue:
    mapping = _mapping(value, "node-control state")
    if not _STATE_COMMON_KEYS <= set(mapping):
        raise NodeControlContractError("node-control state is missing kind")
    kind = _enum(
        ControlPlaneVariableKind,
        mapping.get("kind"),
        "node-control state kind",
    )
    if kind is ControlPlaneVariableKind.SCALAR:
        _require_keys(mapping, _SCALAR_STATE_KEYS, "scalar control state")
        return ScalarControlState(mapping.get("value"))
    if kind is ControlPlaneVariableKind.MAP:
        _require_keys(mapping, _MAP_STATE_KEYS, "map control state")
        entries = _mapping(mapping.get("entries"), "map control state entries")
        return MapControlState(tuple(entries.items()))
    _require_keys(mapping, _WEIGHTED_STATE_KEYS, "weighted routing control state")
    raw_targets = mapping.get("targets")
    if not isinstance(raw_targets, list) or not all(
        isinstance(target, str) for target in raw_targets
    ):
        raise NodeControlContractError(
            "weighted routing targets must be a list of text"
        )
    raw_weights = _mapping(
        mapping.get("weights"),
        "weighted routing control weights",
    )
    return WeightedRoutingControlState(
        targets=tuple(raw_targets),
        weights=tuple(raw_weights.items()),
    )


def _validate_identifier(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_IDENTIFIER
        or not _IDENTIFIER.fullmatch(value)
    ):
        raise NodeControlContractError(f"{name} must be a bounded identifier")
    _reject_secret_or_endpoint(value, name)


def _validate_reference(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_REFERENCE
        or not _REFERENCE.fullmatch(value)
    ):
        raise NodeControlContractError(f"{name} must be a bounded reference")
    _reject_secret_or_endpoint(value, name)


def _validate_public_text(value: object, name: str, *, max_length: int) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > max_length
        or "\x00" in value
    ):
        raise NodeControlContractError(f"{name} must be bounded public text")
    _reject_secret_or_endpoint(value, name)


def _validate_scalar(value: object, name: str) -> None:
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > _MAX_SAFE_INTEGER:
            raise NodeControlContractError(f"{name} integer is out of bounds")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise NodeControlContractError(f"{name} number must be finite")
        return
    if isinstance(value, str):
        _validate_identifier(value, name)
        return
    raise NodeControlContractError(f"{name} must be a public scalar")


def _validate_version(value: object, name: str) -> None:
    if type(value) is not int or value < 0 or value > _MAX_SAFE_INTEGER:
        raise NodeControlContractError(
            f"{name} must be a bounded nonnegative integer"
        )


def _validate_epoch(value: object, name: str) -> None:
    if type(value) is not int or value < 0:
        raise NodeControlContractError(
            f"{name} must be a nonnegative integer epoch second"
        )


def _reject_secret_or_endpoint(value: str, name: str) -> None:
    lowered = value.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise NodeControlContractError(f"{name} contains secret-shaped text")
    if (
        "://" in value
        or value.startswith("//")
        or _HOST_PORT.fullmatch(value)
        or _IPV4.fullmatch(value)
        or lowered == "localhost"
    ):
        raise NodeControlContractError(f"{name} contains endpoint-shaped text")


def _validate_descriptor_size(descriptor: object, name: str) -> None:
    encoded = json.dumps(
        descriptor,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    if len(encoded) > MAX_NODE_CONTROL_PAYLOAD_BYTES:
        raise NodeControlContractError(f"{name} exceeds the public size bound")


def _canonical_digest(descriptor: object) -> str:
    content = json.dumps(
        descriptor,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(content).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise NodeControlContractError(f"{name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise NodeControlContractError(f"{name} keys must be text")
    return value


def _require_keys(
    mapping: Mapping[str, object],
    expected: frozenset[str],
    name: str,
) -> None:
    unknown = set(mapping) - expected
    missing = expected - set(mapping)
    if unknown:
        raise NodeControlContractError(
            f"{name} has unknown keys: {sorted(unknown)}"
        )
    if missing:
        raise NodeControlContractError(
            f"{name} is missing keys: {sorted(missing)}"
        )


def _text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise NodeControlContractError(f"{key} must be text")
    return value


def _integer(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if type(value) is not int:
        raise NodeControlContractError(f"{key} must be an integer")
    return value


def _enum(enum_type: type[StrEnum], value: object, name: str) -> StrEnum:
    if not isinstance(value, str):
        raise NodeControlContractError(f"{name} must be text")
    try:
        return enum_type(value)
    except ValueError as error:
        raise NodeControlContractError(f"{name} is unknown") from error


def _optional_enum(
    enum_type: type[StrEnum],
    value: object,
    name: str,
) -> StrEnum | None:
    return None if value is None else _enum(enum_type, value, name)


__all__ = [
    "ControlPlaneCommandCodec",
    "ControlPlaneResultCodec",
    "ControlPlaneStateCodec",
    "ControlPlaneTransitionPrecondition",
    "ControlPlaneVariableDescriptor",
    "ControlPlaneVariableDescriptorCodec",
    "ControlPlaneVariableKind",
    "DelegatedWorkloadNodeControlGrant",
    "DelegatedWorkloadNodeControlGrantCodec",
    "MAX_NODE_CONTROL_EVIDENCE_ITEMS",
    "MAX_NODE_CONTROL_PAYLOAD_BYTES",
    "MAX_NODE_CONTROL_STATE_ITEMS",
    "MAX_WORKLOAD_NODE_CONTROL_GRANT_LIFETIME_SECONDS",
    "MapControlState",
    "NodeControlCommandRequest",
    "NodeControlCommandRequestCodec",
    "NodeControlContractError",
    "NodeControlEvidence",
    "NodeControlEvidenceCode",
    "NodeControlOperation",
    "NodeControlPayload",
    "NodeControlRequestDigest",
    "NodeControlResult",
    "NodeControlResultCodec",
    "NodeControlResultStatus",
    "NodeControlTarget",
    "ScalarControlState",
    "WeightedRoutingControlState",
    "WorkloadNodeControlGrantVerificationCode",
    "WorkloadNodeControlGrantVerificationResult",
    "verify_workload_node_control_grant",
]
