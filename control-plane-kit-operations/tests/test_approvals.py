from __future__ import annotations

import concurrent.futures
import os
import threading
import unittest

import psycopg

from control_plane_kit_core.planning import (
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    NodeTarget,
    PlannedActivity,
    RiskLevel,
    StartNode,
    StopNode,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.approvals import (
    ApprovalAuthorizationDenied,
    ApprovalCommandService,
    ApprovalIdempotencyConflict,
    ApprovalStateConflict,
    DecideApproval,
    RequestApproval,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    ActivityPlanRecord,
    ActivityPlanStatus,
    ApprovalDecisionKind,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import (
    CloseOperationSession,
    IdempotencyKey,
    OperationCommandService,
    StartOperationSession,
)


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class ApprovalCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-a", "Workspace A")
            )
            unit_of_work.commit()
        self.operation_service("session-a", "action-start").execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "Approve plan",
                IdempotencyKey("start"),
            )
        )
        self.save_plan("plan-safe", _safe_plan())
        self.save_plan("plan-destructive", _destructive_plan())

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def operation_service(self, *ids: str) -> OperationCommandService:
        return OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T11:00:00Z",
            id_factory=Sequence(*ids),
        )

    def approval_service(self, *ids: str) -> ApprovalCommandService:
        return ApprovalCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T11:01:00Z",
            id_factory=Sequence(*ids),
        )

    def save_plan(self, plan_id: str, plan: ActivityPlan) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.activity_history.add_plan(
                ActivityPlanRecord(
                    plan_id=plan_id,
                    session_id="session-a",
                    base_graph_id="graph-current",
                    desired_graph_id="graph-desired",
                    status=ActivityPlanStatus.PLANNED,
                    created_at="2026-07-22T11:00:30Z",
                    plan=plan,
                )
            )
            unit_of_work.commit()

    def request(
        self,
        *,
        plan_id: str = "plan-safe",
        scopes: tuple[PolicyScope, ...] = (PolicyScope.PLAN_REQUEST,),
        key: str = "request-approval",
    ) -> RequestApproval:
        return RequestApproval(
            session_id="session-a",
            plan_id=plan_id,
            actor_id="operator-a",
            actor_scopes=scopes,
            idempotency_key=IdempotencyKey(key),
            comment="Please review.",
        )

    def decision(
        self,
        *,
        request_id: str = "request-a",
        scopes: tuple[PolicyScope, ...] = (PolicyScope.PLAN_APPROVE,),
        decision: ApprovalDecisionKind = ApprovalDecisionKind.APPROVED,
        key: str = "decide-approval",
    ) -> DecideApproval:
        return DecideApproval(
            session_id="session-a",
            request_id=request_id,
            actor_id="manager-a",
            actor_scopes=scopes,
            decision=decision,
            idempotency_key=IdempotencyKey(key),
            comment="Reviewed.",
        )

    def test_request_is_pending_fact_with_plan_risk_evidence(self) -> None:
        result = self.approval_service("request-a", "action-request").execute(
            self.request()
        )

        self.assertEqual(result.descriptor()["state"], "pending")
        self.assertEqual(result.request.required_scope, PolicyScope.PLAN_APPROVE)
        self.assertEqual(result.request.max_risk, RiskLevel.LOW)
        self.assertFalse(result.request.destructive)

        with self.unit_of_work() as unit_of_work:
            history = unit_of_work.stores.activity_history
            self.assertIsNone(history.approval_decision_for_request("request-a"))
            self.assertEqual(
                history.actions_for_session("session-a")[-1].action_type.value,
                "request-approval",
            )
            unit_of_work.commit()

    def test_request_and_decision_are_distinct_durable_facts(self) -> None:
        self.approval_service("request-a", "action-request").execute(self.request())
        result = self.approval_service("decision-a", "action-decision").execute(
            self.decision()
        )

        with self.unit_of_work() as unit_of_work:
            history = unit_of_work.stores.activity_history
            self.assertEqual(
                history.approval_decision_for_request("request-a"),
                result.decision,
            )
            self.assertEqual(result.descriptor()["state"], "approved")
            self.assertEqual(
                tuple(action.action_type.value for action in history.actions_for_session("session-a")),
                (
                    "start-operation-session",
                    "request-approval",
                    "decide-approval",
                ),
            )
            unit_of_work.commit()

    def test_authority_fails_closed_and_destructive_plan_requires_scope(self) -> None:
        with self.assertRaises(ApprovalAuthorizationDenied):
            self.approval_service("request-a", "action-request").execute(
                self.request(scopes=())
            )

        request = self.approval_service("request-b", "action-request-b").execute(
            self.request(
                plan_id="plan-destructive",
                key="request-destructive",
            )
        ).request
        self.assertTrue(request.destructive)
        self.assertEqual(
            request.required_scope,
            PolicyScope.PLAN_APPROVE_DESTRUCTIVE,
        )
        with self.assertRaises(ApprovalAuthorizationDenied):
            self.approval_service("decision-a", "action-decision").execute(
                self.decision(request_id=request.request_id)
            )
        approved = self.approval_service("decision-b", "action-decision-b").execute(
            self.decision(
                request_id=request.request_id,
                scopes=(PolicyScope.PLAN_APPROVE_DESTRUCTIVE,),
                key="decide-destructive",
            )
        )
        self.assertEqual(approved.decision.scope, PolicyScope.PLAN_APPROVE_DESTRUCTIVE)

    def test_request_and_decision_replay_without_duplicate_history(self) -> None:
        request_command = self.request()
        first_request = self.approval_service("request-a", "action-request").execute(
            request_command
        )
        replay_request = self.approval_service("unused", "unused").execute(
            request_command
        )
        self.assertTrue(replay_request.replayed)
        self.assertEqual(replay_request.request, first_request.request)

        decision_command = self.decision()
        first_decision = self.approval_service("decision-a", "action-decision").execute(
            decision_command
        )
        replay_decision = self.approval_service("unused", "unused").execute(
            decision_command
        )
        self.assertTrue(replay_decision.replayed)
        self.assertEqual(replay_decision.decision, first_decision.decision)

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                len(unit_of_work.stores.activity_history.actions_for_session("session-a")),
                3,
            )
            unit_of_work.commit()

    def test_conflicting_reuse_and_second_decision_are_rejected(self) -> None:
        self.approval_service("request-a", "action-request").execute(self.request())
        with self.assertRaises(ApprovalIdempotencyConflict):
            self.approval_service("unused", "unused").execute(
                self.request(plan_id="plan-destructive")
            )

        self.approval_service("decision-a", "action-decision").execute(self.decision())
        with self.assertRaises(ApprovalStateConflict):
            self.approval_service("decision-b", "action-decision-b").execute(
                self.decision(
                    decision=ApprovalDecisionKind.REJECTED,
                    key="another-decision",
                )
            )

    def test_late_action_failure_rolls_back_request_and_decision(self) -> None:
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.approval_service("request-fail", "action-start").execute(
                self.request()
            )

        with self.unit_of_work() as unit_of_work:
            with self.assertRaises(KeyError):
                unit_of_work.stores.activity_history.get_approval_request(
                    "request-fail"
                )
            unit_of_work.commit()

        self.approval_service("request-a", "action-request").execute(self.request())
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.approval_service("decision-fail", "action-start").execute(
                self.decision()
            )

        with self.unit_of_work() as unit_of_work:
            self.assertIsNone(
                unit_of_work.stores.activity_history.approval_decision_for_request(
                    "request-a"
                )
            )
            unit_of_work.commit()

    def test_concurrent_identical_requests_converge_on_one_request(self) -> None:
        barrier = threading.Barrier(2)

        def submit(ids: tuple[str, str]):
            barrier.wait(timeout=5)
            return self.approval_service(*ids).execute(self.request())

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    submit,
                    (("request-a", "action-a"), ("request-b", "action-b")),
                )
            )

        self.assertEqual(len({result.request.request_id for result in results}), 1)
        self.assertEqual(len({result.action.action_id for result in results}), 1)
        self.assertEqual(sum(result.replayed for result in results), 1)

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                len(
                    unit_of_work.stores.activity_history.approval_requests_for_session(
                        "session-a"
                    )
                ),
                1,
            )
            unit_of_work.commit()

    def test_concurrent_competing_decisions_publish_exactly_one_fact(self) -> None:
        self.approval_service("request-a", "action-request").execute(self.request())
        barrier = threading.Barrier(2)

        def decide(decision: ApprovalDecisionKind, ids: tuple[str, str], key: str):
            barrier.wait(timeout=5)
            try:
                return self.approval_service(*ids).execute(
                    self.decision(decision=decision, key=key)
                )
            except ApprovalStateConflict as error:
                return error

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            approved = executor.submit(
                decide,
                ApprovalDecisionKind.APPROVED,
                ("approved", "approved-action"),
                "approve",
            )
            rejected = executor.submit(
                decide,
                ApprovalDecisionKind.REJECTED,
                ("rejected", "rejected-action"),
                "reject",
            )
            outcomes = (approved.result(), rejected.result())

        self.assertEqual(
            sum(isinstance(value, ApprovalStateConflict) for value in outcomes),
            1,
        )
        with self.unit_of_work() as unit_of_work:
            persisted = unit_of_work.stores.activity_history.approval_decision_for_request(
                "request-a"
            )
            self.assertIsNotNone(persisted)
            self.assertIn(
                persisted.decision,
                (ApprovalDecisionKind.APPROVED, ApprovalDecisionKind.REJECTED),
            )
            self.assertEqual(
                len(
                    [
                        action
                        for action in unit_of_work.stores.activity_history.actions_for_session(
                            "session-a"
                        )
                        if action.action_type.value == "decide-approval"
                    ]
                ),
                1,
            )
            unit_of_work.commit()

    def test_concurrent_request_and_close_serialize_without_partial_approval(self) -> None:
        barrier = threading.Barrier(2)

        def request():
            barrier.wait(timeout=5)
            try:
                return self.approval_service("request-a", "action-request").execute(
                    self.request()
                )
            except ApprovalStateConflict as error:
                return error

        def close():
            barrier.wait(timeout=5)
            return self.operation_service("action-close").execute(
                CloseOperationSession(
                    "session-a",
                    "operator-a",
                    IdempotencyKey("close"),
                )
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            request_future = executor.submit(request)
            close_future = executor.submit(close)
            request_outcome = request_future.result()
            close_future.result()

        with self.unit_of_work() as unit_of_work:
            kinds = tuple(
                action.action_type.value
                for action in unit_of_work.stores.activity_history.actions_for_session(
                    "session-a"
                )
            )
            if isinstance(request_outcome, ApprovalStateConflict):
                self.assertEqual(
                    kinds,
                    (
                        "start-operation-session",
                        "close-operation-session",
                    ),
                )
                self.assertEqual(
                    unit_of_work.stores.activity_history.approval_requests_for_session(
                        "session-a"
                    ),
                    (),
                )
            else:
                self.assertEqual(
                    kinds,
                    (
                        "start-operation-session",
                        "request-approval",
                        "close-operation-session",
                    ),
                )
            unit_of_work.commit()


def _safe_plan() -> ActivityPlan:
    return ActivityPlan(
        (
            PlannedActivity(
                ActivityId("start-api"),
                StartNode(NodeTarget("api")),
            ),
        )
    )


def _destructive_plan() -> ActivityPlan:
    return ActivityPlan(
        (
            PlannedActivity(
                ActivityId("stop-api"),
                StopNode(NodeTarget("api")),
                risk=RiskLevel.HIGH,
                impact=ActivityImpact.DESTRUCTIVE,
            ),
        )
    )


if __name__ == "__main__":
    unittest.main()
