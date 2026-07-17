import unittest

from control_plane_kit.execution import (
    AcceptUncompensatedFailure,
    ActivityRunStatus,
    BeginCompensation,
    ConfirmEffectFailed,
    ConfirmEffectSucceeded,
    MAX_EVIDENCE_TEXT,
    RecoveryAuthority,
    RecoveryAuthorizationDenied,
    RecoveryContext,
    RecoveryDecisionRecord,
    RecoveryDecisionRejected,
    RecoveryScope,
    RemainPaused,
    ResumeSameIntent,
    RetryAsNewRun,
    authorize_recovery_decision,
    validate_recovery_decision,
)


class RecoveryDecisionTests(unittest.TestCase):
    def test_each_closed_decision_has_an_explicit_authority(self):
        cases = (
            (ConfirmEffectSucceeded("step-a"), RecoveryScope.RESOLVE_UNCERTAINTY),
            (ConfirmEffectFailed("step-a"), RecoveryScope.RESOLVE_UNCERTAINTY),
            (ResumeSameIntent(), RecoveryScope.OPERATE),
            (RetryAsNewRun(), RecoveryScope.OPERATE),
            (BeginCompensation(), RecoveryScope.COMPENSATE),
            (AcceptUncompensatedFailure(), RecoveryScope.ACCEPT_LOSS),
            (RemainPaused(), RecoveryScope.OPERATE),
        )

        for decision, scope in cases:
            with self.subTest(decision=type(decision).__name__):
                authorize_recovery_decision(
                    decision,
                    RecoveryAuthority("operator", "grant-a", (scope,)),
                )
                with self.assertRaises(RecoveryAuthorizationDenied):
                    authorize_recovery_decision(
                        decision,
                        RecoveryAuthority("operator", "grant-b", ()),
                    )

    def test_uncertainty_must_be_resolved_before_other_recovery(self):
        context = RecoveryContext(
            ActivityRunStatus.PAUSED,
            uncertain_activity_ids=frozenset({"step-a"}),
        )

        validate_recovery_decision(ConfirmEffectSucceeded("step-a"), context)
        validate_recovery_decision(ConfirmEffectFailed("step-a"), context)
        with self.assertRaisesRegex(RecoveryDecisionRejected, "before resume"):
            validate_recovery_decision(ResumeSameIntent(), context)
        with self.assertRaisesRegex(RecoveryDecisionRejected, "no unresolved"):
            validate_recovery_decision(ConfirmEffectSucceeded("step-b"), context)

    def test_failed_attempt_choices_are_closed_and_evidence_guarded(self):
        failed = RecoveryContext(
            ActivityRunStatus.FAILED,
            compensation_available=True,
        )
        for decision in (
            RetryAsNewRun(),
            BeginCompensation(),
            AcceptUncompensatedFailure(),
            RemainPaused(),
        ):
            with self.subTest(decision=type(decision).__name__):
                validate_recovery_decision(decision, failed)

        with self.assertRaisesRegex(RecoveryDecisionRejected, "no available"):
            validate_recovery_decision(
                BeginCompensation(),
                RecoveryContext(ActivityRunStatus.FAILED),
            )

    def test_changed_intent_requires_a_fresh_plan(self):
        changed = RecoveryContext(
            ActivityRunStatus.FAILED,
            compensation_available=True,
            intent_matches_admitted_plan=False,
        )

        validate_recovery_decision(RemainPaused(), changed)
        for decision in (
            RetryAsNewRun(),
            BeginCompensation(),
            AcceptUncompensatedFailure(),
        ):
            with self.subTest(decision=type(decision).__name__):
                with self.assertRaisesRegex(RecoveryDecisionRejected, "fresh graph plan"):
                    validate_recovery_decision(decision, changed)

    def test_decision_record_requires_attribution_and_reason(self):
        record = RecoveryDecisionRecord(
            "decision-a",
            RemainPaused(),
            RecoveryAuthority("operator", "grant-a", (RecoveryScope.OPERATE,)),
            "Waiting for independent provider evidence.",
        )
        self.assertEqual(record.decision_id, "decision-a")

        with self.assertRaisesRegex(ValueError, "reason"):
            RecoveryDecisionRecord(
                "decision-b",
                RemainPaused(),
                record.authority,
                "",
            )
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            RecoveryDecisionRecord(
                "decision-c",
                RemainPaused(),
                record.authority,
                "x" * (MAX_EVIDENCE_TEXT + 1),
            )

    def test_open_strings_do_not_enter_recovery_scopes_or_context(self):
        with self.assertRaisesRegex(TypeError, "RecoveryScope"):
            RecoveryAuthority(
                "operator",
                "grant-a",
                ("recovery:operate",),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "ActivityRunStatus"):
            RecoveryContext("failed")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
