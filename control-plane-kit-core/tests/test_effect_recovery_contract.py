from __future__ import annotations

import importlib
import unittest

import control_plane_kit_core as core
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
from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.planning.saga import ActivityJournalEventKind


REQUEST_FINGERPRINT = "a" * 64
OUTCOME_FINGERPRINT = "b" * 64
UNCERTAIN_FINGERPRINT = "c" * 64
RECOVERY_FINGERPRINT = "d" * 64
MAX_EFFECT_ATTEMPT = 2_147_483_647
MAX_EFFECT_FENCE_GENERATION = 9_223_372_036_854_775_807
RUN_ID_MODULE = "control_plane_kit_core.operations.run_identity"


def _run_id(value: str):
    try:
        module = importlib.import_module(RUN_ID_MODULE)
    except ModuleNotFoundError as error:
        if error.name != RUN_ID_MODULE:
            raise
        raise AssertionError("missing #1636 RunId") from error
    return module.RunId(value)


class EffectRecoveryContractTests(unittest.TestCase):
    def assert_invalid_contract(self, factory, *canaries: str) -> None:
        with self.assertRaises(InvalidEffectRecoveryContract) as raised:
            factory()
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        rendered = str(raised.exception)
        for canary in canaries:
            self.assertNotIn(canary, rendered)

    def identity(self, attempt: int = 1) -> EffectAttemptIdentity:
        return EffectAttemptIdentity(_run_id("run-1"), "activity-1", attempt)

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
            request_fingerprint=REQUEST_FINGERPRINT,
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
            uncertain_fingerprint=UNCERTAIN_FINGERPRINT,
            evidence_fingerprint=RECOVERY_FINGERPRINT,
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
                self.assertTrue(hasattr(core, name))

    def test_identity_and_fence_are_bounded_and_positive(self) -> None:
        invalid_factories = (
            lambda: EffectAttemptIdentity("", "activity-1", 1),
            lambda: EffectAttemptIdentity(_run_id("run-1"), "", 1),
            lambda: EffectAttemptIdentity(_run_id("run-1"), "activity-1", 0),
            lambda: EffectAttemptIdentity(_run_id("run-1"), "activity-1", True),
            lambda: EffectAttemptFence("", 1),
            lambda: EffectAttemptFence("worker-1", 0),
            lambda: EffectAttemptFence("worker-1", True),
            lambda: EffectAttemptFence("w" * 257, 1),
        )
        for factory in invalid_factories:
            with self.subTest(factory=factory):
                with self.assertRaises(InvalidEffectRecoveryContract):
                    factory()

    def test_identity_and_fence_match_native_postgres_integer_domains(self) -> None:
        class HostileInt(int):
            pass

        prior = self.identity(attempt=MAX_EFFECT_ATTEMPT - 1)
        identity = self.identity(attempt=MAX_EFFECT_ATTEMPT)
        fence = self.fence(generation=MAX_EFFECT_FENCE_GENERATION)
        state = fold_effect_attempt(
            None,
            self.start(identity=identity, prior_attempt=prior),
            fence=fence,
        )

        self.assertEqual(state.identity, identity)
        self.assertEqual(state.fence, fence)
        self.assertEqual(
            EffectAttemptIdentity.from_descriptor(identity.descriptor()),
            identity,
        )
        self.assertEqual(
            EffectAttemptFence.from_descriptor(fence.descriptor()),
            fence,
        )

        invalid_identity_factories = (
            lambda: self.identity(attempt=MAX_EFFECT_ATTEMPT + 1),
            lambda: EffectAttemptIdentity.from_descriptor(
                {
                    "run_id": "run-1",
                    "activity_id": "activity-1",
                    "attempt": MAX_EFFECT_ATTEMPT + 1,
                }
            ),
            lambda: self.identity(attempt=HostileInt(1)),
        )
        invalid_fence_factories = (
            lambda: self.fence(generation=MAX_EFFECT_FENCE_GENERATION + 1),
            lambda: EffectAttemptFence.from_descriptor(
                {
                    "worker_id": "worker-1",
                    "generation": MAX_EFFECT_FENCE_GENERATION + 1,
                }
            ),
            lambda: self.fence(generation=HostileInt(1)),
        )
        for factory in invalid_identity_factories + invalid_fence_factories:
            with self.subTest(factory=factory):
                self.assert_invalid_contract(factory)

    def test_worker_and_recovery_decision_ids_are_postgres_text_safe(self) -> None:
        unicode_identifier = "\U0001f642" * 256
        fence = EffectAttemptFence(unicode_identifier, 1)
        decision = EffectRecoveryDecision(
            decision_id=unicode_identifier,
            attempt_identity=self.identity(),
            resolution=EffectRecoveryResolution.FAILED,
            uncertain_fingerprint=UNCERTAIN_FINGERPRINT,
            evidence_fingerprint=RECOVERY_FINGERPRINT,
        )
        self.assertEqual(EffectAttemptFence.from_descriptor(fence.descriptor()), fence)
        self.assertEqual(
            EffectRecoveryDecision.from_descriptor(decision.descriptor()),
            decision,
        )

        uncertain = fold_effect_attempt(
            self.started_state(),
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.UNCERTAIN,
                identity=self.identity(),
                outcome_fingerprint=UNCERTAIN_FINGERPRINT,
            ),
            fence=self.fence(),
        )
        recovered = fold_effect_attempt(
            uncertain,
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.RECONCILED,
                identity=self.identity(),
                recovery_decision=self.decision(EffectRecoveryResolution.FAILED),
            ),
            fence=self.fence(),
        )

        candidates = (
            ("nul-canary", "worker\x00nul-canary"),
            ("surrogate-canary", "worker-\ud800-surrogate-canary"),
        )
        for canary, candidate in candidates:
            factories = (
                lambda candidate=candidate: EffectAttemptFence(candidate, 1),
                lambda candidate=candidate: EffectAttemptFence.from_descriptor(
                    {"worker_id": candidate, "generation": 1}
                ),
                lambda candidate=candidate: EffectRecoveryDecision(
                    decision_id=candidate,
                    attempt_identity=self.identity(),
                    resolution=EffectRecoveryResolution.FAILED,
                    uncertain_fingerprint=UNCERTAIN_FINGERPRINT,
                    evidence_fingerprint=RECOVERY_FINGERPRINT,
                ),
                lambda candidate=candidate: EffectRecoveryDecision.from_descriptor(
                    {
                        **decision.descriptor(),
                        "decision_id": candidate,
                    }
                ),
                lambda candidate=candidate: EffectAttemptState.from_descriptor(
                    {
                        **self.started_state().descriptor(),
                        "fence": {
                            "worker_id": candidate,
                            "generation": 1,
                        },
                    }
                ),
                lambda candidate=candidate: EffectAttemptState.from_descriptor(
                    {
                        **recovered.descriptor(),
                        "recovery_decision": {
                            **recovered.recovery_decision.descriptor(),
                            "decision_id": candidate,
                        },
                    }
                ),
            )
            for factory in factories:
                with self.subTest(canary=canary, factory=factory):
                    self.assert_invalid_contract(factory, canary)

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
                    prior_attempt=EffectAttemptIdentity(
                        _run_id("other-run"), "activity-1", 1
                    ),
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
                        outcome_fingerprint=OUTCOME_FINGERPRINT,
                    ),
                    fence=self.fence(),
                )
                self.assertEqual(state.status, expected)
                self.assertEqual(state.outcome_fingerprint, OUTCOME_FINGERPRINT)

    def test_effect_attempt_transitions_have_total_phase_event_representations(
        self,
    ) -> None:
        rows = {
            ("forward", "started", None): "step_started",
            ("forward", "succeeded", None): "step_succeeded",
            ("forward", "failed", None): "step_failed",
            ("forward", "unsupported", None): "step_unsupported",
            ("forward", "uncertain", None): "step_uncertain",
            (
                "forward",
                "reconciled",
                "succeeded",
            ): "step_uncertainty_resolved_succeeded",
            (
                "forward",
                "reconciled",
                "failed",
            ): "step_uncertainty_resolved_failed",
            (
                "forward",
                "abandoned",
                "abandoned",
            ): "step_uncertainty_abandoned",
            ("compensation", "started", None): "step_compensation_started",
            ("compensation", "succeeded", None): "step_compensation_succeeded",
            ("compensation", "failed", None): "step_compensation_failed",
            (
                "compensation",
                "unsupported",
                None,
            ): "step_compensation_unsupported",
            ("compensation", "uncertain", None): "step_compensation_uncertain",
            (
                "compensation",
                "reconciled",
                "succeeded",
            ): "step_compensation_uncertainty_resolved_succeeded",
            (
                "compensation",
                "reconciled",
                "failed",
            ): "step_compensation_uncertainty_resolved_failed",
            (
                "compensation",
                "abandoned",
                "abandoned",
            ): "step_compensation_uncertainty_abandoned",
        }
        direct = {
            kind.value
            for kind in EffectAttemptTransitionKind
            if kind
            not in {
                EffectAttemptTransitionKind.RECONCILED,
                EffectAttemptTransitionKind.ABANDONED,
            }
        }
        expected_keys = {
            (phase, transition, None)
            for phase in ("forward", "compensation")
            for transition in direct
        } | {
            (phase, "reconciled", resolution.value)
            for phase in ("forward", "compensation")
            for resolution in EffectRecoveryResolution
            if resolution is not EffectRecoveryResolution.ABANDONED
        } | {
            (phase, "abandoned", EffectRecoveryResolution.ABANDONED.value)
            for phase in ("forward", "compensation")
        }

        self.assertEqual(len(rows), 16)
        self.assertEqual(set(rows), expected_keys)
        for key, value in rows.items():
            with self.subTest(key=key):
                self.assertEqual(ActivityEventKind(value).value, value)
                self.assertEqual(ActivityJournalEventKind(value).value, value)

        for kind, resolution in (
            (
                EffectAttemptTransitionKind.RECONCILED,
                EffectRecoveryResolution.ABANDONED,
            ),
            (
                EffectAttemptTransitionKind.ABANDONED,
                EffectRecoveryResolution.SUCCEEDED,
            ),
            (
                EffectAttemptTransitionKind.ABANDONED,
                EffectRecoveryResolution.FAILED,
            ),
        ):
            with self.subTest(invalid=(kind, resolution)):
                with self.assertRaises(InvalidEffectRecoveryContract):
                    EffectAttemptTransition(
                        kind=kind,
                        identity=self.identity(),
                        recovery_decision=self.decision(resolution),
                    )

    def test_uncertainty_cannot_be_resolved_by_ordinary_result(self) -> None:
        uncertain = fold_effect_attempt(
            self.started_state(),
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.UNCERTAIN,
                identity=self.identity(),
                outcome_fingerprint=UNCERTAIN_FINGERPRINT,
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
                            outcome_fingerprint=OUTCOME_FINGERPRINT,
                        ),
                        fence=self.fence(),
                    )

    def test_uncertainty_requires_explicit_reconciliation_evidence(self) -> None:
        uncertain = fold_effect_attempt(
            self.started_state(),
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.UNCERTAIN,
                identity=self.identity(),
                outcome_fingerprint=UNCERTAIN_FINGERPRINT,
            ),
            fence=self.fence(),
        )
        for resolution, expected in (
            (EffectRecoveryResolution.SUCCEEDED, EffectAttemptStatus.SUCCEEDED),
            (EffectRecoveryResolution.FAILED, EffectAttemptStatus.FAILED),
        ):
            with self.subTest(resolution=resolution):
                decision = self.decision(resolution)
                transition = EffectAttemptTransition(
                    kind=EffectAttemptTransitionKind.RECONCILED,
                    identity=self.identity(),
                    recovery_decision=decision,
                )
                reconciled = fold_effect_attempt(
                    uncertain,
                    transition,
                    fence=self.fence(),
                )
                self.assertEqual(reconciled.status, expected)
                self.assertEqual(reconciled.recovery_decision, decision)
                self.assertEqual(
                    reconciled.recovery_decision.uncertain_fingerprint,
                    UNCERTAIN_FINGERPRINT,
                )
                self.assertEqual(
                    reconciled.outcome_fingerprint,
                    decision.evidence_fingerprint,
                )
                self.assertIs(
                    fold_effect_attempt(
                        reconciled,
                        transition,
                        fence=self.fence(),
                    ),
                    reconciled,
                )
                self.assertEqual(
                    EffectAttemptState.from_descriptor(reconciled.descriptor()),
                    reconciled,
                )

        mismatched = EffectRecoveryDecision(
            decision_id="decision-2",
            attempt_identity=self.identity(),
            resolution=EffectRecoveryResolution.SUCCEEDED,
            uncertain_fingerprint="e" * 64,
            evidence_fingerprint=RECOVERY_FINGERPRINT,
        )
        with self.assertRaises(InvalidEffectRecoveryContract):
            fold_effect_attempt(
                uncertain,
                EffectAttemptTransition(
                    kind=EffectAttemptTransitionKind.RECONCILED,
                    identity=self.identity(),
                    recovery_decision=mismatched,
                ),
                fence=self.fence(),
            )

    def test_abandonment_requires_uncertainty_and_explicit_decision(self) -> None:
        uncertain = fold_effect_attempt(
            self.started_state(),
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.UNCERTAIN,
                identity=self.identity(),
                outcome_fingerprint=UNCERTAIN_FINGERPRINT,
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
            outcome_fingerprint=OUTCOME_FINGERPRINT,
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
            outcome_fingerprint=OUTCOME_FINGERPRINT,
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
                outcome_fingerprint=OUTCOME_FINGERPRINT,
            ),
            fence=self.fence(),
        )
        for transition in (
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.SUCCEEDED,
                identity=self.identity(),
                outcome_fingerprint="e" * 64,
            ),
            EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.FAILED,
                identity=self.identity(),
                outcome_fingerprint=OUTCOME_FINGERPRINT,
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

    def test_transition_shapes_reject_contradictory_material(self) -> None:
        invalid_factories = (
            lambda: EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.STARTED,
                identity=self.identity(),
                request_fingerprint=REQUEST_FINGERPRINT,
                outcome_fingerprint=OUTCOME_FINGERPRINT,
            ),
            lambda: EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.SUCCEEDED,
                identity=self.identity(),
            ),
            lambda: EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.RECONCILED,
                identity=self.identity(),
            ),
            lambda: EffectAttemptTransition(
                kind=EffectAttemptTransitionKind.ABANDONED,
                identity=self.identity(),
                recovery_decision=self.decision(
                    EffectRecoveryResolution.SUCCEEDED
                ),
            ),
        )
        for factory in invalid_factories:
            with self.subTest(factory=factory):
                with self.assertRaises(InvalidEffectRecoveryContract):
                    factory()

    def test_fingerprints_and_decision_ids_are_bounded_public_material(self) -> None:
        for fingerprint in ("raw-secret-token", "x" * 63, "X" * 64, "x" * 257):
            with self.subTest(fingerprint=fingerprint):
                with self.assertRaises(InvalidEffectRecoveryContract):
                    EffectAttemptTransition(
                        kind=EffectAttemptTransitionKind.STARTED,
                        identity=self.identity(),
                        request_fingerprint=fingerprint,
                    )
        with self.assertRaises(InvalidEffectRecoveryContract):
            EffectRecoveryDecision(
                decision_id="d" * 257,
                attempt_identity=self.identity(),
                resolution=EffectRecoveryResolution.SUCCEEDED,
                uncertain_fingerprint=UNCERTAIN_FINGERPRINT,
                evidence_fingerprint=RECOVERY_FINGERPRINT,
            )


if __name__ == "__main__":
    unittest.main()
