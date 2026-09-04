"""Pure command and result language for execution-lease recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Mapping

from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    LifecycleOperationKind,
    RecoveryDecisionKind,
    RecoveryScope,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionLeaseDuration
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityRunRecord,
    BoundedEvidence,
    ExecutionLeaseRecoveryEvidence,
    ExecutionRequestRecord,
    OperationActionRecord,
    OperationsRecordError,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
)


@dataclass(frozen=True)
class RecoveryAuthority:
    """Admitted operator identity and authority-reference material."""

    actor_id: str
    authority_reference: str = field(repr=False)
    scopes: tuple[RecoveryScope, ...]

    def __post_init__(self) -> None:
        _bounded_command_text(self.actor_id, "actor_id")
        _bounded_command_text(self.authority_reference, "authority_reference")
        if type(self.scopes) is not tuple or not all(
            type(scope) is RecoveryScope for scope in self.scopes
        ):
            raise InvalidOperationCommand("recovery scopes are invalid")
        object.__setattr__(
            self,
            "scopes",
            tuple(sorted(set(self.scopes), key=lambda scope: scope.value)),
        )


@dataclass(frozen=True)
class RenewActiveExecutionClaim:
    request_id: str
    retained_run_id: RunId
    expected_fence: ExecutionLeaseFence
    authority: RecoveryAuthority
    lease_duration: ExecutionLeaseDuration
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _validate_command(self)

    def descriptor(self) -> dict[str, object]:
        return _command_descriptor(self)

    def intent_fingerprint(self) -> str:
        return _command_fingerprint(self)


@dataclass(frozen=True)
class RenewExpiredExecutionClaim:
    request_id: str
    retained_run_id: RunId
    expected_fence: ExecutionLeaseFence
    authority: RecoveryAuthority
    lease_duration: ExecutionLeaseDuration
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _validate_command(self)

    def descriptor(self) -> dict[str, object]:
        return _command_descriptor(self)

    def intent_fingerprint(self) -> str:
        return _command_fingerprint(self)


@dataclass(frozen=True)
class TakeOverExpiredExecutionClaim:
    request_id: str
    retained_run_id: RunId
    expected_fence: ExecutionLeaseFence
    authority: RecoveryAuthority
    next_worker_id: str
    lease_duration: ExecutionLeaseDuration
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _validate_command(self)
        _bounded_command_text(self.next_worker_id, "next_worker_id")
        if self.next_worker_id == self.expected_fence.worker_id:
            raise InvalidOperationCommand(
                "takeover worker must differ from prior worker"
            )

    def descriptor(self) -> dict[str, object]:
        return _command_descriptor(self)

    def intent_fingerprint(self) -> str:
        return _command_fingerprint(self)


@dataclass(frozen=True)
class AbandonExpiredExecutionClaim:
    request_id: str
    retained_run_id: RunId
    expected_fence: ExecutionLeaseFence
    authority: RecoveryAuthority
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _validate_command(self)

    def descriptor(self) -> dict[str, object]:
        return _command_descriptor(self)

    def intent_fingerprint(self) -> str:
        return _command_fingerprint(self)


ExecutionLeaseRecoveryCommand = (
    RenewActiveExecutionClaim
    | RenewExpiredExecutionClaim
    | TakeOverExpiredExecutionClaim
    | AbandonExpiredExecutionClaim
)


@dataclass(frozen=True)
class ExecutionLeaseRecoveryResult:
    """One fully congruent durable recovery decision and consequence."""

    request: ExecutionRequestRecord
    retained_run: ActivityRunRecord
    decision_event: ActivityEventRecord
    consequence_event: ActivityEventRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        _validate_result_types(self)
        _validate_result_lineage(self)
        _validate_result_decision(self)
        _validate_result_action(self)

    def descriptor(self) -> dict[str, object]:
        recovery = self.decision_event.recovery
        assert recovery is not None
        return {
            "decision": recovery.decision_kind.value,
            "request_id": self.request.identity.request_id,
            "plan_id": self.request.identity.plan_id,
            "retained_run_id": self.retained_run.run_id,
            "decision_event": _event_descriptor(self.decision_event),
            "consequence_event": _event_descriptor(self.consequence_event),
            "action_id": self.action.action_id,
            "action_kind": self.action.action_type.value,
            "recovery": recovery.descriptor(),
            "replayed": self.replayed,
        }


_COMMAND_KINDS = {
    RenewActiveExecutionClaim: RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
    RenewExpiredExecutionClaim: RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
    TakeOverExpiredExecutionClaim: RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
    AbandonExpiredExecutionClaim: RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
}

_CONSEQUENCE_KINDS = {
    RecoveryDecisionKind.RENEW_ACTIVE_CLAIM: (
        ActivityEventKind.REQUEST_CLAIM_RENEWED
    ),
    RecoveryDecisionKind.RENEW_EXPIRED_CLAIM: (
        ActivityEventKind.REQUEST_CLAIM_RENEWED
    ),
    RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM: (
        ActivityEventKind.REQUEST_CLAIM_TAKEN_OVER
    ),
    RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM: (
        ActivityEventKind.REQUEST_CLAIM_ABANDONED
    ),
}

_ACTIVE_RENEW_REPLAY_STATUSES = (
    ActivityRunStatus.CLAIMED,
    ActivityRunStatus.RUNNING,
    ActivityRunStatus.PAUSED,
    ActivityRunStatus.SUCCEEDED,
    ActivityRunStatus.FAILED,
    ActivityRunStatus.COMPENSATING,
    ActivityRunStatus.COMPENSATED,
    ActivityRunStatus.PARTIALLY_FAILED,
    ActivityRunStatus.UNCOMPENSATED_FAILURE,
    ActivityRunStatus.CANCELLED,
)


def _validate_command(command: ExecutionLeaseRecoveryCommand) -> None:
    if type(command) not in _COMMAND_KINDS:
        raise InvalidOperationCommand("recovery command variant is invalid")
    _bounded_command_text(command.request_id, "request_id")
    if type(command.retained_run_id) is not RunId:
        raise InvalidOperationCommand("retained_run_id must be RunId")
    if type(command.expected_fence) is not ExecutionLeaseFence:
        raise InvalidOperationCommand(
            "expected_fence must be ExecutionLeaseFence"
        )
    if type(command.authority) is not RecoveryAuthority:
        raise InvalidOperationCommand("authority must be RecoveryAuthority")
    if type(command.idempotency_key) is not IdempotencyKey:
        raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")
    if not isinstance(command, AbandonExpiredExecutionClaim) and type(
        command.lease_duration
    ) is not ExecutionLeaseDuration:
        raise InvalidOperationCommand(
            "lease_duration must be ExecutionLeaseDuration"
        )


def _command_descriptor(
    command: ExecutionLeaseRecoveryCommand,
) -> dict[str, object]:
    descriptor = {
        "command": _COMMAND_KINDS[type(command)].value,
        "request_id": command.request_id,
        "retained_run_id": command.retained_run_id.value,
        "expected_fence": command.expected_fence.descriptor(),
        "actor_id": command.authority.actor_id,
        "idempotency_key": command.idempotency_key.value,
    }
    if not isinstance(command, AbandonExpiredExecutionClaim):
        descriptor["lease_duration_seconds"] = command.lease_duration.seconds
    if isinstance(command, TakeOverExpiredExecutionClaim):
        descriptor["next_worker_id"] = command.next_worker_id
    return descriptor


def _command_fingerprint(command: ExecutionLeaseRecoveryCommand) -> str:
    document = {
        "command": _COMMAND_KINDS[type(command)].value,
        "request_id": command.request_id,
        "retained_run_id": command.retained_run_id.value,
        "expected_fence": command.expected_fence.descriptor(),
        "actor_id": command.authority.actor_id,
        "authority_reference": command.authority.authority_reference,
    }
    if not isinstance(command, AbandonExpiredExecutionClaim):
        document["lease_duration_seconds"] = command.lease_duration.seconds
    if isinstance(command, TakeOverExpiredExecutionClaim):
        document["next_worker_id"] = command.next_worker_id
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_result_types(result: ExecutionLeaseRecoveryResult) -> None:
    expected = (
        (result.request, ExecutionRequestRecord),
        (result.retained_run, ActivityRunRecord),
        (result.decision_event, ActivityEventRecord),
        (result.consequence_event, ActivityEventRecord),
        (result.action, OperationActionRecord),
    )
    if any(type(value) is not expected_type for value, expected_type in expected):
        raise OperationsRecordError("recovery result records are invalid")
    if type(result.replayed) is not bool:
        raise OperationsRecordError("recovery result replay state is invalid")


def _validate_result_lineage(result: ExecutionLeaseRecoveryResult) -> None:
    request_identity = result.request.identity
    run = result.retained_run
    if (
        run.admission.request_id != request_identity.request_id
        or run.plan_id != request_identity.plan_id
        or result.decision_event.run_id != run.run_id
        or result.consequence_event.run_id != run.run_id
    ):
        raise OperationsRecordError("recovery result lineage is incongruent")
    if (
        result.decision_event.event_id == result.consequence_event.event_id
        or result.consequence_event.ordinal != result.decision_event.ordinal + 1
    ):
        raise OperationsRecordError("recovery result event order is incongruent")
    if not (
        result.decision_event.occurred_at
        == result.consequence_event.occurred_at
        == result.action.created_at
    ):
        raise OperationsRecordError("recovery result observation time is incongruent")


def _validate_result_decision(result: ExecutionLeaseRecoveryResult) -> None:
    decision_event = result.decision_event
    consequence_event = result.consequence_event
    recovery = decision_event.recovery
    if (
        decision_event.kind is not ActivityEventKind.RECOVERY_DECISION_RECORDED
        or type(recovery) is not ExecutionLeaseRecoveryEvidence
        or recovery.retained_run_id.value != result.retained_run.run_id
        or decision_event.failure is not None
        or decision_event.evidence != BoundedEvidence()
    ):
        raise OperationsRecordError("recovery decision event is incongruent")
    if recovery.decision_kind not in _CONSEQUENCE_KINDS:
        raise OperationsRecordError("recovery result decision kind is invalid")
    if (
        consequence_event.kind is not _CONSEQUENCE_KINDS[recovery.decision_kind]
        or consequence_event.failure is not None
        or consequence_event.evidence != BoundedEvidence()
        or consequence_event.recovery is not None
    ):
        raise OperationsRecordError("recovery consequence event is incongruent")

    active = recovery.decision_kind is RecoveryDecisionKind.RENEW_ACTIVE_CLAIM
    if active and result.replayed:
        accepted_run_statuses = _ACTIVE_RENEW_REPLAY_STATUSES
    else:
        expected_run_status = (
            ActivityRunStatus.CLAIMED
            if active
            else ActivityRunStatus.FAILED
        )
        accepted_run_statuses = (expected_run_status,)
    if result.retained_run.status not in accepted_run_statuses:
        raise OperationsRecordError("recovery retained run status is incongruent")

    abandoned = (
        recovery.decision_kind is RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM
    )
    if abandoned:
        if (
            result.request.status is not ExecutionRequestStatus.ABANDONED
            or result.request.claim is not None
        ):
            raise OperationsRecordError(
                "recovery abandonment request state is incongruent"
            )
    elif (
        result.request.status is not ExecutionRequestStatus.CLAIMED
        or result.request.claim is None
        or result.request.claim.fence != recovery.replacement_fence
    ):
        raise OperationsRecordError("recovery claimed request state is incongruent")


def _validate_result_action(result: ExecutionLeaseRecoveryResult) -> None:
    action = result.action
    recovery = result.decision_event.recovery
    assert recovery is not None
    if (
        action.action_type is not LifecycleOperationKind.RECORD_RECOVERY_DECISION
        or action.session_id != result.request.identity.session_id
        or not _valid_idempotency_text(action.idempotency_key)
        or not _valid_fingerprint(action.intent_fingerprint)
    ):
        raise OperationsRecordError("recovery action identity is incongruent")
    payload = action.payload
    if not isinstance(payload, Mapping):
        raise OperationsRecordError("recovery action payload is invalid")
    expected = {
        "execution_request_id": result.request.identity.request_id,
        "plan_id": result.request.identity.plan_id,
        "retained_run_id": result.retained_run.run_id,
        "decision_event_id": result.decision_event.event_id,
        "decision_event_kind": result.decision_event.kind.value,
        "decision_event_ordinal": result.decision_event.ordinal,
        "consequence_event_id": result.consequence_event.event_id,
        "consequence_event_kind": result.consequence_event.kind.value,
        "consequence_event_ordinal": result.consequence_event.ordinal,
        "recovery": recovery.descriptor(),
    }
    abandoned = (
        recovery.decision_kind is RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM
    )
    if not abandoned:
        duration = payload.get("lease_duration_seconds")
        if type(duration) is not int or not 1 <= duration <= 3600:
            raise OperationsRecordError("recovery action duration is invalid")
        expected["lease_duration_seconds"] = duration
    if dict(payload) != expected:
        raise OperationsRecordError("recovery action payload is incongruent")


def _event_descriptor(event: ActivityEventRecord) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "kind": event.kind.value,
        "ordinal": event.ordinal,
    }


def _bounded_command_text(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
    ):
        raise InvalidOperationCommand(f"{field_name} is invalid")


def _valid_idempotency_text(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 200
        and not any(ord(character) < 32 for character in value)
    )


def _valid_fingerprint(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


__all__ = [
    "AbandonExpiredExecutionClaim",
    "ExecutionLeaseRecoveryCommand",
    "ExecutionLeaseRecoveryResult",
    "RecoveryAuthority",
    "RenewActiveExecutionClaim",
    "RenewExpiredExecutionClaim",
    "TakeOverExpiredExecutionClaim",
]
