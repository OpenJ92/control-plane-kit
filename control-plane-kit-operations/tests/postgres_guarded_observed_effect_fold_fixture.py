from __future__ import annotations

from dataclasses import replace
from unittest import mock

from control_plane_kit_core.operations import ActivityEventKind
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_fingerprint,
)
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.effect_attempt_fold import (
    GuardedObservedEffectFold,
)
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptEventEvidence,
    effect_attempt_state_fingerprint,
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
from control_plane_kit_operations.postgres.observed_state import (
    PostgresObservedStateStore,
)
from control_plane_kit_operations.postgres.runtime_authority_store import (
    RuntimeAuthorityStore,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
)
from control_plane_kit_operations.runtime_authorities import (
    LocalDockerSocketAuthority,
    RegisteredRuntimeAuthorityStatus,
    RemoteDockerTlsAuthority,
)
from tests.effect_attempt_record_fixture import EffectAttemptRecord
from tests.effect_outcome_evidence_fixture import (
    ObservedEffectOutcome,
    WORKSPACE_ID,
    effect_outcome_failure,
    effect_outcome_transition,
)
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_fold_fixture import (
    PostgresEffectAttemptFoldFixture,
)


class PostgresGuardedObservedEffectFoldFixture(
    PostgresEffectAttemptFoldFixture,
):
    """Exact persisted worlds for the guarded observation transaction."""

    def observed_stories(self):
        return tuple(
            story
            for story in self.stories()
            if story.profile == "provider-observation"
        )

    def observed_story(self, name="observed-succeeded", *, compensation=False):
        return next(
            story
            for story in self.observed_stories()
            if story.name == name and story.compensation is compensation
        )

    def persisted_intent(self, current, *, authority_ref=True):
        intent = self.intent_for_attempt(
            compensation=(
                current.original_start_event.kind
                is ActivityEventKind.STEP_COMPENSATION_STARTED
            ),
            run_id=current.state.identity.run_id.value,
            activity_id=current.state.identity.activity_id,
        )
        if authority_ref is False:
            intent = replace(intent, authority_ref=None, authority_deliveries=())
        return intent

    def intent_record(self, current, *, intent=None):
        value = self.persisted_intent(current) if intent is None else intent
        return EffectAttemptIntentRecord(
            current.state.identity,
            current.original_start_event,
            value,
        )

    def register_runtime_authority(self, intent, *, remote=False):
        if intent.authority_ref is None:
            return None
        authority = (
            RemoteDockerTlsAuthority(
                endpoint="tcp://mac-mini.local:2376",
                ca_certificate=SecretReference("secret://local/docker/ca"),
                client_certificate=SecretReference("secret://local/docker/cert"),
                client_key=SecretReference("secret://local/docker/key"),
            )
            if remote
            else LocalDockerSocketAuthority()
        )
        with self.unit_of_work() as unit_of_work:
            registered = unit_of_work.stores.runtime_authorities.register(
                workspace_id=intent.source.workspace_id,
                authority_ref=intent.authority_ref,
                runtime_kind=RuntimeKind.DOCKER,
                authority=authority,
                admitted_by="operator-a",
                admitted_at="2030-01-01T00:00:00Z",
            )
            unit_of_work.commit()
        return registered

    def guarded_observed_command(
        self,
        story=None,
        *,
        current=None,
        intent=None,
        intent_record=None,
        runtime_authority=None,
        fold=None,
        register=True,
    ):
        story = story or self.observed_story()
        current = current or self.current_attempt()
        intent = intent or self.persisted_intent(current)
        intent_record = intent_record or self.intent_record(current, intent=intent)
        if runtime_authority is None and register and intent.authority_ref is not None:
            runtime_authority = self.register_runtime_authority(intent)
        if fold is None:
            observation = replace(
                story.value,
                effect_id=current.original_start_event.event_id,
                request_fingerprint=runtime_effect_intent_fingerprint(intent),
            )
            outcome = ObservedEffectOutcome(current.state.identity, observation)
            fold = self.fold_command(
                story,
                request_id=intent.source.request_id,
                transition=effect_outcome_transition(outcome),
                failure=effect_outcome_failure(outcome),
                outcome=outcome,
            )
        return GuardedObservedEffectFold(
            fold,
            intent_record,
            runtime_authority,
        )

    def seed_guarded_source(self, story=None, *, authority_ref=True):
        story = story or self.observed_story()
        self._fold_compensation = story.compensation
        self._fold_outcome_story = story
        self.reset_start_truth(compensation=story.compensation)
        current = self.record(
            "started",
            compensation=story.compensation,
            run_id="run-a",
            activity_id="start-runtime",
            event_prefix=f"effect-{int(story.compensation)}",
            original_ordinal=7 if story.compensation else 3,
            original_time="2030-01-01T00:00:00Z",
        )
        intent = self.persisted_intent(current, authority_ref=authority_ref)
        state = replace(
            current.state,
            request_fingerprint=runtime_effect_intent_fingerprint(intent),
        )
        event = replace(current.original_start_event, evidence=self.evidence_for(state))
        current = EffectAttemptRecord(state, event, event)
        self.persist(current, intent=intent)
        expected = self.intent_record(current, intent=intent)
        return current, intent, expected

    def observed_service(self, *ids):
        return self.fold_service_with_sequence(*ids)

    def persist_terminal(self, story):
        current = self.seed_fold_source(story, compensation=story.compensation)
        command = self.fold_command(story)
        state = self.expected_fold_state(current, story)
        event_id = f"terminal-{story.name}-{int(story.compensation)}"
        event = ActivityEventRecord(
            event_id,
            state.identity.run_id.value,
            current.latest_transition_event.ordinal + 1,
            self.event_kind(
                command.transition.kind.value,
                compensation=story.compensation,
            ),
            "2030-01-01T00:00:01Z",
            activity_id=state.identity.activity_id,
            evidence=BoundedEvidence.from_mapping(
                {
                    "effect_attempt": EffectAttemptEventEvidence(
                        state.identity.attempt,
                        effect_attempt_state_fingerprint(state),
                    ).descriptor()
                }
            ),
            failure=command.failure,
        )
        attempt = EffectAttemptRecord(state, current.original_start_event, event)
        outcome_record = self.expected_outcome_record(
            attempt,
            command.outcome,
            event_id=event_id,
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            self.assertEqual(stores.execution.add_event(event), event)
            for observation in outcome_record.endpoint_observations:
                self.assertEqual(stores.observed_state.put(observation), observation)
            self.assertEqual(stores.effect_outcomes.insert(outcome_record), outcome_record)
            self.assertEqual(
                stores.effect_attempts.compare_and_set(current, attempt),
                attempt,
            )
            unit_of_work.commit()
        return attempt, outcome_record

    def forbidden_lower_interactions(self, message):
        forbidden = AssertionError(message)
        return (
            mock.patch.object(EffectAttemptOutcomeStore, "get", side_effect=forbidden),
            mock.patch.object(EffectAttemptIntentStore, "get", side_effect=forbidden),
            mock.patch.object(
                PostgresExecutionStore,
                "observe_request_lease_for_update",
                side_effect=forbidden,
            ),
            mock.patch.object(
                RuntimeAuthorityStore,
                "get_active_for_update",
                side_effect=forbidden,
                create=True,
            ),
            mock.patch.object(PostgresExecutionStore, "add_event", side_effect=forbidden),
            mock.patch.object(PostgresObservedStateStore, "put", side_effect=forbidden),
            mock.patch.object(EffectAttemptOutcomeStore, "insert", side_effect=forbidden),
            mock.patch.object(EffectAttemptStore, "compare_and_set", side_effect=forbidden),
        )

    def complete_snapshot(self):
        return self.attempt_snapshot(), self.non_advancement_snapshot()

    def fold_ids_for_story(self, label, story):
        outcome = self.fold_outcome(story)
        return (
            label,
            *tuple(
                f"{label}-observation-{position}"
                for position, _ in enumerate(outcome.endpoint_observations, start=1)
            ),
        )


__all__ = [
    "ActivityEventRecord",
    "EffectAttemptIntentRecord",
    "EffectAttemptRecord",
    "EffectAttemptStore",
    "EffectAttemptOutcomeStore",
    "PostgresExecutionStore",
    "PostgresGuardedObservedEffectFoldFixture",
    "PostgresObservedStateStore",
    "RegisteredRuntimeAuthorityStatus",
    "RuntimeAuthorityStore",
    "Sequence",
    "WORKSPACE_ID",
    "runtime_effect_intent_fingerprint",
]
