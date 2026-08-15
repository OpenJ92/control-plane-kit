from __future__ import annotations

import dataclasses
import importlib
import unittest

import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    LifecycleOperationKind,
    RecoveryDecisionKind,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityRunRecord,
    AdmittedRun,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionLeaseRecoveryEvidence,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    OperationActionRecord,
    OperationsRecordError,
    RetryIdentity,
)


RETRY_MODULE = "control_plane_kit_operations.activity_run_retry"
MAX_ATTEMPT = 2_147_483_647


try:
    retry_module = importlib.import_module(RETRY_MODULE)
except ModuleNotFoundError as error:
    if error.name != RETRY_MODULE:
        raise
    retry_module = None

ActivityRunRetryResult = getattr(
    retry_module, "ActivityRunRetryResult", None
)


class ActivityRunRetryResultTests(unittest.TestCase):
    maxDiff = None

    def require_language(self) -> None:
        self.assertIsNotNone(
            ActivityRunRetryResult,
            "activity-run retry result language is missing",
        )

    def assert_safe_error(self, error: BaseException, *canaries: str) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        for canary in canaries:
            self.assertNotIn(canary, rendered)

    def result(
        self,
        *,
        prior_attempt: int = 1,
        attempt: int = 2,
        replayed: bool = False,
        run_status: ActivityRunStatus = ActivityRunStatus.CLAIMED,
        **changes,
    ):
        self.require_language()
        fence = ExecutionLeaseFence("worker-a", 7)
        request = ExecutionRequestRecord(
            ExecutionRequestIdentity(
                "request-a", "workspace-a", "session-a", "plan-a"
            ),
            ExecutionRequestStatus.CLAIMED,
            "operator-a",
            "requested",
            "approval-request-a",
            "approval-decision-a",
            ExecutionIdempotency("execute-a", "e" * 64),
            ClaimIdentity(
                "worker-a",
                7,
                "claimed-before",
                "lease-expires-after",
            ),
        )
        prior_retry = RetryIdentity(
            prior_attempt,
            None if prior_attempt == 1 else "run-before",
        )
        prior = ActivityRunRecord(
            "run-a",
            "plan-a",
            AdmittedRun("request-a"),
            prior_retry,
            ActivityRunStatus.FAILED,
            "created-before",
            started_at="started-before",
            metadata=BoundedEvidence.from_mapping(
                (
                    {"attempt": 1}
                    if prior_attempt == 1
                    else {
                        "attempt": prior_attempt,
                        "prior_run_id": "run-before",
                    }
                )
            ),
        )
        started_at = (
            None
            if run_status is ActivityRunStatus.CLAIMED
            else "started-after"
        )
        settled_statuses = {
            ActivityRunStatus.SUCCEEDED,
            ActivityRunStatus.COMPENSATED,
            ActivityRunStatus.PARTIALLY_FAILED,
            ActivityRunStatus.UNCOMPENSATED_FAILURE,
            ActivityRunStatus.CANCELLED,
        }
        run = ActivityRunRecord(
            "run-b",
            "plan-a",
            AdmittedRun("request-a"),
            RetryIdentity(attempt, "run-a"),
            run_status,
            "observed",
            started_at=started_at,
            settled_at="settled-after" if run_status in settled_statuses else None,
            metadata=BoundedEvidence.from_mapping(
                {"attempt": attempt, "prior_run_id": "run-a"}
            ),
        )
        recovery = ExecutionLeaseRecoveryEvidence(
            RecoveryDecisionKind.RETRY_AS_NEW_RUN,
            RunId("run-a"),
            fence,
            fence,
        )
        decision_event = ActivityEventRecord(
            "decision-event-a",
            "run-a",
            4,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            "observed",
            recovery=recovery,
        )
        opened_event = ActivityEventRecord(
            "opened-event-b",
            "run-b",
            1,
            ActivityEventKind.RUN_OPENED,
            "observed",
            evidence=BoundedEvidence.from_mapping(
                {"attempt": attempt, "prior_run_id": "run-a"}
            ),
        )
        action = OperationActionRecord(
            "action-a",
            "session-a",
            8,
            LifecycleOperationKind.RECORD_RECOVERY_DECISION,
            "operator-a",
            {
                "execution_request_id": "request-a",
                "plan_id": "plan-a",
                "prior_run_id": "run-a",
                "run_id": "run-b",
                "prior_attempt": prior_attempt,
                "attempt": attempt,
                "decision_event_id": "decision-event-a",
                "decision_event_kind": "recovery_decision_recorded",
                "decision_event_ordinal": 4,
                "opened_event_id": "opened-event-b",
                "opened_event_kind": "run_opened",
                "opened_event_ordinal": 1,
                "recovery": recovery.descriptor(),
            },
            "observed",
            "retry-a",
            "a" * 64,
        )
        values = {
            "request": request,
            "prior_run": prior,
            "run": run,
            "decision_event": decision_event,
            "opened_event": opened_event,
            "action": action,
            "replayed": replayed,
        }
        values.update(changes)
        return ActivityRunRetryResult(**values)

    def assert_result_rejected(self, valid, **changes) -> None:
        values = {
            "request": valid.request,
            "prior_run": valid.prior_run,
            "run": valid.run,
            "decision_event": valid.decision_event,
            "opened_event": valid.opened_event,
            "action": valid.action,
            "replayed": valid.replayed,
        }
        values.update(changes)
        with self.assertRaises(OperationsRecordError) as captured:
            ActivityRunRetryResult(**values)
        self.assert_safe_error(captured.exception)

    def test_public_result_has_exact_shape_descriptor_and_root_identity(self) -> None:
        self.require_language()
        self.assertIs(
            getattr(operations_root, "ActivityRunRetryResult", None),
            ActivityRunRetryResult,
        )
        self.assertTrue(dataclasses.is_dataclass(ActivityRunRetryResult))
        self.assertTrue(ActivityRunRetryResult.__dataclass_params__.frozen)
        self.assertEqual(
            tuple(
                field.name
                for field in dataclasses.fields(ActivityRunRetryResult)
            ),
            (
                "request",
                "prior_run",
                "run",
                "decision_event",
                "opened_event",
                "action",
                "replayed",
            ),
        )
        result = self.result()
        self.assertEqual(
            result.descriptor(),
            {
                "decision": "retry-as-new-run",
                "request_id": "request-a",
                "plan_id": "plan-a",
                "prior_run_id": "run-a",
                "run_id": "run-b",
                "prior_attempt": 1,
                "attempt": 2,
                "decision_event": {
                    "event_id": "decision-event-a",
                    "kind": "recovery_decision_recorded",
                    "ordinal": 4,
                },
                "opened_event": {
                    "event_id": "opened-event-b",
                    "kind": "run_opened",
                    "ordinal": 1,
                },
                "action_id": "action-a",
                "action_kind": "record-recovery-decision",
                "recovery": result.decision_event.recovery.descriptor(),
                "replayed": False,
            },
        )
        rendered = repr(result.descriptor())
        for forbidden in (
            "authority",
            "scopes",
            "claimed-before",
            "lease-expires-after",
            "secret",
            "endpoint",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_result_rejects_hostile_outer_subclass(self) -> None:
        self.require_language()

        class HostileResult(ActivityRunRetryResult):
            pass

        valid = self.result()
        with self.assertRaises(OperationsRecordError) as captured:
            HostileResult(
                valid.request,
                valid.prior_run,
                valid.run,
                valid.decision_event,
                valid.opened_event,
                valid.action,
                valid.replayed,
            )
        self.assert_safe_error(captured.exception)

    def test_result_enforces_complete_lineage_and_record_identity(self) -> None:
        valid = self.result()
        mutations = (
            {"request": dataclasses.replace(
                valid.request,
                identity=dataclasses.replace(
                    valid.request.identity, request_id="request-other"
                ),
            )},
            {"request": dataclasses.replace(
                valid.request,
                identity=dataclasses.replace(
                    valid.request.identity, plan_id="plan-other"
                ),
            )},
            {"request": dataclasses.replace(
                valid.request,
                identity=dataclasses.replace(
                    valid.request.identity, session_id="session-other"
                ),
            )},
            {"request": dataclasses.replace(
                valid.request,
                status=ExecutionRequestStatus.QUEUED,
                claim=None,
            )},
            {"request": dataclasses.replace(
                valid.request,
                claim=ClaimIdentity(
                    "worker-a", 8, "claimed-before", "lease-expires-after"
                ),
            )},
            {"prior_run": dataclasses.replace(valid.prior_run, run_id="run-other")},
            {"prior_run": dataclasses.replace(valid.prior_run, plan_id="plan-other")},
            {"prior_run": dataclasses.replace(
                valid.prior_run, admission=AdmittedRun("request-other")
            )},
            {"prior_run": dataclasses.replace(
                valid.prior_run,
                status=ActivityRunStatus.PAUSED,
            )},
            {"run": dataclasses.replace(valid.run, run_id="run-other")},
            {"run": dataclasses.replace(valid.run, plan_id="plan-other")},
            {"run": dataclasses.replace(
                valid.run, admission=AdmittedRun("request-other")
            )},
            {"run": dataclasses.replace(
                valid.run, retry=RetryIdentity(2, "run-other")
            )},
            {"run": dataclasses.replace(
                valid.run,
                metadata=BoundedEvidence.from_mapping(
                    {"attempt": 2, "prior_run_id": "run-other"}
                ),
            )},
            {"replayed": "false"},
        )
        for mutation in mutations:
            with self.subTest(mutation=tuple(mutation)):
                self.assert_result_rejected(valid, **mutation)

    def test_result_enforces_exact_events_recovery_and_one_time(self) -> None:
        valid = self.result()
        fence = ExecutionLeaseFence("worker-a", 7)
        changed_recovery = ExecutionLeaseRecoveryEvidence(
            RecoveryDecisionKind.RETRY_AS_NEW_RUN,
            RunId("run-other"),
            fence,
            fence,
        )
        foreign_decision = ActivityEventRecord(
            "decision-event-a",
            "run-other",
            4,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            "observed",
            recovery=changed_recovery,
        )
        mutations = (
            {"run": dataclasses.replace(valid.run, created_at="later")},
            {"decision_event": foreign_decision},
            {"decision_event": dataclasses.replace(valid.decision_event, ordinal=5)},
            {"decision_event": dataclasses.replace(
                valid.decision_event, occurred_at="later"
            )},
            {"opened_event": dataclasses.replace(valid.opened_event, run_id="run-other")},
            {"opened_event": dataclasses.replace(valid.opened_event, ordinal=2)},
            {"opened_event": dataclasses.replace(
                valid.opened_event, kind=ActivityEventKind.RUN_STARTED
            )},
            {"opened_event": dataclasses.replace(
                valid.opened_event, occurred_at="later"
            )},
            {"opened_event": dataclasses.replace(
                valid.opened_event,
                evidence=BoundedEvidence.from_mapping(
                    {"attempt": 2, "prior_run_id": "run-other"}
                ),
            )},
            {"action": dataclasses.replace(valid.action, created_at="later")},
        )
        for mutation in mutations:
            with self.subTest(mutation=tuple(mutation)):
                self.assert_result_rejected(valid, **mutation)

    def test_result_accepts_exact_maximum_attempt_and_rejects_no_successor(
        self,
    ) -> None:
        maximum = self.result(
            prior_attempt=MAX_ATTEMPT - 1,
            attempt=MAX_ATTEMPT,
        )
        self.assertEqual(maximum.run.retry.attempt, MAX_ATTEMPT)

        valid = self.result()
        exhausted_prior = dataclasses.replace(
            valid.prior_run,
            retry=RetryIdentity(MAX_ATTEMPT, "run-before"),
            metadata=BoundedEvidence.from_mapping(
                {"attempt": MAX_ATTEMPT, "prior_run_id": "run-before"}
            ),
        )
        self.assert_result_rejected(valid, prior_run=exhausted_prior)

    def test_direct_result_is_claimed_and_replay_uses_closed_lawful_status_set(
        self,
    ) -> None:
        self.assertIs(self.result().run.status, ActivityRunStatus.CLAIMED)
        for status in ActivityRunStatus:
            with self.subTest(status=status):
                replay = self.result(replayed=True, run_status=status)
                self.assertTrue(replay.replayed)
                self.assertIs(replay.run.status, status)

        for status in ActivityRunStatus:
            if status is ActivityRunStatus.CLAIMED:
                continue
            with self.subTest(direct_status=status):
                with self.assertRaises(OperationsRecordError) as captured:
                    self.result(replayed=False, run_status=status)
                self.assert_safe_error(captured.exception)

    def test_result_rejects_every_action_identity_and_payload_drift(self) -> None:
        valid = self.result()
        identity_mutations = (
            dataclasses.replace(valid.action, session_id="session-other"),
            dataclasses.replace(
                valid.action, action_type=LifecycleOperationKind.START_RUN
            ),
            dataclasses.replace(valid.action, idempotency_key=None),
            dataclasses.replace(valid.action, idempotency_key="x" * 201),
            dataclasses.replace(valid.action, intent_fingerprint=None),
            dataclasses.replace(valid.action, intent_fingerprint="A" * 64),
        )
        for action in identity_mutations:
            with self.subTest(action=action):
                self.assert_result_rejected(valid, action=action)

        payload = dict(valid.action.payload)
        changed = {
            "execution_request_id": "request-other",
            "plan_id": "plan-other",
            "prior_run_id": "run-other",
            "run_id": "run-other",
            "prior_attempt": 7,
            "attempt": 8,
            "decision_event_id": "decision-other",
            "decision_event_kind": "run_opened",
            "decision_event_ordinal": 5,
            "opened_event_id": "opened-other",
            "opened_event_kind": "run_started",
            "opened_event_ordinal": 2,
            "recovery": {**payload["recovery"], "retained_run_id": "run-other"},
        }
        for key, changed_value in changed.items():
            for kind, candidate in (
                (
                    "missing",
                    {name: value for name, value in payload.items() if name != key},
                ),
                ("changed", {**payload, key: changed_value}),
            ):
                with self.subTest(key=key, mutation=kind):
                    self.assert_result_rejected(
                        valid,
                        action=dataclasses.replace(valid.action, payload=candidate),
                    )
        self.assert_result_rejected(
            valid,
            action=dataclasses.replace(
                valid.action,
                payload={**payload, "extra": "extra-canary"},
            ),
        )


if __name__ == "__main__":
    unittest.main()
