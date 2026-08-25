"""Authorize and persist one exact failed-run compensation program."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Callable

from control_plane_kit_core.operations import (
    ActivityEventKind,
    ActivityRunStatus,
    FailedRunCompensationEvidence,
    FailedRunCompensationLineage,
    FailedRunCompensationProgram,
    FailedRunCompensationReason,
    FailedRunCompensationStep,
    LifecycleOperationKind,
    RecoveryDecisionKind,
    RecoveryScope,
    RunId,
)
from control_plane_kit_core.planning import (
    ActivityId,
    Compensate,
    NoCompensationRequired,
    NonCompensatable,
)
from control_plane_kit_operations.execution_lease_recovery import RecoveryAuthority
from control_plane_kit_operations.lifecycle import (
    RunLifecycleConflict,
    RunLifecycleDenied,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityRunRecord,
    BoundedEvidence,
    FailedRunCompensationRecord,
    FailureEvidence,
    OperationActionRecord,
    OperationSessionStatus,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
)


_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_SOURCE_FAILURE_DETAIL_KEYS = frozenset(
    {"activity_id", "node_id", "phase", "runtime_id"}
)


class FailedRunCompensationConflict(RunLifecycleConflict):
    """Raised when durable failed-run truth rejects compensation admission."""


class FailedRunCompensationDenied(RunLifecycleDenied, InvalidOperationCommand):
    """Raised when the command lacks explicit compensation authority."""


class FailedRunCompensationIdempotencyConflict(FailedRunCompensationConflict):
    """Raised when one compensation key is reused for different intent."""


class FailedRunCompensationNotFound(FailedRunCompensationConflict):
    """Raised when an owned recovery coordinate is absent."""


@dataclass(frozen=True, slots=True)
class BeginFailedRunCompensation:
    workspace_id: str
    request_id: str
    run_id: RunId
    plan_id: str
    expected_current_graph_id: str
    desired_graph_id: str
    expected_desired_graph_revision: int
    execution_intent_fingerprint: str
    authority: RecoveryAuthority
    reason: FailedRunCompensationReason
    source_failure: FailureEvidence
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        for name in (
            "workspace_id",
            "request_id",
            "plan_id",
            "expected_current_graph_id",
            "desired_graph_id",
        ):
            _require_identifier(getattr(self, name), name)
        if type(self.run_id) is not RunId:
            raise InvalidOperationCommand("run_id must be RunId")
        if (
            type(self.expected_desired_graph_revision) is not int
            or self.expected_desired_graph_revision < 0
        ):
            raise InvalidOperationCommand(
                "expected_desired_graph_revision must be non-negative"
            )
        _require_fingerprint(
            self.execution_intent_fingerprint,
            "execution_intent_fingerprint",
        )
        if type(self.authority) is not RecoveryAuthority:
            raise InvalidOperationCommand("authority must be RecoveryAuthority")
        if RecoveryScope.COMPENSATE not in self.authority.scopes:
            raise FailedRunCompensationDenied("compensation authority is required")
        if type(self.reason) is not FailedRunCompensationReason:
            raise InvalidOperationCommand("compensation reason is invalid")
        _validate_source_failure(self.source_failure)
        if type(self.idempotency_key) is not IdempotencyKey:
            raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")

    def descriptor(self) -> dict[str, object]:
        return {
            "command": LifecycleOperationKind.BEGIN_COMPENSATION.value,
            "workspace_id": self.workspace_id,
            "request_id": self.request_id,
            "run_id": self.run_id.value,
            "plan_id": self.plan_id,
            "expected_current_graph_id": self.expected_current_graph_id,
            "desired_graph_id": self.desired_graph_id,
            "expected_desired_graph_revision": self.expected_desired_graph_revision,
            "execution_intent_fingerprint": self.execution_intent_fingerprint,
            "actor_id": self.authority.actor_id,
            "reason": self.reason.value,
            "source_failure": _failure_descriptor(self.source_failure),
            "idempotency_key": self.idempotency_key.value,
        }

    def intent_fingerprint(self) -> str:
        return _fingerprint(self.descriptor())


@dataclass(frozen=True, slots=True)
class FailedRunCompensationResult:
    record: FailedRunCompensationRecord
    program: FailedRunCompensationProgram
    run: ActivityRunRecord
    event: ActivityEventRecord
    action: OperationActionRecord
    replayed: bool = False


class FailedRunCompensationCommandService:
    """Persist admission truth only; execution belongs to a later child."""

    def __init__(
        self,
        unit_of_work: Callable[[], PostgresUnitOfWork],
        *,
        clock: Callable[[], str],
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._id_factory = id_factory

    def execute(
        self,
        command: BeginFailedRunCompensation,
    ) -> FailedRunCompensationResult:
        if type(command) is not BeginFailedRunCompensation:
            raise InvalidOperationCommand(
                "command must be BeginFailedRunCompensation"
            )
        with self._unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            result = self._admit(stores, command)
            unit_of_work.commit()
            return result

    def _admit(self, stores, command) -> FailedRunCompensationResult:
        try:
            request = stores.execution.get_request_for_update(command.request_id)
        except KeyError as error:
            raise FailedRunCompensationNotFound(
                "execution request was not found"
            ) from error
        identity = request.identity
        if (
            identity.workspace_id != command.workspace_id
            or identity.plan_id != command.plan_id
        ):
            raise FailedRunCompensationConflict(
                "execution request ownership is incongruent"
            )
        stores.activity_history.lock_action_idempotency(
            identity.session_id,
            command.idempotency_key.value,
        )
        existing = stores.activity_history.action_for_idempotency(
            identity.session_id,
            command.idempotency_key.value,
        )
        if existing is not None:
            return _replay(stores, command, existing)
        try:
            session = stores.activity_history.get_session_for_update(
                identity.session_id
            )
            plan_record = stores.activity_history.get_plan(command.plan_id)
            run = stores.execution.get_run_for_update(command.run_id.value)
            workspace = stores.workspaces.get_for_update(command.workspace_id)
        except KeyError as error:
            raise FailedRunCompensationNotFound(
                "failed-run recovery lineage was not found"
            ) from error
        if (
            session.workspace_id != command.workspace_id
            or session.status is not OperationSessionStatus.OPEN
            or plan_record.session_id != session.session_id
            or plan_record.base_graph_id != command.expected_current_graph_id
            or plan_record.desired_graph_id != command.desired_graph_id
            or plan_record.desired_graph_revision
            != command.expected_desired_graph_revision
            or run.plan_id != command.plan_id
            or run.admission.request_id != command.request_id
            or run.status is not ActivityRunStatus.FAILED
        ):
            raise FailedRunCompensationConflict(
                "failed-run recovery lineage is incongruent"
            )
        if (
            workspace.current_graph_id != command.expected_current_graph_id
            or workspace.desired_graph_id != command.desired_graph_id
            or workspace.desired_graph_revision
            != command.expected_desired_graph_revision
            or request.idempotency.intent_fingerprint
            != command.execution_intent_fingerprint
        ):
            raise FailedRunCompensationConflict(
                "workspace or execution intent changed"
            )
        events = stores.execution.events_for_run(command.run_id.value)
        if (
            not events
            or events[-1].kind is not ActivityEventKind.RUN_FAILED
            or events[-1].failure != command.source_failure
        ):
            raise FailedRunCompensationConflict(
                "failed-run terminal evidence is incongruent"
            )
        projection = stores.failed_run_compensations
        if projection.unresolved_attempt_count(command.run_id.value) != 0:
            raise FailedRunCompensationConflict(
                "effect attempt uncertainty must be resolved first"
            )
        successes = projection.successful_effects_for_run(command.run_id.value)
        if projection.succeeded_attempt_count(command.run_id.value) != len(successes):
            raise FailedRunCompensationConflict(
                "succeeded effect evidence is incomplete"
            )
        steps = []
        selected = []
        for success in successes:
            try:
                activity = plan_record.plan.activity(
                    ActivityId(success.attempt_identity.activity_id)
                )
            except (KeyError, TypeError) as error:
                raise FailedRunCompensationConflict(
                    "succeeded effect is absent from the admitted plan"
                ) from error
            compensation = activity.compensation
            if type(compensation) is Compensate:
                selected.append(success)
                steps.append(
                    FailedRunCompensationStep(
                        len(steps) + 1,
                        success,
                        compensation.operation,
                        compensation.material_source,
                    )
                )
            elif type(compensation) is NonCompensatable:
                raise FailedRunCompensationConflict(
                    "succeeded effect is not safely compensatable"
                )
            elif type(compensation) is not NoCompensationRequired:
                raise FailedRunCompensationConflict(
                    "plan compensation is not closed"
                )
        if not steps:
            raise FailedRunCompensationConflict(
                "failed run has no compensatable succeeded effects"
            )
        lineage = FailedRunCompensationLineage(
            command.workspace_id,
            command.request_id,
            command.run_id,
            command.plan_id,
            command.expected_current_graph_id,
            command.desired_graph_id,
            command.expected_desired_graph_revision,
            command.execution_intent_fingerprint,
        )
        evidence = FailedRunCompensationEvidence(
            lineage,
            command.reason,
            _fingerprint(_failure_descriptor(command.source_failure)),
            tuple(selected),
        )
        program_id = self._id_factory()
        program = FailedRunCompensationProgram(
            program_id,
            evidence,
            tuple(steps),
        )
        event_id = self._id_factory()
        action_id = self._id_factory()
        observed_at = self._clock()
        event = ActivityEventRecord(
            event_id,
            command.run_id.value,
            stores.execution.next_event_ordinal(command.run_id.value),
            ActivityEventKind.RUN_COMPENSATION_STARTED,
            observed_at,
            evidence=BoundedEvidence.from_mapping(
                {
                    "program_id": program.program_id,
                    "program_fingerprint": program.fingerprint(),
                }
            ),
        )
        action = OperationActionRecord(
            action_id,
            session.session_id,
            stores.activity_history.next_action_ordinal(session.session_id),
            LifecycleOperationKind.BEGIN_COMPENSATION,
            command.authority.actor_id,
            {
                "decision": RecoveryDecisionKind.BEGIN_COMPENSATION.value,
                "program_id": program.program_id,
                "program_fingerprint": program.fingerprint(),
                "run_id": command.run_id.value,
                "event_id": event.event_id,
            },
            observed_at,
            command.idempotency_key.value,
            command.intent_fingerprint(),
        )
        record = FailedRunCompensationRecord(
            program_id=program.program_id,
            workspace_id=command.workspace_id,
            request_id=command.request_id,
            run_id=command.run_id.value,
            plan_id=command.plan_id,
            session_id=session.session_id,
            action_id=action.action_id,
            event_id=event.event_id,
            actor_id=command.authority.actor_id,
            reason=command.reason.value,
            source_failure=command.source_failure,
            authority_reference_fingerprint=hashlib.sha256(
                command.authority.authority_reference.encode("utf-8")
            ).hexdigest(),
            command_fingerprint=command.intent_fingerprint(),
            evidence_fingerprint=_fingerprint(evidence.descriptor()),
            program_fingerprint=program.fingerprint(),
            created_at=observed_at,
        )
        stores.execution.add_event(event)
        stores.activity_history.add_action(action)
        stores.failed_run_compensations.insert(record, program)
        updated = stores.execution.compare_and_set_run_status(
            command.run_id.value,
            expected=ActivityRunStatus.FAILED,
            replacement=ActivityRunStatus.COMPENSATING,
        )
        if updated is None:
            raise FailedRunCompensationConflict(
                "failed run changed during compensation admission"
            )
        return FailedRunCompensationResult(
            record,
            program,
            updated,
            event,
            action,
        )


def _replay(stores, command, action) -> FailedRunCompensationResult:
    if (
        action.action_type is not LifecycleOperationKind.BEGIN_COMPENSATION
        or action.intent_fingerprint != command.intent_fingerprint()
        or set(action.payload) != {
            "decision",
            "program_id",
            "program_fingerprint",
            "run_id",
            "event_id",
        }
        or action.payload["decision"]
        != RecoveryDecisionKind.BEGIN_COMPENSATION.value
        or action.payload["run_id"] != command.run_id.value
    ):
        raise FailedRunCompensationIdempotencyConflict(
            "compensation idempotency key was reused"
        )
    try:
        record, program = stores.failed_run_compensations.get(
            action.payload["program_id"]
        )
        event = stores.execution.get_event(record.event_id)
        run = stores.execution.get_run(record.run_id)
    except KeyError as error:
        raise FailedRunCompensationConflict(
            "compensation replay truth is incomplete"
        ) from error
    if (
        record.command_fingerprint != command.intent_fingerprint()
        or record.program_fingerprint != action.payload["program_fingerprint"]
        or record.event_id != action.payload["event_id"]
        or event.kind is not ActivityEventKind.RUN_COMPENSATION_STARTED
        or run.status is not ActivityRunStatus.COMPENSATING
    ):
        raise FailedRunCompensationConflict(
            "compensation replay truth is incongruent"
        )
    return FailedRunCompensationResult(
        record,
        program,
        run,
        event,
        action,
        replayed=True,
    )


def _validate_source_failure(value: object) -> None:
    if type(value) is not FailureEvidence:
        raise InvalidOperationCommand("source_failure must be FailureEvidence")
    details = value.details.descriptor()
    if not set(details).issubset(_SOURCE_FAILURE_DETAIL_KEYS):
        raise InvalidOperationCommand("source_failure details are not closed")
    if not all(type(item) is str for item in details.values()):
        raise InvalidOperationCommand("source_failure details must be text")


def _failure_descriptor(value: FailureEvidence) -> dict[str, object]:
    return {
        "category": value.category.value,
        "code": value.code,
        "message": value.message,
        "details": value.details.descriptor(),
    }


def _require_identifier(value: object, field_name: str) -> None:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise InvalidOperationCommand(f"{field_name} is invalid")


def _require_fingerprint(value: object, field_name: str) -> None:
    if type(value) is not str or _FINGERPRINT.fullmatch(value) is None:
        raise InvalidOperationCommand(f"{field_name} is invalid")


def _fingerprint(value: object) -> str:
    preimage = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(preimage).hexdigest()


__all__ = [
    "BeginFailedRunCompensation",
    "FailedRunCompensationCommandService",
    "FailedRunCompensationConflict",
    "FailedRunCompensationDenied",
    "FailedRunCompensationIdempotencyConflict",
    "FailedRunCompensationNotFound",
    "FailedRunCompensationReason",
    "FailedRunCompensationRecord",
    "FailedRunCompensationResult",
]
