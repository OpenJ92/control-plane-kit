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
    NodeTarget,
    PlannedActivity,
    StartNode,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductReference,
    ProductRuntimeContract,
    ProviderRuntimePort,
)
from control_plane_kit_core.topology import DeploymentGraph, Node, RuntimeRecord
from control_plane_kit_core.types import BlockFamily, Protocol, RuntimeKind
from control_plane_kit_operations.coordinator import ActivityRealizationContext
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
from control_plane_kit_operations.runtime_effects import runtime_effect_request_for_context


class RuntimeEffectTranslationTests(unittest.TestCase):
    def test_context_translates_to_core_runtime_effect_request(self) -> None:
        context = _context()

        request = runtime_effect_request_for_context(context)

        self.assertEqual(request.effect_id, "event-started")
        self.assertEqual(request.runtime_kind, RuntimeKind.DOCKER)
        self.assertEqual(request.source.workspace_id, "workspace-a")
        self.assertEqual(request.source.desired_graph_id, "graph-desired")
        self.assertEqual(request.activity_id, ActivityId("activity-a"))
        self.assertEqual(request.operation, StartNode(NodeTarget("api")))
        self.assertEqual(len(request.products), 1)
        self.assertEqual(request.products[0].node_id, "api")
        self.assertEqual(request.products[0].runtime_id, "docker")
        self.assertEqual(
            request.products[0].reference,
            ProductReference.from_document(_registered_product().descriptor_document),
        )


def _context() -> ActivityRealizationContext:
    activity = PlannedActivity(ActivityId("activity-a"), StartNode(NodeTarget("api")))
    plan = ActivityPlan((activity,))
    graph = _graph()
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
            "graph-base",
            "graph-desired",
            ActivityPlanStatus.PLANNED,
            "2026-07-22T10:00:00Z",
            plan,
        ),
        base_graph=GraphVersionRecord.from_graph(
            graph_id="graph-base",
            workspace_id="workspace-a",
            version=1,
            graph=graph,
            created_by="operator-a",
            created_at="2026-07-22T09:00:00Z",
        ),
        desired_graph=GraphVersionRecord.from_graph(
            graph_id="graph-desired",
            workspace_id="workspace-a",
            version=2,
            graph=graph,
            created_by="operator-a",
            created_at="2026-07-22T10:00:00Z",
        ),
        registered_products=(_registered_product(),),
        authority=ExecutionWorkerAuthority(
            worker_id="worker-a",
            scopes=(PolicyScope.EXECUTION_OPERATE,),
        ),
        intent_event=ActivityEventRecord(
            event_id="event-started",
            run_id="run-a",
            kind=ActivityEventKind.STEP_STARTED,
            activity_id="activity-a",
            occurred_at="2026-07-22T10:02:00Z",
            ordinal=3,
        ),
    )


def _graph() -> DeploymentGraph:
    product = _registered_product()
    reference = product.reference
    return DeploymentGraph(
        name="demo",
        nodes={
            "api": Node(
                node_id="api",
                block_family=BlockFamily.APPLICATION,
                block_spec=BlockSpec("api"),
                kind="container-server",
                runtime_id="docker",
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),)),
                metadata={
                    "product_identity": reference.identity.key,
                    "product_descriptor_digest": reference.descriptor_sha256.value,
                },
            )
        },
        runtimes={"docker": RuntimeRecord("docker", RuntimeKind.DOCKER, ("api",))},
    )


def _registered_product() -> RegisteredProduct:
    product = ContainerServerProduct(
        identity=ProductReference.from_document(
            ProductDescriptorCodec().encode_document(
                ContainerServerProduct(
                    identity=_identity(),
                    image=OciImageReference(
                        registry="ghcr.io",
                        repository="openj92/control-plane-kit-servers/hello-server",
                        digest="sha256:" + "a" * 64,
                    ),
                    runtime_contract=ProductRuntimeContract(
                        sockets=BlockSockets(
                            providers=(ProviderSocket("http", Protocol.HTTP),)
                        ),
                        provider_ports=(ProviderRuntimePort("http", 8000),),
                    ),
                )
            )
        ).identity,
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
    document = ProductDescriptorCodec().encode_document(product)
    return RegisteredProduct.from_document(
        workspace_id="workspace-a",
        descriptor_document=document,
        source=InlineDescriptorSource(),
        imported_by="operator-a",
        imported_at="2026-07-22T09:00:00Z",
    )


def _identity():
    from control_plane_kit_core.products import ProductIdentity

    return ProductIdentity("openj92", "hello-server", 1)


if __name__ == "__main__":
    unittest.main()
