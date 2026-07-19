"""Durable webhook commands interpreted around one bounded outbound effect."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
from typing import Protocol, TypeAlias

from control_plane_kit.webhook.language import (
    WebhookAttemptFinished,
    WebhookAttemptOutcome,
    WebhookAttemptStarted,
    WebhookAuthority,
    WebhookClaim,
    WebhookClaimed,
    WebhookClaimReleased,
    WebhookClaimReleaseReason,
    WebhookDeadLettered,
    WebhookDeliveryIdentity,
    WebhookDeliveryIntent,
    WebhookDeliveryState,
    WebhookDeliveryStatus,
    WebhookEndpoint,
    WebhookEnqueued,
    WebhookEvent,
    WebhookOperatorRequired,
    WebhookPayload,
    WebhookRetryScheduled,
    WebhookScope,
    WebhookSigning,
    evolve_webhook_delivery,
    replay_webhook_events,
)
from control_plane_kit.webhook.protocols import (
    WebhookProjectionRecord,
    WebhookUnitOfWork,
)


class WebhookCommandKind(StrEnum):
    ENQUEUE = "enqueue"
    CLAIM = "claim"
    RELEASE_CLAIM = "release-claim"
    START_ATTEMPT = "start-attempt"
    RECOVER = "require-operator"


class WebhookServiceError(RuntimeError):
    pass


class WebhookAuthorizationError(WebhookServiceError):
    pass


class WebhookCommandConflict(WebhookServiceError):
    pass


class WebhookStateConflict(WebhookServiceError):
    pass


@dataclass(frozen=True, slots=True)
class EnqueueWebhook:
    intent: WebhookDeliveryIntent
    authority: WebhookAuthority

    def __post_init__(self) -> None:
        if not isinstance(self.intent, WebhookDeliveryIntent):
            raise TypeError("webhook enqueue command intent must be typed")
        if not isinstance(self.authority, WebhookAuthority):
            raise TypeError("webhook enqueue command authority must be typed")


@dataclass(frozen=True, slots=True)
class ClaimWebhook:
    command_id: str
    identity: WebhookDeliveryIdentity
    worker_id: str
    lease_seconds: int
    authority: WebhookAuthority

    def __post_init__(self) -> None:
        _identifier("command_id", self.command_id)
        _identifier("worker_id", self.worker_id)
        if not isinstance(self.identity, WebhookDeliveryIdentity):
            raise TypeError("webhook claim command identity must be typed")
        if type(self.lease_seconds) is not int or not 1 <= self.lease_seconds <= 86_400:
            raise ValueError("webhook claim lease must be between 1 and 86400 seconds")


@dataclass(frozen=True, slots=True)
class ReleaseWebhookClaim:
    command_id: str
    identity: WebhookDeliveryIdentity
    claim_id: str
    worker_id: str
    authority: WebhookAuthority

    def __post_init__(self) -> None:
        _identifier("command_id", self.command_id)
        _identifier("claim_id", self.claim_id)
        _identifier("worker_id", self.worker_id)
        if not isinstance(self.identity, WebhookDeliveryIdentity):
            raise TypeError("webhook release command identity must be typed")


@dataclass(frozen=True, slots=True)
class DispatchWebhook:
    command_id: str
    identity: WebhookDeliveryIdentity
    claim_id: str
    worker_id: str
    authority: WebhookAuthority

    def __post_init__(self) -> None:
        _identifier("command_id", self.command_id)
        _identifier("claim_id", self.claim_id)
        _identifier("worker_id", self.worker_id)
        if not isinstance(self.identity, WebhookDeliveryIdentity):
            raise TypeError("webhook dispatch command identity must be typed")


@dataclass(frozen=True, slots=True)
class RecoverWebhook:
    command_id: str
    identity: WebhookDeliveryIdentity
    authority: WebhookAuthority

    def __post_init__(self) -> None:
        _identifier("command_id", self.command_id)
        if not isinstance(self.identity, WebhookDeliveryIdentity):
            raise TypeError("webhook recovery command identity must be typed")


WebhookCommand: TypeAlias = (
    EnqueueWebhook
    | ClaimWebhook
    | ReleaseWebhookClaim
    | DispatchWebhook
    | RecoverWebhook
)


@dataclass(frozen=True, slots=True)
class WebhookOutboundRequest:
    identity: WebhookDeliveryIdentity
    endpoint: WebhookEndpoint
    payload: WebhookPayload
    signing: WebhookSigning | None
    claim_id: str
    attempt_number: int


@dataclass(frozen=True, slots=True)
class WebhookOutboundResult:
    outcome: WebhookAttemptOutcome
    response_status: int | None = None
    failure_code: str | None = None

    def event_for(
        self,
        request: WebhookOutboundRequest,
        recorded_at: datetime,
    ) -> WebhookAttemptFinished:
        return WebhookAttemptFinished(
            request.identity,
            request.attempt_number,
            request.claim_id,
            self.outcome,
            recorded_at,
            self.response_status,
            self.failure_code,
        )


class WebhookOutboundDelivery(Protocol):
    def deliver(self, request: WebhookOutboundRequest) -> WebhookOutboundResult: ...


@dataclass(frozen=True, slots=True)
class WebhookCommandResult:
    state: WebhookDeliveryState
    replayed: bool
    external_effect_attempted: bool


UnitOfWorkFactory: TypeAlias = Callable[[], WebhookUnitOfWork]
Clock: TypeAlias = Callable[[], datetime]
IdFactory: TypeAlias = Callable[[], str]


class WebhookDeliveryService:
    """Interpret closed commands without hiding an effect inside a transaction."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        outbound: WebhookOutboundDelivery,
        *,
        clock: Clock = lambda: datetime.now(timezone.utc),
        id_factory: IdFactory,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._outbound = outbound
        self._clock = clock
        self._id_factory = id_factory

    def execute(self, command: WebhookCommand) -> WebhookCommandResult:
        match command:
            case EnqueueWebhook():
                return self._enqueue(command)
            case ClaimWebhook():
                return self._claim(command)
            case ReleaseWebhookClaim():
                return self._release(command)
            case DispatchWebhook():
                return self._dispatch(command)
            case RecoverWebhook():
                return self._recover(command)
            case _:
                raise TypeError("webhook command must be a closed command variant")

    def _enqueue(self, command: EnqueueWebhook) -> WebhookCommandResult:
        intent = command.intent
        _authorize(command.authority, WebhookScope.ENQUEUE, intent.identity)
        fingerprint = intent.intent_fingerprint
        with self._unit_of_work_factory() as work:
            work.commands.lock_command(intent.command_id)
            replay = work.commands.get(intent.command_id)
            if replay is not None:
                state = _command_replay(work, replay.intent_fingerprint, fingerprint, intent.identity)
                work.commit()
                return WebhookCommandResult(state, True, False)
            work.journal.lock_delivery(intent.identity)
            if work.intents.get(intent.identity) is not None:
                raise WebhookCommandConflict("webhook delivery identity already exists")
            event = WebhookEnqueued(intent)
            state = evolve_webhook_delivery(None, event)
            work.intents.add(intent)
            if not work.journal.append(intent.identity, 1, event):
                raise WebhookStateConflict("webhook enqueue lost journal ownership")
            work.projections.add(state, 1)
            work.commands.add(
                intent.command_id,
                intent.identity.workspace_id,
                WebhookCommandKind.ENQUEUE.value,
                fingerprint,
                command.authority.actor_id,
                _result_descriptor(state, 1),
                intent.enqueued_at,
            )
            work.commit()
            return WebhookCommandResult(state, False, False)

    def _claim(self, command: ClaimWebhook) -> WebhookCommandResult:
        _authorize(command.authority, WebhookScope.DISPATCH, command.identity)
        fingerprint = _fingerprint(
            WebhookCommandKind.CLAIM,
            command.identity,
            command.worker_id,
            command.lease_seconds,
        )
        with self._unit_of_work_factory() as work:
            work.commands.lock_command(command.command_id)
            replay = work.commands.get(command.command_id)
            if replay is not None:
                state = _command_replay(work, replay.intent_fingerprint, fingerprint, command.identity)
                work.commit()
                return WebhookCommandResult(state, True, False)
            work.journal.lock_delivery(command.identity)
            projection = _projection(work, command.identity)
            claimed_at = _now(self._clock)
            claim = WebhookClaim(
                command.identity,
                self._id_factory(),
                command.worker_id,
                projection.state.attempts_started + 1,
                claimed_at,
                claimed_at + timedelta(seconds=command.lease_seconds),
            )
            state, version = _append(work, projection, (WebhookClaimed(claim),))
            work.commands.add(
                command.command_id,
                command.identity.workspace_id,
                WebhookCommandKind.CLAIM.value,
                fingerprint,
                command.authority.actor_id,
                _result_descriptor(state, version),
                claimed_at,
            )
            work.commit()
            return WebhookCommandResult(state, False, False)

    def _release(self, command: ReleaseWebhookClaim) -> WebhookCommandResult:
        _authorize(command.authority, WebhookScope.DISPATCH, command.identity)
        fingerprint = _fingerprint(
            WebhookCommandKind.RELEASE_CLAIM,
            command.identity,
            command.claim_id,
            command.worker_id,
        )
        with self._unit_of_work_factory() as work:
            work.commands.lock_command(command.command_id)
            replay = work.commands.get(command.command_id)
            if replay is not None:
                state = _command_replay(work, replay.intent_fingerprint, fingerprint, command.identity)
                work.commit()
                return WebhookCommandResult(state, True, False)
            work.journal.lock_delivery(command.identity)
            projection = _projection(work, command.identity)
            claim = _owned_claim(projection.state, command.claim_id, command.worker_id)
            recorded_at = _now(self._clock)
            if recorded_at >= claim.lease_expires_at:
                raise WebhookStateConflict(
                    "expired webhook claim requires explicit recovery"
                )
            event = WebhookClaimReleased(
                command.identity,
                claim.claim_id,
                claim.attempt_number,
                WebhookClaimReleaseReason.ABANDONED,
                recorded_at,
            )
            state, version = _append(work, projection, (event,))
            work.commands.add(
                command.command_id,
                command.identity.workspace_id,
                WebhookCommandKind.RELEASE_CLAIM.value,
                fingerprint,
                command.authority.actor_id,
                _result_descriptor(state, version),
                recorded_at,
            )
            work.commit()
            return WebhookCommandResult(state, False, False)

    def _dispatch(self, command: DispatchWebhook) -> WebhookCommandResult:
        _authorize(command.authority, WebhookScope.DISPATCH, command.identity)
        fingerprint = _fingerprint(
            WebhookCommandKind.START_ATTEMPT,
            command.identity,
            command.claim_id,
            command.worker_id,
        )
        with self._unit_of_work_factory() as work:
            work.commands.lock_command(command.command_id)
            replay = work.commands.get(command.command_id)
            if replay is not None:
                state = _command_replay(work, replay.intent_fingerprint, fingerprint, command.identity)
                work.commit()
                return WebhookCommandResult(state, True, False)
            work.journal.lock_delivery(command.identity)
            projection = _projection(work, command.identity)
            claim = _owned_claim(projection.state, command.claim_id, command.worker_id)
            started_at = _now(self._clock)
            event = WebhookAttemptStarted(
                command.identity,
                claim.attempt_number,
                claim.claim_id,
                started_at,
            )
            state, version = _append(work, projection, (event,))
            work.commands.add(
                command.command_id,
                command.identity.workspace_id,
                WebhookCommandKind.START_ATTEMPT.value,
                fingerprint,
                command.authority.actor_id,
                _result_descriptor(state, version),
                started_at,
            )
            work.commit()

        request = WebhookOutboundRequest(
            command.identity,
            state.intent.endpoint,
            state.intent.payload,
            state.intent.signing,
            claim.claim_id,
            claim.attempt_number,
        )
        try:
            outbound_result = self._outbound.deliver(request)
        except Exception:
            outbound_result = WebhookOutboundResult(WebhookAttemptOutcome.UNCERTAIN)

        finished_at = _now(self._clock)
        with self._unit_of_work_factory() as work:
            work.journal.lock_delivery(command.identity)
            projection = _projection(work, command.identity)
            if (
                projection.state.status is not WebhookDeliveryStatus.IN_FLIGHT
                or projection.state.active_claim is None
                or projection.state.active_claim.claim_id != claim.claim_id
            ):
                raise WebhookStateConflict("webhook attempt result lost claim ownership")
            events = _completion_events(projection.state, outbound_result, request, finished_at)
            completed, _ = _append(work, projection, events)
            work.commit()
            return WebhookCommandResult(completed, False, True)

    def _recover(self, command: RecoverWebhook) -> WebhookCommandResult:
        _authorize(command.authority, WebhookScope.RECOVER, command.identity)
        fingerprint = _fingerprint(WebhookCommandKind.RECOVER, command.identity)
        with self._unit_of_work_factory() as work:
            work.commands.lock_command(command.command_id)
            replay = work.commands.get(command.command_id)
            if replay is not None:
                state = _command_replay(work, replay.intent_fingerprint, fingerprint, command.identity)
                work.commit()
                return WebhookCommandResult(state, True, False)
            work.journal.lock_delivery(command.identity)
            projection = _projection(work, command.identity)
            state = projection.state
            claim = state.active_claim
            recorded_at = _now(self._clock)
            if claim is None or recorded_at < claim.lease_expires_at:
                raise WebhookStateConflict("webhook delivery has no expired active claim")
            if state.status is WebhookDeliveryStatus.CLAIMED:
                events: tuple[WebhookEvent, ...] = (
                    WebhookClaimReleased(
                        command.identity,
                        claim.claim_id,
                        claim.attempt_number,
                        WebhookClaimReleaseReason.EXPIRED,
                        recorded_at,
                    ),
                )
                kind = WebhookCommandKind.RELEASE_CLAIM
            elif state.status is WebhookDeliveryStatus.IN_FLIGHT:
                events = (
                    WebhookAttemptFinished(
                        command.identity,
                        claim.attempt_number,
                        claim.claim_id,
                        WebhookAttemptOutcome.UNCERTAIN,
                        recorded_at,
                    ),
                    WebhookOperatorRequired(
                        command.identity,
                        "webhook.effect-outcome-unknown",
                        recorded_at,
                    ),
                )
                kind = WebhookCommandKind.RECOVER
            else:
                raise WebhookStateConflict("webhook delivery is not recoverable by claim expiry")
            recovered, version = _append(work, projection, events)
            work.commands.add(
                command.command_id,
                command.identity.workspace_id,
                kind.value,
                fingerprint,
                command.authority.actor_id,
                _result_descriptor(recovered, version),
                recorded_at,
            )
            work.commit()
            return WebhookCommandResult(recovered, False, False)


