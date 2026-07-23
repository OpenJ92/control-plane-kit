from __future__ import annotations

from dataclasses import replace
import subprocess
from typing import get_protocol_members
import unittest
from unittest.mock import patch

from control_plane_kit_core.algebra import (
    BlockSockets,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
    RequirementSocket,
    SocketConnection,
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
    ActivityDependency,
    ActivityPlan,
    NodeTarget,
    PlannedActivity,
    RuntimeTarget,
    RemoveNodeResource,
    RemoveRuntimeResource,
    ReconcileNode,
    ReconcileRuntime,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    WaitForHealthy,
)
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductFamily,
    ProductInstanceConfiguration,
    ProductReference,
    ProviderRuntimePort,
    ProductRuntimeContract,
    ProductIdentity,
    RetainedDataMount,
    instantiate_product,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.probe_intents import ProbeKind, ProbeOutcome
from control_plane_kit_core.secrets import (
    LocalDevelopmentSecretResolver,
    SecretEnvironmentDelivery,
    SecretProviderAuthority,
    SecretProviderId,
    SecretReference,
)
from control_plane_kit_core.topology import DeploymentGraph, compile_topology
from control_plane_kit_core.types import Protocol, RuntimeKind
from control_plane_kit_core.verification import (
    HttpCheck,
    PostgresQueryCheck,
    VerificationContract,
    VerificationOutcome,
    VerificationPolicy,
)
from control_plane_kit_operations.coordinator import (
    ActivityRealizationContext,
    RuntimeInterpreterDispatcher,
)
from control_plane_kit_operations.docker_realization import (
    DockerHealthCheckResult,
    DockerProductRealizationAdapter,
    DockerRealizationClient,
    DockerResourceInspection,
    StdlibDockerHealthCheckClient,
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
    ObservationStatus,
    RetryIdentity,
)


class RecordingDockerClient:
    def __init__(self) -> None:
        self.networks: dict[str, DockerResourceInspection] = {}
        self.containers: dict[str, DockerResourceInspection] = {}
        self.volumes: dict[str, DockerResourceInspection] = {}
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

    def inspect_volume(self, name: str) -> DockerResourceInspection | None:
        self.calls.append(("inspect-volume", name))
        return self.volumes.get(name)

    def create_volume(self, name: str, *, labels: dict[str, str]) -> None:
        self.calls.append(("create-volume", (name, dict(labels))))
        self.volumes[name] = DockerResourceInspection(
            name=name,
            running=False,
            image=None,
            labels=dict(labels),
        )

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
        mounts: dict[str, str],
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
                    "mounts": dict(mounts),
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

    def stop_container(self, name: str) -> None:
        self.calls.append(("stop-container", name))
        inspected = self.containers[name]
        self.containers[name] = replace(inspected, running=False)

    def remove_container(self, name: str) -> None:
        self.calls.append(("remove-container", name))
        del self.containers[name]

    def remove_network(self, name: str) -> None:
        self.calls.append(("remove-network", name))
        del self.networks[name]


class FailingPullDockerClient(RecordingDockerClient):
    def pull_image(self, image: str) -> None:
        self.calls.append(("pull-image", image))
        raise subprocess.CalledProcessError(
            125,
            ["docker", "pull", image],
            stderr="invalid reference format\n" + ("x" * 3000),
        )


class RecordingHealthClient:
    def __init__(self, *, outcome: VerificationOutcome = VerificationOutcome.PASSED) -> None:
        self.outcome = outcome
        self.http_calls: list[tuple[str, str]] = []
        self.postgres_calls: list[tuple[str, str]] = []

    def check_http(self, base_url: str, check: HttpCheck) -> DockerHealthCheckResult:
        self.http_calls.append((base_url, check.check_id))
        if self.outcome is VerificationOutcome.PASSED:
            return DockerHealthCheckResult.passed(
                {"kind": "http", "url": base_url + check.path, "status_code": 200}
            )
        return DockerHealthCheckResult.failed(
            self.outcome,
            {"kind": "http", "url": base_url + check.path, "status_code": 503},
        )

    def check_postgres(
        self,
        base_url: str,
        check: PostgresQueryCheck,
    ) -> DockerHealthCheckResult:
        self.postgres_calls.append((base_url, check.check_id))
        if self.outcome is VerificationOutcome.PASSED:
            return DockerHealthCheckResult.passed(
                {"kind": "postgres", "url": base_url, "operation": "select-one"}
            )
        return DockerHealthCheckResult.failed(
            self.outcome,
            {"kind": "postgres", "url": base_url, "operation": "select-one"},
        )


