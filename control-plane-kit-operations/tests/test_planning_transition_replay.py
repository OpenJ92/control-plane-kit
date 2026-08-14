from __future__ import annotations

import ast
from dataclasses import fields, replace
import os
from pathlib import Path
import unittest

import psycopg
from psycopg.types.json import Jsonb

from control_plane_kit_core.algebra import DeploymentTopology, ExternalRuntime
from control_plane_kit_core.planning import (
    ActivityPlan,
    DEFAULT_ACTIVITY_PLAN_CODEC,
    planning_scenarios,
)
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    GraphDescriptorCodec,
    compile_topology,
    validate_graph,
)
from control_plane_kit_operations import planning as planning_module
from control_plane_kit_operations.deployment_transitions import (
    Deploy,
    InitialDeployment,
    NoOpDeployment,
    TeardownDeployment,
    UpdateDeployment,
)
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService,
    ActivityPlanningGraphInvalid,
    ActivityPlanningGraphStateConflict,
    ActivityPlanningResult,
    ActivityPlanningSessionConflict,
    DesiredGraphCommandService,
    RequestActivityPlan,
    SetDesiredGraph,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import (
    CloseOperationSession,
    IdempotencyKey,
    InvalidOperationCommand,
    OperationCommandService,
    StartOperationSession,
)


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        if not self._values:
            raise AssertionError("unexpected identity allocation")
        return self._values.pop(0)


class SentinelFailure(RuntimeError):
    pass


class ExplodingCodec(GraphDescriptorCodec):
    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure
        self.decode_calls = 0

    def decode(self, descriptor):
        self.decode_calls += 1
        raise self.failure


class MissingProjectionStore:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    def get(self, projection_id: str):
        raise self.failure


class ProjectionStores:
    def __init__(self, realized_graphs) -> None:
        self.realized_graphs = realized_graphs


class ProjectionUnitOfWork:
    def __init__(self, realized_graphs) -> None:
        self.stores = ProjectionStores(realized_graphs)


class StaticProjectionStore:
    def __init__(self, record: RealizedGraphProjectionRecord) -> None:
        self.record = record

    def get(self, projection_id: str) -> RealizedGraphProjectionRecord:
        return self.record


class ReplayHistory:
    def __init__(
        self,
        *,
        action,
        plan=None,
        session=None,
        plan_failure: BaseException | None = None,
        session_failure: BaseException | None = None,
    ) -> None:
        self.action = action
        self.plan = plan
        self.session = session
        self.plan_failure = plan_failure
        self.session_failure = session_failure

    def lock_action_idempotency(self, session_id: str, key: str) -> None:
        return None

    def action_for_idempotency(self, session_id: str, key: str):
        return self.action

    def get_plan(self, plan_id: str):
        if self.plan_failure is not None:
            raise self.plan_failure
        return self.plan

    def get_session(self, session_id: str):
        if self.session_failure is not None:
            raise self.session_failure
        return self.session


class ReplayStores:
    def __init__(self, history: ReplayHistory) -> None:
        self.activity_history = history


class ReplayUnitOfWork:
    def __init__(self, history: ReplayHistory) -> None:
        self.stores = ReplayStores(history)
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def commit(self) -> None:
        self.commits += 1


class PlanningTransitionReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; run this test "
                "through the Docker-first Operations test apparatus"
            )
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self._reset()

    def tearDown(self) -> None:
        try:
            self._reset()
        finally:
            self.connection.close()

    def _reset(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def operation_service(self, *ids: str) -> OperationCommandService:
        return OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-14T10:00:00Z",
            id_factory=Sequence(*ids),
        )

    def desired_service(self, *ids: str) -> DesiredGraphCommandService:
        return DesiredGraphCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-14T10:01:00Z",
            id_factory=Sequence(*ids),
        )

    def planning_service(
        self,
        *ids: str,
        graph_codec: GraphDescriptorCodec | None = None,
        unit_of_work_factory=None,
    ) -> ActivityPlanningCommandService:
        arguments = {}
        if graph_codec is not None:
            arguments["graph_codec"] = graph_codec
        return ActivityPlanningCommandService(
            unit_of_work_factory or self.unit_of_work,
            clock=lambda: "2026-08-14T10:02:00Z",
            id_factory=Sequence(*ids),
            **arguments,
        )

    def scenario(self, scenario_id: str):
        return next(
            value
            for value in planning_scenarios()
            if value.scenario_id == scenario_id
        )

    def plan(
        self,
        current: DeploymentGraph,
        desired: DeploymentGraph,
    ) -> tuple[RequestActivityPlan, ActivityPlanningResult]:
        command = self.prepare(current, desired)
        return command, self.planning_service("plan-a", "action-plan").execute(
            command
        )

    def prepare(
        self,
        current: DeploymentGraph,
        desired: DeploymentGraph,
    ) -> RequestActivityPlan:
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.workspaces.create(WorkspaceRecord("workspace-a", "Workspace A"))
            current_record = GraphVersionRecord.from_graph(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph=current,
                created_by="operator-a",
                created_at="2026-08-14T09:59:00Z",
            )
            stores.graphs.save(current_record)
            stores.workspaces.set_current_graph("workspace-a", current_record.graph_id)
            unit_of_work.commit()

        self.operation_service("session-a", "action-start").execute(
            StartOperationSession(
                workspace_id="workspace-a",
                actor_id="operator-a",
                title="Plan transition replay",
                idempotency_key=IdempotencyKey("session"),
            )
        )
        desired_result = self.desired_service(
            "graph-desired", "action-desired"
        ).execute(
            SetDesiredGraph(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="operator-a",
                graph=desired,
                expected_desired_graph_id=None,
                expected_desired_realized_projection_id=None,
                expected_desired_graph_revision=0,
                idempotency_key=IdempotencyKey("desired"),
            )
        )
        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
        assert workspace.current_graph_id is not None
        assert workspace.current_realized_projection_id is not None
        command = RequestActivityPlan(
            session_id="session-a",
            workspace_id="workspace-a",
            actor_id="operator-a",
            expected_current_graph_id=workspace.current_graph_id,
            expected_desired_graph_id=desired_result.graph_version_id,
            expected_current_realized_projection_id=(
                workspace.current_realized_projection_id
            ),
            expected_desired_realized_projection_id=(
                desired_result.desired_realized_projection_id
            ),
            expected_desired_graph_revision=(
                desired_result.desired_graph_revision
            ),
            idempotency_key=IdempotencyKey("plan"),
        )
        return command

    def test_result_retains_exact_transition_without_descriptor_or_repr_material(
        self,
    ) -> None:
        scenario = self.scenario("fresh-deployment")
        _, result = self.plan(scenario.current_graph, scenario.desired_graph)

        transition_field = next(
            value for value in fields(ActivityPlanningResult) if value.name == "transition"
        )
        self.assertFalse(transition_field.repr)
        self.assertIsInstance(result.transition, InitialDeployment)
        self.assertEqual(
            result.plan_record.plan,
            planning_module.compile_activity_plan(result.transition.diff),
        )
        self.assertNotIn(scenario.current_graph.name, repr(result))
        self.assertNotIn(scenario.desired_graph.name, repr(result))
        self.assertEqual(
            result.descriptor(),
            {
                "plan_id": result.plan_record.plan_id,
                "session_id": result.plan_record.session_id,
                "base_graph_id": result.plan_record.base_graph_id,
                "desired_graph_id": result.plan_record.desired_graph_id,
                "base_realized_projection_id": (
                    result.plan_record.base_realized_projection_id
                ),
                "desired_realized_projection_id": (
                    result.plan_record.desired_realized_projection_id
                ),
                "desired_graph_revision": (
                    result.plan_record.desired_graph_revision
                ),
                "ready_for_execution": (
                    result.plan_record.plan.ready_for_execution
                ),
                "activity_count": len(result.plan_record.plan.activities),
                "action_id": result.action.action_id,
                "action_ordinal": result.action.ordinal,
                "replayed": False,
            },
        )

    def test_equal_graph_values_under_distinct_ids_are_no_op(self) -> None:
        scenario = self.scenario("no-change")
        _, result = self.plan(scenario.current_graph, scenario.desired_graph)

        self.assertIsInstance(result.transition, NoOpDeployment)
        self.assertEqual(result.plan_record.plan.activities, ())
        self.assertNotEqual(
            result.plan_record.base_graph_id,
            result.plan_record.desired_graph_id,
        )

    def test_distinct_empty_graph_names_are_zero_activity_update(self) -> None:
        _, result = self.plan(
            DeploymentGraph("empty-before"),
            DeploymentGraph("empty-after"),
        )

        self.assertIsInstance(result.transition, UpdateDeployment)
        self.assertEqual(result.plan_record.plan.activities, ())
        self.assertFalse(result.transition.diff.empty)

    def test_external_runtime_addition_is_zero_activity_initial_deployment(
        self,
    ) -> None:
        desired = compile_topology(
            DeploymentTopology("external-runtime", ExternalRuntime())
        )
        _, result = self.plan(DeploymentGraph("empty"), desired)

        self.assertIsInstance(result.transition, InitialDeployment)
        self.assertEqual(result.plan_record.plan.activities, ())
        self.assertFalse(result.transition.diff.empty)

    def test_scenario_matrix_returns_exact_transition_forms(self) -> None:
        cases = (
            ("fresh-deployment", InitialDeployment),
            ("backend-switch", UpdateDeployment),
            ("full-teardown", TeardownDeployment),
            ("no-change", NoOpDeployment),
        )
        for index, (scenario_id, expected_type) in enumerate(cases):
            with self.subTest(scenario_id=scenario_id):
                if index:
                    self._reset()
                scenario = self.scenario(scenario_id)
                _, result = self.plan(
                    scenario.current_graph,
                    scenario.desired_graph,
                )
                self.assertIsInstance(result.transition, expected_type)
                self.assertEqual(
                    result.plan_record.plan,
                    planning_module.compile_activity_plan(result.transition.diff),
                )

    def test_review_blocked_plan_retains_update_transition(self) -> None:
        scenario = self.scenario("unsupported-implementation-transition")
        _, result = self.plan(scenario.current_graph, scenario.desired_graph)

        self.assertIsInstance(result.transition, UpdateDeployment)
        self.assertFalse(result.plan_record.plan.ready_for_execution)

    def test_new_service_replays_plan_pinned_transition_after_pointer_move(
        self,
    ) -> None:
        scenario = self.scenario("backend-switch")
        command, first = self.plan(scenario.current_graph, scenario.desired_graph)
        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
        self.desired_service("graph-moved", "action-moved").execute(
            SetDesiredGraph(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="operator-a",
                graph=DeploymentGraph("later-desired-pointer"),
                expected_desired_graph_id=workspace.desired_graph_id,
                expected_desired_realized_projection_id=(
                    workspace.desired_realized_projection_id
                ),
                expected_desired_graph_revision=workspace.desired_graph_revision,
                idempotency_key=IdempotencyKey("move-desired"),
            )
        )
        self.operation_service("action-close").execute(
            CloseOperationSession(
                session_id="session-a",
                actor_id="operator-a",
                idempotency_key=IdempotencyKey("close"),
            )
        )
        before = self._durable_counts()

        replay = self.planning_service().execute(command)

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.plan_record, first.plan_record)
        self.assertEqual(replay.transition, first.transition)
        self.assertEqual(self._durable_counts(), before)
        with self.unit_of_work() as unit_of_work:
            moved = unit_of_work.stores.workspaces.get("workspace-a")
        self.assertEqual(moved.desired_graph_id, "graph-moved")
        self.assertNotEqual(
            moved.desired_graph_id,
            replay.plan_record.desired_graph_id,
        )

    def test_workspace_evidence_conflict_precedes_projection_decode(self) -> None:
        scenario = self.scenario("backend-switch")
        command, result = self.plan(scenario.current_graph, scenario.desired_graph)
        self.connection.execute(
            """
            UPDATE cpk_operation_actions
            SET payload = jsonb_set(payload, '{workspace_id}', '"workspace-b"')
            WHERE action_id = %s
            """,
            (result.action.action_id,),
        )
        codec = ExplodingCodec(AssertionError("codec must not run"))

        with self.assertRaises(ActivityPlanningGraphStateConflict) as captured:
            self.planning_service(graph_codec=codec).execute(command)

        self.assertEqual(str(captured.exception), "planning replay evidence is incongruent")
        self.assertEqual(codec.decode_calls, 0)
        self._assert_clean_error(captured.exception)

    def test_missing_plan_and_session_use_distinct_bounded_replay_categories(
        self,
    ) -> None:
        scenario = self.scenario("backend-switch")
        command, result = self.plan(scenario.current_graph, scenario.desired_graph)
        self.connection.execute(
            """
            UPDATE cpk_operation_actions
            SET payload = jsonb_set(payload, '{plan_id}', '"MISSING-PLAN-CANARY"')
            WHERE action_id = %s
            """,
            (result.action.action_id,),
        )
        with self.assertRaises(ActivityPlanningGraphStateConflict) as missing_plan:
            self.planning_service().execute(command)
        self.assertEqual(str(missing_plan.exception), "planning replay truth is missing")
        self._assert_clean_error(missing_plan.exception, "MISSING-PLAN-CANARY")

        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        command, result = self.plan(scenario.current_graph, scenario.desired_graph)
        history = ReplayHistory(
            action=result.action,
            plan=result.plan_record,
            session_failure=KeyError("MISSING-SESSION-CANARY"),
        )
        unit_of_work = ReplayUnitOfWork(history)
        with self.assertRaises(ActivityPlanningSessionConflict) as missing_session:
            self.planning_service(
                unit_of_work_factory=lambda: unit_of_work
            ).execute(command)
        self.assertEqual(
            str(missing_session.exception),
            "planning replay session is missing",
        )
        self._assert_clean_error(
            missing_session.exception,
            "MISSING-SESSION-CANARY",
        )
        self.assertEqual(unit_of_work.commits, 0)

    def test_foreign_projection_membership_is_bounded_before_graph_use(self) -> None:
        foreign = RealizedGraphProjectionRecord.identity_for_authored(
            authored_record=GraphVersionRecord.from_graph(
                graph_id="graph-a",
                workspace_id="workspace-b",
                version=1,
                graph=DeploymentGraph("FOREIGN-GRAPH-CANARY"),
                created_by="operator-b",
                created_at="2026-08-14T10:00:00Z",
            )
        )

        with self.assertRaises(ActivityPlanningGraphStateConflict) as captured:
            planning_module._projection_record(
                ProjectionUnitOfWork(StaticProjectionStore(foreign)),
                foreign.projection_id,
                "graph-a",
                "workspace-a",
            )

        self.assertEqual(str(captured.exception), "realized graph truth is unavailable")
        self._assert_clean_error(
            captured.exception,
            "workspace-b",
            "FOREIGN-GRAPH-CANARY",
        )

    def test_first_planning_translates_malformed_projection_cause_free(self) -> None:
        scenario = self.scenario("backend-switch")
        command = self.prepare(scenario.current_graph, scenario.desired_graph)
        self.connection.execute(
            """
            UPDATE cpk_realized_graph_projections
            SET graph_descriptor = '{"name":"FIRST-GRAPH-CANARY","nodes":"bad"}'::jsonb
            WHERE projection_id = %s
            """,
            (command.expected_desired_realized_projection_id,),
        )

        with self.assertRaises(ActivityPlanningGraphInvalid) as captured:
            self.planning_service("plan-a", "action-plan").execute(command)

        self.assertEqual(str(captured.exception), "persisted graph pair is invalid")
        self._assert_clean_error(captured.exception, "FIRST-GRAPH-CANARY")

    def test_replay_rejects_malformed_graph_and_plan_incongruence(self) -> None:
        scenario = self.scenario("backend-switch")
        command, result = self.plan(scenario.current_graph, scenario.desired_graph)
        self.connection.execute(
            """
            UPDATE cpk_realized_graph_projections
            SET graph_descriptor = '{"name":"GRAPH-CANARY","nodes":"bad"}'::jsonb
            WHERE projection_id = %s
            """,
            (result.plan_record.desired_realized_projection_id,),
        )
        with self.assertRaises(ActivityPlanningGraphInvalid) as malformed:
            self.planning_service().execute(command)
        self.assertEqual(str(malformed.exception), "persisted graph pair is invalid")
        self._assert_clean_error(malformed.exception, "GRAPH-CANARY")

        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        command, result = self.plan(scenario.current_graph, scenario.desired_graph)
        self.connection.execute(
            "UPDATE cpk_activity_plans SET payload = %s WHERE plan_id = %s",
            (
                Jsonb(DEFAULT_ACTIVITY_PLAN_CODEC.encode(ActivityPlan(()))),
                result.plan_record.plan_id,
            ),
        )
        with self.assertRaises(ActivityPlanningGraphStateConflict) as mismatch:
            self.planning_service().execute(command)
        self.assertEqual(
            str(mismatch.exception),
            "persisted plan does not match graph transition",
        )
        self._assert_clean_error(mismatch.exception)

    def test_replay_rejects_decodable_validation_invalid_graph(self) -> None:
        scenario = self.scenario("backend-switch")
        command, result = self.plan(scenario.current_graph, scenario.desired_graph)
        connected = self.scenario("insert-rate-limiter").desired_graph
        invalid = replace(connected, edges={})
        self.assertFalse(validate_graph(invalid).valid)
        self.connection.execute(
            """
            UPDATE cpk_realized_graph_projections
            SET graph_descriptor = %s
            WHERE projection_id = %s
            """,
            (
                Jsonb(DEFAULT_GRAPH_CODEC.encode(invalid)),
                result.plan_record.desired_realized_projection_id,
            ),
        )

        with self.assertRaises(ActivityPlanningGraphInvalid) as captured:
            self.planning_service().execute(command)

        self.assertEqual(str(captured.exception), "persisted graph pair is invalid")
        self._assert_clean_error(captured.exception, invalid.name)

    def test_unexpected_codec_failure_escapes_replay_by_identity(self) -> None:
        scenario = self.scenario("backend-switch")
        command, _ = self.plan(scenario.current_graph, scenario.desired_graph)
        sentinel = SentinelFailure("unexpected-codec-failure")
        codec = ExplodingCodec(sentinel)

        with self.assertRaises(SentinelFailure) as captured:
            self.planning_service(graph_codec=codec).execute(command)

        self.assertIs(captured.exception, sentinel)
        self.assertEqual(codec.decode_calls, 1)

    def test_shared_projection_lookup_is_bounded_and_unexpected_errors_escape(
        self,
    ) -> None:
        with self.assertRaises(ActivityPlanningGraphStateConflict) as missing:
            planning_module._projection_record(
                ProjectionUnitOfWork(
                    MissingProjectionStore(KeyError("STORE-CANARY"))
                ),
                "PROJECTION-CANARY",
                "graph-a",
                "workspace-a",
            )
        self.assertEqual(str(missing.exception), "realized graph truth is unavailable")
        self._assert_clean_error(
            missing.exception,
            "STORE-CANARY",
            "PROJECTION-CANARY",
        )

        sentinel = SentinelFailure("unexpected-store-failure")
        with self.assertRaises(SentinelFailure) as unexpected:
            planning_module._projection_record(
                ProjectionUnitOfWork(MissingProjectionStore(sentinel)),
                "projection-a",
                "graph-a",
                "workspace-a",
            )
        self.assertIs(unexpected.exception, sentinel)

    def test_result_rejects_transition_whose_compiled_plan_is_incongruent(
        self,
    ) -> None:
        scenario = self.scenario("fresh-deployment")
        _, result = self.plan(scenario.current_graph, scenario.desired_graph)
        same = validate_graph(DeploymentGraph("same"))
        wrong_transition = Deploy(same, same)

        with self.assertRaises(TypeError):
            ActivityPlanningResult(result.plan_record, result.action)
        with self.assertRaises(InvalidOperationCommand):
            ActivityPlanningResult(result.plan_record, result.action, object())
        with self.assertRaises(InvalidOperationCommand):
            ActivityPlanningResult(
                result.plan_record,
                result.action,
                wrong_transition,
            )

    def test_source_uses_deploy_without_a_second_diff_classifier(self) -> None:
        source_path = Path(planning_module.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.setdefault(node.module, set()).update(
                    alias.name for alias in node.names
                )

        module_functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        service_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ActivityPlanningCommandService"
        )
        first_execute = next(
            node
            for node in service_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "execute"
        )
        replay = module_functions["_activity_plan_replay"]

        def reachable_calls(root) -> set[str]:
            reachable: set[str] = set()
            pending = [root]
            visited: set[str] = set()
            while pending:
                function = pending.pop()
                for node in ast.walk(function):
                    if not (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                    ):
                        continue
                    name = node.func.id
                    reachable.add(name)
                    if name in module_functions and name not in visited:
                        visited.add(name)
                        pending.append(module_functions[name])
            return reachable

        first_calls = reachable_calls(first_execute)
        replay_calls = reachable_calls(replay)

        self.assertIn(
            "Deploy",
            imported.get(
                "control_plane_kit_operations.deployment_transitions",
                set(),
            ),
        )
        self.assertNotIn(
            "diff_graphs",
            imported.get("control_plane_kit_core.topology", set()),
        )
        for owner, calls in (("first", first_calls), ("replay", replay_calls)):
            with self.subTest(owner=owner):
                self.assertIn("Deploy", calls)
                self.assertIn("compile_activity_plan", calls)
                self.assertNotIn("diff_graphs", calls)

    def _durable_counts(self) -> tuple[int, ...]:
        return tuple(
            self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "cpk_graph_versions",
                "cpk_realized_graph_projections",
                "cpk_activity_plans",
                "cpk_operation_actions",
            )
        )

    def _assert_clean_error(
        self,
        error: BaseException,
        *canaries: str,
    ) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        for canary in canaries:
            self.assertNotIn(canary, str(error))


if __name__ == "__main__":
    unittest.main()
