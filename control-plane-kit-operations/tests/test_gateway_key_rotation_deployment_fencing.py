from __future__ import annotations

from dataclasses import replace
import os
import unittest

import psycopg

import control_plane_kit_operations as operations_root
import control_plane_kit_operations.gateway_key_rotations as rotations
from gateway_rotation_overlap_fixture import GatewayRotationOverlapFixture
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.gateway_key_rotation_overlap_program import (
    GatewayKeyRotationOverlapPreparationProgram,
    PrepareGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotationDeployment,
    AdvanceGatewayKeyRotation,
    GatewayKeyRotationAuthorizationDenied,
    GatewayKeyRotationConflict,
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationDeploymentHandoff,
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


class RecordingConnection:
    def __init__(self, inner, *, fail_transition: bool = False) -> None:
        self.inner = inner
        self.fail_transition = fail_transition
        self.locked_tables: list[str] = []

    def execute(self, query, parameters=None):
        normalized = " ".join(str(query).split()).lower()
        if " for update" in normalized:
            for table in (
                "cpk_execution_requests",
                "cpk_activity_runs",
                "cpk_gateway_key_rotations",
            ):
                if f"from {table}" in normalized:
                    self.locked_tables.append(table)
        if (
            self.fail_transition
            and "insert into cpk_gateway_key_rotation_transitions" in normalized
        ):
            raise RuntimeError("late transition persistence failed")
        return self.inner.execute(query, parameters)

    def commit(self) -> None:
        self.inner.commit()

    def rollback(self) -> None:
        self.inner.rollback()

    def close(self) -> None:
        self.inner.close()


class GatewayKeyRotationDeploymentFencingTests(
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
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_graph_and_keys()
        self.seed_rotation_approval()
        self.prepared = self._prepare()

    def tearDown(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def _prepare(self):
        timestamps = iter(
            f"2026-08-02T02:{minute:02d}:00Z" for minute in range(30)
        )
        program = GatewayKeyRotationOverlapPreparationProgram(
            self.unit_of_work,
            clock=lambda: next(timestamps),
            trusted_epoch_clock=lambda: 2_000,
            id_factory=CountingIds("deployment-fence"),
        )
        return program.prepare(
            PrepareGatewayKeyRotationOverlap(
                rotation_id=self.rotation_id,
                expected_rotation_version=self.rotation_version,
                expected_authored_graph_id="graph-a",
                expected_current_realized_projection_id="projection-a",
                expected_desired_realized_projection_id="projection-a",
                expected_desired_graph_revision=1,
                actor_id="operator-a",
                actor_scopes=(
                    PolicyScope.DELEGATION_KEY_ROTATE,
                    PolicyScope.PLAN_EXECUTE,
                    PolicyScope.EXECUTION_OPERATE,
                ),
                worker_authority=self.worker_authority(),
                lease_duration=ExecutionLeaseDuration(1800),
            )
        )

    @staticmethod
    def worker_authority(
        worker_id: str = "worker-a",
    ) -> ExecutionWorkerAuthority:
        return ExecutionWorkerAuthority(
            worker_id,
            (PolicyScope.EXECUTION_OPERATE,),
        )

    def read_handoff(
        self,
        *,
        authority: ExecutionWorkerAuthority | None = None,
    ):
        command_type = self._required_type(
            "ReadGatewayKeyRotationDeploymentHandoff"
        )
        return self.service().deployment_handoff(
            command_type(
                rotation_id=self.rotation_id,
                phase=GatewayKeyRotationDeploymentPhase.OVERLAP,
                worker_authority=authority or self.worker_authority(),
            )
        )

    def service(self, factory=None) -> GatewayKeyRotationService:
        return GatewayKeyRotationService(
            factory or self.unit_of_work,
            clock=lambda: 3_000,
        )

    def blocked_command(self, handoff=None, *, failure_code="overlap-effect-failed"):
        command_type = self._required_type("AdvanceGatewayKeyRotationDeployment")
        return command_type(
            transition=AdvanceGatewayKeyRotation(
                rotation_id=self.rotation_id,
                transition_id="deployment-overlap-blocked",
                expected_status=GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                expected_version=self.prepared.rotation.version,
                target_status=GatewayKeyRotationStatus.BLOCKED,
                advanced_by="operator-a",
                advanced_at="2026-08-02T03:00:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                failure_code=failure_code,
            ),
            handoff=handoff or self.prepared.handoff,
        )

    def first_prepared_command(self, handoff):
        rotation = self.service().get(self.rotation_id)
        return AdvanceGatewayKeyRotationDeployment(
            transition=AdvanceGatewayKeyRotation(
                rotation_id=self.rotation_id,
                transition_id="deployment-overlap-prepared-amendment",
                expected_status=GatewayKeyRotationStatus.KEY_GENERATED,
                expected_version=rotation.version,
                target_status=GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                advanced_by="operator-a",
                advanced_at=handoff.checkpoint.prepared_at,
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                deployment=handoff.checkpoint,
            ),
            handoff=handoff,
        )

    def rewind_to_key_generated(self):
        self.connection.execute(
            "DELETE FROM cpk_gateway_key_rotation_transitions "
            "WHERE rotation_id=%s AND to_status='overlap-deploying'",
            (self.rotation_id,),
        )
        self.connection.execute(
            "DELETE FROM cpk_gateway_key_rotation_deployments "
            "WHERE rotation_id=%s AND phase='overlap'",
            (self.rotation_id,),
        )
        self.connection.execute(
            "UPDATE cpk_gateway_key_rotations "
            "SET status='key-generated', version=version-1 "
            "WHERE rotation_id=%s",
            (self.rotation_id,),
        )
        return self.service().get(self.rotation_id)

    def foreign_approval(self) -> tuple[str, str]:
        checkpoint = self.prepared.checkpoint
        request_id = "foreign-plan-approval-request"
        decision_id = "foreign-plan-approval-decision"
        self.connection.execute(
            """
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, rotation_id, subject_kind,
               subject_payload, review_digest, requested_by, requested_at,
               required_scope, max_risk, destructive, comment,
               idempotency_key, intent_fingerprint)
            SELECT %s, session_id, %s, NULL, 'activity-plan',
                   jsonb_build_object(
                       'kind', 'activity-plan', 'plan_id', %s::text
                   ),
                   review_digest, requested_by, requested_at,
                   required_scope, max_risk, destructive, NULL, NULL, NULL
            FROM cpk_approval_requests
            WHERE request_id=%s
            """,
            (
                request_id,
                checkpoint.plan_id,
                checkpoint.plan_id,
                checkpoint.approval_request_id,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_approval_decisions
              (decision_id, request_id, actor_id, decision, scope, decided_at,
               comment, idempotency_key, intent_fingerprint)
            SELECT %s, %s, actor_id, decision, scope, decided_at,
                   NULL, NULL, NULL
            FROM cpk_approval_decisions
            WHERE decision_id=%s
            """,
            (decision_id, request_id, checkpoint.approval_decision_id),
        )
        return request_id, decision_id

    @staticmethod
    def _required_type(name: str):
        value = getattr(rotations, name, None)
        if value is None:
            raise AssertionError(f"{name} is missing")
        return value

    def test_public_language_is_nominal_bounded_and_checkpoint_is_not_authority(
        self,
    ) -> None:
        for name in (
            "GatewayKeyRotationDeploymentHandoff",
            "ReadGatewayKeyRotationDeploymentHandoff",
            "AdvanceGatewayKeyRotationDeployment",
        ):
            value = self._required_type(name)
            self.assertIs(getattr(operations_root, name, None), value)

        handoff = self.prepared.handoff
        self.assertEqual(handoff.rotation_id, self.rotation_id)
        self.assertEqual(handoff.checkpoint, self.prepared.checkpoint)
        self.assertEqual(handoff.fence, ExecutionLeaseFence("worker-a", 1))
        self.assertFalse(hasattr(handoff.checkpoint, "fence"))
        self.assertFalse(hasattr(handoff.checkpoint, "claim_generation"))
        self.assertNotIn("worker-a", repr(handoff))
        self.assertNotIn("generation", repr(handoff))

        class HostileFence(ExecutionLeaseFence):
            pass

        with self.assertRaises(GatewayKeyRotationConflict) as captured:
            self._required_type("GatewayKeyRotationDeploymentHandoff")(
                self.rotation_id,
                self.prepared.checkpoint,
                HostileFence("worker-a", 1),
            )
        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)

    def test_restart_handoff_requires_authenticated_current_worker(self) -> None:
        self.assertEqual(self.read_handoff(), self.prepared.handoff)

        with self.assertRaises(GatewayKeyRotationAuthorizationDenied) as captured:
            self.read_handoff(authority=self.worker_authority("foreign-worker-canary"))
        self.assertEqual(
            str(captured.exception),
            "gateway deployment worker is not current",
        )
        self.assertNotIn("foreign-worker-canary", str(captured.exception))
        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)

        self.connection.execute(
            "UPDATE cpk_execution_requests SET claim_generation=2 "
            "WHERE request_id=%s",
            (self.prepared.checkpoint.execution_request_id,),
        )
        restarted = self.read_handoff()
        self.assertEqual(restarted.fence, ExecutionLeaseFence("worker-a", 2))
        self.assertEqual(restarted.checkpoint, self.prepared.checkpoint)

    def test_generic_writer_rejects_all_deployment_owned_transition_shapes(
        self,
    ) -> None:
        prepared_overlap = self.prepared.checkpoint
        accepted_overlap = replace(
            prepared_overlap,
            status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
            accepted_current_graph_id=prepared_overlap.desired_authored_graph_id,
            accepted_current_projection_id=(
                prepared_overlap.desired_realized_projection_id
            ),
            accepted_at="2026-08-02T03:00:00Z",
        )
        prepared_retirement = replace(
            prepared_overlap,
            phase=GatewayKeyRotationDeploymentPhase.RETIREMENT,
        )
        accepted_retirement = replace(
            prepared_retirement,
            status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
            accepted_current_graph_id=prepared_retirement.desired_authored_graph_id,
            accepted_current_projection_id=(
                prepared_retirement.desired_realized_projection_id
            ),
            accepted_at="2026-08-02T03:00:00Z",
        )
        cases = (
            (
                GatewayKeyRotationStatus.KEY_GENERATED,
                GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                prepared_overlap,
            ),
            (
                GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                GatewayKeyRotationStatus.OVERLAP_READY,
                accepted_overlap,
            ),
            (
                GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                GatewayKeyRotationStatus.BLOCKED,
                None,
            ),
            (
                GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
                GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
                prepared_retirement,
            ),
            (
                GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
                GatewayKeyRotationStatus.RETIREMENT_READY,
                accepted_retirement,
            ),
            (
                GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
                GatewayKeyRotationStatus.BLOCKED,
                None,
            ),
        )
        before = self.service().get(self.rotation_id)
        transitions = self.service().transitions(self.rotation_id)
        for index, (source, target, deployment) in enumerate(cases):
            with self.subTest(source=source, target=target):
                command = AdvanceGatewayKeyRotation(
                    rotation_id=self.rotation_id,
                    transition_id=f"generic-deployment-bypass-{index}",
                    expected_status=source,
                    expected_version=before.version,
                    target_status=target,
                    advanced_by="operator-a",
                    advanced_at="2026-08-02T03:00:00Z",
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                    deployment=deployment,
                    failure_code=(
                        "deployment-effect-failed"
                        if target is GatewayKeyRotationStatus.BLOCKED
                        else None
                    ),
                )
                with self.assertRaises(GatewayKeyRotationConflict) as captured:
                    self.service().advance(command)
                self.assertEqual(
                    str(captured.exception),
                    "deployment-owned rotation transition requires fenced writer",
                )
                self.assertIsNone(captured.exception.__cause__)
                self.assertIsNone(captured.exception.__context__)
                self.assertEqual(self.service().get(self.rotation_id), before)
                self.assertEqual(
                    self.service().transitions(self.rotation_id),
                    transitions,
                )

    def test_first_prepared_write_requires_rotation_approval_congruence(
        self,
    ) -> None:
        before = self.rewind_to_key_generated()
        transition_count = len(self.service().transitions(self.rotation_id))
        request_id, decision_id = self.foreign_approval()
        self.connection.execute(
            "UPDATE cpk_execution_requests "
            "SET approval_request_id=%s, approval_decision_id=%s "
            "WHERE request_id=%s",
            (
                request_id,
                decision_id,
                self.prepared.checkpoint.execution_request_id,
            ),
        )
        checkpoint = replace(
            self.prepared.checkpoint,
            approval_request_id=request_id,
            approval_decision_id=decision_id,
        )
        handoff = GatewayKeyRotationDeploymentHandoff(
            self.rotation_id,
            checkpoint,
            self.prepared.handoff.fence,
        )

        with self.assertRaises(GatewayKeyRotationConflict) as captured:
            self.service().advance_deployment(self.first_prepared_command(handoff))

        self.assertEqual(
            str(captured.exception),
            "gateway deployment approval does not match rotation truth",
        )
        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        self.assertEqual(self.service().get(self.rotation_id), before)
        self.assertEqual(
            len(self.service().transitions(self.rotation_id)),
            transition_count,
        )

    def test_first_prepared_write_requires_immutable_plan_congruence(self) -> None:
        before = self.rewind_to_key_generated()
        transition_count = len(self.service().transitions(self.rotation_id))
        checkpoint = replace(
            self.prepared.checkpoint,
            desired_revision=self.prepared.checkpoint.desired_revision + 1,
        )
        handoff = GatewayKeyRotationDeploymentHandoff(
            self.rotation_id,
            checkpoint,
            self.prepared.handoff.fence,
        )

        with self.assertRaises(GatewayKeyRotationConflict) as captured:
            self.service().advance_deployment(self.first_prepared_command(handoff))

        self.assertEqual(
            str(captured.exception),
            "gateway deployment plan linkage changed",
        )
        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        self.assertEqual(self.service().get(self.rotation_id), before)
        self.assertEqual(
            len(self.service().transitions(self.rotation_id)),
            transition_count,
        )

    def test_deployment_transition_locks_request_run_rotation_and_replays_semantics(
        self,
    ) -> None:
        connections: list[RecordingConnection] = []

        def factory():
            connection = RecordingConnection(psycopg.connect(self.database_url))
            connections.append(connection)
            return PostgresUnitOfWork(lambda: connection)

        command = self.blocked_command()
        blocked = self.service(factory).advance_deployment(command)
        self.assertIs(blocked.status, GatewayKeyRotationStatus.BLOCKED)
        self.assertEqual(
            connections[-1].locked_tables,
            [
                "cpk_execution_requests",
                "cpk_activity_runs",
                "cpk_gateway_key_rotations",
            ],
        )

        self.connection.execute(
            "UPDATE cpk_execution_requests SET claim_generation=2 "
            "WHERE request_id=%s",
            (self.prepared.checkpoint.execution_request_id,),
        )
        current_handoff = self.read_handoff()
        with self.assertRaises(GatewayKeyRotationAuthorizationDenied):
            self.service().advance_deployment(command)

        replay = self.service().advance_deployment(
            replace(command, handoff=current_handoff)
        )
        self.assertEqual(replay, blocked)
        self.assertEqual(
            len(
                tuple(
                    value
                    for value in self.service().transitions(self.rotation_id)
                    if value.transition_id == "deployment-overlap-blocked"
                )
            ),
            1,
        )
        with self.assertRaises(GatewayKeyRotationConflict):
            self.service().advance_deployment(
                self.blocked_command(
                    current_handoff,
                    failure_code="overlap-effect-uncertain",
                )
            )

    def test_stale_fence_rejects_before_rotation_mutation_with_bounded_error(
        self,
    ) -> None:
        before = len(self.service().transitions(self.rotation_id))
        self.connection.execute(
            "UPDATE cpk_execution_requests SET claim_generation=2 "
            "WHERE request_id=%s",
            (self.prepared.checkpoint.execution_request_id,),
        )
        command = self.blocked_command()

        with self.assertRaises(GatewayKeyRotationAuthorizationDenied) as captured:
            self.service().advance_deployment(command)

        self.assertEqual(
            str(captured.exception),
            "gateway deployment execution authority is stale",
        )
        self.assertNotIn("worker-a", str(captured.exception))
        self.assertNotIn(self.prepared.checkpoint.run_id, str(captured.exception))
        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        self.assertIs(
            self.service().get(self.rotation_id).status,
            GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
        )
        self.assertEqual(len(self.service().transitions(self.rotation_id)), before)

    def test_late_transition_failure_rolls_back_rotation_checkpoint_and_evidence(
        self,
    ) -> None:
        before = self.service().get(self.rotation_id)
        transition_count = len(self.service().transitions(self.rotation_id))

        def factory():
            connection = RecordingConnection(
                psycopg.connect(self.database_url),
                fail_transition=True,
            )
            return PostgresUnitOfWork(lambda: connection)

        command = self.blocked_command()
        with self.assertRaisesRegex(
            RuntimeError,
            "late transition persistence failed",
        ):
            self.service(factory).advance_deployment(command)

        self.assertEqual(self.service().get(self.rotation_id), before)
        self.assertEqual(
            len(self.service().transitions(self.rotation_id)),
            transition_count,
        )


if __name__ == "__main__":
    unittest.main()
