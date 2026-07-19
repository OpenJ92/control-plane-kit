"""Application boundary for graph-pinned semantic verification."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import TypeAlias

from control_plane_kit.effects.material import VerificationCheckMaterial
from control_plane_kit.effects.verification import VerificationInterpreterRegistry
from control_plane_kit.execution import (
    BoundedEvidence,
    EndpointContext,
    ObservationRecord,
    ObservationStatus,
    ProbeKind,
    ProbeOutcome,
)
from control_plane_kit.stores import PostgresUnitOfWork
from control_plane_kit.types import EndpointScope
from control_plane_kit.verification import (
    VerificationCompleted,
    VerificationOutcome,
    VerificationResult,
    VerificationUnsupported,
    verification_capability,
)


class VerificationScope(StrEnum):
    EXECUTE = "verification:execute"


class VerificationCommandError(RuntimeError):
    """A semantic verification command could not be completed safely."""


class VerificationCommandDenied(VerificationCommandError):
    pass


class VerificationCommandNotFound(VerificationCommandError):
    pass


class VerificationCommandConflict(VerificationCommandError):
    pass


@dataclass(frozen=True)
class VerificationAuthority:
    actor_id: str
    scopes: frozenset[VerificationScope]

    def __post_init__(self) -> None:
        if not self.actor_id.strip():
            raise ValueError("verification actor identity must not be empty")
        if not isinstance(self.scopes, frozenset) or not all(
            isinstance(scope, VerificationScope) for scope in self.scopes
        ):
            raise TypeError("verification scopes must be a typed frozenset")


@dataclass(frozen=True)
class ExecuteVerification:
    workspace_id: str
    material: VerificationCheckMaterial
    authority: VerificationAuthority

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("verification workspace identity must not be empty")
        if not isinstance(self.material, VerificationCheckMaterial):
            raise TypeError("verification command material must be graph-pinned")
        if not isinstance(self.authority, VerificationAuthority):
            raise TypeError("verification command authority must be typed")


@dataclass(frozen=True)
class VerificationCommandResult:
    intent: ObservationRecord
    result: VerificationResult
    observation: ObservationRecord


UnitOfWorkFactory: TypeAlias = Callable[[], PostgresUnitOfWork]


class VerificationCommandService:
    """Execute one read-only check between two short Postgres transactions."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        registry: VerificationInterpreterRegistry,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._registry = registry
        self._clock = clock
        self._id_factory = id_factory

    def execute(self, command: ExecuteVerification) -> VerificationCommandResult:
        if VerificationScope.EXECUTE not in command.authority.scopes:
            raise VerificationCommandDenied("scope 'verification:execute' is missing")

        intent_time = self._clock()
        intent = _intent_observation(
            command,
            observation_id=self._id_factory(),
            observed_at=_timestamp(intent_time),
        )
        with self._unit_of_work_factory() as work:
            _require_owned_graph_in(work, command)
            work.stores.observed_state.put(intent)
            work.commit()

        result = self._registry.execute(command.material)
        observation = _observation(
            command,
            result,
            observation_id=self._id_factory(),
            observed_at=_timestamp(_causally_after(intent_time, self._clock())),
        )

        with self._unit_of_work_factory() as work:
            _require_owned_graph_in(work, command)
            work.stores.observed_state.put(observation)
            work.commit()
        return VerificationCommandResult(intent, result, observation)


def _require_owned_graph_in(work: PostgresUnitOfWork, command: ExecuteVerification) -> None:
    try:
        work.stores.workspace.get(command.workspace_id)
        graph = work.stores.graph_topology.get(command.material.graph_id)
    except KeyError as error:
        raise VerificationCommandNotFound(str(error)) from error
    if graph.workspace_id != command.workspace_id:
        raise VerificationCommandConflict("verification graph belongs to another workspace")


def _observation(
    command: ExecuteVerification,
    result: VerificationResult,
    *,
    observation_id: str,
    observed_at: str,
) -> ObservationRecord:
    status, outcome = _observation_truth(result)
    descriptor = result.descriptor()
    return ObservationRecord(
        observation_id=observation_id,
        workspace_id=command.workspace_id,
        subject_id=(
            f"verification:{command.material.node_id}:"
            f"{command.material.check.check_id}"
        ),
        status=status,
        observed_at=observed_at,
        evidence=BoundedEvidence.from_mapping(descriptor),
        graph_id=command.material.graph_id,
        probe_kind=ProbeKind.SEMANTIC_VERIFICATION,
        probe_outcome=outcome,
        endpoint_context=_endpoint_context(command.material.endpoint.scope),
    )


def _intent_observation(
    command: ExecuteVerification,
    *,
    observation_id: str,
    observed_at: str,
) -> ObservationRecord:
    identity = {
        "node_id": command.material.node_id,
        "graph_id": command.material.graph_id,
        "check_id": command.material.check.check_id,
    }
    return ObservationRecord(
        observation_id=observation_id,
        workspace_id=command.workspace_id,
        subject_id=(
            f"verification:{command.material.node_id}:"
            f"{command.material.check.check_id}"
        ),
        status=ObservationStatus.STARTING,
        observed_at=observed_at,
        evidence=BoundedEvidence.from_mapping(
            {
                "type": "verification-intent",
                "identity": identity,
                "capability": verification_capability(
                    command.material.check
                ).value,
            }
        ),
        graph_id=command.material.graph_id,
        probe_kind=ProbeKind.SEMANTIC_VERIFICATION,
        probe_outcome=ProbeOutcome.UNKNOWN,
        endpoint_context=_endpoint_context(command.material.endpoint.scope),
    )


def _observation_truth(
    result: VerificationResult,
) -> tuple[ObservationStatus, ProbeOutcome]:
    if isinstance(result, VerificationUnsupported):
        return ObservationStatus.UNSUPPORTED, ProbeOutcome.UNSUPPORTED
    return {
        VerificationOutcome.PASSED: (
            ObservationStatus.VERIFIED,
            ProbeOutcome.VERIFIED,
        ),
        VerificationOutcome.FAILED: (
            ObservationStatus.VERIFICATION_FAILED,
            ProbeOutcome.VERIFICATION_FAILED,
        ),
        VerificationOutcome.TIMED_OUT: (
            ObservationStatus.TIMED_OUT,
            ProbeOutcome.TIMED_OUT,
        ),
        VerificationOutcome.MALFORMED: (
            ObservationStatus.MALFORMED,
            ProbeOutcome.MALFORMED,
        ),
        VerificationOutcome.REJECTED: (
            ObservationStatus.REJECTED,
            ProbeOutcome.REJECTED,
        ),
    }[result.outcome]


def _endpoint_context(scope: EndpointScope) -> EndpointContext:
    return {
        EndpointScope.LOCAL: EndpointContext.HOST_LOCAL,
        EndpointScope.PRIVATE: EndpointContext.RUNTIME_PRIVATE,
        EndpointScope.PUBLIC: EndpointContext.PUBLIC,
    }[scope]


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise VerificationCommandError("verification clock must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _causally_after(earlier: datetime, candidate: datetime) -> datetime:
    if not isinstance(earlier, datetime) or earlier.tzinfo is None:
        raise VerificationCommandError("verification clock must be timezone-aware")
    if not isinstance(candidate, datetime) or candidate.tzinfo is None:
        raise VerificationCommandError("verification clock must be timezone-aware")
    if candidate <= earlier:
        return earlier + timedelta(microseconds=1)
    return candidate
