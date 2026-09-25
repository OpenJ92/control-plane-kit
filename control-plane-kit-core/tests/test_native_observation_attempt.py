"""The completed native not-ready disposition preserves direct attempt truth."""

import unittest

from control_plane_kit_core.operations import (
    EffectAttemptFence, EffectAttemptIdentity, EffectAttemptState,
    EffectAttemptTransition, EffectAttemptTransitionKind, RunId, fold_effect_attempt,
)


class NativeObservationAttemptTests(unittest.TestCase):
    def test_not_ready_is_an_immutable_terminal_attempt_with_exact_replay(self):
        identity = EffectAttemptIdentity(RunId("run-a"), "connection", 1)
        fence = EffectAttemptFence("worker-a", 7)
        started = fold_effect_attempt(None, EffectAttemptTransition(
            EffectAttemptTransitionKind.STARTED, identity, request_fingerprint="a" * 64,
        ), fence=fence)
        kind = getattr(EffectAttemptTransitionKind, "NOT_READY", None)
        self.assertIsNotNone(kind, "completed native not-ready disposition is missing")
        transition = EffectAttemptTransition(kind, identity, outcome_fingerprint="b" * 64)
        completed = fold_effect_attempt(started, transition, fence=fence)
        self.assertEqual(completed.status.value, "not_ready")
        self.assertEqual(completed.outcome_fingerprint, "b" * 64)
        self.assertEqual(EffectAttemptState.from_descriptor(completed.descriptor()), completed)
        self.assertEqual(fold_effect_attempt(completed, transition, fence=fence), completed)
        with self.assertRaises(ValueError):
            fold_effect_attempt(completed, EffectAttemptTransition(kind, identity,
                outcome_fingerprint="c" * 64), fence=fence)
        with self.assertRaises(ValueError):
            fold_effect_attempt(completed, EffectAttemptTransition(
                EffectAttemptTransitionKind.SUCCEEDED, identity, outcome_fingerprint="b" * 64), fence=fence)
        uncertain = fold_effect_attempt(started, EffectAttemptTransition(
            EffectAttemptTransitionKind.UNCERTAIN, identity, outcome_fingerprint="b" * 64), fence=fence)
        with self.assertRaises(ValueError):
            fold_effect_attempt(uncertain, transition, fence=fence)
        self.assertEqual(started.status.value, "started")
        self.assertIsNone(started.outcome_fingerprint)
