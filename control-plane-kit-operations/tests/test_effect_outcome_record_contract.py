from __future__ import annotations

from dataclasses import fields, replace
import inspect
import unittest

from control_plane_kit_core.operations import (
    ActivityEventKind,
    EffectAttemptIdentity,
    EffectAttemptState,
    EffectAttemptStatus,
    RunId,
)
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    ProbeKind,
    ProbeOutcome,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    FailureEvidence,
    ObservationFreshness,
    ObservationRecord,
    ObservationStatus,
    OperationsRecordError,
)

from effect_outcome_evidence_fixture import (
    EffectAttemptOutcomeRecord,
    EffectOutcomeEvidenceFixture,
    HostileActivityEventRecord,
    HostileBoundedEvidence,
    HostileEffectAttemptRecord,
    HostileEffectAttemptState,
    HostileFailureEvidence,
    HostileInt,
    HostileStr,
    REQUEST_FINGERPRINT,
    WORKSPACE_ID,
    effect_outcome_observation_records,
    forge_exact,
)


class EffectOutcomeRecordPredecessorTest(
    EffectOutcomeEvidenceFixture,
    unittest.TestCase,
):
    def test_projection_fixtures_are_valid_existing_records(self) -> None:
        for story in self.stories():
            with self.subTest(story=story.name, compensation=story.compensation):
                records = self.expected_observation_records(story)
                self.assertEqual(len(records), len(story.endpoint_observations))
                self.assertEqual(
                    tuple(record.subject_id for record in records),
                    tuple(value.subject_id for value in story.endpoint_observations),
                )
                self.assertTrue(
                    all(
                        record.observed_at
                        == story.attempt.latest_transition_event.occurred_at
                        for record in records
                    )
                )

    def test_alternate_snapshots_and_row_mutations_are_predecessor_valid(self) -> None:
        story = next(
            item
            for item in self.stories()
            if item.name == "execution-succeeded" and not item.compensation
        )
        candidates = (
            self.direct_attempt_for(
                story,
                identity=EffectAttemptIdentity(RunId("run-b"), "activity-b", 1),
            ),
            self.direct_attempt_for(
                story,
                original_event_id="alternate-start-event",
            ),
            self.direct_attempt_for(story, request_fingerprint="b" * 64),
            self.direct_attempt_for(story, status=EffectAttemptStatus.FAILED),
            self.direct_attempt_for(story, outcome_fingerprint="c" * 64),
            self.recovery_attempt_for(story),
        )
        self.assertTrue(all(type(value) is EffectAttemptRecord for value in candidates))

        rows = self.expected_observation_records(story)
        valid_mutations = (
            replace(rows[0], observation_id="observation-foreign"),
            replace(rows[0], workspace_id="workspace-foreign"),
            replace(rows[0], observed_at="2040-01-01T00:00:00Z"),
            replace(rows[0], subject_id="subject-foreign"),
            replace(rows[0], graph_id="graph-foreign"),
            replace(
                rows[0],
                evidence=BoundedEvidence.from_mapping(
                    {"runtime_endpoint": {"foreign": True}}
                ),
            ),
            replace(
                rows[0],
                probe_kind=ProbeKind.APPLICATION_HEALTH,
                probe_outcome=ProbeOutcome.HEALTHY,
            ),
            replace(rows[0], probe_outcome=ProbeOutcome.REACHABLE),
            replace(rows[0], endpoint_context=EndpointContext.PUBLIC),
            replace(rows[0], status=ObservationStatus.HEALTHY),
            replace(rows[0], freshness=ObservationFreshness.STALE),
        )
        self.assertTrue(
            all(type(value) is ObservationRecord for value in valid_mutations)
        )


