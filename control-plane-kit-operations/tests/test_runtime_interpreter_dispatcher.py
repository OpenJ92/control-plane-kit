from __future__ import annotations

import unittest

from control_plane_kit_core.algebra import BlockSockets, BlockSpec
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
)
from control_plane_kit_core.planning import (
    ActivityId,
    ActivityPlan,
    PlannedActivity,
    NodeTarget,
    ReconcileRuntime,
    RemoveNodeResource,
    RuntimeTarget,
    SocketConnectionTarget,
    StartNode,
    StartRuntime,
    StopNode,
    SwitchSocketConnection,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DeploymentGraph, Node, RuntimeRecord
from control_plane_kit_core.types import BlockFamily, RuntimeKind
from control_plane_kit_operations.coordinator import (
    ActivityExecutionOutcome,
    ActivityRealizationContext,
    RuntimeInterpreterDispatcher,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityPlanStatus,
    ActivityRunRecord,
    AdmittedRun,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    GraphVersionRecord,
    RetryIdentity,
)


class RecordingInterpreter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.contexts: list[ActivityRealizationContext] = []

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome:
        self.contexts.append(context)
        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping({"interpreter": self.name})
        )


class RuntimeInterpreterDispatcherTests(unittest.TestCase):
    def test_start_node_dispatches_by_desired_graph_runtime_kind(self) -> None:
        docker = RecordingInterpreter("docker")
        dry_run = RecordingInterpreter("dry-run")
        dispatcher = RuntimeInterpreterDispatcher(
            {
                RuntimeKind.DOCKER: docker,
                RuntimeKind.DRY_RUN: dry_run,
            }
        )
        context = context_for(
            StartNode(NodeTarget("api")),
            base_kind=RuntimeKind.DRY_RUN,
            desired_kind=RuntimeKind.DOCKER,
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(outcome.evidence.descriptor(), {"interpreter": "docker"})
        self.assertEqual(docker.contexts, [context])
        self.assertEqual(dry_run.contexts, [])

    def test_stop_node_dispatches_by_base_graph_runtime_kind(self) -> None:
        docker = RecordingInterpreter("docker")
        dry_run = RecordingInterpreter("dry-run")
        dispatcher = RuntimeInterpreterDispatcher(
            {
                RuntimeKind.DOCKER: docker,
                RuntimeKind.DRY_RUN: dry_run,
            }
        )
        context = context_for(
            StopNode(NodeTarget("api")),
            base_kind=RuntimeKind.DOCKER,
            desired_kind=RuntimeKind.DRY_RUN,
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(outcome.evidence.descriptor(), {"interpreter": "docker"})
        self.assertEqual(docker.contexts, [context])
        self.assertEqual(dry_run.contexts, [])

    def test_runtime_operation_dispatches_from_pinned_runtime_record(self) -> None:
        dry_run = RecordingInterpreter("dry-run")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DRY_RUN: dry_run})
        context = context_for(
            ReconcileRuntime(RuntimeTarget("runtime-a")),
            base_kind=RuntimeKind.DOCKER,
            desired_kind=RuntimeKind.DRY_RUN,
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.evidence.descriptor(), {"interpreter": "dry-run"})
        self.assertEqual(dry_run.contexts, [context])

    def test_missing_interpreter_is_explicit_unsupported_without_attempt(self) -> None:
        docker = RecordingInterpreter("docker")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: docker})
        context = context_for(
            StartRuntime(RuntimeTarget("runtime-a")),
            desired_kind=RuntimeKind.AWS,
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        self.assertIsNotNone(outcome.failure)
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "runtime.interpreter-missing")
        self.assertEqual(
            outcome.failure.details.descriptor(),
            {
                "activity_id": "activity-a",
                "operation": "StartRuntime",
                "runtime_kind": "aws",
            },
        )
        self.assertEqual(docker.contexts, [])

    def test_operation_without_runtime_target_is_explicit_unsupported(self) -> None:
        docker = RecordingInterpreter("docker")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: docker})
        context = context_for(
            SwitchSocketConnection(SocketConnectionTarget("edge-a")),
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        self.assertIsNotNone(outcome.failure)
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "runtime.dispatch-target-unsupported")
        self.assertEqual(
            outcome.failure.details.descriptor(),
            {
                "activity_id": "activity-a",
                "operation": "SwitchSocketConnection",
            },
        )
        self.assertEqual(docker.contexts, [])

    def test_missing_base_node_is_explicit_unsupported_without_desired_lookup(self) -> None:
        docker = RecordingInterpreter("docker")
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: docker})
        context = context_for(
            RemoveNodeResource(NodeTarget("api")),
            base_graph=graph_without_node(RuntimeKind.DOCKER),
            desired_kind=RuntimeKind.DOCKER,
        )

        outcome = dispatcher.execute(context)

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        self.assertIsNotNone(outcome.failure)
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "runtime.dispatch-target-unsupported")
        self.assertIn("missing node", outcome.failure.message)
        self.assertEqual(docker.contexts, [])


