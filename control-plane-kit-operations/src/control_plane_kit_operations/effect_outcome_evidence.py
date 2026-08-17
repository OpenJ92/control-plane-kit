"""Pure durable evidence for direct runtime-effect outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from control_plane_kit_core.operations import (
    EffectAttemptIdentity,
    EffectAttemptStatus,
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
    EffectResultKind,
    FailureCategory,
)
from control_plane_kit_core.runtime_effect_observation import (
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
        mappings = [
            {
                "effect_outcome": {
                    "profile": self.profile.value,
                    "outcome_fingerprint": self.outcome_fingerprint,
                }
            }
        ]
        for endpoint in self.endpoint_observations:
            mappings += [{"runtime_endpoint": endpoint.descriptor()}]
        return [BoundedEvidence.from_mapping(value) for value in mappings]

    @property
    def _admitted(self) -> bool:
        if self.__class__ not in (ExecutionEffectOutcome, ObservedEffectOutcome):
            return False
        if self.identity.__class__ is not EffectAttemptIdentity:
            return False
        identity = self.identity
        run_class = identity.run_id.__class__
        if (
            run_class.__module__ != "control_plane_kit_core.operations.run_identity"
            or run_class.__qualname__ != "RunId"
            or run_class.__mro__[1:] != (object,)
            or identity.attempt.__class__ is not int
            or not 1 <= identity.attempt <= 2_147_483_647
        ):
            return False

        if self.__class__ is ExecutionEffectOutcome:
            if (
                self.result.__class__ is not RuntimeEffectResult
                or self.result.kind not in _EXECUTION_ROWS
                or self.request_fingerprint.__class__ is not str
            ):
                return False
        elif self.observation.__class__ not in _OBSERVATION_ROWS:
            return False

        texts = [
            (identity.run_id.value, 200, "identity"),
            (identity.activity_id, 200, "identity"),
            (self.effect_id, 512, "text"),
            (self.request_fingerprint, 64, "fingerprint"),
        ]
        if self.__class__ is ObservedEffectOutcome:
            evidence = self.observation.evidence
            if (
                evidence.__class__.__module__
                != "control_plane_kit_core.runtime_effect_observation"
                or evidence.__class__.__qualname__
                != "RuntimeEffectObservationEvidence"
            ):
                return False
            failure = self.observation.failure
            if failure is not None:
                if (
                    failure.__class__.__module__
                    != "control_plane_kit_core.runtime_effect_observation"
                    or failure.__class__.__qualname__
                    != "RuntimeEffectObservationFailure"
                ):
                    return False
                texts += [(failure.code, 512, "text"), (failure.message, 512, "text")]
                if failure.details is not None and (
                    failure.details.__class__.__module__
                    != "control_plane_kit_core.runtime_effect_observation"
                    or failure.details.__class__.__qualname__
                    != "RuntimeEffectObservationEvidence"
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

        for endpoint in self.endpoint_observations:
            endpoint_class = endpoint.__class__
            if (
                endpoint_class.__module__ != "control_plane_kit_core.probe_intents"
                or endpoint_class.__qualname__ != "RuntimeEndpointObservation"
                or endpoint_class.__mro__[1:] != (object,)
                or endpoint.protocol.__class__.__module__
                != "control_plane_kit_core.types"
                or endpoint.protocol.__class__.__qualname__ != "Protocol"
                or endpoint.context.__class__.__module__
                != "control_plane_kit_core.probe_intents"
                or endpoint.context.__class__.__qualname__ != "EndpointContext"
            ):
                return False
            address_class = endpoint.address.__class__
            if (
                address_class.__module__ != "control_plane_kit_core.probe_intents"
                or address_class.__qualname__
                not in ("LiteralEndpointMaterial", "SecretEndpointMaterial")
            ):
                return False
            texts += [
                (endpoint.subject_id, 512, "text"),
                (endpoint.socket_name, 512, "text"),
                (endpoint.graph_id, 512, "text"),
                (
                    endpoint.address.value
                    if address_class.__qualname__ == "LiteralEndpointMaterial"
                    else endpoint.address.reference_id,
                    512,
                    "text",
                ),
            ]

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
            and self.attempt.__class__ is EffectAttemptRecord
            and self.endpoint_observations.__class__ is tuple
        )
        for character in self.workspace_id if valid else ():
            if character < " " or "\ud800" <= character <= "\udfff":
                valid = False

        if valid:
            state = self.attempt.state
            original = self.attempt.original_start_event
            latest = self.attempt.latest_transition_event
            state_class = state.__class__
            run_class = state.identity.run_id.__class__
            original_class = original.__class__
            latest_class = latest.__class__
            valid = (
                state_class.__module__
                == "control_plane_kit_core.operations.recovery"
                and state_class.__qualname__ == "EffectAttemptState"
                and state.identity.__class__ is EffectAttemptIdentity
                and state.request_fingerprint.__class__ is str
                and run_class.__module__
                == "control_plane_kit_core.operations.run_identity"
                and run_class.__qualname__ == "RunId"
                and run_class.__mro__[1:] == (object,)
                and state.identity.run_id.value.__class__ is str
                and state.identity.activity_id.__class__ is str
                and state.identity.attempt.__class__ is int
                and state.identity == self.outcome.identity
                and state.request_fingerprint == self.outcome.request_fingerprint
                and state.status is self.outcome.status
                and state.outcome_fingerprint == self.outcome.outcome_fingerprint
                and state.recovery_decision is None
                and original_class.__module__
                == "control_plane_kit_operations.records"
                and original_class.__qualname__ == "ActivityEventRecord"
                and latest_class.__module__
                == "control_plane_kit_operations.records"
                and latest_class.__qualname__ == "ActivityEventRecord"
                and original.event_id == self.outcome.effect_id
                and original.event_id != latest.event_id
                and original.run_id == state.identity.run_id.value
                and latest.run_id == state.identity.run_id.value
                and original.activity_id == state.identity.activity_id
                and latest.activity_id == state.identity.activity_id
                and original.ordinal.__class__ is int
                and latest.ordinal.__class__ is int
                and 1 <= original.ordinal < latest.ordinal <= 2_147_483_647
                and original.failure is None
                and original.recovery is None
                and latest.recovery is None
                and original.evidence.__class__ is BoundedEvidence
                and latest.evidence.__class__ is BoundedEvidence
                and original.evidence.canonical_json.__class__ is str
                and latest.evidence.canonical_json.__class__ is str
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
                details_json = (
                    '{"effect_outcome":{"outcome_fingerprint":"'
                    + self.outcome.outcome_fingerprint
                    + '","profile":"'
                    + self.outcome.profile.value
                    + '"}}'
                )
                valid = (
                    failure.__class__ is FailureEvidence
                    and failure.category is row[0]
                    and failure.code == row[1]
                    and failure.message == row[2]
                    and failure.details.__class__ is BoundedEvidence
                    and failure.details.canonical_json.__class__ is str
                    and failure.details.canonical_json == details_json
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
                endpoint = endpoints[index]
                valid = (
                    record.__class__ is ObservationRecord
                    and record.observation_id.__class__ is str
                    and record.observation_id
                    and not record.observation_id[512:]
                    and record.observation_id not in seen_ids
                    and record.workspace_id.__class__ is str
                    and record.workspace_id == self.workspace_id
                    and record.subject_id.__class__ is str
                    and record.subject_id == endpoint.subject_id
                    and record.status is ObservationStatus.UNKNOWN
                    and record.observed_at.__class__ is str
                    and record.observed_at == latest.occurred_at
                    and record.evidence.__class__ is BoundedEvidence
                    and record.evidence.canonical_json.__class__ is str
                    and record.evidence == evidence[index + 1]
                    and record.freshness is ObservationFreshness.FRESH
                    and record.graph_id.__class__ is str
                    and record.graph_id == endpoint.graph_id
                    and record.probe_kind is ProbeKind.TRANSPORT
                    and record.probe_outcome is ProbeOutcome.UNKNOWN
                    and record.endpoint_context is endpoint.context
                )
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
) -> tuple[ObservationRecord, ...]:
    valid = (
        outcome.__class__ in (ExecutionEffectOutcome, ObservedEffectOutcome)
        and outcome._admitted
        and attempt.__class__ is EffectAttemptRecord
        and attempt.state.identity == outcome.identity
        and attempt.state.request_fingerprint == outcome.request_fingerprint
        and attempt.state.status is outcome.status
        and attempt.state.outcome_fingerprint == outcome.outcome_fingerprint
        and attempt.state.recovery_decision is None
        and workspace_id.__class__ is str
        and workspace_id
        and not workspace_id[512:]
        and observation_ids.__class__ is tuple
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

    evidence = outcome._bounded_evidence
    occurred_at = attempt.latest_transition_event.occurred_at
    return tuple(
        ObservationRecord(
            observation_id=observation_id,
            workspace_id=workspace_id,
            subject_id=endpoint.subject_id,
            status=ObservationStatus.UNKNOWN,
            observed_at=occurred_at,
            evidence=evidence[index + 1],
            freshness=ObservationFreshness.FRESH,
            graph_id=endpoint.graph_id,
            probe_kind=ProbeKind.TRANSPORT,
            probe_outcome=ProbeOutcome.UNKNOWN,
            endpoint_context=endpoint.context,
        )
        for index, (observation_id, endpoint) in enumerate(
            zip(observation_ids, outcome.endpoint_observations, strict=True)
        )
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
