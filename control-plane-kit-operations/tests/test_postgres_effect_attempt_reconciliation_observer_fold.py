from __future__ import annotations

from dataclasses import replace
import unittest

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationRequest,
)
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    FoldEffectAttempt,
    NewlyFolded,
)
from control_plane_kit_operations.effect_attempt_reconciliation import (
    EffectAttemptReconciliationConflict,
    EffectAttemptReconciliationDenied,
)
from control_plane_kit_operations.effect_outcome_evidence import (
    ObservedEffectOutcome,
    effect_outcome_failure,
    effect_outcome_transition,
)
from tests.postgres_effect_attempt_reconciliation_fixture import (
    AUTHORITY_ERROR,
    INVALID_TRUTH_ERROR,
    PostgresEffectAttemptReconciliationFixture,
    REPLAY_ERROR,
    RecordingObserver,
    UnitOfWorkLedger,
)
from tests.execution_lease_recovery_fixture import Sequence


_NO_RESULT_OVERRIDE = object()


class _RecordingFold:
    def __init__(
        self,
        service,
        *,
        error=None,
        result=_NO_RESULT_OVERRIDE,
    ) -> None:
        self._service = service
        self._error = error
        self._result = result
        self.calls = []

    def execute_observed(self, command):
        self.calls.append(command)
        if self._error is not None:
            raise self._error
        if self._result is not _NO_RESULT_OVERRIDE:
            return self._result
        return self._service.execute_observed(command)


