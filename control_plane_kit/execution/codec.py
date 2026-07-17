"""Strict versioned descriptors for closed execution values."""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import TypeVar

from control_plane_kit.execution.values import (
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunRecord,
    ActivityRunStatus,
    AdmittedRun,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionDescriptorValue,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    ExecutionRequestStatus,
    FailureCategory,
    FailureEvidence,
    LegacyImportedRun,
    EndpointContext,
    ObservationFreshness,
    ObservationRecord,
    ObservationStatus,
    ProbeKind,
    ProbeOutcome,
    RetryIdentity,
)
from control_plane_kit.execution.recovery import (
    RecoveryDecisionRecord,
    RecoveryValueError,
    UnknownRecoveryVariant,
    recovery_decision_record_from_descriptor,
)

_EnumValue = TypeVar("_EnumValue", bound=StrEnum)


EXECUTION_SCHEMA = "control-plane-kit.execution"
EXECUTION_VERSION = 2


class ExecutionDescriptorError(ValueError):
    """Base error for the durable execution descriptor boundary."""


class MalformedExecutionDescriptor(ExecutionDescriptorError):
    """Raised when descriptor data has an invalid primitive shape."""


class UnknownExecutionVariant(ExecutionDescriptorError):
    """Raised when a descriptor names an unknown closed variant or version."""


class LossyExecutionDescriptor(ExecutionDescriptorError):
    """Raised when a descriptor cannot be preserved by the typed language."""


