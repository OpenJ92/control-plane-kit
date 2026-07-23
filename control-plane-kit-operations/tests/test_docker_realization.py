from __future__ import annotations

from dataclasses import replace
import unittest

from control_plane_kit_core.algebra import (
    BlockSockets,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
)
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.lifecycle import ResourceLifecycle
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    FailureCategory,
)
from control_plane_kit_core.planning import (
    ActivityId,
    ActivityPlan,
    NodeTarget,
    PlannedActivity,
    RuntimeTarget,
    StartNode,
    StartRuntime,
)
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductInstanceConfiguration,
    ProductReference,
    ProductRuntimeContract,
    ProductIdentity,
    instantiate_product,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit_core.topology import DeploymentGraph, compile_topology
from control_plane_kit_core.types import Protocol
from control_plane_kit_operations.coordinator import ActivityRealizationContext
from control_plane_kit_operations.docker_realization import (
    DockerProductRealizationAdapter,
    DockerResourceInspection,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.products import InlineDescriptorSource, RegisteredProduct
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


class RecordingDockerClient:
    def __init__(self) -> None:
        self.networks: dict[str, DockerResourceInspection] = {}
        self.containers: dict[str, DockerResourceInspection] = {}
        self.calls: list[tuple[str, object]] = []

    def inspect_network(self, name: str) -> DockerResourceInspection | None:
        self.calls.append(("inspect-network", name))
        return self.networks.get(name)

    def create_network(self, name: str, *, labels: dict[str, str]) -> None:
        self.calls.append(("create-network", (name, dict(labels))))
        self.networks[name] = DockerResourceInspection(
            name=name,
            running=False,
            image=None,
            labels=dict(labels),
        )

    def inspect_container(self, name: str) -> DockerResourceInspection | None:
        self.calls.append(("inspect-container", name))
        return self.containers.get(name)

    def pull_image(self, image: str) -> None:
        self.calls.append(("pull-image", image))

    def run_container(
        self,
        *,
        name: str,
        image: str,
        network: str,
        environment: dict[str, str],
        labels: dict[str, str],
        network_aliases: tuple[str, ...],
    ) -> None:
        self.calls.append(
            (
                "run-container",
                {
                    "name": name,
                    "image": image,
                    "network": network,
                    "environment": dict(environment),
                    "labels": dict(labels),
                    "network_aliases": list(network_aliases),
                },
            )
        )
        self.containers[name] = DockerResourceInspection(
            name=name,
            running=True,
            image=image,
            labels=dict(labels),
        )

    def start_container(self, name: str) -> None:
        self.calls.append(("start-container", name))
        inspected = self.containers[name]
        self.containers[name] = replace(inspected, running=True)


class DockerProductRealizationAdapterTests(unittest.TestCase):
    def test_start_runtime_creates_owned_network_with_workspace_and_plan_labels(self) -> None:
        client = RecordingDockerClient()
        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=client,
        ).execute(context_for(StartRuntime(RuntimeTarget("docker"))))

        self.assertEqual(outcome.kind.value, "succeeded")
        self.assertEqual(
            client.calls,
            [
                ("inspect-network", "cpk-workspace-a-docker"),
                (
                    "create-network",
                    (
                        "cpk-workspace-a-docker",
                        {
                            "control-plane-kit.graph-id": "graph-desired",
                            "control-plane-kit.owner": "operations",
                            "control-plane-kit.plan-id": "plan-a",
                            "control-plane-kit.runtime-id": "docker",
                            "control-plane-kit.workspace-id": "workspace-a",
                        },
                    ),
                ),
            ],
        )
        self.assertEqual(
            outcome.evidence.descriptor(),
            {
                "docker": {
                    "action": "ensure-network",
                    "network": "cpk-workspace-a-docker",
                    "runtime_id": "docker",
                }
            },
        )

    def test_start_node_pulls_digest_and_runs_with_private_aliases_and_labels(self) -> None:
        client = RecordingDockerClient()
        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=client,
        ).execute(context_for(StartNode(NodeTarget("hello"))))

        self.assertEqual(outcome.kind.value, "succeeded")
        run_call = client.calls[-1]
        self.assertEqual(run_call[0], "run-container")
        command = run_call[1]
        self.assertEqual(
            command["image"],
            "ghcr.io/openj92/control-plane-kit-servers/hello-server@sha256:"
            + "a" * 64,
        )
        self.assertEqual(
            command["environment"],
            {"HELLO_MESSAGE": "Hello from ops"},
        )
        self.assertEqual(command["network_aliases"], ["hello-internal"])
        self.assertEqual(
            command["labels"]["control-plane-kit.product-identity"],
            "control-plane-kit/hello-server/1",
        )
        self.assertEqual(
            command["labels"]["control-plane-kit.product-descriptor-sha256"],
            context_for(StartNode(NodeTarget("hello"))).registered_products[
                0
            ].descriptor_document.content_digest,
        )
        self.assertIn(
            ("pull-image", command["image"]),
            client.calls,
        )
        self.assertEqual(
            outcome.evidence.descriptor()["docker"]["action"],
            "start-container",
        )

    def test_foreign_container_collision_fails_before_pull_or_run(self) -> None:
        client = RecordingDockerClient()
        client.containers["cpk-workspace-a-docker-hello"] = DockerResourceInspection(
            name="cpk-workspace-a-docker-hello",
            running=True,
            image="foreign",
            labels={"owner": "somebody-else"},
        )

        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=client,
        ).execute(context_for(StartNode(NodeTarget("hello"))))

        self.assertEqual(outcome.kind.value, "failed")
        self.assertEqual(outcome.failure.category, FailureCategory.TERMINAL)
        self.assertEqual(outcome.failure.code, "docker.ownership-conflict")
        self.assertEqual(
            [name for name, _ in client.calls],
            ["inspect-container"],
        )

    def test_secret_and_retained_data_products_are_unsupported_before_mutation(self) -> None:
        product = hello_product(
            name="postgres-server",
            contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("postgres", Protocol.POSTGRES),)),
                public_environment=(PublicStaticEnvironmentBinding("POSTGRES_DB", "cpk"),),
                secret_deliveries=(
                    SecretEnvironmentDelivery(
                        "POSTGRES_PASSWORD",
                        SecretReference("secret://control-plane-kit/postgres/password"),
                    ),
                ),
                lifecycle=ResourceLifecycle.owned_with_retained_data("postgres-data"),
            ),
        )
        context = context_for(StartNode(NodeTarget("hello")), product=product)
        client = RecordingDockerClient()

        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=client,
        ).execute(context)

        self.assertEqual(outcome.kind.value, "unsupported")
        self.assertEqual(outcome.failure.category, FailureCategory.OPERATOR_REVIEW)
        self.assertEqual(outcome.failure.code, "docker.product-runtime-unsupported")
        self.assertEqual(client.calls, [])


