from __future__ import annotations

import dataclasses
import inspect
import unittest

import control_plane_kit_operations as operations_root
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntent,
)
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.effect_outcome_evidence import (
    ExecutionEffectOutcome,
    ObservedEffectOutcome,
    effect_outcome_failure,
    effect_outcome_transition,
)
from control_plane_kit_operations.runtime_authorities import (
    RegisteredRuntimeAuthority,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.atomic_effect_attempt_fold_fixture import ExistingFold, NewlyFolded
from tests.effect_attempt_fold_fixture import (
    EffectAttemptFoldDenied,
    EffectAttemptFoldResult,
    FoldEffectAttempt,
)
from tests.guarded_observed_effect_fold_fixture import (
    EffectAttemptFoldService,
    GuardedObservedEffectFold,
    GuardedObservedEffectFoldFixture,
    forge_exact,
    subclass_copy,
)


class FailIfUnitOfWork:
    def __init__(self, message: str) -> None:
        self.error = AssertionError(message)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise self.error


class HostileText(str):
    def __new__(cls, value: str, dispatches: list[str]):
        instance = super().__new__(cls, value)
        instance.dispatches = dispatches
        return instance

    def __getattribute__(self, name):
        if name == "__class__":
            object.__getattribute__(self, "dispatches").append("class")
            raise AssertionError("hostile class access dispatched")
        return super().__getattribute__(name)

    def __eq__(self, _other):
        self.dispatches.append("eq")
        raise AssertionError("hostile equality dispatched")

    def __hash__(self):
        self.dispatches.append("hash")
        raise AssertionError("hostile hash dispatched")


class GuardedObservedEffectFoldContractTests(
    GuardedObservedEffectFoldFixture,
    unittest.TestCase,
):
    def assert_invalid_guard(self, construct, *canaries: str) -> None:
        with self.assertRaises(InvalidOperationCommand) as caught:
            construct()
        self.assertEqual(
            str(caught.exception),
            "guarded observed effect fold command is invalid",
        )
        self.assert_safe_error(caught.exception, *canaries)

    def service(self, factory):
        self.require_fold_service()
        return EffectAttemptFoldService(
            factory,
            id_factory=lambda: "unused-event-id",
        )

    # Ungated controls: these execute before every missing-publication gate.
    def test_ungated_all_observed_projection_worlds_are_exact(self) -> None:
        stories = self.observed_stories()
        self.assertEqual(len(stories), 12)
        for story in stories:
            with self.subTest(story=story.name, compensation=story.compensation):
                outcome = self.outcome_for(story)
                self.assertIs(type(outcome), ObservedEffectOutcome)
                self.assertEqual(
                    effect_outcome_transition(outcome).kind,
                    story.transition,
                )
                self.assertEqual(
                    effect_outcome_failure(outcome),
                    story.attempt.latest_transition_event.failure,
                )
                self.assertEqual(
                    outcome.request_fingerprint,
                    story.attempt.state.request_fingerprint,
                )

    def test_ungated_intent_and_authority_values_are_lawful(self) -> None:
        for compensation in (False, True):
            story = next(
                value
                for value in self.observed_stories()
                if value.compensation is compensation
            )
            intent = self.intent_for_story(story)
            record = self.intent_record_for_story(story, intent=intent)
            authority = self.runtime_authority_for_intent(intent)
            self.assertEqual(record.identity, story.attempt.state.identity)
            self.assertEqual(
                record.original_start_event,
                story.attempt.original_start_event,
            )
            self.assertEqual(
                record.request_fingerprint,
                story.attempt.state.request_fingerprint,
            )
            self.assertIs(type(authority), RegisteredRuntimeAuthority)
            self.assertEqual(authority.workspace_id, record.workspace_id)
            self.assertEqual(authority.authority_ref, intent.authority_ref)
            self.assertIs(authority.runtime_kind, RuntimeKind.DOCKER)

        no_ref = self.intent_for_story(story, authority_ref=False)
        no_ref_record = self.intent_record_for_story(story, intent=no_ref)
        self.assertIsNone(no_ref.authority_ref)
        self.assertEqual(no_ref.authority_deliveries, ())
        self.assertIsNone(self.runtime_authority_for_intent(no_ref))
        self.assertEqual(no_ref_record.intent, no_ref)

        non_docker = self.intent_for_story(
            story,
            runtime_kind=RuntimeKind.EXTERNAL,
        )
        non_docker_record = self.intent_record_for_story(
            story,
            intent=non_docker,
        )
        self.assertIs(non_docker.runtime_kind, RuntimeKind.EXTERNAL)
        self.assertIsNotNone(non_docker.authority_ref)
        self.assertEqual(non_docker_record.intent, non_docker)

    def test_ungated_ordinary_fold_language_and_service_are_unchanged(self) -> None:
        story = next(
            value
            for value in self.stories()
            if value.profile == "execution-result"
        )
        execution = self.outcome_for(story)
        observed = self.outcome_for(self.observed_stories()[0])
        self.assertIs(type(execution), ExecutionEffectOutcome)
        self.assertIs(type(observed), ObservedEffectOutcome)
        self.assertEqual(
            tuple(item.name for item in dataclasses.fields(FoldEffectAttempt)),
            ("request_id", "transition", "authority", "fence", "failure", "outcome"),
        )
        self.assertEqual(
            tuple(item.name for item in dataclasses.fields(NewlyFolded)),
            ("attempt", "outcome_record"),
        )
        self.assertEqual(
            tuple(item.name for item in dataclasses.fields(ExistingFold)),
            ("attempt", "outcome_record"),
        )
        self.assertEqual(EffectAttemptFoldResult, NewlyFolded | ExistingFold)
        self.assertEqual(
            tuple(inspect.signature(EffectAttemptFoldService).parameters),
            ("unit_of_work_factory", "id_factory"),
        )
        self.assertEqual(
            tuple(inspect.signature(EffectAttemptFoldService.execute).parameters),
            ("self", "command"),
        )

    def test_ungated_ordinary_execution_reaches_the_existing_uow_boundary(self) -> None:
        story = next(
            value
            for value in self.stories()
            if value.profile == "execution-result"
        )
        fail = FailIfUnitOfWork("ordinary execution reached unit of work")
        with self.assertRaises(AssertionError) as caught:
            self.service(fail).execute(self.direct_command(story))
        self.assertIs(caught.exception, fail.error)
        self.assertEqual(fail.calls, 1)

    # Target laws: each method has one missing-publication gate at entry.
    def test_guard_is_exact_frozen_three_field_product_and_repr_hidden(self) -> None:
        self.require_guarded_language()
        value = self.guarded_command()
        self.assertEqual(
            tuple(item.name for item in dataclasses.fields(GuardedObservedEffectFold)),
            ("fold", "intent_record", "runtime_authority"),
        )
        self.assertTrue(GuardedObservedEffectFold.__dataclass_params__.frozen)
        self.assertIs(operations_root.GuardedObservedEffectFold, GuardedObservedEffectFold)
        self.assertFalse(dataclasses.fields(GuardedObservedEffectFold)[1].repr)
        self.assertFalse(dataclasses.fields(GuardedObservedEffectFold)[2].repr)
        rendered = f"{value!s} {value!r}"
        for canary in (
            "remote-docker",
            "secret://local/workspace-a/docker/client-key",
            "http://upstream.internal:8080",
        ):
            self.assertNotIn(canary, rendered)

    def test_guard_accepts_all_observed_variants_and_both_phases(self) -> None:
        self.require_guarded_language()
        stories = self.observed_stories()
        self.assertEqual(len(stories), 12)
        for story in stories:
            with self.subTest(story=story.name, compensation=story.compensation):
                value = self.guarded_command(story)
                self.assertIs(type(value.fold.outcome), ObservedEffectOutcome)
                self.assertEqual(value.fold.transition, effect_outcome_transition(value.fold.outcome))
                self.assertEqual(value.fold.failure, effect_outcome_failure(value.fold.outcome))
                self.assertEqual(value.intent_record.identity, value.fold.outcome.identity)
                self.assertEqual(
                    value.intent_record.original_start_event,
                    story.attempt.original_start_event,
                )

    def test_guard_authority_sum_is_exact_and_non_docker_is_uninhabited(self) -> None:
        self.require_guarded_language()
        story = self.observed_stories()[0]
        with_ref = self.guarded_command(story)
        self.assertIs(type(with_ref.runtime_authority), RegisteredRuntimeAuthority)

        no_ref_intent = self.intent_for_story(story, authority_ref=False)
        no_ref = self.guarded_command(
            story,
            intent=no_ref_intent,
            runtime_authority=None,
        )
        self.assertIsNone(no_ref.intent_record.intent.authority_ref)
        self.assertIsNone(no_ref.runtime_authority)

        self.assert_invalid_guard(
            lambda: self.guarded_command(story, runtime_authority=None)
        )
        self.assert_invalid_guard(
            lambda: self.guarded_command(
                story,
                intent=no_ref_intent,
                runtime_authority=with_ref.runtime_authority,
            )
        )
        non_docker = self.intent_for_story(
            story,
            runtime_kind=RuntimeKind.EXTERNAL,
        )
        self.assert_invalid_guard(
            lambda: self.guarded_command(
                story,
                intent=non_docker,
                runtime_authority=None,
            )
        )

    def test_guard_rejects_execution_recovery_and_cross_joined_truth(self) -> None:
        self.require_guarded_language()
        observed_story = self.observed_stories()[0]
        valid = self.guarded_command(observed_story)
        execution_story = next(
            value
            for value in self.stories()
            if value.profile == "execution-result"
        )
        other = next(
            value
            for value in self.observed_stories()
            if value.compensation is not observed_story.compensation
        )
        cases = (
            lambda: self.guarded_command(
                observed_story,
                fold=self.direct_command(execution_story),
            ),
            lambda: self.guarded_command(
                observed_story,
                fold=self.recovery_command(),
            ),
            lambda: self.guarded_command(
                observed_story,
                intent_record=self.intent_record_for_story(other),
            ),
            lambda: self.guarded_command(
                observed_story,
                fold=dataclasses.replace(valid.fold, request_id="request-b"),
            ),
        )
        for construct in cases:
            self.assert_invalid_guard(construct)

    def test_guard_revalidates_hostile_and_deep_forged_values_without_dispatch(self) -> None:
        self.require_guarded_language()
        valid = self.guarded_command()
        dispatches: list[str] = []
        hostile_guard_type = type(
            "HostileGuardedObservedEffectFold",
            (GuardedObservedEffectFold,),
            {},
        )
        hostile_text = HostileText("workspace-a", dispatches)
        forged_authority = forge_exact(
            RegisteredRuntimeAuthority,
            registration_id=valid.runtime_authority.registration_id,
            workspace_id=hostile_text,
            authority_ref=valid.runtime_authority.authority_ref,
            runtime_kind=valid.runtime_authority.runtime_kind,
            authority=valid.runtime_authority.authority,
            admitted_by=valid.runtime_authority.admitted_by,
            admitted_at=valid.runtime_authority.admitted_at,
            status=valid.runtime_authority.status,
            metadata=valid.runtime_authority.metadata,
        )
        forged_intent = forge_exact(
            RuntimeEffectIntent,
            kind=valid.intent_record.intent.kind,
            runtime_kind=valid.intent_record.intent.runtime_kind,
            source=object(),
            activity_id=valid.intent_record.intent.activity_id,
            operation=valid.intent_record.intent.operation,
            authority_ref=valid.intent_record.intent.authority_ref,
            authority_deliveries=valid.intent_record.intent.authority_deliveries,
            products=valid.intent_record.intent.products,
        )
        forged_record = forge_exact(
            EffectAttemptIntentRecord,
            identity=valid.intent_record.identity,
            original_start_event=valid.intent_record.original_start_event,
            intent=forged_intent,
        )
        for construct in (
            lambda: hostile_guard_type(
                valid.fold,
                valid.intent_record,
                valid.runtime_authority,
            ),
            lambda: self.guarded_command(fold=subclass_copy(valid.fold)),
            lambda: self.guarded_command(intent_record=subclass_copy(valid.intent_record)),
            lambda: self.guarded_command(runtime_authority=subclass_copy(valid.runtime_authority)),
            lambda: self.guarded_command(runtime_authority=forged_authority),
            lambda: self.guarded_command(intent_record=forged_record),
        ):
            self.assert_invalid_guard(construct, "workspace-a")
            self.assertEqual(dispatches, [])

    def test_service_adds_only_exact_observed_entry_and_valid_reaches_uow(self) -> None:
        self.require_guarded_language()
        self.require_guarded_service()
        self.assertEqual(
            tuple(inspect.signature(EffectAttemptFoldService.execute_observed).parameters),
            ("self", "command"),
        )
        valid = self.guarded_command()
        fail = FailIfUnitOfWork("guarded observation reached unit of work")
        with self.assertRaises(AssertionError) as caught:
            self.service(fail).execute_observed(valid)
        self.assertIs(caught.exception, fail.error)
        self.assertEqual(fail.calls, 1)

    def test_service_rejects_invalid_and_scope_before_unit_of_work(self) -> None:
        self.require_guarded_language()
        self.require_guarded_service()
        valid = self.guarded_command()
        forged_intent = forge_exact(
            RuntimeEffectIntent,
            kind=valid.intent_record.intent.kind,
            runtime_kind=valid.intent_record.intent.runtime_kind,
            source=object(),
            activity_id=valid.intent_record.intent.activity_id,
            operation=valid.intent_record.intent.operation,
            authority_ref=valid.intent_record.intent.authority_ref,
            authority_deliveries=valid.intent_record.intent.authority_deliveries,
            products=valid.intent_record.intent.products,
        )
        forged_record = forge_exact(
            EffectAttemptIntentRecord,
            identity=valid.intent_record.identity,
            original_start_event=valid.intent_record.original_start_event,
            intent=forged_intent,
        )
        dispatches: list[str] = []

        def hostile_access(_self, name):
            dispatches.append(name)
            raise AssertionError("hostile guard access dispatched")

        hostile_type = type(
            "HostileGuardedObservedEffectFold",
            (GuardedObservedEffectFold,),
            {"__getattribute__": hostile_access},
        )
        hostile = object.__new__(hostile_type)
        for item in dataclasses.fields(GuardedObservedEffectFold):
            object.__setattr__(hostile, item.name, getattr(valid, item.name))
        for invalid in (
            self.forge_guard(valid, runtime_authority=None),
            self.forge_guard(valid, intent_record=forged_record),
            hostile,
        ):
            fail = FailIfUnitOfWork("invalid guard reached unit of work")
            with self.assertRaises(InvalidOperationCommand) as caught:
                self.service(fail).execute_observed(invalid)
            self.assertEqual(
                str(caught.exception),
                "guarded observed effect fold command is invalid",
            )
            self.assert_safe_error(caught.exception)
            self.assertEqual(fail.calls, 0)
            self.assertEqual(dispatches, [])

        denied_fold = dataclasses.replace(
            valid.fold,
            authority=self.authority(scopes=()),
        )
        denied = self.guarded_command(fold=denied_fold)
        fail = FailIfUnitOfWork("guarded scope denial reached unit of work")
        with self.assertRaises(EffectAttemptFoldDenied):
            self.service(fail).execute_observed(denied)
        self.assertEqual(fail.calls, 0)


if __name__ == "__main__":
    unittest.main()