class EventuallyReadyOpener:
    def __init__(self) -> None:
        self.calls = 0

    def open(self, request, *, timeout: float):  # noqa: ANN001
        del request, timeout
        self.calls += 1
        if self.calls < 3:
            raise OSError("not listening yet")
        return ReadyResponse()


class ReadyResponse:
    status = 200

    def __enter__(self) -> "ReadyResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        del exc_type, exc, traceback

    def read(self, size: int) -> bytes:
        del size
        return b"ok"


class DockerProductRealizationAdapterTests(unittest.TestCase):
    def test_client_protocol_is_the_stable_backend_boundary_for_sdk_replacement(self) -> None:
        self.assertEqual(
            get_protocol_members(DockerRealizationClient),
            frozenset(
                {
                    "inspect_network",
                    "create_network",
                    "inspect_container",
                    "inspect_volume",
                    "create_volume",
                    "pull_image",
                    "run_container",
                    "start_container",
                    "stop_container",
                    "remove_container",
                    "remove_network",
                }
            ),
        )

    def test_runtime_dispatcher_can_wrap_docker_adapter_without_server_branch(self) -> None:
        client = RecordingDockerClient()
        adapter = DockerProductRealizationAdapter(project_name="cpk", client=client)
        dispatcher = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: adapter})

        outcome = dispatcher.execute(context_for(StartNode(NodeTarget("hello"))))

        self.assertEqual(outcome.kind.value, "succeeded")
        self.assertEqual(client.calls[-1][0], "run-container")

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

    def test_reconcile_runtime_ensures_owned_network_without_new_runtime_model(self) -> None:
        client = RecordingDockerClient()
        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=client,
        ).execute(context_for(ReconcileRuntime(RuntimeTarget("docker"))))

        self.assertEqual(outcome.kind.value, "succeeded")
        self.assertEqual(client.calls[0], ("inspect-network", "cpk-workspace-a-docker"))
        self.assertEqual(client.calls[1][0], "create-network")
        self.assertEqual(
            outcome.evidence.descriptor()["docker"],
            {
                "action": "ensure-network",
                "network": "cpk-workspace-a-docker",
                "runtime_id": "docker",
            },
        )

    def test_reconcile_runtime_accepts_prior_plan_and_graph_provenance_labels(self) -> None:
        client = RecordingDockerClient()
        client.networks["cpk-workspace-a-docker"] = DockerResourceInspection(
            name="cpk-workspace-a-docker",
            running=False,
            image=None,
            labels={
                "control-plane-kit.graph-id": "graph-before",
                "control-plane-kit.owner": "operations",
                "control-plane-kit.plan-id": "plan-before",
                "control-plane-kit.runtime-id": "docker",
                "control-plane-kit.workspace-id": "workspace-a",
            },
        )

        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=client,
        ).execute(context_for(ReconcileRuntime(RuntimeTarget("docker"))))

        self.assertEqual(outcome.kind.value, "succeeded")
        self.assertEqual(client.calls, [("inspect-network", "cpk-workspace-a-docker")])

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
        self.assertEqual(len(outcome.observations), 1)
        observation = outcome.observations[0]
        self.assertEqual(observation.observation_id, "event-start-node:process-started")
        self.assertEqual(observation.workspace_id, "workspace-a")
        self.assertEqual(observation.subject_id, "hello")
        self.assertIs(observation.status, ObservationStatus.PROCESS_STARTED)
        self.assertEqual(observation.graph_id, "graph-desired")
        self.assertIs(observation.probe_kind, ProbeKind.PROCESS)
        self.assertIs(observation.probe_outcome, ProbeOutcome.PROCESS_RUNNING)
        self.assertEqual(
            observation.evidence.descriptor(),
            {
                "docker": {
                    "action": "start-container",
                    "container": "cpk-workspace-a-docker-hello",
                }
            },
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

    def test_docker_command_failure_records_bounded_stderr_without_command_echo(self) -> None:
        client = FailingPullDockerClient()

        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=client,
        ).execute(context_for(StartNode(NodeTarget("hello"))))

        self.assertEqual(outcome.kind.value, "failed")
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.category, FailureCategory.TERMINAL)
        self.assertEqual(outcome.failure.code, "docker.command-failed")
        details = outcome.failure.details.descriptor()
        self.assertEqual(details["returncode"], 125)
        self.assertIn("invalid reference format", details["stderr"])
        self.assertLessEqual(len(details["stderr"]), 512)
        self.assertNotIn("ghcr.io/openj92", details["stderr"])

    def test_data_service_resolves_secret_and_mounts_retained_volume(self) -> None:
        product = postgres_product()
        context = context_for(StartNode(NodeTarget("hello")), product=product)
        client = RecordingDockerClient()

        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=client,
            secret_resolver=postgres_secret_resolver(),
        ).execute(context)

        self.assertEqual(outcome.kind.value, "succeeded")
        self.assertEqual(client.calls[0], ("inspect-container", "cpk-workspace-a-docker-hello"))
        self.assertEqual(client.calls[1][0], "inspect-volume")
        self.assertEqual(client.calls[1][1], "cpk-workspace-a-docker-hello-postgres-data")
        self.assertEqual(client.calls[2][0], "create-volume")
        volume_name, volume_labels = client.calls[2][1]
        self.assertEqual(volume_name, "cpk-workspace-a-docker-hello-postgres-data")
        self.assertEqual(
            volume_labels["control-plane-kit.data-resource-id"],
            "postgres-data",
        )
        run_call = client.calls[-1]
        self.assertEqual(run_call[0], "run-container")
        self.assertEqual(
            run_call[1]["environment"],
            {"POSTGRES_DB": "cpk", "POSTGRES_PASSWORD": "never-persist-this"},
        )
        self.assertEqual(
            run_call[1]["mounts"],
            {
                "cpk-workspace-a-docker-hello-postgres-data": "/var/lib/postgresql/data",
            },
        )
        self.assertNotIn("never-persist-this", repr(outcome.evidence))

    def test_secret_product_requires_runtime_resolver_before_mutation(self) -> None:
        client = RecordingDockerClient()
        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=client,
        ).execute(context_for(StartNode(NodeTarget("hello")), product=postgres_product()))

        self.assertEqual(outcome.kind.value, "unsupported")
        self.assertEqual(outcome.failure.category, FailureCategory.OPERATOR_REVIEW)
        self.assertEqual(outcome.failure.code, "docker.product-runtime-unsupported")
        self.assertEqual(client.calls, [])

    def test_foreign_retained_volume_fails_before_pull_or_run(self) -> None:
        client = RecordingDockerClient()
        client.volumes["cpk-workspace-a-docker-hello-postgres-data"] = (
            DockerResourceInspection(
                name="cpk-workspace-a-docker-hello-postgres-data",
                running=False,
                image=None,
                labels={"owner": "somebody-else"},
            )
        )

        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=client,
            secret_resolver=postgres_secret_resolver(),
        ).execute(context_for(StartNode(NodeTarget("hello")), product=postgres_product()))

        self.assertEqual(outcome.kind.value, "failed")
        self.assertEqual(outcome.failure.category, FailureCategory.TERMINAL)
        self.assertEqual(outcome.failure.code, "docker.ownership-conflict")
        self.assertEqual(
            [name for name, _ in client.calls],
            ["inspect-container", "inspect-volume"],
        )

    def test_router_target_environment_comes_from_graph_edge(self) -> None:
        app = hello_product(name="hello-server")
        router = router_product()
        graph = graph_with_products(
            (
                product_block(app, "app"),
                product_block(router, "router"),
                SocketConnection("app", "internal", "router", "active"),
            )
        )
        client = RecordingDockerClient()

        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=client,
        ).execute(
            context_for_graph(
                StartNode(NodeTarget("router")),
                graph=graph,
                products=(app, router),
            )
        )

        self.assertEqual(outcome.kind.value, "succeeded")
        run_call = client.calls[-1]
        self.assertEqual(run_call[0], "run-container")
        self.assertEqual(
            run_call[1]["environment"],
            {"ACTIVE_TARGET_URL": graph.node("app").endpoint("internal").url},
        )

    def test_multiplexer_environment_comes_from_each_connected_requirement(self) -> None:
        primary = hello_product(name="hello-server")
        observer = hello_product(name="hello-observer")
        multiplexer = multiplexer_product()
        graph = graph_with_products(
            (
                product_block(primary, "primary"),
                product_block(observer, "observer"),
                product_block(multiplexer, "multiplexer"),
                SocketConnection("primary", "internal", "multiplexer", "primary"),
                SocketConnection("observer", "internal", "multiplexer", "observer-a"),
            )
        )
        client = RecordingDockerClient()

        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=client,
        ).execute(
            context_for_graph(
                StartNode(NodeTarget("multiplexer")),
                graph=graph,
                products=(primary, observer, multiplexer),
            )
        )

        self.assertEqual(outcome.kind.value, "succeeded")
        run_call = client.calls[-1]
        self.assertEqual(run_call[0], "run-container")
        self.assertEqual(
            run_call[1]["environment"],
            {
                "MULTIPLEXER_OBSERVER_A_URL": graph.node("observer")
                .endpoint("internal")
                .url,
                "MULTIPLEXER_PRIMARY_URL": graph.node("primary")
                .endpoint("internal")
                .url,
            },
        )
        self.assertNotIn("MULTIPLEXER_OBSERVER_B_URL", run_call[1]["environment"])

    def test_wait_for_healthy_interprets_http_verification_against_provider_port(self) -> None:
        health = RecordingHealthClient()
        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=RecordingDockerClient(),
            health=health,
        ).execute(context_for(WaitForHealthy(NodeTarget("hello"))))

        self.assertEqual(outcome.kind.value, "succeeded")
        self.assertEqual(health.http_calls, [("http://hello-internal:8000", "live")])
        self.assertEqual(
            outcome.evidence.descriptor()["docker"],
            {
                "action": "wait-healthy",
                "node_id": "hello",
                "checks": ["live"],
            },
        )
        observation = outcome.observations[0]
        self.assertEqual(observation.observation_id, "event-start-node:healthy")
        self.assertIs(observation.status, ObservationStatus.HEALTHY)
        self.assertIs(observation.probe_kind, ProbeKind.APPLICATION_HEALTH)
        self.assertIs(observation.probe_outcome, ProbeOutcome.HEALTHY)

    def test_wait_for_healthy_failed_check_fails_without_process_status_rewrite(self) -> None:
        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=RecordingDockerClient(),
            health=RecordingHealthClient(outcome=VerificationOutcome.FAILED),
        ).execute(context_for(WaitForHealthy(NodeTarget("hello"))))

        self.assertEqual(outcome.kind.value, "failed")
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "docker.health-check-failed")
        self.assertEqual(
            outcome.failure.details.descriptor(),
            {
                "check_ids": ["live"],
                "checks": [
                    {
                        "check_id": "live",
                        "outcome": "failed",
                        "evidence": {
                            "kind": "http",
                            "status_code": 503,
                            "url": "http://hello-internal:8000/health/live",
                        },
                    }
                ],
            },
        )
        self.assertEqual(outcome.observations, ())

    def test_stdlib_http_health_retry_attempts_wait_between_startup_failures(self) -> None:
        opener = EventuallyReadyOpener()
        sleeps: list[float] = []

        with patch(
            "control_plane_kit_operations.docker_realization.build_opener",
            return_value=opener,
        ):
            result = StdlibDockerHealthCheckClient(
                retry_delay_seconds=0.125,
                sleeper=sleeps.append,
            ).check_http(
                "http://hello-internal:8000",
                HttpCheck(
                    check_id="ready",
                    provider_socket="internal",
                    path="/health/ready",
                    policy=VerificationPolicy(maximum_attempts=3),
                ),
            )

        self.assertIs(result.outcome, VerificationOutcome.PASSED)
        self.assertEqual(opener.calls, 3)
        self.assertEqual(sleeps, [0.125, 0.125])
        assert result.evidence is not None
        self.assertEqual(result.evidence.descriptor()["attempts"], 3)

    def test_postgres_health_uses_injected_checker_and_provider_port(self) -> None:
        health = RecordingHealthClient()
        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=RecordingDockerClient(),
            health=health,
        ).execute(
            context_for(
                WaitForHealthy(NodeTarget("hello")),
                product=postgres_product(),
            )
        )

        self.assertEqual(outcome.kind.value, "succeeded")
        self.assertEqual(
            health.postgres_calls,
            [("postgresql://hello-postgres:5432", "select-one")],
        )

    def test_postgres_health_without_checker_is_explicitly_unsupported(self) -> None:
        outcome = DockerProductRealizationAdapter(
            project_name="cpk",
            client=RecordingDockerClient(),
        ).execute(
            context_for(
                WaitForHealthy(NodeTarget("hello")),
                product=postgres_product(),
            )
        )

        self.assertEqual(outcome.kind.value, "unsupported")
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "docker.product-runtime-unsupported")
        self.assertIn("postgres health verification", outcome.failure.message)

    def test_teardown_stops_and_removes_owned_compute_and_runtime_network(self) -> None:
        client = RecordingDockerClient()
        adapter = DockerProductRealizationAdapter(project_name="cpk", client=client)

        adapter.execute(context_for(StartRuntime(RuntimeTarget("docker"))))
        adapter.execute(context_for(StartNode(NodeTarget("hello"))))
        stopped = adapter.execute(context_for(StopNode(NodeTarget("hello"))))
        removed = adapter.execute(context_for(RemoveNodeResource(NodeTarget("hello"))))
        runtime_stopped = adapter.execute(context_for(StopRuntime(RuntimeTarget("docker"))))
        runtime_removed = adapter.execute(
            context_for(RemoveRuntimeResource(RuntimeTarget("docker")))
        )

        self.assertEqual(stopped.kind.value, "succeeded")
        self.assertEqual(removed.kind.value, "succeeded")
        self.assertEqual(runtime_stopped.kind.value, "succeeded")
        self.assertEqual(runtime_removed.kind.value, "succeeded")
        self.assertIn(("stop-container", "cpk-workspace-a-docker-hello"), client.calls)
        self.assertIn(("remove-container", "cpk-workspace-a-docker-hello"), client.calls)
        self.assertIn(("remove-network", "cpk-workspace-a-docker"), client.calls)
        self.assertEqual(client.containers, {})
        self.assertEqual(client.networks, {})

    def test_remove_node_resource_uses_base_product_material_when_desired_reuses_node_id(
        self,
    ) -> None:
        base_product = hello_product(name="hello-server")
        desired_product = hello_product(name="hello-server-v2")
        base_graph = graph_with_products((product_block(base_product, "hello"),))
        desired_graph = graph_with_products((product_block(desired_product, "hello"),))
        client = RecordingDockerClient()
        adapter = DockerProductRealizationAdapter(project_name="cpk", client=client)
        adapter.execute(
            context_for_graph(
                StartNode(NodeTarget("hello")),
                graph=base_graph,
                products=(base_product,),
            )
        )
        client.calls.clear()

        outcome = adapter.execute(
            context_for_transition(
                RemoveNodeResource(NodeTarget("hello")),
                base_graph=base_graph,
                desired_graph=desired_graph,
                products=(base_product, desired_product),
            )
        )

        self.assertEqual(outcome.kind.value, "succeeded")
        self.assertEqual(
            client.calls,
            [
                ("inspect-container", "cpk-workspace-a-docker-hello"),
                ("remove-container", "cpk-workspace-a-docker-hello"),
            ],
        )

    def test_reconcile_recreates_container_from_desired_material(self) -> None:
        client = RecordingDockerClient()
        adapter = DockerProductRealizationAdapter(project_name="cpk", client=client)
        adapter.execute(context_for(StartNode(NodeTarget("hello"))))

        outcome = adapter.execute(context_for(ReconcileNode(NodeTarget("hello"))))

        self.assertEqual(outcome.kind.value, "succeeded")
        self.assertIn(("stop-container", "cpk-workspace-a-docker-hello"), client.calls)
        self.assertIn(("remove-container", "cpk-workspace-a-docker-hello"), client.calls)
        self.assertEqual(client.calls[-1][0], "run-container")
        self.assertEqual(
            outcome.evidence.descriptor()["docker"]["action"],
            "reconcile-container",
        )


