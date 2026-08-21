from __future__ import annotations

import unittest
from unittest import mock

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
)
from control_plane_kit_operations.effect_attempt_reconciliation import (
    EffectAttemptReconciliationConflict,
    EffectAttemptReconciliationDenied,
    EffectAttemptReconciliationNotFound,
)
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.effect_attempt_intent_store import (
    EffectAttemptIntentStore,
)
from control_plane_kit_operations.secret_providers import (
    SecretProviderRegistrationError,
    SecretUseAuthorizationService,
)
from tests.postgres_effect_attempt_reconciliation_fixture import (
    AUTHORITY_ERROR,
    INVALID_TRUTH_ERROR,
    NOT_FOUND_ERROR,
    PostgresEffectAttemptReconciliationFixture,
    REPLAY_ERROR,
    RecordingObserver,
)


class _FailingFold:
    def __init__(self, error) -> None:
        self.error = error
        self.calls = []

    def execute_observed(self, command):
        self.calls.append(command)
        raise self.error


class PostgresEffectAttemptReconciliationRollbackTests(
    PostgresEffectAttemptReconciliationFixture,
    unittest.TestCase,
):
    def test_expected_initial_read_failures_are_fixed_and_leave_truth_unchanged(self) -> None:
        story = self.observed_story()
        for owner, name, error, expected_type, message in (
            (
                PostgresExecutionStore,
                "get_request_for_update",
                KeyError("request-canary"),
                EffectAttemptReconciliationNotFound,
                NOT_FOUND_ERROR,
            ),
            (
                PostgresExecutionStore,
                "observe_request_lease_for_update",
                KeyError("lease-canary"),
                EffectAttemptReconciliationConflict,
                INVALID_TRUTH_ERROR,
            ),
            (
                EffectAttemptIntentStore,
                "get",
                KeyError("intent-canary"),
                EffectAttemptReconciliationConflict,
                INVALID_TRUTH_ERROR,
            ),
        ):
            with self.subTest(stage=name):
                current, _intent, _record, _authority = self.seed_reconciliation_source(story)
                before = self.complete_reconciliation_snapshot()
                with mock.patch.object(owner, name, side_effect=error):
                    with self.assertRaises(expected_type) as caught:
                        self.reconciliation_service(
                            RecordingObserver(story.value),
                            fold_service=_FailingFold(AssertionError("fold-canary")),
                        ).execute(self.reconciliation_command(current))
                self.assertEqual(str(caught.exception), message)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertEqual(self.complete_reconciliation_snapshot(), before)

    def test_partial_authorization_failure_commits_prefix_but_prevents_observer_and_fold(self) -> None:
        story = self.observed_story()
        current, intent, _record, authority = self.seed_reconciliation_source(
            story,
            remote=True,
        )
        uses = self.required_secret_uses(current, intent, authority)
        self.assertGreaterEqual(len(uses), 2)
        self.admit_secret_uses(uses)
        observer = RecordingObserver(self.observation_for(story, current, intent))
        fold = _FailingFold(AssertionError("partial grant reached fold"))
        original = SecretUseAuthorizationService.authorize_resolution
        calls = 0

        def authorize(service, command):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SecretProviderRegistrationError("partial-grant-canary")
            return original(service, command)

        before_non_advancement = self.non_advancement_snapshot()
        with mock.patch.object(
            SecretUseAuthorizationService,
            "authorize_resolution",
            authorize,
        ):
            with self.assertRaises(EffectAttemptReconciliationDenied) as caught:
                self.reconciliation_service(observer, fold_service=fold).execute(
                    self.reconciliation_command(
                        current,
                        scopes=(
                            PolicyScope.EXECUTION_OPERATE,
                            PolicyScope.SECRET_PROVIDER_USE,
                        ),
                    )
                )
        self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(observer.calls, [])
        self.assertEqual(fold.calls, [])
        self.assertEqual(len(self.authorization_rows()), 1)
        self.assertEqual(self.non_advancement_snapshot(), before_non_advancement)

    def test_observer_fault_is_raw_and_committed_grants_remain_audit_truth(self) -> None:
        story = self.observed_story()
        current, intent, _record, authority = self.seed_reconciliation_source(
            story,
            remote=True,
        )
        uses = self.required_secret_uses(current, intent, authority)
        self.admit_secret_uses(uses)
        error = RuntimeError("raw-observer-rollback-canary")
        observer = self.observer_for(story, current, intent, error=error)
        fold = _FailingFold(AssertionError("observer fault reached fold"))
        before = self.complete_snapshot()
        with self.assertRaises(RuntimeError) as caught:
            self.reconciliation_service(observer, fold_service=fold).execute(
                self.reconciliation_command(
                    current,
                    scopes=(
                        PolicyScope.EXECUTION_OPERATE,
                        PolicyScope.SECRET_PROVIDER_USE,
                    ),
                )
            )
        self.assertIs(caught.exception, error)
        self.assertEqual(fold.calls, [])
        self.assertEqual(self.complete_snapshot(), before)
        self.assertEqual(len(self.authorization_rows()), len(uses))

    def test_guarded_fold_expected_and_raw_failures_do_not_duplicate_persistence(self) -> None:
        story = self.observed_story()
        rows = (
            (
                EffectAttemptFoldDenied("guarded-denied-canary"),
                EffectAttemptReconciliationDenied,
                AUTHORITY_ERROR,
            ),
            (
                EffectAttemptFoldConflict("guarded-conflict-canary"),
                EffectAttemptReconciliationConflict,
                REPLAY_ERROR,
            ),
            (TypeError("raw-fold-type-canary"), TypeError, None),
            (RuntimeError("raw-fold-runtime-canary"), RuntimeError, None),
        )
        for error, expected_type, message in rows:
            with self.subTest(error=type(error).__name__):
                current, intent, _record, authority = self.seed_reconciliation_source(story)
                uses = self.required_secret_uses(current, intent, authority)
                self.admit_secret_uses(uses)
                observer = self.observer_for(story, current, intent)
                fold = _FailingFold(error)
                before = self.complete_snapshot()
                with self.assertRaises(expected_type) as caught:
                    self.reconciliation_service(observer, fold_service=fold).execute(
                        self.reconciliation_command(
                            current,
                            scopes=(
                                PolicyScope.EXECUTION_OPERATE,
                                PolicyScope.SECRET_PROVIDER_USE,
                            ),
                        )
                    )
                if message is None:
                    self.assertIs(caught.exception, error)
                else:
                    self.assertEqual(str(caught.exception), message)
                    self.assertIsNone(caught.exception.__cause__)
                    self.assertIsNone(caught.exception.__context__)
                self.assertEqual(len(fold.calls), 1)
                self.assertEqual(self.complete_snapshot(), before)
                self.assertEqual(len(self.authorization_rows()), len(uses))


if __name__ == "__main__":
    unittest.main()
