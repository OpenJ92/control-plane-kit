from __future__ import annotations

from dataclasses import replace

from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from tests.effect_attempt_fold_fixture import (
    EffectAttemptFoldFixture,
    ExistingFold,
    FoldEffectAttempt,
    NewlyFolded,
)
from tests.effect_outcome_evidence_fixture import (
    EffectAttemptOutcomeRecord,
    EffectOutcomeEvidenceFixture,
    WORKSPACE_ID,
    effect_outcome_failure,
    effect_outcome_observation_records,
    effect_outcome_transition,
)


class AtomicEffectAttemptFoldFixture(
    EffectOutcomeEvidenceFixture,
    EffectAttemptFoldFixture,
):
    def direct_outcome_record(self, story):
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

    def direct_command(self, story, **changes):
        outcome = self.outcome_for(story)
        values = {
            "request_id": "request-a",
            "transition": effect_outcome_transition(outcome),
            "authority": self.authority(),
            "fence": self.execution_fence(),
            "failure": effect_outcome_failure(outcome),
            "outcome": outcome,
        }
        values.update(changes)
        return FoldEffectAttempt(**values)

    def recovery_command(self, story: str = "recovered-succeeded", **changes):
        values = {
            "request_id": "request-a",
            "transition": self.transition(story),
            "authority": self.authority(),
            "fence": self.execution_fence(),
            "failure": self.failure(story) if story == "recovered-failed" else None,
            "outcome": None,
        }
        values.update(changes)
        return FoldEffectAttempt(**values)

    def recovery_record(self, story: str, *, compensation: bool):
        record = self.record(story, compensation=compensation)
        if story != "recovered-failed":
            return record
        latest = replace(
            record.latest_transition_event,
            failure=self.failure(story),
        )
        return EffectAttemptRecord(
            record.state,
            record.original_start_event,
            latest,
        )

    def direct_result(self, variant, story):
        return variant(story.attempt, self.direct_outcome_record(story))

    def recovery_result(self, variant, story: str, *, compensation: bool):
        return variant(
            self.recovery_record(story, compensation=compensation),
            None,
        )


__all__ = [
    "AtomicEffectAttemptFoldFixture",
    "ExistingFold",
    "NewlyFolded",
]