def context_for(operation, *, product: ContainerServerProduct | None = None) -> ActivityRealizationContext:
    product = hello_product() if product is None else product
    return context_for_graph(
        operation,
        graph=desired_graph(product),
        products=(product,),
    )


def context_for_graph(
    operation,
    *,
    graph: DeploymentGraph,
    products: tuple[ContainerServerProduct, ...],
) -> ActivityRealizationContext:
    documents = tuple(
        ProductDescriptorCodec().encode_document(product)
        for product in products
    )
    registered = tuple(
        RegisteredProduct(
            f"registration-{index}",
            "workspace-a",
            ProductReference.from_document(document),
            document,
            InlineDescriptorSource(),
            "operator-a",
            "2026-07-22T10:00:10Z",
        )
        for index, document in enumerate(documents)
    )
    if isinstance(operation, WaitForHealthy):
        plan = ActivityPlan(
            (
                PlannedActivity(ActivityId("start-runtime"), StartRuntime(RuntimeTarget("docker"))),
                PlannedActivity(
                    ActivityId("start-node"),
                    StartNode(operation.target),
                    dependencies=(ActivityDependency(ActivityId("start-runtime")),),
                ),
                PlannedActivity(
                    ActivityId("wait-healthy"),
                    operation,
                    dependencies=(ActivityDependency(ActivityId("start-node")),),
                ),
            )
        )
        activity = plan.activity(ActivityId("wait-healthy"))
    else:
        plan = ActivityPlan(
            (
                PlannedActivity(ActivityId("start-runtime"), StartRuntime(RuntimeTarget("docker"))),
                PlannedActivity(ActivityId("start-node"), operation),
            )
        )
    if isinstance(operation, StartRuntime):
        activity = plan.activity(ActivityId("start-runtime"))
    elif not isinstance(operation, WaitForHealthy):
        activity = plan.activity(ActivityId("start-node"))
    source_graph = graph if isinstance(
        operation,
        (StopNode, RemoveNodeResource, StopRuntime, RemoveRuntimeResource),
    ) else DeploymentGraph("current")
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
            graph=source_graph,
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
        registered_products=registered,
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


