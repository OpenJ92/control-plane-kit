"""Pure reference and command language for a resumable deployment program."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from control_plane_kit_core.identity import TrustedCommandContext
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.admission import ExternalReadinessAttestation
from control_plane_kit_operations.records import GraphProjectionLineage
from control_plane_kit_operations.workflows import IdempotencyKey


class InvalidDeploymentProgramContract(ValueError):
    """Raised when deployment-program command data is incoherent."""


@dataclass(frozen=True, slots=True)
class DeploymentProgramReference:
    """Complete caller-supplied identity for one durable deployment program."""

    workspace_id: str
    plan_id: str

    def __post_init__(self) -> None:
        _bounded_identity(self.workspace_id, "workspace_id")
        _bounded_identity(self.plan_id, "plan_id")

    def descriptor(self) -> dict[str, str]:
        return {"workspace_id": self.workspace_id, "plan_id": self.plan_id}


@dataclass(frozen=True, slots=True)
class PrepareDeploymentProgram:
    """Bounded desired intent for effect-free deployment preparation."""

    context: TrustedCommandContext = field(repr=False)
    desired: DeploymentGraph = field(repr=False)
    expected_current: GraphProjectionLineage
    expected_desired: GraphProjectionLineage | None
    expected_desired_graph_revision: int
    title: str = field(repr=False)
    idempotency_key: IdempotencyKey
    approval_comment: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _context(self.context)
        if type(self.desired) is not DeploymentGraph:
            raise InvalidDeploymentProgramContract(
                "desired must be DeploymentGraph"
            )
        _lineage(self.expected_current, "expected_current")
        if self.expected_desired is not None:
            _lineage(self.expected_desired, "expected_desired")
        revision = self.expected_desired_graph_revision
        if type(revision) is not int or revision < 0:
            raise InvalidDeploymentProgramContract(
                "expected_desired_graph_revision must be a nonnegative integer"
            )
        if self.expected_desired is None and revision != 0:
            raise InvalidDeploymentProgramContract(
                "absent expected_desired requires revision zero"
            )
        if self.expected_desired is not None and revision == 0:
            raise InvalidDeploymentProgramContract(
                "present expected_desired requires a positive revision"
            )
        _bounded_content(self.title, "title")
        _idempotency_key(self.idempotency_key)
        if self.approval_comment is not None:
            _bounded_content(self.approval_comment, "approval_comment")

    def descriptor(self) -> dict[str, object]:
        return {
            "command": "prepare-deployment-program",
            "workspace_id": self.context.workspace_id,
            "expected_current": _lineage_descriptor(self.expected_current),
            "expected_desired": (
                None
                if self.expected_desired is None
                else _lineage_descriptor(self.expected_desired)
            ),
            "expected_desired_graph_revision": (
                self.expected_desired_graph_revision
            ),
            "idempotency_key": self.idempotency_key.value,
            "approval_comment_present": self.approval_comment is not None,
        }


@dataclass(frozen=True, slots=True)
class ProgressDeploymentProgram:
    """Bounded caller-held evidence for one future progression attempt."""

    context: TrustedCommandContext = field(repr=False)
    reference: DeploymentProgramReference
    readiness: tuple[ExternalReadinessAttestation, ...] = field(repr=False)
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _context(self.context)
        if type(self.reference) is not DeploymentProgramReference:
            raise InvalidDeploymentProgramContract(
                "reference must be DeploymentProgramReference"
            )
        if self.context.workspace_id != self.reference.workspace_id:
            raise InvalidDeploymentProgramContract(
                "command context and program reference must share a workspace"
            )
        if type(self.readiness) is not tuple:
            raise InvalidDeploymentProgramContract(
                "readiness must be a tuple of ExternalReadinessAttestation values"
            )
        if not all(
            type(item) is ExternalReadinessAttestation for item in self.readiness
        ):
            raise InvalidDeploymentProgramContract(
                "readiness must contain exact ExternalReadinessAttestation values"
            )
        activity_ids = tuple(item.activity_id for item in self.readiness)
        if len(set(activity_ids)) != len(activity_ids):
            raise InvalidDeploymentProgramContract(
                "readiness contains duplicate activity identities"
            )
        object.__setattr__(
            self,
            "readiness",
            tuple(sorted(self.readiness, key=lambda item: item.activity_id)),
        )
        _idempotency_key(self.idempotency_key)

    def descriptor(self) -> dict[str, object]:
        return {
            "command": "progress-deployment-program",
            "reference": self.reference.descriptor(),
            "readiness_count": len(self.readiness),
            "idempotency_key": self.idempotency_key.value,
        }


DeploymentProgramCommand: TypeAlias = (
    PrepareDeploymentProgram | ProgressDeploymentProgram
)


def _context(value: object) -> None:
    if type(value) is not TrustedCommandContext:
        raise InvalidDeploymentProgramContract(
            "context must be TrustedCommandContext"
        )
    _bounded_identity(value.workspace_id, "context workspace_id")


def _lineage(value: object, field_name: str) -> None:
    if type(value) is not GraphProjectionLineage:
        raise InvalidDeploymentProgramContract(
            f"{field_name} must be GraphProjectionLineage"
        )


def _idempotency_key(value: object) -> None:
    if type(value) is not IdempotencyKey:
        raise InvalidDeploymentProgramContract(
            "idempotency_key must be IdempotencyKey"
        )


def _bounded_identity(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
    ):
        raise InvalidDeploymentProgramContract(
            f"{field_name} must be nonempty bounded text"
        )


def _bounded_content(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or not value.strip()
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
    ):
        raise InvalidDeploymentProgramContract(
            f"{field_name} must be nonempty bounded text"
        )


def _lineage_descriptor(value: GraphProjectionLineage) -> dict[str, str]:
    return {
        "authored_graph_id": value.authored_graph_id,
        "realized_projection_id": value.realized_projection_id,
    }


__all__ = [
    "DeploymentProgramCommand",
    "DeploymentProgramReference",
    "InvalidDeploymentProgramContract",
    "PrepareDeploymentProgram",
    "ProgressDeploymentProgram",
]
