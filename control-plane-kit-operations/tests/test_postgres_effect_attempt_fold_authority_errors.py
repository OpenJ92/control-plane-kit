from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
import unittest
from unittest import mock

from control_plane_kit_core.operations import (
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
)
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    EffectAttemptFoldNotFound,
    ExistingFold,
    NewlyFolded,
)
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.effect_attempt_store import (
    EffectAttemptStore,
)
from control_plane_kit_operations.records import OperationsRecordError
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_fold_fixture import (
    AUTHORITY_ERROR,
    INVALID_TRUTH_ERROR,
    NOT_FOUND_ERROR,
    PostgresEffectAttemptFoldFixture,
    REPLAY_ERROR,
)


class PostgresEffectAttemptFoldAuthorityErrorTests(
    PostgresEffectAttemptFoldFixture,
    unittest.TestCase,
):
    def test_direct_fold_requires_current_authority_equal_to_attempt_fence(self) -> None:
        cases = (
            ("exact", "worker-a", 7, None),
            ("old-after-rotation", "worker-a", 7, EffectAttemptFoldDenied),
            ("current-rotated", "worker-b", 8, EffectAttemptFoldConflict),
        )
        for label, worker_id, generation, category in cases:
            with self.subTest(case=label):
                self.seed_fold_source("succeeded")
                if label != "exact":
                    self.replace_current_claim(worker_id="worker-b", generation=8)
                command = self.fold_command(
                    "succeeded",
                    authority=self.authority(worker_id),
                    fence=self.fence(worker_id, generation),
                )
                before = self.attempt_snapshot()
                if category is None:
                    result = self.fold_service(f"direct-{label}").execute(command)
                    self.assertIsInstance(result, NewlyFolded)
                else:
                    with self.reject_fold_database_observation(
                        f"{label} sampled database time"
                    ):
                        with self.assertRaises(category) as caught:
                            self.fold_service("must-not-allocate").execute(command)
                    self.assert_safe_error(caught.exception, "worker-a", "worker-b")
                    self.assertEqual(
                        str(caught.exception),
                        AUTHORITY_ERROR
                        if category is EffectAttemptFoldDenied
                        else REPLAY_ERROR,
                    )
                    self.assertEqual(self.attempt_snapshot(), before)

    def test_recovery_authority_is_exact_current_and_monotonic(self) -> None:
        cases = (
            ("equal", "worker-a", 7, None),
            ("greater-same-worker", "worker-a", 9, None),
            ("greater-new-worker", "worker-b", 9, None),
            ("equal-new-worker-canary", "worker-b", 7, EffectAttemptFoldConflict),
            ("lower-generation-canary", "worker-a", 6, EffectAttemptFoldConflict),
        )
        for label, worker_id, generation, category in cases:
            with self.subTest(case=label):
                current = self.seed_fold_source("recovered-succeeded")
                if (worker_id, generation) != ("worker-a", 7):
                    self.replace_current_claim(
                        worker_id=worker_id,
                        generation=generation,
                    )
                command = self.fold_command(
                    "recovered-succeeded",
                    authority=self.authority(worker_id),
                    fence=self.fence(worker_id, generation),
                )
                before = self.attempt_snapshot()
                ids = Sequence(f"recovery-{label}")
                if category is None:
                    result = self.fold_service_with_id_factory(ids).execute(command)
                    self.assertIsInstance(result, NewlyFolded)
                    self.assertEqual(result.attempt.state.fence, current.state.fence)
                    self.assertEqual(ids.calls, [f"recovery-{label}"])
                else:
                    with self.reject_fold_database_observation(
                        f"{label} sampled database time"
                    ):
                        with self.assertRaises(category) as caught:
                            self.fold_service_with_id_factory(ids).execute(command)
                    self.assert_safe_error(caught.exception, "canary")
                    self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                    self.assertEqual(ids.calls, [])
                    self.assertEqual(self.attempt_snapshot(), before)

    def test_recovery_replay_survives_later_lawful_rotation_but_not_bad_lineage(
        self,
    ) -> None:
        self.seed_fold_source("recovered-succeeded")
        first = self.fold_service("first-recovery").execute(
            self.fold_command("recovered-succeeded")
        )
        self.assertIsInstance(first, NewlyFolded)
        for label, worker_id, generation, category in (
            ("later-current", "worker-c", 11, None),
            ("equal-foreign-canary", "worker-c", 7, EffectAttemptFoldConflict),
            ("lower-canary", "worker-a", 6, EffectAttemptFoldConflict),
        ):
            with self.subTest(case=label):
                self.replace_current_claim(worker_id=worker_id, generation=generation)
                before = self.attempt_snapshot()
                ids = Sequence("replay-must-not-allocate")
                command = self.fold_command(
                    "recovered-succeeded",
                    authority=self.authority(worker_id),
                    fence=self.fence(worker_id, generation),
                )
                with self.reject_fold_database_observation(
                    "recovery replay sampled database time"
                ):
                    if category is None:
                        result = self.fold_service_with_id_factory(ids).execute(command)
                        self.assertEqual(result, ExistingFold(first.attempt))
                    else:
                        with self.assertRaises(category) as caught:
                            self.fold_service_with_id_factory(ids).execute(command)
                        self.assert_safe_error(caught.exception, "canary")
                        self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.attempt_snapshot(), before)

    def test_expired_but_still_current_claim_may_fold_and_replay(self) -> None:
        for story in ("succeeded", "recovered-succeeded"):
            with self.subTest(story=story):
                self.seed_fold_source(story)
                self.expire_claim()
                first = self.fold_service(f"expired-{story}").execute(
                    self.fold_command(story)
                )
                self.assertIsInstance(first, NewlyFolded)
                before = self.attempt_snapshot()
                with self.reject_fold_database_observation(
                    "expired exact replay sampled database time"
                ):
                    replay = self.fold_service("must-not-allocate").execute(
                        self.fold_command(story)
                    )
                self.assertEqual(replay, ExistingFold(first.attempt))
                self.assertEqual(self.attempt_snapshot(), before)

    def test_missing_request_run_attempt_and_foreign_activity_are_categorical(self) -> None:
        cases = ("request", "run", "attempt", "activity")
        for target in cases:
            with self.subTest(target=target):
                current = self.seed_fold_source("succeeded")
                command = self.fold_command("succeeded")
                if target == "request":
                    command = self.fold_command(
                        "succeeded", request_id="missing-request-canary"
                    )
                elif target == "run":
                    transition = EffectAttemptTransition(
                        EffectAttemptTransitionKind.SUCCEEDED,
                        self.identity(
                            run_id="missing-run-canary",
                            activity_id="start-runtime",
                        ),
                        outcome_fingerprint="b" * 64,
                    )
                    command = self.fold_command("succeeded", transition=transition)
                elif target == "attempt":
                    self.connection.execute(
                        "DELETE FROM cpk_effect_attempts WHERE run_id='run-a' "
                        "AND activity_id='start-runtime' AND attempt=1"
                    )
                else:
                    transition = EffectAttemptTransition(
                        EffectAttemptTransitionKind.SUCCEEDED,
                        self.identity(activity_id="foreign-activity-canary"),
                        outcome_fingerprint="b" * 64,
                    )
                    command = self.fold_command("succeeded", transition=transition)
                before = self.attempt_snapshot()
                ids = Sequence("missing-must-not-allocate")
                with self.reject_fold_database_observation(
                    f"{target} rejection sampled database time"
                ):
                    with self.assertRaises(EffectAttemptFoldNotFound) as caught:
                        self.fold_service_with_id_factory(ids).execute(command)
                self.assert_safe_error(caught.exception, "canary")
                self.assertEqual(str(caught.exception), NOT_FOUND_ERROR)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.attempt_snapshot(), before)
                if target not in {"attempt", "request"}:
                    self.assertEqual(self.current_attempt(), current)

    def test_expected_decoder_failures_are_categorical_and_internal_errors_raw(
        self,
    ) -> None:
        boundaries = (
            (PostgresExecutionStore, "get_request_for_update"),
            (PostgresExecutionStore, "get_run_for_request_for_update"),
            (EffectAttemptStore, "get_for_update"),
            (PostgresExecutionStore, "observe_request_lease_for_update"),
        )
        for owner, method in boundaries:
            for error_type in (ValueError, OperationsRecordError):
                with self.subTest(method=method, error=error_type.__name__):
                    self.seed_fold_source("succeeded")
                    before = self.attempt_snapshot()
                    canary = f"{method}-{error_type.__name__}-canary"
                    ids = Sequence("decoder-must-not-allocate")
                    guard = (
                        nullcontext()
                        if method == "observe_request_lease_for_update"
                        else self.reject_fold_database_observation(
                            "decoder rejection sampled database time"
                        )
                    )
                    with mock.patch.object(owner, method, side_effect=error_type(canary)):
                        with guard:
                            with self.assertRaises(EffectAttemptFoldConflict) as caught:
                                self.fold_service_with_id_factory(ids).execute(
                                    self.fold_command("succeeded")
                                )
                    self.assert_safe_error(caught.exception, canary)
                    self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                    self.assertEqual(ids.calls, [])
                    self.assertEqual(self.attempt_snapshot(), before)

            for error_type in (TypeError, RuntimeError):
                with self.subTest(method=method, raw=error_type.__name__):
                    self.seed_fold_source("succeeded")
                    error = error_type(f"{method}-raw-canary")
                    with mock.patch.object(owner, method, side_effect=error):
                        with self.assertRaises(error_type) as caught:
                            self.fold_service("must-not-allocate").execute(
                                self.fold_command("succeeded")
                            )
                    self.assertIs(caught.exception, error)

    def test_observation_must_match_locked_request_and_claim_must_exist(self) -> None:
        self.seed_fold_source("succeeded")
        before = self.attempt_snapshot()
        ids = Sequence("changed-observation-must-not-allocate")
        with mock.patch.object(
            PostgresExecutionStore,
            "observe_request_lease_for_update",
            self.changed_observation("changed-request-canary"),
        ):
            with self.assertRaises(EffectAttemptFoldConflict) as caught:
                self.fold_service_with_id_factory(ids).execute(
                    self.fold_command("succeeded")
                )
        self.assert_safe_error(caught.exception, "changed-request-canary")
        self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)

        self.reset_start_truth()
        self.persisted_started()
        self.connection.execute(
            "UPDATE cpk_execution_requests SET status='queued', "
            "claim_worker_id=NULL, claim_generation=NULL, claimed_at=NULL, "
            "lease_expires_at=NULL WHERE request_id='request-a'"
        )
        before = self.attempt_snapshot()
        with self.reject_fold_database_observation(
            "claimless request sampled database time"
        ):
            with self.assertRaises(EffectAttemptFoldDenied) as caught:
                self.fold_service("must-not-allocate").execute(
                    self.fold_command("succeeded")
                )
        self.assert_safe_error(caught.exception)
        self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
        self.assertEqual(self.attempt_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
