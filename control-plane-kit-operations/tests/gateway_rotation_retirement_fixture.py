from __future__ import annotations

from dataclasses import replace
import os

import psycopg

from gateway_rotation_overlap_fixture import GatewayRotationOverlapFixture
from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.coordinator import (
    ActivityExecutionOutcome,
    ActivityRealizationContext,
    ExecutionCoordinator,
)
from control_plane_kit_operations.gateway_key_rotation_overlap_program import (
    GatewayKeyRotationOverlapPreparationProgram,
    PrepareGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotation_retirement_execution import (
    GatewayKeyRotationRetirementExecutionProgram,
    ProgressGatewayKeyRotationRetirement,
)
from control_plane_kit_operations.gateway_key_rotation_retirement_program import (
    GatewayKeyRotationRetirementPreparationProgram,
    PrepareGatewayKeyRotationRetirement,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    GatewayKeyRotationDeploymentStatus,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.lifecycle import (
    ExecutionLeaseDuration,
    ExecutionWorkerAuthority,
    RunLifecycleCommandService,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema


class CountingIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"{self.prefix}-{self.count}"


class RecordingAdapter:
    def __init__(
        self,
        *outcomes: ActivityExecutionOutcome | BaseException,
    ) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome:
        self.calls.append(context.activity.activity_id.value)
        if not self.outcomes:
            raise AssertionError("unexpected duplicate runtime effect")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class GatewayRotationRetirementFixture(GatewayRotationOverlapFixture):
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
                lease_duration=ExecutionLeaseDuration(1800),
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
            lease_duration=ExecutionLeaseDuration(1800),
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

    def prepare_retirement_execution(self) -> None:
        prepared = self.program(prefix="prepare-execution").prepare(self.command())
        self.retirement_prepared_version = prepared.rotation.version
        self.retirement_checkpoint = prepared.checkpoint

    def execution_command(
        self,
        *,
        expected_version: int | None = None,
        actor_scopes: tuple[PolicyScope, ...] = (
            PolicyScope.DELEGATION_KEY_ROTATE,
        ),
        worker_scopes: tuple[PolicyScope, ...] = (
            PolicyScope.EXECUTION_OPERATE,
        ),
    ) -> ProgressGatewayKeyRotationRetirement:
        return ProgressGatewayKeyRotationRetirement(
            rotation_id=self.rotation_id,
            expected_prepared_rotation_version=(
                self.retirement_prepared_version
                if expected_version is None
                else expected_version
            ),
            actor_id="operator-a",
            actor_scopes=actor_scopes,
            worker_authority=ExecutionWorkerAuthority("worker-a", worker_scopes),
            fence=ExecutionLeaseFence("worker-a", 1),
        )

    def execution_program(
        self,
        adapter: RecordingAdapter,
        *,
        unit_of_work_factory=None,
        prefix: str = "execute-retirement",
    ) -> GatewayKeyRotationRetirementExecutionProgram:
        factory = unit_of_work_factory or self.unit_of_work
        ids = CountingIds(prefix)
        clock = lambda: "2026-08-02T05:00:00Z"
        lifecycle = RunLifecycleCommandService(factory, clock=clock, id_factory=ids)
        coordinator = ExecutionCoordinator(
            factory,
            lifecycle=lifecycle,
            adapter=adapter,
            clock=clock,
            id_factory=ids,
        )
        return GatewayKeyRotationRetirementExecutionProgram(
            factory,
            coordinator=coordinator,
            clock=clock,
            trusted_epoch_clock=lambda: 5_000,
            id_factory=ids,
        )

    def workspace(self):
        with self.unit_of_work() as unit_of_work:
            return unit_of_work.stores.workspaces.get("workspace-a")

    def old_key(self):
        rotation = self.rotation()
        with self.unit_of_work() as unit_of_work:
            return unit_of_work.stores.delegation_signing_keys.get(
                "workspace-a",
                rotation.purpose,
                rotation.issuer,
                "key-a",
            )

    def rotation(self):
        return GatewayKeyRotationService(
            self.unit_of_work,
            clock=lambda: 5_000,
        ).get(self.rotation_id)

    def retirement_activity_count(self) -> int:
        with self.unit_of_work() as unit_of_work:
            plan = unit_of_work.stores.activity_history.get_plan(
                self.retirement_checkpoint.plan_id
            )
        return len(plan.plan.activities)

    def retirement_event_kinds(self) -> list[ActivityEventKind]:
        with self.unit_of_work() as unit_of_work:
            events = unit_of_work.stores.execution.events_for_run(
                self.retirement_checkpoint.run_id
            )
        return [event.kind for event in events]

    def retirement_advancement_count(self) -> int:
        return self.retirement_event_kinds().count(
            ActivityEventKind.CURRENT_GRAPH_ADVANCED
        )

    def authored_graph_count(self) -> int:
        return self.connection.execute(
            "SELECT count(*) FROM cpk_graph_versions"
        ).fetchone()[0]

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
            "worker-a",
            (PolicyScope.EXECUTION_OPERATE,),
        )

    @staticmethod
    def _timestamp_clock(prefix: str):
        count = 0

        def clock() -> str:
            nonlocal count
            count += 1
            return f"2026-08-02T04:{count:02d}:00Z"

        return clock

    def count(self, table: str) -> int:
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

    def child_counts(self) -> tuple[int, ...]:
        return tuple(
            self.count(table)
            for table in (
                "cpk_operation_sessions",
                "cpk_activity_plans",
                "cpk_execution_requests",
                "cpk_activity_runs",
            )
        )

    def desired_pointer(self) -> tuple[str, int]:
        return self.connection.execute(
            "SELECT desired_realized_projection_id, desired_graph_revision "
            "FROM cpk_workspaces WHERE workspace_id='workspace-a'"
        ).fetchone()
