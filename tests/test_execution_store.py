from __future__ import annotations

import os
import unittest

import psycopg

from control_plane_kit import (
    ActivityEventKind,
    ActivityEventRecord,
    ActivityId,
    ActivityPlan,
    ActivityRunRecord,
    ActivityRunStatus,
    AcceptUncompensatedFailure,
    AdmittedRun,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    ExecutionRequestStatus,
    FailureCategory,
    FailureEvidence,
    RecoveryAuthority,
    RecoveryDecisionRecord,
    RecoveryScope,
    RetryIdentity,
    RiskLevel,
    PlannedActivity,
    RuntimeTarget,
    StartRuntime,
)
from control_plane_kit.stores import (
    ActivityPlanRecord,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    PostgresUnitOfWork,
    WorkspaceRecord,
    GraphVersionRecord,
)
from control_plane_kit.topology import DeploymentGraph, RuntimeRecord
from control_plane_kit.core.types import RuntimeKind
from tests.postgres_case import PostgresStoreTestCase


class ExecutionStoreTests(PostgresStoreTestCase):
    def test_canonical_request_run_and_event_round_trip(self):
        self._seed_admission_truth(self.stores)
        request = self._request()
        run = ActivityRunRecord(
            run_id="run-a",
            plan_id="plan-a",
            admission=AdmittedRun("execution-request-a"),
            retry=RetryIdentity(1),
            status=ActivityRunStatus.CLAIMED,
            created_at="2026-07-16T00:04:00Z",
            metadata=BoundedEvidence.from_mapping({"worker": "worker-a"}),
        )
        event = ActivityEventRecord(
            event_id="event-a",
            run_id="run-a",
            ordinal=1,
            kind=ActivityEventKind.REQUEST_CLAIMED,
            occurred_at="2026-07-16T00:04:00Z",
            evidence=BoundedEvidence.from_mapping({"worker": "worker-a"}),
        )

        self.stores.execution.add_request(request)
        self.stores.execution.add_run(run)
        self.stores.execution.add_event(event)

        self.assertEqual(
            self.stores.execution.get_request("execution-request-a"), request
        )
        self.assertEqual(
            self.stores.execution.request_for_idempotency(
                "workspace-a", "execute-a"
            ),
            request,
        )
        self.assertEqual(
            self.stores.execution.runs_for_request("execution-request-a"),
            (run,),
        )
        self.assertEqual(self.stores.execution.events_for_run("run-a"), (event,))

    def test_uncommitted_unit_of_work_rolls_back_execution_and_history_together(self):
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        with PostgresUnitOfWork(
            lambda: psycopg.connect(database_url)
        ) as unit_of_work:
            self._seed_admission_truth(unit_of_work.stores)
            unit_of_work.stores.execution.add_request(self._request())

        counts = self.connection.execute(
            """
            SELECT
              (SELECT count(*) FROM cpk_operation_sessions),
              (SELECT count(*) FROM cpk_activity_plans),
              (SELECT count(*) FROM cpk_approval_requests),
              (SELECT count(*) FROM cpk_approval_decisions),
              (SELECT count(*) FROM cpk_execution_requests)
            """
        ).fetchone()
        self.assertEqual(counts, (0, 0, 0, 0, 0))

    def test_recovery_and_forward_compensation_failures_round_trip_together(self):
        self._seed_admission_truth(self.stores)
        self.stores.execution.add_request(self._request())
        self.stores.execution.add_run(
            ActivityRunRecord(
                run_id="run-a",
                plan_id="plan-a",
                admission=AdmittedRun("execution-request-a"),
                retry=RetryIdentity(1),
                status=ActivityRunStatus.PARTIALLY_FAILED,
                created_at="2026-07-16T00:04:00Z",
                started_at="2026-07-16T00:04:30Z",
                settled_at="2026-07-16T00:07:00Z",
            )
        )
        original_failure = FailureEvidence(
            FailureCategory.TERMINAL,
            "forward-failed",
            "The forward effect failed.",
        )
        compensation_failure = FailureEvidence(
            FailureCategory.OPERATOR_REVIEW,
            "compensation-failed",
            "The compensation effect also failed.",
        )
        recovery = RecoveryDecisionRecord(
            "decision-a",
            AcceptUncompensatedFailure(),
            RecoveryAuthority(
                "operator-a",
                "grant-a",
                (RecoveryScope.ACCEPT_LOSS,),
            ),
            "The remaining failure requires manual repair.",
        )
        events = (
            ActivityEventRecord(
                "event-forward-failed",
                "run-a",
                1,
                ActivityEventKind.STEP_FAILED,
                "2026-07-16T00:05:00Z",
                activity_id="start-api",
                failure=original_failure,
            ),
            ActivityEventRecord(
                "event-recovery",
                "run-a",
                2,
                ActivityEventKind.RECOVERY_DECISION_RECORDED,
                "2026-07-16T00:06:00Z",
                recovery=recovery,
            ),
            ActivityEventRecord(
                "event-compensation-failed",
                "run-a",
                3,
                ActivityEventKind.STEP_COMPENSATION_FAILED,
                "2026-07-16T00:07:00Z",
                activity_id="start-api",
                failure=compensation_failure,
            ),
        )
        for event in events:
            self.stores.execution.add_event(event)

        persisted = self.stores.execution.events_for_run("run-a")

        self.assertEqual(persisted, events)
        self.assertEqual(persisted[0].failure, original_failure)
        self.assertEqual(persisted[1].recovery, recovery)
        self.assertEqual(persisted[2].failure, compensation_failure)

    @staticmethod
    def _seed_admission_truth(stores: object, *, include_graphs: bool = False) -> None:
        stores.workspace.create(WorkspaceRecord("workspace-a", "Demo"))
        if include_graphs:
            graph = DeploymentGraph("execution", runtimes={
                "runtime-a": RuntimeRecord("runtime-a", RuntimeKind.DOCKER)
            })
            for graph_id, version in (("graph-a", 1), ("graph-b", 2)):
                stores.graph_topology.save(
                    GraphVersionRecord.from_graph(
                        graph_id=graph_id,
                        workspace_id="workspace-a",
                        version=version,
                        graph=graph,
                        created_by="operator",
                        created_at=f"2026-07-16T00:00:0{version}Z",
                    )
                )
        stores.activity_history.add_session(
            OperationSessionRecord(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="operator",
                title="Execute plan",
                status=OperationSessionStatus.OPEN,
                created_at="2026-07-16T00:00:00Z",
            )
        )
        stores.activity_history.add_plan(
            ActivityPlanRecord(
                plan_id="plan-a",
                session_id="session-a",
                base_graph_id="graph-a",
                desired_graph_id="graph-b",
                status="planned",
                created_at="2026-07-16T00:01:00Z",
                plan=ActivityPlan(
                    (
                        PlannedActivity(
                            ActivityId("start-runtime-a"),
                            StartRuntime(RuntimeTarget("runtime-a")),
                        ),
                    )
                ),
            )
        )
        stores.activity_history.add_approval_request(
            ApprovalRequestRecord(
                request_id="approval-request-a",
                session_id="session-a",
                plan_id="plan-a",
                requested_by="operator",
                requested_at="2026-07-16T00:02:00Z",
                required_scope="plan:approve",
                max_risk=RiskLevel.LOW,
                destructive=False,
            )
        )
        stores.activity_history.add_approval_decision(
            ApprovalDecisionRecord(
                decision_id="approval-decision-a",
                request_id="approval-request-a",
                actor_id="manager",
                decision=ApprovalDecisionKind.APPROVED,
                scope="plan:approve",
                decided_at="2026-07-16T00:03:00Z",
            )
        )

    @staticmethod
    def _request() -> ExecutionRequestRecord:
        return ExecutionRequestRecord(
            identity=ExecutionRequestIdentity(
                "execution-request-a", "workspace-a", "session-a", "plan-a"
            ),
            status=ExecutionRequestStatus.CLAIMED,
            requested_by="operator",
            requested_at="2026-07-16T00:03:30Z",
            approval_request_id="approval-request-a",
            approval_decision_id="approval-decision-a",
            idempotency=ExecutionIdempotency("execute-a", "fingerprint-a"),
            claim=ClaimIdentity(
                "worker-a",
                "2026-07-16T00:03:45Z",
                "2026-07-16T00:04:45Z",
            ),
        )


if __name__ == "__main__":
    unittest.main()
