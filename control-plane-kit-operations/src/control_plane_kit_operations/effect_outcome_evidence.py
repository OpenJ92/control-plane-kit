"""Pure durable evidence for direct runtime-effect outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib

import rfc8785

from control_plane_kit_core.operations import (
    EffectAttemptIdentity,
    EffectAttemptStatus,
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
    EffectResultKind,
    FailureCategory,
    RunId,
)
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
    RuntimeEndpointObservation,
    SecretEndpointMaterial,
)
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationEvidence,
    RuntimeEffectObservationFailure,
    RuntimeEffectObservationResult,
    RuntimeEffectObservedAbsent,
    RuntimeEffectObservedConflict,
    RuntimeEffectObservedFailed,
    RuntimeEffectObservedIndeterminate,
    RuntimeEffectObservedSucceeded,
    RuntimeEffectObserverUnsupported,
    runtime_effect_observation_fingerprint,
    runtime_effect_result_fingerprint,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectContractError,
    RuntimeEffectResult,
)
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.verification import (
    HttpCheck,
    HttpVerificationEvidence,
    VerificationCapability,
    VerificationCompleted,
    VerificationOutcome,
)

from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.records import (
    BoundedEvidence,
    FailureEvidence,
    ObservationFreshness,
    ObservationRecord,
    ObservationStatus,
    OperationsRecordError,
    ProbeKind,
    ProbeOutcome,
)


class EffectOutcomeProfile(StrEnum):
    """Closed provenance for one direct effect-attempt outcome."""

    EXECUTION_RESULT = "execution-result"
    PROVIDER_OBSERVATION = "provider-observation"


class _OutcomeError(StrEnum):
    EVIDENCE = "effect outcome evidence is invalid"
    RECORD = "effect outcome record is invalid"
    OBSERVATION = "effect outcome observation projection is invalid"

    @property
    def raised(self) -> None:
        raise OperationsRecordError(self.value)


_EXECUTION_ROWS = {
    EffectResultKind.SUCCEEDED: (
        EffectAttemptStatus.SUCCEEDED,
        EffectAttemptTransitionKind.SUCCEEDED,
        None,
    ),
    EffectResultKind.FAILED: (
        EffectAttemptStatus.FAILED,
        EffectAttemptTransitionKind.FAILED,
        (
            FailureCategory.TERMINAL,
            "runtime.effect-failed",
            "runtime effect reported failure",
        ),
    ),
    EffectResultKind.UNSUPPORTED: (
        EffectAttemptStatus.UNSUPPORTED,
        EffectAttemptTransitionKind.UNSUPPORTED,
        (
            FailureCategory.OPERATOR_REVIEW,
            "runtime.effect-unsupported",
            "runtime effect is unsupported",
        ),
    ),
    EffectResultKind.UNCERTAIN: (
        EffectAttemptStatus.UNCERTAIN,
        EffectAttemptTransitionKind.UNCERTAIN,
        (
            FailureCategory.UNCERTAIN,
            "runtime.effect-uncertain",
            "runtime effect outcome is uncertain",
        ),
    ),
}

_OBSERVATION_ROWS = {
    RuntimeEffectObservedSucceeded: (
        EffectAttemptStatus.SUCCEEDED,
        EffectAttemptTransitionKind.SUCCEEDED,
        None,
    ),
    RuntimeEffectObservedFailed: (
        EffectAttemptStatus.FAILED,
        EffectAttemptTransitionKind.FAILED,
        (
            FailureCategory.TERMINAL,
            "runtime.effect-observed-failed",
            "runtime observer confirmed failure",
        ),
    ),
    RuntimeEffectObservedAbsent: (
        EffectAttemptStatus.UNCERTAIN,
        EffectAttemptTransitionKind.UNCERTAIN,
        (
            FailureCategory.UNCERTAIN,
            "runtime.effect-observed-absent",
            "runtime observer found no matching effect",
        ),
    ),
    RuntimeEffectObservedConflict: (
        EffectAttemptStatus.UNCERTAIN,
        EffectAttemptTransitionKind.UNCERTAIN,
        (
            FailureCategory.UNCERTAIN,
            "runtime.effect-observed-conflict",
            "runtime observer found conflicting effect truth",
        ),
    ),
    RuntimeEffectObservedIndeterminate: (
        EffectAttemptStatus.UNCERTAIN,
        EffectAttemptTransitionKind.UNCERTAIN,
        (
            FailureCategory.UNCERTAIN,
            "runtime.effect-observed-indeterminate",
            "runtime observer could not determine effect truth",
        ),
    ),
    RuntimeEffectObserverUnsupported: (
        EffectAttemptStatus.UNCERTAIN,
        EffectAttemptTransitionKind.UNCERTAIN,
        (
            FailureCategory.OPERATOR_REVIEW,
            "runtime.effect-observer-unsupported",
            "runtime observer does not support this effect",
        ),
    ),
}

_HTTP_VERIFICATION_ROWS = {
    VerificationOutcome.PASSED: (
        ObservationStatus.VERIFIED,
        ProbeOutcome.HEALTHY,
    ),
    VerificationOutcome.FAILED: (
        ObservationStatus.VERIFICATION_FAILED,
        ProbeOutcome.UNHEALTHY,
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
        ProbeOutcome.UNKNOWN,
    ),
}

_FIXED_HTTP_VERIFICATION_CATEGORIES = {
    VerificationOutcome.TIMED_OUT: "policy-exhausted",
    VerificationOutcome.MALFORMED: "response-oversized",
    VerificationOutcome.REJECTED: "response-rejected",
}


def _validated_attempt(
    value: object,
    error: _OutcomeError,
) -> EffectAttemptRecord:
    if value.__class__ is not EffectAttemptRecord:
        error.raised
    validated = None
    try:
        validated = EffectAttemptRecord(
            value.state,
            value.original_start_event,
            value.latest_transition_event,
        )
    except OperationsRecordError:
        pass
    if validated is None:
        error.raised
    return validated


def _legacy_effect_outcome_failure(
    outcome: EffectAttemptOutcome,
) -> FailureEvidence | None:
    row = outcome.failure_row
    if row is None:
        return None
    return FailureEvidence(
        category=row[0],
        code=row[1],
        message=row[2],
        details=BoundedEvidence.from_mapping(
            {
                "effect_outcome": {
                    "profile": outcome.profile.value,
                    "outcome_fingerprint": outcome.outcome_fingerprint,
                }
            }
        ),
    )


class _EffectOutcomeValue:
    @property
    def profile(self) -> EffectOutcomeProfile:
        if self.__class__ is ExecutionEffectOutcome:
            return EffectOutcomeProfile.EXECUTION_RESULT
        return EffectOutcomeProfile.PROVIDER_OBSERVATION

    @property
    def effect_id(self) -> str:
        if self.__class__ is ExecutionEffectOutcome:
            return self.result.effect_id
        return self.observation.effect_id

    @property
    def outcome_fingerprint(self) -> str:
        if self.__class__ is ExecutionEffectOutcome:
            return runtime_effect_result_fingerprint(self.result)
        return runtime_effect_observation_fingerprint(self.observation)

    @property
    def endpoint_observations(self) -> tuple:
        if self.__class__ is ExecutionEffectOutcome:
            return self.result.observations
        return self.observation.observations

    @property
    def status(self) -> EffectAttemptStatus:
        if self.__class__ is ExecutionEffectOutcome:
            return _EXECUTION_ROWS[self.result.kind][0]
        return _OBSERVATION_ROWS[self.observation.__class__][0]

    @property
    def transition_kind(self) -> EffectAttemptTransitionKind:
        if self.__class__ is ExecutionEffectOutcome:
            return _EXECUTION_ROWS[self.result.kind][1]
        return _OBSERVATION_ROWS[self.observation.__class__][1]

    @property
    def failure_row(self) -> tuple | None:
        if self.__class__ is ExecutionEffectOutcome:
            return _EXECUTION_ROWS[self.result.kind][2]
        return _OBSERVATION_ROWS[self.observation.__class__][2]

    @property
    def observation_count(self) -> int:
        count = 0
        for _ in self.endpoint_observations:
            count += 1
        return count

    @property
    def _descriptor(self) -> dict[str, object]:
        identity = self.identity
        return {
            "profile": self.profile.value,
            "identity": {
                "run_id": identity.run_id.value,
                "activity_id": identity.activity_id,
                "attempt": identity.attempt,
            },
            "effect_id": self.effect_id,
            "request_fingerprint": self.request_fingerprint,
            "outcome_fingerprint": self.outcome_fingerprint,
            "transition_kind": self.transition_kind.value,
            "observation_count": self.observation_count,
        }

    @property
    def _bounded_evidence(self) -> list[BoundedEvidence]:
        effect_outcome = {
            "profile": self.profile.value,
            "outcome_fingerprint": self.outcome_fingerprint,
        }
        if (
            self.__class__ is ExecutionEffectOutcome
            and self.result.kind is EffectResultKind.FAILED
        ):
            category = self.result.failure.code
            category_admitted = (
                category.__class__ is str and category != "" and not category[128:]
            )
            namespace_seen = False
            segment_start = True
            previous_hyphen = False
            for character in category if category_admitted else ():
                if "a" <= character <= "z":
                    segment_start = False
                    previous_hyphen = False
                elif "0" <= character <= "9" and not segment_start:
                    previous_hyphen = False
                elif character == "-" and not segment_start and not previous_hyphen:
                    previous_hyphen = True
                elif character == "." and not segment_start and not previous_hyphen:
                    namespace_seen = True
                    segment_start = True
                else:
                    category_admitted = False
            if segment_start or previous_hyphen or not namespace_seen:
                category_admitted = False
            if category_admitted:
                effect_outcome["runtime_failure_code"] = category

        mappings = [
            {
                "effect_outcome": effect_outcome,
            }
        ]
        for observation in self.endpoint_observations:
            if type(observation) is RuntimeEndpointObservation:
                mappings.append({"runtime_endpoint": observation.descriptor()})
            else:
                mappings.append(
                    {"verification_completion": observation.descriptor()}
                )
        return [BoundedEvidence.from_mapping(value) for value in mappings]

    @property
    def _admitted(self) -> bool:
        if self.__class__ not in (ExecutionEffectOutcome, ObservedEffectOutcome):
            return False
        if self.identity.__class__ is not EffectAttemptIdentity:
            return False
        identity = self.identity
        if (
            identity.run_id.__class__ is not RunId
            or identity.run_id.value.__class__ is not str
            or identity.activity_id.__class__ is not str
            or identity.attempt.__class__ is not int
            or not 1 <= identity.attempt <= 2_147_483_647
        ):
            return False

        if self.__class__ is ExecutionEffectOutcome:
            if (
                self.result.__class__ is not RuntimeEffectResult
                or self.result.kind.__class__ is not EffectResultKind
                or self.result.kind not in _EXECUTION_ROWS
                or self.result.observations.__class__ is not tuple
                or self.request_fingerprint.__class__ is not str
            ):
                return False
        else:
            if (
                self.observation.__class__ not in _OBSERVATION_ROWS
                or self.observation.evidence.__class__
                is not RuntimeEffectObservationEvidence
                or self.observation.observations.__class__ is not tuple
            ):
                return False

        texts = [
            (identity.run_id.value, 200, "identity"),
            (identity.activity_id, 200, "identity"),
            (self.effect_id, 512, "text"),
            (self.request_fingerprint, 64, "fingerprint"),
        ]
        if self.__class__ is ObservedEffectOutcome:
            failure = self.observation.failure
            if failure is not None:
                if failure.__class__ is not RuntimeEffectObservationFailure:
                    return False
                texts += [(failure.code, 512, "text"), (failure.message, 512, "text")]
                if failure.details is not None and (
                    failure.details.__class__
                    is not RuntimeEffectObservationEvidence
                ):
                    return False
            requires_failure = self.observation.__class__ in (
                RuntimeEffectObservedFailed,
                RuntimeEffectObservedConflict,
                RuntimeEffectObservedIndeterminate,
                RuntimeEffectObserverUnsupported,
            )
            if requires_failure != (failure is not None):
                return False
            if self.observation.__class__ in (
                RuntimeEffectObservedAbsent,
                RuntimeEffectObserverUnsupported,
            ) and self.endpoint_observations:
                return False

        for observation in self.endpoint_observations:
            if type(observation) is RuntimeEndpointObservation:
                if (
                    observation.protocol.__class__ is not Protocol
                    or observation.context.__class__ is not EndpointContext
                ):
                    return False
                address_class = observation.address.__class__
                if address_class not in (
                    LiteralEndpointMaterial,
                    SecretEndpointMaterial,
                ):
                    return False
                texts += [
                    (observation.subject_id, 512, "text"),
                    (observation.socket_name, 512, "text"),
                    (observation.graph_id, 512, "text"),
                    (
                        observation.address.value
                        if address_class is LiteralEndpointMaterial
                        else observation.address.reference_id,
                        512,
                        "text",
                    ),
                ]
            elif type(observation) is not VerificationCompleted:
                return False

        for value, maximum, grammar in texts:
            if (
                type(value) is not str
                or not value
                or len(value) > maximum
                or any(ord(character) < 32 for character in value)
            ):
                return False
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                return False
            if grammar == "fingerprint":
                if not value[63:] or value[64:]:
                    return False
                for character in value:
                    if character not in "0123456789abcdef":
                        return False
            if grammar == "identity":
                first = value[0]
                if not (
                    "A" <= first <= "Z"
                    or "a" <= first <= "z"
                    or "0" <= first <= "9"
                ):
                    return False
                for character in value[1:]:
                    if not (
                        "A" <= character <= "Z"
                        or "a" <= character <= "z"
                        or "0" <= character <= "9"
                        or character in "._:-"
                    ):
                        return False

        for endpoint in self.endpoint_observations:
            if type(endpoint) is VerificationCompleted:
                continue
            admitted = True
            try:
                material = endpoint.address
                if material.__class__ is SecretEndpointMaterial:
                    material = SecretEndpointMaterial(material.reference_id)
                protocol = Protocol(
                    endpoint.protocol.transport,
                    endpoint.protocol.application,
                )
                RuntimeEndpointObservation(
                    endpoint.subject_id,
                    endpoint.socket_name,
                    endpoint.graph_id,
                    protocol,
                    endpoint.context,
                    material,
                )
            except (TypeError, ValueError):
                admitted = False
            if not admitted:
                return False

        accepted = True
        try:
            self.outcome_fingerprint
            self._bounded_evidence
        except (RuntimeEffectContractError, OperationsRecordError):
            accepted = False
        return accepted


@dataclass(frozen=True)
class ExecutionEffectOutcome(_EffectOutcomeValue):
    """One exact live execution result and its direct-attempt coordinates."""

    identity: EffectAttemptIdentity
    request_fingerprint: str
    result: RuntimeEffectResult = field(repr=False)

    def __post_init__(self) -> None:
        if not self._admitted:
            _OutcomeError.EVIDENCE.raised

    def descriptor(self) -> dict[str, object]:
        return self._descriptor


@dataclass(frozen=True)
class ObservedEffectOutcome(_EffectOutcomeValue):
    """One exact inspect-only observation and its direct-attempt coordinates."""

    identity: EffectAttemptIdentity
    observation: RuntimeEffectObservationResult = field(repr=False)

    def __post_init__(self) -> None:
        if not self._admitted:
            _OutcomeError.EVIDENCE.raised

    @property
    def request_fingerprint(self) -> str:
        return self.observation.request_fingerprint

    def descriptor(self) -> dict[str, object]:
        return self._descriptor


EffectAttemptOutcome = ExecutionEffectOutcome | ObservedEffectOutcome


@dataclass(frozen=True)
class EffectAttemptOutcomeRecord:
    """A direct outcome bound to its exact historical attempt snapshot."""

    workspace_id: str
    outcome: EffectAttemptOutcome = field(repr=False)
    attempt: EffectAttemptRecord = field(repr=False)
    endpoint_observations: tuple[ObservationRecord, ...] = field(repr=False)

    def __post_init__(self) -> None:
        valid = (
            self.workspace_id.__class__ is str
            and self.workspace_id
            and not self.workspace_id[512:]
            and self.outcome.__class__
            in (ExecutionEffectOutcome, ObservedEffectOutcome)
            and self.outcome._admitted
            and self.endpoint_observations.__class__ is tuple
        )
        for character in self.workspace_id if valid else ():
            if character < " " or "\ud800" <= character <= "\udfff":
                valid = False

        attempt = None
        if valid:
            attempt = _validated_attempt(self.attempt, _OutcomeError.RECORD)
            state = attempt.state
            original = attempt.original_start_event
            latest = attempt.latest_transition_event
            valid = (
                state.identity == self.outcome.identity
                and state.request_fingerprint == self.outcome.request_fingerprint
                and state.status is self.outcome.status
                and state.outcome_fingerprint == self.outcome.outcome_fingerprint
                and state.recovery_decision is None
                and original.event_id == self.outcome.effect_id
                and original.event_id != latest.event_id
                and original.run_id == state.identity.run_id.value
                and latest.run_id == state.identity.run_id.value
                and original.activity_id == state.identity.activity_id
                and latest.activity_id == state.identity.activity_id
                and 1 <= original.ordinal < latest.ordinal <= 2_147_483_647
                and original.failure is None
                and original.recovery is None
                and latest.recovery is None
            )

        if valid:
            start_kind = original.kind.value
            compensation = start_kind == "step_compensation_started"
            valid = start_kind in ("step_started", "step_compensation_started")
            expected_latest_kind = (
                "step_compensation_" if compensation else "step_"
            ) + self.outcome.status.value
            valid = valid and latest.kind.value == expected_latest_kind

        if valid:
            for value in (
                original.event_id,
                latest.event_id,
                original.run_id,
                latest.run_id,
                original.occurred_at,
                latest.occurred_at,
                original.activity_id,
                latest.activity_id,
            ):
                if value.__class__ is not str or not value or value[512:]:
                    valid = False
                    break
                for character in value:
                    if character < " " or "\ud800" <= character <= "\udfff":
                        valid = False
                        break
                if not valid:
                    break

        if valid:
            failure = latest.failure
            row = self.outcome.failure_row
            if row is None:
                valid = failure is None
            else:
                current_failure = effect_outcome_failure(self.outcome)
                legacy_failure = _legacy_effect_outcome_failure(self.outcome)
                valid = (
                    failure.__class__ is FailureEvidence
                    and failure.details.__class__ is BoundedEvidence
                    and failure.details.canonical_json.__class__ is str
                    and failure in (current_failure, legacy_failure)
                )

        if valid:
            endpoints = self.outcome.endpoint_observations
            evidence = self.outcome._bounded_evidence
            endpoint_count = 0
            row_count = 0
            for _ in endpoints:
                endpoint_count += 1
            for _ in self.endpoint_observations:
                row_count += 1
            valid = endpoint_count == row_count

        if valid:
            index = 0
            seen_ids: dict[str, None] = {}
            for record in self.endpoint_observations:
                observation = endpoints[index]
                valid = (
                    record.__class__ is ObservationRecord
                    and record.observation_id.__class__ is str
                    and record.observation_id
                    and not record.observation_id[512:]
                    and record.observation_id not in seen_ids
                    and record.workspace_id.__class__ is str
                    and record.workspace_id == self.workspace_id
                    and record.subject_id.__class__ is str
                    and record.observed_at.__class__ is str
                    and record.observed_at == latest.occurred_at
                    and record.evidence.__class__ is BoundedEvidence
                    and record.evidence.canonical_json.__class__ is str
                    and record.freshness is ObservationFreshness.FRESH
                    and record.graph_id.__class__ is str
                )
                if valid and type(observation) is RuntimeEndpointObservation:
                    valid = (
                        record.subject_id == observation.subject_id
                        and record.status is ObservationStatus.UNKNOWN
                        and record.evidence == evidence[index + 1]
                        and record.graph_id == observation.graph_id
                        and record.probe_kind is ProbeKind.TRANSPORT
                        and record.probe_outcome is ProbeOutcome.UNKNOWN
                        and record.endpoint_context is observation.context
                    )
                elif valid and type(observation) is VerificationCompleted:
                    valid = _verification_record_matches(
                        record,
                        observation,
                        self.outcome,
                    )
                else:
                    valid = False
                if not valid:
                    break
                for value in (
                    record.observation_id,
                    record.workspace_id,
                    record.subject_id,
                    record.observed_at,
                    record.graph_id,
                ):
                    for character in value:
                        if character < " " or "\ud800" <= character <= "\udfff":
                            valid = False
                            break
                    if not valid:
                        break
                if not valid:
                    break
                seen_ids[record.observation_id] = None
                index += 1

        if not valid:
            _OutcomeError.RECORD.raised

    def descriptor(self) -> dict[str, object]:
        event = self.attempt.latest_transition_event
        return {
            "workspace_id": self.workspace_id,
            "outcome": self.outcome._descriptor,
            "transition_event": {
                "event_id": event.event_id,
                "run_id": event.run_id,
                "ordinal": event.ordinal,
            },
            "observation_count": self.outcome.observation_count,
        }


def effect_outcome_transition(
    outcome: EffectAttemptOutcome,
) -> EffectAttemptTransition:
    if (
        outcome.__class__ not in (ExecutionEffectOutcome, ObservedEffectOutcome)
        or not outcome._admitted
    ):
        _OutcomeError.EVIDENCE.raised
    return EffectAttemptTransition(
        kind=outcome.transition_kind,
        identity=outcome.identity,
        outcome_fingerprint=outcome.outcome_fingerprint,
    )


def effect_outcome_failure(
    outcome: EffectAttemptOutcome,
) -> FailureEvidence | None:
    if (
        outcome.__class__ not in (ExecutionEffectOutcome, ObservedEffectOutcome)
        or not outcome._admitted
    ):
        _OutcomeError.EVIDENCE.raised
    row = outcome.failure_row
    if row is None:
        return None
    return FailureEvidence(
        category=row[0],
        code=row[1],
        message=row[2],
        details=outcome._bounded_evidence[0],
    )


def effect_outcome_observation_records(
    outcome: EffectAttemptOutcome,
    attempt: EffectAttemptRecord,
    *,
    workspace_id: str,
    observation_ids: tuple[str, ...],
    intent_record: EffectAttemptIntentRecord | None = None,
) -> tuple[ObservationRecord, ...]:
    valid = (
        outcome.__class__ in (ExecutionEffectOutcome, ObservedEffectOutcome)
        and outcome._admitted
        and workspace_id.__class__ is str
        and workspace_id
        and not workspace_id[512:]
        and observation_ids.__class__ is tuple
    )
    validated_attempt = None
    if valid:
        validated_attempt = _validated_attempt(
            attempt,
            _OutcomeError.OBSERVATION,
        )
        valid = (
            validated_attempt.state.identity == outcome.identity
            and validated_attempt.state.request_fingerprint
            == outcome.request_fingerprint
            and validated_attempt.state.status is outcome.status
            and validated_attempt.state.outcome_fingerprint
            == outcome.outcome_fingerprint
            and validated_attempt.state.recovery_decision is None
            and validated_attempt.original_start_event.event_id
            == outcome.effect_id
        )
    endpoint_count = 0
    id_count = 0
    for _ in outcome.endpoint_observations if valid else ():
        endpoint_count += 1
    for observation_id in observation_ids if valid else ():
        id_count += 1
        if observation_id.__class__ is not str or not observation_id or observation_id[512:]:
            valid = False
            break
        for character in observation_id:
            if character < " " or "\ud800" <= character <= "\udfff":
                valid = False
                break
    for character in workspace_id if valid else ():
        if character < " " or "\ud800" <= character <= "\udfff":
            valid = False
    identities = set(observation_ids) if valid else {}
    unique_count = 0
    for _ in identities:
        unique_count += 1
    if endpoint_count != id_count or unique_count != id_count:
        valid = False
    if not valid:
        _OutcomeError.OBSERVATION.raised

    occurred_at = validated_attempt.latest_transition_event.occurred_at
    records = []
    for index, (observation_id, observation) in enumerate(
        zip(observation_ids, outcome.endpoint_observations, strict=True)
    ):
        if type(observation) is RuntimeEndpointObservation:
            record = ObservationRecord(
                observation_id=observation_id,
                workspace_id=workspace_id,
                subject_id=observation.subject_id,
                status=ObservationStatus.UNKNOWN,
                observed_at=occurred_at,
                evidence=outcome._bounded_evidence[index + 1],
                freshness=ObservationFreshness.FRESH,
                graph_id=observation.graph_id,
                probe_kind=ProbeKind.TRANSPORT,
                probe_outcome=ProbeOutcome.UNKNOWN,
                endpoint_context=observation.context,
            )
        elif type(observation) is VerificationCompleted:
            check = _authoritative_http_check(
                intent_record,
                validated_attempt,
                outcome,
                observation,
            )
            record = _http_verification_record(
                observation_id,
                workspace_id,
                occurred_at,
                outcome,
                observation,
                check,
            )
        else:
            _OutcomeError.OBSERVATION.raised
        records.append(record)
    return tuple(records)


def _authoritative_http_check(
    intent_record: EffectAttemptIntentRecord | None,
    attempt: EffectAttemptRecord,
    outcome: EffectAttemptOutcome,
    completion: VerificationCompleted,
) -> HttpCheck:
    valid = (
        type(intent_record) is EffectAttemptIntentRecord
        and intent_record.identity == attempt.state.identity
        and intent_record.original_start_event == attempt.original_start_event
        and intent_record.request_fingerprint == outcome.request_fingerprint
        and intent_record.intent.source.desired_graph_id
        == completion.identity.graph_id
        and completion.capability is VerificationCapability.HTTP
    )
    materials = (
        tuple(
            material
            for material in intent_record.intent.products
            if material.node_id == completion.identity.node_id
        )
        if valid
        else ()
    )
    valid = valid and len(materials) == 1
    checks = (
        tuple(
            check
            for check in materials[0].product.runtime_contract.verification.checks
            if check.check_id == completion.identity.check_id
        )
        if valid
        else ()
    )
    valid = valid and len(checks) == 1 and type(checks[0]) is HttpCheck
    if not valid:
        _OutcomeError.OBSERVATION.raised
    check = checks[0]
    evidence = completion.evidence
    if evidence is not None and type(evidence) is not HttpVerificationEvidence:
        _OutcomeError.OBSERVATION.raised
    if evidence is not None and (
        evidence.expected_body_sha256 != check.expected_body_sha256
        or (
            check.expected_body_sha256 is None
            and evidence.body_sha256_matches is not None
        )
    ):
        _OutcomeError.OBSERVATION.raised
    if completion.outcome is VerificationOutcome.PASSED and (
        evidence is None
        or evidence.status_code not in check.expected_statuses
        or (
            check.expected_body_sha256 is not None
            and evidence.body_sha256_matches is not True
        )
    ):
        _OutcomeError.OBSERVATION.raised
    _http_verification_category(completion, check)
    return check


def _http_verification_record(
    observation_id: str,
    workspace_id: str,
    occurred_at: str,
    outcome: EffectAttemptOutcome,
    completion: VerificationCompleted,
    check: HttpCheck,
) -> ObservationRecord:
    evidence = completion.evidence
    row = _HTTP_VERIFICATION_ROWS[completion.outcome]
    category = _http_verification_category(completion, check)
    payload = {
        "run_id": outcome.identity.run_id.value,
        "activity_id": outcome.identity.activity_id,
        "attempt": outcome.identity.attempt,
        "effect_id": outcome.effect_id,
        "node_id": completion.identity.node_id,
        "check_id": completion.identity.check_id,
        "provider_socket": check.provider_socket,
        "path": check.path,
        "http_status": None if evidence is None else evidence.status_code,
        "response_bytes": None if evidence is None else evidence.response_bytes,
        "expected_body_sha256": check.expected_body_sha256,
        "body_sha256_matches": (
            None if evidence is None else evidence.body_sha256_matches
        ),
    }
    if category is not None:
        payload.update({"stage": "http-verification", "category": category})
    return ObservationRecord(
        observation_id=observation_id,
        workspace_id=workspace_id,
        subject_id=_verification_subject(completion),
        status=row[0],
        observed_at=occurred_at,
        evidence=BoundedEvidence.from_mapping({"http_verification": payload}),
        freshness=ObservationFreshness.FRESH,
        graph_id=completion.identity.graph_id,
        probe_kind=ProbeKind.APPLICATION_HEALTH,
        probe_outcome=row[1],
        endpoint_context=EndpointContext.RUNTIME_PRIVATE,
    )


def _http_verification_category(
    completion: VerificationCompleted,
    check: HttpCheck,
) -> str | None:
    if completion.outcome is VerificationOutcome.PASSED:
        return None
    if completion.outcome is not VerificationOutcome.FAILED:
        return _FIXED_HTTP_VERIFICATION_CATEGORIES[completion.outcome]
    evidence = completion.evidence
    if evidence is None:
        return "transport-unavailable"
    if evidence.status_code not in check.expected_statuses:
        return "status-mismatch"
    if (
        check.expected_body_sha256 is not None
        and evidence.expected_body_sha256 == check.expected_body_sha256
        and evidence.body_sha256_matches is False
    ):
        return "body-mismatch"
    _OutcomeError.OBSERVATION.raised


def _verification_subject(completion: VerificationCompleted) -> str:
    document = {
        "check_id": completion.identity.check_id,
        "node_id": completion.identity.node_id,
        "schema": "cpk.verification-subject.v1",
    }
    return "verification:" + hashlib.sha256(rfc8785.dumps(document)).hexdigest()


def _verification_record_matches(
    record: ObservationRecord,
    completion: VerificationCompleted,
    outcome: EffectAttemptOutcome,
) -> bool:
    row = _HTTP_VERIFICATION_ROWS.get(completion.outcome)
    if row is None or completion.capability is not VerificationCapability.HTTP:
        return False
    payloads = record.evidence.descriptor()
    if set(payloads) != {"http_verification"}:
        return False
    payload = payloads["http_verification"]
    category = None
    if completion.outcome is VerificationOutcome.FAILED:
        if completion.evidence is None:
            category = "transport-unavailable"
        else:
            admitted = {"status-mismatch"}
            if (
                completion.evidence.expected_body_sha256 is not None
                and completion.evidence.body_sha256_matches is False
            ):
                admitted.add("body-mismatch")
            candidate = payload.get("category") if type(payload) is dict else None
            if candidate in admitted:
                category = candidate
    elif completion.outcome is not VerificationOutcome.PASSED:
        category = _FIXED_HTTP_VERIFICATION_CATEGORIES[completion.outcome]
    keys = {
        "run_id",
        "activity_id",
        "attempt",
        "effect_id",
        "node_id",
        "check_id",
        "provider_socket",
        "path",
        "http_status",
        "response_bytes",
        "expected_body_sha256",
        "body_sha256_matches",
    }
    if category is not None:
        keys |= {"stage", "category"}
    evidence = completion.evidence
    return (
        type(payload) is dict
        and set(payload) == keys
        and payload["run_id"] == outcome.identity.run_id.value
        and payload["activity_id"] == outcome.identity.activity_id
        and payload["attempt"] == outcome.identity.attempt
        and payload["effect_id"] == outcome.effect_id
        and payload["node_id"] == completion.identity.node_id
        and payload["check_id"] == completion.identity.check_id
        and type(payload["provider_socket"]) is str
        and bool(payload["provider_socket"])
        and type(payload["path"]) is str
        and payload["path"].startswith("/")
        and payload["http_status"]
        == (None if evidence is None else evidence.status_code)
        and payload["response_bytes"]
        == (None if evidence is None else evidence.response_bytes)
        and (
            (
                evidence is not None
                and payload["expected_body_sha256"]
                == evidence.expected_body_sha256
            )
            or (
                evidence is None
                and (
                    payload["expected_body_sha256"] is None
                    or (
                        type(payload["expected_body_sha256"]) is str
                        and len(payload["expected_body_sha256"]) == 64
                        and all(
                            character in "0123456789abcdef"
                            for character in payload["expected_body_sha256"]
                        )
                    )
                )
            )
        )
        and payload["body_sha256_matches"]
        == (None if evidence is None else evidence.body_sha256_matches)
        and (category is None or payload["stage"] == "http-verification")
        and (category is None or payload["category"] == category)
        and record.subject_id == _verification_subject(completion)
        and record.status is row[0]
        and record.graph_id == completion.identity.graph_id
        and record.probe_kind is ProbeKind.APPLICATION_HEALTH
        and record.probe_outcome is row[1]
        and record.endpoint_context is EndpointContext.RUNTIME_PRIVATE
    )


__all__ = (
    "EffectAttemptOutcome",
    "EffectAttemptOutcomeRecord",
    "EffectOutcomeProfile",
    "ExecutionEffectOutcome",
    "ObservedEffectOutcome",
    "effect_outcome_failure",
    "effect_outcome_observation_records",
    "effect_outcome_transition",
)
