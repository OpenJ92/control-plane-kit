from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from itertools import count
import os
import unittest

import psycopg

from control_plane_kit import (
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    DeploymentRecipe,
    DockerRuntime,
    PackageServerProduct,
    SocketConnection,
    StartNode,
    VerificationCapability,
    VerificationCheckMaterial,
    VerificationCompleted,
    VerificationIdentity,
    VerificationInterpreterRegistry,
    VerificationOutcome,
    compile_recipe,
    materialize_verification_contract,
)
from control_plane_kit.application.deploy import (
    AdvancedDeployment,
    AdvancementGrant,
    AdmissionGrant,
    ApprovalGrant,
    ApprovalSuspension,
    ClaimGrant,
    DeploymentExecutionGrant,
    DeploymentPlanRequest,
    DeploymentProgram,
    DeploymentProgramServices,
    NoDeploymentChanges,
    PlanningServices,
)
from control_plane_kit.read_services import InstanceReadService
from control_plane_kit.stores import (
    GraphVersionRecord,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
    ActivityPlanningGraphInvalid,
    ApprovalCommandService,
    CurrentGraphAdvancementCommandService,
    DeploymentPlanContextQueryService,
    DesiredGraphCommandService,
    ExecutionAdmissionCommandService,
    ExecutionCoordinator,
    ExecutionWorkerAuthority,
    ExecuteVerification,
    IdempotencyKey,
    OperationCommandService,
    RunLifecycleCommandService,
    VerificationAuthority,
    VerificationCommandService,
    VerificationScope,
)
from examples.scenarios.runner import ScenarioEffectInterpreter
from examples.service_infrastructure import service_infrastructure_recipe
from tests.postgres_case import PostgresStoreTestCase


class Ids:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._values = count(1)

    def __call__(self) -> str:
        return f"{self._prefix}-{next(self._values)}"


class TrackingUnitOfWork:
    def __init__(
        self,
        inner: PostgresUnitOfWork,
        tracker: "TransactionTracker",
    ) -> None:
        self._inner = inner
        self._tracker = tracker

    def __enter__(self):
        self._inner.__enter__()
        self._tracker.active += 1
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return self._inner.__exit__(exc_type, exc_value, traceback)
        finally:
            self._tracker.active -= 1

    @property
    def stores(self):
        return self._inner.stores

    def commit(self) -> None:
        self._inner.commit()


class TransactionTracker:
    def __init__(self) -> None:
        self.active = 0

    def __call__(self):
        return TrackingUnitOfWork(
            PostgresUnitOfWork(
                lambda: psycopg.connect(os.environ["CPK_TEST_DATABASE_URL"])
            ),
            self,
        )


@dataclass(frozen=True)
class PassingHttpVerificationInterpreter:
    tracker: TransactionTracker
    calls: list[VerificationCheckMaterial]

    @property
    def capabilities(self) -> frozenset[VerificationCapability]:
        return frozenset((VerificationCapability.HTTP,))

    def execute(self, material: VerificationCheckMaterial) -> VerificationCompleted:
        if self.tracker.active:
            raise AssertionError("verification executed inside a UnitOfWork")
        self.calls.append(material)
        return VerificationCompleted(
            VerificationIdentity(
                material.node_id,
                material.graph_id,
                material.check.check_id,
            ),
            VerificationCapability.HTTP,
            VerificationOutcome.PASSED,
            1,
        )


