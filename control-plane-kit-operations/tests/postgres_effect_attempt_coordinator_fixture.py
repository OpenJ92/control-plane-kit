from __future__ import annotations

from dataclasses import dataclass, replace
import threading

from control_plane_kit_core.operations import EffectRecoveryResolution
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    RuntimeRecord,
)
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.coordinator import (
    ExecuteActivityRun,
    ExecutionCoordinator,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import (
    EffectAttemptFoldService,
)
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import (
    EffectAttemptReconciliationService,
)
from control_plane_kit_operations.effect_attempt_start_interpreter import (
    EffectAttemptStartService,
)
from control_plane_kit_operations.lifecycle import RunLifecycleCommandService
from control_plane_kit_operations.records import RealizedGraphProjectionRecord
from control_plane_kit_operations.workflows import IdempotencyKey
from psycopg.types.json import Jsonb
from tests.postgres_effect_attempt_reconciliation_fixture import (
    FailIfObserver,
    PostgresEffectAttemptReconciliationFixture,
)


class GeneratedIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.calls: list[str] = []

    def __call__(self) -> str:
        value = f"{self.prefix}-{len(self.calls) + 1}"
        self.calls.append(value)
        return value


class RecordingRuntimeAdapter:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.legacy_calls: list[object] = []
        self.runtime_calls: list[tuple[object, object]] = []

    def execute(self, context):
        self.legacy_calls.append(context)
        return self.results.pop(0)

    def execute_runtime(self, context, request):
        self.runtime_calls.append((context, request))
        value = (
            self.results.pop(0)
            if self.results
            else RuntimeEffectResult.succeeded(request.effect_id)
        )
        if type(value) in (TypeError, RuntimeError):
            raise value
        if callable(value):
            return value(context, request)
        return value


class RecordingService:
    def __init__(self, inner, *, before_execute=None) -> None:
        self.inner = inner
        self.before_execute = before_execute
        self.commands: list[object] = []

    def execute(self, command):
        self.commands.append(command)
        if self.before_execute is not None:
            self.before_execute()
        return self.inner.execute(command)

    def execute_observed(self, command):
        self.commands.append(command)
        if self.before_execute is not None:
            self.before_execute()
        return self.inner.execute_observed(command)


class TimeoutRendezvous:
    """Release exactly the expected callers or fail boundedly."""

    def __init__(self, parties: int, *, timeout: float = 5.0) -> None:
        self._barrier = threading.Barrier(parties, timeout=timeout)
        self._lock = threading.Lock()
        self.calls = 0

    def __call__(self) -> None:
        with self._lock:
            self.calls += 1
        self._barrier.wait()


@dataclass
class CoordinatorHarness:
    coordinator: ExecutionCoordinator
    adapter: RecordingRuntimeAdapter
    lifecycle: RecordingService
    start: RecordingService
    fold: RecordingService
    reconciliation: RecordingService
    lifecycle_ids: GeneratedIds
    start_ids: GeneratedIds
    fold_ids: GeneratedIds
    coordinator_ids: GeneratedIds


class PostgresEffectAttemptCoordinatorFixture(
    PostgresEffectAttemptReconciliationFixture,
):
    """Accepted service composition for the #1695 PostgreSQL coordinator laws."""

    def reset_start_truth(self, *, compensation: bool = False) -> None:
        super().reset_start_truth(compensation=compensation)
        for graph_id in ("graph-current", "graph-desired"):
            graph = DeploymentGraph(
                graph_id,
                runtimes={
                    "runtime-a": RuntimeRecord(
                        "runtime-a",
                        RuntimeKind.DOCKER,
                    )
                },
            )
            with self.unit_of_work() as unit_of_work:
                authored = unit_of_work.stores.graphs.get(graph_id)
            authored = replace(
                authored,
                graph_descriptor=DEFAULT_GRAPH_CODEC.encode(graph),
            )
            projection = RealizedGraphProjectionRecord.identity_for_authored(
                authored_record=authored,
            )
            self.connection.execute(
                "UPDATE cpk_graph_versions SET graph_descriptor=%s "
                "WHERE graph_id=%s",
                (Jsonb(authored.graph_descriptor), graph_id),
            )
            self.connection.execute(
                "UPDATE cpk_realized_graph_projections "
                "SET projection_digest=%s, graph_descriptor=%s "
                "WHERE source_authored_graph_id=%s",
                (
                    projection.projection_digest,
                    Jsonb(projection.graph_descriptor),
                    graph_id,
                ),
            )

    def coordinator_command(
        self,
        *,
        max_effects: int = 1,
        worker_id: str = "worker-a",
        generation: int = 7,
        scopes: tuple[PolicyScope, ...] = (
            PolicyScope.EXECUTION_OPERATE,
            PolicyScope.SECRET_PROVIDER_USE,
        ),
    ) -> ExecuteActivityRun:
        return ExecuteActivityRun(
            "run-a",
            self.authority(worker_id, scopes),
            self.fence(worker_id, generation),
            IdempotencyKey("coordinator-a"),
            max_effects,
        )

    def coordinator_harness(
        self,
        *,
        adapter: RecordingRuntimeAdapter | None = None,
        observer=None,
    ) -> CoordinatorHarness:
        adapter = adapter or RecordingRuntimeAdapter()
        lifecycle_ids = GeneratedIds("coordinator-lifecycle")
        start_ids = GeneratedIds("coordinator-start")
        fold_ids = GeneratedIds("coordinator-fold")
        coordinator_ids = GeneratedIds("coordinator-direct")
        lifecycle = RecordingService(
            RunLifecycleCommandService(
                self.unit_of_work,
                clock=lambda: "2030-01-01T00:00:20Z",
                id_factory=lifecycle_ids,
            )
        )
        start = RecordingService(
            EffectAttemptStartService(
                self.unit_of_work,
                id_factory=start_ids,
            )
        )
        fold = RecordingService(
            EffectAttemptFoldService(
                self.unit_of_work,
                id_factory=fold_ids,
            )
        )
        reconciliation = RecordingService(
            EffectAttemptReconciliationService(
                self.unit_of_work,
                observer or FailIfObserver("coordinator invoked observer unexpectedly"),
                fold,
            )
        )
        coordinator = ExecutionCoordinator(
            self.unit_of_work,
            lifecycle=lifecycle,
            adapter=adapter,
            start_service=start,
            fold_service=fold,
            reconciliation_service=reconciliation,
            clock=lambda: "2030-01-01T00:00:21Z",
            id_factory=coordinator_ids,
        )
        return CoordinatorHarness(
            coordinator,
            adapter,
            lifecycle,
            start,
            fold,
            reconciliation,
            lifecycle_ids,
            start_ids,
            fold_ids,
            coordinator_ids,
        )

    def seed_running_reconciliation(self, story=None):
        story = story or self.observed_story()
        current, intent, record, authority = self.seed_reconciliation_source(
            story,
            zero_use=True,
        )
        observer = self.observer_for(story, current, intent)
        return current, intent, record, authority, observer

    def persist_recovery_resolution(
        self,
        resolution: EffectRecoveryResolution,
    ):
        story = (
            "recovered-succeeded"
            if resolution is EffectRecoveryResolution.SUCCEEDED
            else "recovered-failed"
        )
        self.seed_fold_source(story)
        return self.fold_service(f"coordinator-{story}").execute(
            self.fold_command(story)
        ).attempt

    def coordinator_snapshot(self):
        return (
            tuple(
                self.connection.execute(
                    "SELECT request_id, status, claim_worker_id, claim_generation "
                    "FROM cpk_execution_requests ORDER BY request_id"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT run_id, status, settled_at FROM cpk_activity_runs "
                    "ORDER BY run_id"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT run_id, ordinal, event_type, "
                    "payload->>'activity_id' "
                    "FROM cpk_activity_events ORDER BY run_id, ordinal"
                ).fetchall()
            ),
            self.attempt_only_snapshot(),
            tuple(
                self.connection.execute(
                    "SELECT run_id, activity_id, attempt, direct_event_id "
                    "FROM cpk_effect_attempt_outcomes "
                    "ORDER BY run_id, activity_id, attempt"
                ).fetchall()
            ),
        )

    def graph_request_snapshot(self):
        return (
            tuple(
                self.connection.execute(
                    "SELECT workspace_id, current_graph_id, desired_graph_id, "
                    "desired_graph_revision FROM cpk_workspaces ORDER BY workspace_id"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT request_id, workspace_id, plan_id, claim_worker_id, "
                    "claim_generation FROM cpk_execution_requests ORDER BY request_id"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT plan_id, status, base_graph_id, desired_graph_id "
                    "FROM cpk_activity_plans ORDER BY plan_id"
                ).fetchall()
            ),
        )

    def run_status(self):
        return ActivityRunStatus(
            self.connection.execute(
                "SELECT status FROM cpk_activity_runs WHERE run_id='run-a'"
            ).fetchone()[0]
        )


__all__ = [
    "CoordinatorHarness",
    "GeneratedIds",
    "PostgresEffectAttemptCoordinatorFixture",
    "RecordingRuntimeAdapter",
    "RecordingService",
    "TimeoutRendezvous",
]
