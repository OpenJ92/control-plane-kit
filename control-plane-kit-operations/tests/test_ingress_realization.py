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
    CloudflareZoneIngressAuthority,
    GeneratedSecretPurpose,
    InMemoryGeneratedSecretRecorder,
    IngressAuthorityProviderKind,
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
        self.create_origins: list[str] = []
        self.create_authorities: list[CloudflareZoneIngressAuthority] = []

    def create(
        self,
        ingress: NamedPublicIngress,
        *,
        authority: CloudflareZoneIngressAuthority,
        origin_service_url: str,
    ) -> FakeIngressAllocation:
        self.create_active_counts.append(self.tracker.active)
        self.create_origins.append(origin_service_url)
        self.create_authorities.append(authority)
        return FakeIngressAllocation(hostname=ingress.hostname)

    def teardown(
        self,
        *,
        authority: CloudflareZoneIngressAuthority,
        resources: object,
    ) -> None:
        del authority, resources


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

    def context(self) -> ActivityRealizationContext:
        graph = DeploymentGraph(
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
        activity = PlannedActivity(
            ActivityId("allocate-gateway"),
            AllocatePublicIngress(PublicIngressActivityTarget("gateway-001")),
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
                "run-a",
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
                graph=DeploymentGraph("empty"),
                created_by="operator-a",
                created_at="2026-07-28T08:00:00Z",
            ),
            desired_graph=GraphVersionRecord.from_graph(
                graph_id="graph-desired",
                workspace_id="workspace-a",
                version=2,
                graph=graph,
                created_by="operator-a",
                created_at="2026-07-28T08:00:05Z",
            ),
            registered_products=(),
            authority=ExecutionWorkerAuthority(
                "worker-a",
                (PolicyScope.EXECUTION_OPERATE,),
            ),
            intent_event=ActivityEventRecord(
                "event-001",
                "run-a",
                1,
                ActivityEventKind.STEP_STARTED,
                "2026-07-28T08:01:00Z",
                activity_id="allocate-gateway",
            ),
        )


if __name__ == "__main__":
    unittest.main()
