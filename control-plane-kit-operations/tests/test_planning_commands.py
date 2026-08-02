from __future__ import annotations

import concurrent.futures
import os
import threading
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
    ActivityPlanningGraphStateConflict,
    ActivityPlanningIdempotencyConflict,
    ActivityPlanningSessionConflict,
    DesiredGraphCommandService,
    DesiredGraphIdempotencyConflict,
    DesiredGraphSessionConflict,
    RequestActivityPlan,
    SetDesiredGraph,
    StaleDesiredGraph,
)
from control_plane_kit_operations.postgres import (
    PostgresUnitOfWork,
    RealizedGraphProjectionConflict,
    install_schema,
)
from control_plane_kit_operations.products import InlineDescriptorSource
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import (
    CancelOperationSession,
    CloseOperationSession,
    IdempotencyKey,
    OperationCommandService,
    OperationSessionStateConflict,
    StartOperationSession,
)


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class SessionLockObservedConnection:
    def __init__(
        self,
        connection,
        *,
        before_session_lock: threading.Event | None = None,
        after_session_lock: threading.Event | None = None,
    ) -> None:
        self._connection = connection
        self._before_session_lock = before_session_lock
        self._after_session_lock = after_session_lock

    def execute(self, query, params=()):
        normalized = " ".join(str(query).upper().split())
        session_lock = (
            "FROM CPK_OPERATION_SESSIONS" in normalized
            and "FOR UPDATE" in normalized
        )
        if session_lock and self._before_session_lock is not None:
            self._before_session_lock.set()
        result = self._connection.execute(query, params)
        if session_lock and self._after_session_lock is not None:
            self._after_session_lock.set()
        return result

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


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

    def operation_service(
        self,
        *ids: str,
        clock=None,
        unit_of_work_factory=None,
    ) -> OperationCommandService:
        return OperationCommandService(
            unit_of_work_factory or self.unit_of_work,
            clock=clock or (lambda: "2026-07-22T10:01:00Z"),
            id_factory=Sequence(*ids),
        )

    def desired_service(self, *ids: str) -> DesiredGraphCommandService:
        return DesiredGraphCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T10:02:00Z",
            id_factory=Sequence(*ids),
        )

    def planning_service(
        self,
        *ids: str,
        clock=None,
        unit_of_work_factory=None,
    ) -> ActivityPlanningCommandService:
        return ActivityPlanningCommandService(
            unit_of_work_factory or self.unit_of_work,
            clock=clock or (lambda: "2026-07-22T10:03:00Z"),
            id_factory=Sequence(*ids),
        )

    def observed_unit_of_work_factory(
        self,
        *,
        before_session_lock: threading.Event | None = None,
        after_session_lock: threading.Event | None = None,
    ):
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]

        def factory() -> PostgresUnitOfWork:
            return PostgresUnitOfWork(
                lambda: SessionLockObservedConnection(
                    psycopg.connect(database_url),
                    before_session_lock=before_session_lock,
                    after_session_lock=after_session_lock,
                )
            )

        return factory

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

    def test_concurrent_identical_requests_converge_on_one_graph(self) -> None:
        barrier = threading.Barrier(2)

        def submit(ids: tuple[str, str]):
            barrier.wait(timeout=5)
            return self.set_desired(self.desired_service(*ids))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    submit,
                    (("graph-a", "action-a"), ("graph-b", "action-b")),
                )
            )

        self.assertEqual(len({result.graph_version_id for result in results}), 1)
        self.assertEqual(len({result.action.action_id for result in results}), 1)
        self.assertEqual(sum(result.replayed for result in results), 1)

    def test_concurrent_identical_request_persists_one_complete_result(self) -> None:
        barrier = threading.Barrier(2)

        def submit(ids: tuple[str, str]):
            barrier.wait(timeout=5)
            return self.set_desired(self.desired_service(*ids))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    submit,
                    (("graph-a", "action-a"), ("graph-b", "action-b")),
                )
            )

        workspace = self.connection.execute(
            """
            SELECT desired_graph_id, desired_realized_projection_id,
                   desired_graph_revision
            FROM cpk_workspaces
            WHERE workspace_id = 'workspace-a'
            """
        ).fetchone()
        graph_count = self.connection.execute(
            "SELECT count(*) FROM cpk_graph_versions WHERE workspace_id = 'workspace-a'"
        ).fetchone()[0]
        projection_count = self.connection.execute(
            """
            SELECT count(*)
            FROM cpk_realized_graph_projections
            WHERE workspace_id = 'workspace-a'
            """
        ).fetchone()[0]
        actions = self.connection.execute(
            """
            SELECT action_id, ordinal
            FROM cpk_operation_actions
            WHERE session_id = 'session-a'
            ORDER BY ordinal
            """
        ).fetchall()

        self.assertEqual(graph_count, 2)
        self.assertEqual(projection_count, 2)
        self.assertEqual(workspace[2], 1)
        self.assertEqual(workspace[0], results[0].graph_version_id)
        self.assertEqual(workspace[1], results[0].desired_realized_projection_id)
        self.assertEqual(actions[0], ("action-start", 1))
        self.assertEqual(actions[1], (results[0].action.action_id, 2))
        self.assertEqual(len(actions), 2)

    def test_concurrent_distinct_requests_publish_one_and_reject_one_stale(
        self,
    ) -> None:
        barrier = threading.Barrier(2)

        def submit(actor_id: str, ids: tuple[str, str], key: str):
            barrier.wait(timeout=5)
            try:
                return self.set_desired(
                    self.desired_service(*ids),
                    key=key,
                    actor_id=actor_id,
                )
            except StaleDesiredGraph as error:
                return error

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                submit,
                "operator-a",
                ("graph-a", "action-a"),
                "desired-a",
            )
            second = executor.submit(
                submit,
                "operator-b",
                ("graph-b", "action-b"),
                "desired-b",
            )
            outcomes = (first.result(), second.result())

        self.assertEqual(sum(isinstance(value, StaleDesiredGraph) for value in outcomes), 1)
        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            actions = unit_of_work.stores.activity_history.actions_for_session(
                "session-a"
            )
        self.assertIn(workspace.desired_graph_id, {"graph-a", "graph-b"})
        self.assertEqual(
            tuple(action.action_type.value for action in actions),
            ("start-operation-session", "set-desired-graph"),
        )

    def test_desired_graph_replay_survives_pointer_change_and_session_close(
        self,
    ) -> None:
        first = self.set_desired()
        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
        self.desired_service("graph-next", "action-next").execute(
            SetDesiredGraph(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="operator-a",
                graph=self.product_graph(),
                expected_desired_graph_id=workspace.desired_graph_id,
                expected_desired_realized_projection_id=(
                    workspace.desired_realized_projection_id
                ),
                expected_desired_graph_revision=workspace.desired_graph_revision,
                idempotency_key=IdempotencyKey("desired-next"),
            )
        )
        self.operation_service("action-close").execute(
            CloseOperationSession(
                "session-a",
                "operator-a",
                IdempotencyKey("close"),
            )
        )

        replay = self.set_desired(
            self.desired_service("unused-graph", "unused-action")
        )

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.graph_version_id, first.graph_version_id)
        self.assertEqual(
            replay.desired_realized_projection_id,
            first.desired_realized_projection_id,
        )
        self.assertEqual(replay.desired_graph_revision, first.desired_graph_revision)
        self.assertEqual(replay.action, first.action)

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
        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
        self.assertEqual(
            result.plan_record.base_realized_projection_id,
            workspace.current_realized_projection_id,
        )
        self.assertEqual(
            result.plan_record.desired_realized_projection_id,
            workspace.desired_realized_projection_id,
        )
        self.assertEqual(
            result.plan_record.desired_graph_revision,
            workspace.desired_graph_revision,
        )
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

    def test_planning_uses_selected_realized_projection_for_stable_authored_graph(
        self,
    ) -> None:
        self.set_desired()
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            before = stores.workspaces.get("workspace-a")
            assert before.desired_realized_projection_id is not None
            projected = stores.realized_graphs.save(
                RealizedGraphProjectionRecord.from_graph(
                    projection_id="projection-rotation-b",
                    workspace_id="workspace-a",
                    source_authored_graph_id="graph-desired",
                    projection_kind=(
                        RealizedGraphProjectionKind.DELEGATION_VERIFIER
                    ),
                    projection_key="rotation-b",
                    graph=self.product_graph(),
                    created_by="operator-a",
                    created_at="2026-07-22T10:02:30Z",
                )
            )
            moved = stores.workspaces.compare_and_set_desired_projection(
                "workspace-a",
                expected_authored_graph_id="graph-desired",
                expected_realized_projection_id=(
                    before.desired_realized_projection_id
                ),
                expected_revision=before.desired_graph_revision,
                replacement_realized_projection_id=projected.projection_id,
            )
            assert moved is not None
            unit_of_work.commit()

        result = self.planning_service("plan-projected", "action-projected").execute(
            RequestActivityPlan(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="operator-a",
                expected_current_graph_id="graph-current",
                expected_desired_graph_id="graph-desired",
                idempotency_key=IdempotencyKey("plan-projected"),
                expected_current_realized_projection_id=(
                    moved.current_realized_projection_id
                ),
                expected_desired_realized_projection_id=(
                    moved.desired_realized_projection_id
                ),
                expected_desired_graph_revision=moved.desired_graph_revision,
            )
        )

        self.assertEqual(result.plan_record.desired_graph_id, "graph-desired")
        self.assertEqual(
            result.plan_record.desired_realized_projection_id,
            projected.projection_id,
        )
        self.assertEqual(
            result.plan_record.desired_graph_revision,
            moved.desired_graph_revision,
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

        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
        assert workspace.current_realized_projection_id is not None
        assert workspace.desired_realized_projection_id is not None
        for key, current_projection, desired_projection in (
            (
                "stale-current-projection",
                workspace.desired_realized_projection_id,
                workspace.desired_realized_projection_id,
            ),
            (
                "stale-desired-projection",
                workspace.current_realized_projection_id,
                workspace.current_realized_projection_id,
            ),
        ):
            with self.assertRaises(ActivityPlanningGraphStateConflict):
                self.planning_service("unused-plan", "unused-action").execute(
                    RequestActivityPlan(
                        session_id="session-a",
                        workspace_id="workspace-a",
                        actor_id="operator-a",
                        expected_current_graph_id="graph-current",
                        expected_desired_graph_id="graph-desired",
                        idempotency_key=IdempotencyKey(key),
                        expected_current_realized_projection_id=(
                            current_projection
                        ),
                        expected_desired_realized_projection_id=(
                            desired_projection
                        ),
                        expected_desired_graph_revision=(
                            workspace.desired_graph_revision
                        ),
                    )
                )

    def test_replay_survives_later_pointer_and_session_state_changes(self) -> None:
        self.set_desired()
        first = self.request_plan()
        self.operation_service("action-close").execute(
            CloseOperationSession(
                "session-a",
                "operator-a",
                IdempotencyKey("close"),
            )
        )

        replay = self.request_plan(
            self.planning_service("unused-plan", "unused-action")
        )

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.plan_record, first.plan_record)

    def test_concurrent_identical_requests_converge_on_one_plan(self) -> None:
        self.set_desired()
        barrier = threading.Barrier(2)

        def submit(ids: tuple[str, str]):
            barrier.wait(timeout=5)
            return self.request_plan(self.planning_service(*ids))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    submit,
                    (("plan-a", "action-a"), ("plan-b", "action-b")),
                )
            )

        self.assertEqual(len({result.plan_record.plan_id for result in results}), 1)
        self.assertEqual(len({result.action.action_id for result in results}), 1)
        self.assertEqual(sum(result.replayed for result in results), 1)

    def test_concurrent_close_and_plan_publish_in_a_serial_session_order(
        self,
    ) -> None:
        self.set_desired()
        barrier = threading.Barrier(2)

        def plan():
            barrier.wait(timeout=5)
            try:
                return self.request_plan(
                    self.planning_service("plan-a", "action-plan")
                )
            except ActivityPlanningSessionConflict as error:
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
            plan_future = executor.submit(plan)
            close_future = executor.submit(close)
            plan_outcome = plan_future.result()
            close_future.result()

        with self.unit_of_work() as unit_of_work:
            kinds = tuple(
                action.action_type.value
                for action in unit_of_work.stores.activity_history.actions_for_session(
                    "session-a"
                )
            )
        if isinstance(plan_outcome, ActivityPlanningSessionConflict):
            self.assertEqual(
                kinds,
                (
                    "start-operation-session",
                    "set-desired-graph",
                    "close-operation-session",
                ),
            )
        else:
            self.assertEqual(
                kinds,
                (
                    "start-operation-session",
                    "set-desired-graph",
                    "request-activity-plan",
                    "close-operation-session",
                ),
            )

    def test_planning_lock_owner_commits_before_close(self) -> None:
        self.set_desired()
        plan_session_locked = threading.Event()
        plan_at_clock = threading.Event()
        release_plan = threading.Event()
        close_started = threading.Event()

        def plan_clock() -> str:
            plan_at_clock.set()
            if not release_plan.wait(timeout=5):
                raise TimeoutError("plan test barrier timed out")
            return "2026-07-22T10:03:00Z"

        def close():
            close_started.set()
            return self.operation_service("action-close").execute(
                CloseOperationSession(
                    "session-a", "operator-a", IdempotencyKey("close")
                )
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            plan_future = executor.submit(
                self.request_plan,
                self.planning_service(
                    "plan-a",
                    "action-plan",
                    clock=plan_clock,
                    unit_of_work_factory=self.observed_unit_of_work_factory(
                        after_session_lock=plan_session_locked
                    ),
                ),
            )
            if not plan_session_locked.wait(timeout=5):
                release_plan.set()
                plan_future.result(timeout=10)
                self.fail("planning did not lock the session before its clock")
            self.assertTrue(plan_at_clock.wait(timeout=5))
            close_future = executor.submit(close)
            self.assertTrue(close_started.wait(timeout=5))
            release_plan.set()
            plan_future.result(timeout=10)
            close_future.result(timeout=10)

        self.assertEqual(
            self._action_kinds(),
            (
                "start-operation-session",
                "set-desired-graph",
                "request-activity-plan",
                "close-operation-session",
            ),
        )

    def test_close_lock_owner_rejects_later_planning_without_writes(self) -> None:
        self._assert_terminal_owner_rejects_plan(cancel=False)

    def test_cancel_lock_owner_rejects_later_planning_without_writes(self) -> None:
        self._assert_terminal_owner_rejects_plan(cancel=True)

    def test_terminal_transition_is_write_once_and_exactly_replayable(self) -> None:
        self.set_desired()
        command = CloseOperationSession(
            "session-a", "operator-a", IdempotencyKey("close")
        )
        first = self.operation_service("action-close").execute(command)
        replay = self.operation_service("unused-action").execute(command)

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.session, first.session)
        self.assertEqual(replay.action, first.action)
        with self.assertRaises(OperationSessionStateConflict):
            self.operation_service("action-cancel").execute(
                CancelOperationSession(
                    "session-a", "operator-a", IdempotencyKey("cancel")
                )
            )
        self.assertEqual(
            self._action_kinds(),
            (
                "start-operation-session",
                "set-desired-graph",
                "close-operation-session",
            ),
        )

    def test_malformed_durable_graph_cannot_become_desired_truth(self) -> None:
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
            with self.assertRaisesRegex(
                RealizedGraphProjectionConflict,
                "valid realized graph material",
            ):
                unit_of_work.stores.workspaces.set_desired_graph(
                    "workspace-a",
                    "graph-invalid",
                )

        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            self.assertIsNone(workspace.desired_graph_id)
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

    def _assert_terminal_owner_rejects_plan(self, *, cancel: bool) -> None:
        self.set_desired()
        terminal_at_clock = threading.Event()
        release_terminal = threading.Event()
        plan_started = threading.Event()

        def terminal_clock() -> str:
            terminal_at_clock.set()
            if not release_terminal.wait(timeout=5):
                raise TimeoutError("terminal test barrier timed out")
            return "2026-07-22T10:04:00Z"

        terminal_command = (
            CancelOperationSession(
                "session-a", "operator-a", IdempotencyKey("cancel")
            )
            if cancel
            else CloseOperationSession(
                "session-a", "operator-a", IdempotencyKey("close")
            )
        )

        def plan():
            plan_started.set()
            return self.request_plan(
                self.planning_service(
                    "plan-a",
                    "action-plan",
                    unit_of_work_factory=self.observed_unit_of_work_factory(
                        before_session_lock=plan_session_lock_attempted
                    ),
                )
            )

        plan_session_lock_attempted = threading.Event()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            terminal_future = executor.submit(
                self.operation_service(
                    "action-terminal",
                    clock=terminal_clock,
                ).execute,
                terminal_command,
            )
            self.assertTrue(terminal_at_clock.wait(timeout=5))
            plan_future = executor.submit(plan)
            self.assertTrue(plan_started.wait(timeout=5))
            self.assertTrue(plan_session_lock_attempted.wait(timeout=5))
            release_terminal.set()
            terminal_future.result(timeout=10)
            with self.assertRaises(ActivityPlanningSessionConflict):
                plan_future.result(timeout=10)

        terminal_kind = (
            "cancel-operation-session" if cancel else "close-operation-session"
        )
        self.assertEqual(
            self._action_kinds(),
            (
                "start-operation-session",
                "set-desired-graph",
                terminal_kind,
            ),
        )
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.activity_history.plans_for_session("session-a"),
                (),
            )

    def _action_kinds(self) -> tuple[str, ...]:
        with self.unit_of_work() as unit_of_work:
            return tuple(
                action.action_type.value
                for action in unit_of_work.stores.activity_history.actions_for_session(
                    "session-a"
                )
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
