from __future__ import annotations

from dataclasses import replace
import os
import unittest

import psycopg

from gateway_rotation_overlap_fixture import (
    CrashAfterCommitUnitOfWork,
    CrashControl,
    GatewayRotationOverlapFixture,
    SimulatedProcessLoss,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.gateway_key_rotation_overlap_program import (
    GatewayKeyRotationOverlapPreparationAuthorizationDenied,
    GatewayKeyRotationOverlapPreparationConflict,
    GatewayKeyRotationOverlapPreparationOutcome,
    GatewayKeyRotationOverlapPreparationProgram,
    PrepareGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    AdvanceGatewayKeyRotationDeployment,
    GatewayKeyRotationDeploymentCheckpoint,
    GatewayKeyRotationDeploymentStatus,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.lifecycle import (
    ExecutionLeaseDuration,
    ExecutionWorkerAuthority,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema


class CountingIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"{self.prefix}-{self.count}"


class GatewayKeyRotationOverlapPreparationTests(
    GatewayRotationOverlapFixture,
    unittest.TestCase,
):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError("run through control-plane-kit-operations/test.sh")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.reset_truth()

    def tearDown(self) -> None:
        self.connection.close()

    def reset_truth(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_graph_and_keys()
        self.seed_rotation_approval()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def crashing_unit_of_work(self, control: CrashControl):
        return CrashAfterCommitUnitOfWork(self.unit_of_work(), control)

    def command(
        self,
        *,
        expected_version: int | None = None,
        expected_authored_graph_id: str = "graph-a",
        expected_current_projection_id: str = "projection-a",
        expected_desired_projection_id: str = "projection-a",
        expected_revision: int = 1,
        scopes: tuple[PolicyScope, ...] | None = None,
    ) -> PrepareGatewayKeyRotationOverlap:
        actor_scopes = scopes or (
            PolicyScope.DELEGATION_KEY_ROTATE,
            PolicyScope.PLAN_EXECUTE,
            PolicyScope.EXECUTION_OPERATE,
        )
        return PrepareGatewayKeyRotationOverlap(
            rotation_id=self.rotation_id,
            expected_rotation_version=(
                self.rotation_version
                if expected_version is None
                else expected_version
            ),
            expected_authored_graph_id=expected_authored_graph_id,
            expected_current_realized_projection_id=(
                expected_current_projection_id
            ),
            expected_desired_realized_projection_id=(
                expected_desired_projection_id
            ),
            expected_desired_graph_revision=expected_revision,
            actor_id="operator-a",
            actor_scopes=actor_scopes,
            worker_authority=ExecutionWorkerAuthority(
                "worker-a",
                (PolicyScope.EXECUTION_OPERATE,),
            ),
            lease_duration=ExecutionLeaseDuration(1800),
        )

    def program(
        self,
        *,
        unit_of_work_factory=None,
        prefix: str = "program",
    ) -> GatewayKeyRotationOverlapPreparationProgram:
        timestamps = iter(
            f"2026-08-02T02:{minute:02d}:00Z" for minute in range(30)
        )
        return GatewayKeyRotationOverlapPreparationProgram(
            unit_of_work_factory or self.unit_of_work,
            clock=lambda: next(timestamps),
            trusted_epoch_clock=lambda: 2_000,
            id_factory=CountingIds(prefix),
        )

    def test_prepares_exact_started_child_run_before_any_runtime_effect(self) -> None:
        result = self.program().prepare(self.command())

        self.assertEqual(
            result.outcome,
            GatewayKeyRotationOverlapPreparationOutcome.PREPARED,
        )
        checkpoint = result.checkpoint
        self.assertIsNotNone(checkpoint)
        assert checkpoint is not None
        self.assertEqual(
            result.rotation.status,
            GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
        )
        self.assertEqual(checkpoint.status, GatewayKeyRotationDeploymentStatus.PREPARED)
        self.assertEqual(checkpoint.approval_request_id, self.approval_request_id)
        self.assertEqual(checkpoint.approval_decision_id, self.approval_decision_id)
        self.assertEqual(checkpoint.base_authored_graph_id, "graph-a")
        self.assertEqual(checkpoint.desired_authored_graph_id, "graph-a")
        self.assertEqual(checkpoint.base_realized_projection_id, "projection-a")
        self.assertEqual(
            checkpoint.desired_realized_projection_id,
            f"gateway-rotation-{self.rotation_id}-overlap",
        )
        self.assertEqual(checkpoint.desired_revision, 2)
        self.assertEqual(checkpoint.prepared_at, "2026-08-02T02:04:00Z")
        self.assertEqual(result.handoff.checkpoint, checkpoint)
        self.assertEqual(result.handoff.fence, ExecutionLeaseFence("worker-a", 1))

        workspace = self.connection.execute(
            "SELECT current_graph_id, desired_graph_id, "
            "current_realized_projection_id, desired_realized_projection_id, "
            "desired_graph_revision FROM cpk_workspaces WHERE workspace_id=%s",
            ("workspace-a",),
        ).fetchone()
        self.assertEqual(
            workspace,
            (
                "graph-a",
                "graph-a",
                "projection-a",
                checkpoint.desired_realized_projection_id,
                2,
            ),
        )
        self.assertEqual(self._count("cpk_activity_plans"), 1)
        self.assertEqual(self._count("cpk_execution_requests"), 1)
        self.assertEqual(self._count("cpk_activity_runs"), 1)
        self.assertEqual(self._count("cpk_activity_events"), 2)
        self.assertEqual(self._count("cpk_observations"), 0)

        replay = self.program(prefix="restarted").prepare(self.command())
        self.assertEqual(
            replay.outcome,
            GatewayKeyRotationOverlapPreparationOutcome.PREPARED_REPLAY,
        )
        self.assertEqual(replay.checkpoint, checkpoint)
        self.assertEqual(replay.handoff, result.handoff)
        self.assertEqual(self._count("cpk_activity_plans"), 1)
        self.assertEqual(self._count("cpk_execution_requests"), 1)
        self.assertEqual(self._count("cpk_activity_runs"), 1)

    def test_restart_after_each_commit_recovers_exact_identities(self) -> None:
        # Read rotation, start session, publish, plan, admit, claim, start, checkpoint.
        for commit_number in range(1, 9):
            with self.subTest(commit_number=commit_number):
                self.reset_truth()
                control = CrashControl(commit_number)
                with self.assertRaises(SimulatedProcessLoss):
                    self.program(
                        unit_of_work_factory=lambda: self.crashing_unit_of_work(
                            control
                        ),
                        prefix=f"crash-{commit_number}",
                    ).prepare(self.command())

                recovered = self.program(prefix=f"recover-{commit_number}").prepare(
                    self.command()
                )
                self.assertIn(
                    recovered.outcome,
                    {
                        GatewayKeyRotationOverlapPreparationOutcome.PREPARED,
                        GatewayKeyRotationOverlapPreparationOutcome.PREPARED_REPLAY,
                    },
                )
                self.assertEqual(
                    recovered.rotation.status,
                    GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                )
                self.assertEqual(self._count("cpk_activity_plans"), 1)
                self.assertEqual(self._count("cpk_execution_requests"), 1)
                self.assertEqual(self._count("cpk_activity_runs"), 1)

    def test_stale_version_lineage_and_permission_fail_before_child_run(self) -> None:
        with self.assertRaises(GatewayKeyRotationOverlapPreparationConflict):
            self.program(prefix="stale-version").prepare(
                self.command(expected_version=self.rotation_version + 1)
            )
        self.assertEqual(self._count("cpk_activity_runs"), 0)

        with self.assertRaises(GatewayKeyRotationOverlapPreparationConflict):
            self.program(prefix="stale-lineage").prepare(
                self.command(
                    expected_current_projection_id="projection-forged",
                    expected_desired_projection_id="projection-forged",
                )
            )
        self.assertEqual(self._count("cpk_activity_runs"), 0)

        with self.assertRaises(
            GatewayKeyRotationOverlapPreparationAuthorizationDenied
        ):
            self.program(prefix="missing-scope").prepare(
                self.command(scopes=(PolicyScope.DELEGATION_KEY_ROTATE,))
            )
        self.assertEqual(self._count("cpk_activity_runs"), 0)

    def test_later_rotation_state_is_bounded_and_never_reprepares(self) -> None:
        prepared = self.program().prepare(self.command())
        assert prepared.checkpoint is not None
        accepted = replace(
            prepared.checkpoint,
            status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
            accepted_current_graph_id="graph-a",
            accepted_current_projection_id=(
                prepared.checkpoint.desired_realized_projection_id
            ),
            accepted_at="2026-08-02T03:00:00Z",
        )
        rotations = GatewayKeyRotationService(self.unit_of_work, clock=lambda: 3_000)
        rotations.advance_deployment(
            AdvanceGatewayKeyRotationDeployment(
                transition=AdvanceGatewayKeyRotation(
                    rotation_id=self.rotation_id,
                    transition_id=f"{self.rotation_id}:overlap-accepted",
                    expected_status=GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                    expected_version=prepared.rotation.version,
                    target_status=GatewayKeyRotationStatus.OVERLAP_READY,
                    advanced_by="operator-a",
                    advanced_at="2026-08-02T03:00:00Z",
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                    deployment=accepted,
                ),
                handoff=replace(prepared.handoff, checkpoint=accepted),
            ),
        )
        counts = self._child_counts()

        result = self.program(prefix="late").prepare(self.command())

        self.assertEqual(
            result.outcome,
            GatewayKeyRotationOverlapPreparationOutcome.ALREADY_ADVANCED,
        )
        self.assertEqual(result.checkpoint, accepted)
        self.assertEqual(self._child_counts(), counts)

    def _count(self, table: str) -> int:
        allowed = {
            "cpk_activity_events",
            "cpk_activity_plans",
            "cpk_activity_runs",
            "cpk_execution_requests",
            "cpk_observations",
            "cpk_operation_sessions",
        }
        if table not in allowed:
            raise AssertionError("test table is not allowlisted")
        return self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def _child_counts(self) -> tuple[int, ...]:
        return tuple(
            self._count(table)
            for table in (
                "cpk_operation_sessions",
                "cpk_activity_plans",
                "cpk_execution_requests",
                "cpk_activity_runs",
                "cpk_activity_events",
            )
        )


if __name__ == "__main__":
    unittest.main()
