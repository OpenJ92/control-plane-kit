from __future__ import annotations

from contextlib import ExitStack
from dataclasses import fields, replace
import unittest
from unittest import mock

from control_plane_kit_operations.effect_attempt_fold import ExistingFold
from control_plane_kit_operations.effect_attempt_reconciliation import (
    EffectAttemptReconciliationConflict,
    EffectAttemptReconciliationDenied,
    EffectAttemptReconciliationNotFound,
)
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.effect_attempt_intent_store import (
    EffectAttemptIntentStore,
)
from control_plane_kit_operations.postgres.effect_attempt_store import (
    EffectAttemptStore,
)
from control_plane_kit_operations.postgres.effect_outcome_store import (
    EffectAttemptOutcomeStore,
)
from control_plane_kit_operations.postgres.runtime_authority_store import (
    RuntimeAuthorityStore,
)
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.secret_providers import (
    SecretUseAuthorizationService,
)
from tests.postgres_effect_attempt_reconciliation_fixture import (
    AUTHORITY_ERROR,
    FailIfFold,
    FailIfObserver,
    INVALID_TRUTH_ERROR,
    NOT_FOUND_ERROR,
    PostgresEffectAttemptReconciliationFixture,
    REPLAY_ERROR,
)


def _forge_exact(candidate, **changes):
    forged = object.__new__(type(candidate))
    for item in fields(candidate):
        object.__setattr__(
            forged,
            item.name,
            changes.get(item.name, getattr(candidate, item.name)),
        )
    return forged


