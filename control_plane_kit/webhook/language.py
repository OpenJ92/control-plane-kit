"""Closed durable values and interpreters for package-owned webhook delivery."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
import re
from typing import Mapping, TypeAlias
from urllib.parse import urlsplit

from control_plane_kit.secrets import SecretReference


MAX_WEBHOOK_PAYLOAD_BYTES = 1_048_576
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_HEADER = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,126}\Z")
_FAILURE_CODE = re.compile(r"[a-z][a-z0-9.-]{0,127}\Z")


class WebhookScheme(StrEnum):
    HTTP = "http"
    HTTPS = "https"


class WebhookContentType(StrEnum):
    JSON = "application/json"
    CLOUD_EVENTS_JSON = "application/cloudevents+json"
    OCTET_STREAM = "application/octet-stream"


class WebhookSigningAlgorithm(StrEnum):
    HMAC_SHA256 = "hmac-sha256"


class WebhookDeliveryStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    IN_FLIGHT = "in-flight"
    RETRY_SCHEDULED = "retry-scheduled"
    DELIVERED = "delivered"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    DEAD_LETTER = "dead-letter"
    OPERATOR_REQUIRED = "operator-required"


class WebhookAttemptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable-failure"
    TERMINAL_FAILURE = "terminal-failure"
    UNCERTAIN = "uncertain"


class WebhookClaimReleaseReason(StrEnum):
    ABANDONED = "abandoned"
    EXPIRED = "expired"


class WebhookScope(StrEnum):
    ENQUEUE = "webhook:enqueue"
    DISPATCH = "webhook:dispatch"
    RECOVER = "webhook:recover"
    READ = "webhook:read"


@dataclass(frozen=True, slots=True)
class WebhookDeliveryIdentity:
    workspace_id: str
    delivery_id: str

    def __post_init__(self) -> None:
        _identifier("workspace_id", self.workspace_id)
        _identifier("delivery_id", self.delivery_id)

    def descriptor(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "delivery_id": self.delivery_id,
        }


@dataclass(frozen=True, slots=True)
class WebhookEndpoint:
    endpoint_id: str
    url: str
    scheme: WebhookScheme = field(init=False)

    def __post_init__(self) -> None:
        _identifier("endpoint_id", self.endpoint_id)
        if not isinstance(self.url, str) or len(self.url.encode()) > 2_048:
            raise ValueError("webhook endpoint URL must be bounded text")
        if any(value in self.url for value in "\r\n\0\\") or any(
            value.isspace() for value in self.url
        ):
            raise ValueError("webhook endpoint URL must be single-line text")
        parsed = urlsplit(self.url)
        try:
            scheme = WebhookScheme(parsed.scheme.lower())
        except ValueError as error:
            raise ValueError("webhook endpoint scheme must be HTTP or HTTPS") from error
        if (
            parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
        ):
            raise ValueError("webhook endpoint URL shape is unsafe")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("webhook endpoint port is invalid") from error
        if port is not None and not 1 <= port <= 65_535:
            raise ValueError("webhook endpoint port is invalid")
        object.__setattr__(self, "scheme", scheme)

    def descriptor(self) -> dict[str, str]:
        return {
            "endpoint_id": self.endpoint_id,
            "url": self.url,
            "scheme": self.scheme.value,
        }


@dataclass(frozen=True, slots=True)
class WebhookPayload:
    content_type: WebhookContentType
    body: bytes = field(repr=False)
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.content_type, WebhookContentType):
            raise TypeError("webhook content type must be typed")
        if (
            not isinstance(self.body, bytes)
            or not self.body
            or len(self.body) > MAX_WEBHOOK_PAYLOAD_BYTES
        ):
            raise ValueError("webhook payload must be nonempty and bounded")
        object.__setattr__(self, "content_digest", hashlib.sha256(self.body).hexdigest())
        if self.content_type in {
            WebhookContentType.JSON,
            WebhookContentType.CLOUD_EVENTS_JSON,
        }:
            try:
                parsed = json.loads(self.body)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("webhook JSON payload is malformed") from error
            if (
                self.content_type is WebhookContentType.CLOUD_EVENTS_JSON
                and not isinstance(parsed, dict)
            ):
                raise ValueError("CloudEvents webhook payload must be an object")

    def descriptor(self) -> dict[str, str]:
        return {
            "content_type": self.content_type.value,
            "body_base64": base64.b64encode(self.body).decode("ascii"),
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True, slots=True)
class WebhookSigning:
    secret_reference: SecretReference
    algorithm: WebhookSigningAlgorithm = WebhookSigningAlgorithm.HMAC_SHA256
    header_name: str = "X-CPK-Webhook-Signature"

    def __post_init__(self) -> None:
        if not isinstance(self.secret_reference, SecretReference):
            raise TypeError("webhook signing requires an opaque SecretReference")
        if not isinstance(self.algorithm, WebhookSigningAlgorithm):
            raise TypeError("webhook signing algorithm must be typed")
        if not isinstance(self.header_name, str) or not _HEADER.fullmatch(
            self.header_name
        ):
            raise ValueError("webhook signing header is invalid")
        if self.header_name.lower() in {
            "authorization",
            "connection",
            "content-length",
            "content-type",
            "host",
            "transfer-encoding",
        }:
            raise ValueError("webhook signing header is reserved")

    def descriptor(self) -> dict[str, str]:
        return {
            "reference_id": self.secret_reference.reference_id,
            "algorithm": self.algorithm.value,
            "header_name": self.header_name,
        }


@dataclass(frozen=True, slots=True)
class WebhookRetryPolicy:
    max_attempts: int = 5
    initial_backoff_ms: int = 1_000
    maximum_backoff_ms: int = 60_000
    deadline_seconds: int = 86_400

    def __post_init__(self) -> None:
        _bounded("webhook maximum attempts", self.max_attempts, 1, 20)
        _bounded("webhook initial backoff", self.initial_backoff_ms, 1, 60_000)
        _bounded(
            "webhook maximum backoff",
            self.maximum_backoff_ms,
            self.initial_backoff_ms,
            3_600_000,
        )
        _bounded("webhook deadline", self.deadline_seconds, 1, 604_800)

    def backoff_ms(self, completed_attempts: int) -> int:
        _bounded("webhook completed attempts", completed_attempts, 1, self.max_attempts)
        return min(
            self.initial_backoff_ms * (2 ** (completed_attempts - 1)),
            self.maximum_backoff_ms,
        )

    def descriptor(self) -> dict[str, int]:
        return {
            "max_attempts": self.max_attempts,
            "initial_backoff_ms": self.initial_backoff_ms,
            "maximum_backoff_ms": self.maximum_backoff_ms,
            "deadline_seconds": self.deadline_seconds,
        }


@dataclass(frozen=True, slots=True)
class WebhookDeliveryIntent:
    command_id: str
    identity: WebhookDeliveryIdentity
    endpoint: WebhookEndpoint
    payload: WebhookPayload
    retry_policy: WebhookRetryPolicy
    enqueued_at: datetime
    signing: WebhookSigning | None = None

    def __post_init__(self) -> None:
        _identifier("command_id", self.command_id)
        if not isinstance(self.identity, WebhookDeliveryIdentity):
            raise TypeError("webhook intent identity must be typed")
        if not isinstance(self.endpoint, WebhookEndpoint):
            raise TypeError("webhook intent endpoint must be typed")
        if not isinstance(self.payload, WebhookPayload):
            raise TypeError("webhook intent payload must be typed")
        if not isinstance(self.retry_policy, WebhookRetryPolicy):
            raise TypeError("webhook intent retry policy must be typed")
        _aware("enqueued_at", self.enqueued_at)
        if self.signing is not None and not isinstance(self.signing, WebhookSigning):
            raise TypeError("webhook intent signing must be typed")

    @property
    def deadline_at(self) -> datetime:
        return self.enqueued_at + timedelta(seconds=self.retry_policy.deadline_seconds)

    @property
    def intent_fingerprint(self) -> str:
        return _digest(self.descriptor())

    def descriptor(self) -> dict[str, object]:
        return {
            "command_id": self.command_id,
            "identity": self.identity.descriptor(),
            "endpoint": self.endpoint.descriptor(),
            "payload": self.payload.descriptor(),
            "retry_policy": self.retry_policy.descriptor(),
            "enqueued_at": _timestamp(self.enqueued_at),
            "signing": None if self.signing is None else self.signing.descriptor(),
        }


@dataclass(frozen=True, slots=True)
class WebhookAuthority:
    actor_id: str
    workspace_id: str
    scopes: frozenset[WebhookScope]

    def __post_init__(self) -> None:
        _identifier("actor_id", self.actor_id)
        _identifier("workspace_id", self.workspace_id)
        if not isinstance(self.scopes, frozenset) or any(
            not isinstance(value, WebhookScope) for value in self.scopes
        ):
            raise TypeError("webhook authority scopes must be typed")

    def descriptor(self) -> dict[str, object]:
        return {
            "actor_id": self.actor_id,
            "workspace_id": self.workspace_id,
            "scopes": [value.value for value in sorted(self.scopes, key=lambda item: item.value)],
        }


@dataclass(frozen=True, slots=True)
class WebhookEnqueued:
    intent: WebhookDeliveryIntent

    def __post_init__(self) -> None:
        if not isinstance(self.intent, WebhookDeliveryIntent):
            raise TypeError("webhook enqueue event requires a typed intent")


@dataclass(frozen=True, slots=True)
class WebhookClaim:
    identity: WebhookDeliveryIdentity
    claim_id: str
    worker_id: str
    attempt_number: int
    claimed_at: datetime
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        _event_identity(self.identity)
        _identifier("claim_id", self.claim_id)
        _identifier("worker_id", self.worker_id)
        _bounded("webhook claim attempt number", self.attempt_number, 1, 20)
        claimed = _aware("claim claimed_at", self.claimed_at)
        expires = _aware("claim lease_expires_at", self.lease_expires_at)
        if expires <= claimed:
            raise ValueError("webhook claim lease must expire after claim time")
        if expires - claimed > timedelta(days=1):
            raise ValueError("webhook claim lease must be bounded")

    def descriptor(self) -> dict[str, object]:
        return {
            "identity": self.identity.descriptor(),
            "claim_id": self.claim_id,
            "worker_id": self.worker_id,
            "attempt_number": self.attempt_number,
            "claimed_at": _timestamp(self.claimed_at),
            "lease_expires_at": _timestamp(self.lease_expires_at),
        }


@dataclass(frozen=True, slots=True)
class WebhookClaimed:
    claim: WebhookClaim

    def __post_init__(self) -> None:
        if not isinstance(self.claim, WebhookClaim):
            raise TypeError("webhook claimed event requires a typed claim")


@dataclass(frozen=True, slots=True)
class WebhookClaimReleased:
    identity: WebhookDeliveryIdentity
    claim_id: str
    attempt_number: int
    reason: WebhookClaimReleaseReason
    recorded_at: datetime

    def __post_init__(self) -> None:
        _event_identity(self.identity)
        _identifier("claim_id", self.claim_id)
        _bounded("webhook claim attempt number", self.attempt_number, 1, 20)
        if not isinstance(self.reason, WebhookClaimReleaseReason):
            raise TypeError("webhook claim release reason must be typed")
        _aware("claim release recorded_at", self.recorded_at)


@dataclass(frozen=True, slots=True)
class WebhookAttemptStarted:
    identity: WebhookDeliveryIdentity
    attempt_number: int
    claim_id: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        _event_identity(self.identity)
        _bounded("webhook attempt number", self.attempt_number, 1, 20)
        _identifier("claim_id", self.claim_id)
        _aware("attempt recorded_at", self.recorded_at)


@dataclass(frozen=True, slots=True)
class WebhookAttemptFinished:
    identity: WebhookDeliveryIdentity
    attempt_number: int
    claim_id: str
    outcome: WebhookAttemptOutcome
    recorded_at: datetime
    response_status: int | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        _event_identity(self.identity)
        _bounded("webhook attempt number", self.attempt_number, 1, 20)
        _identifier("claim_id", self.claim_id)
        _aware("attempt recorded_at", self.recorded_at)
        if not isinstance(self.outcome, WebhookAttemptOutcome):
            raise TypeError("webhook attempt outcome must be typed")
        if self.outcome is WebhookAttemptOutcome.SUCCEEDED:
            if (
                type(self.response_status) is not int
                or not 200 <= self.response_status <= 299
                or self.failure_code is not None
            ):
                raise ValueError("successful webhook attempt requires only a 2xx status")
        elif self.outcome is WebhookAttemptOutcome.UNCERTAIN:
            if self.response_status is not None or self.failure_code is not None:
                raise ValueError("uncertain webhook attempt carries no guessed result")
        elif (
            (
                self.response_status is not None
                and (
                    type(self.response_status) is not int
                    or not 100 <= self.response_status <= 599
                )
            )
            or not isinstance(self.failure_code, str)
            or not _FAILURE_CODE.fullmatch(self.failure_code)
        ):
            raise ValueError("failed webhook attempt requires a closed failure code")


@dataclass(frozen=True, slots=True)
class WebhookRetryScheduled:
    identity: WebhookDeliveryIdentity
    next_attempt_number: int
    available_at: datetime
    recorded_at: datetime

    def __post_init__(self) -> None:
        _event_identity(self.identity)
        _bounded("webhook retry attempt number", self.next_attempt_number, 1, 20)
        available = _aware("retry available_at", self.available_at)
        recorded = _aware("retry recorded_at", self.recorded_at)
        if available <= recorded:
            raise ValueError("webhook retry availability must follow its record time")


@dataclass(frozen=True, slots=True)
class WebhookDeadLettered:
    identity: WebhookDeliveryIdentity
    reason_code: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        _event_identity(self.identity)
        _failure_code(self.reason_code)
        _aware("dead-letter recorded_at", self.recorded_at)


@dataclass(frozen=True, slots=True)
class WebhookOperatorRequired:
    identity: WebhookDeliveryIdentity
    reason_code: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        _event_identity(self.identity)
        _failure_code(self.reason_code)
        _aware("operator-required recorded_at", self.recorded_at)


WebhookEvent: TypeAlias = (
    WebhookEnqueued
    | WebhookClaimed
    | WebhookClaimReleased
    | WebhookAttemptStarted
    | WebhookAttemptFinished
    | WebhookRetryScheduled
    | WebhookDeadLettered
    | WebhookOperatorRequired
)


@dataclass(frozen=True, slots=True)
class WebhookDeliveryState:
    intent: WebhookDeliveryIntent
    status: WebhookDeliveryStatus
    attempts_started: int
    attempts_completed: int
    updated_at: datetime
    active_claim: WebhookClaim | None = None
    next_attempt_at: datetime | None = None
    last_outcome: WebhookAttemptOutcome | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.intent, WebhookDeliveryIntent):
            raise TypeError("webhook state intent must be typed")
        if not isinstance(self.status, WebhookDeliveryStatus):
            raise TypeError("webhook state status must be typed")
        _bounded("webhook attempts started", self.attempts_started, 0, 20)
        _bounded("webhook attempts completed", self.attempts_completed, 0, 20)
        if self.attempts_completed > self.attempts_started:
            raise ValueError("completed webhook attempts cannot exceed started attempts")
        _aware("updated_at", self.updated_at)
        if self.active_claim is not None and not isinstance(
            self.active_claim, WebhookClaim
        ):
            raise TypeError("webhook state active claim must be typed")
        if self.next_attempt_at is not None:
            _aware("next_attempt_at", self.next_attempt_at)
        if self.last_outcome is not None and not isinstance(
            self.last_outcome, WebhookAttemptOutcome
        ):
            raise TypeError("webhook state last outcome must be typed")
        _validate_state_shape(self)


def evolve_webhook_delivery(
    state: WebhookDeliveryState | None,
    event: WebhookEvent,
) -> WebhookDeliveryState:
    """Apply one immutable event to the webhook delivery projection."""

    if state is None:
        if not isinstance(event, WebhookEnqueued):
            raise ValueError("webhook history must begin with enqueue")
        return WebhookDeliveryState(
            intent=event.intent,
            status=WebhookDeliveryStatus.QUEUED,
            attempts_started=0,
            attempts_completed=0,
            updated_at=event.intent.enqueued_at,
        )
    _same_identity(state, event)
    recorded_at = _event_recorded_at(event)
    if recorded_at < state.updated_at:
        raise ValueError("webhook event time cannot move backward")
    match event:
        case WebhookEnqueued():
            raise ValueError("webhook delivery cannot be enqueued twice")
        case WebhookClaimed(claim=claim):
            if state.status not in {
                WebhookDeliveryStatus.QUEUED,
                WebhookDeliveryStatus.RETRY_SCHEDULED,
            }:
                raise ValueError("webhook delivery cannot be claimed from current status")
            if (
                claim.attempt_number != state.attempts_started + 1
                or claim.attempt_number > state.intent.retry_policy.max_attempts
            ):
                raise ValueError("webhook claim is not for the next bounded attempt")
            if (
                state.next_attempt_at is not None
                and claim.claimed_at < state.next_attempt_at
            ):
                raise ValueError("webhook retry cannot be claimed before availability")
            if (
                claim.claimed_at >= state.intent.deadline_at
                or claim.lease_expires_at > state.intent.deadline_at
            ):
                raise ValueError("webhook claim must fit within the delivery deadline")
            return WebhookDeliveryState(
                intent=state.intent,
                status=WebhookDeliveryStatus.CLAIMED,
                attempts_started=state.attempts_started,
                attempts_completed=state.attempts_completed,
                updated_at=claim.claimed_at,
                active_claim=claim,
                next_attempt_at=state.next_attempt_at,
                last_outcome=state.last_outcome,
            )
        case WebhookClaimReleased(
            claim_id=claim_id,
            attempt_number=number,
            reason=reason,
            recorded_at=recorded_at,
        ):
            claim = state.active_claim
            if state.status is not WebhookDeliveryStatus.CLAIMED or claim is None:
                raise ValueError("webhook claim can be released only before attempt start")
            if claim.claim_id != claim_id or claim.attempt_number != number:
                raise ValueError("webhook claim release does not own the active claim")
            if (
                reason is WebhookClaimReleaseReason.EXPIRED
                and recorded_at < claim.lease_expires_at
            ):
                raise ValueError("webhook claim cannot expire before its lease boundary")
            restored = (
                WebhookDeliveryStatus.QUEUED
                if state.attempts_started == 0
                else WebhookDeliveryStatus.RETRY_SCHEDULED
            )
            return WebhookDeliveryState(
                intent=state.intent,
                status=restored,
                attempts_started=state.attempts_started,
                attempts_completed=state.attempts_completed,
                updated_at=recorded_at,
                next_attempt_at=state.next_attempt_at,
                last_outcome=state.last_outcome,
            )
        case WebhookAttemptStarted(
            attempt_number=number,
            claim_id=claim_id,
            recorded_at=recorded_at,
        ):
            claim = state.active_claim
            if state.status is not WebhookDeliveryStatus.CLAIMED or claim is None:
                raise ValueError("webhook attempt requires an active claim")
            if claim.claim_id != claim_id or claim.attempt_number != number:
                raise ValueError("webhook attempt does not own the active claim")
            if recorded_at >= claim.lease_expires_at:
                raise ValueError("webhook attempt cannot start after claim expiry")
            if recorded_at >= state.intent.deadline_at:
                raise ValueError("webhook attempt cannot start after delivery deadline")
            if number != state.attempts_started + 1:
                raise ValueError("webhook attempt number is not the next attempt")
            return WebhookDeliveryState(
                intent=state.intent,
                status=WebhookDeliveryStatus.IN_FLIGHT,
                attempts_started=number,
                attempts_completed=state.attempts_completed,
                updated_at=recorded_at,
                active_claim=claim,
                last_outcome=state.last_outcome,
            )
        case WebhookAttemptFinished(
            attempt_number=number,
            claim_id=claim_id,
            outcome=outcome,
            recorded_at=recorded_at,
        ):
            claim = state.active_claim
            if state.status is not WebhookDeliveryStatus.IN_FLIGHT or claim is None:
                raise ValueError("webhook attempt can finish only while in flight")
            if claim.claim_id != claim_id or claim.attempt_number != number:
                raise ValueError("webhook attempt result does not own the active claim")
            if (
                number != state.attempts_started
                or state.attempts_completed != number - 1
            ):
                raise ValueError("webhook attempt completion is out of order")
            status = {
                WebhookAttemptOutcome.SUCCEEDED: WebhookDeliveryStatus.DELIVERED,
                WebhookAttemptOutcome.RETRYABLE_FAILURE: WebhookDeliveryStatus.FAILED,
                WebhookAttemptOutcome.TERMINAL_FAILURE: WebhookDeliveryStatus.FAILED,
                WebhookAttemptOutcome.UNCERTAIN: WebhookDeliveryStatus.UNCERTAIN,
            }[outcome]
            return WebhookDeliveryState(
                intent=state.intent,
                status=status,
                attempts_started=number,
                attempts_completed=number,
                updated_at=recorded_at,
                last_outcome=outcome,
            )
        case WebhookRetryScheduled(
            next_attempt_number=number,
            available_at=available_at,
            recorded_at=recorded_at,
        ):
            if (
                state.status is not WebhookDeliveryStatus.FAILED
                or state.last_outcome is not WebhookAttemptOutcome.RETRYABLE_FAILURE
            ):
                raise ValueError("only retryable webhook failure can schedule retry")
            if number != state.attempts_started + 1 or number > state.intent.retry_policy.max_attempts:
                raise ValueError("webhook retry number is not the next bounded attempt")
            expected = recorded_at + timedelta(
                milliseconds=state.intent.retry_policy.backoff_ms(state.attempts_completed)
            )
            if available_at != expected or available_at > state.intent.deadline_at:
                raise ValueError("webhook retry availability violates policy")
            return WebhookDeliveryState(
                intent=state.intent,
                status=WebhookDeliveryStatus.RETRY_SCHEDULED,
                attempts_started=state.attempts_started,
                attempts_completed=state.attempts_completed,
                updated_at=recorded_at,
                next_attempt_at=available_at,
                last_outcome=state.last_outcome,
            )
        case WebhookDeadLettered(recorded_at=recorded_at):
            if state.status is not WebhookDeliveryStatus.FAILED:
                raise ValueError("only failed webhook delivery can enter dead letter")
            if (
                state.last_outcome is not WebhookAttemptOutcome.TERMINAL_FAILURE
                and state.attempts_started < state.intent.retry_policy.max_attempts
                and recorded_at < state.intent.deadline_at
            ):
                raise ValueError(
                    "retryable webhook failure cannot enter dead letter before exhaustion"
                )
            return WebhookDeliveryState(
                intent=state.intent,
                status=WebhookDeliveryStatus.DEAD_LETTER,
                attempts_started=state.attempts_started,
                attempts_completed=state.attempts_completed,
                updated_at=recorded_at,
                last_outcome=state.last_outcome,
            )
        case WebhookOperatorRequired(recorded_at=recorded_at):
            if state.status is not WebhookDeliveryStatus.UNCERTAIN:
                raise ValueError("only uncertain webhook delivery requires operator action")
            return WebhookDeliveryState(
                intent=state.intent,
                status=WebhookDeliveryStatus.OPERATOR_REQUIRED,
                attempts_started=state.attempts_started,
                attempts_completed=state.attempts_completed,
                updated_at=recorded_at,
                last_outcome=state.last_outcome,
            )


def replay_webhook_events(events: tuple[WebhookEvent, ...]) -> WebhookDeliveryState:
    if not events:
        raise ValueError("webhook event history must be nonempty")
    state: WebhookDeliveryState | None = None
    for event in events:
        state = evolve_webhook_delivery(state, event)
    if state is None:  # pragma: no cover - nonempty events make this unreachable.
        raise AssertionError("webhook replay did not construct state")
    return state


def webhook_event_descriptor(event: WebhookEvent) -> dict[str, object]:
    match event:
        case WebhookEnqueued(intent=intent):
            return {"variant": "enqueued", "intent": intent.descriptor()}
        case WebhookClaimed(claim=claim):
            return {"variant": "claimed", "claim": claim.descriptor()}
        case WebhookClaimReleased(
            identity=identity,
            claim_id=claim_id,
            attempt_number=number,
            reason=reason,
            recorded_at=at,
        ):
            return {
                "variant": "claim-released",
                "identity": identity.descriptor(),
                "claim_id": claim_id,
                "attempt_number": number,
                "reason": reason.value,
                "recorded_at": _timestamp(at),
            }
        case WebhookAttemptStarted(
            identity=identity,
            attempt_number=number,
            claim_id=claim_id,
            recorded_at=at,
        ):
            return {
                "variant": "attempt-started",
                "identity": identity.descriptor(),
                "attempt_number": number,
                "claim_id": claim_id,
                "recorded_at": _timestamp(at),
            }
        case WebhookAttemptFinished(
            identity=identity,
            attempt_number=number,
            claim_id=claim_id,
            outcome=outcome,
            recorded_at=at,
            response_status=response_status,
            failure_code=failure_code,
        ):
            return {
                "variant": "attempt-finished",
                "identity": identity.descriptor(),
                "attempt_number": number,
                "claim_id": claim_id,
                "outcome": outcome.value,
                "recorded_at": _timestamp(at),
                "response_status": response_status,
                "failure_code": failure_code,
            }
        case WebhookRetryScheduled(
            identity=identity,
            next_attempt_number=number,
            available_at=available_at,
            recorded_at=at,
        ):
            return {
                "variant": "retry-scheduled",
                "identity": identity.descriptor(),
                "next_attempt_number": number,
                "available_at": _timestamp(available_at),
                "recorded_at": _timestamp(at),
            }
        case WebhookDeadLettered(identity=identity, reason_code=reason, recorded_at=at):
            return {
                "variant": "dead-lettered",
                "identity": identity.descriptor(),
                "reason_code": reason,
                "recorded_at": _timestamp(at),
            }
        case WebhookOperatorRequired(identity=identity, reason_code=reason, recorded_at=at):
            return {
                "variant": "operator-required",
                "identity": identity.descriptor(),
                "reason_code": reason,
                "recorded_at": _timestamp(at),
            }


def webhook_event_from_descriptor(value: object) -> WebhookEvent:
    if not isinstance(value, Mapping):
        raise TypeError("webhook event descriptor must be an object")
    variant = value.get("variant")
    match variant:
        case "enqueued":
            _exact(value, "variant", "intent")
            return WebhookEnqueued(_intent(_mapping(value, "intent")))
        case "claimed":
            _exact(value, "variant", "claim")
            return WebhookClaimed(_claim(_mapping(value, "claim")))
        case "claim-released":
            _exact(
                value,
                "variant",
                "identity",
                "claim_id",
                "attempt_number",
                "reason",
                "recorded_at",
            )
            try:
                reason = WebhookClaimReleaseReason(_text(value, "reason"))
            except ValueError as error:
                raise ValueError("unknown webhook claim release reason") from error
            return WebhookClaimReleased(
                _identity(_mapping(value, "identity")),
                _text(value, "claim_id"),
                _integer(value, "attempt_number"),
                reason,
                _datetime(value, "recorded_at"),
            )
        case "attempt-started":
            _exact(
                value,
                "variant",
                "identity",
                "attempt_number",
                "claim_id",
                "recorded_at",
            )
            return WebhookAttemptStarted(
                _identity(_mapping(value, "identity")),
                _integer(value, "attempt_number"),
                _text(value, "claim_id"),
                _datetime(value, "recorded_at"),
            )
        case "attempt-finished":
            _exact(
                value,
                "variant",
                "identity",
                "attempt_number",
                "claim_id",
                "outcome",
                "recorded_at",
                "response_status",
                "failure_code",
            )
            try:
                outcome = WebhookAttemptOutcome(_text(value, "outcome"))
            except ValueError as error:
                raise ValueError("unknown webhook attempt outcome") from error
            return WebhookAttemptFinished(
                _identity(_mapping(value, "identity")),
                _integer(value, "attempt_number"),
                _text(value, "claim_id"),
                outcome,
                _datetime(value, "recorded_at"),
                _optional_integer(value, "response_status"),
                _optional_text(value, "failure_code"),
            )
        case "retry-scheduled":
            _exact(
                value,
                "variant",
                "identity",
                "next_attempt_number",
                "available_at",
                "recorded_at",
            )
            return WebhookRetryScheduled(
                _identity(_mapping(value, "identity")),
                _integer(value, "next_attempt_number"),
                _datetime(value, "available_at"),
                _datetime(value, "recorded_at"),
            )
        case "dead-lettered":
            _exact(value, "variant", "identity", "reason_code", "recorded_at")
            return WebhookDeadLettered(
                _identity(_mapping(value, "identity")),
                _text(value, "reason_code"),
                _datetime(value, "recorded_at"),
            )
        case "operator-required":
            _exact(value, "variant", "identity", "reason_code", "recorded_at")
            return WebhookOperatorRequired(
                _identity(_mapping(value, "identity")),
                _text(value, "reason_code"),
                _datetime(value, "recorded_at"),
            )
        case _:
            raise ValueError("unknown webhook event variant")


def webhook_intent_from_descriptor(value: object) -> WebhookDeliveryIntent:
    if not isinstance(value, Mapping):
        raise TypeError("webhook intent descriptor must be an object")
    return _intent(value)


def webhook_authority_from_descriptor(value: object) -> WebhookAuthority:
    if not isinstance(value, Mapping):
        raise TypeError("webhook authority descriptor must be an object")
    _exact(value, "actor_id", "workspace_id", "scopes")
    scopes = value.get("scopes")
    if not isinstance(scopes, list) or any(not isinstance(item, str) for item in scopes):
        raise TypeError("webhook authority scopes must be a string list")
    try:
        typed_scopes = frozenset(WebhookScope(item) for item in scopes)
    except ValueError as error:
        raise ValueError("unknown webhook authority scope") from error
    if len(typed_scopes) != len(scopes):
        raise ValueError("webhook authority scopes must be unique")
    return WebhookAuthority(
        _text(value, "actor_id"),
        _text(value, "workspace_id"),
        typed_scopes,
    )


def _intent(value: Mapping[str, object]) -> WebhookDeliveryIntent:
    _exact(
        value,
        "command_id",
        "identity",
        "endpoint",
        "payload",
        "retry_policy",
        "enqueued_at",
        "signing",
    )
    signing = value.get("signing")
    return WebhookDeliveryIntent(
        _text(value, "command_id"),
        _identity(_mapping(value, "identity")),
        _endpoint(_mapping(value, "endpoint")),
        _payload(_mapping(value, "payload")),
        _retry_policy(_mapping(value, "retry_policy")),
        _datetime(value, "enqueued_at"),
        None if signing is None else _signing(_mapping(value, "signing")),
    )


def _identity(value: Mapping[str, object]) -> WebhookDeliveryIdentity:
    _exact(value, "workspace_id", "delivery_id")
    return WebhookDeliveryIdentity(
        _text(value, "workspace_id"),
        _text(value, "delivery_id"),
    )


def _claim(value: Mapping[str, object]) -> WebhookClaim:
    _exact(
        value,
        "identity",
        "claim_id",
        "worker_id",
        "attempt_number",
        "claimed_at",
        "lease_expires_at",
    )
    return WebhookClaim(
        _identity(_mapping(value, "identity")),
        _text(value, "claim_id"),
        _text(value, "worker_id"),
        _integer(value, "attempt_number"),
        _datetime(value, "claimed_at"),
        _datetime(value, "lease_expires_at"),
    )


def _endpoint(value: Mapping[str, object]) -> WebhookEndpoint:
    _exact(value, "endpoint_id", "url", "scheme")
    endpoint = WebhookEndpoint(_text(value, "endpoint_id"), _text(value, "url"))
    if endpoint.scheme.value != _text(value, "scheme"):
        raise ValueError("webhook endpoint scheme does not match URL")
    return endpoint


def _payload(value: Mapping[str, object]) -> WebhookPayload:
    _exact(value, "content_type", "body_base64", "content_digest")
    try:
        content_type = WebhookContentType(_text(value, "content_type"))
        body = base64.b64decode(_text(value, "body_base64"), validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("webhook payload descriptor is malformed") from error
    payload = WebhookPayload(content_type, body)
    if payload.content_digest != _text(value, "content_digest"):
        raise ValueError("webhook payload digest does not match content")
    return payload


def _retry_policy(value: Mapping[str, object]) -> WebhookRetryPolicy:
    _exact(
        value,
        "max_attempts",
        "initial_backoff_ms",
        "maximum_backoff_ms",
        "deadline_seconds",
    )
    return WebhookRetryPolicy(
        _integer(value, "max_attempts"),
        _integer(value, "initial_backoff_ms"),
        _integer(value, "maximum_backoff_ms"),
        _integer(value, "deadline_seconds"),
    )


def _signing(value: Mapping[str, object]) -> WebhookSigning:
    _exact(value, "reference_id", "algorithm", "header_name")
    try:
        algorithm = WebhookSigningAlgorithm(_text(value, "algorithm"))
    except ValueError as error:
        raise ValueError("unknown webhook signing algorithm") from error
    return WebhookSigning(
        SecretReference(_text(value, "reference_id")),
        algorithm,
        _text(value, "header_name"),
    )


def _same_identity(state: WebhookDeliveryState, event: WebhookEvent) -> None:
    if isinstance(event, WebhookEnqueued):
        identity = event.intent.identity
    elif isinstance(event, WebhookClaimed):
        identity = event.claim.identity
    else:
        identity = event.identity
    if identity != state.intent.identity:
        raise ValueError("webhook event belongs to another delivery")


def _event_identity(value: WebhookDeliveryIdentity) -> None:
    if not isinstance(value, WebhookDeliveryIdentity):
        raise TypeError("webhook event identity must be typed")


def _event_recorded_at(event: WebhookEvent) -> datetime:
    if isinstance(event, WebhookEnqueued):
        return event.intent.enqueued_at
    if isinstance(event, WebhookClaimed):
        return event.claim.claimed_at
    return event.recorded_at


def _validate_state_shape(state: WebhookDeliveryState) -> None:
    status = state.status
    started = state.attempts_started
    completed = state.attempts_completed
    outcome = state.last_outcome
    next_attempt = state.next_attempt_at
    claim = state.active_claim
    if status is WebhookDeliveryStatus.QUEUED:
        valid = (started, completed, outcome, next_attempt, claim) == (
            0,
            0,
            None,
            None,
            None,
        )
    elif status is WebhookDeliveryStatus.CLAIMED:
        initial_claim = (
            started == completed == 0
            and outcome is None
            and next_attempt is None
        )
        retry_claim = (
            started == completed
            and started >= 1
            and outcome is WebhookAttemptOutcome.RETRYABLE_FAILURE
            and next_attempt is not None
        )
        valid = (
            claim is not None
            and claim.identity == state.intent.identity
            and claim.attempt_number == started + 1
            and (initial_claim or retry_claim)
        )
    elif status is WebhookDeliveryStatus.IN_FLIGHT:
        valid = (
            started == completed + 1
            and claim is not None
            and claim.identity == state.intent.identity
            and claim.attempt_number == started
            and outcome in (None, WebhookAttemptOutcome.RETRYABLE_FAILURE)
            and next_attempt is None
        )
    elif status is WebhookDeliveryStatus.RETRY_SCHEDULED:
        valid = (
            started == completed
            and started >= 1
            and outcome is WebhookAttemptOutcome.RETRYABLE_FAILURE
            and next_attempt is not None
            and claim is None
        )
    elif status is WebhookDeliveryStatus.DELIVERED:
        valid = (
            started == completed
            and started >= 1
            and outcome is WebhookAttemptOutcome.SUCCEEDED
            and next_attempt is None
            and claim is None
        )
    elif status is WebhookDeliveryStatus.FAILED:
        valid = (
            started == completed
            and started >= 1
            and outcome in {
                WebhookAttemptOutcome.RETRYABLE_FAILURE,
                WebhookAttemptOutcome.TERMINAL_FAILURE,
            }
            and next_attempt is None
            and claim is None
        )
    elif status in {
        WebhookDeliveryStatus.UNCERTAIN,
        WebhookDeliveryStatus.OPERATOR_REQUIRED,
    }:
        valid = (
            started == completed
            and started >= 1
            and outcome is WebhookAttemptOutcome.UNCERTAIN
            and next_attempt is None
            and claim is None
        )
    else:
        valid = (
            status is WebhookDeliveryStatus.DEAD_LETTER
            and started == completed
            and started >= 1
            and outcome in {
                WebhookAttemptOutcome.RETRYABLE_FAILURE,
                WebhookAttemptOutcome.TERMINAL_FAILURE,
            }
            and next_attempt is None
            and claim is None
        )
    if not valid:
        raise ValueError("webhook delivery state shape is inconsistent")


def _exact(value: Mapping[str, object], *keys: str) -> None:
    if set(value) != set(keys):
        raise ValueError("webhook descriptor has unknown or missing fields")


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise TypeError("webhook descriptor field must be an object")
    return item


def _text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise TypeError("webhook descriptor field must be text")
    return item


def _optional_text(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is not None and not isinstance(item, str):
        raise TypeError("webhook descriptor optional field must be text")
    return item


def _integer(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if type(item) is not int:
        raise TypeError("webhook descriptor field must be an integer")
    return item


def _optional_integer(value: Mapping[str, object], key: str) -> int | None:
    item = value.get(key)
    if item is not None and type(item) is not int:
        raise TypeError("webhook descriptor optional field must be an integer")
    return item


def _datetime(value: Mapping[str, object], key: str) -> datetime:
    text = _text(value, key)
    try:
        return _aware(key, datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError as error:
        raise ValueError("webhook descriptor timestamp is invalid") from error


def _aware(label: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"webhook {label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _aware("timestamp", value).isoformat().replace("+00:00", "Z")


def _identifier(label: str, value: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"webhook {label} is invalid")


def _failure_code(value: str) -> None:
    if not isinstance(value, str) or not _FAILURE_CODE.fullmatch(value):
        raise ValueError("webhook failure code is invalid")


def _bounded(label: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
