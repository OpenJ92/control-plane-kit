from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import unittest

import psycopg

from control_plane_kit_core.operations import ControlPlaneServiceRole
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
)
from control_plane_kit_operations.planning import (
    DesiredGraphCommandService,
    RequestActivityPlan,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.products import ProductRegistrationService
from control_plane_kit_operations.records import GraphVersionRecord, WorkspaceRecord
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
