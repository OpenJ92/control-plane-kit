from __future__ import annotations

from dataclasses import replace
import unittest

import psycopg
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from control_plane_kit_core.operations import RecoveryDecisionKind
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord,
    effect_outcome_observation_records,
)
from tests.postgres_effect_outcome_store_fixture import (
    PostgresEffectOutcomeStoreFixture,
)


OUTCOME = "cpk_effect_attempt_outcomes"
MEMBERSHIP = "cpk_effect_attempt_outcome_observations"


class PostgresEffectOutcomeStoreTests(
    PostgresEffectOutcomeStoreFixture,
    unittest.TestCase,
):
    def test_all_twenty_direct_post_transition_rows_roundtrip_after_restart(self) -> None:
        self.require_store()
        for story in self.stories():
            with self.subTest(story=story.name, compensation=story.compensation):
                self.reset_truth(
                    RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                    history="active-empty",
                )
                record = self.record_for(story)
                self.assertEqual(self.persist_outcome(record), record)
                with self.unit_of_work() as fresh:
                    loaded = fresh.stores.effect_outcomes.get(
                        record.attempt.state.identity,
                        record.attempt.latest_transition_event.event_id,
                    )
                self.assertIs(type(loaded), type(record))
                self.assertEqual(loaded, record)
                self.assertEqual(
                    self.connection.execute(
                        f"SELECT preimage FROM {OUTCOME}"
                    ).fetchone()[0],
                    self.preimage_for(record),
                )

    def test_uncertain_direct_snapshot_survives_later_current_recovery(self) -> None:
        self.require_store()
        story = self.story_named("execution-uncertain")
        record = self.record_for(story)
        self.assertEqual(self.persist_outcome(record), record)
        recovered = self.recover_current_attempt(record)
        self.assertIsNotNone(recovered.state.recovery_decision)

        with self.unit_of_work() as fresh:
            loaded = fresh.stores.effect_outcomes.get(
                record.attempt.state.identity,
                record.attempt.latest_transition_event.event_id,
            )
        self.assertEqual(loaded, record)
        self.assertIsNone(loaded.attempt.state.recovery_decision)
        self.assertEqual(loaded.attempt.state.status.value, "uncertain")

    def test_ordered_membership_is_exact_and_linked_observations_are_retained(self) -> None:
        self.require_store()
        record = self.record_for(self.story_named("execution-succeeded"))
        self.persist_outcome(record)
        rows = tuple(
            self.connection.execute(
                f"SELECT position, observation_id, observation_count "
                f"FROM {MEMBERSHIP} ORDER BY position"
            ).fetchall()
        )
        self.assertEqual(
            rows,
            tuple(
                (index, observation.observation_id, len(record.endpoint_observations))
                for index, observation in enumerate(record.endpoint_observations)
            ),
        )
        with self.assertRaises(ForeignKeyViolation) as caught:
            self.connection.execute(
                "DELETE FROM cpk_observations WHERE observation_id=%s",
                (record.endpoint_observations[0].observation_id,),
            )
        self.assertEqual(
            caught.exception.diag.constraint_name,
            "cpk_effect_attempt_outcome_observations_observation_fk",
        )

        unrelated = replace(
            record.endpoint_observations[0],
            observation_id="observation-unrelated",
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.observed_state.put(unrelated)
            unit_of_work.commit()
        self.connection.execute(
            "DELETE FROM cpk_observations WHERE observation_id='observation-unrelated'"
        )

    def test_missing_extra_reordered_and_payload_drift_are_categorical(self) -> None:
        self.require_store()
        record = self.record_for(self.story_named("execution-succeeded"))
        mutations = (
            (
                "missing",
                f"DELETE FROM {MEMBERSHIP} WHERE position=1",
                (),
            ),
            (
                "reordered",
                f"UPDATE {MEMBERSHIP} SET observation_id = CASE position "
                "WHEN 0 THEN %s ELSE %s END",
                (
                    record.endpoint_observations[1].observation_id,
                    record.endpoint_observations[0].observation_id,
                ),
            ),
            (
                "payload",
                f"UPDATE {OUTCOME} SET preimage=%s",
                (b'{"kind":"private-canary"}',),
            ),
        )
        for label, statement, parameters in mutations:
            with self.subTest(label=label):
                self.reset_truth(
                    RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                    history="active-empty",
                )
                self.persist_outcome(record)
                self.connection.execute(statement, parameters)
                with self.unit_of_work() as fresh:
                    with self.assertRaises(OperationsRecordError) as caught:
                        fresh.stores.effect_outcomes.get(
                            record.attempt.state.identity,
                            record.attempt.latest_transition_event.event_id,
                        )
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt outcome row is invalid",
                )
                self.assert_safe_error(caught.exception, "private-canary")

    def test_strict_inner_rfc8785_codec_rejects_admissible_row_drift(self) -> None:
        self.require_store()
        record = self.record_for(self.story_named("execution-succeeded"))
        valid_preimage = self.preimage_for(record)
        mutations = (
            ("utf8", "preimage=%s", (b"\xff",)),
            ("duplicate-key", "preimage=%s", (b'{"x":1,"x":2}',)),
            ("nonfinite", "preimage=%s", (b'{"x":NaN}',)),
            ("nonobject", "preimage=%s", (b"[]",)),
            ("noncanonical", "preimage=%s", (b" " + valid_preimage,)),
            ("profile", "profile='provider-observation'", ()),
            ("request-fingerprint", "request_fingerprint=%s", ("b" * 64,)),
            ("outcome-fingerprint", "outcome_fingerprint=%s", ("c" * 64,)),
        )
        for label, assignment, parameters in mutations:
            with self.subTest(label=label):
                self.reset_truth(
                    RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                    history="active-empty",
                )
                self.persist_outcome(record)
                self.connection.execute(
                    f"UPDATE {OUTCOME} SET {assignment}",
                    parameters,
                )
                with self.unit_of_work() as fresh:
                    with self.assertRaises(OperationsRecordError) as caught:
                        fresh.stores.effect_outcomes.get(
                            record.attempt.state.identity,
                            record.attempt.latest_transition_event.event_id,
                        )
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt outcome row is invalid",
                )
                self.assert_safe_error(
                    caught.exception,
                    "NaN",
                    "provider-observation",
                    "b" * 64,
                    "c" * 64,
                )

    def test_exact_8192_byte_preimage_roundtrips_and_workspace_is_derived(self) -> None:
        self.require_store()
        story = self.story_named("execution-succeeded")
        bounded = replace(story, value=self.live_result_for_size(8_192))
        bounded = replace(bounded, attempt=self.direct_attempt_for(bounded))
        record = self.record_for(bounded)
        self.assertEqual(len(self.preimage_for(record)), 8_192)
        self.assertEqual(self.persist_outcome(record), record)

        self.reset_truth(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            history="active-empty",
        )
        empty_story = self.story_named("observed-absent")
        outcome = self.outcome_for(empty_story)
        observations = effect_outcome_observation_records(
            outcome,
            empty_story.attempt,
            workspace_id="workspace-foreign",
            observation_ids=(),
        )
        foreign = EffectAttemptOutcomeRecord(
            "workspace-foreign",
            outcome,
            empty_story.attempt,
            observations,
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.add_event(foreign.attempt.original_start_event)
            stores.execution.add_event(foreign.attempt.latest_transition_event)
            stores.effect_attempts.insert_absent(foreign.attempt)
            unit_of_work.commit()
        with self.unit_of_work() as unit_of_work:
            with self.assertRaises(OperationsRecordError) as caught:
                unit_of_work.stores.effect_outcomes.insert(foreign)
        self.assertEqual(
            str(caught.exception),
            "effect attempt outcome store input is invalid",
        )
        self.assert_safe_error(caught.exception, "workspace-foreign")
        self.assertEqual(
            self.connection.execute(f"SELECT count(*) FROM {OUTCOME}").fetchone(),
            (0,),
        )

    def test_conflicting_insert_is_raw_and_uncommitted_insert_rolls_back(self) -> None:
        self.require_store()
        record = self.record_for(self.story_named("observed-absent"))
        self.persist_outcome(record)
        with self.unit_of_work() as unit_of_work:
            with self.assertRaises(UniqueViolation):
                unit_of_work.stores.effect_outcomes.insert(record)

        self.reset_truth(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            history="active-empty",
        )
        self.persist_prerequisites(record)
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(unit_of_work.stores.effect_outcomes.insert(record), record)
        self.assertEqual(
            self.connection.execute(f"SELECT count(*) FROM {OUTCOME}").fetchone(),
            (0,),
        )
        self.assertEqual(
            self.connection.execute(f"SELECT count(*) FROM {MEMBERSHIP}").fetchone(),
            (0,),
        )


if __name__ == "__main__":
    unittest.main()
