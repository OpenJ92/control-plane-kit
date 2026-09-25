"""Original passive evidence and the immutable decision at durable acceptance."""

import unittest
from dataclasses import replace

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_operations import effect_outcome_evidence as outcomes


class NativeConnectionOutcomeTests(unittest.TestCase):
    def api(self):
        names = ("NativeConnectionObservation", "NativeConnectionOutcome", "NativeConnectionEffectOutcome")
        values = tuple(getattr(outcomes, name, None) for name in names)
        for name, value in zip(names, values):
            self.assertIsNotNone(value, f"native acceptance evidence is missing: {name}")
        return values

    def observation(self, outcome="connected", *, sample_end="2030-01-01T00:00:01Z"):
        observation_type, kind, _ = self.api()
        return observation_type(outcome=kind(outcome), effect_id="native-start-1",
            activity_id="connection", container_id="c" * 64,
            sample_start="2030-01-01T00:00:00Z", sample_end=sample_end,
            ready_connections=1 if outcome == "connected" else 0,
            connector_id="11111111-1111-4111-8111-111111111111")

    def accept(self, observation, accepted_at):
        _, _, outcome_type = self.api()
        return outcome_type.from_observation(
            identity=EffectAttemptIdentity(RunId("run-a"), "connection", 1),
            request_fingerprint="a" * 64, observation=observation, accepted_at=accepted_at,
        )

    def test_stale_connected_preserves_original_evidence_and_acceptance_time(self):
        observation = self.observation()
        accepted = self.accept(observation, "2030-01-01T00:00:12Z")
        self.assertEqual(accepted.status.value, "not_ready")
        self.assertEqual(accepted.acceptance_reason, "stale-at-acceptance")
        self.assertEqual(accepted.accepted_at, "2030-01-01T00:00:12Z")
        self.assertEqual(accepted.observation, observation)
        self.assertEqual(accepted.observation.outcome.value, "connected")
        reconstructed = replace(accepted)
        self.assertEqual(reconstructed, accepted)
        self.assertEqual(reconstructed.outcome_fingerprint, accepted.outcome_fingerprint)
        self.assertNotEqual(self.accept(observation, "2030-01-01T00:00:13Z").outcome_fingerprint,
            accepted.outcome_fingerprint)

    def test_native_age_boundary_does_not_truncate_nanoseconds_into_success(self):
        exact = self.accept(self.observation(sample_end="2030-01-01T00:00:01Z"),
            "2030-01-01T00:00:11Z")
        stale = self.accept(self.observation(sample_end="2030-01-01T00:00:00.999999999Z"),
            "2030-01-01T00:00:11Z")
        self.assertEqual(exact.status.value, "succeeded")
        self.assertEqual(stale.status.value, "not_ready")
        self.assertEqual(stale.acceptance_reason, "stale-at-acceptance")

    def test_returned_unknown_and_disconnected_are_completed_not_ready(self):
        observation_type, kind, _ = self.api()
        unknown = observation_type(outcome=kind.UNKNOWN, effect_id="native-start-1", activity_id="connection")
        accepted = self.accept(unknown, "2030-01-01T00:00:05Z")
        self.assertEqual(accepted.status.value, "not_ready")
        self.assertEqual(accepted.acceptance_reason, "unknown")
        self.assertEqual(accepted.observation, unknown)
        disconnected = self.accept(self.observation("disconnected"), "2030-01-01T00:00:05Z")
        self.assertEqual(disconnected.status.value, "not_ready")
        self.assertEqual(disconnected.acceptance_reason, "disconnected")
        # Raw selector refusal has no observation correlation. It is mapped by
        # the effect boundary to explicit refusal, never a fabricated UNKNOWN.
        with self.assertRaises(ValueError):
            kind("refused")

    def test_acceptance_cannot_relabel_stale_or_foreign_evidence(self):
        observation = self.observation()
        stale = self.accept(observation, "2030-01-01T00:00:12Z")
        with self.assertRaises(ValueError):
            replace(stale, acceptance_reason="connected")
        with self.assertRaises(ValueError):
            self.accept(replace(observation, activity_id="foreign"), "2030-01-01T00:00:02Z")

    def test_future_or_reversed_samples_and_incomplete_connected_evidence_refuse(self):
        observation = self.observation()
        with self.assertRaises(ValueError):
            self.accept(observation, "2030-01-01T00:00:00Z")
        for changes in (
            {"sample_start": "2030-01-01T00:00:02Z"}, {"sample_end": None},
            {"container_id": None}, {"ready_connections": 0}, {"connector_id": None},
            {"effect_id": None}, {"activity_id": None},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.accept(replace(observation, **changes), "2030-01-01T00:00:05Z")
