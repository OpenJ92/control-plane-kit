from __future__ import annotations

import concurrent.futures
import inspect
import os

import psycopg

from control_plane_kit import (
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    ChangeTarget,
    DeploymentGraph,
    PlannedActivity,
    ReviewChange,
    ReviewReason,
    RiskLevel,
    RuntimeKind,
    RuntimeRecord,
    NodeTarget,
    StopNode,
    SocketConnectionTarget,
    SwitchSocketConnection,
)
from control_plane_kit.stores import (
    ActivityPlanRecord,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    GraphVersionRecord,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.topology import FieldSubject, GraphSubject, StructuralField
from control_plane_kit.planning import compile_activity_plan
from control_plane_kit.policies import ApprovalPolicy
from control_plane_kit.topology import diff_graphs, validate_graph
from control_plane_kit.workflows import (
    ExecutionAdmissionCommandService,
    ExecutionAdmissionConflict,
    ExecutionAdmissionDenied,
    ExecutionAdmissionIdempotencyConflict,
    ExecutionReadinessRequired,
    ExternalReadinessAttestation,
    IdempotencyKey,
    RequestPlanExecution,
)
from control_plane_kit.workflows.commands import InvalidOperationCommand
from tests.postgres_case import PostgresStoreTestCase


class Sequence:
    def __init__(self, *values: str) -> None:
        self.values = list(values)

    def __call__(self) -> str:
        return self.values.pop(0)


class ExecutionAdmissionTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._seed()

    def _unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def _service(self, *ids: str) -> ExecutionAdmissionCommandService:
        return ExecutionAdmissionCommandService(
            self._unit_of_work,
            clock=lambda: "2026-07-16T00:04:00Z",
            id_factory=Sequence(*ids),
        )

    def _command(
        self,
        *,
        workspace_id: str = "workspace-a",
        plan_id: str = "plan-a",
        approval_request_id: str = "approval-request-a",
        actor_id: str = "operator",
        scopes: tuple[str, ...] = ("plan:execute",),
        key: str = "execute-a",
        readiness: tuple[ExternalReadinessAttestation, ...] = (),
    ) -> RequestPlanExecution:
        return RequestPlanExecution(
            workspace_id=workspace_id,
            session_id="session-a",
            plan_id=plan_id,
            approval_request_id=approval_request_id,
            actor_id=actor_id,
            actor_scopes=scopes,
            idempotency_key=IdempotencyKey(key),
            readiness=readiness,
        )

    def test_approved_current_plan_is_atomically_admitted_without_effect_dependency(self):
        result = self._service("execution-a", "execution-action").execute(
            self._command()
        )

        self.assertEqual(result.request.identity.request_id, "execution-a")
        self.assertEqual(result.action.action_type, OperationActionKind.EXECUTION_REQUESTED)
        self.assertEqual(result.action.payload["base_graph_id"], "graph-current")
        self.assertEqual(result.action.payload["desired_graph_id"], "graph-desired")
        self.assertNotIn(
            "effects",
            inspect.signature(ExecutionAdmissionCommandService.__init__).parameters,
        )

    def test_identical_replay_returns_original_and_changed_intent_conflicts(self):
        command = self._command()
        first = self._service("execution-a", "execution-action").execute(command)
        replay = self._service("unused-request", "unused-action").execute(command)

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.request, first.request)
        self.assertEqual(replay.action, first.action)
        with self.assertRaises(ExecutionAdmissionDenied):
            self._service("unused-request", "unused-action").execute(
                self._command(scopes=())
            )
        with self.assertRaises(ExecutionAdmissionIdempotencyConflict):
            self._service("unused-request", "unused-action").execute(
                self._command(actor_id="another-operator")
            )

    def test_concurrent_identical_admission_converges(self):
        def submit(ids: tuple[str, str]):
            return self._service(*ids).execute(self._command())

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    submit,
                    (("execution-a", "action-a"), ("execution-b", "action-b")),
                )
            )

        self.assertEqual(
            len({value.request.identity.request_id for value in results}),
            1,
        )
        self.assertEqual(sum(value.replayed for value in results), 1)

    def test_late_action_failure_rolls_back_execution_request(self):
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._service("execution-a", "start-action").execute(self._command())

        with self.assertRaises(KeyError):
            self.stores.execution.get_request("execution-a")

    def test_missing_scope_rejected_and_rejected_approval_rejected(self):
        with self.assertRaises(ExecutionAdmissionDenied):
            self._service("unused", "unused").execute(self._command(scopes=()))

        self._seed_plan_truth(
            plan_id="plan-rejected",
            approval_request_id="approval-request-rejected",
            approval_decision_id="approval-decision-rejected",
            plan=self._base_plan,
            decision=ApprovalDecisionKind.REJECTED,
        )
        with self.assertRaises(ExecutionAdmissionDenied):
            self._service("unused", "unused").execute(
                self._command(
                    plan_id="plan-rejected",
                    approval_request_id="approval-request-rejected",
                )
            )

    def test_empty_plan_is_valid_planning_truth_but_not_executable_work(self):
        self._seed_plan_truth(
            plan_id="plan-empty",
            approval_request_id="approval-request-empty",
            approval_decision_id="approval-decision-empty",
            plan=ActivityPlan(()),
        )

        with self.assertRaises(ExecutionAdmissionConflict):
            self._service("unused", "unused").execute(
                self._command(
                    plan_id="plan-empty",
                    approval_request_id="approval-request-empty",
                )
            )
        self.assertEqual(
            self.stores.execution.request_for_idempotency(
                "workspace-a", "execute-a"
            ),
            None,
        )

    def test_destructive_plan_cannot_use_forged_non_destructive_approval(self):
        destructive = ActivityPlan(
            (
                PlannedActivity(
                    ActivityId("stop-node"),
                    StopNode(NodeTarget("node-a")),
                    risk=RiskLevel.HIGH,
                    impact=ActivityImpact.DESTRUCTIVE,
                ),
            )
        )
        self._seed_plan_truth(
            plan_id="plan-destructive",
            approval_request_id="approval-request-weak",
            approval_decision_id="approval-decision-weak",
            plan=destructive,
            max_risk=RiskLevel.HIGH,
            required_scope="plan:approve",
            destructive=False,
        )

        with self.assertRaises(ExecutionAdmissionDenied):
            self._service("unused", "unused").execute(
                self._command(
                    plan_id="plan-destructive",
                    approval_request_id="approval-request-weak",
                )
            )

    def test_foreign_workspace_stale_graph_and_review_blocker_fail_closed(self):
        self.stores.workspace.create(WorkspaceRecord("workspace-b", "B"))
        with self.assertRaises(ExecutionAdmissionConflict):
            self._service("unused", "unused").execute(
                self._command(workspace_id="workspace-b")
            )

        self.stores.workspace.set_desired_graph("workspace-a", "graph-current")
        with self.assertRaises(ExecutionAdmissionConflict):
            self._service("unused", "unused").execute(self._command())
        self.stores.workspace.set_desired_graph("workspace-a", "graph-desired")

        self._seed_plan_truth(
            plan_id="plan-review",
            approval_request_id="approval-request-review",
            approval_decision_id="approval-decision-review",
            plan=_review_plan(),
        )
        with self.assertRaises(ExecutionAdmissionConflict):
            self._service("unused", "unused").execute(
                self._command(
                    plan_id="plan-review",
                    approval_request_id="approval-request-review",
                )
            )

    def test_database_endpoint_switch_requires_reference_only_readiness(self):
        from examples.scenarios import switch_database_endpoint

        scenario = switch_database_endpoint()
        self._seed_graphs(
            "graph-database-current",
            scenario.current_graph,
            "graph-database-desired",
            scenario.desired_graph,
        )
        self.stores.workspace.set_current_graph(
            "workspace-a", "graph-database-current"
        )
        self.stores.workspace.set_desired_graph(
            "workspace-a", "graph-database-desired"
        )
        plan = compile_activity_plan(
            diff_graphs(
                validate_graph(scenario.current_graph),
                validate_graph(scenario.desired_graph),
            )
        )
        self._seed_plan_truth(
            plan_id="plan-database",
            approval_request_id="approval-request-database",
            approval_decision_id="approval-decision-database",
            plan=plan,
            base_graph_id="graph-database-current",
            desired_graph_id="graph-database-desired",
        )
        command = self._command(
            plan_id="plan-database",
            approval_request_id="approval-request-database",
        )
        with self.assertRaises(ExecutionReadinessRequired):
            self._service("unused", "unused").execute(command)

        switch = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, SwitchSocketConnection)
        )
        evidence = ExternalReadinessAttestation(
            activity_id=switch.activity_id.value,
            evidence_ref="migration-check/2026-07-16/a",
        )
        result = self._service("execution-a", "execution-action").execute(
            self._command(
                plan_id="plan-database",
                approval_request_id="approval-request-database",
                readiness=(evidence,),
            )
        )
        self.assertEqual(
            result.action.payload["readiness"],
            [
                {
                    "activity_id": switch.activity_id.value,
                    "evidence_ref": "migration-check/2026-07-16/a",
                }
            ],
        )
        self.assertNotIn("password", repr(result.action.payload).lower())

    def test_readiness_evidence_rejects_values_urls_and_unbounded_text(self):
        for unsafe in (
            "https://evidence.example/check/a",
            "Bearer secret-value",
            "evidence/" + "x" * 257,
        ):
            with self.subTest(unsafe=unsafe[:24]):
                with self.assertRaises(InvalidOperationCommand):
                    ExternalReadinessAttestation("activity-a", unsafe)

    def test_readiness_evidence_cannot_claim_an_unrelated_activity(self):
        evidence = ExternalReadinessAttestation(
            self._base_plan.activities[0].activity_id.value,
            "migration-check/2026-07-16/a",
        )
        with self.assertRaises(ExecutionAdmissionConflict):
            self._service("unused", "unused").execute(
                self._command(readiness=(evidence,))
            )

    def _seed_graphs(
        self,
        current_id: str,
        current: DeploymentGraph,
        desired_id: str,
        desired: DeploymentGraph,
    ) -> None:
        for graph_id, graph, version in (
            (current_id, current, 3),
            (desired_id, desired, 4),
        ):
            self.stores.graph_topology.save(
                GraphVersionRecord.from_graph(
                    graph_id=graph_id,
                    workspace_id="workspace-a",
                    version=version,
                    graph=graph,
                    created_by="operator",
                    created_at="2026-07-16T00:00:30Z",
                )
            )

    def _seed_plan_truth(
        self,
        *,
        plan_id: str,
        approval_request_id: str,
        approval_decision_id: str,
        plan: ActivityPlan,
        decision: ApprovalDecisionKind = ApprovalDecisionKind.APPROVED,
        base_graph_id: str = "graph-current",
        desired_graph_id: str = "graph-desired",
        max_risk: RiskLevel | None = None,
        required_scope: str | None = None,
        destructive: bool | None = None,
    ) -> None:
        requirement = ApprovalPolicy().requirement_for(plan)
        self.stores.activity_history.add_plan(
            ActivityPlanRecord(
                plan_id,
                "session-a",
                base_graph_id,
                desired_graph_id,
                "planned",
                "2026-07-16T00:01:00Z",
                plan,
            )
        )
        self.stores.activity_history.add_approval_request(
            ApprovalRequestRecord(
                approval_request_id,
                "session-a",
                plan_id,
                "operator",
                "2026-07-16T00:02:00Z",
                required_scope or requirement.required_scope,
                requirement.max_risk if max_risk is None else max_risk,
                requirement.destructive if destructive is None else destructive,
            )
        )
        self.stores.activity_history.add_approval_decision(
            ApprovalDecisionRecord(
                approval_decision_id,
                approval_request_id,
                "manager",
                decision,
                required_scope or requirement.required_scope,
                "2026-07-16T00:03:00Z",
            )
        )

    def _seed(self) -> None:
        self.stores.workspace.create(WorkspaceRecord("workspace-a", "A"))
        current = DeploymentGraph("current")
        desired = DeploymentGraph(
            "desired",
            runtimes={
                "runtime-a": RuntimeRecord(
                    "runtime-a",
                    RuntimeKind.DRY_RUN,
                )
            },
        )
        for graph_id, graph, version in (
            ("graph-current", current, 1),
            ("graph-desired", desired, 2),
        ):
            self.stores.graph_topology.save(
                GraphVersionRecord.from_graph(
                    graph_id=graph_id,
                    workspace_id="workspace-a",
                    version=version,
                    graph=graph,
                    created_by="operator",
                    created_at="2026-07-16T00:00:00Z",
                )
            )
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")
        self.stores.workspace.set_desired_graph("workspace-a", "graph-desired")
        self.stores.activity_history.add_session(
            OperationSessionRecord(
                "session-a",
                "workspace-a",
                "operator",
                "Execute",
                OperationSessionStatus.OPEN,
                "2026-07-16T00:00:00Z",
            )
        )
        self.stores.activity_history.add_action(
            OperationActionRecord(
                "start-action",
                "session-a",
                1,
                OperationActionKind.SESSION_STARTED,
                "operator",
                created_at="2026-07-16T00:00:00Z",
            )
        )
        self._base_plan = compile_activity_plan(
            diff_graphs(validate_graph(current), validate_graph(desired))
        )
        self._seed_plan_truth(
            plan_id="plan-a",
            approval_request_id="approval-request-a",
            approval_decision_id="approval-decision-a",
            plan=self._base_plan,
        )


def _review_plan() -> ActivityPlan:
    return ActivityPlan(
        (
            PlannedActivity(
                ActivityId("review"),
                ReviewChange(
                    ChangeTarget(
                        FieldSubject(GraphSubject(), StructuralField.GRAPH_NAME)
                    ),
                    ReviewReason.AMBIGUOUS_CHANGE,
                ),
                risk=RiskLevel.HIGH,
            ),
        )
    )
