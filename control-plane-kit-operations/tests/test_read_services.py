from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone

import psycopg

from control_plane_kit_core.algebra import (
    BlockSockets,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
    RequirementSocket,
    SocketConnection,
)
from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.planning import ActivityPlan, RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    ProbeKind,
    ProbeOutcome,
)
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    ProductRuntimeContract,
    instantiate_product,
)
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    NamedPublicIngress,
    PublicIngressTarget,
)
from control_plane_kit_core.topology import DeploymentGraph, compile_topology
from control_plane_kit_core.types import Protocol, SocketBinding, WorkspaceLifecycle
from control_plane_kit_operations import (
    ActivityPlanRecord,
    ActivityPlanStatus,
    ApprovalRequestRecord,
    BoundedEvidence,
    GraphVersionRecord,
    InstanceReadService,
    ObservationFreshness,
    ObservationFreshnessPolicy,
    ObservationRecord,
    ObservationStatus,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    ReadModelError,
    WorkspaceRecord,
)
from control_plane_kit_operations.postgres import (
    PostgresStoreBundle,
    PostgresUnitOfWork,
    install_schema,
)


class InstanceReadServiceTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def service(self) -> InstanceReadService:
        stores = PostgresStoreBundle(self.connection)
        return InstanceReadService(
            workspace_store=stores.workspaces,
            graph_topology_store=stores.graphs,
            activity_history_store=stores.activity_history,
            execution_store=stores.execution,
            observed_state_store=stores.observed_state,
            clock=lambda: datetime(2026, 7, 22, 13, 5, tzinfo=timezone.utc),
            observation_freshness=ObservationFreshnessPolicy(),
        )

    def test_workspace_and_graph_reads_are_redacted(self) -> None:
        self.seed_graphs()
        model = self.service().workspace("workspace-a").descriptor()

        self.assertEqual(model["workspace"]["workspace_id"], "workspace-a")
        self.assertIsNotNone(
            model["workspace"]["current_realized_projection_id"]
        )
        self.assertIsNotNone(
            model["workspace"]["desired_realized_projection_id"]
        )
        self.assertEqual(model["workspace"]["desired_graph_revision"], 1)
        self.assertEqual(model["current_graph"]["graph_id"], "graph-current")
        self.assertEqual(
            model["current_graph"]["authored_graph_id"],
            "graph-current",
        )
        self.assertEqual(
            model["current_graph"]["realized_projection_id"],
            model["workspace"]["current_realized_projection_id"],
        )
        self.assertEqual(
            model["desired_graph"]["authored_graph_id"],
            "graph-desired",
        )
        self.assertEqual(
            model["desired_graph"]["realized_projection_id"],
            model["workspace"]["desired_realized_projection_id"],
        )
        metadata = model["current_graph"]["graph_descriptor"]["nodes"]["hello"]["metadata"]
        self.assertEqual(metadata["api_token"], "<redacted>")
        self.assertEqual(metadata["public_note"], "visible")

    def test_operator_graph_projects_socket_contracts(self) -> None:
        self.seed_graphs()
        descriptor = self.service().operator_graph("workspace-a").descriptor()
        operator = descriptor["operator_graph"]

        self.assertEqual(operator["name"], "current")
        self.assertEqual(operator["nodes"][0]["providers"][0]["name"], "http")
        self.assertEqual(
            operator["nodes"][0]["providers"][0]["protocol"]["application"],
            "http",
        )

    def test_open_sessions_are_paged_and_unknown_workspace_fails_readably(self) -> None:
        self.seed_activity()
        page = self.service().open_sessions(
            "workspace-a",
            limit=1,
            offset=0,
        ).descriptor()

        self.assertEqual(page["total"], 1)
        self.assertFalse(page["has_more"])
        self.assertEqual(page["items"][0]["session_id"], "session-a")
        with self.assertRaisesRegex(ReadModelError, "missing workspace 'missing'"):
            self.service().open_sessions("missing")
        with self.assertRaisesRegex(ReadModelError, "limit must not exceed 100"):
            self.service().open_sessions("workspace-a", limit=101)

    def test_activity_timeline_redacts_action_payloads_and_lists_pending_approvals(self) -> None:
        self.seed_activity()
        timeline = self.service().activity_timeline("workspace-a").descriptor()
        approval_page = self.service().pending_approvals("workspace-a").descriptor()

        action = timeline["sessions"][0]["actions"][0]
        self.assertEqual(action["payload"]["api_token"], "<redacted>")
        self.assertEqual(approval_page["items"][0]["request_id"], "approval-a")
        self.assertEqual(approval_page["items"][0]["state"], "pending")

    def test_plan_detail_uses_pinned_graph_truth_and_core_plan_codec(self) -> None:
        self.seed_activity()
        detail = self.service().plan_detail("workspace-a", "plan-a").descriptor()
        plan = detail["plan"]

        self.assertEqual(plan["plan_id"], "plan-a")
        self.assertIsNotNone(plan["base_realized_projection_id"])
        self.assertIsNotNone(plan["desired_realized_projection_id"])
        self.assertEqual(plan["desired_graph_revision"], 1)
        self.assertEqual(plan["payload"]["schema"], "control-plane-kit.activity-plan")
        self.assertEqual(plan["risk_summary"]["ready_for_execution"], True)
        self.assertEqual(plan["recovery"]["mode"], "reverse-transition")

    def test_approval_detail_joins_request_to_pinned_plan_review_context(self) -> None:
        self.seed_activity()
        detail = self.service().approval_detail(
            "workspace-a",
            "approval-a",
        ).descriptor()

        approval = detail["approval"]
        plan = detail["plan"]
        self.assertEqual(approval["request_id"], "approval-a")
        self.assertEqual(approval["state"], "pending")
        self.assertEqual(approval["required_scope"], "plan:approve")
        self.assertEqual(plan["plan_id"], "plan-a")
        self.assertEqual(plan["payload"]["schema"], "control-plane-kit.activity-plan")
        self.assertEqual(plan["risk_summary"]["ready_for_execution"], True)
        self.assertEqual(plan["recovery"]["mode"], "reverse-transition")

    def test_approval_detail_handles_public_ingress_graph_truth(self) -> None:
        self.seed_activity_with_public_ingress()
        detail = self.service().approval_detail(
            "workspace-a",
            "approval-public-ingress",
        ).descriptor()

        plan = detail["plan"]
        self.assertEqual(plan["plan_id"], "plan-public-ingress")
        self.assertEqual(plan["recovery"]["mode"], "reverse-transition")
        self.assertEqual(
            plan["recovery"]["source_graph_name"],
            "public-ingress-desired",
        )
        self.assertEqual(plan["recovery"]["target_graph_name"], "public-ingress-base")

    def test_observed_state_is_latest_per_subject_and_does_not_rewrite_graph_truth(self) -> None:
        self.seed_graphs()
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.observed_state.put(
                observation(
                    "obs-old",
                    status=ObservationStatus.STARTING,
                    observed_at="2026-07-22T13:00:00Z",
                )
            )
            unit_of_work.stores.observed_state.put(
                observation(
                    "obs-new",
                    status=ObservationStatus.HEALTHY,
                    observed_at="2026-07-22T13:01:00Z",
                    evidence={"url": "http://internal:8080", "message": "ok"},
                )
            )
            unit_of_work.stores.observed_state.put(
                observation(
                    "obs-other",
                    subject_id="worker",
                    status=ObservationStatus.UNKNOWN,
                    observed_at="2026-07-22T12:00:00Z",
                    graph_id="graph-old",
                )
            )
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            unit_of_work.commit()

        model = self.service().observed_state("workspace-a").descriptor()

        self.assertEqual(workspace.current_graph_id, "graph-current")
        self.assertEqual(
            [item["observation_id"] for item in model["observations"]],
            ["obs-new", "obs-other"],
        )
        self.assertEqual(model["observations"][0]["freshness"], "fresh")
        self.assertEqual(model["observations"][0]["payload"]["url"], "<redacted>")
        self.assertEqual(model["observations"][1]["freshness"], "stale")
        self.assertEqual(model["observations"][1]["stale_reason"], "graph-changed")

    def test_explicit_stale_observation_stays_stale(self) -> None:
        self.seed_graphs()
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.observed_state.put(
                observation(
                    "obs-stale",
                    status=ObservationStatus.UNKNOWN,
                    freshness=ObservationFreshness.STALE,
                )
            )
            unit_of_work.commit()

        model = self.service().observed_state("workspace-a").descriptor()

        self.assertEqual(model["observations"][0]["freshness"], "stale")
        self.assertEqual(model["observations"][0]["stale_reason"], "recorded-stale")

    def test_control_surface_reads_declared_nodes_without_endpoint_leakage(self) -> None:
        self.seed_graphs()
        surface = self.service().control_surface("workspace-a").descriptor()

        self.assertEqual(surface["graph_id"], "graph-current")
        self.assertEqual(surface["nodes"][0]["node_id"], "hello")
        self.assertNotIn("capabilities", surface["nodes"][0]["metadata"])

    def seed_graphs(self) -> None:
        current = product_graph("current")
        desired = DeploymentGraph("desired")
        current_descriptor = dict(
            GraphVersionRecord.from_graph(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph=current,
                created_by="operator-a",
                created_at="2026-07-22T10:00:00Z",
            ).graph_descriptor
        )
        current_descriptor["nodes"]["hello"]["metadata"]["api_token"] = "do-not-disclose"
        current_descriptor["nodes"]["hello"]["metadata"]["public_note"] = "visible"
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(
                    workspace_id="workspace-a",
                    name="Demo",
                    lifecycle=WorkspaceLifecycle.RUNNING,
                )
            )
            unit_of_work.stores.graphs.save(
                GraphVersionRecord(
                    graph_id="graph-current",
                    workspace_id="workspace-a",
                    version=1,
                    graph_descriptor=current_descriptor,
                    created_by="operator-a",
                    created_at="2026-07-22T10:00:00Z",
                )
            )
            unit_of_work.stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-desired",
                    workspace_id="workspace-a",
                    version=2,
                    graph=desired,
                    created_by="operator-a",
                    created_at="2026-07-22T10:01:00Z",
                )
            )
            unit_of_work.stores.workspaces.set_current_graph(
                "workspace-a",
                "graph-current",
            )
            unit_of_work.stores.workspaces.set_desired_graph(
                "workspace-a",
                "graph-desired",
            )
            unit_of_work.commit()

    def seed_activity(self) -> None:
        self.seed_graphs()
        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            unit_of_work.stores.activity_history.add_session(
                OperationSessionRecord(
                    session_id="session-a",
                    workspace_id="workspace-a",
                    actor_id="operator-a",
                    title="Demo deploy",
                    status=OperationSessionStatus.OPEN,
                    created_at="2026-07-22T11:00:00Z",
                )
            )
            unit_of_work.stores.activity_history.add_action(
                OperationActionRecord(
                    action_id="action-a",
                    session_id="session-a",
                    ordinal=1,
                    action_type=OperatorCommandKind.SET_DESIRED_GRAPH,
                    actor_id="operator-a",
                    payload={"api_token": "do-not-disclose", "note": "ok"},
                    created_at="2026-07-22T11:01:00Z",
                )
            )
            unit_of_work.stores.activity_history.add_plan(
                ActivityPlanRecord(
                    plan_id="plan-a",
                    session_id="session-a",
                    base_graph_id="graph-current",
                    desired_graph_id="graph-desired",
                    status=ActivityPlanStatus.PLANNED,
                    created_at="2026-07-22T11:02:00Z",
                    plan=ActivityPlan(()),
                    base_realized_projection_id=(
                        workspace.current_realized_projection_id
                    ),
                    desired_realized_projection_id=(
                        workspace.desired_realized_projection_id
                    ),
                    desired_graph_revision=workspace.desired_graph_revision,
                )
            )
            unit_of_work.stores.activity_history.add_approval_request(
                ApprovalRequestRecord(
                    request_id="approval-a",
                    session_id="session-a",
                    plan_id="plan-a",
                    requested_by="operator-a",
                    requested_at="2026-07-22T11:03:00Z",
                    required_scope=PolicyScope.PLAN_APPROVE,
                    max_risk=RiskLevel.INFORMATIONAL,
                    destructive=False,
                )
            )
            unit_of_work.commit()

    def seed_activity_with_public_ingress(self) -> None:
        base = DeploymentGraph("public-ingress-base")
        desired = public_ingress_graph("public-ingress-desired")
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(
                    workspace_id="workspace-a",
                    name="Public ingress demo",
                    lifecycle=WorkspaceLifecycle.RUNNING,
                )
            )
            unit_of_work.stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-public-base",
                    workspace_id="workspace-a",
                    version=1,
                    graph=base,
                    created_by="operator-a",
                    created_at="2026-07-22T10:00:00Z",
                )
            )
            unit_of_work.stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-public-desired",
                    workspace_id="workspace-a",
                    version=2,
                    graph=desired,
                    created_by="operator-a",
                    created_at="2026-07-22T10:01:00Z",
                )
            )
            unit_of_work.stores.workspaces.set_current_graph(
                "workspace-a",
                "graph-public-base",
            )
            unit_of_work.stores.workspaces.set_desired_graph(
                "workspace-a",
                "graph-public-desired",
            )
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            unit_of_work.stores.activity_history.add_session(
                OperationSessionRecord(
                    session_id="session-public-ingress",
                    workspace_id="workspace-a",
                    actor_id="operator-a",
                    title="Public ingress deploy",
                    status=OperationSessionStatus.OPEN,
                    created_at="2026-07-22T11:00:00Z",
                )
            )
            unit_of_work.stores.activity_history.add_plan(
                ActivityPlanRecord(
                    plan_id="plan-public-ingress",
                    session_id="session-public-ingress",
                    base_graph_id="graph-public-base",
                    desired_graph_id="graph-public-desired",
                    status=ActivityPlanStatus.PLANNED,
                    created_at="2026-07-22T11:02:00Z",
                    plan=ActivityPlan(()),
                    base_realized_projection_id=(
                        workspace.current_realized_projection_id
                    ),
                    desired_realized_projection_id=(
                        workspace.desired_realized_projection_id
                    ),
                    desired_graph_revision=workspace.desired_graph_revision,
                )
            )
            unit_of_work.stores.activity_history.add_approval_request(
                ApprovalRequestRecord(
                    request_id="approval-public-ingress",
                    session_id="session-public-ingress",
                    plan_id="plan-public-ingress",
                    requested_by="operator-a",
                    requested_at="2026-07-22T11:03:00Z",
                    required_scope=PolicyScope.PLAN_APPROVE,
                    max_risk=RiskLevel.INFORMATIONAL,
                    destructive=False,
                )
            )
            unit_of_work.commit()


