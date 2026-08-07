from __future__ import annotations

import unittest

import control_plane_kit_core.operations as operations
from control_plane_kit_core.operations.recovery import (
    EffectAttemptFence,
    EffectAttemptIdentity,
    EffectAttemptState,
    EffectAttemptStatus,
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
    EffectRecoveryDecision,
    EffectRecoveryResolution,
    InvalidEffectRecoveryContract,
    fold_effect_attempt,
)


class EffectRecoveryContractTests(unittest.TestCase):
    def identity(self, attempt: int = 1) -> EffectAttemptIdentity:
        return EffectAttemptIdentity("run-1", "activity-1", attempt)

    def fence(self, generation: int = 1) -> EffectAttemptFence:
        return EffectAttemptFence("worker-1", generation)

    def start(
        self,
        *,
        identity: EffectAttemptIdentity | None = None,
        prior_attempt: EffectAttemptIdentity | None = None,
    ) -> EffectAttemptTransition:
        return EffectAttemptTransition(
            kind=EffectAttemptTransitionKind.STARTED,
            identity=identity or self.identity(),
            request_fingerprint="request-sha256",
            prior_attempt=prior_attempt,
        )

    def started_state(self) -> EffectAttemptState:
        return fold_effect_attempt(None, self.start(), fence=self.fence())

    def decision(
        self,
        resolution: EffectRecoveryResolution,
    ) -> EffectRecoveryDecision:
        return EffectRecoveryDecision(
            decision_id="decision-1",
            attempt_identity=self.identity(),
            resolution=resolution,
            evidence_fingerprint="evidence-sha256",
        )

    def test_public_operational_contract_exports_are_complete(self) -> None:
        for name in (
            "EffectAttemptFence",
            "EffectAttemptIdentity",
            "EffectAttemptState",
            "EffectAttemptStatus",
            "EffectAttemptTransition",
            "EffectAttemptTransitionKind",
            "EffectRecoveryDecision",
            "EffectRecoveryResolution",
            "InvalidEffectRecoveryContract",
            "fold_effect_attempt",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(operations, name))

    def test_identity_and_fence_are_bounded_and_positive(self) -> None:
        invalid_factories = (
            lambda: EffectAttemptIdentity("", "activity-1", 1),
            lambda: EffectAttemptIdentity("run-1", "", 1),
            lambda: EffectAttemptIdentity("run-1", "activity-1", 0),
            lambda: EffectAttemptIdentity("run-1", "activity-1", True),
            lambda: EffectAttemptFence("", 1),
            lambda: EffectAttemptFence("worker-1", 0),
            lambda: EffectAttemptFence("worker-1", True),
            lambda: EffectAttemptFence("w" * 257, 1),
        )
        for factory in invalid_factories:
            with self.subTest(factory=factory):
                with self.assertRaises(InvalidEffectRecoveryContract):
                    factory()

    def test_retry_attempt_preserves_exact_lineage(self) -> None:
        second = self.identity(attempt=2)
        state = fold_effect_attempt(
            None,
            self.start(identity=second, prior_attempt=self.identity()),
            fence=self.fence(),
        )
        self.assertEqual(state.identity, second)
        self.assertEqual(state.prior_attempt, self.identity())

        with self.assertRaises(InvalidEffectRecoveryContract):
            fold_effect_attempt(None, self.start(identity=second), fence=self.fence())
        with self.assertRaises(InvalidEffectRecoveryContract):
            fold_effect_attempt(
                None,
                self.start(
                    identity=second,
                    prior_attempt=EffectAttemptIdentity("other-run", "activity-1", 1),
                ),
                fence=self.fence(),
            )

    def test_started_attempt_folds_each_direct_result(self) -> None:
        cases = (
            (EffectAttemptTransitionKind.SUCCEEDED, EffectAttemptStatus.SUCCEEDED),
            (EffectAttemptTransitionKind.FAILED, EffectAttemptStatus.FAILED),
            (EffectAttemptTransitionKind.UNSUPPORTED, EffectAttemptStatus.UNSUPPORTED),
            (EffectAttemptTransitionKind.UNCERTAIN, EffectAttemptStatus.UNCERTAIN),
        )
        for kind, expected in cases:
            with self.subTest(kind=kind):
                state = fold_effect_attempt(
                    self.started_state(),
                    EffectAttemptTransition(
                        kind=kind,
                        identity=self.identity(),
                        outcome_fingerprint="outcome-sha256",
                    ),
                    fence=self.fence(),
                )
                self.assertEqual(state.status, expected)
                self.assertEqual(state.outcome_fingerprint, "outcome-sha256")

    def test_uncertainty_cannot_be_resolved_by_ordinary_result(self) -> None:
        uncertain = fold_effect_attempt(
            self.started_state(),
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.UNCERTAIN,
                identity=self.identity(),
                outcome_fingerprint="uncertain-sha256",
            ),
            fence=self.fence(),
        )
        for kind in (
            EffectAttemptTransitionKind.SUCCEEDED,
            EffectAttemptTransitionKind.FAILED,
        ):
            with self.subTest(kind=kind):
                with self.assertRaises(InvalidEffectRecoveryContract):
                    fold_effect_attempt(
                        uncertain,
                        EffectAttemptTransition(
                            kind=kind,
                            identity=self.identity(),
                            outcome_fingerprint="late-result-sha256",
                        ),
                        fence=self.fence(),
                    )

    def test_uncertainty_requires_explicit_reconciliation_evidence(self) -> None:
        uncertain = fold_effect_attempt(
            self.started_state(),
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.UNCERTAIN,
                identity=self.identity(),
                outcome_fingerprint="uncertain-sha256",
            ),
            fence=self.fence(),
        )
        for resolution, expected in (
            (EffectRecoveryResolution.SUCCEEDED, EffectAttemptStatus.SUCCEEDED),
            (EffectRecoveryResolution.FAILED, EffectAttemptStatus.FAILED),
        ):
            with self.subTest(resolution=resolution):
                decision = self.decision(resolution)
                reconciled = fold_effect_attempt(
                    uncertain,
                    EffectAttemptTransition(
                        kind=EffectAttemptTransitionKind.RECONCILED,
                        identity=self.identity(),
                        recovery_decision=decision,
                    ),
                    fence=self.fence(),
                )
                self.assertEqual(reconciled.status, expected)
                self.assertEqual(reconciled.recovery_decision, decision)
                self.assertEqual(
                    reconciled.outcome_fingerprint,
                    decision.evidence_fingerprint,
                )

    def test_abandonment_requires_uncertainty_and_explicit_decision(self) -> None:
        uncertain = fold_effect_attempt(
            self.started_state(),
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.UNCERTAIN,
                identity=self.identity(),
                outcome_fingerprint="uncertain-sha256",
            ),
            fence=self.fence(),
        )
        decision = self.decision(EffectRecoveryResolution.ABANDONED)
        abandoned = fold_effect_attempt(
            uncertain,
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.ABANDONED,
                identity=self.identity(),
                recovery_decision=decision,
            ),
            fence=self.fence(),
        )
        self.assertEqual(abandoned.status, EffectAttemptStatus.ABANDONED)
        self.assertEqual(abandoned.recovery_decision, decision)

        with self.assertRaises(InvalidEffectRecoveryContract):
            fold_effect_attempt(
                self.started_state(),
                EffectAttemptTransition(
                    kind=EffectAttemptTransitionKind.ABANDONED,
                    identity=self.identity(),
                    recovery_decision=decision,
                ),
                fence=self.fence(),
            )

    def test_stale_or_foreign_fence_cannot_fold(self) -> None:
        state = self.started_state()
        transition = EffectAttemptTransition(
            kind=EffectAttemptTransitionKind.SUCCEEDED,
            identity=self.identity(),
            outcome_fingerprint="outcome-sha256",
        )
        for fence in (
            EffectAttemptFence("worker-1", 2),
            EffectAttemptFence("worker-2", 1),
        ):
            with self.subTest(fence=fence):
                with self.assertRaises(InvalidEffectRecoveryContract):
                    fold_effect_attempt(state, transition, fence=fence)

    def test_exact_duplicate_terminal_fold_is_idempotent(self) -> None:
        transition = EffectAttemptTransition(
            kind=EffectAttemptTransitionKind.SUCCEEDED,
            identity=self.identity(),
            outcome_fingerprint="outcome-sha256",
        )
        settled = fold_effect_attempt(
            self.started_state(), transition, fence=self.fence()
        )
        self.assertIs(
            fold_effect_attempt(settled, transition, fence=self.fence()),
            settled,
        )

    def test_incompatible_duplicate_terminal_fold_is_rejected(self) -> None:
        settled = fold_effect_attempt(
            self.started_state(),
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.SUCCEEDED,
                identity=self.identity(),
                outcome_fingerprint="outcome-sha256",
            ),
            fence=self.fence(),
        )
        for transition in (
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.SUCCEEDED,
                identity=self.identity(),
                outcome_fingerprint="different-sha256",
            ),
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.FAILED,
                identity=self.identity(),
                outcome_fingerprint="outcome-sha256",
            ),
        ):
            with self.subTest(transition=transition):
                with self.assertRaises(InvalidEffectRecoveryContract):
                    fold_effect_attempt(settled, transition, fence=self.fence())

    def test_descriptors_round_trip_and_reject_extra_material(self) -> None:
        transition = self.start()
        state = self.started_state()
        decision = self.decision(EffectRecoveryResolution.SUCCEEDED)

        self.assertEqual(
            EffectAttemptTransition.from_descriptor(transition.descriptor()),
            transition,
        )
        self.assertEqual(EffectAttemptState.from_descriptor(state.descriptor()), state)
        self.assertEqual(
            EffectRecoveryDecision.from_descriptor(decision.descriptor()),
            decision,
        )

        descriptor = state.descriptor()
        descriptor["secret"] = "forbidden"
        with self.assertRaises(InvalidEffectRecoveryContract):
            EffectAttemptState.from_descriptor(descriptor)

    def test_fingerprints_and_decision_ids_are_bounded_public_material(self) -> None:
        with self.assertRaises(InvalidEffectRecoveryContract):
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.STARTED,
                identity=self.identity(),
                request_fingerprint="x" * 257,
            )
        with self.assertRaises(InvalidEffectRecoveryContract):
            EffectRecoveryDecision(
                decision_id="d" * 257,
                attempt_identity=self.identity(),
                resolution=EffectRecoveryResolution.SUCCEEDED,
                evidence_fingerprint="evidence-sha256",
            )


if __name__ == "__main__":
    unittest.main()
