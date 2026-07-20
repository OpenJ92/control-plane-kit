"""Postgres integration tests for approval request and decision commands."""

from __future__ import annotations

import concurrent.futures
import os
import threading
import unittest

import psycopg

from control_plane_kit import (
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    NodeTarget,
    PlannedActivity,
    RiskLevel,
    StartNode,
    StopNode,
)
from control_plane_kit.stores import (
    ActivityPlanRecord,
    ApprovalDecisionKind,
    OperationActionKind,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.workflows import (
    ApprovalAuthorizationDenied,
    ApprovalCommandService,
    ApprovalIdempotencyConflict,
    ApprovalStateConflict,
    CloseOperationSession,
    DecidePlanApproval,
    IdempotencyKey,
    OperationCommandService,
    RequestPlanApproval,
    StartOperationSession,
)
from tests.postgres_case import PostgresStoreTestCase


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class ApprovalCommandServiceTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stores.workspace.create(WorkspaceRecord("workspace-a", "Workspace A"))
        self._operation_service("session-a", "start-action").execute(
            StartOperationSession(
                "workspace-a",
                "jacob",
                "Approve plan",
                IdempotencyKey("start"),
            )
        )
        self._save_plan("plan-safe", _safe_plan())
        self._save_plan("plan-destructive", _destructive_plan())

    def _operation_service(self, *ids: str) -> OperationCommandService:
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return OperationCommandService(
            lambda: PostgresUnitOfWork(lambda: psycopg.connect(database_url)),
            clock=lambda: "2026-07-16T00:00:00Z",
            id_factory=Sequence(*ids),
        )

    def _service(self, *ids: str) -> ApprovalCommandService:
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return ApprovalCommandService(
            lambda: PostgresUnitOfWork(lambda: psycopg.connect(database_url)),
            clock=lambda: "2026-07-16T00:01:00Z",
            id_factory=Sequence(*ids),
        )

    def _save_plan(self, plan_id: str, plan: ActivityPlan) -> None:
        self.stores.activity_history.add_plan(
            ActivityPlanRecord(
                plan_id=plan_id,
                session_id="session-a",
                base_graph_id="graph-a",
                desired_graph_id="graph-b",
                status="planned",
                created_at="2026-07-16T00:00:30Z",
                plan=plan,
            )
        )

    def _request(
        self,
        *,
        plan_id: str = "plan-safe",
        scopes: tuple[str, ...] = ("plan:request",),
        key: str = "request-approval",
    ) -> RequestPlanApproval:
        return RequestPlanApproval(
            session_id="session-a",
            plan_id=plan_id,
            actor_id="jacob",
            actor_scopes=scopes,
            idempotency_key=IdempotencyKey(key),
            comment="Please review.",
        )

    def _decision(
        self,
        *,
        request_id: str = "request-a",
        scopes: tuple[str, ...] = ("plan:approve",),
        decision: ApprovalDecisionKind = ApprovalDecisionKind.APPROVED,
        key: str = "decide-approval",
    ) -> DecidePlanApproval:
        return DecidePlanApproval(
            session_id="session-a",
            request_id=request_id,
            actor_id="manager",
            actor_scopes=scopes,
            decision=decision,
            idempotency_key=IdempotencyKey(key),
            comment="Reviewed.",
        )

    def test_request_is_pending_fact_with_plan_risk_evidence(self):
        result = self._service("request-a", "request-action").execute(self._request())

        self.assertEqual(result.descriptor()["state"], "pending")
        self.assertEqual(result.request.required_scope, "plan:approve")
        self.assertEqual(result.request.max_risk, RiskLevel.LOW)
        self.assertFalse(result.request.destructive)
        self.assertIsNone(
            self.stores.activity_history.approval_decision_for_request("request-a")
        )
        self.assertEqual(
            self.stores.activity_history.actions_for_session("session-a")[-1].action_type,
            OperationActionKind.APPROVAL_REQUESTED,
        )

    def test_request_and_decision_are_distinct_durable_facts(self):
        self._service("request-a", "request-action").execute(self._request())
        result = self._service("decision-a", "decision-action").execute(
            self._decision()
        )

        persisted = self.stores.activity_history.approval_decision_for_request(
            "request-a"
        )
        self.assertEqual(persisted, result.decision)
        self.assertEqual(result.descriptor()["state"], "approved")
        self.assertEqual(
            [
                action.action_type
                for action in self.stores.activity_history.actions_for_session("session-a")
            ],
            [
                OperationActionKind.SESSION_STARTED,
                OperationActionKind.APPROVAL_REQUESTED,
                OperationActionKind.APPROVAL_DECIDED,
            ],
        )

    def test_authority_fails_closed_and_destructive_plan_requires_stronger_scope(self):
        with self.assertRaises(ApprovalAuthorizationDenied):
            self._service("request-a", "request-action").execute(
                self._request(scopes=())
            )

        request = self._service("request-b", "request-action-b").execute(
            self._request(plan_id="plan-destructive", key="request-destructive")
        ).request
        self.assertTrue(request.destructive)
        self.assertEqual(request.required_scope, "plan:approve-destructive")
        with self.assertRaises(ApprovalAuthorizationDenied):
            self._service("decision-a", "decision-action").execute(
                self._decision(request_id=request.request_id)
            )
        approved = self._service("decision-b", "decision-action-b").execute(
            self._decision(
                request_id=request.request_id,
                scopes=("plan:approve-destructive",),
                key="decide-destructive",
            )
        )
        self.assertEqual(approved.decision.scope, "plan:approve-destructive")

    def test_request_and_decision_replay_without_duplicate_history(self):
        request_command = self._request()
        first_request = self._service("request-a", "request-action").execute(
            request_command
        )
        replay_request = self._service("unused", "unused").execute(request_command)
        self.assertTrue(replay_request.replayed)
        self.assertEqual(replay_request.request.request_id, first_request.request.request_id)

        decision_command = self._decision()
        first_decision = self._service("decision-a", "decision-action").execute(
            decision_command
        )
        replay_decision = self._service("unused", "unused").execute(decision_command)
        self.assertTrue(replay_decision.replayed)
        self.assertEqual(
            replay_decision.decision.decision_id,
            first_decision.decision.decision_id,
        )
        self.assertEqual(
            len(self.stores.activity_history.actions_for_session("session-a")),
            3,
        )

    def test_conflicting_reuse_and_second_decision_are_rejected(self):
        self._service("request-a", "request-action").execute(self._request())
        with self.assertRaises(ApprovalIdempotencyConflict):
            self._service("unused", "unused").execute(
                self._request(plan_id="plan-destructive")
            )

        self._service("decision-a", "decision-action").execute(self._decision())
        with self.assertRaises(ApprovalStateConflict):
            self._service("decision-b", "decision-action-b").execute(
                self._decision(
                    decision=ApprovalDecisionKind.REJECTED,
                    key="another-decision",
                )
            )

    def test_late_action_failures_roll_back_request_and_decision(self):
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._service("request-fail", "start-action").execute(self._request())
        with self.assertRaises(KeyError):
            self.stores.activity_history.get_approval_request("request-fail")

        self._service("request-a", "request-action").execute(self._request())
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._service("decision-fail", "start-action").execute(self._decision())
        self.assertIsNone(
            self.stores.activity_history.approval_decision_for_request("request-a")
        )

    def test_concurrent_identical_requests_converge_on_one_request(self):
        barrier = threading.Barrier(2)

        def submit(ids: tuple[str, str]):
            barrier.wait(timeout=5)
            return self._service(*ids).execute(self._request())

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    submit,
                    (("request-a", "action-a"), ("request-b", "action-b")),
                )
            )

        request_ids = {result.request.request_id for result in results}
        self.assertEqual(len(request_ids), 1)
        self.assertTrue(request_ids <= {"request-a", "request-b"})
        self.assertEqual(len({result.action.action_id for result in results}), 1)
        self.assertEqual(sum(result.replayed for result in results), 1)
        self.assertEqual(
            len(self.stores.activity_history.approval_requests_for_session("session-a")),
            1,
        )

    def test_concurrent_identical_decisions_converge_on_one_decision(self):
        self._service("request-a", "request-action").execute(self._request())
        barrier = threading.Barrier(2)

        def submit(ids: tuple[str, str]):
            barrier.wait(timeout=5)
            return self._service(*ids).execute(self._decision())

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    submit,
                    (("decision-a", "action-a"), ("decision-b", "action-b")),
                )
            )

        decision_ids = {result.decision.decision_id for result in results}
        self.assertEqual(len(decision_ids), 1)
        self.assertTrue(decision_ids <= {"decision-a", "decision-b"})
        self.assertEqual(len({result.action.action_id for result in results}), 1)
        self.assertEqual(sum(result.replayed for result in results), 1)

    def test_concurrent_competing_decisions_publish_exactly_one_fact(self):
        self._service("request-a", "request-action").execute(self._request())
        barrier = threading.Barrier(2)

        def decide(decision: ApprovalDecisionKind, ids: tuple[str, str], key: str):
            barrier.wait(timeout=5)
            try:
                return self._service(*ids).execute(
                    self._decision(decision=decision, key=key)
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

        self.assertEqual(sum(isinstance(value, ApprovalStateConflict) for value in outcomes), 1)
        persisted = self.stores.activity_history.approval_decision_for_request(
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
                    for action in self.stores.activity_history.actions_for_session(
                        "session-a"
                    )
                    if action.action_type is OperationActionKind.APPROVAL_DECIDED
                ]
            ),
            1,
        )

    def test_concurrent_request_and_close_serialize_without_partial_approval(self):
        barrier = threading.Barrier(2)

        def request():
            barrier.wait(timeout=5)
            try:
                return self._service("request-a", "request-action").execute(
                    self._request()
                )
            except ApprovalStateConflict as error:
                return error

        def close():
            barrier.wait(timeout=5)
            return self._operation_service("close-action").execute(
                CloseOperationSession(
                    "session-a",
                    "jacob",
                    IdempotencyKey("close"),
                )
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            request_future = executor.submit(request)
            close_future = executor.submit(close)
            request_outcome = request_future.result()
            close_future.result()

        actions = self.stores.activity_history.actions_for_session("session-a")
        kinds = tuple(action.action_type for action in actions)
        if isinstance(request_outcome, ApprovalStateConflict):
            self.assertEqual(
                kinds,
                (
                    OperationActionKind.SESSION_STARTED,
                    OperationActionKind.SESSION_CLOSED,
                ),
            )
            self.assertEqual(
                self.stores.activity_history.approval_requests_for_session(
                    "session-a"
                ),
                (),
            )
        else:
            self.assertEqual(
                kinds,
                (
                    OperationActionKind.SESSION_STARTED,
                    OperationActionKind.APPROVAL_REQUESTED,
                    OperationActionKind.SESSION_CLOSED,
                ),
            )


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