def product_graph(name: str) -> object:
    product = ContainerServerProduct(
        identity=ProductIdentity("cpk-servers", "hello-server", 1),
        image=OciImageReference(
            "ghcr.io",
            "openj92/control-plane-kit-servers/hello-server",
            "sha256:" + "a" * 64,
            tag="v1",
        ),
        runtime_contract=ProductRuntimeContract(
            sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
        ),
        display_name="Hello server",
        description="Server product used for read projection tests.",
    )
    document = ProductDescriptorCodec().encode_document(product)
    block = instantiate_product(
        document.product,
        "hello",
        ProductInstanceConfiguration(),
    )
    return compile_topology(DeploymentTopology(name, DockerRuntime(children=(block,))))


def public_ingress_graph(name: str) -> object:
    product = ContainerServerProduct(
        identity=ProductIdentity("cpk-servers", "hello-server", 1),
        image=OciImageReference(
            "ghcr.io",
            "openj92/control-plane-kit-servers/hello-server",
            "sha256:" + "a" * 64,
            tag="v1",
        ),
        runtime_contract=ProductRuntimeContract(
            sockets=BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),))
        ),
        display_name="Hello server",
        description="Server product used for public ingress read projection tests.",
    )
    gateway = ContainerServerProduct(
        identity=ProductIdentity("cpk-servers", "cpk-local-gateway", 1),
        image=OciImageReference(
            "ghcr.io",
            "openj92/control-plane-kit-servers/cpk-local-gateway",
            "sha256:" + "b" * 64,
            tag="v1",
        ),
        runtime_contract=ProductRuntimeContract(
            sockets=BlockSockets(
                requirements=(
                    RequirementSocket(
                        "target-http",
                        Protocol.HTTP,
                        (),
                        required=False,
                        binding=SocketBinding.RUNTIME_CONTROL,
                    ),
                ),
                providers=(ProviderSocket("control", Protocol.HTTP),),
            )
        ),
        display_name="Gateway",
        description="Gateway product used for public ingress read projection tests.",
    )
    connector = ContainerServerProduct(
        identity=ProductIdentity("cpk-servers", "cloudflared-connector", 1),
        image=OciImageReference(
            "docker.io",
            "cloudflare/cloudflared",
            "sha256:" + "c" * 64,
            tag="2026.6.1",
        ),
        runtime_contract=ProductRuntimeContract(),
        display_name="cloudflared-connector",
        description="Connector product used for public ingress read projection tests.",
    )
    hello = instantiate_product(
        ProductDescriptorCodec().encode_document(product).product,
        "hello",
        ProductInstanceConfiguration(),
    )
    gateway_node = instantiate_product(
        ProductDescriptorCodec().encode_document(gateway).product,
        "gateway",
        ProductInstanceConfiguration(),
    )
    cloudflared = instantiate_product(
        ProductDescriptorCodec().encode_document(connector).product,
        "cloudflared-gateway",
        ProductInstanceConfiguration(),
    )
    return compile_topology(
        DeploymentTopology(
            name,
            DockerRuntime(
                children=(
                    hello,
                    gateway_node,
                    cloudflared,
                    SocketConnection("hello", "internal", "gateway", "target-http"),
                )
            ),
            public_ingresses=(
                NamedPublicIngress(
                    ingress_id="gateway-public",
                    authority_ref=IngressAuthorityReference("openj92-cloudflare"),
                    target=PublicIngressTarget("gateway", "control"),
                    connector_node_id="cloudflared-gateway",
                    hostname="cpk-gateway-001.openj92.dev",
                ),
            ),
        )
    )


def observation(
    observation_id: str,
    *,
    subject_id: str = "hello",
    status: ObservationStatus = ObservationStatus.HEALTHY,
    observed_at: str = "2026-07-22T13:00:00Z",
    evidence: dict[str, object] | None = None,
    freshness: ObservationFreshness = ObservationFreshness.FRESH,
    graph_id: str = "graph-current",
) -> ObservationRecord:
    return ObservationRecord(
        observation_id=observation_id,
        workspace_id="workspace-a",
        subject_id=subject_id,
        status=status,
        observed_at=observed_at,
        evidence=BoundedEvidence.from_mapping(evidence),
        freshness=freshness,
        graph_id=graph_id,
        probe_kind=ProbeKind.APPLICATION_HEALTH,
        probe_outcome=ProbeOutcome.HEALTHY,
        endpoint_context=EndpointContext.RUNTIME_PRIVATE,
    )


if __name__ == "__main__":
    unittest.main()
