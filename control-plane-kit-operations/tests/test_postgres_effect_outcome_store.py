from __future__ import annotations

from dataclasses import replace
import json
import unittest

import psycopg
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from control_plane_kit_core import RuntimeEffectResult
from control_plane_kit_core.operations import RecoveryDecisionKind
from control_plane_kit_operations.postgres import (
    SchemaInstallationError,
    install_schema,
)
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord,
    effect_outcome_observation_records,
)
from tests.postgres_effect_outcome_store_fixture import (
    PostgresEffectOutcomeStoreFixture,
    store_module,
)


OUTCOME = "cpk_effect_attempt_outcomes"
MEMBERSHIP = "cpk_effect_attempt_outcome_observations"


class _TransportCursor:
    def __init__(self, cursor, query, oversized) -> None:
        self.cursor = cursor
        self.query = str(query)
        self.oversized = oversized

    def _record(self, rows) -> None:
        if not any(
            relation in self.query
            for relation in (OUTCOME, MEMBERSHIP)
        ):
            return
        for row in rows:
            for value in row:
                if type(value) is bytes and len(value) > 8_192:
                    self.oversized.append((self.query, "bytes"))
                if type(value) is dict and len(
                    json.dumps(
                        value,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("ascii")
                ) > 8_192:
                    self.oversized.append((self.query, "json"))

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is not None:
            self._record((row,))
        return row

    def fetchall(self):
        rows = self.cursor.fetchall()
        self._record(rows)
        return rows

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class _TransportConnection:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.oversized = []

    def execute(self, query, parameters=None):
        cursor = (
            self.connection.execute(query)
            if parameters is None
            else self.connection.execute(query, parameters)
        )
        return _TransportCursor(cursor, query, self.oversized)

    def __getattr__(self, name):
        return getattr(self.connection, name)


class PostgresEffectOutcomeStoreTests(
    PostgresEffectOutcomeStoreFixture,
    unittest.TestCase,
):
    def test_predecessor_seed_and_attempt_prerequisites_are_lawful(self) -> None:
        self.assertEqual(
            self.connection.execute(
                "SELECT workspace_id FROM cpk_workspaces WHERE workspace_id='workspace-a'"
            ).fetchone(),
            ("workspace-a",),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT request_id FROM cpk_execution_requests "
                "WHERE request_id='request-a'"
            ).fetchone(),
            ("request-a",),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT run_id FROM cpk_activity_runs WHERE run_id='run-a'"
            ).fetchone(),
            ("run-a",),
        )
        record = self.record_for(self.story_named("execution-succeeded"))
        self.persist_prerequisites(record)
        with self.unit_of_work() as fresh:
            self.assertEqual(
                fresh.stores.effect_attempts.get(record.attempt.state.identity),
                record.attempt,
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_observations WHERE observation_id = ANY(%s)",
                ([value.observation_id for value in record.endpoint_observations],),
            ).fetchone(),
            (len(record.endpoint_observations),),
        )

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

    def test_attempt_two_immediate_prior_roundtrips_after_restart(self) -> None:
        self.require_store()
        prior, record = self.retry_record()
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.add_event(prior.original_start_event)
            self.assertEqual(stores.effect_attempts.insert_absent(prior), prior)
            stores.execution.add_event(record.attempt.original_start_event)
            stores.execution.add_event(record.attempt.latest_transition_event)
            self.assertEqual(
                stores.effect_attempts.insert_absent(record.attempt),
                record.attempt,
            )
            for observation in record.endpoint_observations:
                self.assertEqual(stores.observed_state.put(observation), observation)
            self.assertEqual(stores.effect_outcomes.insert(record), record)
            unit_of_work.commit()

        with self.unit_of_work() as fresh:
            loaded = fresh.stores.effect_outcomes.get(
                record.attempt.state.identity,
                record.attempt.latest_transition_event.event_id,
            )
        self.assertEqual(loaded, record)
        self.assertEqual(loaded.attempt.state.identity.attempt, 2)
        self.assertEqual(loaded.attempt.state.prior_attempt, prior.state.identity)

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
            (
                "linked-observation-body",
                "UPDATE cpk_observations SET evidence="
                "'{\"runtime_endpoint\":{\"candidate\":\"linked-body\"}}'::jsonb "
                "WHERE observation_id=%s",
                (record.endpoint_observations[0].observation_id,),
            ),
            (
                "original-event-payload",
                "UPDATE cpk_activity_events SET payload=jsonb_set("
                "payload, '{evidence,effect_attempt,state_fingerprint}', "
                "to_jsonb(%s::text)) WHERE event_id=%s",
                ("f" * 64, record.attempt.original_start_event.event_id),
            ),
            (
                "direct-event-payload",
                "UPDATE cpk_activity_events SET payload=jsonb_set("
                "payload, '{evidence,effect_attempt,state_fingerprint}', "
                "to_jsonb(%s::text)) WHERE event_id=%s",
                ("f" * 64, record.attempt.latest_transition_event.event_id),
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
                self.assert_safe_error(
                    caught.exception,
                    "private-canary",
                    "linked-body",
                    "f" * 64,
                )

        self.reset_truth(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            history="active-empty",
        )
        self.persist_outcome(record)
        extra = replace(
            record.endpoint_observations[1],
            observation_id="observation-actual-extra",
        )
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(unit_of_work.stores.observed_state.put(extra), extra)
            unit_of_work.commit()
        self.connection.execute(f"DELETE FROM {MEMBERSHIP}")
        self.connection.execute(f"UPDATE {OUTCOME} SET observation_count=3")
        for position, observation in enumerate((*record.endpoint_observations, extra)):
            self.connection.execute(
                f"INSERT INTO {MEMBERSHIP} "
                "(run_id, activity_id, attempt, workspace_id, observation_count, "
                "position, observation_id) VALUES (%s, %s, %s, %s, 3, %s, %s)",
                (
                    record.attempt.state.identity.run_id.value,
                    record.attempt.state.identity.activity_id,
                    record.attempt.state.identity.attempt,
                    record.workspace_id,
                    position,
                    observation.observation_id,
                ),
            )
        with self.unit_of_work() as fresh:
            with self.assertRaises(OperationsRecordError) as caught:
                fresh.stores.effect_outcomes.get(
                    record.attempt.state.identity,
                    record.attempt.latest_transition_event.event_id,
                )
        self.assertEqual(str(caught.exception), "effect attempt outcome row is invalid")
        self.assert_safe_error(caught.exception, "observation-actual-extra")

    def test_strict_inner_rfc8785_codec_rejects_admissible_row_drift(self) -> None:
        self.require_store()
        record = self.record_for(self.story_named("execution-succeeded"))
        valid_preimage = self.preimage_for(record)
        first_member_end = valid_preimage.index(b",")
        duplicate_key = (
            b"{"
            + valid_preimage[1:first_member_end]
            + b","
            + valid_preimage[1:]
        )
        finite_story = self.story_named("execution-succeeded")
        finite_story = replace(
            finite_story,
            value=RuntimeEffectResult.succeeded(
                finite_story.value.effect_id,
                evidence={"finite-number": 1.5},
                observations=finite_story.value.observations,
            ),
        )
        finite_story = replace(
            finite_story,
            attempt=self.direct_attempt_for(finite_story),
        )
        finite_record = self.record_for(finite_story)
        finite_preimage = self.preimage_for(finite_record)
        self.assertIn(b"1.5", finite_preimage)
        self.assertEqual(self.persist_outcome(finite_record), finite_record)
        mutations = (
            ("utf8", "preimage=%s", (b"\xff",)),
            ("duplicate-key", "preimage=%s", (duplicate_key,)),
            (
                "nonfinite",
                "preimage=%s",
                (finite_preimage.replace(b"1.5", b"NaN", 1),),
            ),
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
                candidate_record = finite_record if label == "nonfinite" else record
                self.persist_outcome(candidate_record)
                self.connection.execute(
                    f"UPDATE {OUTCOME} SET {assignment}",
                    parameters,
                )
                with self.unit_of_work() as fresh:
                    with self.assertRaises(OperationsRecordError) as caught:
                        fresh.stores.effect_outcomes.get(
                            candidate_record.attempt.state.identity,
                            candidate_record.attempt.latest_transition_event.event_id,
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

    def test_oversized_values_are_not_transferred_to_python_decoders(self) -> None:
        self.require_store()
        record = self.record_for(self.story_named("execution-succeeded"))
        self.persist_outcome(record)

        with self.subTest(boundary="outcome-preimage-get"):
            connection = psycopg.connect(self.database_url)
            try:
                connection.execute(
                    f"ALTER TABLE {OUTCOME} DROP CONSTRAINT "
                    "cpk_effect_attempt_outcomes_preimage_check"
                )
                connection.execute(
                    f"UPDATE {OUTCOME} SET preimage=%s",
                    (b"x" * 8_193,),
                )
                traced = _TransportConnection(connection)
                with self.assertRaises(OperationsRecordError) as caught:
                    store_module.EffectAttemptOutcomeStore(traced).get(
                        record.attempt.state.identity,
                        record.attempt.latest_transition_event.event_id,
                    )
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt outcome row is invalid",
                )
                self.assertEqual(traced.oversized, [])
            finally:
                connection.rollback()
                connection.close()

        with self.subTest(boundary="outcome-preimage-current-verifier"):
            connection = psycopg.connect(self.database_url)
            try:
                connection.execute(
                    f"ALTER TABLE {OUTCOME} DROP CONSTRAINT "
                    "cpk_effect_attempt_outcomes_preimage_check"
                )
                connection.execute(
                    f"UPDATE {OUTCOME} SET preimage=%s",
                    (b"x" * 8_193,),
                )
                traced = _TransportConnection(connection)
                with self.assertRaises(OperationsRecordError) as caught:
                    store_module._validate_current_rows(traced)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt outcome row is invalid",
                )
                self.assertEqual(traced.oversized, [])
            finally:
                connection.rollback()
                connection.close()

        self.connection.execute(
            "UPDATE cpk_observations SET evidence=%s::jsonb "
            "WHERE observation_id=%s",
            (
                json.dumps({"candidate": "x" * 8_193}),
                record.endpoint_observations[0].observation_id,
            ),
        )
        with self.subTest(boundary="linked-observation-get"):
            traced = _TransportConnection(self.connection)
            with self.assertRaises(OperationsRecordError) as caught:
                store_module.EffectAttemptOutcomeStore(traced).get(
                    record.attempt.state.identity,
                    record.attempt.latest_transition_event.event_id,
                )
            self.assertEqual(
                str(caught.exception),
                "effect attempt outcome row is invalid",
            )
            self.assertEqual(traced.oversized, [])

        with self.subTest(boundary="linked-observation-current-verifier"):
            traced = _TransportConnection(self.connection)
            with self.assertRaises(SchemaInstallationError) as caught:
                install_schema(traced)
            self.assertEqual(
                str(caught.exception),
                "operations schema reset is required",
            )
            self.assertEqual(traced.oversized, [])

    def test_deep_bounded_json_is_categorical_not_recursion_error(self) -> None:
        self.require_store()
        record = self.record_for(self.story_named("execution-succeeded"))
        valid = self.preimage_for(record)
        evidence_start = valid.index(b'"evidence":') + len(b'"evidence":')
        evidence_end = valid.index(b',"failure"', evidence_start)
        nested = b"[" * 1_100 + b"0" + b"]" * 1_100
        candidate = valid[:evidence_start] + nested + valid[evidence_end:]
        self.assertLessEqual(len(candidate), 8_192)
        self.persist_outcome(record)
        self.connection.execute(
            f"UPDATE {OUTCOME} SET preimage=%s",
            (candidate,),
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
        self.assert_safe_error(caught.exception, "nested")

    def test_unexpected_codec_faults_remain_raw(self) -> None:
        self.require_store()
        record = self.record_for(self.story_named("observed-absent"))
        self.persist_outcome(record)
        for error in (
            TypeError("codec-type-canary"),
            RuntimeError("codec-runtime-canary"),
        ):
            with self.subTest(error=type(error).__name__):
                original = store_module._decode_preimage

                def raise_fault(*_args, error=error, **_kwargs):
                    raise error

                store_module._decode_preimage = raise_fault
                try:
                    with self.unit_of_work() as fresh:
                        with self.assertRaises(type(error)) as raw:
                            fresh.stores.effect_outcomes.get(
                                record.attempt.state.identity,
                                record.attempt.latest_transition_event.event_id,
                            )
                finally:
                    store_module._decode_preimage = original
                self.assertIs(raw.exception, error)

    def test_observation_identity_is_relation_wide_unique(self) -> None:
        self.require_store()
        shared = ("observation-shared-a", "observation-shared-b")
        first = self.indexed_record(
            0,
            story_name="execution-succeeded",
            observation_ids=shared,
        )
        second = self.indexed_record(
            1,
            story_name="execution-succeeded",
            observation_ids=shared,
        )
        self.assertEqual(first.endpoint_observations, second.endpoint_observations)
        self.persist_outcome(first)
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.add_event(second.attempt.original_start_event)
            stores.execution.add_event(second.attempt.latest_transition_event)
            stores.effect_attempts.insert_absent(second.attempt)
            with self.assertRaises(UniqueViolation) as caught:
                stores.effect_outcomes.insert(second)
        self.assertEqual(
            caught.exception.diag.constraint_name,
            "cpk_effect_attempt_outcome_observations_observation_key",
        )

        self.reset_truth(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            history="active-empty",
        )
        first = self.indexed_record(0, story_name="execution-succeeded")
        second = self.indexed_record(1, story_name="execution-succeeded")
        self.assertEqual(self.persist_outcome(first), first)
        self.assertEqual(self.persist_outcome(second), second)

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
