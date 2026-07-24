from __future__ import annotations

import unittest

from control_plane_kit_core.algebra import BlockSockets, BlockSpec, ProviderSocket
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
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductReference,
    ProductRuntimeContract,
    ProviderRuntimePort,
)
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
    RuntimeEndpointObservation,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectFailure,
    RuntimeEffectRequest,
    RuntimeEffectResult,
)
from control_plane_kit_core.topology import DeploymentGraph, Node, RuntimeRecord
from control_plane_kit_core.types import BlockFamily, Protocol, RuntimeKind
from control_plane_kit_operations.coordinator import (
    ActivityRealizationContext,
    RuntimeInterpreterDispatcher,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.products import InlineDescriptorSource, RegisteredProduct
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityPlanStatus,
    ActivityRunRecord,
    AdmittedRun,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    GraphVersionRecord,
    RetryIdentity,
)


class RecordingInterpreter:
    def __init__(self, name: str, result: RuntimeEffectResult | None = None) -> None:
        self.name = name
        self.result = result
        self.requests: list[RuntimeEffectRequest] = []

    def execute(
        self,
        request: RuntimeEffectRequest,
    ) -> RuntimeEffectResult:
        self.requests.append(request)
        return self.result or RuntimeEffectResult.succeeded(
            request.effect_id,
            evidence={"interpreter": self.name},
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
        self.assertEqual(len(docker.requests), 1)
        self.assertEqual(docker.requests[0].runtime_kind, RuntimeKind.DOCKER)
        self.assertEqual(docker.requests[0].activity_id, ActivityId("activity-a"))
        self.assertEqual(dry_run.requests, [])

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
        self.assertEqual(len(docker.requests), 1)
        self.assertEqual(docker.requests[0].runtime_kind, RuntimeKind.DOCKER)
        self.assertEqual(dry_run.requests, [])

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
        self.assertEqual(len(dry_run.requests), 1)
        self.assertEqual(dry_run.requests[0].runtime_kind, RuntimeKind.DRY_RUN)

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
        self.assertEqual(docker.requests, [])

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
        self.assertEqual(docker.requests, [])

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
        self.assertEqual(outcome.failure.message, "runtime effect node target is missing")
        self.assertEqual(docker.requests, [])

    def test_runtime_result_failure_is_converted_to_activity_outcome(self) -> None:
        interpreter = RecordingInterpreter(
            "docker",
            RuntimeEffectResult.failed(
                "event-intent",
                RuntimeEffectFailure(
                    "docker.container-failed",
                    "container failed",
                    {"container": "api"},
                ),
            ),
        )
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: interpreter})

        outcome = dispatcher.execute(context_for(StartNode(NodeTarget("api"))))

        self.assertEqual(outcome.kind.name, "FAILED")
        self.assertIsNotNone(outcome.failure)
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "docker.container-failed")
        self.assertEqual(outcome.failure.details.descriptor(), {"container": "api"})

    def test_runtime_result_effect_id_mismatch_becomes_uncertain(self) -> None:
        interpreter = RecordingInterpreter(
            "docker",
            RuntimeEffectResult.succeeded("different-effect"),
        )
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: interpreter})

        outcome = dispatcher.execute(context_for(StartNode(NodeTarget("api"))))

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertIsNotNone(outcome.failure)
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "runtime.effect-id-mismatch")

    def test_runtime_endpoint_observations_become_operations_observations(self) -> None:
        interpreter = RecordingInterpreter(
            "docker",
            RuntimeEffectResult.succeeded(
                "event-intent",
                observations=(
                    RuntimeEndpointObservation(
                        "api",
                        "http",
                        "graph-desired",
                        Protocol.HTTP,
                        EndpointContext.RUNTIME_PRIVATE,
                        LiteralEndpointMaterial("http://api-http:8000"),
                    ),
                ),
            ),
        )
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: interpreter})

        outcome = dispatcher.execute(context_for(StartNode(NodeTarget("api"))))

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(len(outcome.observations), 1)
        observation = outcome.observations[0]
        self.assertEqual(observation.observation_id, "event-intent:runtime-endpoint:1")
        self.assertEqual(observation.workspace_id, "workspace-a")
        self.assertEqual(observation.subject_id, "api")
        self.assertEqual(observation.graph_id, "graph-desired")
        self.assertEqual(observation.endpoint_context, EndpointContext.RUNTIME_PRIVATE)
        self.assertEqual(observation.evidence.descriptor()["runtime_endpoint"]["subject_id"], "api")


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
        registered_products=(_registered_product(),),
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
    reference = ProductReference.from_document(_registered_product().descriptor_document)
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
                metadata={
                    "product_identity": reference.identity.key,
                    "product_descriptor_digest": reference.descriptor_sha256.value,
                },
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


def _registered_product() -> RegisteredProduct:
    product = ContainerServerProduct(
        identity=ProductIdentity("openj92", "hello-server", 1),
        image=OciImageReference(
            registry="ghcr.io",
            repository="openj92/control-plane-kit-servers/hello-server",
            digest="sha256:" + "a" * 64,
        ),
        runtime_contract=ProductRuntimeContract(
            sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),)),
            provider_ports=(ProviderRuntimePort("http", 8000),),
        ),
    )
    return RegisteredProduct.from_document(
        workspace_id="workspace-a",
        descriptor_document=ProductDescriptorCodec().encode_document(product),
        source=InlineDescriptorSource(),
        imported_by="operator-a",
        imported_at="2026-07-22T09:00:00Z",
    )


if __name__ == "__main__":
    unittest.main()