def _completion_events(
    state: WebhookDeliveryState,
    result: WebhookOutboundResult,
    request: WebhookOutboundRequest,
    recorded_at: datetime,
) -> tuple[WebhookEvent, ...]:
    finished = result.event_for(request, recorded_at)
    completed = evolve_webhook_delivery(state, finished)
    if result.outcome is WebhookAttemptOutcome.SUCCEEDED:
        return (finished,)
    if result.outcome is WebhookAttemptOutcome.UNCERTAIN:
        return (
            finished,
            WebhookOperatorRequired(
                request.identity,
                "webhook.effect-outcome-unknown",
                recorded_at,
            ),
        )
    if result.outcome is WebhookAttemptOutcome.TERMINAL_FAILURE:
        return (
            finished,
            WebhookDeadLettered(request.identity, "webhook.terminal-failure", recorded_at),
        )
    policy = completed.intent.retry_policy
    available_at = recorded_at + timedelta(
        milliseconds=policy.backoff_ms(completed.attempts_completed)
    )
    if completed.attempts_completed >= policy.max_attempts or available_at > completed.intent.deadline_at:
        return (
            finished,
            WebhookDeadLettered(request.identity, "webhook.retry-exhausted", recorded_at),
        )
    return (
        finished,
        WebhookRetryScheduled(
            request.identity,
            completed.attempts_started + 1,
            available_at,
            recorded_at,
        ),
    )


