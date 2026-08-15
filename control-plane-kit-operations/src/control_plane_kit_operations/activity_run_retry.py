"""Pure command and result language for linked activity-run retry."""

from __future__ import annotations

from dataclasses import dataclass
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
)
from control_plane_kit_operations.execution_lease_recovery import (
    RecoveryAuthority,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
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


_MAX_ATTEMPT = 2_147_483_647
_REPLAY_STATUSES = frozenset(
    {
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
    }
)


@dataclass(frozen=True)
class RetryFailedActivityRun:
    """Request one linked run under the exact current lease authority."""

    request_id: str
    prior_run_id: RunId
    expected_fence: ExecutionLeaseFence
    authority: RecoveryAuthority
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        if type(self) is not RetryFailedActivityRun:
            raise InvalidOperationCommand("retry command variant is invalid")
        _bounded_command_text(self.request_id, "request_id")
        if type(self.prior_run_id) is not RunId:
            raise InvalidOperationCommand("prior_run_id must be RunId")
        if type(self.expected_fence) is not ExecutionLeaseFence:
            raise InvalidOperationCommand(
                "expected_fence must be ExecutionLeaseFence"
            )
        if type(self.authority) is not RecoveryAuthority:
            raise InvalidOperationCommand(
                "authority must be RecoveryAuthority"
            )
        if type(self.idempotency_key) is not IdempotencyKey:
            raise InvalidOperationCommand(
                "idempotency_key must be IdempotencyKey"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "command": RecoveryDecisionKind.RETRY_AS_NEW_RUN.value,
            "request_id": self.request_id,
            "prior_run_id": self.prior_run_id.value,
            "expected_fence": self.expected_fence.descriptor(),
            "actor_id": self.authority.actor_id,
            "idempotency_key": self.idempotency_key.value,
        }

    def intent_fingerprint(self) -> str:
        document = {
            "command": RecoveryDecisionKind.RETRY_AS_NEW_RUN.value,
            "request_id": self.request_id,
            "prior_run_id": self.prior_run_id.value,
            "expected_fence": self.expected_fence.descriptor(),
            "actor_id": self.authority.actor_id,
            "authority_reference": self.authority.authority_reference,
        }
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ActivityRunRetryResult:
    """One congruent retry decision, linked run, opening event, and action."""

    request: ExecutionRequestRecord
    prior_run: ActivityRunRecord
    run: ActivityRunRecord
    decision_event: ActivityEventRecord
    opened_event: ActivityEventRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        _validate_result_types(self)
        _validate_result_lineage(self)
        _validate_result_events(self)
        _validate_result_action(self)

    def descriptor(self) -> dict[str, object]:
        recovery = self.decision_event.recovery
        assert recovery is not None
        return {
            "decision": recovery.decision_kind.value,
            "request_id": self.request.identity.request_id,
            "plan_id": self.request.identity.plan_id,
            "prior_run_id": self.prior_run.run_id,
            "run_id": self.run.run_id,
            "prior_attempt": self.prior_run.retry.attempt,
            "attempt": self.run.retry.attempt,
            "decision_event": _event_descriptor(self.decision_event),
            "opened_event": _event_descriptor(self.opened_event),
            "action_id": self.action.action_id,
            "action_kind": self.action.action_type.value,
            "recovery": recovery.descriptor(),
            "replayed": self.replayed,
        }


def _validate_result_types(result: ActivityRunRetryResult) -> None:
    records = (
        (result.request, ExecutionRequestRecord),
        (result.prior_run, ActivityRunRecord),
        (result.run, ActivityRunRecord),
        (result.decision_event, ActivityEventRecord),
        (result.opened_event, ActivityEventRecord),
        (result.action, OperationActionRecord),
    )
    if (
        type(result) is not ActivityRunRetryResult
        or any(type(value) is not kind for value, kind in records)
        or type(result.replayed) is not bool
    ):
        raise OperationsRecordError("activity run retry result records are invalid")


def _validate_result_lineage(result: ActivityRunRetryResult) -> None:
    request = result.request
    prior = result.prior_run
    run = result.run
    if (
        request.status is not ExecutionRequestStatus.CLAIMED
        or request.claim is None
        or prior.status is not ActivityRunStatus.FAILED
        or prior.started_at is None
        or prior.settled_at is not None
        or prior.admission.request_id != request.identity.request_id
        or prior.plan_id != request.identity.plan_id
        or run.run_id == prior.run_id
        or run.admission.request_id != request.identity.request_id
        or run.plan_id != request.identity.plan_id
        or run.retry.prior_run_id != prior.run_id
        or run.retry.attempt != prior.retry.attempt + 1
        or run.retry.attempt > _MAX_ATTEMPT
        or prior.metadata.descriptor() != _run_metadata(prior)
        or run.metadata.descriptor() != _run_metadata(run)
    ):
        raise OperationsRecordError("activity run retry lineage is incongruent")
    if result.replayed:
        if run.status not in _REPLAY_STATUSES:
            raise OperationsRecordError(
                "activity run retry replay status is invalid"
            )
    elif run.status is not ActivityRunStatus.CLAIMED:
        raise OperationsRecordError(
            "activity run retry direct status is invalid"
        )


def _validate_result_events(result: ActivityRunRetryResult) -> None:
    decision = result.decision_event
    opened = result.opened_event
    recovery = decision.recovery
    request_claim = result.request.claim
    assert request_claim is not None
    expected_opened_evidence = BoundedEvidence.from_mapping(
        _run_metadata(result.run)
    )
    if (
        decision.kind is not ActivityEventKind.RECOVERY_DECISION_RECORDED
        or type(recovery) is not ExecutionLeaseRecoveryEvidence
        or recovery.decision_kind is not RecoveryDecisionKind.RETRY_AS_NEW_RUN
        or decision.run_id != result.prior_run.run_id
        or recovery.retained_run_id.value != result.prior_run.run_id
        or recovery.prior_fence != recovery.replacement_fence
        or request_claim.fence != recovery.prior_fence
        or opened.kind is not ActivityEventKind.RUN_OPENED
        or opened.run_id != result.run.run_id
        or opened.ordinal != 1
        or opened.evidence != expected_opened_evidence
        or opened.failure is not None
        or decision.event_id == opened.event_id
        or not (
            result.run.created_at
            == decision.occurred_at
            == opened.occurred_at
            == result.action.created_at
        )
    ):
        raise OperationsRecordError("activity run retry events are incongruent")


def _validate_result_action(result: ActivityRunRetryResult) -> None:
    action = result.action
    recovery = result.decision_event.recovery
    assert recovery is not None
    if (
        action.action_type
        is not LifecycleOperationKind.RECORD_RECOVERY_DECISION
        or action.session_id != result.request.identity.session_id
        or not _valid_idempotency_text(action.idempotency_key)
        or not _valid_fingerprint(action.intent_fingerprint)
    ):
        raise OperationsRecordError("activity run retry action is incongruent")
    payload = action.payload
    if not isinstance(payload, Mapping):
        raise OperationsRecordError("activity run retry action payload is invalid")
    expected = {
        "execution_request_id": result.request.identity.request_id,
        "plan_id": result.request.identity.plan_id,
        "prior_run_id": result.prior_run.run_id,
        "run_id": result.run.run_id,
        "prior_attempt": result.prior_run.retry.attempt,
        "attempt": result.run.retry.attempt,
        "decision_event_id": result.decision_event.event_id,
        "decision_event_kind": result.decision_event.kind.value,
        "decision_event_ordinal": result.decision_event.ordinal,
        "opened_event_id": result.opened_event.event_id,
        "opened_event_kind": result.opened_event.kind.value,
        "opened_event_ordinal": result.opened_event.ordinal,
        "recovery": recovery.descriptor(),
    }
    if dict(payload) != expected:
        raise OperationsRecordError(
            "activity run retry action payload is incongruent"
        )


def _run_metadata(run: ActivityRunRecord) -> dict[str, object]:
    metadata: dict[str, object] = {"attempt": run.retry.attempt}
    if run.retry.prior_run_id is not None:
        metadata["prior_run_id"] = run.retry.prior_run_id
    return metadata


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


__all__ = ["ActivityRunRetryResult", "RetryFailedActivityRun"]