class ExecutionDescriptorCodec:
    """Encode and decode the canonical durable execution language."""

    def encode(self, value: ExecutionDescriptorValue) -> dict[str, object]:
        descriptor = {
            "schema": EXECUTION_SCHEMA,
            "version": EXECUTION_VERSION,
            "value": self._encode_value(value),
        }
        return descriptor

    def dumps(self, value: ExecutionDescriptorValue) -> str:
        return json.dumps(self.encode(value), sort_keys=True, separators=(",", ":"))

    def decode(self, descriptor: Mapping[str, object]) -> ExecutionDescriptorValue:
        try:
            top = _mapping(descriptor, "execution descriptor")
            if _text(top, "schema") != EXECUTION_SCHEMA:
                raise UnknownExecutionVariant("unknown execution schema")
            version = top.get("version")
            if type(version) is not int:
                raise MalformedExecutionDescriptor("execution version must be an integer")
            if version != EXECUTION_VERSION:
                raise UnknownExecutionVariant(
                    f"unsupported execution version {version!r}"
                )
            value = self._decode_value(_mapping(top.get("value"), "execution value"))
            if self.encode(value) != _json_value(top):
                raise LossyExecutionDescriptor(
                    "execution descriptor does not round-trip through the typed codec"
                )
            return value
        except ExecutionDescriptorError:
            raise
        except (TypeError, ValueError) as error:
            raise MalformedExecutionDescriptor(
                f"invalid typed execution value: {error}"
            ) from error

    def _encode_value(self, value: ExecutionDescriptorValue) -> dict[str, object]:
        match value:
            case ExecutionRequestRecord():
                return {
                    "kind": "execution-request",
                    "request_id": value.identity.request_id,
                    "workspace_id": value.identity.workspace_id,
                    "session_id": value.identity.session_id,
                    "plan_id": value.identity.plan_id,
                    "status": value.status.value,
                    "requested_by": value.requested_by,
                    "requested_at": value.requested_at,
                    "approval_request_id": value.approval_request_id,
                    "approval_decision_id": value.approval_decision_id,
                    "idempotency_key": value.idempotency.key,
                    "intent_fingerprint": value.idempotency.intent_fingerprint,
                    "claim": _encode_claim(value.claim),
                }
            case ActivityRunRecord():
                return {
                    "kind": "activity-run",
                    "run_id": value.run_id,
                    "plan_id": value.plan_id,
                    "admission": _encode_admission(value.admission),
                    "retry": {
                        "attempt": value.retry.attempt,
                        "prior_run_id": value.retry.prior_run_id,
                    },
                    "status": value.status.value,
                    "created_at": value.created_at,
                    "started_at": value.started_at,
                    "settled_at": value.settled_at,
                    "metadata": value.metadata.descriptor(),
                }
            case ActivityEventRecord():
                return {
                    "kind": "activity-event",
                    "event_id": value.event_id,
                    "run_id": value.run_id,
                    "ordinal": value.ordinal,
                    "event_kind": value.kind.value,
                    "occurred_at": value.occurred_at,
                    "activity_id": value.activity_id,
                    "evidence": value.evidence.descriptor(),
                    "failure": _encode_failure(value.failure),
                    "recovery": (
                        None if value.recovery is None else value.recovery.descriptor()
                    ),
                }
            case ObservationRecord():
                return {
                    "kind": "observation",
                    "observation_id": value.observation_id,
                    "workspace_id": value.workspace_id,
                    "subject_id": value.subject_id,
                    "status": value.status.value,
                    "observed_at": value.observed_at,
                    "evidence": value.evidence.descriptor(),
                    "freshness": value.freshness.value,
                    "graph_id": value.graph_id,
                    "probe_kind": (
                        None if value.probe_kind is None else value.probe_kind.value
                    ),
                    "probe_outcome": (
                        None if value.probe_outcome is None else value.probe_outcome.value
                    ),
                    "endpoint_context": (
                        None
                        if value.endpoint_context is None
                        else value.endpoint_context.value
                    ),
                }
            case ClaimIdentity():
                return {
                    "kind": "claim-identity",
                    "worker_id": value.worker_id,
                    "claimed_at": value.claimed_at,
                    "lease_expires_at": value.lease_expires_at,
                }
            case RetryIdentity():
                return {
                    "kind": "retry-identity",
                    "attempt": value.attempt,
                    "prior_run_id": value.prior_run_id,
                }
            case AdmittedRun():
                return {"kind": "admitted-run", "request_id": value.request_id}
            case LegacyImportedRun():
                return {
                    "kind": "legacy-imported-run",
                    "schema_version": value.schema_version,
                }
            case FailureEvidence():
                return {
                    "kind": "failure-evidence",
                    **_encode_failure(value),
                }
            case _:
                raise MalformedExecutionDescriptor(
                    "encode requires a closed execution descriptor value"
                )

    def _decode_value(self, value: Mapping[str, object]) -> ExecutionDescriptorValue:
        kind = _text(value, "kind")
        try:
            match kind:
                case "execution-request":
                    return ExecutionRequestRecord(
                        identity=ExecutionRequestIdentity(
                            request_id=_text(value, "request_id"),
                            workspace_id=_text(value, "workspace_id"),
                            session_id=_text(value, "session_id"),
                            plan_id=_text(value, "plan_id"),
                        ),
                        status=ExecutionRequestStatus(_text(value, "status")),
                        requested_by=_text(value, "requested_by"),
                        requested_at=_text(value, "requested_at"),
                        approval_request_id=_text(value, "approval_request_id"),
                        approval_decision_id=_text(value, "approval_decision_id"),
                        idempotency=ExecutionIdempotency(
                            key=_text(value, "idempotency_key"),
                            intent_fingerprint=_text(value, "intent_fingerprint"),
                        ),
                        claim=_decode_claim(value.get("claim")),
                    )
                case "activity-run":
                    _require_exact_fields(
                        value,
                        {
                            "kind",
                            "run_id",
                            "plan_id",
                            "admission",
                            "retry",
                            "status",
                            "created_at",
                            "started_at",
                            "settled_at",
                            "metadata",
                        },
                        "activity-run",
                    )
                    return ActivityRunRecord(
                        run_id=_text(value, "run_id"),
                        plan_id=_text(value, "plan_id"),
                        admission=_decode_admission(value.get("admission")),
                        retry=_decode_retry(value.get("retry")),
                        status=ActivityRunStatus(_text(value, "status")),
                        created_at=_text(value, "created_at"),
                        started_at=_optional_text(value, "started_at"),
                        settled_at=_optional_text(value, "settled_at"),
                        metadata=_evidence(value, "metadata"),
                    )
                case "activity-event":
                    _require_exact_fields(
                        value,
                        {
                            "kind",
                            "event_id",
                            "run_id",
                            "ordinal",
                            "event_kind",
                            "occurred_at",
                            "activity_id",
                            "evidence",
                            "failure",
                            "recovery",
                        },
                        "activity-event",
                    )
                    ordinal = value.get("ordinal")
                    if type(ordinal) is not int:
                        raise MalformedExecutionDescriptor("ordinal must be an integer")
                    return ActivityEventRecord(
                        event_id=_text(value, "event_id"),
                        run_id=_text(value, "run_id"),
                        ordinal=ordinal,
                        kind=ActivityEventKind(_text(value, "event_kind")),
                        occurred_at=_text(value, "occurred_at"),
                        activity_id=_optional_text(value, "activity_id"),
                        evidence=_evidence(value, "evidence"),
                        failure=_decode_failure(value.get("failure")),
                        recovery=_decode_recovery(value.get("recovery")),
                    )
                case "observation":
                    return ObservationRecord(
                        observation_id=_text(value, "observation_id"),
                        workspace_id=_text(value, "workspace_id"),
                        subject_id=_text(value, "subject_id"),
                        status=ObservationStatus(_text(value, "status")),
                        observed_at=_text(value, "observed_at"),
                        evidence=_evidence(value, "evidence"),
                        freshness=ObservationFreshness(_text(value, "freshness")),
                        graph_id=_optional_text(value, "graph_id"),
                        probe_kind=_optional_enum(value, "probe_kind", ProbeKind),
                        probe_outcome=_optional_enum(
                            value, "probe_outcome", ProbeOutcome
                        ),
                        endpoint_context=_optional_enum(
                            value, "endpoint_context", EndpointContext
                        ),
                    )
                case "claim-identity":
                    return ClaimIdentity(
                        worker_id=_text(value, "worker_id"),
                        claimed_at=_text(value, "claimed_at"),
                        lease_expires_at=_text(value, "lease_expires_at"),
                    )
                case "retry-identity":
                    attempt = value.get("attempt")
                    if type(attempt) is not int:
                        raise MalformedExecutionDescriptor("attempt must be an integer")
                    return RetryIdentity(
                        attempt=attempt,
                        prior_run_id=_optional_text(value, "prior_run_id"),
                    )
                case "failure-evidence":
                    failure = _decode_failure(value)
                    if failure is None:
                        raise MalformedExecutionDescriptor("failure evidence is required")
                    return failure
                case "admitted-run":
                    return AdmittedRun(_text(value, "request_id"))
                case "legacy-imported-run":
                    version = value.get("schema_version")
                    if type(version) is not int:
                        raise MalformedExecutionDescriptor(
                            "schema_version must be an integer"
                        )
                    return LegacyImportedRun(version)
                case _:
                    raise UnknownExecutionVariant(f"unknown execution value {kind!r}")
        except ValueError as error:
            if isinstance(error, ExecutionDescriptorError):
                raise
            raise UnknownExecutionVariant(str(error)) from error