class EffectOutcomeRecordContractTest(
    EffectOutcomeEvidenceFixture,
    unittest.TestCase,
):
    def record_for(self, story):
        outcome = self.outcome_for(story)
        observations = effect_outcome_observation_records(
            outcome,
            story.attempt,
            workspace_id=WORKSPACE_ID,
            observation_ids=self.observation_ids(story),
        )
        return EffectAttemptOutcomeRecord(
            workspace_id=WORKSPACE_ID,
            outcome=outcome,
            attempt=story.attempt,
            endpoint_observations=observations,
        )

    def test_record_shape_and_all_twenty_phase_outcomes_are_closed(self) -> None:
        self.require_outcome_language()
        self.assertEqual(
            tuple(field.name for field in fields(EffectAttemptOutcomeRecord)),
            ("workspace_id", "outcome", "attempt", "endpoint_observations"),
        )

        for story in self.stories():
            with self.subTest(story=story.name, compensation=story.compensation):
                record = self.record_for(story)
                self.assertIs(record.attempt, story.attempt)
                self.assertEqual(
                    record.endpoint_observations,
                    self.expected_observation_records(story),
                )
                self.assertEqual(
                    record.descriptor(),
                    {
                        "workspace_id": WORKSPACE_ID,
                        "outcome": record.outcome.descriptor(),
                        "transition_event": {
                            "event_id": story.attempt.latest_transition_event.event_id,
                            "run_id": story.attempt.latest_transition_event.run_id,
                            "ordinal": story.attempt.latest_transition_event.ordinal,
                        },
                        "observation_count": len(story.endpoint_observations),
                    },
                )

    def test_snapshot_binds_identity_start_effect_request_and_direct_state(self) -> None:
        self.require_outcome_language()
        story = next(
            item for item in self.stories() if item.name == "execution-succeeded"
        )
        outcome = self.outcome_for(story)
        rows = self.expected_observation_records(story)
        other_identity = EffectAttemptIdentity(RunId("run-b"), "activity-b", 1)
        cases = (
            self.direct_attempt_for(story, identity=other_identity),
            self.direct_attempt_for(
                story,
                original_event_id="foreign-start-event-canary",
            ),
            self.direct_attempt_for(
                story,
                request_fingerprint="b" * 64,
            ),
            self.direct_attempt_for(
                story,
                status=EffectAttemptStatus.FAILED,
            ),
            self.direct_attempt_for(
                story,
                outcome_fingerprint="c" * 64,
            ),
            self.recovery_attempt_for(story),
        )
        for attempt in cases:
            with self.subTest(attempt=attempt):
                self.assertIs(type(attempt), EffectAttemptRecord)
                self.assert_fixed_error(
                    lambda attempt=attempt: EffectAttemptOutcomeRecord(
                        WORKSPACE_ID,
                        outcome,
                        attempt,
                        rows,
                    ),
                    "effect outcome record is invalid",
                    "foreign-start-event-canary",
                    "run-b",
                    "activity-b",
                    "b" * 64,
                    "decision-a",
                )

    def test_snapshot_rejects_only_impossible_latest_event_joins(self) -> None:
        self.require_outcome_language()
        story = next(item for item in self.stories() if item.name == "execution-failed")
        outcome = self.outcome_for(story)
        base = story.attempt
        cases = (
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=replace(
                    base.latest_transition_event,
                    event_id=base.original_start_event.event_id,
                ),
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=replace(
                    base.latest_transition_event,
                    ordinal=base.original_start_event.ordinal,
                ),
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=replace(
                    base.latest_transition_event,
                    run_id="run-foreign",
                ),
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=replace(
                    base.latest_transition_event,
                    activity_id="activity-foreign",
                ),
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=replace(
                    base.latest_transition_event,
                    kind=ActivityEventKind.STEP_COMPENSATION_FAILED,
                ),
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=replace(
                    base.latest_transition_event,
                    event_id="event-invalid-\x00canary",
                ),
            ),
        )
        for attempt in cases:
            with self.subTest(attempt=attempt):
                self.assert_fixed_error(
                    lambda attempt=attempt: EffectAttemptOutcomeRecord(
                        WORKSPACE_ID,
                        outcome,
                        attempt,
                        (),
                    ),
                    "effect outcome record is invalid",
                    "foreign",
                    "canary",
                )

    def test_distinct_valid_terminal_event_ids_identify_distinct_records(self) -> None:
        self.require_outcome_language()
        story = next(item for item in self.stories() if item.name == "execution-failed")
        first = self.direct_attempt_for(story, latest_event_id="terminal-event-a")
        second = self.direct_attempt_for(story, latest_event_id="terminal-event-b")

        first_record = EffectAttemptOutcomeRecord(
            WORKSPACE_ID,
            self.outcome_for(story),
            first,
            (),
        )
        second_record = EffectAttemptOutcomeRecord(
            WORKSPACE_ID,
            self.outcome_for(story),
            second,
            (),
        )
        self.assertNotEqual(first_record, second_record)
        self.assertEqual(
            first_record.attempt.latest_transition_event.event_id,
            "terminal-event-a",
        )
        self.assertEqual(
            second_record.attempt.latest_transition_event.event_id,
            "terminal-event-b",
        )

    def test_snapshot_binds_the_exact_fixed_failure_projection(self) -> None:
        self.require_outcome_language()
        story = next(item for item in self.stories() if item.name == "execution-failed")
        outcome = self.outcome_for(story)
        base = story.attempt
        wrong_failure = replace(
            base.latest_transition_event.failure,
            code="private-failure-canary",
        )
        candidates = (
            EffectAttemptRecord(
                base.state,
                base.original_start_event,
                replace(base.latest_transition_event, failure=None),
            ),
            EffectAttemptRecord(
                base.state,
                base.original_start_event,
                replace(base.latest_transition_event, failure=wrong_failure),
            ),
        )
        for attempt in candidates:
            with self.subTest(failure=attempt.latest_transition_event.failure):
                self.assert_fixed_error(
                    lambda attempt=attempt: EffectAttemptOutcomeRecord(
                        WORKSPACE_ID,
                        outcome,
                        attempt,
                        (),
                    ),
                    "effect outcome record is invalid",
                    "private-failure-canary",
                )

    def test_projection_is_ordered_unique_counted_and_snapshot_timed(self) -> None:
        self.require_outcome_language()
        signature = inspect.signature(effect_outcome_observation_records)
        self.assertNotIn("observed_at", signature.parameters)

        for story in self.stories():
            with self.subTest(story=story.name, compensation=story.compensation):
                outcome = self.outcome_for(story)
                ids = self.observation_ids(story)
                records = effect_outcome_observation_records(
                    outcome,
                    story.attempt,
                    workspace_id=WORKSPACE_ID,
                    observation_ids=ids,
                )
                self.assertIs(type(records), tuple)
                self.assertTrue(
                    all(type(record) is ObservationRecord for record in records)
                )
                self.assertEqual(records, self.expected_observation_records(story))
                self.assertEqual(tuple(row.observation_id for row in records), ids)
                self.assertEqual(
                    tuple(row.subject_id for row in records),
                    tuple(item.subject_id for item in story.endpoint_observations),
                )

        story = next(item for item in self.stories() if len(item.endpoint_observations) == 2)
        outcome = self.outcome_for(story)
        for ids in (
            ("only-one",),
            ("duplicate", "duplicate"),
            ("one", "two", "three"),
        ):
            with self.subTest(ids=ids):
                self.assert_fixed_error(
                    lambda ids=ids: effect_outcome_observation_records(
                        outcome,
                        story.attempt,
                        workspace_id=WORKSPACE_ID,
                        observation_ids=ids,
                    ),
                    "effect outcome observation projection is invalid",
                    *ids,
                )

    def test_projection_preserves_the_exact_4096_byte_bridge_boundary(self) -> None:
        self.require_outcome_language()
        story = next(
            item
            for item in self.stories()
            if item.name == "execution-succeeded" and not item.compensation
        )
        endpoint = self.endpoint_for_bridge_size(4_096)
        value = replace(story.value, observations=(endpoint,))
        bounded_story = replace(story, value=value)
        bounded_story = replace(
            bounded_story,
            attempt=self.direct_attempt_for(bounded_story),
        )
        records = effect_outcome_observation_records(
            self.outcome_for(bounded_story),
            bounded_story.attempt,
            workspace_id=WORKSPACE_ID,
            observation_ids=("observation-max",),
        )

        self.assertIs(type(records), tuple)
        self.assertEqual(len(records), 1)
        self.assertIs(type(records[0]), ObservationRecord)
        self.assertEqual(
            len(records[0].evidence.canonical_json.encode("utf-8")),
            4_096,
        )

    def test_projection_rejects_workspace_and_coordinate_hostility(self) -> None:
        self.require_outcome_language()
        story = next(item for item in self.stories() if item.endpoint_observations)
        outcome = self.outcome_for(story)
        ids = self.observation_ids(story)
        maximum_workspace = "w" * 512
        maximum_ids = ("a" * 512, "b" * 512)
        projected = effect_outcome_observation_records(
            outcome,
            story.attempt,
            workspace_id=maximum_workspace,
            observation_ids=maximum_ids,
        )
        self.assertEqual(
            tuple(record.workspace_id for record in projected),
            (maximum_workspace, maximum_workspace),
        )
        self.assertEqual(
            tuple(record.observation_id for record in projected),
            maximum_ids,
        )

        class HostileTuple(tuple):
            pass

        candidates = (
            (HostileStr(WORKSPACE_ID), ids),
            ("", ids),
            ("w" * 513, ids),
            ("workspace\ncanary", ids),
            ("workspace-surrogate-\ud800", ids),
            (WORKSPACE_ID, tuple(HostileStr(value) for value in ids)),
            (WORKSPACE_ID, ("", ids[1])),
            (WORKSPACE_ID, ("o" * 513, ids[1])),
            (WORKSPACE_ID, ("observation\x00canary", ids[1])),
            (WORKSPACE_ID, ("observation-surrogate-\ud800", ids[1])),
            (WORKSPACE_ID, tuple(HostileInt(index) for index, _ in enumerate(ids))),
            (WORKSPACE_ID, list(ids)),
            (WORKSPACE_ID, HostileTuple(ids)),
        )
        for workspace, observation_ids in candidates:
            with self.subTest(workspace=workspace, observation_ids=observation_ids):
                self.assert_fixed_error(
                    lambda workspace=workspace, observation_ids=observation_ids: effect_outcome_observation_records(
                        outcome,
                        story.attempt,
                        workspace_id=workspace,
                        observation_ids=observation_ids,
                    ),
                    "effect outcome observation projection is invalid",
                    "workspace\ncanary",
                    "surrogate",
                    "observation\x00canary",
                )

    def test_record_requires_an_exact_tuple_of_exact_observation_rows(self) -> None:
        self.require_outcome_language()
        story = next(item for item in self.stories() if item.endpoint_observations)
        outcome = self.outcome_for(story)
        rows = self.expected_observation_records(story)

        class HostileObservationRecord(ObservationRecord):
            pass

        class HostileTuple(tuple):
            pass

        hostile_row = HostileObservationRecord(**rows[0].__dict__)
        forged_row = forge_exact(
            ObservationRecord,
            **{
                **rows[0].__dict__,
                "workspace_id": HostileStr(WORKSPACE_ID),
            },
        )
        for candidate in (
            list(rows),
            HostileTuple(rows),
            (hostile_row, *rows[1:]),
            (forged_row, *rows[1:]),
        ):
            with self.subTest(candidate=type(candidate)):
                self.assert_fixed_error(
                    lambda candidate=candidate: EffectAttemptOutcomeRecord(
                        WORKSPACE_ID,
                        outcome,
                        story.attempt,
                        candidate,
                    ),
                    "effect outcome record is invalid",
                )

    def test_record_owns_the_complete_valid_observation_row_inverse(self) -> None:
        self.require_outcome_language()
        story = next(
            item
            for item in self.stories()
            if item.name == "execution-succeeded" and not item.compensation
        )
        outcome = self.outcome_for(story)
        rows = self.expected_observation_records(story)
        extra = replace(rows[1], observation_id="observation-extra")
        changed_rows = (
            (rows[1],),
            (rows[0], rows[1], extra),
            (rows[0], rows[0]),
            tuple(reversed(rows)),
            (replace(rows[0], observation_id="observation-foreign"), rows[1]),
            (replace(rows[0], workspace_id="workspace-foreign"), rows[1]),
            (replace(rows[0], observed_at="2040-01-01T00:00:00Z"), rows[1]),
            (replace(rows[0], subject_id="subject-foreign"), rows[1]),
            (replace(rows[0], graph_id="graph-foreign"), rows[1]),
            (
                replace(
                    rows[0],
                    evidence=BoundedEvidence.from_mapping(
                        {"runtime_endpoint": {"foreign": True}}
                    ),
                ),
                rows[1],
            ),
            (
                replace(
                    rows[0],
                    probe_kind=ProbeKind.APPLICATION_HEALTH,
                    probe_outcome=ProbeOutcome.HEALTHY,
                ),
                rows[1],
            ),
            (replace(rows[0], probe_outcome=ProbeOutcome.REACHABLE), rows[1]),
            (replace(rows[0], endpoint_context=EndpointContext.PUBLIC), rows[1]),
            (replace(rows[0], status=ObservationStatus.HEALTHY), rows[1]),
            (replace(rows[0], freshness=ObservationFreshness.STALE), rows[1]),
            (),
        )
        for candidate in changed_rows:
            self.assertIs(type(candidate), tuple)
            self.assertTrue(all(type(row) is ObservationRecord for row in candidate))
            with self.subTest(candidate=candidate):
                self.assert_fixed_error(
                    lambda candidate=candidate: EffectAttemptOutcomeRecord(
                        WORKSPACE_ID,
                        outcome,
                        story.attempt,
                        candidate,
                    ),
                    "effect outcome record is invalid",
                    "foreign",
                    "2040-01-01",
                )

        empty_story = next(
            item
            for item in self.stories()
            if item.name == "observed-absent" and not item.compensation
        )
        self.assertEqual(empty_story.endpoint_observations, ())
        self.assert_fixed_error(
            lambda: EffectAttemptOutcomeRecord(
                WORKSPACE_ID,
                self.outcome_for(empty_story),
                empty_story.attempt,
                (rows[0],),
            ),
            "effect outcome record is invalid",
        )

    def test_subclass_and_exact_forgery_graph_is_rejected_at_new_boundary(self) -> None:
        self.require_outcome_language()
        story = next(item for item in self.stories() if item.name == "execution-failed")
        outcome = self.outcome_for(story)
        base = story.attempt
        hostile_state = HostileEffectAttemptState(**base.state.__dict__)
        hostile_event = HostileActivityEventRecord(**base.latest_transition_event.__dict__)
        hostile_evidence = HostileBoundedEvidence(
            base.latest_transition_event.evidence.canonical_json
        )
        hostile_failure = HostileFailureEvidence(
            **base.latest_transition_event.failure.__dict__
        )
        hostile_record = HostileEffectAttemptRecord(
            base.state,
            base.original_start_event,
            base.latest_transition_event,
        )

        exact_evidence = forge_exact(
            BoundedEvidence,
            canonical_json=HostileStr(
                base.latest_transition_event.evidence.canonical_json
            ),
        )
        exact_failure = forge_exact(
            FailureEvidence,
            category=base.latest_transition_event.failure.category,
            code=HostileStr("private-code-canary"),
            message=base.latest_transition_event.failure.message,
            details=exact_evidence,
        )
        exact_event = forge_exact(
            ActivityEventRecord,
            event_id=base.latest_transition_event.event_id,
            run_id=base.latest_transition_event.run_id,
            ordinal=HostileInt(base.latest_transition_event.ordinal),
            kind=base.latest_transition_event.kind,
            occurred_at=base.latest_transition_event.occurred_at,
            activity_id=base.latest_transition_event.activity_id,
            evidence=exact_evidence,
            failure=exact_failure,
            recovery=None,
        )
        exact_state = forge_exact(
            EffectAttemptState,
            identity=base.state.identity,
            request_fingerprint=HostileStr(REQUEST_FINGERPRINT),
            fence=base.state.fence,
            status=base.state.status,
            outcome_fingerprint=base.state.outcome_fingerprint,
            prior_attempt=None,
            recovery_decision=None,
        )
        hostile_run_id = forge_exact(RunId, value=HostileStr("run-a"))
        hostile_identity = forge_exact(
            EffectAttemptIdentity,
            run_id=hostile_run_id,
            activity_id=HostileStr("activity-a"),
            attempt=HostileInt(1),
        )
        exact_identity_state = forge_exact(
            EffectAttemptState,
            identity=hostile_identity,
            request_fingerprint=base.state.request_fingerprint,
            fence=base.state.fence,
            status=base.state.status,
            outcome_fingerprint=base.state.outcome_fingerprint,
            prior_attempt=None,
            recovery_decision=None,
        )
        exact_original = forge_exact(
            ActivityEventRecord,
            event_id=HostileStr(base.original_start_event.event_id),
            run_id=base.original_start_event.run_id,
            ordinal=base.original_start_event.ordinal,
            kind=base.original_start_event.kind,
            occurred_at=base.original_start_event.occurred_at,
            activity_id=base.original_start_event.activity_id,
            evidence=forge_exact(
                BoundedEvidence,
                canonical_json=HostileStr(
                    base.original_start_event.evidence.canonical_json
                ),
            ),
            failure=None,
            recovery=None,
        )
        exact_records = (
            forge_exact(
                EffectAttemptRecord,
                state=hostile_state,
                original_start_event=base.original_start_event,
                latest_transition_event=base.latest_transition_event,
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=hostile_event,
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=replace(
                    base.latest_transition_event,
                    evidence=hostile_evidence,
                ),
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=replace(
                    base.latest_transition_event,
                    failure=hostile_failure,
                ),
            ),
            forge_exact(
                EffectAttemptRecord,
                state=exact_state,
                original_start_event=base.original_start_event,
                latest_transition_event=base.latest_transition_event,
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=exact_event,
            ),
            forge_exact(
                EffectAttemptRecord,
                state=exact_identity_state,
                original_start_event=base.original_start_event,
                latest_transition_event=base.latest_transition_event,
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=exact_original,
                latest_transition_event=base.latest_transition_event,
            ),
            hostile_record,
        )
        for attempt in exact_records:
            with self.subTest(attempt=attempt):
                self.assert_fixed_error(
                    lambda attempt=attempt: EffectAttemptOutcomeRecord(
                        WORKSPACE_ID,
                        outcome,
                        attempt,
                        (),
                    ),
                    "effect outcome record is invalid",
                    "private-code-canary",
                )

    def test_record_repr_hides_attempt_outcome_and_endpoint_truth(self) -> None:
        self.require_outcome_language()
        story = next(
            item for item in self.stories() if item.name == "execution-succeeded"
        )
        record = self.record_for(story)
        rendered = f"{record!s} {record!r}"
        for canary in (
            "success-canary",
            "event-start",
            "event-execution-succeeded",
            "http://service-a:8080",
            "http://service-b:8080",
            "observation-execution-succeeded",
        ):
            self.assertNotIn(canary, rendered)


if __name__ == "__main__":
    unittest.main()