class ServiceInfrastructureProgramTests(PostgresStoreTestCase):
    def test_deploy_reconstructs_and_advances_with_redacted_read_truth(self) -> None:
        workspace_id = "service-program-deploy"
        current = DeploymentGraph("service-program-empty")
        desired = compile_recipe(service_infrastructure_recipe())
        current_graph_id = self._initialize(workspace_id, current)

        planning_program, _, _, planning_tracker = self._program("service-plan")
        deployment = planning_program.between(current, desired)
        prepared = deployment.plan(
            self._plan_request(
                deployment,
                workspace_id,
                current_graph_id,
                current_graph_id,
                "service-deploy",
            )
        )
        self.assertIsInstance(prepared, ApprovalSuspension)
        assert isinstance(prepared, ApprovalSuspension)
        plan_id = prepared.preparation.plan.plan_record.plan_id
        request_id = prepared.approval_request.request.request_id

        approval_program, _, _, approval_tracker = self._program("service-approve")
        approved = approval_program.for_plan(plan_id).approve(
            request_id,
            ApprovalGrant(
                actor_id="service-approver",
                actor_scopes=(prepared.approval_request.request.required_scope,),
                idempotency_key=IdempotencyKey("service-deploy:decision"),
            ),
        )
        self.assertEqual(approved.approval.request.plan_id, plan_id)

        execution_program, interpreter, reads, execution_tracker = self._program(
            "service-execute"
        )
        result = execution_program.for_plan(plan_id).run(
            request_id,
            self._execution_grant("service-deploy"),
        )

        self.assertIsInstance(result, AdvancedDeployment)
        assert isinstance(result, AdvancedDeployment)
        self.assertEqual(
            self.stores.workspace.get(workspace_id).current_graph_id,
            result.advancement.to_graph_id,
        )
        self.assertGreater(len(interpreter.requests), 0)
        self.assertEqual(
            {request.material_graph_id for request in interpreter.requests},
            {prepared.preparation.desired_graph.graph_version.graph_id},
        )
        verification_material = tuple(
            material
            for request in interpreter.requests
            if isinstance(request.action, StartNode)
            for material in materialize_verification_contract(request)
        )
        verification_calls: list[VerificationCheckMaterial] = []
        verification = VerificationCommandService(
            execution_tracker,
            VerificationInterpreterRegistry(
                {
                    VerificationCapability.HTTP:
                        PassingHttpVerificationInterpreter(
                            execution_tracker,
                            verification_calls,
                        )
                }
            ),
            clock=_datetime_clock,
            id_factory=Ids("service-verification"),
        )
        authority = VerificationAuthority(
            "service-verifier",
            frozenset((VerificationScope.EXECUTE,)),
        )
        for material in verification_material:
            verification.execute(
                ExecuteVerification(workspace_id, material, authority)
            )

        self.assertEqual(verification_calls, list(verification_material))
        observed = reads.observed_state(workspace_id).observations
        semantic = tuple(
            observation
            for observation in observed
            if observation["probe_kind"] == "semantic-verification"
        )
        self.assertEqual(len(semantic), len(verification_material))
        self.assertGreater(len(semantic), 0)
        self.assertTrue(
            all(
                observation["status"] == "verified"
                and observation["probe_outcome"] == "verified"
                for observation in semantic
            )
        )
        self.assertNotIn(
            "service-infrastructure-webhook-receiver:8090",
            str(semantic),
        )

        projected = str(reads.workspace(workspace_id).descriptor())
        for product in (
            PackageServerProduct.SERVICE_DISCOVERY,
            PackageServerProduct.OPENTELEMETRY_COLLECTOR,
            PackageServerProduct.WEBHOOK_DELIVERY,
        ):
            self.assertIn(product.value, projected)
        self.assertIn("postgres", projected)
        self.assertNotIn("secret://service-acceptance", projected)
        self.assertNotIn("service-infrastructure-webhook-receiver:8090", projected)
        self.assertEqual(
            (planning_tracker.active, approval_tracker.active, execution_tracker.active),
            (0, 0, 0),
        )

    def test_invalid_graph_records_no_plan_approval_run_effect_or_advancement(
        self,
    ) -> None:
        workspace_id = "service-program-invalid"
        current = DeploymentGraph("service-program-invalid-empty")
        current_graph_id = self._initialize(workspace_id, current)
        recipe = service_infrastructure_recipe()
        root = recipe.root
        self.assertIsInstance(root, DockerRuntime)
        invalid_root = replace(
            root,
            children=tuple(
                child
                for child in root.children
                if not (
                    isinstance(child, SocketConnection)
                    and child.consumer_role == "webhook-delivery"
                )
            ),
        )
        invalid = compile_recipe(DeploymentRecipe(recipe.name, invalid_root))
        program, interpreter, _, tracker = self._program("service-invalid")
        deployment = program.between(current, invalid)

        with self.assertRaises(ActivityPlanningGraphInvalid):
            deployment.plan(
                self._plan_request(
                    deployment,
                    workspace_id,
                    current_graph_id,
                    current_graph_id,
                    "service-invalid",
                )
            )

        sessions = self.stores.activity_history.sessions_for_workspace(workspace_id)
        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(
            self.stores.activity_history.plans_for_session(session.session_id),
            (),
        )
        self.assertEqual(
            self.stores.activity_history.approval_requests_for_session(
                session.session_id
            ),
            (),
        )
        self.assertEqual(self._execution_request_count(workspace_id), 0)
        self.assertEqual(interpreter.requests, [])
        self.assertEqual(
            self.stores.workspace.get(workspace_id).current_graph_id,
            current_graph_id,
        )
        self.assertEqual(tracker.active, 0)

    def test_no_change_records_plan_without_approval_run_or_effect(self) -> None:
        workspace_id = "service-program-no-change"
        graph = compile_recipe(service_infrastructure_recipe())
        current_graph_id = self._initialize(workspace_id, graph)
        program, interpreter, _, tracker = self._program("service-no-change")
        deployment = program.between(graph, graph)

        prepared = deployment.plan(
            self._plan_request(
                deployment,
                workspace_id,
                current_graph_id,
                current_graph_id,
                "service-no-change",
            )
        )

        self.assertIsInstance(prepared, NoDeploymentChanges)
        assert isinstance(prepared, NoDeploymentChanges)
        self.assertEqual(prepared.preparation.plan.plan_record.plan.activities, ())
        session_id = prepared.preparation.session.session.session_id
        self.assertEqual(
            self.stores.activity_history.approval_requests_for_session(session_id),
            (),
        )
        self.assertEqual(self._execution_request_count(workspace_id), 0)
        self.assertEqual(interpreter.requests, [])
        self.assertEqual(
            self.stores.workspace.get(workspace_id).current_graph_id,
            current_graph_id,
        )
        self.assertEqual(tracker.active, 0)

    def test_teardown_reconstructs_executes_and_advances_to_empty_graph(self) -> None:
        workspace_id = "service-program-teardown"
        current = compile_recipe(service_infrastructure_recipe())
        desired = DeploymentGraph("service-program-teardown-empty")
        current_graph_id = self._initialize(workspace_id, current)
        planning_program, _, _, planning_tracker = self._program("teardown-plan")
        deployment = planning_program.between(current, desired)
        prepared = deployment.plan(
            self._plan_request(
                deployment,
                workspace_id,
                current_graph_id,
                current_graph_id,
                "service-teardown",
            )
        )
        self.assertIsInstance(prepared, ApprovalSuspension)
        assert isinstance(prepared, ApprovalSuspension)
        plan_id = prepared.preparation.plan.plan_record.plan_id
        request_id = prepared.approval_request.request.request_id

        approval_program, _, _, approval_tracker = self._program("teardown-approve")
        approval_program.for_plan(plan_id).approve(
            request_id,
            ApprovalGrant(
                actor_id="service-approver",
                actor_scopes=(prepared.approval_request.request.required_scope,),
                idempotency_key=IdempotencyKey("service-teardown:decision"),
            ),
        )
        execution_program, interpreter, _, execution_tracker = self._program(
            "teardown-execute"
        )
        result = execution_program.for_plan(plan_id).run(
            request_id,
            self._execution_grant("service-teardown"),
        )

        self.assertIsInstance(result, AdvancedDeployment)
        assert isinstance(result, AdvancedDeployment)
        self.assertGreater(len(interpreter.requests), 0)
        self.assertEqual(
            self.stores.workspace.get(workspace_id).current_graph_id,
            result.advancement.to_graph_id,
        )
        stored = self.stores.graph_topology.get(result.advancement.to_graph_id)
        self.assertEqual(DEFAULT_GRAPH_CODEC.decode(stored.graph_descriptor), desired)
        self.assertEqual(
            (planning_tracker.active, approval_tracker.active, execution_tracker.active),
            (0, 0, 0),
        )

    def _initialize(self, workspace_id: str, graph: DeploymentGraph) -> str:
        graph_id = f"{workspace_id}:current"
        self.stores.workspace.create(WorkspaceRecord(workspace_id, workspace_id))
        self.stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id=graph_id,
                workspace_id=workspace_id,
                version=1,
                graph=graph,
                created_by="service-acceptance-fixture",
                created_at=_text_clock(),
            )
        )
        self.stores.workspace.set_current_graph(workspace_id, graph_id)
        self.stores.workspace.set_desired_graph(workspace_id, graph_id)
        return graph_id

    def _execution_request_count(self, workspace_id: str) -> int:
        row = self.connection.execute(
            "SELECT count(*) FROM cpk_execution_requests WHERE workspace_id = %s",
            (workspace_id,),
        ).fetchone()
        assert row is not None
        return int(row[0])

    def _program(self, prefix: str):
        tracker = TransactionTracker()
        approval = ApprovalCommandService(
            tracker,
            clock=_text_clock,
            id_factory=Ids(f"{prefix}:approval"),
        )
        planning = PlanningServices(
            OperationCommandService(
                tracker,
                clock=_text_clock,
                id_factory=Ids(f"{prefix}:operation"),
            ),
            DesiredGraphCommandService(
                tracker,
                clock=_text_clock,
                id_factory=Ids(f"{prefix}:graph"),
            ),
            ActivityPlanningCommandService(
                tracker,
                clock=_text_clock,
                id_factory=Ids(f"{prefix}:plan"),
            ),
            approval,
        )
        lifecycle = RunLifecycleCommandService(
            tracker,
            clock=_text_clock,
            id_factory=Ids(f"{prefix}:run"),
        )
        interpreter = ScenarioEffectInterpreter(
            transaction_active=lambda: tracker.active > 0
        )
        reads = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            execution_store=self.stores.execution,
            observed_state_store=self.stores.observed_state,
            clock=_datetime_clock,
        )
        return (
            DeploymentProgram(
                DeploymentProgramServices(
                    planning=planning,
                    approvals=approval,
                    admission=ExecutionAdmissionCommandService(
                        tracker,
                        clock=_text_clock,
                        id_factory=Ids(f"{prefix}:admission"),
                    ),
                    lifecycle=lifecycle,
                    coordinator=ExecutionCoordinator(
                        tracker,
                        lifecycle,
                        interpreter,
                        clock=_datetime_clock,
                        id_factory=Ids(f"{prefix}:coordinator"),
                    ),
                    advancement=CurrentGraphAdvancementCommandService(
                        tracker,
                        clock=_text_clock,
                        id_factory=Ids(f"{prefix}:advance"),
                    ),
                    contexts=DeploymentPlanContextQueryService(tracker),
                )
            ),
            interpreter,
            reads,
            tracker,
        )

    @staticmethod
    def _plan_request(
        deployment,
        workspace_id: str,
        current_graph_id: str,
        desired_graph_id: str,
        prefix: str,
    ) -> DeploymentPlanRequest:
        return DeploymentPlanRequest(
            transition=deployment.transition,
            workspace_id=workspace_id,
            current_graph_id=current_graph_id,
            expected_desired_graph_id=desired_graph_id,
            actor_id="service-operator",
            title=f"Service infrastructure {prefix}",
            approval_comment="Approve the heterogeneous service transition.",
            idempotency_prefix=prefix,
        )

    @staticmethod
    def _execution_grant(prefix: str) -> DeploymentExecutionGrant:
        return DeploymentExecutionGrant(
            admission=AdmissionGrant(
                actor_id="service-operator",
                actor_scopes=("plan:execute",),
                idempotency_key=IdempotencyKey(f"{prefix}:admission"),
            ),
            claim=ClaimGrant(
                authority=ExecutionWorkerAuthority(
                    "service-worker",
                    ("execution:operate",),
                ),
                lease_expires_at="2026-07-19T13:00:00Z",
                claim_idempotency_key=IdempotencyKey(f"{prefix}:claim"),
                start_idempotency_key=IdempotencyKey(f"{prefix}:start"),
            ),
            advancement=AdvancementGrant(
                IdempotencyKey(f"{prefix}:advance")
            ),
        )


def _text_clock() -> str:
    return "2026-07-19T12:00:00Z"


def _datetime_clock() -> datetime:
    return datetime(2026, 7, 19, 12, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