def _encode_failure(value: FailureEvidence | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "category": value.category.value,
        "code": value.code,
        "message": value.message,
        "details": value.details.descriptor(),
    }


def _encode_claim(value: ClaimIdentity | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "worker_id": value.worker_id,
        "claimed_at": value.claimed_at,
        "lease_expires_at": value.lease_expires_at,
    }


def _decode_claim(value: object) -> ClaimIdentity | None:
    if value is None:
        return None
    claim = _mapping(value, "claim")
    return ClaimIdentity(
        worker_id=_text(claim, "worker_id"),
        claimed_at=_text(claim, "claimed_at"),
        lease_expires_at=_text(claim, "lease_expires_at"),
    )


def _encode_admission(value: object) -> dict[str, object]:
    match value:
        case AdmittedRun(request_id=request_id):
            return {"kind": "admitted", "request_id": request_id}
        case LegacyImportedRun(schema_version=version):
            return {"kind": "legacy-imported", "schema_version": version}
        case _:
            raise MalformedExecutionDescriptor("run admission must be typed")


def _decode_admission(value: object) -> AdmittedRun | LegacyImportedRun:
    admission = _mapping(value, "admission")
    match _text(admission, "kind"):
        case "admitted":
            return AdmittedRun(_text(admission, "request_id"))
        case "legacy-imported":
            version = admission.get("schema_version")
            if type(version) is not int:
                raise MalformedExecutionDescriptor(
                    "schema_version must be an integer"
                )
            return LegacyImportedRun(version)
        case kind:
            raise UnknownExecutionVariant(f"unknown run admission {kind!r}")


