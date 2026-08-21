from __future__ import annotations

from dataclasses import fields, replace

from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.runtime_authorities import (
    LocalDockerSocketAuthority,
    RegisteredRuntimeAuthority,
    RegisteredRuntimeAuthorityStatus,
)
from tests.atomic_effect_attempt_fold_fixture import (
    AtomicEffectAttemptFoldFixture,
)
from tests.effect_attempt_fold_fixture import (
    EffectAttemptFoldService,
    FoldEffectAttempt,
    fold_module,
)
from tests.effect_outcome_evidence_fixture import (
    ObservedEffectOutcome,
    effect_outcome_failure,
    effect_outcome_transition,
    forge_exact,
)


GuardedObservedEffectFold = getattr(
    fold_module,
    "GuardedObservedEffectFold",
    None,
)
_DEFAULT_AUTHORITY = object()


def subclass_copy(value):
    hostile_type = type(f"Hostile{type(value).__name__}", (type(value),), {})
    hostile = object.__new__(hostile_type)
    for item in fields(value):
        object.__setattr__(hostile, item.name, getattr(value, item.name))
    return hostile


class GuardedObservedEffectFoldFixture(AtomicEffectAttemptFoldFixture):
    def require_guarded_language(self) -> None:
        self.assertIsNotNone(
            GuardedObservedEffectFold,
            "guarded observed effect fold language is missing",
        )

    def require_guarded_service(self) -> None:
        self.require_fold_service()
        self.assertTrue(
            hasattr(EffectAttemptFoldService, "execute_observed"),
            "guarded observed effect fold service entry point is missing",
        )

    def observed_stories(self):
        return tuple(
            story
            for story in self.stories()
            if story.profile == "provider-observation"
        )

    def intent_for_story(self, story, *, authority_ref=True, runtime_kind=None):
        intent = self.intent_for_attempt(
            compensation=story.compensation,
            run_id=story.attempt.state.identity.run_id.value,
            activity_id=story.attempt.state.identity.activity_id,
        )
        changes = {}
        if authority_ref is False:
            changes.update(authority_ref=None, authority_deliveries=())
        if runtime_kind is not None:
            changes["runtime_kind"] = runtime_kind
        return replace(intent, **changes) if changes else intent

    def intent_record_for_story(self, story, *, intent=None):
        value = intent or self.intent_for_story(story)
        return EffectAttemptIntentRecord(
            story.attempt.state.identity,
            story.attempt.original_start_event,
            value,
        )

    def runtime_authority_for_intent(self, intent):
        if intent.authority_ref is None:
            return None
        return RegisteredRuntimeAuthority(
            registration_id="runtime-authority-a",
            workspace_id=intent.source.workspace_id,
            authority_ref=intent.authority_ref,
            runtime_kind=RuntimeKind.DOCKER,
            authority=LocalDockerSocketAuthority(),
            admitted_by="operator-a",
            admitted_at="2030-01-01T00:00:00Z",
            status=RegisteredRuntimeAuthorityStatus.ACTIVE,
            metadata={},
        )

    def fold_for_story(self, story, *, intent=None, outcome=None, **changes):
        value = intent or self.intent_for_story(story)
        observation = story.value
        if observation.request_fingerprint != self.request_fingerprint(value):
            observation = replace(
                observation,
                request_fingerprint=self.request_fingerprint(value),
            )
        admitted_outcome = outcome or ObservedEffectOutcome(
            story.attempt.state.identity,
            observation,
        )
        values = {
            "request_id": value.source.request_id,
            "transition": effect_outcome_transition(admitted_outcome),
            "authority": self.authority(),
            "fence": self.execution_fence(),
            "failure": effect_outcome_failure(admitted_outcome),
            "outcome": admitted_outcome,
        }
        values.update(changes)
        return FoldEffectAttempt(**values)

    def guarded_command(
        self,
        story=None,
        *,
        intent=None,
        intent_record=None,
        runtime_authority=_DEFAULT_AUTHORITY,
        fold=None,
    ):
        self.require_guarded_language()
        value_story = story or self.observed_stories()[0]
        value_intent = intent or self.intent_for_story(value_story)
        value_record = intent_record or self.intent_record_for_story(
            value_story,
            intent=value_intent,
        )
        value_authority = (
            self.runtime_authority_for_intent(value_intent)
            if runtime_authority is _DEFAULT_AUTHORITY
            else runtime_authority
        )
        return GuardedObservedEffectFold(
            fold or self.fold_for_story(value_story, intent=value_intent),
            value_record,
            value_authority,
        )

    @staticmethod
    def request_fingerprint(intent) -> str:
        from control_plane_kit_core.runtime_effect_observation import (
            runtime_effect_intent_fingerprint,
        )

        return runtime_effect_intent_fingerprint(intent)

    def forge_guard(self, template, **changes):
        values = {
            item.name: getattr(template, item.name)
            for item in fields(GuardedObservedEffectFold)
        }
        values.update(changes)
        return forge_exact(GuardedObservedEffectFold, **values)


__all__ = [
    "EffectAttemptFoldService",
    "FoldEffectAttempt",
    "GuardedObservedEffectFold",
    "GuardedObservedEffectFoldFixture",
    "RuntimeKind",
    "forge_exact",
    "subclass_copy",
]
