from __future__ import annotations

from dataclasses import replace
import importlib

import rfc8785

from control_plane_kit_core.operations import (
    EffectAttemptState,
    EffectAttemptStatus,
    EffectRecoveryDecision,
    EffectRecoveryResolution,
    RecoveryDecisionKind,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord,
    ExecutionEffectOutcome,
    effect_outcome_observation_records,
)
from tests.effect_outcome_evidence_fixture import (
    EffectOutcomeEvidenceFixture,
    REQUEST_FINGERPRINT,
    WORKSPACE_ID,
)
from tests.effect_attempt_intent_fixture import EffectAttemptIntentFixture
from tests.execution_lease_recovery_fixture import (
    PostgresExecutionLeaseRecoveryFixture,
)


MODULE_NAME = "control_plane_kit_operations.postgres.effect_outcome_store"


def _load_module(import_module=importlib.import_module):
    try:
        return import_module(MODULE_NAME)
    except ModuleNotFoundError as error:
        if error.name != MODULE_NAME:
            raise
        return None


store_module = _load_module()
EffectAttemptOutcomeStore = getattr(store_module, "EffectAttemptOutcomeStore", None)


class PostgresEffectOutcomeStoreFixture(
    EffectOutcomeEvidenceFixture,
    PostgresExecutionLeaseRecoveryFixture,
):
    def setUp(self) -> None:
        PostgresExecutionLeaseRecoveryFixture.setUp(self)
        self.seed_truth(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            history="active-empty",
        )

    def tearDown(self) -> None:
        PostgresExecutionLeaseRecoveryFixture.tearDown(self)

    def require_store(self) -> None:
        self.assertIsNotNone(
            EffectAttemptOutcomeStore,
            "effect-attempt outcome store is missing",
        )

    def story_named(self, name: str, *, compensation: bool = False):
        return next(
            story
            for story in self.stories()
            if story.name == name and story.compensation is compensation
        )

    def record_for(self, story) -> EffectAttemptOutcomeRecord:
        outcome = self.outcome_for(story)
        observations = effect_outcome_observation_records(
            outcome,
            story.attempt,
            workspace_id=WORKSPACE_ID,
            observation_ids=self.observation_ids(story),
        )
        return EffectAttemptOutcomeRecord(
            WORKSPACE_ID,
            outcome,
            story.attempt,
            observations,
        )

    def indexed_record(
        self,
        index: int,
        *,
        story_name: str = "observed-absent",
        observation_ids: tuple[str, ...] | None = None,
    ) -> EffectAttemptOutcomeRecord:
        story = self.story_named(story_name)
        start_event_id = f"page-{index:03d}-start"
        direct_event_id = f"page-{index:03d}-direct"
        value = replace(story.value, effect_id=start_event_id)
        indexed = replace(story, value=value)
        indexed = replace(
            indexed,
            attempt=self.direct_attempt_for(
                indexed,
                identity=self.identity(activity_id=f"activity-{index:03d}"),
                original_event_id=start_event_id,
                latest_event_id=direct_event_id,
                original_ordinal=10 + index * 2,
                latest_ordinal=11 + index * 2,
            ),
        )
        outcome = self.outcome_for(indexed)
        observations = effect_outcome_observation_records(
            outcome,
            indexed.attempt,
            workspace_id=WORKSPACE_ID,
            observation_ids=(
                tuple(
                    f"page-{index:03d}-observation-{position}"
                    for position, _ in enumerate(
                        indexed.endpoint_observations,
                        start=1,
                    )
                )
                if observation_ids is None
                else observation_ids
            ),
        )
        return EffectAttemptOutcomeRecord(
            WORKSPACE_ID,
            outcome,
            indexed.attempt,
            observations,
        )

    def indexed_empty_record(self, index: int) -> EffectAttemptOutcomeRecord:
        return self.indexed_record(index)

    def retry_record(self) -> tuple[EffectAttemptRecord, EffectAttemptOutcomeRecord]:
        story = self.story_named("execution-succeeded")
        identity = self.identity(attempt=2)
        prior_identity = self.identity(attempt=1)
        value = replace(story.value, effect_id="retry-direct-start")
        outcome = ExecutionEffectOutcome(identity, REQUEST_FINGERPRINT, value)
        state = EffectAttemptState(
            identity=identity,
            request_fingerprint=REQUEST_FINGERPRINT,
            fence=story.attempt.state.fence,
            status=EffectAttemptStatus.SUCCEEDED,
            outcome_fingerprint=outcome.outcome_fingerprint,
            prior_attempt=prior_identity,
        )
        original = self.event(
            self.started_state(state),
            self.event_kind("started", compensation=False),
            event_id="retry-direct-start",
            ordinal=5,
            occurred_at="2030-01-01T00:00:01Z",
        )
        latest = self.event(
            state,
            self.event_kind("succeeded", compensation=False),
            event_id="retry-direct-succeeded",
            ordinal=7,
            occurred_at="2030-01-01T00:00:02Z",
        )
        attempt = EffectAttemptRecord(state, original, latest)
        observations = effect_outcome_observation_records(
            outcome,
            attempt,
            workspace_id=WORKSPACE_ID,
            observation_ids=("retry-observation-a", "retry-observation-b"),
        )
        prior = self.record(
            "started",
            event_prefix="retry-prior",
            original_ordinal=3,
            original_time="2030-01-01T00:00:00Z",
        )
        return (
            prior,
            EffectAttemptOutcomeRecord(
                WORKSPACE_ID,
                outcome,
                attempt,
                observations,
            ),
        )

    def preimage_for(self, record: EffectAttemptOutcomeRecord) -> bytes:
        value = (
            record.outcome.result
            if record.outcome.__class__ is ExecutionEffectOutcome
            else record.outcome.observation
        )
        return rfc8785.dumps(value.descriptor())

    def persist_prerequisites(self, record: EffectAttemptOutcomeRecord) -> None:
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.add_event(record.attempt.original_start_event)
            stores.execution.add_event(record.attempt.latest_transition_event)
            if hasattr(stores, "effect_attempt_intents"):
                compensation = record.attempt.original_start_event.kind.value.startswith(
                    "step_compensation"
                )
                intent = EffectAttemptIntentFixture().intent(
                    compensation=compensation,
                    run_id=record.attempt.state.identity.run_id.value,
                    activity_id=record.attempt.state.identity.activity_id,
                )
                evidence = EffectAttemptIntentRecord(
                    record.attempt.state.identity,
                    record.attempt.original_start_event,
                    intent,
                )
                self.assertEqual(
                    evidence.request_fingerprint,
                    record.attempt.state.request_fingerprint,
                )
                self.assertEqual(
                    stores.effect_attempt_intents.insert(evidence),
                    evidence,
                )
            self.assertEqual(
                stores.effect_attempts.insert_absent(record.attempt),
                record.attempt,
            )
            for observation in record.endpoint_observations:
                self.assertEqual(stores.observed_state.put(observation), observation)
            unit_of_work.commit()

    def persist_outcome(self, record: EffectAttemptOutcomeRecord):
        self.persist_prerequisites(record)
        with self.unit_of_work() as unit_of_work:
            inserted = unit_of_work.stores.effect_outcomes.insert(record)
            unit_of_work.commit()
        return inserted

    def recover_current_attempt(
        self,
        record: EffectAttemptOutcomeRecord,
    ) -> EffectAttemptRecord:
        current = record.attempt
        recovered_fingerprint = "f" * 64
        decision = EffectRecoveryDecision(
            "decision-later-recovery",
            current.state.identity,
            EffectRecoveryResolution.SUCCEEDED,
            current.state.outcome_fingerprint,
            recovered_fingerprint,
        )
        recovered = EffectAttemptState(
            identity=current.state.identity,
            request_fingerprint=current.state.request_fingerprint,
            fence=current.state.fence,
            status=EffectAttemptStatus.SUCCEEDED,
            outcome_fingerprint=recovered_fingerprint,
            prior_attempt=current.state.prior_attempt,
            recovery_decision=decision,
        )
        latest = self.event(
            recovered,
            self.event_kind(
                "recovered-succeeded",
                compensation=current.original_start_event.kind.value.startswith(
                    "step_compensation"
                ),
            ),
            event_id="event-recovered-succeeded",
            ordinal=current.latest_transition_event.ordinal + 1,
            occurred_at="2030-01-01T00:00:03Z",
        )
        replacement = EffectAttemptRecord(
            recovered,
            current.original_start_event,
            latest,
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.add_event(latest)
            self.assertEqual(
                stores.effect_attempts.compare_and_set(current, replacement),
                replacement,
            )
            unit_of_work.commit()
        return replacement


__all__ = [
    "EffectAttemptOutcomeStore",
    "MODULE_NAME",
    "PostgresEffectOutcomeStoreFixture",
    "store_module",
]