class PostgresEffectAttemptReconciliationObserverFoldTests(
    PostgresEffectAttemptReconciliationFixture,
    unittest.TestCase,
):
    def test_control_all_twelve_observation_worlds_have_exact_projections(self) -> None:
        stories = self.observed_stories()
        self.assertEqual(len(stories), 12)
        self.assertEqual({story.compensation for story in stories}, {False, True})
        for story in stories:
            with self.subTest(story=story.name, compensation=story.compensation):
                current = self.record(
                    "started",
                    compensation=story.compensation,
                    run_id="run-a",
                    activity_id="start-runtime",
                )
                intent = self.persisted_intent(current)
                observation = self.observation_for(story, current, intent)
                outcome = ObservedEffectOutcome(current.state.identity, observation)
                transition = effect_outcome_transition(outcome)
                failure = effect_outcome_failure(outcome)
                command = FoldEffectAttempt(
                    intent.source.request_id,
                    transition,
                    self.authority(),
                    self.fence(),
                    failure,
                    outcome,
                )
                self.assertEqual(command.transition, transition)
                self.assertEqual(command.failure, failure)
                self.assertEqual(command.outcome, outcome)

    def test_all_twelve_worlds_observe_outside_uow_and_delegate_exact_guard_once(self) -> None:
        for story in self.observed_stories():
            with self.subTest(story=story.name, compensation=story.compensation):
                current, intent, record, authority = self.seed_reconciliation_source(
                    story,
                    remote=True,
                )
                uses = self.required_secret_uses(current, intent, authority)
                self.admit_secret_uses(uses)
                ledger = UnitOfWorkLedger(self.unit_of_work)
                observer = self.observer_for(story, current, intent, ledger=ledger)
                event_id = f"reconciled-{int(story.compensation)}-{story.name}"
                fold_service = self.fold_service_with_id_factory(
                    Sequence(*self.fold_ids_for_story(event_id, story))
                )
                recording_fold = _RecordingFold(fold_service)
                before_non_advancement = self.non_advancement_snapshot()
                result = self.reconciliation_service(
                    observer,
                    ledger=ledger,
                    fold_service=recording_fold,
                ).execute(
                    self.reconciliation_command(
                        current,
                        scopes=(
                            PolicyScope.EXECUTION_OPERATE,
                            PolicyScope.SECRET_PROVIDER_USE,
                        ),
                    )
                )
                self.assertIsInstance(result, NewlyFolded)
                self.assertEqual(len(observer.calls), 1)
                request, observed_authority = observer.calls[0]
                self.assertIs(type(request), RuntimeEffectObservationRequest)
                self.assertEqual(request.effect_id, current.original_start_event.event_id)
                self.assertEqual(request.intent, intent)
                self.assertEqual(
                    tuple(
                        (grant.reference, grant.intent)
                        for grant in request.runtime_request.secret_resolution_grants
                    ),
                    uses,
                )
                self.assertTrue(
                    all(
                        grant.actor_subject == "worker-a"
                        and grant.operation_id == intent.source.request_id
                        and grant.run_id == current.state.identity.run_id.value
                        and grant.activity_id == current.state.identity.activity_id
                        and grant.effect_id == current.original_start_event.event_id
                        for grant in request.runtime_request.secret_resolution_grants
                    )
                )
                self.assertEqual(observed_authority, authority)
                self.assertEqual(len(recording_fold.calls), 1)
                guarded = recording_fold.calls[0]
                self.assertEqual(guarded.intent_record, record)
                self.assertEqual(guarded.runtime_authority, authority)
                self.assertEqual(guarded.fold.outcome, ObservedEffectOutcome(current.state.identity, self.observation_for(story, current, intent)))
                self.assertEqual(
                    result.outcome_record,
                    self.expected_outcome_record(
                        result.attempt,
                        guarded.fold.outcome,
                        event_id=event_id,
                    ),
                )
                self.assertEqual(ledger.active, 0)
                self.assertEqual(ledger.entries, 1 + len(uses))
                self.assertEqual(ledger.entries, ledger.exits)
                self.assertEqual(self.non_advancement_snapshot(), before_non_advancement)

    def test_observer_effect_and_fingerprint_must_match_before_fold(self) -> None:
        story = self.observed_story()
        for fault in ("effect", "fingerprint"):
            with self.subTest(fault=fault):
                current, intent, _record, authority = self.seed_reconciliation_source(story)
                uses = self.required_secret_uses(current, intent, authority)
                self.admit_secret_uses(uses)
                observation = self.observation_for(story, current, intent)
                observation = replace(
                    observation,
                    effect_id=(
                        "foreign-effect" if fault == "effect" else observation.effect_id
                    ),
                    request_fingerprint=(
                        "f" * 64
                        if fault == "fingerprint"
                        else observation.request_fingerprint
                    ),
                )
                observer = RecordingObserver(observation)
                fold = _RecordingFold(
                    self.fold_service("must-not-fold"),
                    error=AssertionError("incongruent observation reached fold"),
                )
                with self.assertRaises(EffectAttemptReconciliationConflict) as caught:
                    self.reconciliation_service(
                        observer,
                        fold_service=fold,
                    ).execute(
                        self.reconciliation_command(
                            current,
                            scopes=(
                                PolicyScope.EXECUTION_OPERATE,
                                PolicyScope.SECRET_PROVIDER_USE,
                            ),
                        )
                    )
                self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertEqual(len(observer.calls), 1)
                self.assertEqual(observer.calls[0][1], authority)
                self.assertEqual(fold.calls, [])

    def test_observer_exact_result_admission_and_raw_fault_identity(self) -> None:
        story = self.observed_story()
        for returned in (None, object()):
            with self.subTest(returned=type(returned).__name__):
                current, intent, _record, authority = self.seed_reconciliation_source(story)
                uses = self.required_secret_uses(current, intent, authority)
                self.admit_secret_uses(uses)
                observer = RecordingObserver(returned)
                with self.assertRaises(EffectAttemptReconciliationConflict) as caught:
                    self.reconciliation_service(
                        observer,
                        fold_service=_RecordingFold(self.fold_service("unused")),
                    ).execute(
                        self.reconciliation_command(
                            current,
                            scopes=(
                                PolicyScope.EXECUTION_OPERATE,
                                PolicyScope.SECRET_PROVIDER_USE,
                            ),
                        )
                    )
                self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

        for error_type in (TypeError, RuntimeError):
            with self.subTest(raw=error_type.__name__):
                current, intent, _record, authority = self.seed_reconciliation_source(story)
                uses = self.required_secret_uses(current, intent, authority)
                self.admit_secret_uses(uses)
                error = error_type("raw-observer-canary")
                observer = self.observer_for(story, current, intent, error=error)
                with self.assertRaises(error_type) as caught:
                    self.reconciliation_service(
                        observer,
                        fold_service=_RecordingFold(self.fold_service("unused")),
                    ).execute(
                        self.reconciliation_command(
                            current,
                            scopes=(
                                PolicyScope.EXECUTION_OPERATE,
                                PolicyScope.SECRET_PROVIDER_USE,
                            ),
                        )
                    )
                self.assertIs(caught.exception, error)

    def test_guarded_fold_return_is_exact_and_fold_faults_preserve_identity(self) -> None:
        story = self.observed_story()
        current, intent, _record, authority = self.seed_reconciliation_source(story)
        uses = self.required_secret_uses(current, intent, authority)
        self.admit_secret_uses(uses)
        observer = self.observer_for(story, current, intent)
        for returned in (None, object()):
            with self.subTest(returned=type(returned).__name__):
                fold = _RecordingFold(self.fold_service("unused"), result=returned)
                with self.assertRaises(EffectAttemptReconciliationConflict) as caught:
                    self.reconciliation_service(
                        observer,
                        fold_service=fold,
                    ).execute(
                        self.reconciliation_command(
                            current,
                            scopes=(
                                PolicyScope.EXECUTION_OPERATE,
                                PolicyScope.SECRET_PROVIDER_USE,
                            ),
                        )
                    )
                self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

        for error in (
            EffectAttemptFoldConflict("expected-fold-canary"),
            EffectAttemptFoldDenied("expected-fold-denied-canary"),
            TypeError("raw-fold-type-canary"),
            RuntimeError("raw-fold-runtime-canary"),
        ):
            with self.subTest(error=type(error).__name__):
                fold = _RecordingFold(self.fold_service("unused"), error=error)
                if isinstance(error, EffectAttemptFoldConflict):
                    with self.assertRaises(EffectAttemptReconciliationConflict) as caught:
                        self.reconciliation_service(
                            observer,
                            fold_service=fold,
                        ).execute(
                            self.reconciliation_command(
                                current,
                                scopes=(
                                    PolicyScope.EXECUTION_OPERATE,
                                    PolicyScope.SECRET_PROVIDER_USE,
                                ),
                            )
                        )
                    self.assertEqual(str(caught.exception), REPLAY_ERROR)
                    self.assertIsNone(caught.exception.__cause__)
                    self.assertIsNone(caught.exception.__context__)
                elif isinstance(error, EffectAttemptFoldDenied):
                    with self.assertRaises(EffectAttemptReconciliationDenied) as caught:
                        self.reconciliation_service(
                            observer,
                            fold_service=fold,
                        ).execute(
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
                else:
                    with self.assertRaises(type(error)) as caught:
                        self.reconciliation_service(
                            observer,
                            fold_service=fold,
                        ).execute(
                            self.reconciliation_command(
                                current,
                                scopes=(
                                    PolicyScope.EXECUTION_OPERATE,
                                    PolicyScope.SECRET_PROVIDER_USE,
                                ),
                            )
                        )
                    self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