def context_for(
    operation,
    *,
    base_kind: RuntimeKind = RuntimeKind.DOCKER,
    desired_kind: RuntimeKind = RuntimeKind.DOCKER,
    base_graph: DeploymentGraph | None = None,
) -> ActivityRealizationContext:
    activity = PlannedActivity(ActivityId("activity-a"), operation)
    plan = ActivityPlan((activity,))
    return ActivityRealizationContext(
        activity=activity,
        request=ExecutionRequestRecord(
            ExecutionRequestIdentity("request-a", "workspace-a", "session-a", "plan-a"),
            ExecutionRequestStatus.CLAIMED,
            "operator-a",
            "2026-07-22T10:00:00Z",
            "approval-request-a",
            "approval-decision-a",
            ExecutionIdempotency("execute-a", "fingerprint-a"),
            ClaimIdentity("worker-a", "2026-07-22T10:01:00Z", "2026-07-22T10:30:00Z"),
        ),
        run=ActivityRunRecord(
            "run-a",
            "plan-a",
            AdmittedRun("request-a"),
            RetryIdentity(1),
            ActivityRunStatus.RUNNING,
            "2026-07-22T10:01:00Z",
            started_at="2026-07-22T10:02:00Z",
        ),
        plan_record=ActivityPlanRecord(
            "plan-a",
            "session-a",
            "graph-current",
            "graph-desired",
            ActivityPlanStatus.PLANNED,
            "2026-07-22T10:00:30Z",
            plan,
        ),
        base_graph=graph_version_record_from_graph(
            "graph-current",
            base_graph if base_graph is not None else graph_with_node(base_kind),
        ),
        desired_graph=graph_version_record_from_graph(
            "graph-desired",
            graph_with_node(desired_kind),
            version=2,
        ),
        registered_products=(),
        authority=ExecutionWorkerAuthority(
            "worker-a",
            (PolicyScope.EXECUTION_OPERATE,),
        ),
        intent_event=ActivityEventRecord(
            "event-intent",
            "run-a",
            1,
            ActivityEventKind.STEP_STARTED,
            "2026-07-22T10:02:30Z",
            activity_id="activity-a",
        ),
    )


def graph_version_record_from_graph(
    graph_id: str,
    graph: DeploymentGraph,
    *,
    version: int = 1,
) -> GraphVersionRecord:
    return GraphVersionRecord.from_graph(
        graph_id=graph_id,
        workspace_id="workspace-a",
        version=version,
        graph=graph,
        created_by="operator-a",
        created_at="2026-07-22T10:00:00Z",
    )


def graph_with_node(kind: RuntimeKind) -> DeploymentGraph:
    return DeploymentGraph(
        "graph",
        nodes={
            "api": Node(
                "api",
                BlockFamily.APPLICATION,
                BlockSpec("api"),
                "container",
                "runtime-a",
                BlockSockets(),
            )
        },
        runtimes={
            "runtime-a": RuntimeRecord(
                "runtime-a",
                kind,
                children=("api",),
            )
        },
    )


def graph_without_node(kind: RuntimeKind) -> DeploymentGraph:
    return DeploymentGraph(
        "graph",
        runtimes={
            "runtime-a": RuntimeRecord(
                "runtime-a",
                kind,
            )
        },
    )


if __name__ == "__main__":
    unittest.main()
