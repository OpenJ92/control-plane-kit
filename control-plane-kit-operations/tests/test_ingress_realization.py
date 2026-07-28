from __future__ import annotations

from dataclasses import dataclass
import os
import unittest

import psycopg

from control_plane_kit_core.algebra import BlockSockets, BlockSpec, ProviderSocket
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.planning import (
    ActivityId,
    ActivityPlan,
    AllocatePublicIngress,
    PlannedActivity,
    PublicIngressActivityTarget,
    RemovePublicIngress,
)
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    NamedPublicIngress,
    PublicIngressTarget,
)
from control_plane_kit_core.secrets import SecretReference, SecretValue
from control_plane_kit_core.topology import (
    DeploymentGraph,
    Endpoint,
    LiteralAddress,
    Node,
    RuntimeRecord,
)
from control_plane_kit_core.types import BlockFamily, Protocol, RuntimeKind
from control_plane_kit_operations.coordinator import ActivityRealizationContext
from control_plane_kit_operations.ingress_authorities import (
    CloudflareOwnedIngressResource,
    CloudflareZoneIngressAuthority,
    GeneratedSecretPurpose,
    InMemoryGeneratedSecretRecorder,
    IngressAuthorityProviderKind,
    OwnedIngressResourceStatus,
)
from control_plane_kit_operations.ingress_realization import IngressRealizationAdapter
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
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


class TrackingUnitOfWorkFactory:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.active = 0
        self.entered = 0
        self.committed = 0

    def __call__(self) -> "TrackingUnitOfWork":
        return TrackingUnitOfWork(self, PostgresUnitOfWork(self._connect))

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url)


class TrackingUnitOfWork:
    def __init__(
        self,
        factory: TrackingUnitOfWorkFactory,
        inner: PostgresUnitOfWork,
    ) -> None:
        self._factory = factory
        self._inner = inner

    @property
    def stores(self):
        return self._inner.stores

    def __enter__(self) -> "TrackingUnitOfWork":
        self._factory.entered += 1
        self._factory.active += 1
        self._inner.__enter__()
        return self

    def commit(self) -> None:
        self._factory.committed += 1
        self._inner.commit()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            self._inner.__exit__(exc_type, exc, traceback)
        finally:
            self._factory.active -= 1


@dataclass(frozen=True)
class FakeIngressAllocation:
    tunnel_id: str = "tunnel-001"
    tunnel_name: str = "cpk-gateway-001"
    tunnel_token: SecretValue = SecretValue("eyJ-cloudflare-tunnel-token-bearer-value")
    dns_record_id: str = "dns-001"
    hostname: str = "cpk-gateway-001.openj92.dev"
    endpoint_url: str = "https://cpk-gateway-001.openj92.dev"


class RecordingIngressInterpreter:
    def __init__(self, tracker: TrackingUnitOfWorkFactory) -> None:
        self.tracker = tracker
        self.create_active_counts: list[int] = []
        self.create_allocation_names: list[str] = []
        self.create_origins: list[str] = []
        self.create_authorities: list[CloudflareZoneIngressAuthority] = []
        self.teardown_active_counts: list[int] = []
        self.teardown_resources: list[CloudflareOwnedIngressResource] = []
        self.fail_teardown = False

    def create(
        self,
        ingress: NamedPublicIngress,
        *,
        authority: CloudflareZoneIngressAuthority,
        allocation_name: str,
        origin_service_url: str,
    ) -> FakeIngressAllocation:
        self.create_active_counts.append(self.tracker.active)
        self.create_allocation_names.append(allocation_name)
        self.create_origins.append(origin_service_url)
        self.create_authorities.append(authority)
        return FakeIngressAllocation(
            tunnel_name=allocation_name,
            hostname=ingress.hostname,
        )

    def teardown(
        self,
        *,
        authority: CloudflareZoneIngressAuthority,
        resources: CloudflareOwnedIngressResource,
    ) -> None:
        del authority
        self.teardown_active_counts.append(self.tracker.active)
        self.teardown_resources.append(resources)
        if self.fail_teardown:
            raise RuntimeError("provider teardown failed")


class IngressRealizationAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        self.tracker = TrackingUnitOfWorkFactory(database_url)
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.ingress_authorities.register(
                workspace_id="workspace-a",
                authority_ref=IngressAuthorityReference("openj92-public-ingress"),
                authority=CloudflareZoneIngressAuthority(
                    account_id="account-openj92",
                    zone_id="zone-openj92",
                    zone_name="openj92.dev",
                    api_token_ref=SecretReference(
                        "secret://cloudflare/openj92/api-token"
                    ),
                    allowed_hostname_pattern="cpk-gateway-*.openj92.dev",
                ),
                admitted_by="operator-a",
                admitted_at="2026-07-28T08:00:00Z",
            )
            unit_of_work.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> TrackingUnitOfWork:
        return self.tracker()

    def test_allocate_public_ingress_calls_provider_outside_transaction_and_records_references(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        recorder = InMemoryGeneratedSecretRecorder()
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            generated_secret_recorder=recorder,
            clock=lambda: "2026-07-28T08:01:00Z",
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(interpreter.create_active_counts, [0])
        self.assertEqual(interpreter.create_origins, ["http://gateway:8000"])
        self.assertEqual(
            interpreter.create_authorities[0].api_token_ref.reference_id,
            "secret://cloudflare/openj92/api-token",
        )
        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(
            interpreter.create_allocation_names,
            ["cpk-gateway-001-c0303ba7369e"],
        )
        descriptor = outcome.evidence.descriptor()
        self.assertEqual(descriptor["provider_kind"], "cloudflare")
        self.assertEqual(descriptor["ingress_id"], "gateway-001")
        self.assertIs(descriptor["connector_material_recorded"], True)
        self.assertNotIn("secret://", repr(descriptor).lower())
        self.assertNotIn("eyj-cloudflare", repr(descriptor).lower())
        self.assertNotIn("bearer-value", repr(descriptor).lower())

        with self.unit_of_work() as unit_of_work:
            resource = unit_of_work.stores.ingress_resources.get_cloudflare(
                "workspace-a",
                "gateway-001",
            )
            generated = unit_of_work.stores.generated_ingress_secrets.get_by_source(
                workspace_id="workspace-a",
                purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                source_run_id="run-a",
                source_activity_id="allocate-gateway",
                source_event_id="event-001",
            )

        self.assertEqual(resource.tunnel_id, "tunnel-001")
        self.assertEqual(resource.tunnel_name, "cpk-gateway-001-c0303ba7369e")
        self.assertEqual(resource.dns_record_id, "dns-001")
        self.assertEqual(
            generated.secret_ref.reference_id,
            (
                "secret://generated/ingress/b64-d29ya3NwYWNlLWE/"
                "b64-Y2xvdWRmbGFyZWQtdHVubmVsLXRva2Vu/b64-cnVuLWE/"
                "b64-YWxsb2NhdGUtZ2F0ZXdheQ/b64-ZXZlbnQtMDAx"
            ),
        )
        self.assertEqual(
            recorder.resolve_generated_secret(generated.secret_ref).reveal(),
            "eyJ-cloudflare-tunnel-token-bearer-value",
        )

    def test_allocate_public_ingress_uses_unique_tunnel_names_for_distinct_runs(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            generated_secret_recorder=InMemoryGeneratedSecretRecorder(),
            clock=lambda: "2026-07-28T08:01:00Z",
        )

        first = adapter.execute(self.context())
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.ingress_resources.mark_removed(
                "workspace-a",
                "gateway-001",
                removed_at="2026-07-28T08:02:00Z",
                removed_by_run_id="run-a",
            )
            unit_of_work.commit()
        second = adapter.execute(
            self.context(run_id="run-b", intent_event_id="event-002")
        )

        self.assertEqual(first.kind.name, "SUCCEEDED")
        self.assertEqual(second.kind.name, "SUCCEEDED")
        self.assertEqual(len(interpreter.create_allocation_names), 2)
        self.assertNotEqual(
            interpreter.create_allocation_names[0],
            interpreter.create_allocation_names[1],
        )
        for allocation_name in interpreter.create_allocation_names:
            self.assertRegex(allocation_name, r"^cpk-gateway-001-[0-9a-f]{12}$")

    def test_remove_public_ingress_marks_resource_removed_around_provider_io(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            generated_secret_recorder=InMemoryGeneratedSecretRecorder(),
            clock=lambda: "2026-07-28T08:02:00Z",
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.ingress_resources.record_cloudflare(
                self.cloudflare_resource()
            )
            unit_of_work.commit()

        outcome = adapter.execute(
            self.context(
                activity_id="remove-gateway",
                operation=RemovePublicIngress(PublicIngressActivityTarget("gateway-001")),
                base_graph=self.graph(),
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(interpreter.teardown_active_counts, [0])
        self.assertEqual(interpreter.teardown_resources[0].status.name, "REMOVING")
        with self.unit_of_work() as unit_of_work:
            history = unit_of_work.stores.ingress_resources.list_cloudflare(
                "workspace-a"
            )
        self.assertEqual(history[0].status, OwnedIngressResourceStatus.REMOVED)
        self.assertEqual(history[0].removed_at, "2026-07-28T08:02:00Z")
        self.assertEqual(history[0].removed_by_run_id, "run-a")

    def test_remove_public_ingress_marks_uncertain_when_provider_fails(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.fail_teardown = True
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            generated_secret_recorder=InMemoryGeneratedSecretRecorder(),
            clock=lambda: "2026-07-28T08:02:00Z",
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.ingress_resources.record_cloudflare(
                self.cloudflare_resource()
            )
            unit_of_work.commit()

        outcome = adapter.execute(
            self.context(
                activity_id="remove-gateway",
                operation=RemovePublicIngress(PublicIngressActivityTarget("gateway-001")),
                base_graph=self.graph(),
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertEqual(interpreter.teardown_active_counts, [0])
        with self.unit_of_work() as unit_of_work:
            resource = unit_of_work.stores.ingress_resources.get_cloudflare(
                "workspace-a",
                "gateway-001",
            )
        self.assertEqual(resource.status, OwnedIngressResourceStatus.UNCERTAIN)

    def graph(self) -> DeploymentGraph:
        return DeploymentGraph(
            "ingress-test",
            nodes={
                "gateway": Node(
                    node_id="gateway",
                    block_family=BlockFamily.PROXY,
                    block_spec=BlockSpec("gateway"),
                    kind="container",
                    runtime_id="docker-a",
                    sockets=BlockSockets(
                        providers=(ProviderSocket("control", Protocol.HTTP),)
                    ),
                    endpoints={
                        "control": Endpoint(
                            LiteralAddress("http://gateway:8000"),
                            Protocol.HTTP,
                        )
                    },
                ),
                "cloudflared": Node(
                    node_id="cloudflared",
                    block_family=BlockFamily.PROXY,
                    block_spec=BlockSpec("cloudflared"),
                    kind="container",
                    runtime_id="docker-a",
                    sockets=BlockSockets(),
                ),
            },
            runtimes={
                "docker-a": RuntimeRecord(
                    "docker-a",
                    RuntimeKind.DOCKER,
                    children=("gateway", "cloudflared"),
                )
            },
            public_ingresses=(
                NamedPublicIngress(
                    ingress_id="gateway-001",
                    authority_ref=IngressAuthorityReference("openj92-public-ingress"),
                    target=PublicIngressTarget("gateway", "control"),
                    connector_node_id="cloudflared",
                    hostname="cpk-gateway-001.openj92.dev",
                ),
            ),
        )

    def cloudflare_resource(self) -> CloudflareOwnedIngressResource:
        return CloudflareOwnedIngressResource(
            workspace_id="workspace-a",
            runtime_id="docker-a",
            ingress_id="gateway-001",
            authority_ref=IngressAuthorityReference("openj92-public-ingress"),
            provider_kind=IngressAuthorityProviderKind.CLOUDFLARE,
            tunnel_name="cpk-gateway-001",
            tunnel_id="tunnel-001",
            dns_record_id="dns-001",
            hostname="cpk-gateway-001.openj92.dev",
            zone_id="zone-openj92",
            lifecycle=self.graph().public_ingresses[0].lifecycle,
            created_at="2026-07-28T08:01:00Z",
            observed_at="2026-07-28T08:01:00Z",
            source_run_id="run-a",
            source_activity_id="allocate-gateway",
            source_event_id="event-001",
        )

    def context(
        self,
        *,
        activity_id: str = "allocate-gateway",
        run_id: str = "run-a",
        intent_event_id: str = "event-001",
        operation: object | None = None,
        base_graph: DeploymentGraph | None = None,
        desired_graph: DeploymentGraph | None = None,
    ) -> ActivityRealizationContext:
        graph = self.graph()
        operation = operation or AllocatePublicIngress(
            PublicIngressActivityTarget("gateway-001")
        )
        activity = PlannedActivity(
            ActivityId(activity_id),
            operation,
        )
        return ActivityRealizationContext(
            activity=activity,
            request=ExecutionRequestRecord(
                ExecutionRequestIdentity(
                    "request-a",
                    "workspace-a",
                    "session-a",
                    "plan-a",
                ),
                ExecutionRequestStatus.CLAIMED,
                "operator-a",
                "2026-07-28T08:00:00Z",
                "approval-request-a",
                "approval-decision-a",
                ExecutionIdempotency("execute-a", "fingerprint-a"),
                claim=ClaimIdentity(
                    "worker-a",
                    "2026-07-28T08:00:30Z",
                    "2026-07-28T08:10:30Z",
                ),
            ),
            run=ActivityRunRecord(
                run_id,
                "plan-a",
                AdmittedRun("request-a"),
                RetryIdentity(1),
                ActivityRunStatus.RUNNING,
                "2026-07-28T08:00:45Z",
                started_at="2026-07-28T08:00:50Z",
            ),
            plan_record=ActivityPlanRecord(
                "plan-a",
                "session-a",
                "graph-current",
                "graph-desired",
                ActivityPlanStatus.PLANNED,
                "2026-07-28T08:00:10Z",
                ActivityPlan((activity,)),
            ),
            base_graph=GraphVersionRecord.from_graph(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph=base_graph or DeploymentGraph("empty"),
                created_by="operator-a",
                created_at="2026-07-28T08:00:00Z",
            ),
            desired_graph=GraphVersionRecord.from_graph(
                graph_id="graph-desired",
                workspace_id="workspace-a",
                version=2,
                graph=desired_graph or graph,
                created_by="operator-a",
                created_at="2026-07-28T08:00:05Z",
            ),
            registered_products=(),
            authority=ExecutionWorkerAuthority(
                "worker-a",
                (PolicyScope.EXECUTION_OPERATE,),
            ),
            intent_event=ActivityEventRecord(
                intent_event_id,
                run_id,
                1,
                ActivityEventKind.STEP_STARTED,
                "2026-07-28T08:01:00Z",
                activity_id=activity_id,
            ),
        )


if __name__ == "__main__":
    unittest.main()
