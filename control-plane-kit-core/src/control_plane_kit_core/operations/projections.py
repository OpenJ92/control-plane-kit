"""Pure operator read projection contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.operations.http import HttpAuthScope, HttpOperationSafety
from control_plane_kit_core.operations.services import ControlPlaneServiceRole


class InvalidReadProjectionContract(ValueError):
    """Raised when read projection contract data is incoherent."""


class ReadProjectionKind(StrEnum):
    """Closed operator read projection identities."""

    WORKSPACE = "workspace"
    CURRENT_GRAPH = "current-graph"
    DESIRED_GRAPH = "desired-graph"
    OPERATOR_GRAPH = "operator-graph"
    ACTIVITY_TIMELINE = "activity-timeline"
    OPEN_SESSIONS = "open-sessions"
    SESSION_DETAIL = "session-detail"
    PLAN_DETAIL = "plan-detail"
    PENDING_APPROVALS = "pending-approvals"
    OBSERVED_STATE = "observed-state"
    CONTROL_SURFACE = "control-surface"


class ReadProjectionPolicy(StrEnum):
    """Closed projection redaction and evidence policy."""

    REDACTED_WORKSPACE = "redacted-workspace"
    REDACTED_GRAPH_DESCRIPTOR = "redacted-graph-descriptor"
    REDACTED_CONTROL_SURFACE = "redacted-control-surface"
    REDACTED_PAGED_HISTORY = "redacted-paged-history"
    PINNED_PLAN_AND_RECOVERY = "pinned-plan-and-recovery"
    OBSERVED_STATE_EVIDENCE = "observed-state-evidence"


@dataclass(frozen=True)
class ReadProjectionContract:
    """One read projection boundary without a read-service callback."""

    operation_id: str
    kind: ReadProjectionKind
    service_role: ControlPlaneServiceRole
    response_schema: str
    policy: ReadProjectionPolicy
    auth_scope: HttpAuthScope
    safety: HttpOperationSafety
    requires_workspace_scope: bool
    paged: bool
    max_page_size: int | None = None

    def __post_init__(self) -> None:
        _validate_identity(self.operation_id, "operation_id")
        if not isinstance(self.kind, ReadProjectionKind):
            raise InvalidReadProjectionContract(
                "kind must be ReadProjectionKind"
            )
        if not isinstance(self.service_role, ControlPlaneServiceRole):
            raise InvalidReadProjectionContract(
                "service_role must be ControlPlaneServiceRole"
            )
        if self.service_role is not ControlPlaneServiceRole.READS:
            raise InvalidReadProjectionContract("read projections use reads service")
        _validate_identity(self.response_schema, "response_schema")
        if not isinstance(self.policy, ReadProjectionPolicy):
            raise InvalidReadProjectionContract(
                "policy must be ReadProjectionPolicy"
            )
        if self.auth_scope is not HttpAuthScope.READ:
            raise InvalidReadProjectionContract("read projections require read auth")
        if self.safety is not HttpOperationSafety.READ_ONLY:
            raise InvalidReadProjectionContract("read projections must be read-only")
        _validate_bool(self.requires_workspace_scope, "requires_workspace_scope")
        _validate_bool(self.paged, "paged")
        if self.paged:
            if (
                not isinstance(self.max_page_size, int)
                or self.max_page_size < 1
                or self.max_page_size > 500
            ):
                raise InvalidReadProjectionContract(
                    "paged projections require bounded max_page_size"
                )
        elif self.max_page_size is not None:
            raise InvalidReadProjectionContract(
                "unpaged projections must not declare max_page_size"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind.value,
            "service_role": self.service_role.value,
            "response_schema": self.response_schema,
            "policy": self.policy.value,
            "auth_scope": self.auth_scope.value,
            "safety": self.safety.value,
            "requires_workspace_scope": self.requires_workspace_scope,
            "paged": self.paged,
            "max_page_size": self.max_page_size,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "ReadProjectionContract":
        if set(value) != {
            "operation_id",
            "kind",
            "service_role",
            "response_schema",
            "policy",
            "auth_scope",
            "safety",
            "requires_workspace_scope",
            "paged",
            "max_page_size",
        }:
            raise InvalidReadProjectionContract(
                "read projection descriptor has unexpected keys"
            )
        try:
            return cls(
                operation_id=_text(value["operation_id"], "operation_id"),
                kind=ReadProjectionKind(_text(value["kind"], "kind")),
                service_role=ControlPlaneServiceRole(
                    _text(value["service_role"], "service_role")
                ),
                response_schema=_text(value["response_schema"], "response_schema"),
                policy=ReadProjectionPolicy(_text(value["policy"], "policy")),
                auth_scope=HttpAuthScope(_text(value["auth_scope"], "auth_scope")),
                safety=HttpOperationSafety(_text(value["safety"], "safety")),
                requires_workspace_scope=_bool(
                    value["requires_workspace_scope"],
                    "requires_workspace_scope",
                ),
                paged=_bool(value["paged"], "paged"),
                max_page_size=_optional_int(
                    value["max_page_size"],
                    "max_page_size",
                ),
            )
        except ValueError as error:
            raise InvalidReadProjectionContract(str(error)) from error


@dataclass(frozen=True)
class ReadProjectionSet:
    """Closed read projection vocabulary for operator-facing adapters."""

    projections: tuple[ReadProjectionContract, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.projections, tuple) or not all(
            isinstance(projection, ReadProjectionContract)
            for projection in self.projections
        ):
            raise InvalidReadProjectionContract(
                "projections must be ReadProjectionContract values"
            )
        _reject_duplicates(
            "operation_id",
            (projection.operation_id for projection in self.projections),
        )
        _reject_duplicates("kind", (projection.kind for projection in self.projections))
        expected = {
            definition.operation_id
            for definition in _CANONICAL_PROJECTIONS
        }
        if {projection.operation_id for projection in self.projections} != expected:
            raise InvalidReadProjectionContract(
                "read projection set must cover the canonical projections"
            )
        ordered = tuple(
            sorted(self.projections, key=lambda projection: projection.operation_id)
        )
        object.__setattr__(self, "projections", ordered)

    def projection(self, operation_id: str) -> ReadProjectionContract:
        _validate_identity(operation_id, "operation_id")
        for projection in self.projections:
            if projection.operation_id == operation_id:
                return projection
        raise InvalidReadProjectionContract(
            f"unknown operation_id {operation_id!r}"
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "operator-read-projection-set",
            "projections": [
                projection.descriptor() for projection in self.projections
            ],
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "ReadProjectionSet":
        if set(value) != {"kind", "projections"}:
            raise InvalidReadProjectionContract(
                "read projection set descriptor has unexpected keys"
            )
        if value["kind"] != "operator-read-projection-set":
            raise InvalidReadProjectionContract(
                "read projection set descriptor has wrong kind"
            )
        projections = value["projections"]
        if not isinstance(projections, list):
            raise InvalidReadProjectionContract("projections must be a list")
        return cls(
            tuple(
                ReadProjectionContract.from_descriptor(
                    _mapping(projection, "projection")
                )
                for projection in projections
            )
        )


def canonical_operator_read_projection_set() -> ReadProjectionSet:
    """Return the pure operator read projection contract."""

    return ReadProjectionSet(
        tuple(
            ReadProjectionContract(
                operation_id=definition.operation_id,
                kind=definition.kind,
                service_role=ControlPlaneServiceRole.READS,
                response_schema=definition.response_schema,
                policy=definition.policy,
                auth_scope=HttpAuthScope.READ,
                safety=HttpOperationSafety.READ_ONLY,
                requires_workspace_scope=True,
                paged=definition.paged,
                max_page_size=definition.max_page_size,
            )
            for definition in _CANONICAL_PROJECTIONS
        )
    )


@dataclass(frozen=True)
class _ProjectionDefinition:
    operation_id: str
    kind: ReadProjectionKind
    response_schema: str
    policy: ReadProjectionPolicy
    paged: bool = False
    max_page_size: int | None = None


_CANONICAL_PROJECTIONS = (
    _ProjectionDefinition(
        "read.activity-timeline",
        ReadProjectionKind.ACTIVITY_TIMELINE,
        "ActivityTimelineReadResponse",
        ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
        paged=True,
        max_page_size=200,
    ),
    _ProjectionDefinition(
        "read.control-surface",
        ReadProjectionKind.CONTROL_SURFACE,
        "ControlSurfaceReadResponse",
        ReadProjectionPolicy.REDACTED_CONTROL_SURFACE,
    ),
    _ProjectionDefinition(
        "read.current-graph",
        ReadProjectionKind.CURRENT_GRAPH,
        "GraphReadResponse",
        ReadProjectionPolicy.REDACTED_GRAPH_DESCRIPTOR,
    ),
    _ProjectionDefinition(
        "read.desired-graph",
        ReadProjectionKind.DESIRED_GRAPH,
        "GraphReadResponse",
        ReadProjectionPolicy.REDACTED_GRAPH_DESCRIPTOR,
    ),
    _ProjectionDefinition(
        "read.observed-state",
        ReadProjectionKind.OBSERVED_STATE,
        "ObservedStateReadResponse",
        ReadProjectionPolicy.OBSERVED_STATE_EVIDENCE,
    ),
    _ProjectionDefinition(
        "read.open-sessions",
        ReadProjectionKind.OPEN_SESSIONS,
        "OpenSessionsReadResponse",
        ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
        paged=True,
        max_page_size=200,
    ),
    _ProjectionDefinition(
        "read.operator-graph",
        ReadProjectionKind.OPERATOR_GRAPH,
        "OperatorGraphReadResponse",
        ReadProjectionPolicy.REDACTED_GRAPH_DESCRIPTOR,
    ),
    _ProjectionDefinition(
        "read.pending-approvals",
        ReadProjectionKind.PENDING_APPROVALS,
        "PendingApprovalsReadResponse",
        ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
        paged=True,
        max_page_size=200,
    ),
    _ProjectionDefinition(
        "read.plan-detail",
        ReadProjectionKind.PLAN_DETAIL,
        "PlanDetailReadResponse",
        ReadProjectionPolicy.PINNED_PLAN_AND_RECOVERY,
    ),
    _ProjectionDefinition(
        "read.session-detail",
        ReadProjectionKind.SESSION_DETAIL,
        "SessionDetailReadResponse",
        ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
    ),
    _ProjectionDefinition(
        "read.workspace",
        ReadProjectionKind.WORKSPACE,
        "WorkspaceReadResponse",
        ReadProjectionPolicy.REDACTED_WORKSPACE,
    ),
)


def _reject_duplicates(field: str, values: object) -> None:
    sequence = tuple(values)
    if len(sequence) != len(set(sequence)):
        raise InvalidReadProjectionContract(f"{field} values must be unique")


def _validate_identity(value: str, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidReadProjectionContract(f"{field} must be non-empty text")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(character not in allowed for character in value):
        raise InvalidReadProjectionContract(
            f"{field} must contain only letters, numbers, dots, dashes, or underscores"
        )


def _validate_bool(value: object, field: str) -> None:
    if type(value) is not bool:
        raise InvalidReadProjectionContract(f"{field} must be bool")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidReadProjectionContract(f"{field} must be text")
    return value


def _bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise InvalidReadProjectionContract(f"{field} must be bool")
    return value


def _optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise InvalidReadProjectionContract(f"{field} must be int")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidReadProjectionContract(f"{field} must be a descriptor")
    return value