class PostgresEffectAttemptReconciliationFirstReplayTests(
    PostgresEffectAttemptReconciliationFixture,
    unittest.TestCase,
):
    def test_control_complete_started_and_both_terminal_profiles_are_lawful(self) -> None:
        story = self.observed_story()
        current, _intent, record, _authority = self.seed_reconciliation_source(story)
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.effect_attempt_intents.get(
                    current.state.identity
                ),
                record,
            )
        for profile in ("execution", "observed"):
            with self.subTest(profile=profile):
                terminal_story = self.outcome_story(f"{profile}-succeeded")
                attempt, outcome = self.persist_terminal(terminal_story)
                with self.unit_of_work() as unit_of_work:
                    self.assertEqual(
                        unit_of_work.stores.effect_outcomes.get(
                            attempt.state.identity,
                            attempt.latest_transition_event.event_id,
                        ),
                        outcome,
                    )

    def test_terminal_profiles_replay_without_guard_time_authority_or_fold(self) -> None:
        for profile in ("execution", "observed"):
            for newer in (False, True):
                for expired in (False, True):
                    with self.subTest(
                        profile=profile,
                        newer=newer,
                        expired=expired,
                    ):
                        story = self.outcome_story(f"{profile}-succeeded")
                        attempt, outcome = self.persist_terminal(story)
                        worker, generation = (
                            ("worker-b", 8) if newer else ("worker-a", 7)
                        )
                        if newer:
                            self.replace_current_claim(
                                worker_id=worker,
                                generation=generation,
                            )
                        if expired:
                            self.expire_claim()
                        observer = FailIfObserver("terminal replay invoked observer")
                        fold = FailIfFold("terminal replay invoked guarded fold")
                        command = self.reconciliation_command(
                            attempt,
                            worker_id=worker,
                            generation=generation,
                        )
                        forbidden = AssertionError(
                            "terminal replay crossed a lower interaction"
                        )
                        with mock.patch.object(
                            EffectAttemptIntentStore,
                            "get",
                            side_effect=forbidden,
                        ), mock.patch.object(
                            PostgresExecutionStore,
                            "observe_request_lease_for_update",
                            side_effect=forbidden,
                        ), mock.patch.object(
                            RuntimeAuthorityStore,
                            "get_active_for_update",
                            side_effect=forbidden,
                        ), mock.patch.object(
                            SecretUseAuthorizationService,
                            "authorize_resolution",
                            side_effect=forbidden,
                        ):
                            result = self.reconciliation_service(
                                observer,
                                fold_service=fold,
                            ).execute(command)
                        self.assertEqual(result, ExistingFold(attempt, outcome))
                        self.assertEqual(observer.calls, [])
                        self.assertEqual(fold.calls, [])

    def test_current_claim_precedes_recovery_profile_and_lineage_disclosure(self) -> None:
        worlds = ("started", "execution", "observed", "recovery")
        for world in worlds:
            with self.subTest(stale_world=world):
                if world == "started":
                    current, _intent, _record, _authority = (
                        self.seed_reconciliation_source()
                    )
                elif world == "recovery":
                    self.seed_fold_source("recovered-succeeded")
                    current = self.fold_service(
                        "recovery-classification-event"
                    ).execute(
                        self.fold_command("recovered-succeeded")
                    ).attempt
                else:
                    current, _outcome = self.persist_terminal(
                        self.outcome_story(f"{world}-succeeded")
                    )
                self.replace_current_claim(worker_id="worker-b", generation=8)
                forbidden = AssertionError("stale claim disclosed durable truth")
                with ExitStack() as stack:
                    for owner, name in (
                        (EffectAttemptOutcomeStore, "get"),
                        (EffectAttemptIntentStore, "get"),
                        (PostgresExecutionStore, "observe_request_lease_for_update"),
                        (RuntimeAuthorityStore, "get_active_for_update"),
                        (SecretUseAuthorizationService, "authorize_resolution"),
                    ):
                        stack.enter_context(
                            mock.patch.object(owner, name, side_effect=forbidden)
                        )
                    with self.assertRaises(
                        EffectAttemptReconciliationDenied
                    ) as caught:
                        self.reconciliation_service(
                            FailIfObserver(),
                            fold_service=FailIfFold(),
                        ).execute(self.reconciliation_command(current))
                self.assertEqual(str(caught.exception), AUTHORITY_ERROR)

        for label, worker, generation in (
            ("lower", "worker-a", 6),
            ("equal-other", "worker-b", 7),
        ):
            with self.subTest(lineage=label):
                current, _outcome = self.persist_terminal(
                    self.outcome_story("observed-succeeded")
                )
                self.replace_current_claim(worker_id=worker, generation=generation)
                with mock.patch.object(
                    EffectAttemptOutcomeStore,
                    "get",
                    side_effect=AssertionError(
                        "incongruent lineage read outcome evidence"
                    ),
                ):
                    with self.assertRaises(
                        EffectAttemptReconciliationConflict
                    ) as caught:
                        self.reconciliation_service(
                            FailIfObserver(),
                            fold_service=FailIfFold(),
                        ).execute(
                            self.reconciliation_command(
                                current,
                                worker_id=worker,
                                generation=generation,
                            )
                        )
                self.assertEqual(str(caught.exception), REPLAY_ERROR)

        self.seed_fold_source("recovered-succeeded")
        recovered = self.fold_service("recovery-barrier-event").execute(
            self.fold_command("recovered-succeeded")
        ).attempt
        with mock.patch.object(
            EffectAttemptOutcomeStore,
            "get",
            side_effect=AssertionError("recovery classification read direct outcome"),
        ):
            with self.assertRaises(EffectAttemptReconciliationConflict) as caught:
                self.reconciliation_service(
                    FailIfObserver(),
                    fold_service=FailIfFold(),
                ).execute(self.reconciliation_command(recovered))
        self.assertEqual(str(caught.exception), REPLAY_ERROR)

    def test_missing_and_invalid_locked_truth_use_fixed_categories(self) -> None:
        current, _intent, _record, _authority = self.seed_reconciliation_source()
        rows = (
            (PostgresExecutionStore, "get_request_for_update"),
            (PostgresExecutionStore, "get_run_for_request_for_update"),
            (EffectAttemptStore, "get_for_update"),
        )
        for owner, name in rows:
            with self.subTest(missing=name):
                with mock.patch.object(owner, name, side_effect=KeyError("canary")):
                    with self.assertRaises(
                        EffectAttemptReconciliationNotFound
                    ) as caught:
                        self.reconciliation_service(
                            FailIfObserver(),
                            fold_service=FailIfFold(),
                        ).execute(self.reconciliation_command(current))
                self.assertEqual(str(caught.exception), NOT_FOUND_ERROR)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

        for error_type in (TypeError, RuntimeError):
            with self.subTest(raw=error_type.__name__):
                error = error_type("raw-lock-canary")
                with mock.patch.object(
                    PostgresExecutionStore,
                    "get_request_for_update",
                    side_effect=error,
                ):
                    with self.assertRaises(error_type) as caught:
                        self.reconciliation_service(
                            FailIfObserver(),
                            fold_service=FailIfFold(),
                        ).execute(self.reconciliation_command(current))
                self.assertIs(caught.exception, error)

    def test_terminal_missing_corrupt_foreign_and_drifted_outcomes_conflict(self) -> None:
        for fault in ("missing", "corrupt", "foreign", "drifted"):
            with self.subTest(fault=fault):
                story = self.outcome_story("observed-succeeded")
                attempt, outcome = self.persist_terminal(story)
                side_effect = None
                observed = outcome
                if fault == "missing":
                    side_effect = KeyError("missing-outcome-canary")
                elif fault == "corrupt":
                    side_effect = OperationsRecordError("corrupt-outcome-canary")
                elif fault == "foreign":
                    observed = _forge_exact(
                        outcome,
                        workspace_id="workspace-foreign",
                    )
                else:
                    observed = _forge_exact(
                        outcome,
                        attempt=_forge_exact(
                            outcome.attempt,
                            latest_transition_event=replace(
                                outcome.attempt.latest_transition_event,
                                event_id="drifted-direct-event",
                            ),
                        ),
                    )
                with mock.patch.object(
                    EffectAttemptOutcomeStore,
                    "get",
                    side_effect=side_effect,
                    return_value=observed,
                ):
                    with self.assertRaises(
                        EffectAttemptReconciliationConflict
                    ) as caught:
                        self.reconciliation_service(
                            FailIfObserver(),
                            fold_service=FailIfFold(),
                        ).execute(self.reconciliation_command(attempt))
                self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

    def test_fresh_expiry_and_intent_original_event_truth_precede_observer(self) -> None:
        story = self.observed_story()
        with self.subTest(world="expired-current-claim"):
            current, intent, record, _authority = self.seed_reconciliation_source(
                story
            )
            with self.lease_observation(
                "2030-01-01T00:00:02Z",
                expired=True,
            ), mock.patch.object(
                EffectAttemptIntentStore,
                "get",
                side_effect=AssertionError("expired claim loaded intent"),
            ):
                with self.assertRaises(EffectAttemptReconciliationDenied) as caught:
                    self.reconciliation_service(
                        FailIfObserver(),
                        fold_service=FailIfFold(),
                    ).execute(self.reconciliation_command(current))
            self.assertEqual(str(caught.exception), AUTHORITY_ERROR)

        for fault in ("missing", "corrupt", "foreign", "original-event"):
            with self.subTest(intent=fault):
                current, intent, record, _authority = self.seed_reconciliation_source(
                    story
                )
                side_effect = None
                observed = record
                if fault == "missing":
                    side_effect = KeyError("missing-intent-canary")
                elif fault == "corrupt":
                    side_effect = OperationsRecordError("corrupt-intent-canary")
                elif fault == "foreign":
                    observed = _forge_exact(
                        record,
                        identity=self.identity(activity_id="foreign-activity"),
                    )
                else:
                    observed = _forge_exact(
                        record,
                        original_start_event=replace(
                            record.original_start_event,
                            event_id="foreign-original-event",
                        ),
                    )
                with mock.patch.object(
                    EffectAttemptIntentStore,
                    "get",
                    side_effect=side_effect,
                    return_value=observed,
                ):
                    with self.assertRaises(
                        EffectAttemptReconciliationConflict
                    ) as caught:
                        self.reconciliation_service(
                            FailIfObserver(),
                            fold_service=FailIfFold(),
                        ).execute(self.reconciliation_command(current))
                self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)


if __name__ == "__main__":
    unittest.main()