def _decode_retry(value: object) -> RetryIdentity:
    retry = _mapping(value, "retry")
    attempt = retry.get("attempt")
    if type(attempt) is not int:
        raise MalformedExecutionDescriptor("attempt must be an integer")
    return RetryIdentity(
        attempt=attempt,
        prior_run_id=_optional_text(retry, "prior_run_id"),
    )


def _decode_failure(value: object) -> FailureEvidence | None:
    if value is None:
        return None
    failure = _mapping(value, "failure")
    try:
        category = FailureCategory(_text(failure, "category"))
    except ValueError as error:
        raise UnknownExecutionVariant(str(error)) from error
    return FailureEvidence(
        category=category,
        code=_text(failure, "code"),
        message=_text(failure, "message"),
        details=_evidence(failure, "details"),
    )


def _decode_recovery(value: object) -> RecoveryDecisionRecord | None:
    if value is None:
        return None
    try:
        return recovery_decision_record_from_descriptor(_mapping(value, "recovery"))
    except UnknownRecoveryVariant as error:
        raise UnknownExecutionVariant(str(error)) from error
    except RecoveryValueError as error:
        raise MalformedExecutionDescriptor(str(error)) from error


def _evidence(value: Mapping[str, object], key: str) -> BoundedEvidence:
    return BoundedEvidence.from_mapping(_mapping(value.get(key), key))


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise MalformedExecutionDescriptor(f"{name} must be an object with text keys")
    return value


def _require_exact_fields(
    value: Mapping[str, object], expected: set[str], name: str
) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details = []
    if missing:
        details.append(f"missing fields: {', '.join(missing)}")
    if unknown:
        details.append(f"unknown fields: {', '.join(unknown)}")
    raise MalformedExecutionDescriptor(f"{name} has {'; '.join(details)}")


def _text(value: Mapping[str, object], key: str) -> str:
    text = value.get(key)
    if not isinstance(text, str) or not text.strip():
        raise MalformedExecutionDescriptor(f"{key} must be non-empty text")
    return text


def _optional_text(value: Mapping[str, object], key: str) -> str | None:
    text = value.get(key)
    if text is None:
        return None
    if not isinstance(text, str) or not text.strip():
        raise MalformedExecutionDescriptor(f"{key} must be non-empty text when present")
    return text


def _optional_enum(
    value: Mapping[str, object],
    key: str,
    enum_type: type[_EnumValue],
) -> _EnumValue | None:
    text = _optional_text(value, key)
    return None if text is None else enum_type(text)


def _json_value(value: object) -> object:
    try:
        return json.loads(json.dumps(value, sort_keys=True))
    except (TypeError, ValueError) as error:
        raise MalformedExecutionDescriptor(
            "descriptor must contain JSON values"
        ) from error


DEFAULT_EXECUTION_CODEC = ExecutionDescriptorCodec()