def context_for_transition(
    operation,
    *,
    base_graph: DeploymentGraph,
    desired_graph: DeploymentGraph,
    products: tuple[ContainerServerProduct, ...],
) -> ActivityRealizationContext:
    documents = tuple(
        ProductDescriptorCodec().encode_document(product)
        for product in products
    )
    registered = tuple(
        RegisteredProduct(
            f"registration-{index}",
            "workspace-a",
            ProductReference.from_document(document),
            document,
            InlineDescriptorSource(),
            "operator-a",
            "2026-07-22T10:00:10Z",
        )
        for index, document in enumerate(documents)
    )
    plan = ActivityPlan(
        (
            PlannedActivity(ActivityId("remove-node"), operation),
        )
    )
    activity = plan.activity(ActivityId("remove-node"))
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
            graph=base_graph,
            created_by="operator-a",
            created_at="2026-07-22T10:00:00Z",
        ),
        desired_graph=GraphVersionRecord.from_graph(
            graph_id="graph-desired",
            workspace_id="workspace-a",
            version=2,
            graph=desired_graph,
            created_by="operator-a",
            created_at="2026-07-22T10:00:30Z",
        ),
        registered_products=registered,
        authority=ExecutionWorkerAuthority(
            "worker-a",
            (PolicyScope.EXECUTION_OPERATE,),
        ),
        intent_event=ActivityEventRecord(
            "event-remove-node",
            "run-a",
            3,
            ActivityEventKind.STEP_STARTED,
            "2026-07-22T10:03:00Z",
            activity_id=activity.activity_id.value,
            evidence=BoundedEvidence.from_mapping({"phase": "intent"}),
        ),
    )


