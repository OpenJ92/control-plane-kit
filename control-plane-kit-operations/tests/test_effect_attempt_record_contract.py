from __future__ import annotations

import dataclasses
import unittest

from control_plane_kit_core.operations import ActivityEventKind
from control_plane_kit_operations.records import (
    BoundedEvidence,
    OperationsRecordError,
)
from tests.effect_attempt_record_fixture import (
    EffectAttemptRecord,
    EffectAttemptRecordFixture,
    HostileActivityEventRecord,
    HostileBoundedEvidence,
    HostileEffectAttemptState,
    STORIES,
    canonical_state_fingerprint,
)


class EffectAttemptRecordContractTests(
    EffectAttemptRecordFixture,
    unittest.TestCase,
):
    def assert_invalid_record(self, state, original, latest, *canaries: str) -> None:
        with self.assertRaises(OperationsRecordError) as caught:
            EffectAttemptRecord(state, original, latest)
        self.assertEqual(
            str(caught.exception),
            "effect attempt record is invalid",
        )
        self.assert_safe_error(caught.exception, *canaries)

    def test_every_state_story_is_valid_in_both_event_phases(self) -> None:
        self.require_language()
        for compensation in (False, True):
            for story in STORIES:
                with self.subTest(compensation=compensation, story=story):
                    record = self.record(story, compensation=compensation)
                    self.assertEqual(record.state, self.state(story))
                    self.assertEqual(
                        record.original_start_event.kind,
                        self.event_kind("started", compensation=compensation),
                    )
                    self.assertEqual(
                        record.latest_transition_event.kind,
                        self.event_kind(story, compensation=compensation),
                    )

    def test_retry_lineage_and_nonchronological_timestamps_are_preserved(self) -> None:
        self.require_language()
        reverse_time = self.record(
            "succeeded",
            attempt=2,
            original_time="2030-01-01T00:00:09.000000Z",
            latest_time="2030-01-01T00:00:01.000000Z",
        )
        equal_time = self.record(
            "failed",
            original_time="2030-01-01T00:00:05.000000Z",
            latest_time="2030-01-01T00:00:05.000000Z",
        )
        self.assertEqual(reverse_time.state.identity.attempt, 2)
        self.assertEqual(reverse_time.state.prior_attempt.attempt, 1)
        self.assertGreater(
            reverse_time.latest_transition_event.ordinal,
            reverse_time.original_start_event.ordinal,
        )
        self.assertEqual(
            equal_time.latest_transition_event.occurred_at,
            equal_time.original_start_event.occurred_at,
        )

    def test_exact_nominal_state_event_and_evidence_types_are_required(self) -> None:
        self.require_language()
        state = self.state()
        original = self.record().original_start_event
        hostile_state = HostileEffectAttemptState(**state.__dict__)
        hostile_event = HostileActivityEventRecord(**original.__dict__)
        hostile_evidence = HostileBoundedEvidence(original.evidence.canonical_json)
        hostile_evidence_event = dataclasses.replace(
            original,
            evidence=hostile_evidence,
        )
        for values in (
            (hostile_state, original, original),
            (state, hostile_event, hostile_event),
            (state, hostile_evidence_event, hostile_evidence_event),
        ):
            with self.subTest(values=tuple(type(value).__name__ for value in values)):
                self.assert_invalid_record(*values)

    def test_latest_event_coordinates_and_commitment_must_match(self) -> None:
        self.require_language()
        valid = self.record("succeeded")
        state = valid.state
        evidence = {
            "absent": BoundedEvidence.from_mapping({}),
            "missing-attempt": BoundedEvidence.from_mapping(
                {
                    "effect_attempt": {
                        "state_fingerprint": canonical_state_fingerprint(state),
                    }
                }
            ),
            "missing-fingerprint": BoundedEvidence.from_mapping(
                {"effect_attempt": {"attempt": 1}}
            ),
            "wrong-attempt": BoundedEvidence.from_mapping(
                {
                    "effect_attempt": {
                        "attempt": 2,
                        "state_fingerprint": canonical_state_fingerprint(state),
                    }
                }
            ),
            "wrong-fingerprint": BoundedEvidence.from_mapping(
                {
                    "effect_attempt": {
                        "attempt": 1,
                        "state_fingerprint": "d" * 64,
                    }
                }
            ),
            "extra": BoundedEvidence.from_mapping(
                {
                    "effect_attempt": {
                        "attempt": 1,
                        "state_fingerprint": canonical_state_fingerprint(state),
                        "secret-canary": "must-not-render",
                    }
                }
            ),
        }
        candidates = (
            dataclasses.replace(
                valid.latest_transition_event,
                run_id="run-foreign-canary",
            ),
            dataclasses.replace(
                valid.latest_transition_event,
                activity_id="activity-foreign-canary",
            ),
            dataclasses.replace(
                valid.latest_transition_event,
                evidence=evidence["absent"],
            ),
            dataclasses.replace(
                valid.latest_transition_event,
                evidence=evidence["missing-attempt"],
            ),
            dataclasses.replace(
                valid.latest_transition_event,
                evidence=evidence["missing-fingerprint"],
            ),
            dataclasses.replace(
                valid.latest_transition_event,
                evidence=evidence["wrong-attempt"],
            ),
            dataclasses.replace(
                valid.latest_transition_event,
                evidence=evidence["wrong-fingerprint"],
            ),
            dataclasses.replace(
                valid.latest_transition_event,
                evidence=evidence["extra"],
            ),
            dataclasses.replace(
                valid.latest_transition_event,
                event_id=valid.original_start_event.event_id,
            ),
        )
        for latest in candidates:
            with self.subTest(latest=latest):
                self.assert_invalid_record(
                    state,
                    valid.original_start_event,
                    latest,
                    "foreign-canary",
                    "secret-canary",
                    "must-not-render",
                )

    def test_original_event_coordinates_kind_and_commitment_must_match(self) -> None:
        self.require_language()
        valid = self.record("recovered-failed")
        started = self.started_state(valid.state)
        evidence = {
            "absent": BoundedEvidence.from_mapping({}),
            "missing-attempt": BoundedEvidence.from_mapping(
                {
                    "effect_attempt": {
                        "state_fingerprint": canonical_state_fingerprint(started),
                    }
                }
            ),
            "missing-fingerprint": BoundedEvidence.from_mapping(
                {"effect_attempt": {"attempt": 1}}
            ),
            "wrong-attempt": BoundedEvidence.from_mapping(
                {
                    "effect_attempt": {
                        "attempt": 2,
                        "state_fingerprint": canonical_state_fingerprint(started),
                    }
                }
            ),
            "wrong-fingerprint": BoundedEvidence.from_mapping(
                {
                    "effect_attempt": {
                        "attempt": 1,
                        "state_fingerprint": canonical_state_fingerprint(valid.state),
                    }
                }
            ),
            "extra": BoundedEvidence.from_mapping(
                {
                    "effect_attempt": {
                        "attempt": 1,
                        "state_fingerprint": canonical_state_fingerprint(started),
                        "secret-canary": "must-not-render",
                    }
                }
            ),
        }
        candidates = (
            dataclasses.replace(
                valid.original_start_event,
                run_id="run-original-canary",
            ),
            dataclasses.replace(
                valid.original_start_event,
                activity_id="activity-original-canary",
            ),
            dataclasses.replace(
                valid.original_start_event,
                evidence=evidence["absent"],
            ),
            dataclasses.replace(
                valid.original_start_event,
                evidence=evidence["missing-attempt"],
            ),
            dataclasses.replace(
                valid.original_start_event,
                evidence=evidence["missing-fingerprint"],
            ),
            dataclasses.replace(
                valid.original_start_event,
                evidence=evidence["wrong-attempt"],
            ),
            dataclasses.replace(
                valid.original_start_event,
                evidence=evidence["wrong-fingerprint"],
            ),
            dataclasses.replace(
                valid.original_start_event,
                evidence=evidence["extra"],
            ),
            dataclasses.replace(
                valid.original_start_event,
                kind=ActivityEventKind.STEP_SUCCEEDED,
            ),
        )
        for original in candidates:
            with self.subTest(original=original):
                self.assert_invalid_record(
                    valid.state,
                    original,
                    valid.latest_transition_event,
                    "original-canary",
                    "secret-canary",
                    "must-not-render",
                )

    def test_phase_and_transition_kind_are_derived_not_caller_selected(self) -> None:
        self.require_language()
        direct = self.record("succeeded")
        recovered = self.record("recovered-succeeded")
        compensation = self.record("failed", compensation=True)
        candidates = (
            (
                direct.state,
                direct.original_start_event,
                dataclasses.replace(
                    direct.latest_transition_event,
                    kind=ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED,
                ),
            ),
            (
                recovered.state,
                recovered.original_start_event,
                dataclasses.replace(
                    recovered.latest_transition_event,
                    kind=ActivityEventKind.STEP_SUCCEEDED,
                ),
            ),
            (
                compensation.state,
                compensation.original_start_event,
                dataclasses.replace(
                    compensation.latest_transition_event,
                    kind=ActivityEventKind.STEP_FAILED,
                ),
            ),
            (
                direct.state,
                dataclasses.replace(
                    direct.original_start_event,
                    kind=ActivityEventKind.STEP_COMPENSATION_STARTED,
                ),
                direct.latest_transition_event,
            ),
        )
        for values in candidates:
            with self.subTest(kinds=(values[1].kind, values[2].kind)):
                self.assert_invalid_record(*values)

    def test_event_ordinal_and_started_identity_laws_are_exact(self) -> None:
        self.require_language()
        settled = self.record("succeeded")
        for ordinal in (3, 2):
            with self.subTest(ordinal=ordinal):
                self.assert_invalid_record(
                    settled.state,
                    settled.original_start_event,
                    dataclasses.replace(
                        settled.latest_transition_event,
                        ordinal=ordinal,
                    ),
                )

        started = self.record()
        for latest in (
            dataclasses.replace(
                started.latest_transition_event,
                event_id="event-latest-canary",
            ),
            dataclasses.replace(started.latest_transition_event, ordinal=4),
            dataclasses.replace(
                started.latest_transition_event,
                occurred_at="2030-01-01T00:00:03.000000Z",
            ),
        ):
            with self.subTest(latest=latest):
                self.assert_invalid_record(
                    started.state,
                    started.original_start_event,
                    latest,
                    "event-latest-canary",
                )


if __name__ == "__main__":
    unittest.main()
