from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import importlib
import json

import rfc8785

from control_plane_kit_core import (
    FailureCategory,
    RuntimeEffectFailure,
    RuntimeEffectObservationEvidence,
    RuntimeEffectObservationFailure,
    RuntimeEffectObservedAbsent,
    RuntimeEffectObservedConflict,
    RuntimeEffectObservedFailed,
    RuntimeEffectObservedIndeterminate,
    RuntimeEffectObservedSucceeded,
    RuntimeEffectObserverUnsupported,
    RuntimeEffectResult,
    runtime_effect_observation_fingerprint,
    runtime_effect_result_fingerprint,
)
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
    ProbeKind,
    ProbeOutcome,
    RuntimeEndpointObservation,
)
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.operations import (
    ActivityEventKind,
    EffectAttemptFence,
    EffectAttemptIdentity,
    EffectAttemptState,
    EffectAttemptStatus,
    EffectAttemptTransitionKind,
    EffectRecoveryDecision,
    EffectRecoveryResolution,
    RunId,
)
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptRecord,
    effect_attempt_state_fingerprint,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    FailureEvidence,
    ObservationFreshness,
    ObservationRecord,
    ObservationStatus,
    OperationsRecordError,
)

from effect_attempt_record_fixture import (
    EffectAttemptRecordFixture,
    REQUEST_FINGERPRINT,
)
from effect_attempt_intent_fixture import EffectAttemptIntentFixture


MODULE_NAME = "control_plane_kit_operations.effect_outcome_evidence"
WORKSPACE_ID = "workspace-a"
OUTCOME_MAX_BYTES = 8_192
ENDPOINT_TEXT_MAX = 512
BRIDGE_EVIDENCE_MAX_BYTES = 4_096
_UNSET = object()


def _load_language(import_module=importlib.import_module):
    try:
        return import_module(MODULE_NAME)
    except ModuleNotFoundError as error:
        if error.name != MODULE_NAME:
            raise
        return None


language = _load_language()
EffectOutcomeProfile = getattr(language, "EffectOutcomeProfile", None)
ExecutionEffectOutcome = getattr(language, "ExecutionEffectOutcome", None)
ObservedEffectOutcome = getattr(language, "ObservedEffectOutcome", None)
EffectAttemptOutcomeRecord = getattr(language, "EffectAttemptOutcomeRecord", None)
EffectAttemptOutcome = getattr(language, "EffectAttemptOutcome", None)
effect_outcome_transition = getattr(language, "effect_outcome_transition", None)
effect_outcome_failure = getattr(language, "effect_outcome_failure", None)
effect_outcome_observation_records = getattr(
    language,
    "effect_outcome_observation_records",
    None,
)


FAILURE_ROWS = {
    "execution-failed": (
        FailureCategory.TERMINAL,
        "runtime.effect-failed",
        "runtime effect reported failure",
    ),
    "execution-unsupported": (
        FailureCategory.OPERATOR_REVIEW,
        "runtime.effect-unsupported",
        "runtime effect is unsupported",
    ),
    "execution-uncertain": (
        FailureCategory.UNCERTAIN,
        "runtime.effect-uncertain",
        "runtime effect outcome is uncertain",
    ),
    "observed-failed": (
        FailureCategory.TERMINAL,
        "runtime.effect-observed-failed",
        "runtime observer confirmed failure",
    ),
    "observed-absent": (
        FailureCategory.UNCERTAIN,
        "runtime.effect-observed-absent",
        "runtime observer found no matching effect",
    ),
    "observed-conflict": (
        FailureCategory.UNCERTAIN,
        "runtime.effect-observed-conflict",
        "runtime observer found conflicting effect truth",
    ),
    "observed-indeterminate": (
        FailureCategory.UNCERTAIN,
        "runtime.effect-observed-indeterminate",
        "runtime observer could not determine effect truth",
    ),
    "observer-unsupported": (
        FailureCategory.OPERATOR_REVIEW,
        "runtime.effect-observer-unsupported",
        "runtime observer does not support this effect",
    ),
}


@dataclass(frozen=True)
class OutcomeStory:
    name: str
    profile: str
    value: object
    status: EffectAttemptStatus
    transition: EffectAttemptTransitionKind
    failure_row: str | None
    compensation: bool
    attempt: EffectAttemptRecord

    @property
    def fingerprint(self) -> str:
        if self.profile == "execution-result":
            return runtime_effect_result_fingerprint(self.value)
        return runtime_effect_observation_fingerprint(self.value)

    @property
    def endpoint_observations(self) -> tuple[RuntimeEndpointObservation, ...]:
        return self.value.observations