def desired_graph(product: ContainerServerProduct) -> DeploymentGraph:
    block = product_block(product, "hello")
    return graph_with_products((block,))


def graph_with_products(children) -> DeploymentGraph:
    return compile_topology(
        DeploymentTopology(
            "desired",
            DockerRuntime(
                runtime_id="docker",
                network_name="cpk-workspace-a-docker",
                children=tuple(children),
            ),
        )
    )


def product_block(product: ContainerServerProduct, role_id: str):
    return instantiate_product(
        product,
        role_id,
        ProductInstanceConfiguration.from_contract(product.runtime_contract),
    )


def hello_product(
    *,
    name: str = "hello-server",
    contract: ProductRuntimeContract | None = None,
    product_family: ProductFamily = ProductFamily.SERVER,
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
            provider_ports=(ProviderRuntimePort("internal", 8000),),
            public_environment=(
                PublicStaticEnvironmentBinding("HELLO_MESSAGE", "Hello from ops"),
            ),
            verification=VerificationContract(
                (
                    HttpCheck(
                        check_id="live",
                        provider_socket="internal",
                        path="/health/live",
                    ),
                )
            ),
        ),
        display_name=name,
        description="test product",
        product_family=product_family,
    )


def postgres_product() -> ContainerServerProduct:
    return hello_product(
        name="postgres-server",
        contract=ProductRuntimeContract(
            sockets=BlockSockets(
                providers=(ProviderSocket("postgres", Protocol.POSTGRES),),
            ),
            provider_ports=(ProviderRuntimePort("postgres", 5432),),
            public_environment=(
                PublicStaticEnvironmentBinding("POSTGRES_DB", "cpk"),
            ),
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "POSTGRES_PASSWORD",
                    SecretReference("secret://control-plane-kit/postgres/password"),
                ),
            ),
            retained_data_mounts=(
                RetainedDataMount("postgres-data", "/var/lib/postgresql/data"),
            ),
            verification=VerificationContract(
                (PostgresQueryCheck(check_id="select-one", provider_socket="postgres"),)
            ),
            lifecycle=ResourceLifecycle.owned_with_retained_data("postgres-data"),
        ),
        product_family=ProductFamily.DATA_SERVICE,
    )


