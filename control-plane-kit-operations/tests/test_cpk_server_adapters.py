from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import unittest

import psycopg

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.planning import ActivityPlan
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.algebra import (
    BlockSockets,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
)
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    ProductRuntimeContract,
    instantiate_product,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, compile_topology
from control_plane_kit_core.types import Protocol
from control_plane_kit_operations.cpk_server import (
    CpkServerApplicationError,
    CpkServerApprovalService,
    CpkServerLifecycleService,
    CpkServerOperationsApplication,
    CpkServerPlanningService,
    CpkServerReadService,
    CpkServerUnsupportedService,
    cpk_server_services,
)
from control_plane_kit_operations.admission import ExecutionAdmissionCommandService
from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService
from control_plane_kit_operations.approvals import ApprovalCommandService, RequestApproval
from control_plane_kit_operations.coordinator import (
    ActivityExecutionOutcome,
    ExecutionCoordinator,
)
from control_plane_kit_operations.lifecycle import RunLifecycleCommandService
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService,
    DesiredGraphCommandService,
    RequestActivityPlan,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.products import ProductRegistrationService
from control_plane_kit_operations.records import (
    ActivityPlanRecord,
    ActivityPlanStatus,
    BoundedEvidence,
    GraphVersionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import OperationCommandService
from control_plane_kit_operations.workspaces import WorkspaceCommandService


@dataclass(frozen=True)
class RouteRequest:
    surface: str
    route_id: str
    service_role: ControlPlaneServiceRole
    path_parameters: dict[str, str]
    payload: dict[str, object]


class RecordingService:
    def __init__(self) -> None:
        self.commands: list[object] = []

    def execute(self, command: object):
        self.commands.append(command)
        return DescriptorResult({"command_type": type(command).__name__})


class DescriptorResult:
    def __init__(self, descriptor: dict[str, object]) -> None:
        self._descriptor = descriptor

    def descriptor(self) -> dict[str, object]:
        return dict(self._descriptor)


class GeneratedIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.next = 0

    def __call__(self) -> str:
        self.next += 1
        return f"{self.prefix}-{self.next}"


class SucceedingActivityAdapter:
    def __init__(self) -> None:
        self.activities: list[str] = []

    def execute(self, context) -> ActivityExecutionOutcome:
        activity_id = context.activity.activity_id.value
        self.activities.append(activity_id)
        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping({"activity_id": activity_id})
        )


class CpkServerOperationsAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def seed_workspace(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-a", "Workspace A")
            )
            unit_of_work.stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-current",
                    workspace_id="workspace-a",
                    version=1,
                    graph=DeploymentGraph("current"),
                    created_by="operator-a",
                    created_at="2026-07-22T10:00:00Z",
                )
            )
            unit_of_work.stores.workspaces.set_current_graph(
                "workspace-a",
                "graph-current",
            )
            unit_of_work.commit()

    def seed_reviewable_plan(self) -> None:
        self.seed_workspace()
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-desired",
                    workspace_id="workspace-a",
                    version=2,
                    graph=DeploymentGraph("desired"),
                    created_by="operator-a",
                    created_at="2026-07-22T10:01:00Z",
                )
            )
            unit_of_work.stores.workspaces.set_desired_graph(
                "workspace-a",
                "graph-desired",
            )
            unit_of_work.stores.activity_history.add_session(
                OperationSessionRecord(
                    session_id="session-a",
                    workspace_id="workspace-a",
                    actor_id="operator-a",
                    title="Initial deployment",
                    status=OperationSessionStatus.OPEN,
                    created_at="2026-07-22T10:02:00Z",
                )
            )
            unit_of_work.stores.activity_history.add_plan(
                ActivityPlanRecord(
                    plan_id="plan-a",
                    session_id="session-a",
                    base_graph_id="graph-current",
                    desired_graph_id="graph-desired",
                    status=ActivityPlanStatus.PLANNED,
                    created_at="2026-07-22T10:03:00Z",
                    plan=ActivityPlan(()),
                )
            )
            unit_of_work.commit()

    def test_http_read_route_uses_operations_read_projection_not_demo_echo(self) -> None:
        self.seed_workspace()
        service = CpkServerReadService(
            self.unit_of_work,
            clock=lambda: datetime(2026, 7, 22, 13, 0, tzinfo=timezone.utc),
        )

        result = service.handle(
            RouteRequest(
                surface="http",
                route_id="read.current-graph",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={"workspace_id": "workspace-a"},
                payload={},
            )
        )

        self.assertEqual(result["graph_id"], "graph-current")
        self.assertEqual(result["graph_name"], "current")
        self.assertNotIn("service", result)
        self.assertNotIn("payload", result)

    def test_mcp_read_arguments_use_same_read_service_boundary(self) -> None:
        self.seed_workspace()
        service = CpkServerReadService(self.unit_of_work)

        result = service.handle(
            RouteRequest(
                surface="mcp",
                route_id="read.workspace",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={},
                payload={"workspace_id": "workspace-a"},
            )
        )

        self.assertEqual(result["workspace"]["workspace_id"], "workspace-a")
        self.assertEqual(result["current_graph"]["graph_id"], "graph-current")

    def test_read_errors_are_bounded_without_sql_or_secret_leakage(self) -> None:
        service = CpkServerReadService(self.unit_of_work)

        with self.assertRaises(CpkServerApplicationError) as raised:
            service.handle(
                RouteRequest(
                    surface="http",
                    route_id="read.workspace",
                    service_role=ControlPlaneServiceRole.READS,
                    path_parameters={"workspace_id": "missing"},
                    payload={"api_token": "do-not-disclose"},
                )
            )

        self.assertEqual(raised.exception.status, 404)
        descriptor = raised.exception.descriptor()
        self.assertIn("missing workspace", descriptor["error"]["message"])
        self.assertNotIn("do-not-disclose", str(descriptor))
        self.assertNotIn("SELECT", str(descriptor).upper())

    def test_setup_routes_create_workspace_import_product_and_set_desired_graph(self) -> None:
        product_document = ProductDescriptorCodec().encode_document(
            self.product("hello-server")
        )
        graph = self.graph_from_document(product_document.product)
        planning = CpkServerPlanningService(
            RecordingService(),
            workspaces=WorkspaceCommandService(
                self.unit_of_work,
                clock=lambda: "2026-07-22T10:00:00Z",
                id_factory=self.ids("graph-empty"),
            ),
            products=ProductRegistrationService(self.unit_of_work),
            desired_graphs=DesiredGraphCommandService(
                self.unit_of_work,
                clock=lambda: "2026-07-22T10:05:00Z",
                id_factory=self.ids("graph-desired", "action-desired"),
            ),
        )
        lifecycle = CpkServerLifecycleService(
            RecordingService(),
            operations=OperationCommandService(
                self.unit_of_work,
                clock=lambda: "2026-07-22T10:01:00Z",
                id_factory=self.ids("session-a", "action-start"),
            ),
        )

        workspace = planning.handle(
            RouteRequest(
                surface="http",
                route_id="command.workspace.create",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "name": "Workspace A",
                    "actor_id": "operator-a",
                    "idempotency_key": "workspace-a",
                },
            )
        )
        self.assertEqual(workspace["workspace"]["current_graph_id"], "graph-empty")

        registered = planning.handle(
            RouteRequest(
                surface="http",
                route_id="command.product.import",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "descriptor_document": json.loads(
                        product_document.content.decode("utf-8")
                    ),
                    "actor_id": "operator-a",
                    "imported_at": "2026-07-22T10:02:00Z",
                    "idempotency_key": "import-product-a",
                },
            )
        )
        self.assertEqual(
            registered["reference"]["identity"]["name"],
            "hello-server",
        )
        self.assertEqual(registered["status"], "active")

        session = lifecycle.handle(
            RouteRequest(
                surface="http",
                route_id="command.operation-session.start",
                service_role=ControlPlaneServiceRole.LIFECYCLE,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "actor_id": "operator-a",
                    "title": "Initial deployment",
                    "idempotency_key": "session-a",
                },
            )
        )
        self.assertEqual(session["session_id"], "session-a")

        desired = planning.handle(
            RouteRequest(
                surface="http",
                route_id="command.desired-graph.set",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "session_id": "session-a",
                    "actor_id": "operator-a",
                    "graph": DEFAULT_GRAPH_CODEC.encode(graph),
                    "expected_desired_graph_id": None,
                    "idempotency_key": "desired-a",
                },
            )
        )
        self.assertEqual(desired["desired_graph_id"], "graph-desired")

        with self.unit_of_work() as unit_of_work:
            workspace_record = unit_of_work.stores.workspaces.get("workspace-a")
            self.assertEqual(workspace_record.current_graph_id, "graph-empty")
            self.assertEqual(workspace_record.desired_graph_id, "graph-desired")
            self.assertEqual(
                unit_of_work.stores.activity_history.get_session("session-a").workspace_id,
                "workspace-a",
            )

    def test_command_route_translates_payload_to_existing_planning_command(self) -> None:
        recording = RecordingService()
        service = CpkServerPlanningService(recording)

        result = service.handle(
            RouteRequest(
                surface="http",
                route_id="command.deployment.plan",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "session_id": "session-a",
                    "actor_id": "operator-a",
                    "expected_current_graph_id": "graph-current",
                    "expected_desired_graph_id": "graph-desired",
                    "idempotency_key": "plan-a",
                },
            )
        )

        self.assertEqual(result, {"command_type": "RequestActivityPlan"})
        command = recording.commands[0]
        self.assertIsInstance(command, RequestActivityPlan)
        self.assertEqual(command.workspace_id, "workspace-a")
        self.assertEqual(command.expected_current_graph_id, "graph-current")
        self.assertEqual(command.idempotency_key.value, "plan-a")

    def test_approval_request_route_translates_payload_to_existing_command(self) -> None:
        recording = RecordingService()
        service = CpkServerApprovalService(recording)

        result = service.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.approval.request",
                service_role=ControlPlaneServiceRole.APPROVAL,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "session_id": "session-a",
                    "plan_id": "plan-a",
                    "actor_id": "operator-a",
                    "actor_scopes": [PolicyScope.PLAN_REQUEST.value],
                    "idempotency_key": "request-approval-a",
                    "comment": "Please review the deployment.",
                },
            )
        )

        self.assertEqual(result, {"command_type": "RequestApproval"})
        command = recording.commands[0]
        self.assertIsInstance(command, RequestApproval)
        self.assertEqual(command.session_id, "session-a")
        self.assertEqual(command.plan_id, "plan-a")
        self.assertEqual(command.idempotency_key.value, "request-approval-a")
        self.assertEqual(
            command.actor_scopes,
            (PolicyScope.PLAN_REQUEST,),
        )

    def test_public_approval_loop_persists_and_reads_queue_detail_and_decision(self) -> None:
        self.seed_reviewable_plan()
        approval = CpkServerApprovalService(
            ApprovalCommandService(
                self.unit_of_work,
                clock=lambda: "2026-07-22T10:04:00Z",
                id_factory=self.ids(
                    "approval-a",
                    "action-approval",
                    "decision-a",
                    "action-decision",
                ),
            )
        )
        reads = CpkServerReadService(self.unit_of_work)

        requested = approval.handle(
            RouteRequest(
                surface="http",
                route_id="command.approval.request",
                service_role=ControlPlaneServiceRole.APPROVAL,
                path_parameters={
                    "workspace_id": "workspace-a",
                    "plan_id": "plan-a",
                },
                payload={
                    "session_id": "session-a",
                    "actor_id": "operator-a",
                    "actor_scopes": [PolicyScope.PLAN_REQUEST.value],
                    "idempotency_key": "request-approval-a",
                    "comment": "Please review the deployment.",
                },
            )
        )
        self.assertEqual(requested["request_id"], "approval-a")
        self.assertEqual(requested["state"], "pending")

        pending = reads.handle(
            RouteRequest(
                surface="http",
                route_id="read.pending-approvals",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={"workspace_id": "workspace-a"},
                payload={"limit": 10, "offset": 0},
            )
        )
        self.assertEqual(pending["items"][0]["request_id"], "approval-a")

        detail = reads.handle(
            RouteRequest(
                surface="mcp",
                route_id="read.approval-detail",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "approval_id": "approval-a",
                },
            )
        )
        self.assertEqual(detail["approval"]["request_id"], "approval-a")
        self.assertEqual(detail["plan"]["plan_id"], "plan-a")
        self.assertEqual(detail["plan"]["risk_summary"]["ready_for_execution"], True)

        decided = approval.handle(
            RouteRequest(
                surface="http",
                route_id="command.approval.decide",
                service_role=ControlPlaneServiceRole.APPROVAL,
                path_parameters={"approval_id": "approval-a"},
                payload={
                    "session_id": "session-a",
                    "actor_id": "manager-a",
                    "actor_scopes": [requested["required_scope"]],
                    "decision": "approved",
                    "idempotency_key": "decide-approval-a",
                    "comment": "Approved.",
                },
            )
        )
        self.assertEqual(decided["state"], "approved")
        self.assertEqual(decided["request_id"], "approval-a")

    def test_public_workflow_routes_plan_approve_admit_claim_execute_and_advance(self) -> None:
        adapter = SucceedingActivityAdapter()
        lifecycle = RunLifecycleCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T10:10:00Z",
            id_factory=GeneratedIds("lifecycle"),
        )
        application = CpkServerOperationsApplication(
            cpk_server_services(
                unit_of_work_factory=self.unit_of_work,
                planning=ActivityPlanningCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:04:00Z",
                    id_factory=GeneratedIds("plan"),
                ),
                workspaces=WorkspaceCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:00:00Z",
                    id_factory=GeneratedIds("workspace"),
                ),
                products=ProductRegistrationService(self.unit_of_work),
                desired_graphs=DesiredGraphCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:02:00Z",
                    id_factory=GeneratedIds("desired"),
                ),
                approval=ApprovalCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:05:00Z",
                    id_factory=GeneratedIds("approval"),
                ),
                admission=ExecutionAdmissionCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:06:00Z",
                    id_factory=GeneratedIds("admission"),
                ),
                lifecycle=lifecycle,
                operations=OperationCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:01:00Z",
                    id_factory=GeneratedIds("session"),
                ),
                execution=ExecutionCoordinator(
                    self.unit_of_work,
                    lifecycle=lifecycle,
                    adapter=adapter,
                    clock=lambda: "2026-07-22T10:11:00Z",
                    id_factory=GeneratedIds("execution"),
                ),
                advancement=CurrentGraphAdvancementCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:12:00Z",
                    id_factory=GeneratedIds("advance"),
                ),
                clock=lambda: datetime(2026, 7, 22, 10, 13, tzinfo=timezone.utc),
            )
        )
        product_document = ProductDescriptorCodec().encode_document(
            self.product("hello-server")
        )

        workspace = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.workspace.create",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "name": "Workspace A",
                    "actor_id": "operator-a",
                    "idempotency_key": "workspace-a",
                },
            )
        )
        current_graph_id = str(workspace["workspace"]["current_graph_id"])

        application.handle(
            RouteRequest(
                surface="http",
                route_id="command.product.import",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "descriptor_document": json.loads(
                        product_document.content.decode("utf-8")
                    ),
                    "actor_id": "operator-a",
                    "imported_at": "2026-07-22T10:00:30Z",
                    "idempotency_key": "import-product-a",
                },
            )
        )

        session = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.operation-session.start",
                service_role=ControlPlaneServiceRole.LIFECYCLE,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "actor_id": "operator-a",
                    "title": "Initial deployment",
                    "idempotency_key": "session-a",
                },
            )
        )
        session_id = str(session["session_id"])

        desired = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.desired-graph.set",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "session_id": session_id,
                    "actor_id": "operator-a",
                    "graph": DEFAULT_GRAPH_CODEC.encode(
                        self.graph_from_document(product_document.product)
                    ),
                    "expected_desired_graph_id": None,
                    "idempotency_key": "desired-a",
                },
            )
        )
        desired_graph_id = str(desired["desired_graph_id"])

        planned = application.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.deployment.plan",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "session_id": session_id,
                    "actor_id": "operator-a",
                    "expected_current_graph_id": current_graph_id,
                    "expected_desired_graph_id": desired_graph_id,
                    "idempotency_key": "plan-a",
                },
            )
        )
        plan_id = str(planned["plan_id"])

        requested = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.approval.request",
                service_role=ControlPlaneServiceRole.APPROVAL,
                path_parameters={"workspace_id": "workspace-a", "plan_id": plan_id},
                payload={
                    "session_id": session_id,
                    "actor_id": "operator-a",
                    "actor_scopes": [PolicyScope.PLAN_REQUEST.value],
                    "idempotency_key": "approval-request-a",
                },
            )
        )
        approval_request_id = str(requested["request_id"])

        pending = application.handle(
            RouteRequest(
                surface="http",
                route_id="read.pending-approvals",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={"workspace_id": "workspace-a"},
                payload={"limit": 10, "offset": 0},
            )
        )
        self.assertEqual(pending["items"][0]["request_id"], approval_request_id)

        detail = application.handle(
            RouteRequest(
                surface="mcp",
                route_id="read.approval-detail",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={},
                payload={"workspace_id": "workspace-a", "approval_id": approval_request_id},
            )
        )
        self.assertEqual(detail["plan"]["plan_id"], plan_id)

        application.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.approval.decide",
                service_role=ControlPlaneServiceRole.APPROVAL,
                path_parameters={},
                payload={
                    "session_id": session_id,
                    "request_id": approval_request_id,
                    "actor_id": "manager-a",
                    "actor_scopes": [requested["required_scope"]],
                    "decision": "approved",
                    "idempotency_key": "approval-decision-a",
                },
            )
        )

        admitted = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.deployment.admit",
                service_role=ControlPlaneServiceRole.ADMISSION,
                path_parameters={"workspace_id": "workspace-a", "plan_id": plan_id},
                payload={
                    "session_id": session_id,
                    "approval_request_id": approval_request_id,
                    "actor_id": "operator-a",
                    "actor_scopes": [PolicyScope.PLAN_EXECUTE.value],
                    "idempotency_key": "admit-a",
                    "readiness": [],
                },
            )
        )
        request_id = str(admitted["execution_request_id"])

        claimed = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.run.claim",
                service_role=ControlPlaneServiceRole.LIFECYCLE,
                path_parameters={"workspace_id": "workspace-a", "run_id": request_id},
                payload={
                    "worker_id": "worker-a",
                    "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
                    "lease_expires_at": "2026-07-22T10:30:00Z",
                    "idempotency_key": "claim-a",
                },
            )
        )
        run_id = str(claimed["run_id"])

        application.handle(
            RouteRequest(
                surface="http",
                route_id="command.run.start",
                service_role=ControlPlaneServiceRole.EXECUTION,
                path_parameters={"workspace_id": "workspace-a", "run_id": run_id},
                payload={
                    "worker_id": "worker-a",
                    "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
                    "idempotency_key": "start-a",
                },
            )
        )

        executed = application.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.deployment.execute",
                service_role=ControlPlaneServiceRole.EXECUTION,
                path_parameters={},
                payload={
                    "run_id": run_id,
                    "worker_id": "worker-a",
                    "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
                    "idempotency_key": "execute-a",
                    "max_effects": 10,
                },
            )
        )
        self.assertEqual(executed["coordinator_status"], "completed")
        self.assertEqual(executed["run_status"], "succeeded")
        self.assertEqual(
            [activity.split(":", 1)[0] for activity in adapter.activities],
            ["start-runtime", "start-node", "wait-healthy"],
        )

        advanced = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.graph.advance-current",
                service_role=ControlPlaneServiceRole.LIFECYCLE,
                path_parameters={"workspace_id": "workspace-a", "run_id": run_id},
                payload={
                    "plan_id": plan_id,
                    "expected_current_graph_id": current_graph_id,
                    "desired_graph_id": desired_graph_id,
                    "worker_id": "worker-a",
                    "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
                    "idempotency_key": "advance-a",
                },
            )
        )
        self.assertEqual(advanced["from_graph_id"], current_graph_id)
        self.assertEqual(advanced["to_graph_id"], desired_graph_id)

        current = application.handle(
            RouteRequest(
                surface="http",
                route_id="read.current-graph",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={"workspace_id": "workspace-a"},
                payload={},
            )
        )
        self.assertEqual(current["graph_id"], desired_graph_id)

    def test_unsupported_services_fail_closed_until_extracted(self) -> None:
        service = CpkServerUnsupportedService(ControlPlaneServiceRole.RECOVERY)

        with self.assertRaises(CpkServerApplicationError) as raised:
            service.handle(
                RouteRequest(
                    surface="mcp",
                    route_id="command.recovery.decide",
                    service_role=ControlPlaneServiceRole.RECOVERY,
                    path_parameters={"workspace_id": "workspace-a", "run_id": "run-a"},
                    payload={"actor_scopes": [PolicyScope.EXECUTION_OPERATE.value]},
                )
            )

        self.assertEqual(raised.exception.status, 501)
        self.assertIn("not implemented", raised.exception.message)

    def test_product_import_requires_public_command_idempotency_key(self) -> None:
        product_document = ProductDescriptorCodec().encode_document(
            self.product("hello-server")
        )
        service = CpkServerPlanningService(
            RecordingService(),
            products=ProductRegistrationService(self.unit_of_work),
        )

        with self.assertRaises(CpkServerApplicationError) as raised:
            service.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.product.import",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={
                        "descriptor_document": json.loads(
                            product_document.content.decode("utf-8")
                        ),
                        "actor_id": "operator-a",
                        "imported_at": "2026-07-22T10:02:00Z",
                    },
                )
            )

        self.assertEqual(raised.exception.status, 400)
        self.assertIn("idempotency_key", raised.exception.message)

    def ids(self, *values: str):
        remaining = list(values)

        def next_id() -> str:
            if not remaining:
                raise AssertionError("id factory exhausted")
            return remaining.pop(0)

        return next_id

    def product(
        self,
        name: str,
        *,
        digest: str = "sha256:" + "b" * 64,
    ) -> ContainerServerProduct:
        return ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", name, 1),
            image=OciImageReference(
                "ghcr.io",
                f"openj92/control-plane-kit-servers/{name}",
                digest,
                tag="v1",
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
            display_name=name,
            description="Server product used for cpk-server adapter tests.",
        )

    def graph_from_document(self, product: ContainerServerProduct) -> DeploymentGraph:
        block = instantiate_product(product, "app", ProductInstanceConfiguration())
        return compile_topology(
            DeploymentTopology("desired", DockerRuntime(children=(block,)))
        )

    def test_command_scopes_reject_non_text_entries(self) -> None:
        recording = RecordingService()
        service = CpkServerApprovalService(recording)

        with self.assertRaises(CpkServerApplicationError) as raised:
            service.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.approval.decide",
                    service_role=ControlPlaneServiceRole.APPROVAL,
                    path_parameters={"approval_id": "approval-a"},
                    payload={
                        "session_id": "session-a",
                        "actor_id": "operator-a",
                        "actor_scopes": [PolicyScope.INSTANCE_WORKSPACE_READ.value, 17],
                        "decision": "approved",
                        "idempotency_key": "approval-a",
                    },
                )
            )

        self.assertEqual(raised.exception.status, 400)
        self.assertIn("actor_scopes entries must be text", raised.exception.message)
        self.assertEqual(recording.commands, [])

    def test_application_boundary_requires_one_service_for_every_role(self) -> None:
        services = {
            role: CpkServerUnsupportedService(role) for role in ControlPlaneServiceRole
        }
        application = CpkServerOperationsApplication(services)

        with self.assertRaises(CpkServerApplicationError) as raised:
            application.handle(
                RouteRequest(
                    surface="http",
                    route_id="read.workspace",
                    service_role=ControlPlaneServiceRole.READS,
                    path_parameters={},
                    payload={},
                )
            )

        self.assertEqual(raised.exception.status, 501)


if __name__ == "__main__":
    unittest.main()