def _append(
    work: WebhookUnitOfWork,
    projection: WebhookProjectionRecord,
    events: tuple[WebhookEvent, ...],
) -> tuple[WebhookDeliveryState, int]:
    state = projection.state
    version = projection.journal_version
    for event in events:
        state = evolve_webhook_delivery(state, event)
        version += 1
        if not work.journal.append(state.intent.identity, version, event):
            raise WebhookStateConflict("webhook journal version changed concurrently")
    if not work.projections.replace(state, projection.journal_version, version):
        raise WebhookStateConflict("webhook projection version changed concurrently")
    return state, version


def _projection(
    work: WebhookUnitOfWork,
    identity: WebhookDeliveryIdentity,
) -> WebhookProjectionRecord:
    projection = work.projections.get(identity)
    if projection is None:
        raise WebhookStateConflict("webhook delivery does not exist")
    events = work.journal.events_for(identity)
    if projection.journal_version != len(events):
        raise WebhookStateConflict("webhook projection journal version is inconsistent")
    if projection.state != replay_webhook_events(events):
        raise WebhookStateConflict("webhook projection does not match canonical journal")
    return projection


def _command_replay(
    work: WebhookUnitOfWork,
    stored_fingerprint: str,
    requested_fingerprint: str,
    identity: WebhookDeliveryIdentity,
) -> WebhookDeliveryState:
    if stored_fingerprint != requested_fingerprint:
        raise WebhookCommandConflict("webhook command id was used for different intent")
    return _projection(work, identity).state