def postgres_secret_resolver() -> LocalDevelopmentSecretResolver:
    return LocalDevelopmentSecretResolver(
        SecretProviderAuthority(
            SecretProviderId("control-plane-kit"),
            (("postgres",),),
        ),
        {
            "secret://control-plane-kit/postgres/password": "never-persist-this",
        },
    )


def router_product() -> ContainerServerProduct:
    return hello_product(
        name="http-active-router",
        contract=ProductRuntimeContract(
            sockets=BlockSockets(
                requirements=(
                    RequirementSocket(
                        "active",
                        Protocol.HTTP,
                        ("ACTIVE_TARGET_URL",),
                    ),
                ),
                providers=(ProviderSocket("internal", Protocol.HTTP),),
            ),
            provider_ports=(ProviderRuntimePort("internal", 8000),),
            verification=VerificationContract(
                (HttpCheck(check_id="live", provider_socket="internal", path="/health/live"),)
            ),
        ),
    )


def multiplexer_product() -> ContainerServerProduct:
    return hello_product(
        name="http-multiplexer",
        contract=ProductRuntimeContract(
            sockets=BlockSockets(
                requirements=(
                    RequirementSocket(
                        "primary",
                        Protocol.HTTP,
                        ("MULTIPLEXER_PRIMARY_URL",),
                    ),
                    RequirementSocket(
                        "observer-a",
                        Protocol.HTTP,
                        ("MULTIPLEXER_OBSERVER_A_URL",),
                        required=False,
                    ),
                    RequirementSocket(
                        "observer-b",
                        Protocol.HTTP,
                        ("MULTIPLEXER_OBSERVER_B_URL",),
                        required=False,
                    ),
                ),
                providers=(ProviderSocket("internal", Protocol.HTTP),),
            ),
            provider_ports=(ProviderRuntimePort("internal", 8000),),
            verification=VerificationContract(
                (HttpCheck(check_id="live", provider_socket="internal", path="/health/live"),)
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
