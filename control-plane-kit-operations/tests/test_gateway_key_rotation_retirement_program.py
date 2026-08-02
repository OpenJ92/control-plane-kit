from __future__ import annotations

from dataclasses import replace
import os
import unittest

import psycopg

from gateway_rotation_overlap_fixture import (
    CrashAfterCommitUnitOfWork,
    CrashControl,
    GatewayRotationOverlapFixture,
    PUBLIC_KEY_OTHER,
    SimulatedProcessLoss,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.gateway_key_rotation_overlap_program import (
    GatewayKeyRotationOverlapPreparationProgram,
    PrepareGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotation_retirement_program import (
    GatewayKeyRotationRetirementPreparationAuthorizationDenied,
    GatewayKeyRotationRetirementPreparationConflict,
    GatewayKeyRotationRetirementPreparationOutcome,
    GatewayKeyRotationRetirementPreparationProgram,
    PrepareGatewayKeyRotationRetirement,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    GatewayKeyRotationDeploymentStatus,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema


class CountingIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"{self.prefix}-{self.count}"


class GatewayKeyRotationRetirementPreparationTests(
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

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def reset_truth(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_graph_and_keys()
        self.seed_rotation_approval()
        overlap = GatewayKeyRotationOverlapPreparationProgram(
            self.unit_of_work,
            clock=self._timestamp_clock("overlap"),
            trusted_epoch_clock=lambda: 1_000,
            id_factory=CountingIds("overlap"),
        ).prepare(
            PrepareGatewayKeyRotationOverlap(
                rotation_id=self.rotation_id,
                expected_rotation_version=self.rotation_version,
                expected_authored_graph_id="graph-a",
                expected_current_realized_projection_id="projection-a",
                expected_desired_realized_projection_id="projection-a",
                expected_desired_graph_revision=1,
                actor_id="operator-a",
                actor_scopes=self.scopes(),
                worker_authority=self.worker(),
                lease_expires_at="2026-08-02T02:30:00Z",
            )
        )
        checkpoint = overlap.checkpoint
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.workspaces.set_current_graph(
                "workspace-a",
                "graph-a",
                checkpoint.desired_realized_projection_id,
            )
            stores.delegation_signing_keys.activate(
                "workspace-a",
                overlap.rotation.purpose,
                overlap.rotation.issuer,
                "key-b",
                activated_by="operator-a",
                activated_at="2026-08-02T03:00:01Z",
            )
            unit_of_work.commit()
        accepted = replace(
            checkpoint,
            status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
            accepted_current_graph_id="graph-a",
            accepted_current_projection_id=checkpoint.desired_realized_projection_id,
            accepted_at="2026-08-02T03:00:00Z",
        )
        rotations = GatewayKeyRotationService(self.unit_of_work, clock=lambda: 1_000)
        ready = rotations.advance(
            AdvanceGatewayKeyRotation(
                self.rotation_id,
                "accept-overlap",
                GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                overlap.rotation.version,
                GatewayKeyRotationStatus.OVERLAP_READY,
                "operator-a",
                "2026-08-02T03:00:00Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                deployment=accepted,
            )
        )
        active = rotations.advance(
            AdvanceGatewayKeyRotation(
                self.rotation_id,
                "activate-b",
                GatewayKeyRotationStatus.OVERLAP_READY,
                ready.version,
                GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
                "operator-a",
                "2026-08-02T03:00:01Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                new_key_activated_at="2026-08-02T03:00:01Z",
            )
        )
        draining = rotations.advance(
            AdvanceGatewayKeyRotation(
                self.rotation_id,
                "drain-a",
                GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
                active.version,
                GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
                "operator-a",
                "2026-08-02T03:00:02Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
            )
        )
        self.overlap_projection_id = checkpoint.desired_realized_projection_id
        self.retirement_version = draining.version
        self.drain_deadline = draining.drain_deadline_epoch
        assert self.drain_deadline == 1_065

    def command(
        self,
        *,
        expected_version: int | None = None,
        projection_id: str | None = None,
        revision: int = 2,
        scopes: tuple[PolicyScope, ...] | None = None,
    ) -> PrepareGatewayKeyRotationRetirement:
        settled = projection_id or self.overlap_projection_id
        return PrepareGatewayKeyRotationRetirement(
            rotation_id=self.rotation_id,
            expected_rotation_version=(
                self.retirement_version
                if expected_version is None
                else expected_version
            ),
            expected_authored_graph_id="graph-a",
            expected_current_realized_projection_id=settled,
            expected_desired_realized_projection_id=settled,
            expected_desired_graph_revision=revision,
            actor_id="operator-a",
            actor_scopes=scopes or self.scopes(),
            worker_authority=self.worker(),
            lease_expires_at="2026-08-02T04:30:00Z",
        )

    def program(
        self,
        *,
        epoch: int = 1_065,
        unit_of_work_factory=None,
        prefix: str = "retirement",
    ) -> GatewayKeyRotationRetirementPreparationProgram:
        return GatewayKeyRotationRetirementPreparationProgram(
            unit_of_work_factory or self.unit_of_work,
            clock=self._timestamp_clock(prefix),
            trusted_epoch_clock=lambda: epoch,
            id_factory=CountingIds(prefix),
        )

    def test_prepares_exact_b_only_child_without_changing_authored_truth(self) -> None:
        result = self.program().prepare(self.command())

        self.assertEqual(
            result.outcome,
            GatewayKeyRotationRetirementPreparationOutcome.PREPARED,
        )
        self.assertIs(
            result.rotation.status,
            GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
        )
        checkpoint = result.checkpoint
        self.assertEqual(checkpoint.base_authored_graph_id, "graph-a")
        self.assertEqual(checkpoint.desired_authored_graph_id, "graph-a")
        self.assertEqual(
            checkpoint.base_realized_projection_id,
            self.overlap_projection_id,
        )
        self.assertEqual(
            checkpoint.desired_realized_projection_id,
            f"gateway-rotation-{self.rotation_id}-retirement",
        )
        self.assertEqual(checkpoint.desired_revision, 3)
        projection = self.connection.execute(
            "SELECT graph_descriptor FROM cpk_realized_graph_projections "
            "WHERE projection_id=%s",
            (checkpoint.desired_realized_projection_id,),
        ).fetchone()[0]
        realized = DEFAULT_GRAPH_CODEC.decode(projection)
        target = realized.node("gateway-a").delegation_verifier_projection
        other = realized.node("gateway-other").delegation_verifier_projection
        self.assertEqual(tuple(key.key_id for key in target.public_keys), ("key-b",))
        self.assertEqual(tuple(key.key_id for key in other.public_keys), ("key-other",))
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_graph_versions WHERE workspace_id='workspace-a'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(self._count("cpk_observations"), 0)

        replay = self.program(prefix="replay").prepare(self.command())
        self.assertEqual(
            replay.outcome,
            GatewayKeyRotationRetirementPreparationOutcome.PREPARED_REPLAY,
        )
        self.assertEqual(replay.checkpoint, checkpoint)

    def test_premature_drain_rejects_before_child_or_desired_mutation(self) -> None:
        before = self._child_counts()
        pointer = self._desired_pointer()
        with self.assertRaises(GatewayKeyRotationRetirementPreparationConflict):
            self.program(epoch=1_064).prepare(self.command())
        self.assertEqual(self._child_counts(), before)
        self.assertEqual(self._desired_pointer(), pointer)

    def test_stale_lineage_key_truth_approval_and_scope_fail_closed(self) -> None:
        with self.assertRaises(GatewayKeyRotationRetirementPreparationConflict):
            self.program(prefix="stale-version").prepare(
                self.command(expected_version=self.retirement_version + 1)
            )
        with self.assertRaises(GatewayKeyRotationRetirementPreparationConflict):
            self.program(prefix="stale-pointer").prepare(
                self.command(projection_id="projection-forged")
            )
        with self.assertRaises(
            GatewayKeyRotationRetirementPreparationAuthorizationDenied
        ):
            self.program(prefix="missing-scope").prepare(
                self.command(scopes=(PolicyScope.DELEGATION_KEY_ROTATE,))
            )
        self.connection.execute(
            "UPDATE cpk_operation_actions SET payload=jsonb_set("
            "payload, '{review_digest}', to_jsonb(%s::text)) "
            "WHERE idempotency_key='request-rotation-approval'",
            ("f" * 64,),
        )
        with self.assertRaises(
            GatewayKeyRotationRetirementPreparationAuthorizationDenied
        ):
            self.program(prefix="approval-drift").prepare(self.command())

    def test_unexpected_verifier_key_fails_before_projection_publication(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.delegation_signing_keys.register(
                self.signing_key("key-extra", PUBLIC_KEY_OTHER)
            )
            unit_of_work.commit()
        pointer = self._desired_pointer()
        with self.assertRaises(GatewayKeyRotationRetirementPreparationConflict):
            self.program(prefix="extra-key").prepare(self.command())
        self.assertEqual(self._desired_pointer(), pointer)

    def test_restart_after_each_physical_commit_recovers_exact_child(self) -> None:
        for commit_number in range(1, 9):
            with self.subTest(commit_number=commit_number):
                self.reset_truth()
                control = CrashControl(commit_number)
                with self.assertRaises(SimulatedProcessLoss):
                    self.program(
                        unit_of_work_factory=lambda: CrashAfterCommitUnitOfWork(
                            self.unit_of_work(), control
                        ),
                        prefix=f"crash-{commit_number}",
                    ).prepare(self.command())
                recovered = self.program(prefix=f"recover-{commit_number}").prepare(
                    self.command()
                )
                self.assertIn(
                    recovered.outcome,
                    {
                        GatewayKeyRotationRetirementPreparationOutcome.PREPARED,
                        GatewayKeyRotationRetirementPreparationOutcome.PREPARED_REPLAY,
                    },
                )
                self.assertIs(
                    recovered.rotation.status,
                    GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
                )
                self.assertEqual(self._count("cpk_activity_plans"), 2)
                self.assertEqual(self._count("cpk_execution_requests"), 2)
                self.assertEqual(self._count("cpk_activity_runs"), 2)

    @staticmethod
    def scopes() -> tuple[PolicyScope, ...]:
        return (
            PolicyScope.DELEGATION_KEY_ROTATE,
            PolicyScope.PLAN_EXECUTE,
            PolicyScope.EXECUTION_OPERATE,
        )

    @staticmethod
    def worker() -> ExecutionWorkerAuthority:
        return ExecutionWorkerAuthority(
            "worker-a", (PolicyScope.EXECUTION_OPERATE,)
        )

    @staticmethod
    def _timestamp_clock(prefix: str):
        count = 0

        def clock() -> str:
            nonlocal count
            count += 1
            return f"2026-08-02T04:{count:02d}:00Z"

        return clock

    def _count(self, table: str) -> int:
        allowed = {
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
            )
        )

    def _desired_pointer(self) -> tuple[str, int]:
        return self.connection.execute(
            "SELECT desired_realized_projection_id, desired_graph_revision "
            "FROM cpk_workspaces WHERE workspace_id='workspace-a'"
        ).fetchone()


if __name__ == "__main__":
    unittest.main()
