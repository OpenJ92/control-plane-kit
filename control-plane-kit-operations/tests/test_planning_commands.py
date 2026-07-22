from __future__ import annotations

import os
import unittest

import psycopg

from control_plane_kit_core.algebra import (
    BlockSockets,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
)
from control_plane_kit_core.planning import ReconcileRuntime, StartNode, WaitForHealthy
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    ProductRuntimeContract,
    instantiate_product,
)
from control_plane_kit_core.topology import DeploymentGraph, compile_topology
from control_plane_kit_core.types import Protocol
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService,
    ActivityPlanningGraphInvalid,
    ActivityPlanningGraphStateConflict,
    ActivityPlanningIdempotencyConflict,
    DesiredGraphCommandService,
    DesiredGraphIdempotencyConflict,
    DesiredGraphSessionConflict,
    RequestActivityPlan,
    SetDesiredGraph,
    StaleDesiredGraph,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.products import InlineDescriptorSource
from control_plane_kit_operations.records import (
    GraphVersionRecord,
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


class PlanningCommandTests(unittest.TestCase):
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
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-a", "Workspace A")
            )
            unit_of_work.stores.registered_products.register(
                workspace_id="workspace-a",
                descriptor_document=self.document,
                source=InlineDescriptorSource(),
                imported_by="operator-a",
                imported_at="2026-07-22T10:00:00Z",
            )
            current = GraphVersionRecord.from_graph(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph=self.empty_graph("current"),
                created_by="operator-a",
                created_at="2026-07-22T10:00:00Z",
            )
            unit_of_work.stores.graphs.save(current)
            unit_of_work.stores.workspaces.set_current_graph(
                "workspace-a",
                current.graph_id,
            )
            unit_of_work.commit()
        self.operation_service("session-a", "action-start").execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "Plan hello",
                IdempotencyKey("start"),
            )
        )

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def operation_service(self, *ids: str) -> OperationCommandService:
        return OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T10:01:00Z",
            id_factory=Sequence(*ids),
        )

    def desired_service(self, *ids: str) -> DesiredGraphCommandService:
        return DesiredGraphCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T10:02:00Z",
            id_factory=Sequence(*ids),
        )

    def planning_service(self, *ids: str) -> ActivityPlanningCommandService:
        return ActivityPlanningCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T10:03:00Z",
            id_factory=Sequence(*ids),
        )

    def set_desired(
        self,
        service: DesiredGraphCommandService | None = None,
        *,
        key: str = "desired",
        actor_id: str = "operator-a",
    ):
        return (service or self.desired_service("graph-desired", "action-desired")).execute(
            SetDesiredGraph(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id=actor_id,
                graph=self.product_graph(),
                expected_desired_graph_id=None,
                idempotency_key=IdempotencyKey(key),
            )
        )

    def request_plan(
        self,
        service: ActivityPlanningCommandService | None = None,
        *,
        key: str = "plan",
        actor_id: str = "operator-a",
        desired_graph_id: str = "graph-desired",
    ):
        return (service or self.planning_service("plan-a", "action-plan")).execute(
            RequestActivityPlan(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id=actor_id,
                expected_current_graph_id="graph-current",
                expected_desired_graph_id=desired_graph_id,
                idempotency_key=IdempotencyKey(key),
            )
        )

    def test_desired_graph_command_records_graph_and_action_atomically(self) -> None:
        result = self.set_desired()

        self.assertFalse(result.replayed)
        self.assertEqual(result.graph_version_id, "graph-desired")
        self.assertEqual(result.action.ordinal, 2)
        self.assertEqual(
            result.action.payload["desired_graph_id"],
            "graph-desired",
        )

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.workspaces.get("workspace-a").desired_graph_id,
                "graph-desired",
            )
            self.assertEqual(
                tuple(
                    action.action_type.value
                    for action in unit_of_work.stores.activity_history.actions_for_session(
                        "session-a"
                    )
                ),
                ("start-operation-session", "set-desired-graph"),
            )

    def test_desired_graph_replay_and_changed_intent_conflict(self) -> None:
        first = self.set_desired()
        replay = self.set_desired(self.desired_service("unused-graph", "unused-action"))

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.graph_version_id, first.graph_version_id)
        self.assertEqual(replay.action, first.action)

        with self.assertRaises(DesiredGraphIdempotencyConflict):
            self.set_desired(
                self.desired_service("unused-graph", "unused-action"),
                actor_id="operator-b",
            )

    def test_desired_graph_late_action_failure_rolls_back_graph_truth(self) -> None:
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.set_desired(self.desired_service("graph-rolled-back", "action-start"))

        with self.unit_of_work() as unit_of_work:
            with self.assertRaises(KeyError):
                unit_of_work.stores.graphs.get("graph-rolled-back")
            self.assertIsNone(
                unit_of_work.stores.workspaces.get("workspace-a").desired_graph_id
            )

    def test_stale_or_closed_desired_graph_command_writes_nothing(self) -> None:
        self.set_desired()

        with self.assertRaises(StaleDesiredGraph):
            self.desired_service("graph-new", "action-new").execute(
                SetDesiredGraph(
                    "session-a",
                    "workspace-a",
                    "operator-a",
                    self.product_graph(),
                    expected_desired_graph_id=None,
                    idempotency_key=IdempotencyKey("stale"),
                )
            )

        self.operation_service("action-close").execute(
            CloseOperationSession("session-a", "operator-a", IdempotencyKey("close"))
        )
        with self.assertRaises(DesiredGraphSessionConflict):
            self.desired_service("graph-closed", "action-closed").execute(
                SetDesiredGraph(
                    "session-a",
                    "workspace-a",
                    "operator-a",
                    self.product_graph(),
                    expected_desired_graph_id="graph-desired",
                    idempotency_key=IdempotencyKey("closed"),
                )
            )

    def test_planning_pins_current_and_desired_graph_truth(self) -> None:
        self.set_desired()
        result = self.request_plan()

        self.assertFalse(result.replayed)
        self.assertEqual(result.plan_record.base_graph_id, "graph-current")
        self.assertEqual(result.plan_record.desired_graph_id, "graph-desired")
        self.assertEqual(
            tuple(
                type(activity.operation)
                for activity in result.plan_record.plan.activities
            ),
            (ReconcileRuntime, StartNode, WaitForHealthy),
        )
        self.assertEqual(
            tuple(
                dependency.predecessor.value
                for dependency in result.plan_record.plan.activities[2].dependencies
            ),
            (result.plan_record.plan.activities[1].activity_id.value,),
        )
        self.assertEqual(result.action.ordinal, 3)
        self.assertEqual(result.action.payload["plan_id"], "plan-a")

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.activity_history.get_plan("plan-a").plan,
                result.plan_record.plan,
            )

    def test_planning_replay_conflict_and_stale_pointer_guards(self) -> None:
        self.set_desired()
        first = self.request_plan()
        replay = self.request_plan(self.planning_service("unused-plan", "unused-action"))

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.plan_record, first.plan_record)

        with self.assertRaises(ActivityPlanningIdempotencyConflict):
            self.request_plan(
                self.planning_service("unused-plan", "unused-action"),
                actor_id="operator-b",
            )
        with self.assertRaises(ActivityPlanningGraphStateConflict):
            self.request_plan(
                self.planning_service("stale-plan", "stale-action"),
                key="stale-plan",
                desired_graph_id="missing-graph",
            )

    def test_malformed_durable_graph_rejects_without_plan_or_action(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.graphs.save(
                GraphVersionRecord(
                    graph_id="graph-invalid",
                    workspace_id="workspace-a",
                    version=2,
                    graph_descriptor={"name": "invalid", "nodes": "not-a-mapping"},
                    created_by="operator-a",
                    created_at="2026-07-22T10:02:00Z",
                )
            )
            unit_of_work.stores.workspaces.set_desired_graph(
                "workspace-a",
                "graph-invalid",
            )
            unit_of_work.commit()

        with self.assertRaises(ActivityPlanningGraphInvalid):
            self.request_plan(
                self.planning_service("plan-invalid", "action-invalid"),
                key="invalid",
                desired_graph_id="graph-invalid",
            )

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.activity_history.plans_for_session("session-a"),
                (),
            )
            self.assertEqual(
                len(
                    unit_of_work.stores.activity_history.actions_for_session(
                        "session-a"
                    )
                ),
                1,
            )

    def test_late_action_failure_rolls_back_plan_insert(self) -> None:
        self.set_desired()

        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.request_plan(self.planning_service("plan-rolled-back", "action-start"))

        with self.unit_of_work() as unit_of_work:
            with self.assertRaises(KeyError):
                unit_of_work.stores.activity_history.get_plan("plan-rolled-back")
            self.assertEqual(
                len(
                    unit_of_work.stores.activity_history.actions_for_session(
                        "session-a"
                    )
                ),
                2,
            )

    def product(self, name: str) -> ContainerServerProduct:
        return ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", name, 1),
            image=OciImageReference(
                "ghcr.io",
                f"openj92/control-plane-kit-servers/{name}",
                "sha256:" + "b" * 64,
                tag="v1",
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
            display_name=name,
            description="Server product used for planning command tests.",
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


if __name__ == "__main__":
    unittest.main()
