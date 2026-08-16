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
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    FailureEvidence,
    ObservationRecord,
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
        base = story.attempt
        other_identity = EffectAttemptIdentity(RunId("run-b"), "activity-b", 1)
        cases = (
            forge_exact(
                EffectAttemptRecord,
                state=forge_exact(
                    EffectAttemptState,
                    identity=other_identity,
                    request_fingerprint=base.state.request_fingerprint,
                    fence=base.state.fence,
                    status=base.state.status,
                    outcome_fingerprint=base.state.outcome_fingerprint,
                    prior_attempt=None,
                    recovery_decision=None,
                ),
                original_start_event=base.original_start_event,
                latest_transition_event=base.latest_transition_event,
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=replace(
                    base.original_start_event,
                    event_id="foreign-start-event-canary",
                ),
                latest_transition_event=base.latest_transition_event,
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=replace(
                    base.original_start_event,
                    evidence=BoundedEvidence.from_mapping(
                        {
                            "effect_attempt": {
                                "attempt": 1,
                                "state_fingerprint": "d" * 64,
                            }
                        }
                    ),
                ),
                latest_transition_event=base.latest_transition_event,
            ),
            forge_exact(
                EffectAttemptRecord,
                state=replace(base.state, request_fingerprint="b" * 64),
                original_start_event=base.original_start_event,
                latest_transition_event=base.latest_transition_event,
            ),
            forge_exact(
                EffectAttemptRecord,
                state=replace(base.state, status=EffectAttemptStatus.FAILED),
                original_start_event=base.original_start_event,
                latest_transition_event=base.latest_transition_event,
            ),
            forge_exact(
                EffectAttemptRecord,
                state=replace(base.state, outcome_fingerprint="c" * 64),
                original_start_event=base.original_start_event,
                latest_transition_event=base.latest_transition_event,
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
                    "foreign-start-event-canary",
                    "run-b",
                    "activity-b",
                    "b" * 64,
                    "c" * 64,
                )

    def test_snapshot_binds_latest_event_failure_and_rejects_recovery(self) -> None:
        self.require_outcome_language()
        story = next(item for item in self.stories() if item.name == "execution-failed")
        outcome = self.outcome_for(story)
        base = story.attempt
        wrong_failure = replace(
            base.latest_transition_event.failure,
            code="private-failure-canary",
        )
        cases = (
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=replace(
                    base.latest_transition_event,
                    kind=ActivityEventKind.STEP_UNCERTAIN,
                ),
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=replace(
                    base.latest_transition_event,
                    event_id="foreign-latest-event-canary",
                ),
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=replace(
                    base.latest_transition_event,
                    evidence=BoundedEvidence.from_mapping(
                        {
                            "effect_attempt": {
                                "attempt": 1,
                                "state_fingerprint": "e" * 64,
                            }
                        }
                    ),
                ),
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=replace(
                    base.latest_transition_event,
                    failure=None,
                ),
            ),
            forge_exact(
                EffectAttemptRecord,
                state=base.state,
                original_start_event=base.original_start_event,
                latest_transition_event=replace(
                    base.latest_transition_event,
                    failure=wrong_failure,
                ),
            ),
            forge_exact(
                EffectAttemptRecord,
                state=forge_exact(
                    EffectAttemptState,
                    identity=base.state.identity,
                    request_fingerprint=base.state.request_fingerprint,
                    fence=base.state.fence,
                    status=base.state.status,
                    outcome_fingerprint=base.state.outcome_fingerprint,
                    prior_attempt=None,
                    recovery_decision="recovery-canary",
                ),
                original_start_event=base.original_start_event,
                latest_transition_event=base.latest_transition_event,
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
                    "private-failure-canary",
                    "foreign-latest-event-canary",
                    "recovery-canary",
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

    def test_projection_rejects_workspace_and_coordinate_hostility(self) -> None:
        self.require_outcome_language()
        story = next(item for item in self.stories() if item.endpoint_observations)
        outcome = self.outcome_for(story)
        ids = self.observation_ids(story)
        candidates = (
            (HostileStr(WORKSPACE_ID), ids),
            ("workspace\ncanary", ids),
            (WORKSPACE_ID, tuple(HostileStr(value) for value in ids)),
            (WORKSPACE_ID, tuple(HostileInt(index) for index, _ in enumerate(ids))),
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
