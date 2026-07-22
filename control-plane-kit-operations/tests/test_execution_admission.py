from __future__ import annotations

import concurrent.futures
import inspect
import os
import unittest

import psycopg

from control_plane_kit_core.algebra import (
    BlockSockets,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
)
from control_plane_kit_core.operations.lifecycle import (
    ExecutionRequestStatus,
    LifecycleOperationKind,
)
from control_plane_kit_core.planning import (
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    ChangeTarget,
    NodeTarget,
    PlannedActivity,
    ReconcileNode,
    ReviewChange,
    ReviewReason,
    RiskLevel,
    StopNode,
    compile_activity_plan,
)
from control_plane_kit_core.planning.scenarios import switch_database_endpoint
from control_plane_kit_core.policies import ApprovalPolicy, PolicyScope
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    ProductRuntimeContract,
    instantiate_product,
)
from control_plane_kit_core.topology import (
    DeploymentGraph,
    FieldSubject,
    GraphSubject,
    StructuralField,
    compile_topology,
    diff_graphs,
    validate_graph,
)
from control_plane_kit_core.types import Protocol
from control_plane_kit_operations.admission import (
    ExecutionAdmissionCommandService,
    ExecutionAdmissionConflict,
    ExecutionAdmissionDenied,
    ExecutionAdmissionIdempotencyConflict,
    ExecutionReadinessRequired,
    ExternalReadinessAttestation,
    RequestPlanExecution,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    ActivityPlanRecord,
    ActivityPlanStatus,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    GraphVersionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
    OperationCommandService,
    StartOperationSession,
)


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class ExecutionAdmissionTests(unittest.TestCase):
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
        self.document = ProductDescriptorCodec().encode_document(
            self.product("hello-server")
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.workspaces.create(WorkspaceRecord("workspace-a", "Workspace A"))
            current = GraphVersionRecord.from_graph(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph=self.empty_graph("current"),
                created_by="operator-a",
                created_at="2026-07-22T12:00:00Z",
            )
            desired = GraphVersionRecord.from_graph(
                graph_id="graph-desired",
                workspace_id="workspace-a",
                version=2,
                graph=self.product_graph(),
                created_by="operator-a",
                created_at="2026-07-22T12:00:30Z",
            )
            stores.graphs.save(current)
            stores.graphs.save(desired)
            stores.workspaces.set_current_graph("workspace-a", current.graph_id)
            stores.workspaces.set_desired_graph("workspace-a", desired.graph_id)
            unit_of_work.commit()
        self.operation_service("session-a", "action-start").execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "Execute plan",
                IdempotencyKey("start"),
            )
        )
        self._base_plan = compile_activity_plan(
            diff_graphs(
                validate_graph(self.empty_graph("current")),
                validate_graph(self.product_graph()),
            )
        )
        self.seed_plan_truth(
            plan_id="plan-a",
            approval_request_id="approval-request-a",
            approval_decision_id="approval-decision-a",
            plan=self._base_plan,
        )

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def operation_service(self, *ids: str) -> OperationCommandService:
        return OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T12:01:00Z",
            id_factory=Sequence(*ids),
        )

    def admission_service(self, *ids: str) -> ExecutionAdmissionCommandService:
        return ExecutionAdmissionCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T12:04:00Z",
            id_factory=Sequence(*ids),
        )

    def command(
        self,
        *,
        workspace_id: str = "workspace-a",
        plan_id: str = "plan-a",
        approval_request_id: str = "approval-request-a",
        actor_id: str = "operator-a",
        scopes: tuple[PolicyScope, ...] = (PolicyScope.PLAN_EXECUTE,),
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

    def test_approved_current_plan_is_atomically_admitted_without_effect_dependency(
        self,
    ) -> None:
        result = self.admission_service("execution-a", "action-execute").execute(
            self.command()
        )

        self.assertEqual(result.request.identity.request_id, "execution-a")
        self.assertIs(result.request.status, ExecutionRequestStatus.QUEUED)
        self.assertIs(
            result.action.action_type,
            LifecycleOperationKind.ADMIT_EXECUTION,
        )
        self.assertEqual(result.action.payload["base_graph_id"], "graph-current")
        self.assertEqual(result.action.payload["desired_graph_id"], "graph-desired")
        self.assertNotIn(
            "effects",
            inspect.signature(ExecutionAdmissionCommandService.__init__).parameters,
        )

    def test_identical_replay_returns_original_and_changed_intent_conflicts(
        self,
    ) -> None:
        command = self.command()
        first = self.admission_service("execution-a", "action-execute").execute(
            command
        )
        replay = self.admission_service("unused-request", "unused-action").execute(
            command
        )

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.request, first.request)
        self.assertEqual(replay.action, first.action)
        with self.assertRaises(ExecutionAdmissionDenied):
            self.admission_service("unused-request", "unused-action").execute(
                self.command(scopes=())
            )
        with self.assertRaises(ExecutionAdmissionIdempotencyConflict):
            self.admission_service("unused-request", "unused-action").execute(
                self.command(actor_id="operator-b")
            )

    def test_concurrent_identical_admission_converges(self) -> None:
        def submit(ids: tuple[str, str]):
            return self.admission_service(*ids).execute(self.command())

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

    def test_late_action_failure_rolls_back_execution_request(self) -> None:
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.admission_service("execution-a", "action-start").execute(
                self.command()
            )

        with self.unit_of_work() as unit_of_work:
            with self.assertRaises(KeyError):
                unit_of_work.stores.execution.get_request("execution-a")

    def test_missing_scope_rejected_and_rejected_approval_rejected(self) -> None:
        with self.assertRaises(ExecutionAdmissionDenied):
            self.admission_service("unused", "unused").execute(
                self.command(scopes=())
            )

        self.seed_plan_truth(
            plan_id="plan-rejected",
            approval_request_id="approval-request-rejected",
            approval_decision_id="approval-decision-rejected",
            plan=self._base_plan,
            decision=ApprovalDecisionKind.REJECTED,
        )
        with self.assertRaises(ExecutionAdmissionDenied):
            self.admission_service("unused", "unused").execute(
                self.command(
                    plan_id="plan-rejected",
                    approval_request_id="approval-request-rejected",
                )
            )

    def test_empty_plan_is_valid_planning_truth_but_not_executable_work(self) -> None:
        self.seed_plan_truth(
            plan_id="plan-empty",
            approval_request_id="approval-request-empty",
            approval_decision_id="approval-decision-empty",
            plan=ActivityPlan(()),
        )

        with self.assertRaises(ExecutionAdmissionConflict):
            self.admission_service("unused", "unused").execute(
                self.command(
                    plan_id="plan-empty",
                    approval_request_id="approval-request-empty",
                )
            )

        with self.unit_of_work() as unit_of_work:
            self.assertIsNone(
                unit_of_work.stores.execution.request_for_idempotency(
                    "workspace-a",
                    "execute-a",
                )
            )

    def test_destructive_plan_cannot_use_forged_non_destructive_approval(
        self,
    ) -> None:
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
        self.seed_plan_truth(
            plan_id="plan-destructive",
            approval_request_id="approval-request-weak",
            approval_decision_id="approval-decision-weak",
            plan=destructive,
            max_risk=RiskLevel.HIGH,
            required_scope=PolicyScope.PLAN_APPROVE,
            destructive=False,
        )

        with self.assertRaises(ExecutionAdmissionDenied):
            self.admission_service("unused", "unused").execute(
                self.command(
                    plan_id="plan-destructive",
                    approval_request_id="approval-request-weak",
                )
            )

    def test_foreign_workspace_stale_graph_and_review_blocker_fail_closed(
        self,
    ) -> None:
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.workspaces.create(WorkspaceRecord("workspace-b", "Workspace B"))
            unit_of_work.commit()

        with self.assertRaises(ExecutionAdmissionConflict):
            self.admission_service("unused", "unused").execute(
                self.command(workspace_id="workspace-b")
            )

        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.set_desired_graph(
                "workspace-a",
                "graph-current",
            )
            unit_of_work.commit()
        with self.assertRaises(ExecutionAdmissionConflict):
            self.admission_service("unused", "unused").execute(self.command())
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.set_desired_graph(
                "workspace-a",
                "graph-desired",
            )
            unit_of_work.commit()

        self.seed_plan_truth(
            plan_id="plan-review",
            approval_request_id="approval-request-review",
            approval_decision_id="approval-decision-review",
            plan=review_plan(),
        )
        with self.assertRaises(ExecutionAdmissionConflict):
            self.admission_service("unused", "unused").execute(
                self.command(
                    plan_id="plan-review",
                    approval_request_id="approval-request-review",
                )
            )

    def test_database_endpoint_switch_requires_reference_only_readiness(self) -> None:
        scenario = switch_database_endpoint()
        plan = compile_activity_plan(
            diff_graphs(
                validate_graph(scenario.current_graph),
                validate_graph(scenario.desired_graph),
            )
        )
        self.seed_graphs(
            "graph-database-current",
            scenario.current_graph,
            "graph-database-desired",
            scenario.desired_graph,
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.set_current_graph(
                "workspace-a",
                "graph-database-current",
            )
            unit_of_work.stores.workspaces.set_desired_graph(
                "workspace-a",
                "graph-database-desired",
            )
            unit_of_work.commit()
        self.seed_plan_truth(
            plan_id="plan-database",
            approval_request_id="approval-request-database",
            approval_decision_id="approval-decision-database",
            plan=plan,
            base_graph_id="graph-database-current",
            desired_graph_id="graph-database-desired",
        )
        command = self.command(
            plan_id="plan-database",
            approval_request_id="approval-request-database",
        )
        with self.assertRaises(ExecutionReadinessRequired):
            self.admission_service("unused", "unused").execute(command)

        reconcile = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, ReconcileNode)
        )
        evidence = ExternalReadinessAttestation(
            activity_id=reconcile.activity_id.value,
            evidence_ref="migration-check/2026-07-22/a",
        )
        result = self.admission_service("execution-a", "action-execute").execute(
            self.command(
                plan_id="plan-database",
                approval_request_id="approval-request-database",
                readiness=(evidence,),
            )
        )
        self.assertEqual(
            result.action.payload["readiness"],
            [
                {
                    "activity_id": reconcile.activity_id.value,
                    "evidence_ref": "migration-check/2026-07-22/a",
                }
            ],
        )
        self.assertNotIn("password", repr(result.action.payload).lower())

    def test_readiness_evidence_rejects_values_urls_and_unbounded_text(self) -> None:
        for unsafe in (
            "https://evidence.example/check/a",
            "Bearer secret-value",
            "evidence/" + "x" * 257,
        ):
            with self.subTest(unsafe=unsafe[:24]):
                with self.assertRaises(InvalidOperationCommand):
                    ExternalReadinessAttestation("activity-a", unsafe)

    def test_readiness_evidence_cannot_claim_an_unrelated_activity(self) -> None:
        evidence = ExternalReadinessAttestation(
            self._base_plan.activities[0].activity_id.value,
            "migration-check/2026-07-22/a",
        )
        with self.assertRaises(ExecutionAdmissionConflict):
            self.admission_service("unused", "unused").execute(
                self.command(readiness=(evidence,))
            )

    def seed_graphs(
        self,
        current_id: str,
        current: DeploymentGraph,
        desired_id: str,
        desired: DeploymentGraph,
    ) -> None:
        with self.unit_of_work() as unit_of_work:
            for graph_id, graph, version in (
                (current_id, current, 3),
                (desired_id, desired, 4),
            ):
                unit_of_work.stores.graphs.save(
                    GraphVersionRecord.from_graph(
                        graph_id=graph_id,
                        workspace_id="workspace-a",
                        version=version,
                        graph=graph,
                        created_by="operator-a",
                        created_at="2026-07-22T12:00:30Z",
                    )
                )
            unit_of_work.commit()

    def seed_plan_truth(
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
        required_scope: PolicyScope | None = None,
        destructive: bool | None = None,
    ) -> None:
        requirement = ApprovalPolicy().requirement_for(plan)
        with self.unit_of_work() as unit_of_work:
            history = unit_of_work.stores.activity_history
            history.add_plan(
                ActivityPlanRecord(
                    plan_id=plan_id,
                    session_id="session-a",
                    base_graph_id=base_graph_id,
                    desired_graph_id=desired_graph_id,
                    status=ActivityPlanStatus.PLANNED,
                    created_at="2026-07-22T12:01:30Z",
                    plan=plan,
                )
            )
            history.add_approval_request(
                ApprovalRequestRecord(
                    request_id=approval_request_id,
                    session_id="session-a",
                    plan_id=plan_id,
                    requested_by="operator-a",
                    requested_at="2026-07-22T12:02:00Z",
                    required_scope=required_scope or requirement.required_scope,
                    max_risk=requirement.max_risk if max_risk is None else max_risk,
                    destructive=(
                        requirement.destructive if destructive is None else destructive
                    ),
                )
            )
            history.add_approval_decision(
                ApprovalDecisionRecord(
                    decision_id=approval_decision_id,
                    request_id=approval_request_id,
                    actor_id="manager-a",
                    decision=decision,
                    scope=required_scope or requirement.required_scope,
                    decided_at="2026-07-22T12:03:00Z",
                )
            )
            unit_of_work.commit()

    def product(self, name: str) -> ContainerServerProduct:
        return ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", name, 1),
            image=OciImageReference(
                "ghcr.io",
                f"openj92/control-plane-kit-servers/{name}",
                "sha256:" + "c" * 64,
                tag="v1",
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
            display_name=name,
            description="Server product used for admission tests.",
        )

    def product_graph(self) -> DeploymentGraph:
        block = instantiate_product(
            self.document.product,
            "app",
            ProductInstanceConfiguration(),
        )
        return compile_topology(
            DeploymentTopology("desired", DockerRuntime(children=(block,)))
        )

    def empty_graph(self, name: str) -> DeploymentGraph:
        return compile_topology(DeploymentTopology(name, DockerRuntime()))


def review_plan() -> ActivityPlan:
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


if __name__ == "__main__":
    unittest.main()