class HostileEffectAttemptRecord(EffectAttemptRecord):
    pass


class HostileEffectAttemptState(EffectAttemptState):
    pass


class HostileActivityEventRecord(ActivityEventRecord):
    pass


class HostileBoundedEvidence(BoundedEvidence):
    pass


class HostileFailureEvidence(FailureEvidence):
    pass


class HostileStr(str):
    pass


class HostileInt(int):
    pass


def forge_exact(cls, **values):
    forged = object.__new__(cls)
    for name, value in values.items():
        object.__setattr__(forged, name, value)
    return forged


def canonical_fingerprint(domain: str, descriptor: dict[str, object]) -> str:
    document = json.dumps(
        {"domain": domain, "value": descriptor},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(document).hexdigest()


class EffectOutcomeEvidenceFixture(EffectAttemptRecordFixture):
    maxDiff = None

    def require_outcome_language(self) -> None:
        required = {
            "EffectOutcomeProfile": EffectOutcomeProfile,
            "ExecutionEffectOutcome": ExecutionEffectOutcome,
            "ObservedEffectOutcome": ObservedEffectOutcome,
            "EffectAttemptOutcomeRecord": EffectAttemptOutcomeRecord,
            "EffectAttemptOutcome": EffectAttemptOutcome,
            "effect_outcome_transition": effect_outcome_transition,
            "effect_outcome_failure": effect_outcome_failure,
            "effect_outcome_observation_records": effect_outcome_observation_records,
        }
        self.assertEqual(
            [name for name, value in required.items() if value is None],
            [],
            "effect-outcome evidence language is missing",
        )

    def endpoint(self, suffix: str = "a") -> RuntimeEndpointObservation:
        return RuntimeEndpointObservation(
            subject_id=f"subject-{suffix}",
            socket_name=f"socket-{suffix}",
            graph_id=f"graph-{suffix}",
            protocol=Protocol.HTTP,
            context=EndpointContext.RUNTIME_PRIVATE,
            address=LiteralEndpointMaterial(f"http://service-{suffix}:8080"),
        )

    def endpoint_for_bridge_size(self, target: int) -> RuntimeEndpointObservation:
        marker = "\U0001f4a1"
        for marker_count in range(ENDPOINT_TEXT_MAX + 1):
            base = marker * marker_count
            endpoint = self.endpoint("bridge")
            raw = endpoint.descriptor()
            raw["subject_id"] = base
            size = len(
                json.dumps(
                    {"runtime_endpoint": raw},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            remaining = target - size
            if 0 <= remaining <= ENDPOINT_TEXT_MAX - marker_count:
                candidate = base + "s" * remaining
                raw["subject_id"] = candidate
                measured = len(
                    json.dumps(
                        {"runtime_endpoint": raw},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                if measured == target:
                    if target <= BRIDGE_EVIDENCE_MAX_BYTES:
                        return RuntimeEndpointObservation(
                            candidate,
                            endpoint.socket_name,
                            endpoint.graph_id,
                            endpoint.protocol,
                            endpoint.context,
                            endpoint.address,
                        )
                    return forge_exact(
                        RuntimeEndpointObservation,
                        subject_id=candidate,
                        socket_name=endpoint.socket_name,
                        graph_id=endpoint.graph_id,
                        protocol=endpoint.protocol,
                        context=endpoint.context,
                        address=endpoint.address,
                    )
        raise AssertionError(f"cannot construct {target}-byte endpoint evidence")

    def live_result_for_size(self, target: int) -> RuntimeEffectResult:
        observations = (self.endpoint("z"), self.endpoint("a"))
        for full_fields in range(32):
            evidence = {
                f"padding-{index:02d}": "x" * ENDPOINT_TEXT_MAX
                for index in range(full_fields)
            }
            evidence["tail"] = ""
            seed = RuntimeEffectResult.succeeded(
                "event-start",
                evidence=evidence,
                observations=observations,
            )
            remaining = target - len(rfc8785.dumps(seed.descriptor()))
            if 0 <= remaining <= ENDPOINT_TEXT_MAX:
                evidence["tail"] = "x" * remaining
                candidate = RuntimeEffectResult.succeeded(
                    "event-start",
                    evidence=evidence,
                    observations=observations,
                )
                if len(rfc8785.dumps(candidate.descriptor())) == target:
                    return candidate
        raise AssertionError(f"cannot construct {target}-byte live result")

    def observed_result_for_size(self, target: int):
        def evidence(marker: str, size: int) -> RuntimeEffectObservationEvidence:
            values = {}
            remaining = size
            index = 0
            while remaining:
                chunk = min(remaining, 500)
                values[f"padding-{index}"] = marker * chunk
                remaining -= chunk
                index += 1
            return RuntimeEffectObservationEvidence(values)

        def value(message: str, padding_size: int):
            return RuntimeEffectObservedFailed(
                "event-start",
                REQUEST_FINGERPRINT,
                evidence("x", padding_size),
                RuntimeEffectObservationFailure(
                    "observer.failed",
                    message,
                    evidence("y", 3_500),
                ),
                (self.endpoint("a"), self.endpoint("b")),
            )

        padding_size = 1
        message = "msgmax7"
        for _ in range(4):
            padding_size += target - len(
                rfc8785.dumps(value(message, padding_size).descriptor())
            )
        candidate = value(message, padding_size)
        if len(rfc8785.dumps(candidate.descriptor())) != target:
            raise AssertionError(f"cannot construct {target}-byte observed result")
        return candidate

    def raw_rows(self) -> tuple[tuple[str, str, object, EffectAttemptStatus, EffectAttemptTransitionKind, str | None], ...]:
        endpoint_a = self.endpoint("a")
        endpoint_b = self.endpoint("b")
        runtime_failure = RuntimeEffectFailure(
            "provider-canary",
            "provider message must not enter durable failure evidence",
            {"private": "provider-detail-canary"},
        )
        observed_evidence = RuntimeEffectObservationEvidence(
            {"observer": "bounded-evidence-canary"}
        )
        observed_failure = RuntimeEffectObservationFailure(
            "observer-canary",
            "observer message must not enter durable failure evidence",
            RuntimeEffectObservationEvidence({"private": "observer-detail-canary"}),
        )
        return (
            (
                "execution-succeeded",
                "execution-result",
                RuntimeEffectResult.succeeded(
                    "event-start",
                    evidence={"result": "success-canary"},
                    observations=(endpoint_a, endpoint_b),
                ),
                EffectAttemptStatus.SUCCEEDED,
                EffectAttemptTransitionKind.SUCCEEDED,
                None,
            ),
            (
                "execution-failed",
                "execution-result",
                RuntimeEffectResult.failed("event-start", runtime_failure),
                EffectAttemptStatus.FAILED,
                EffectAttemptTransitionKind.FAILED,
                "execution-failed",
            ),
            (
                "execution-unsupported",
                "execution-result",
                RuntimeEffectResult.unsupported("event-start", runtime_failure),
                EffectAttemptStatus.UNSUPPORTED,
                EffectAttemptTransitionKind.UNSUPPORTED,
                "execution-unsupported",
            ),
            (
                "execution-uncertain",
                "execution-result",
                RuntimeEffectResult.uncertain("event-start", runtime_failure),
                EffectAttemptStatus.UNCERTAIN,
                EffectAttemptTransitionKind.UNCERTAIN,
                "execution-uncertain",
            ),
            (
                "observed-succeeded",
                "provider-observation",
                RuntimeEffectObservedSucceeded(
                    "event-start",
                    REQUEST_FINGERPRINT,
                    observed_evidence,
                    observations=(endpoint_a, endpoint_b),
                ),
                EffectAttemptStatus.SUCCEEDED,
                EffectAttemptTransitionKind.SUCCEEDED,
                None,
            ),
            (
                "observed-failed",
                "provider-observation",
                RuntimeEffectObservedFailed(
                    "event-start",
                    REQUEST_FINGERPRINT,
                    observed_evidence,
                    observed_failure,
                    (endpoint_a,),
                ),
                EffectAttemptStatus.FAILED,
                EffectAttemptTransitionKind.FAILED,
                "observed-failed",
            ),
            (
                "observed-absent",
                "provider-observation",
                RuntimeEffectObservedAbsent(
                    "event-start",
                    REQUEST_FINGERPRINT,
                    observed_evidence,
                ),
                EffectAttemptStatus.UNCERTAIN,
                EffectAttemptTransitionKind.UNCERTAIN,
                "observed-absent",
            ),
            (
                "observed-conflict",
                "provider-observation",
                RuntimeEffectObservedConflict(
                    "event-start",
                    REQUEST_FINGERPRINT,
                    observed_evidence,
                    observed_failure,
                    (endpoint_b, endpoint_a),
                ),
                EffectAttemptStatus.UNCERTAIN,
                EffectAttemptTransitionKind.UNCERTAIN,
                "observed-conflict",
            ),
            (
                "observed-indeterminate",
                "provider-observation",
                RuntimeEffectObservedIndeterminate(
                    "event-start",
                    REQUEST_FINGERPRINT,
                    observed_evidence,
                    observed_failure,
                    (endpoint_a,),
                ),
                EffectAttemptStatus.UNCERTAIN,
                EffectAttemptTransitionKind.UNCERTAIN,
                "observed-indeterminate",
            ),
            (
                "observer-unsupported",
                "provider-observation",
                RuntimeEffectObserverUnsupported(
                    "event-start",
                    REQUEST_FINGERPRINT,
                    observed_evidence,
                    observed_failure,
                ),
                EffectAttemptStatus.UNCERTAIN,
                EffectAttemptTransitionKind.UNCERTAIN,
                "observer-unsupported",
            ),
        )

    def failure_for(self, row: str | None, fingerprint: str) -> FailureEvidence | None:
        if row is None:
            return None
        category, code, message = FAILURE_ROWS[row]
        profile = (
            "execution-result" if row.startswith("execution-") else "provider-observation"
        )
        return FailureEvidence(
            category=category,
            code=code,
            message=message,
            details=BoundedEvidence.from_mapping(
                {
                    "effect_outcome": {
                        "profile": profile,
                        "outcome_fingerprint": fingerprint,
                    }
                }
            ),
        )

    def stories(self) -> tuple[OutcomeStory, ...]:
        stories: list[OutcomeStory] = []
        for compensation in (False, True):
            for name, profile, value, status, transition, failure_row in self.raw_rows():
                fingerprint = (
                    runtime_effect_result_fingerprint(value)
                    if profile == "execution-result"
                    else runtime_effect_observation_fingerprint(value)
                )
                state = EffectAttemptState(
                    identity=self.identity(),
                    request_fingerprint=REQUEST_FINGERPRINT,
                    fence=EffectAttemptFence("worker-a", 7),
                    status=status,
                    outcome_fingerprint=fingerprint,
                )
                original = self.event(
                    self.started_state(state),
                    self.event_kind("started", compensation=compensation),
                    event_id="event-start",
                    ordinal=3,
                    occurred_at="2030-01-01T00:00:01Z",
                )
                latest = self.event(
                    state,
                    self.event_kind(status.value, compensation=compensation),
                    event_id=f"event-{name}",
                    ordinal=7,
                    occurred_at="2030-01-01T00:00:02Z",
                )
                latest = replace(
                    latest,
                    failure=self.failure_for(failure_row, fingerprint),
                )
                stories.append(
                    OutcomeStory(
                        name,
                        profile,
                        value,
                        status,
                        transition,
                        failure_row,
                        compensation,
                        EffectAttemptRecord(state, original, latest),
                    )
                )
        return tuple(stories)

    def direct_attempt_for(
        self,
        story: OutcomeStory,
        *,
        identity: EffectAttemptIdentity | None = None,
        request_fingerprint: str | None = None,
        status: EffectAttemptStatus | None = None,
        outcome_fingerprint: str | None = None,
        original_event_id: str = "event-start",
        latest_event_id: str | None = None,
        original_ordinal: int = 3,
        latest_ordinal: int = 7,
        original_kind: ActivityEventKind | None = None,
        latest_kind: ActivityEventKind | None = None,
        failure: FailureEvidence | None | object = _UNSET,
    ) -> EffectAttemptRecord:
        identity = story.attempt.state.identity if identity is None else identity
        request_fingerprint = (
            runtime_effect_intent_fingerprint(
                EffectAttemptIntentFixture().intent(
                    compensation=story.compensation,
                    run_id=identity.run_id.value,
                    activity_id=identity.activity_id,
                )
            )
            if request_fingerprint is None
            else request_fingerprint
        )
        status = story.status if status is None else status
        outcome_fingerprint = (
            story.fingerprint if outcome_fingerprint is None else outcome_fingerprint
        )
        state = EffectAttemptState(
            identity=identity,
            request_fingerprint=request_fingerprint,
            fence=story.attempt.state.fence,
            status=status,
            outcome_fingerprint=outcome_fingerprint,
        )
        compensation = story.compensation
        original = self.event(
            self.started_state(state),
            original_kind
            or self.event_kind("started", compensation=compensation),
            event_id=original_event_id,
            ordinal=original_ordinal,
            occurred_at="2030-01-01T00:00:01Z",
        )
        if failure is _UNSET:
            row = (
                story.failure_row
                if status is story.status
                else {
                    EffectAttemptStatus.SUCCEEDED: None,
                    EffectAttemptStatus.FAILED: "execution-failed",
                    EffectAttemptStatus.UNSUPPORTED: "execution-unsupported",
                    EffectAttemptStatus.UNCERTAIN: "execution-uncertain",
                }[status]
            )
            failure = self.failure_for(row, outcome_fingerprint)
        latest = self.event(
            state,
            latest_kind or self.event_kind(status.value, compensation=compensation),
            event_id=latest_event_id or f"event-{story.name}",
            ordinal=latest_ordinal,
            occurred_at="2030-01-01T00:00:02Z",
        )
        latest = replace(latest, failure=failure)
        return EffectAttemptRecord(state, original, latest)

    def recovery_attempt_for(self, story: OutcomeStory) -> EffectAttemptRecord:
        decision = EffectRecoveryDecision(
            "decision-a",
            story.attempt.state.identity,
            EffectRecoveryResolution.SUCCEEDED,
            "c" * 64,
            story.fingerprint,
        )
        state = EffectAttemptState(
            identity=story.attempt.state.identity,
            request_fingerprint=story.attempt.state.request_fingerprint,
            fence=story.attempt.state.fence,
            status=EffectAttemptStatus.SUCCEEDED,
            outcome_fingerprint=story.fingerprint,
            recovery_decision=decision,
        )
        original = self.event(
            self.started_state(state),
            self.event_kind("started", compensation=story.compensation),
            event_id="event-start",
            ordinal=3,
            occurred_at="2030-01-01T00:00:01Z",
        )
        latest = self.event(
            state,
            self.event_kind("recovered-succeeded", compensation=story.compensation),
            event_id="event-recovered-succeeded",
            ordinal=7,
            occurred_at="2030-01-01T00:00:02Z",
        )
        return EffectAttemptRecord(state, original, latest)

    def outcome_for(self, story: OutcomeStory):
        self.require_outcome_language()
        if story.profile == "execution-result":
            return ExecutionEffectOutcome(
                story.attempt.state.identity,
                story.attempt.state.request_fingerprint,
                story.value,
            )
        return ObservedEffectOutcome(story.attempt.state.identity, story.value)

    def observation_ids(self, story: OutcomeStory) -> tuple[str, ...]:
        return tuple(
            f"observation-{story.name}-{index}"
            for index, _ in enumerate(story.endpoint_observations, start=1)
        )

    def expected_observation_records(
        self,
        story: OutcomeStory,
    ) -> tuple[ObservationRecord, ...]:
        return tuple(
            ObservationRecord(
                observation_id=observation_id,
                workspace_id=WORKSPACE_ID,
                subject_id=endpoint.subject_id,
                status=ObservationStatus.UNKNOWN,
                observed_at=story.attempt.latest_transition_event.occurred_at,
                evidence=BoundedEvidence.from_mapping(
                    {"runtime_endpoint": endpoint.descriptor()}
                ),
                freshness=ObservationFreshness.FRESH,
                graph_id=endpoint.graph_id,
                probe_kind=ProbeKind.TRANSPORT,
                probe_outcome=ProbeOutcome.UNKNOWN,
                endpoint_context=endpoint.context,
            )
            for observation_id, endpoint in zip(
                self.observation_ids(story),
                story.endpoint_observations,
                strict=True,
            )
        )

    def assert_fixed_error(
        self,
        callable_,
        message: str,
        *canaries: str,
    ) -> None:
        with self.assertRaises(OperationsRecordError) as raised:
            callable_()
        self.assertEqual(str(raised.exception), message)
        self.assert_safe_error(raised.exception, *canaries)


__all__ = [
    "EffectAttemptOutcomeRecord",
    "EffectAttemptOutcome",
    "EffectOutcomeEvidenceFixture",
    "EffectOutcomeProfile",
    "ExecutionEffectOutcome",
    "FAILURE_ROWS",
    "HostileActivityEventRecord",
    "HostileBoundedEvidence",
    "HostileEffectAttemptRecord",
    "HostileEffectAttemptState",
    "HostileFailureEvidence",
    "HostileInt",
    "HostileStr",
    "MODULE_NAME",
    "ObservedEffectOutcome",
    "OutcomeStory",
    "REQUEST_FINGERPRINT",
    "WORKSPACE_ID",
    "effect_outcome_failure",
    "effect_outcome_observation_records",
    "effect_outcome_transition",
    "forge_exact",
    "language",
]
