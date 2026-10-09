"""Durable event projection retains explicit read lineage; old events stay old."""
import unittest

from control_plane_kit_core.operations import ActivityEventKind
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.records import ActivityEventRecord, BoundedEvidence


class NativeActivityEventProjectionTests(unittest.TestCase):
    def event(self, kind, attempt=None):
        evidence = {} if attempt is None else {"effect_attempt": {
            "attempt": attempt, "state_fingerprint": "a" * 64}}
        return ActivityEventRecord("event-a", "run-a", 1, kind, "2030-01-01T00:00:00Z",
            activity_id="connection", evidence=BoundedEvidence.from_mapping(evidence))

    def test_original_attempt_number_survives_projection_and_legacy_events_do_not_gain_one(self):
        typed, = activity_journal_events((self.event(ActivityEventKind.STEP_STARTED, 1),))
        self.assertEqual(typed.attempt, 1, "durable attempt lineage was dropped")
        legacy, = activity_journal_events((self.event(ActivityEventKind.STEP_STARTED),))
        self.assertIsNone(legacy.attempt)

    def test_explicit_observation_events_require_exact_bounded_attempt_evidence(self):
        for name, number in (("STEP_OBSERVATION_NOT_READY", 1), ("STEP_OBSERVATION_RESTARTED", 2)):
            kind = getattr(ActivityEventKind, name, None)
            self.assertIsNotNone(kind, "durable observation event kind is missing")
            values = activity_journal_events((self.event(kind, number),))
            self.assertEqual(len(values), 1, "durable observation event was dropped")
            projected, = values
            self.assertEqual((projected.kind.value, projected.attempt), (kind.value, number))
            for invalid in (None, True, 0, 2_147_483_648):
                with self.subTest(name=name, invalid=invalid), self.assertRaises(ValueError):
                    activity_journal_events((self.event(kind, invalid),))

    def test_malformed_ordinary_attempt_commitment_cannot_become_legacy_history(self):
        for invalid in (True, 0, 2_147_483_648):
            with self.subTest(attempt=invalid), self.assertRaises(ValueError):
                activity_journal_events((self.event(ActivityEventKind.STEP_STARTED, invalid),))
