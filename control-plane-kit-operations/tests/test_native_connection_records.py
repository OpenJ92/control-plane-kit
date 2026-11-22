"""Native completion is immutable attempt evidence, never mutation compensation.

These new laws cover record representation only. Transactional admission,
database-time classification and native-operation ownership have separate owners.
"""
from dataclasses import replace
import unittest

from control_plane_kit_core.operations import (
    ActivityEventKind, EffectAttemptFence, EffectAttemptIdentity,
    EffectAttemptState, EffectAttemptStatus, RunId,
)
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptRecord, effect_attempt_state_fingerprint,
)
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord, NativeConnectionEffectOutcome,
    NativeConnectionObservation, NativeConnectionOutcome,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord, BoundedEvidence, OperationsRecordError,
)


class NativeConnectionRecordTests(unittest.TestCase):
    def values(self, *, number=1, connected=False):
        identity = EffectAttemptIdentity(RunId("run-a"), "connection", number)
        prior = None if number == 1 else replace(identity, attempt=number - 1)
        observation = NativeConnectionObservation(
            NativeConnectionOutcome.CONNECTED if connected else NativeConnectionOutcome.DISCONNECTED,
            f"native-start-{number}", "connection", "c" * 64,
            "2030-01-01T00:00:00Z", "2030-01-01T00:00:01Z",
            1 if connected else 0, "11111111-1111-4111-8111-111111111111",
        )
        outcome = NativeConnectionEffectOutcome.from_observation(identity=identity,
            request_fingerprint="a" * 64, observation=observation,
            accepted_at="2030-01-01T00:00:02Z")
        state = EffectAttemptState(identity, "a" * 64, EffectAttemptFence("worker-a", 7),
            EffectAttemptStatus.STARTED, prior_attempt=prior)

        def event(identifier, ordinal, kind, value):
            return ActivityEventRecord(identifier, "run-a", ordinal, kind,
                outcome.accepted_at, activity_id="connection",
                evidence=BoundedEvidence.from_mapping({"effect_attempt": {
                    "attempt": number, "state_fingerprint": effect_attempt_state_fingerprint(value)}}))

        start = event(observation.effect_id, number * 2,
            ActivityEventKind.STEP_STARTED if number == 1 else ActivityEventKind.STEP_OBSERVATION_RESTARTED,
            state)
        complete = replace(state, status=outcome.status, outcome_fingerprint=outcome.outcome_fingerprint)
        end = event(f"native-end-{number}", number * 2 + 1,
            ActivityEventKind.STEP_SUCCEEDED if connected else ActivityEventKind.STEP_OBSERVATION_NOT_READY,
            complete)
        return state, start, complete, end, outcome

    def admitted(self, *args):
        try:
            return EffectAttemptRecord(*args)
        except OperationsRecordError:
            self.fail("native attempt event commitments are not represented")

    def test_completed_not_ready_and_explicit_next_read_keep_distinct_exact_commitments(self):
        for number in (1, 2):
            for connected in (False, True):
                with self.subTest(number=number, connected=connected):
                    state, start, complete, end, outcome = self.values(number=number, connected=connected)
                    started = self.admitted(state, start, start)
                    attempt = self.admitted(complete, start, end)
                    self.assertEqual(attempt.original_start_event, started.original_start_event)
                    try:
                        retained = EffectAttemptOutcomeRecord("workspace-a", outcome, attempt, ())
                    except OperationsRecordError:
                        self.fail("native outcome cannot retain its immutable acceptance alongside the attempt")
                    self.assertEqual(replace(retained), retained)
                    self.assertIsNone(end.failure)
                    self.assertEqual(retained.outcome.accepted_at, "2030-01-01T00:00:02Z")
                    self.assertEqual(retained.outcome.observation, outcome.observation)

    def test_native_restart_cannot_claim_first_attempt_or_compensation(self):
        state, start, complete, end, outcome = self.values()
        for invalid in (
            replace(start, kind=ActivityEventKind.STEP_OBSERVATION_RESTARTED),
            replace(start, kind=ActivityEventKind.STEP_COMPENSATION_STARTED),
        ):
            with self.subTest(kind=invalid.kind), self.assertRaises(OperationsRecordError):
                EffectAttemptRecord(complete, invalid, end)
        # A correlated completion cannot substitute another sample's start ID.
        attempt = self.admitted(complete, start, end)
        foreign = replace(outcome, observation=replace(outcome.observation, effect_id="other-start"))
        with self.assertRaises(OperationsRecordError):
            EffectAttemptOutcomeRecord("workspace-a", foreign, attempt, ())
