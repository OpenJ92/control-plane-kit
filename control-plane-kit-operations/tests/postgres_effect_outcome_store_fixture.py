from __future__ import annotations

from dataclasses import replace
import importlib

import rfc8785

from control_plane_kit_core.operations import (
    EffectAttemptState,
    EffectAttemptStatus,
    EffectRecoveryDecision,
    EffectRecoveryResolution,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord,
    ExecutionEffectOutcome,
    effect_outcome_observation_records,
)
from tests.effect_outcome_evidence_fixture import (
    EffectOutcomeEvidenceFixture,
    WORKSPACE_ID,
)
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