def context_for(operation, *, product: ContainerServerProduct | None = None) -> ActivityRealizationContext:
    product = hello_product() if product is None else product
    document = ProductDescriptorCodec().encode_document(product)
    graph = desired_graph(product)
    plan = ActivityPlan(
        (
            PlannedActivity(ActivityId("start-runtime"), StartRuntime(RuntimeTarget("docker"))),
            PlannedActivity(ActivityId("start-node"), operation),
        )
    )
    if isinstance(operation, StartRuntime):
        activity = plan.activity(ActivityId("start-runtime"))
    else:
        activity = plan.activity(ActivityId("start-node"))
    return ActivityRealizationContext(
        activity=activity,
        request=ExecutionRequestRecord(
            ExecutionRequestIdentity("request-a", "workspace-a", "session-a", "plan-a"),
            ExecutionRequestStatus.CLAIMED,
            "operator-a",
            "2026-07-22T10:00:00Z",
            "approval-request-a",
            "approval-decision-a",
            ExecutionIdempotency("admit-a", "fingerprint-a"),
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
        base_graph=GraphVersionRecord.from_graph(
            graph_id="graph-current",
            workspace_id="workspace-a",
            version=1,
            graph=DeploymentGraph("current"),
            created_by="operator-a",
            created_at="2026-07-22T10:00:00Z",
        ),
        desired_graph=GraphVersionRecord.from_graph(
            graph_id="graph-desired",
            workspace_id="workspace-a",
            version=2,
            graph=graph,
            created_by="operator-a",
            created_at="2026-07-22T10:00:30Z",
        ),
        registered_products=(
            RegisteredProduct(
                "registration-a",
                "workspace-a",
                ProductReference.from_document(document),
                document,
                InlineDescriptorSource(),
                "operator-a",
                "2026-07-22T10:00:10Z",
            ),
        ),
        authority=ExecutionWorkerAuthority(
            "worker-a",
            (PolicyScope.EXECUTION_OPERATE,),
        ),
        intent_event=ActivityEventRecord(
            "event-start-node",
            "run-a",
            3,
            ActivityEventKind.STEP_STARTED,
            "2026-07-22T10:03:00Z",
            activity_id=activity.activity_id.value,
            evidence=BoundedEvidence.from_mapping({"phase": "intent"}),
        ),
    )


def desired_graph(product: ContainerServerProduct) -> DeploymentGraph:
    block = instantiate_product(
        product,
        "hello",
        ProductInstanceConfiguration.from_contract(product.runtime_contract),
    )
    return compile_topology(
        DeploymentTopology(
            "desired",
            DockerRuntime(
                runtime_id="docker",
                network_name="cpk-workspace-a-docker",
                children=(block,),
            ),
        )
    )


def hello_product(
    *,
    name: str = "hello-server",
    contract: ProductRuntimeContract | None = None,
) -> ContainerServerProduct:
    return ContainerServerProduct(
        ProductIdentity("control-plane-kit", name, 1),
        OciImageReference(
            "ghcr.io",
            f"openj92/control-plane-kit-servers/{name}",
            "sha256:" + "a" * 64,
            tag="test",
        ),
        contract
        or ProductRuntimeContract(
            sockets=BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
            public_environment=(
                PublicStaticEnvironmentBinding("HELLO_MESSAGE", "Hello from ops"),
            ),
        ),
        display_name=name,
        description="test product",
    )


if __name__ == "__main__":
    unittest.main()
