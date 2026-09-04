from __future__ import annotations

from dataclasses import replace
import importlib
import itertools
import os
import unittest

import psycopg

from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    PrincipalIdentity,
    PrincipalKind,
    WorkspaceGrant,
)
from control_plane_kit_core.planning import planning_scenarios
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.approvals import ApprovalCommandService
from control_plane_kit_operations.deployment_program import PrepareDeploymentProgram
from control_plane_kit_operations.deployment_program_projections import (
    DeploymentApprovalRequired,
    DeploymentNoChanges,
    DeploymentReviewBlocked,
)
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService,
    DesiredGraphCommandService,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    GraphProjectionLineage,
    GraphVersionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    OperationCommandService,
)


class SentinelFailure(RuntimeError):
    pass


class FailBefore:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    def execute(self, command):
        raise self.failure


class FailAfter:
    def __init__(self, service, failure: BaseException) -> None:
        self.service = service
        self.failure = failure

    def execute(self, command):
        self.service.execute(command)
        raise self.failure


class DeploymentProgramPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; run this test "
                "through the Docker-first Operations apparatus"
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self._reset()
        self._identities = itertools.count(1)

    def tearDown(self) -> None:
        try:
            self._reset()
        finally:
            self.connection.close()

    def _reset(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def module(self):
        try:
            return importlib.import_module(
                "control_plane_kit_operations.deployment_program_interpreter"
            )
        except ModuleNotFoundError as error:
            if error.name != (
                "control_plane_kit_operations.deployment_program_interpreter"
            ):
                raise
            self.fail(f"deployment program interpreter is missing: {error}")

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def id_factory(self) -> str:
        return f"program-{next(self._identities)}"

    def services(self):
        return (
            OperationCommandService(
                self.unit_of_work,
                clock=lambda: "2026-08-14T13:00:00Z",
                id_factory=self.id_factory,
            ),
            DesiredGraphCommandService(
                self.unit_of_work,
                clock=lambda: "2026-08-14T13:01:00Z",
                id_factory=self.id_factory,
            ),
            ActivityPlanningCommandService(
                self.unit_of_work,
                clock=lambda: "2026-08-14T13:02:00Z",
                id_factory=self.id_factory,
            ),
            ApprovalCommandService(
                self.unit_of_work,
                clock=lambda: "2026-08-14T13:03:00Z",
                id_factory=self.id_factory,
            ),
        )

    def program(self, services=None):
        return self.module().DeploymentProgram(*(services or self.services()))

    def context(
        self,
        workspace_id: str,
        *,
        actor_id: str = "operator-a",
        scopes: tuple[PolicyScope, ...] = (
            PolicyScope.INSTANCE_WORKSPACE_EDIT,
            PolicyScope.PLAN_REQUEST,
        ),
    ):
        principal = AuthenticatedPrincipal(
            PrincipalIdentity("issuer-a", actor_id, PrincipalKind.OPERATOR),
            (WorkspaceGrant(workspace_id, scopes),),
        )
        return principal.command_context(workspace_id)

    def setup_workspace(
        self,
        current: DeploymentGraph,
        *,
        workspace_id: str = "workspace-a",
    ) -> GraphProjectionLineage:
        graph_id = f"{workspace_id}-current"
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.workspaces.create(WorkspaceRecord(workspace_id, workspace_id))
            record = GraphVersionRecord.from_graph(
                graph_id=graph_id,
                workspace_id=workspace_id,
                version=1,
                graph=current,
                created_by="operator-a",
                created_at="2026-08-14T12:59:00Z",
            )
            stores.graphs.save(record)
            workspace = stores.workspaces.set_current_graph(workspace_id, graph_id)
            unit_of_work.commit()
        assert workspace.current_realized_projection_id is not None
        return GraphProjectionLineage(
            graph_id,
            workspace.current_realized_projection_id,
        )

    def command(
        self,
        desired: DeploymentGraph,
        current: GraphProjectionLineage,
        *,
        workspace_id: str = "workspace-a",
        actor_id: str = "operator-a",
        parent_key: str = "prepare-a",
    ) -> PrepareDeploymentProgram:
        return PrepareDeploymentProgram(
            context=self.context(workspace_id, actor_id=actor_id),
            desired=desired,
            expected_current=current,
            expected_desired=None,
            expected_desired_graph_revision=0,
            title="Prepare deployment",
            idempotency_key=IdempotencyKey(parent_key),
            approval_comment="Please review",
        )

    def scenario(self, scenario_id: str):
        return next(
            value
            for value in planning_scenarios()
            if value.scenario_id == scenario_id
        )

    def test_scenario_matrix_replays_exact_terminal_projection(self) -> None:
        cases = (
            ("no-change", DeploymentNoChanges, 0),
            ("unsupported-implementation-transition", DeploymentReviewBlocked, 0),
            ("fresh-deployment", DeploymentApprovalRequired, 1),
            ("backend-switch", DeploymentApprovalRequired, 1),
            ("full-teardown", DeploymentApprovalRequired, 1),
        )
        for scenario_id, expected_type, approval_count in cases:
            with self.subTest(scenario_id=scenario_id):
                self._reset()
                scenario = self.scenario(scenario_id)
                current = self.setup_workspace(scenario.current_graph)
                command = self.command(scenario.desired_graph, current)

                first = self.program().prepare(command)
                before = self._counts()
                replay = self.program().prepare(command)

                self.assertIsInstance(first, expected_type)
                self.assertEqual(replay, first)
                self.assertEqual(self._counts(), before)
                self.assertEqual(before["cpk_operation_sessions"], 1)
                self.assertEqual(before["cpk_activity_plans"], 1)
                self.assertEqual(before["cpk_approval_requests"], approval_count)
                self.assertEqual(
                    before["cpk_operation_actions"],
                    3 + approval_count,
                )
                self._assert_current_lineage(current)
                self._assert_no_progression_truth(before)

    def test_zero_activity_graph_update_still_requests_approval(self) -> None:
        current = self.setup_workspace(DeploymentGraph("before"))
        command = self.command(DeploymentGraph("after"), current)

        result = self.program().prepare(command)

        self.assertIsInstance(result, DeploymentApprovalRequired)
        with self.unit_of_work() as unit_of_work:
            plan = unit_of_work.stores.activity_history.get_plan(
                result.reference.plan_id
            )
        self.assertEqual(plan.plan.activities, ())
        self.assertEqual(self._counts()["cpk_approval_requests"], 1)
        self._assert_current_lineage(current)

    def test_restart_after_each_physical_commit_converges_without_duplicates(
        self,
    ) -> None:
        for crash_stage in ("session", "desired", "plan", "approval"):
            with self.subTest(crash_stage=crash_stage):
                self._reset()
                scenario = self.scenario("fresh-deployment")
                current = self.setup_workspace(scenario.current_graph)
                command = self.command(scenario.desired_graph, current)
                services = list(self.services())
                sentinel = SentinelFailure(f"crash-after-{crash_stage}")
                if crash_stage == "session":
                    services[1] = FailBefore(sentinel)
                elif crash_stage == "desired":
                    services[2] = FailBefore(sentinel)
                elif crash_stage == "plan":
                    services[3] = FailBefore(sentinel)
                else:
                    services[3] = FailAfter(services[3], sentinel)

                with self.assertRaises(SentinelFailure) as captured:
                    self.program(tuple(services)).prepare(command)
                self.assertIs(captured.exception, sentinel)

                result = self.program().prepare(command)
                replay = self.program().prepare(command)
                counts = self._counts()
                self.assertIsInstance(result, DeploymentApprovalRequired)
                self.assertEqual(replay, result)
                self.assertEqual(counts["cpk_operation_sessions"], 1)
                self.assertEqual(counts["cpk_graph_versions"], 2)
                self.assertEqual(counts["cpk_activity_plans"], 1)
                self.assertEqual(counts["cpk_approval_requests"], 1)
                self.assertEqual(counts["cpk_operation_actions"], 4)
                self._assert_no_progression_truth(counts)

    def test_changed_intent_after_session_commit_conflicts_before_desired_write(
        self,
    ) -> None:
        scenario = self.scenario("fresh-deployment")
        changes = (
            lambda value: replace(
                value,
                context=self.context("workspace-a", actor_id="operator-b"),
            ),
            lambda value: replace(value, desired=DeploymentGraph("changed-desired")),
            lambda value: replace(
                value,
                expected_current=GraphProjectionLineage(
                    "changed-current",
                    "changed-current-projection",
                ),
            ),
            lambda value: replace(
                value,
                expected_desired=GraphProjectionLineage(
                    "changed-desired-id",
                    "changed-desired-projection",
                ),
                expected_desired_graph_revision=1,
            ),
            lambda value: replace(value, title="Changed title"),
            lambda value: replace(value, approval_comment="Changed comment"),
        )
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                self._reset()
                current = self.setup_workspace(scenario.current_graph)
                command = self.command(scenario.desired_graph, current)
                services = list(self.services())
                sentinel = SentinelFailure("stop-after-session")
                services[1] = FailBefore(sentinel)
                with self.assertRaises(SentinelFailure):
                    self.program(tuple(services)).prepare(command)

                module = self.module()
                with self.assertRaises(module.DeploymentProgramStateConflict) as captured:
                    self.program().prepare(change(command))

                self.assertEqual(
                    str(captured.exception),
                    "deployment preparation state is unavailable",
                )
                self.assertIsNone(captured.exception.__cause__)
                self.assertIsNone(captured.exception.__context__)
                counts = self._counts()
                self.assertEqual(counts["cpk_operation_sessions"], 1)
                self.assertEqual(counts["cpk_graph_versions"], 1)
                self.assertEqual(counts["cpk_activity_plans"], 0)
                self.assertEqual(counts["cpk_approval_requests"], 0)
                self.assertEqual(counts["cpk_operation_actions"], 1)
                self._assert_no_progression_truth(counts)

    def test_same_parent_key_is_independent_across_workspace_namespaces(self) -> None:
        current_a = self.setup_workspace(
            DeploymentGraph("same"),
            workspace_id="workspace-a",
        )
        current_b = self.setup_workspace(
            DeploymentGraph("same"),
            workspace_id="workspace-b",
        )
        command_a = self.command(
            DeploymentGraph("same"),
            current_a,
            workspace_id="workspace-a",
            parent_key="shared-parent",
        )
        command_b = self.command(
            DeploymentGraph("same"),
            current_b,
            workspace_id="workspace-b",
            parent_key="shared-parent",
        )

        result_a = self.program().prepare(command_a)
        result_b = self.program().prepare(command_b)

        self.assertIsInstance(result_a, DeploymentNoChanges)
        self.assertIsInstance(result_b, DeploymentNoChanges)
        self.assertNotEqual(result_a.reference.plan_id, result_b.reference.plan_id)
        rows = self.connection.execute(
            """
            SELECT workspace_id, idempotency_key
            FROM cpk_operation_sessions
            ORDER BY workspace_id
            """
        ).fetchall()
        self.assertEqual(tuple(row[0] for row in rows), ("workspace-a", "workspace-b"))
        self.assertNotEqual(rows[0][1], rows[1][1])

    def test_stale_or_unavailable_graph_truth_records_no_later_stage(self) -> None:
        scenario = self.scenario("fresh-deployment")
        cases = (
            ("stale-desired", (1, 1, 1, 1)),
            ("missing-current", (1, 2, 2, 2)),
            ("malformed-current", (1, 2, 2, 2)),
        )
        for case, partial_counts in cases:
            with self.subTest(case=case):
                self._reset()
                current = self.setup_workspace(scenario.current_graph)
                command = self.command(scenario.desired_graph, current)
                if case == "stale-desired":
                    command = replace(
                        command,
                        expected_desired=GraphProjectionLineage(
                            "stale-desired",
                            "stale-projection",
                        ),
                        expected_desired_graph_revision=1,
                    )
                elif case == "missing-current":
                    command = replace(
                        command,
                        expected_current=GraphProjectionLineage(
                            current.authored_graph_id,
                            "missing-projection",
                        ),
                    )
                else:
                    self.connection.execute(
                        """
                        UPDATE cpk_realized_graph_projections
                        SET graph_descriptor =
                            '{"name":"GRAPH-CANARY","nodes":"bad"}'::jsonb
                        WHERE projection_id = %s
                        """,
                        (current.realized_projection_id,),
                    )

                module = self.module()
                with self.assertRaises(module.DeploymentProgramStateConflict) as captured:
                    self.program().prepare(command)

                self.assertEqual(
                    str(captured.exception),
                    "deployment preparation state is unavailable",
                )
                self.assertNotIn("CANARY", str(captured.exception))
                self.assertIsNone(captured.exception.__cause__)
                self.assertIsNone(captured.exception.__context__)
                counts = self._counts()
                self.assertEqual(
                    (
                        counts["cpk_operation_sessions"],
                        counts["cpk_operation_actions"],
                        counts["cpk_graph_versions"],
                        counts["cpk_realized_graph_projections"],
                    ),
                    partial_counts,
                )
                self.assertEqual(counts["cpk_activity_plans"], 0)
                self.assertEqual(counts["cpk_approval_requests"], 0)
                self._assert_current_lineage(current)
                self._assert_no_progression_truth(counts)

    def test_session_metadata_contains_only_bounded_intent_commitment(self) -> None:
        current = self.setup_workspace(DeploymentGraph("same"))
        command = self.command(DeploymentGraph("same"), current)

        self.program().prepare(command)

        metadata = self.connection.execute(
            "SELECT metadata FROM cpk_operation_sessions"
        ).fetchone()[0]
        self.assertEqual(tuple(metadata), ("deployment_prepare_intent_sha256",))
        digest = metadata["deployment_prepare_intent_sha256"]
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        rendered = repr(metadata)
        for forbidden in (
            command.title,
            command.approval_comment,
            command.desired.name,
            command.context.actor_id,
            command.context.workspace_id,
            command.idempotency_key.value,
        ):
            self.assertNotIn(forbidden, rendered)

    def _counts(self) -> dict[str, int]:
        tables = (
            "cpk_operation_sessions",
            "cpk_operation_actions",
            "cpk_graph_versions",
            "cpk_realized_graph_projections",
            "cpk_activity_plans",
            "cpk_approval_requests",
            "cpk_approval_decisions",
            "cpk_execution_requests",
            "cpk_activity_runs",
            "cpk_activity_events",
            "cpk_observations",
        )
        return {
            table: self.connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0]
            for table in tables
        }

    def _assert_no_progression_truth(self, counts: dict[str, int]) -> None:
        for table in (
            "cpk_approval_decisions",
            "cpk_execution_requests",
            "cpk_activity_runs",
            "cpk_activity_events",
            "cpk_observations",
        ):
            self.assertEqual(counts[table], 0, table)

    def _assert_current_lineage(self, expected: GraphProjectionLineage) -> None:
        current = self.connection.execute(
            """
            SELECT current_graph_id, current_realized_projection_id
            FROM cpk_workspaces
            WHERE workspace_id = 'workspace-a'
            """
        ).fetchone()
        self.assertEqual(
            current,
            (
                expected.authored_graph_id,
                expected.realized_projection_id,
            ),
        )


if __name__ == "__main__":
    unittest.main()
