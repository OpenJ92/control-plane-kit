"""Durable local provider-policy intent, single dispatch, and readback owner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from typing import Callable

from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity
from control_plane_kit_core.provider_policy import (
    ProviderPolicyCommandResult,
    ProviderPolicyCommandResultRequest,
    ProviderPolicyCoordinate,
    ProviderPolicyErrorCode,
    ProviderPolicyFailure,
    ProviderPolicyGrantCommand,
    ProviderPolicyMutationCommand,
    ProviderPolicyOperation,
    ProviderPolicyPort,
    ProviderPolicyRevokeCommand,
    provider_policy_command_fingerprint,
)
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp
from control_plane_kit_operations.workflows import IdempotencyKey


class ProviderPolicyCommandError(ValueError):
    """Bounded provider-policy command failure."""


class ProviderPolicyAdministrationDenied(ProviderPolicyCommandError):
    """Current installer admission or exact historical binding is unavailable."""


class ProviderPolicyCommandConflict(ProviderPolicyCommandError):
    """An idempotency key, immutable intent, claim, or evidence conflicts."""


class ProviderPolicyCommandNotFound(ProviderPolicyCommandError):
    """No exact local provider-policy command exists."""


class ProviderPolicyCommandUnavailable(ProviderPolicyCommandError):
    """Local durable completion is unavailable; no outcome is fabricated."""


def _text(value: object, name: str, maximum: int = 512) -> str:
    try:
        valid = (
            type(value) is str
            and bool(value.strip())
            and len(value.encode("utf-8")) <= maximum
            and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        )
    except UnicodeError:
        valid = False
    if not valid:
        raise ProviderPolicyCommandError(f"{name} is malformed")
    return value


@dataclass(frozen=True, order=True)
class ProviderPolicyBinding:
    """Credential-free identity of one fixed installer-owned provider binding."""

    binding_id: str
    logical_provider_id: str
    provider_id: str

    def __post_init__(self) -> None:
        _text(self.binding_id, "provider-policy binding_id", 200)
        _text(self.logical_provider_id, "provider-policy logical_provider_id", 128)
        _text(self.provider_id, "provider-policy provider_id")


@dataclass(frozen=True, order=True)
class ProviderPolicyAdmission:
    identity: PrincipalIdentity
    binding: ProviderPolicyBinding

    def __post_init__(self) -> None:
        if type(self.identity) is not PrincipalIdentity:
            raise ProviderPolicyCommandError("provider-policy admission identity is malformed")
        if type(self.binding) is not ProviderPolicyBinding:
            raise ProviderPolicyCommandError("provider-policy admission binding is malformed")


@dataclass(frozen=True, order=True)
class ProviderPolicyInvocationContext:
    installation_id: str
    identity: PrincipalIdentity
    binding: ProviderPolicyBinding

    def __post_init__(self) -> None:
        _text(self.installation_id, "provider-policy installation_id")
        if type(self.identity) is not PrincipalIdentity or type(self.binding) is not ProviderPolicyBinding:
            raise ProviderPolicyCommandError("provider-policy invocation context is malformed")


@dataclass(frozen=True)
class ProviderPolicyAdministrationPolicy:
    """Immutable application-instance admission; contains no endpoint or credential."""

    installation_id: str
    admissions: tuple[ProviderPolicyAdmission, ...]

    def __post_init__(self) -> None:
        _text(self.installation_id, "provider-policy installation_id")
        if (
            type(self.admissions) is not tuple
            or not self.admissions
            or len(self.admissions) > 1024
            or any(type(value) is not ProviderPolicyAdmission for value in self.admissions)
        ):
            raise ProviderPolicyCommandError("provider-policy admissions are malformed")
        keys = tuple(
            (value.identity, value.binding.logical_provider_id) for value in self.admissions
        )
        if len(set(keys)) != len(keys):
            raise ProviderPolicyCommandError("provider-policy admissions contain duplicates")

    def authorize(
        self, principal: AuthenticatedPrincipal, logical_provider_id: str
    ) -> ProviderPolicyInvocationContext:
        _text(logical_provider_id, "provider-policy logical_provider_id", 128)
        if type(principal) is not AuthenticatedPrincipal:
            raise ProviderPolicyAdministrationDenied("provider-policy administration is denied")
        for admission in self.admissions:
            if (
                admission.identity == principal.identity
                and admission.binding.logical_provider_id == logical_provider_id
            ):
                return ProviderPolicyInvocationContext(
                    self.installation_id, principal.identity, admission.binding
                )
        raise ProviderPolicyAdministrationDenied("provider-policy administration is denied")

    def require(self, context: ProviderPolicyInvocationContext) -> None:
        if type(context) is not ProviderPolicyInvocationContext:
            raise ProviderPolicyAdministrationDenied("provider-policy administration is denied")
        if context.installation_id != self.installation_id:
            raise ProviderPolicyAdministrationDenied("provider-policy administration is denied")
        if not any(
            admission.identity == context.identity and admission.binding == context.binding
            for admission in self.admissions
        ):
            raise ProviderPolicyAdministrationDenied("provider-policy administration is denied")


@dataclass(frozen=True)
class GrantProviderPolicy:
    context: ProviderPolicyInvocationContext
    coordinate: ProviderPolicyCoordinate
    expected_revision: int
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _command(self.context, self.coordinate, self.expected_revision, self.idempotency_key)


@dataclass(frozen=True)
class RevokeProviderPolicy:
    context: ProviderPolicyInvocationContext
    coordinate: ProviderPolicyCoordinate
    expected_revision: int
    idempotency_key: IdempotencyKey
    grant_id: str

    def __post_init__(self) -> None:
        _command(self.context, self.coordinate, self.expected_revision, self.idempotency_key)
        _text(self.grant_id, "provider-policy grant_id")


@dataclass(frozen=True)
class ReconcileProviderPolicy:
    context: ProviderPolicyInvocationContext
    command_id: str

    def __post_init__(self) -> None:
        if type(self.context) is not ProviderPolicyInvocationContext:
            raise ProviderPolicyCommandError("provider-policy context is malformed")
        _digest(self.command_id, "provider-policy command_id")


class ProviderPolicyCommandState(StrEnum):
    PREPARED = "prepared"
    DISPATCH_CLAIMED = "dispatch-claimed"
    DISPATCHED_UNRESOLVED = "dispatched-unresolved"
    REFUSED = "refused"
    RESOLVED = "resolved"
    EVIDENCE_MISMATCH_UNRESOLVED = "evidence-mismatch-unresolved"


class ProviderPolicyObservationSource(StrEnum):
    INITIAL_RESPONSE = "initial-response"
    COMMAND_RESULT_READ = "command-result-read"


@dataclass(frozen=True)
class ProviderPolicyOutcomeObservation:
    observation_id: str
    source: ProviderPolicyObservationSource
    observed_at: str
    evidence_fingerprint: str
    canonical_result: ProviderPolicyCommandResult | None = None
    failure: ProviderPolicyFailure | None = None
    matches_intent: bool = False

    def __post_init__(self) -> None:
        _digest(self.observation_id, "provider-policy observation_id")
        _digest(self.evidence_fingerprint, "provider-policy evidence_fingerprint")
        validate_canonical_utc_timestamp(self.observed_at)
        if not isinstance(self.source, ProviderPolicyObservationSource):
            raise ProviderPolicyCommandError("provider-policy observation source is malformed")
        if (self.canonical_result is None) == (self.failure is None):
            raise ProviderPolicyCommandError("provider-policy observation evidence is malformed")
        if type(self.matches_intent) is not bool:
            raise ProviderPolicyCommandError("provider-policy observation match is malformed")
        if self.failure is not None and self.matches_intent:
            raise ProviderPolicyCommandError("provider-policy failure cannot match intent")
        evidence = self.canonical_result if self.canonical_result is not None else self.failure
        if self.evidence_fingerprint != _evidence_fingerprint(evidence, self.source):
            raise ProviderPolicyCommandError("provider-policy evidence fingerprint is malformed")


@dataclass(frozen=True)
class ProviderPolicyCommandRecord:
    command_id: str
    installation_id: str
    actor: PrincipalIdentity
    binding: ProviderPolicyBinding
    idempotency_key: IdempotencyKey
    request_fingerprint: str
    provider_correlation: str
    command: ProviderPolicyMutationCommand
    created_at: str
    dispatch_claimed_at: str | None = None
    observations: tuple[ProviderPolicyOutcomeObservation, ...] = ()
    observation_count: int = 0
    evidence_integrity_conflict: bool = False
    has_matching_result: bool = False
    has_definite_refusal: bool = False

    def __post_init__(self) -> None:
        _digest(self.command_id, "provider-policy command_id")
        _text(self.installation_id, "provider-policy installation_id")
        if type(self.actor) is not PrincipalIdentity or type(self.binding) is not ProviderPolicyBinding:
            raise ProviderPolicyCommandError("provider-policy command ownership is malformed")
        if type(self.idempotency_key) is not IdempotencyKey:
            raise ProviderPolicyCommandError("provider-policy idempotency key is malformed")
        _digest(self.request_fingerprint, "provider-policy request_fingerprint")
        _text(self.provider_correlation, "provider-policy correlation", 128)
        if not isinstance(self.command, (ProviderPolicyGrantCommand, ProviderPolicyRevokeCommand)):
            raise ProviderPolicyCommandError("provider-policy command is malformed")
        if self.command.logical_provider_id != self.binding.logical_provider_id or self.command.provider_id != self.binding.provider_id:
            raise ProviderPolicyCommandError("provider-policy command binding is malformed")
        expected_command_id, expected_correlation = _command_identity(
            self.installation_id,
            self.actor,
            self.binding.logical_provider_id,
            self.idempotency_key,
        )
        if (
            self.command_id != expected_command_id
            or self.provider_correlation != expected_correlation
            or self.command.caller_idempotency_key != self.idempotency_key.value
            or self.command.provider_correlation != self.provider_correlation
        ):
            raise ProviderPolicyCommandError("provider-policy command identity is malformed")
        if provider_policy_command_fingerprint(self.command) != self.request_fingerprint:
            raise ProviderPolicyCommandError("provider-policy command fingerprint is malformed")
        validate_canonical_utc_timestamp(self.created_at)
        if self.dispatch_claimed_at is not None:
            validate_canonical_utc_timestamp(self.dispatch_claimed_at)
        if type(self.observations) is not tuple or any(
            type(value) is not ProviderPolicyOutcomeObservation for value in self.observations
        ):
            raise ProviderPolicyCommandError("provider-policy observations are malformed")
        if type(self.observation_count) is not int or not 0 <= len(self.observations) <= self.observation_count:
            raise ProviderPolicyCommandError("provider-policy observation count is malformed")
        if any(type(value) is not bool for value in (
            self.evidence_integrity_conflict, self.has_matching_result,
            self.has_definite_refusal,
        )):
            raise ProviderPolicyCommandError("provider-policy evidence summary is malformed")
        canonical = set()
        included_match = False
        included_refusal = False
        for observation in self.observations:
            expected_id = sha256((
                self.command_id + "\x1f" + observation.source.value + "\x1f"
                + observation.evidence_fingerprint
            ).encode()).hexdigest()
            if observation.observation_id != expected_id:
                raise ProviderPolicyCommandError("provider-policy observation identity is malformed")
            if observation.canonical_result is not None:
                matches = observation.canonical_result.matches(self.command)
                if observation.matches_intent != matches:
                    raise ProviderPolicyCommandError("provider-policy observation match is malformed")
                canonical.add(observation.evidence_fingerprint)
                included_match |= matches
            elif (
                observation.source is ProviderPolicyObservationSource.INITIAL_RESPONSE
                and observation.failure.code is not ProviderPolicyErrorCode.CONFLICT
                and not observation.failure.effect_may_have_occurred
            ):
                included_refusal = True
        included_conflict = (
            len(canonical) > 1
            or any(value.canonical_result is not None and not value.matches_intent
                   for value in self.observations)
            or (included_match and included_refusal)
        )
        if (
            included_conflict and not self.evidence_integrity_conflict
            or included_match and not self.has_matching_result
            or included_refusal and not self.has_definite_refusal
        ):
            raise ProviderPolicyCommandError("provider-policy evidence summary is inconsistent")
        if self.observations and self.dispatch_claimed_at is None:
            raise ProviderPolicyCommandError("provider-policy evidence precedes dispatch claim")

    @property
    def state(self) -> ProviderPolicyCommandState:
        if self.dispatch_claimed_at is None:
            return ProviderPolicyCommandState.PREPARED
        if not self.observations:
            return ProviderPolicyCommandState.DISPATCH_CLAIMED
        if self.evidence_integrity_conflict:
            return ProviderPolicyCommandState.EVIDENCE_MISMATCH_UNRESOLVED
        if self.has_matching_result:
            return ProviderPolicyCommandState.RESOLVED
        if self.has_definite_refusal:
            return ProviderPolicyCommandState.REFUSED
        return ProviderPolicyCommandState.DISPATCHED_UNRESOLVED


class ProviderPolicyCommandService:
    """Own one irreversible mutation claim and read-only uncertainty recovery."""

    def __init__(
        self,
        unit_of_work_factory,
        *,
        administration_policy: ProviderPolicyAdministrationPolicy,
        port_factory: Callable[[ProviderPolicyBinding], ProviderPolicyPort],
        clock: Callable[[], str],
    ) -> None:
        if type(administration_policy) is not ProviderPolicyAdministrationPolicy:
            raise ProviderPolicyCommandError("provider-policy administration policy is malformed")
        self._uow = unit_of_work_factory
        self._policy = administration_policy
        self._port_factory = port_factory
        self._clock = clock

    def grant(self, request: GrantProviderPolicy) -> ProviderPolicyCommandRecord:
        if type(request) is not GrantProviderPolicy:
            raise ProviderPolicyCommandError("provider-policy grant command is malformed")
        return self._mutate(request, ProviderPolicyOperation.GRANT)

    def revoke(self, request: RevokeProviderPolicy) -> ProviderPolicyCommandRecord:
        if type(request) is not RevokeProviderPolicy:
            raise ProviderPolicyCommandError("provider-policy revoke command is malformed")
        return self._mutate(request, ProviderPolicyOperation.REVOKE)

    def _mutate(self, request, operation: ProviderPolicyOperation) -> ProviderPolicyCommandRecord:
        self._policy.require(request.context)
        record = _record_for(request, operation, self._now())
        with self._uow() as uow:
            record = uow.stores.provider_policy_commands.reserve(record)
            uow.commit()
        if record.request_fingerprint != _record_for(request, operation, record.created_at).request_fingerprint:
            raise ProviderPolicyCommandConflict("provider-policy idempotency key conflicts")
        if record.dispatch_claimed_at is not None:
            return record
        self._policy.require(request.context)
        with self._uow() as uow:
            claimed = uow.stores.provider_policy_commands.claim(
                record.command_id, claimed_at=self._now()
            )
            uow.commit()
        if claimed is None:
            return self._read(record.command_id)
        self._policy.require(request.context)
        try:
            port = self._port_factory(request.context.binding)
            result = (
                port.grant(record.command)
                if operation is ProviderPolicyOperation.GRANT
                else port.revoke(record.command)
            )
            observation = _result_observation(
                record, result, ProviderPolicyObservationSource.INITIAL_RESPONSE, self._now()
            )
        except ProviderPolicyFailure as failure:
            observation = _failure_observation(
                record, failure, ProviderPolicyObservationSource.INITIAL_RESPONSE, self._now()
            )
        except Exception:
            observation = _failure_observation(
                record,
                ProviderPolicyFailure(
                    ProviderPolicyErrorCode.UNAVAILABLE,
                    "provider-policy result is unavailable",
                    effect_may_have_occurred=True,
                ),
                ProviderPolicyObservationSource.INITIAL_RESPONSE,
                self._now(),
            )
        return self._append(record.command_id, observation)

    def reconcile(self, request: ReconcileProviderPolicy) -> ProviderPolicyCommandRecord:
        if type(request) is not ReconcileProviderPolicy:
            raise ProviderPolicyCommandError("provider-policy reconcile command is malformed")
        self._policy.require(request.context)
        record = self._read(request.command_id)
        if (
            record.installation_id != request.context.installation_id
            or record.actor != request.context.identity
            or record.binding != request.context.binding
        ):
            raise ProviderPolicyAdministrationDenied(
                "provider-policy historical binding is unavailable"
            )
        if record.state in (
            ProviderPolicyCommandState.REFUSED,
            ProviderPolicyCommandState.RESOLVED,
        ):
            return record
        self._policy.require(request.context)
        try:
            port = self._port_factory(request.context.binding)
            result = port.command_result(
                ProviderPolicyCommandResultRequest(
                    record.binding.logical_provider_id, record.provider_correlation
                )
            )
            observation = _result_observation(
                record, result, ProviderPolicyObservationSource.COMMAND_RESULT_READ, self._now()
            )
        except ProviderPolicyFailure as failure:
            observation = _failure_observation(
                record, failure, ProviderPolicyObservationSource.COMMAND_RESULT_READ, self._now()
            )
        except Exception:
            observation = _failure_observation(
                record,
                ProviderPolicyFailure(
                    ProviderPolicyErrorCode.UNAVAILABLE,
                    "provider-policy command result is unavailable",
                ),
                ProviderPolicyObservationSource.COMMAND_RESULT_READ,
                self._now(),
            )
        return self._append(record.command_id, observation)

    def read(
        self, context: ProviderPolicyInvocationContext, command_id: str
    ) -> ProviderPolicyCommandRecord:
        self._policy.require(context)
        record = self._read(command_id)
        if (
            record.installation_id != context.installation_id
            or record.actor != context.identity
            or record.binding != context.binding
        ):
            raise ProviderPolicyAdministrationDenied("provider-policy command read is denied")
        return record

    def _read(self, command_id: str) -> ProviderPolicyCommandRecord:
        with self._uow() as uow:
            return uow.stores.provider_policy_commands.get(command_id)

    def _append(self, command_id: str, observation: ProviderPolicyOutcomeObservation):
        try:
            with self._uow() as uow:
                record = uow.stores.provider_policy_commands.append_observation(
                    command_id, observation
                )
                uow.commit()
            return record
        except ProviderPolicyCommandConflict:
            return self._read(command_id)
        except Exception:
            raise ProviderPolicyCommandUnavailable(
                "provider-policy outcome could not be durably recorded"
            ) from None

    def _now(self) -> str:
        result = self._clock()
        validate_canonical_utc_timestamp(result)
        return result


def _command(context, coordinate, revision, key) -> None:
    if type(context) is not ProviderPolicyInvocationContext:
        raise ProviderPolicyCommandError("provider-policy context is malformed")
    if type(coordinate) is not ProviderPolicyCoordinate:
        raise ProviderPolicyCommandError("provider-policy coordinate is malformed")
    if type(revision) is not int or not 0 <= revision <= (1 << 63) - 1:
        raise ProviderPolicyCommandError("provider-policy revision is malformed")
    if type(key) is not IdempotencyKey:
        raise ProviderPolicyCommandError("provider-policy idempotency key is malformed")


def _record_for(request, operation, created_at) -> ProviderPolicyCommandRecord:
    context = request.context
    command_id, correlation = _command_identity(
        context.installation_id,
        context.identity,
        context.binding.logical_provider_id,
        request.idempotency_key,
    )
    common = dict(
        logical_provider_id=context.binding.logical_provider_id,
        provider_id=context.binding.provider_id,
        coordinate=request.coordinate,
        expected_revision=request.expected_revision,
        caller_idempotency_key=request.idempotency_key.value,
        provider_correlation=correlation,
    )
    command = (
        ProviderPolicyGrantCommand(**common)
        if operation is ProviderPolicyOperation.GRANT
        else ProviderPolicyRevokeCommand(**common, grant_id=request.grant_id)
    )
    return ProviderPolicyCommandRecord(
        command_id=command_id,
        installation_id=context.installation_id,
        actor=context.identity,
        binding=context.binding,
        idempotency_key=request.idempotency_key,
        request_fingerprint=provider_policy_command_fingerprint(command),
        provider_correlation=correlation,
        command=command,
        created_at=created_at,
    )


def _command_identity(
    installation_id: str,
    actor: PrincipalIdentity,
    logical_provider_id: str,
    idempotency_key: IdempotencyKey,
) -> tuple[str, str]:
    identity_key = json.dumps(
        [
            installation_id,
            actor.issuer,
            actor.subject_id,
            actor.kind.value,
            logical_provider_id,
            idempotency_key.value,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    command_id = sha256(identity_key.encode("utf-8")).hexdigest()
    installation_digest = sha256(installation_id.encode("utf-8")).hexdigest()[:24]
    return command_id, f"cpk.{installation_digest}.{command_id}"


def _result_observation(record, result, source, observed_at):
    if type(result) is not ProviderPolicyCommandResult:
        failure = ProviderPolicyFailure(
            ProviderPolicyErrorCode.MALFORMED_RESULT,
            "provider-policy result is malformed",
            effect_may_have_occurred=True,
        )
        return _failure_observation(record, failure, source, observed_at)
    evidence = _evidence_fingerprint(result, source)
    return ProviderPolicyOutcomeObservation(
        sha256((record.command_id + "\x1f" + source.value + "\x1f" + evidence).encode()).hexdigest(),
        source,
        observed_at,
        evidence,
        canonical_result=result,
        matches_intent=result.matches(record.command),
    )


def _failure_observation(record, failure, source, observed_at):
    evidence = _evidence_fingerprint(failure, source)
    return ProviderPolicyOutcomeObservation(
        sha256((record.command_id + "\x1f" + source.value + "\x1f" + evidence).encode()).hexdigest(),
        source,
        observed_at,
        evidence,
        failure=failure,
    )


def _digest(value: object, name: str) -> None:
    if type(value) is not str or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ProviderPolicyCommandError(f"{name} is malformed")


def _evidence_fingerprint(value, source: ProviderPolicyObservationSource) -> str:
    payload = repr(value.descriptor()) if type(value) is ProviderPolicyFailure else repr(value)
    fingerprint = sha256(payload.encode("utf-8")).hexdigest()
    if type(value) is ProviderPolicyFailure:
        return sha256((source.value + "\x1f" + fingerprint).encode("utf-8")).hexdigest()
    return fingerprint


__all__ = [
    "GrantProviderPolicy",
    "ProviderPolicyAdmission",
    "ProviderPolicyAdministrationDenied",
    "ProviderPolicyAdministrationPolicy",
    "ProviderPolicyBinding",
    "ProviderPolicyCommandConflict",
    "ProviderPolicyCommandError",
    "ProviderPolicyCommandNotFound",
    "ProviderPolicyCommandRecord",
    "ProviderPolicyCommandService",
    "ProviderPolicyCommandState",
    "ProviderPolicyCommandUnavailable",
    "ProviderPolicyInvocationContext",
    "ProviderPolicyObservationSource",
    "ProviderPolicyOutcomeObservation",
    "ReconcileProviderPolicy",
    "RevokeProviderPolicy",
]
