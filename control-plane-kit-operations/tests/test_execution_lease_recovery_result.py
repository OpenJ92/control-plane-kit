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
    FailureCategory,
    LifecycleOperationKind,
    RecoveryDecisionKind,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
import control_plane_kit_operations.records as records_module
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityRunRecord,
    AdmittedRun,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    FailureEvidence,
    OperationActionRecord,
    OperationsRecordError,
    RetryIdentity,
)


RECOVERY_MODULE = "control_plane_kit_operations.execution_lease_recovery"


try:
    recovery_module = importlib.import_module(RECOVERY_MODULE)
except ModuleNotFoundError as error:
    if error.name != RECOVERY_MODULE:
        raise
    recovery_module = None

ExecutionLeaseRecoveryResult = getattr(
    recovery_module, "ExecutionLeaseRecoveryResult", None
)
ExecutionLeaseRecoveryEvidence = getattr(
    records_module, "ExecutionLeaseRecoveryEvidence", None
)


class ExecutionLeaseRecoveryResultTests(unittest.TestCase):
    maxDiff = None

    def assert_safe_error(self, error: BaseException) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertLessEqual(len(f"{error!s} {error!r}"), 512)

    def require_language(self) -> None:
        required = {
            "ExecutionLeaseRecoveryEvidence": ExecutionLeaseRecoveryEvidence,
            "ExecutionLeaseRecoveryResult": ExecutionLeaseRecoveryResult,
        }
        self.assertEqual(
            [name for name, value in required.items() if value is None],
            [],
            "execution-lease recovery result language is missing",
        )

    def evidence(self, decision: RecoveryDecisionKind):
        self.require_language()
        prior = ExecutionLeaseFence("worker-a", 7)
        if decision in (
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
        ):
            replacement = ExecutionLeaseFence("worker-a", 8)
        elif decision is RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM:
            replacement = ExecutionLeaseFence("worker-b", 8)
        else:
            replacement = None
        return ExecutionLeaseRecoveryEvidence(
            decision, RunId("run-a"), prior, replacement
        )

    def result(self, decision: RecoveryDecisionKind, **changes):
        self.require_language()
        evidence = self.evidence(decision)
        abandoned = decision is RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM
        active = decision is RecoveryDecisionKind.RENEW_ACTIVE_CLAIM
        replacement = evidence.replacement_fence
        request = ExecutionRequestRecord(
            ExecutionRequestIdentity(
                "request-a", "workspace-a", "session-a", "plan-a"
            ),
            (
                ExecutionRequestStatus.ABANDONED
                if abandoned
                else ExecutionRequestStatus.CLAIMED
            ),
            "operator-a",
            "requested",
            "approval-request-a",
            "approval-decision-a",
            ExecutionIdempotency("execute-a", "execute-fingerprint-a"),
            (
                None
                if abandoned
                else ClaimIdentity(
                    replacement.worker_id,
                    replacement.generation,
                    "observed",
                    "lease-expires",
                )
            ),
        )
        run = ActivityRunRecord(
            "run-a",
            "plan-a",
            AdmittedRun("request-a"),
            RetryIdentity(1),
            ActivityRunStatus.CLAIMED if active else ActivityRunStatus.FAILED,
            "created",
            started_at=None if active else "started",
            settled_at=None,
        )
        decision_event = ActivityEventRecord(
            "decision-event-a",
            "run-a",
            4,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            "observed",
            recovery=evidence,
        )
        consequence_kind = {
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM: (
                ActivityEventKind.REQUEST_CLAIM_RENEWED
            ),
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM: (
                ActivityEventKind.REQUEST_CLAIM_RENEWED
            ),
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM: (
                ActivityEventKind.REQUEST_CLAIM_TAKEN_OVER
            ),
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM: (
                ActivityEventKind.REQUEST_CLAIM_ABANDONED
            ),
        }[decision]
        consequence_event = ActivityEventRecord(
            "consequence-event-a",
            "run-a",
            5,
            consequence_kind,
            "observed",
        )
        payload = {
            "execution_request_id": "request-a",
            "plan_id": "plan-a",
            "retained_run_id": "run-a",
            "decision_event_id": "decision-event-a",
            "decision_event_kind": "recovery_decision_recorded",
            "decision_event_ordinal": 4,
            "consequence_event_id": "consequence-event-a",
            "consequence_event_kind": consequence_kind.value,
            "consequence_event_ordinal": 5,
            "recovery": evidence.descriptor(),
        }
        if not abandoned:
            payload["lease_duration_seconds"] = 600
        action = OperationActionRecord(
            "action-a",
            "session-a",
            8,
            LifecycleOperationKind.RECORD_RECOVERY_DECISION,
            "operator-a",
            payload,
            "observed",
            "recover-a",
            "a" * 64,
        )
        values = {
            "request": request,
            "retained_run": run,
            "decision_event": decision_event,
            "consequence_event": consequence_event,
            "action": action,
            "replayed": False,
        }
        values.update(changes)
        return ExecutionLeaseRecoveryResult(**values)

    def assert_result_rejected(self, valid, **changes) -> None:
        values = {
            "request": valid.request,
            "retained_run": valid.retained_run,
            "decision_event": valid.decision_event,
            "consequence_event": valid.consequence_event,
            "action": valid.action,
            "replayed": valid.replayed,
        }
        values.update(changes)
        with self.assertRaises(OperationsRecordError) as captured:
            ExecutionLeaseRecoveryResult(**values)
        self.assert_safe_error(captured.exception)

    def test_each_result_variant_has_exact_canonical_history_and_descriptor(self) -> None:
        self.require_language()
        self.assertIs(
            getattr(operations_root, "ExecutionLeaseRecoveryResult", None),
            ExecutionLeaseRecoveryResult,
        )
        self.assertTrue(dataclasses.is_dataclass(ExecutionLeaseRecoveryResult))
        self.assertTrue(ExecutionLeaseRecoveryResult.__dataclass_params__.frozen)
        self.assertEqual(
            tuple(
                field.name
                for field in dataclasses.fields(ExecutionLeaseRecoveryResult)
            ),
            (
                "request",
                "retained_run",
                "decision_event",
                "consequence_event",
                "action",
                "replayed",
            ),
        )

        for decision in (
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
        ):
            with self.subTest(decision=decision):
                result = self.result(decision)
                self.assertEqual(
                    result.descriptor(),
                    {
                        "decision": decision.value,
                        "request_id": "request-a",
                        "plan_id": "plan-a",
                        "retained_run_id": "run-a",
                        "decision_event": {
                            "event_id": "decision-event-a",
                            "kind": "recovery_decision_recorded",
                            "ordinal": 4,
                        },
                        "consequence_event": {
                            "event_id": "consequence-event-a",
                            "kind": result.consequence_event.kind.value,
                            "ordinal": 5,
                        },
                        "action_id": "action-a",
                        "action_kind": "record-recovery-decision",
                        "recovery": result.decision_event.recovery.descriptor(),
                        "replayed": False,
                    },
                )
                rendered = repr(result.descriptor())
                for forbidden in (
                    "authority-reference-canary",
                    "scopes",
                    "lease-expires",
                    "claimed_at",
                ):
                    self.assertNotIn(forbidden, rendered)

    def test_result_rejects_cross_record_and_action_identity_drift(self) -> None:
        valid = self.result(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        foreign_evidence = ExecutionLeaseRecoveryEvidence(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            RunId("run-other"),
            ExecutionLeaseFence("worker-a", 7),
            ExecutionLeaseFence("worker-a", 8),
        )
        foreign_decision_event = ActivityEventRecord(
            "decision-event-a",
            "run-other",
            4,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            "observed",
            recovery=foreign_evidence,
        )
        mutations = (
            {"request": dataclasses.replace(
                valid.request,
                identity=dataclasses.replace(valid.request.identity, request_id="other"),
            )},
            {"request": dataclasses.replace(
                valid.request,
                identity=dataclasses.replace(valid.request.identity, plan_id="other"),
            )},
            {"retained_run": dataclasses.replace(valid.retained_run, run_id="run-other")},
            {"retained_run": dataclasses.replace(valid.retained_run, plan_id="other-plan")},
            {"retained_run": dataclasses.replace(valid.retained_run, admission=AdmittedRun("other-request"))},
            {"decision_event": foreign_decision_event},
            {"decision_event": dataclasses.replace(valid.decision_event, ordinal=3)},
            {"decision_event": dataclasses.replace(valid.decision_event, occurred_at="later")},
            {"consequence_event": dataclasses.replace(valid.consequence_event, run_id="other")},
            {"consequence_event": dataclasses.replace(valid.consequence_event, event_id=valid.decision_event.event_id)},
            {"consequence_event": dataclasses.replace(valid.consequence_event, ordinal=6)},
            {"consequence_event": dataclasses.replace(valid.consequence_event, occurred_at="later")},
            {"consequence_event": dataclasses.replace(valid.consequence_event, kind=ActivityEventKind.REQUEST_CLAIM_TAKEN_OVER)},
            {"consequence_event": dataclasses.replace(valid.consequence_event, evidence=BoundedEvidence.from_mapping({"canary": True}))},
            {"consequence_event": dataclasses.replace(valid.consequence_event, failure=FailureEvidence(FailureCategory.TERMINAL, "code", "message"))},
            {"action": dataclasses.replace(valid.action, session_id="other")},
            {"action": dataclasses.replace(valid.action, action_type=LifecycleOperationKind.START_RUN)},
            {"action": dataclasses.replace(valid.action, created_at="later")},
            {"action": dataclasses.replace(valid.action, idempotency_key=None)},
            {"action": dataclasses.replace(valid.action, idempotency_key="x" * 201)},
            {"action": dataclasses.replace(valid.action, intent_fingerprint=None)},
            {"action": dataclasses.replace(valid.action, intent_fingerprint="A" * 64)},
            {"action": dataclasses.replace(valid.action, intent_fingerprint="a" * 63)},
            {"replayed": "false"},
        )
        for mutation in mutations:
            with self.subTest(mutation=tuple(mutation)):
                self.assert_result_rejected(valid, **mutation)

    def test_result_rejects_every_canonical_payload_coordinate_drift(self) -> None:
        for decision in (
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
        ):
            valid = self.result(decision)
            payload = dict(valid.action.payload)
            changed_values = {
                "execution_request_id": "other-request",
                "plan_id": "other-plan",
                "retained_run_id": "other-run",
                "decision_event_id": "other-decision",
                "decision_event_kind": "run_opened",
                "decision_event_ordinal": 6,
                "consequence_event_id": "other-consequence",
                "consequence_event_kind": "run_opened",
                "consequence_event_ordinal": 6,
                "recovery": {**payload["recovery"], "decision": "remain-paused"},
            }
            for key, changed in changed_values.items():
                for mutation_kind, mutated in (
                    ("missing", {name: value for name, value in payload.items() if name != key}),
                    ("changed", {**payload, key: changed}),
                ):
                    with self.subTest(
                        decision=decision, key=key, mutation=mutation_kind
                    ):
                        self.assert_result_rejected(
                            valid,
                            action=dataclasses.replace(valid.action, payload=mutated),
                        )
            with self.subTest(decision=decision, key="extra"):
                self.assert_result_rejected(
                    valid,
                    action=dataclasses.replace(
                        valid.action, payload={**payload, "extra": True}
                    ),
                )

            if decision is RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM:
                duration_mutations = ({**payload, "lease_duration_seconds": 600},)
            else:
                duration_mutations = (
                    {name: value for name, value in payload.items() if name != "lease_duration_seconds"},
                    {**payload, "lease_duration_seconds": True},
                    {**payload, "lease_duration_seconds": 0},
                    {**payload, "lease_duration_seconds": 3601},
                    {**payload, "lease_duration_seconds": "600"},
                )
            for mutated in duration_mutations:
                with self.subTest(decision=decision, key="lease_duration_seconds"):
                    self.assert_result_rejected(
                        valid,
                        action=dataclasses.replace(valid.action, payload=mutated),
                    )

    def test_result_requires_exact_claim_and_run_status_matrix(self) -> None:
        active = self.result(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        renewed = self.result(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        takeover = self.result(RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM)
        abandoned = self.result(RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM)

        wrong_claims = (
            (renewed, dataclasses.replace(renewed.request, status=ExecutionRequestStatus.ABANDONED, claim=None)),
            (renewed, dataclasses.replace(renewed.request, claim=ClaimIdentity("worker-a", 9, "observed", "lease"))),
            (takeover, dataclasses.replace(takeover.request, claim=ClaimIdentity("worker-z", 8, "observed", "lease"))),
            (abandoned, dataclasses.replace(abandoned.request, status=ExecutionRequestStatus.QUEUED)),
        )
        for result, request in wrong_claims:
            with self.subTest(decision=result.decision_event.recovery.decision_kind):
                self.assert_result_rejected(result, request=request)

        active_as_failed = ActivityRunRecord(
            "run-a", "plan-a", AdmittedRun("request-a"), RetryIdentity(1),
            ActivityRunStatus.FAILED, "created", "started"
        )
        self.assert_result_rejected(active, retained_run=active_as_failed)
        for result in (renewed, takeover, abandoned):
            expired_as_claimed = ActivityRunRecord(
                "run-a", "plan-a", AdmittedRun("request-a"), RetryIdentity(1),
                ActivityRunStatus.CLAIMED, "created"
            )
            with self.subTest(decision=result.decision_event.recovery.decision_kind):
                self.assert_result_rejected(result, retained_run=expired_as_claimed)

    def test_active_renew_replay_admits_exact_closed_lifecycle_set(self) -> None:
        active = self.result(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        replay_statuses = (
            ActivityRunStatus.CLAIMED,
            ActivityRunStatus.RUNNING,
            ActivityRunStatus.PAUSED,
            ActivityRunStatus.SUCCEEDED,
            ActivityRunStatus.FAILED,
            ActivityRunStatus.COMPENSATING,
            ActivityRunStatus.COMPENSATED,
            ActivityRunStatus.PARTIALLY_FAILED,
            ActivityRunStatus.UNCOMPENSATED_FAILURE,
            ActivityRunStatus.CANCELLED,
        )
        self.assertEqual(tuple(ActivityRunStatus), replay_statuses)
        settled = {
            ActivityRunStatus.SUCCEEDED,
            ActivityRunStatus.COMPENSATED,
            ActivityRunStatus.PARTIALLY_FAILED,
            ActivityRunStatus.UNCOMPENSATED_FAILURE,
            ActivityRunStatus.CANCELLED,
        }
        started = {
            ActivityRunStatus.RUNNING,
            ActivityRunStatus.PAUSED,
            ActivityRunStatus.SUCCEEDED,
            ActivityRunStatus.FAILED,
            ActivityRunStatus.COMPENSATING,
            ActivityRunStatus.COMPENSATED,
            ActivityRunStatus.PARTIALLY_FAILED,
            ActivityRunStatus.UNCOMPENSATED_FAILURE,
        }

        for status in replay_statuses:
            run = dataclasses.replace(
                active.retained_run,
                status=status,
                started_at="started" if status in started else None,
                settled_at="settled" if status in settled else None,
            )
            with self.subTest(status=status):
                replayed = ExecutionLeaseRecoveryResult(
                    active.request,
                    run,
                    active.decision_event,
                    active.consequence_event,
                    active.action,
                    replayed=True,
                )
                self.assertIs(replayed.retained_run.status, status)
                if status is not ActivityRunStatus.CLAIMED:
                    self.assert_result_rejected(
                        active,
                        retained_run=run,
                        replayed=False,
                    )

        for decision in (
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
        ):
            result = self.result(decision)
            replayed = dataclasses.replace(result, replayed=True)
            self.assertIs(replayed.retained_run.status, ActivityRunStatus.FAILED)
            with self.subTest(decision=decision):
                self.assert_result_rejected(
                    result,
                    retained_run=dataclasses.replace(
                        result.retained_run,
                        status=ActivityRunStatus.RUNNING,
                    ),
                    replayed=True,
                )

    def test_result_categorically_rejects_retry_evidence(self) -> None:
        valid = self.result(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        prior_fence = valid.decision_event.recovery.prior_fence
        retry_evidence = ExecutionLeaseRecoveryEvidence(
            RecoveryDecisionKind.RETRY_AS_NEW_RUN,
            RunId(valid.retained_run.run_id),
            prior_fence,
            prior_fence,
        )
        retry_event = dataclasses.replace(
            valid.decision_event,
            recovery=retry_evidence,
        )

        with self.assertRaises(OperationsRecordError) as captured:
            ExecutionLeaseRecoveryResult(
                valid.request,
                valid.retained_run,
                retry_event,
                valid.consequence_event,
                valid.action,
            )

        self.assertEqual(
            str(captured.exception),
            "recovery result decision kind is invalid",
        )
        self.assert_safe_error(captured.exception)


if __name__ == "__main__":
    unittest.main()