def _owned_claim(
    state: WebhookDeliveryState,
    claim_id: str,
    worker_id: str,
) -> WebhookClaim:
    claim = state.active_claim
    if (
        state.status is not WebhookDeliveryStatus.CLAIMED
        or claim is None
        or claim.claim_id != claim_id
        or claim.worker_id != worker_id
    ):
        raise WebhookStateConflict("webhook command does not own the active claim")
    return claim


def _authorize(
    authority: WebhookAuthority,
    scope: WebhookScope,
    identity: WebhookDeliveryIdentity,
) -> None:
    if not isinstance(authority, WebhookAuthority):
        raise TypeError("webhook command authority must be typed")
    if authority.workspace_id != identity.workspace_id or scope not in authority.scopes:
        raise WebhookAuthorizationError("webhook command authority is insufficient")


def _fingerprint(
    kind: WebhookCommandKind,
    identity: WebhookDeliveryIdentity,
    *parts: object,
) -> str:
    value = {
        "kind": kind.value,
        "identity": identity.descriptor(),
        "parts": list(parts),
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _result_descriptor(state: WebhookDeliveryState, journal_version: int) -> dict[str, object]:
    return {
        "identity": state.intent.identity.descriptor(),
        "journal_version": journal_version,
        "status": state.status.value,
    }


def _now(clock: Clock) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise WebhookServiceError("webhook clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _identifier(label: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode()) > 128
        or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-" for character in value)
    ):
        raise ValueError(f"webhook {label} is invalid")
