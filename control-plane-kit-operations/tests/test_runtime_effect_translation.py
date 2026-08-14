from __future__ import annotations

import json
import unittest
from dataclasses import replace

from control_plane_kit_core.algebra import (
    BlockSockets,
    BlockSpec,
    ProviderSocket,
    RequirementSocket,
)
from control_plane_kit_core.environment import (
    PublicStaticEnvironmentBinding,
    SocketDerivedEnvironmentBinding,
)
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
    StopRuntime,
    RuntimeTarget,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    NamedPublicIngress,
    PublicIngressLifecycle,
    PublicIngressTarget,
)
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductReference,
    ProductRuntimeContract,
    ProviderRuntimePort,
)
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityReference,
)
from control_plane_kit_core.runtime_effects import ImagePullAuthority
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_core.topology import DeploymentGraph, Edge, Node, RuntimeRecord
from control_plane_kit_core.types import BlockFamily, Protocol, RuntimeKind, SocketBinding
from control_plane_kit_core.verification import HttpCheck, VerificationContract
from control_plane_kit_operations.coordinator import ActivityRealizationContext
from control_plane_kit_operations.ingress_authorities import (
    CloudflareOwnedIngressResource,
    CloudflareZoneIngressAuthority,
    GeneratedIngressSecretReference,
    GeneratedSecretPurpose,
    IngressAuthorityProviderKind,
    OwnedIngressResourceStatus,
    RegisteredIngressAuthority,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.products import (
    InlineDescriptorSource,
    RegisteredImagePullAuthority,
    RegisteredProduct,
)
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
    OperationsRecordError,
    RealizedGraphProjectionRecord,
    RetryIdentity,
)
from control_plane_kit_operations.runtime_effects import runtime_effect_request_for_context
from control_plane_kit_operations.runtime_authorities import (
    RegisteredRuntimeAuthorityDelivery,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


class RuntimeEffectTranslationTests(unittest.TestCase):
    def test_context_translates_to_core_runtime_effect_request(self) -> None:
        context = _context()

        request = runtime_effect_request_for_context(context)

        self.assertEqual(request.effect_id, "event-started")
        self.assertEqual(request.runtime_kind, RuntimeKind.DOCKER)
        self.assertIsNone(request.authority_ref)
        self.assertEqual(request.source.workspace_id, "workspace-a")
        self.assertEqual(
            type(request.source.run_id).__module__,
            "control_plane_kit_core.operations.run_identity",
        )
        self.assertEqual(request.source.run_id.value, "run-a")
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
        self.assertEqual(
            request.products[0].socket_environment,
            (
                SocketDerivedEnvironmentBinding(
                    "UPSTREAM_URL",
                    "http://upstream:8080",
                    "upstream.internal->api.upstream",
                ),
            ),
        )
        self.assertEqual(
            request.products[0].public_environment,
            (
                PublicStaticEnvironmentBinding(
                    "HELLO_MESSAGE",
                    "Hello from selected instance",
                ),
            ),
        )
        self.assertIsNone(request.products[0].pull_authority)

    def test_malformed_retained_run_fails_at_authoritative_record_boundary(
        self,
    ) -> None:
        with self.assertRaises(OperationsRecordError) as captured:
            _context(run_id="retained/run-direct-canary")

        error = captured.exception
        self.assertIs(type(error), OperationsRecordError)
        self.assertEqual(str(error), "run_id is malformed")
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        self.assertNotIn("retained/run-direct-canary", rendered)

    def test_runtime_material_uses_graph_node_verification(self) -> None:
        descriptor_verification = VerificationContract(
            (
                HttpCheck(
                    check_id="ready",
                    provider_socket="http",
                    path="/health/ready",
                ),
            )
        )
        registered = _registered_product(verification=descriptor_verification)
        graph = _graph()
        node = graph.nodes["api"]
        graph = graph.update_node(
            replace(
                node,
                block_spec=replace(
                    node.block_spec,
                    verification=VerificationContract(),
                ),
                metadata={
                    "product_identity": registered.reference.identity.key,
                    "product_descriptor_digest": (
                        registered.reference.descriptor_sha256.value
                    ),
                },
            )
        )
        context = _context(
            desired_graph=graph,
            registered_products=(registered,),
        )

        request = runtime_effect_request_for_context(context)

        self.assertEqual(
            registered.descriptor_document.product.runtime_contract.verification,
            descriptor_verification,
        )
        self.assertEqual(
            request.products[0].product.runtime_contract.verification,
            VerificationContract(),
        )

    def test_context_selects_matching_pull_authority_without_credentials(self) -> None:
        context = _context(
            pull_authorities=(
                _registered_pull_authority(
                    repository="openj92/control-plane-kit-servers"
                ),
            )
        )

        request = runtime_effect_request_for_context(context)

        self.assertIsNotNone(request.products[0].pull_authority)
        assert request.products[0].pull_authority is not None
        self.assertEqual(
            request.products[0].pull_authority.credential_reference.reference_id,
            "secret://local/workspace-a/ghcr-read-token",
        )
        self.assertNotIn("ghp_", repr(request.descriptor()))

    def test_context_prefers_most_specific_matching_pull_authority(self) -> None:
        context = _context(
            pull_authorities=(
                _registered_pull_authority(repository=None),
                _registered_pull_authority(
                    repository="openj92/control-plane-kit-servers/hello-server",
                    credential_reference="secret://local/workspace-a/hello-token",
                ),
            )
        )

        request = runtime_effect_request_for_context(context)

        assert request.products[0].pull_authority is not None
        self.assertEqual(
            request.products[0].pull_authority.credential_reference.reference_id,
            "secret://local/workspace-a/hello-token",
        )

    def test_runtime_teardown_uses_base_graph_to_resolve_runtime_kind(self) -> None:
        activity = PlannedActivity(
            ActivityId("activity-stop-runtime"),
            StopRuntime(RuntimeTarget("docker")),
        )
        context = _context(
            activity=activity,
            desired_graph=DeploymentGraph("empty"),
        )

        request = runtime_effect_request_for_context(context)

        self.assertEqual(request.runtime_kind, RuntimeKind.DOCKER)
        self.assertEqual(request.operation, StopRuntime(RuntimeTarget("docker")))
        self.assertEqual(request.products, ())

    def test_context_carries_runtime_authority_reference_from_graph_to_request(self) -> None:
        graph = _graph(
            authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
        )
        context = _context(desired_graph=graph)

        request = runtime_effect_request_for_context(context)

        self.assertEqual(
            request.authority_ref,
            RuntimeAuthorityReference("mac-mini-docker"),
        )
        self.assertEqual(
            request.descriptor()["authority_ref"],
            {"reference_id": "mac-mini-docker"},
        )
        self.assertNotIn("tcp://", repr(request.descriptor()))

    def test_context_carries_matching_authority_delivery_without_socket_material(self) -> None:
        delivery = RegisteredRuntimeAuthorityDelivery.from_delivery(
            workspace_id="workspace-a",
            delivery=RuntimeAuthorityAccessDelivery(
                RuntimeAuthorityReference("local-docker"),
                RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
            ),
            admitted_by="operator-a",
            admitted_at="2026-07-22T10:03:00Z",
        )
        graph = _graph(authority_ref=RuntimeAuthorityReference("local-docker"))
        context = _context(
            desired_graph=graph,
            runtime_authority_deliveries=(delivery,),
        )

        request = runtime_effect_request_for_context(context)

        self.assertEqual(request.authority_ref, RuntimeAuthorityReference("local-docker"))
        self.assertEqual(
            tuple(value.delivery_kind for value in request.authority_deliveries),
            (RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,),
        )
        self.assertNotIn("/var/run/docker.sock", repr(request.descriptor()))
        self.assertNotIn("unix://", repr(request.descriptor()))

    def test_context_translates_only_compiled_socket_bound_secret_deliveries(
        self,
    ) -> None:
        delivery = SecretEnvironmentDelivery(
            "DATABASE_PASSWORD",
            SecretReference("secret://workspace-a/database/password"),
            SecretUseIntent.POSTGRES_PASSWORD,
        )
        requirement = RequirementSocket(
            "database",
            Protocol.POSTGRES,
            ("DATABASE_URL",),
            required=False,
            secret_deliveries=(delivery,),
        )
        registered = _registered_product(
            name="database-client",
            public_environment=(),
            requirements=(requirement,),
        )
        reference = registered.reference

        for active_deliveries in ((), (delivery,)):
            with self.subTest(active=bool(active_deliveries)):
                graph = _graph()
                graph = graph.update_node(
                    replace(
                        graph.node("api"),
                        sockets=BlockSockets(
                            requirements=(requirement,),
                            providers=(ProviderSocket("http", Protocol.HTTP),),
                        ),
                        metadata={
                            "product_identity": reference.identity.key,
                            "product_descriptor_digest": (
                                reference.descriptor_sha256.value
                            ),
                        },
                        secret_deliveries=active_deliveries,
                    )
                )

                request = runtime_effect_request_for_context(
                    _context(
                        desired_graph=graph,
                        registered_products=(registered,),
                    )
                )

                self.assertEqual(
                    request.products[0].product.runtime_contract.secret_deliveries,
                    active_deliveries,
                )
                self.assertEqual(
                    request.products[0]
                    .product.runtime_contract.sockets.requirement("database")
                    .secret_deliveries,
                    (delivery,),
                )

    def test_context_does_not_carry_unrelated_authority_delivery(self) -> None:
        delivery = RegisteredRuntimeAuthorityDelivery.from_delivery(
            workspace_id="workspace-a",
            delivery=RuntimeAuthorityAccessDelivery(
                RuntimeAuthorityReference("other-docker"),
                RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
            ),
            admitted_by="operator-a",
            admitted_at="2026-07-22T10:03:00Z",
        )
        graph = _graph(authority_ref=RuntimeAuthorityReference("local-docker"))
        context = _context(
            desired_graph=graph,
            runtime_authority_deliveries=(delivery,),
        )

        request = runtime_effect_request_for_context(context)

        self.assertEqual(request.authority_ref, RuntimeAuthorityReference("local-docker"))
        self.assertEqual(request.authority_deliveries, ())

    def test_cloudflared_connector_uses_active_ingress_resource_epoch(self) -> None:
        removed_resource = replace(
            _cloudflare_resource(),
            status=OwnedIngressResourceStatus.REMOVED,
            removed_at="2026-07-28T08:05:00Z",
            removed_by_run_id="run-remove",
        )
        active_resource = replace(
            _cloudflare_resource(),
            tunnel_name="cpk-gateway-002",
            tunnel_id="tunnel-002",
            dns_record_id="dns-002",
            hostname="cpk-gateway-002.openj92.dev",
            source_run_id="run-realloc",
            source_activity_id="allocate-ingress-again",
            source_event_id="event-realloc",
            epoch=2,
        )
        active_secret = replace(
            _generated_ingress_secret(),
            secret_ref=SecretReference(
                "secret://generated/ingress/workspace-a/"
                "cloudflared-tunnel-token/run-realloc/"
                "allocate-ingress-again/event-realloc"
            ),
            source_run_id="run-realloc",
            source_activity_id="allocate-ingress-again",
            source_event_id="event-realloc",
        )
        graph = _public_ingress_graph()
        context = _context(
            activity=PlannedActivity(
                ActivityId("start-cloudflared"),
                StartNode(NodeTarget("cloudflared")),
            ),
            desired_graph=graph,
            registered_products=(
                _registered_product(
                    name="cpk-local-gateway",
                    provider_socket="control",
                    protocol=Protocol.HTTP,
                    port=8000,
                ),
                _registered_product(name="cloudflared-connector"),
            ),
            ingress_authorities=(_registered_ingress_authority(),),
            ingress_resources=(removed_resource, active_resource),
            generated_ingress_secrets=(_generated_ingress_secret(), active_secret),
        )

        request = runtime_effect_request_for_context(context)

        deliveries = request.products[0].product.runtime_contract.secret_deliveries
        self.assertEqual(
            tuple(
                delivery.reference.reference_id
                for delivery in deliveries
                if isinstance(delivery, SecretEnvironmentDelivery)
                and delivery.environment_name == "TUNNEL_TOKEN"
            ),
            (active_secret.secret_ref.reference_id,),
        )
        self.assertNotIn("tunnel-token-value", repr(request.descriptor()).lower())

    def test_gateway_node_receives_graph_derived_target_map_environment(self) -> None:
        graph = _gateway_graph()
        context = _context(
            activity=PlannedActivity(
                ActivityId("activity-gateway"),
                StartNode(NodeTarget("gateway")),
            ),
            desired_graph=graph,
            registered_products=(
                _registered_product(
                    name="cpk-local-gateway",
                    provider_socket="control",
                    protocol=Protocol.HTTP,
                    port=8000,
                    public_environment=(
                        PublicStaticEnvironmentBinding(
                            "CPK_GATEWAY_TARGETS_JSON",
                            "{}",
                        ),
                    ),
                ),
                _registered_product(
                    name="postgres-server",
                    provider_socket="postgres",
                    protocol=Protocol.POSTGRES,
                    port=5432,
                ),
                _registered_product(
                    name="http-active-router",
                    provider_socket="internal",
                    protocol=Protocol.HTTP,
                    port=8000,
                ),
            ),
        )

        request = runtime_effect_request_for_context(context)

        environment = {
            binding.name: binding.value
            for binding in request.products[0].public_environment
        }
        target_map = json.loads(environment["CPK_GATEWAY_TARGETS_JSON"])
        self.assertEqual(
            target_map,
            {
                "postgres.postgres": {
                    "protocol": "postgres",
                    "host": "postgres",
                    "port": 5432,
                    "database": "cpk",
                    "username": "cpk",
                    "password_environment": "POSTGRES_PASSWORD",
                },
                "router.internal": {
                    "protocol": "http",
                    "url": "http://router:8000",
                },
            },
        )
        self.assertNotIn("cpk-postgres-smoke-password", repr(request.descriptor()))
        self.assertNotIn("secret://", repr(request.descriptor()))

    def test_gateway_target_map_requires_declared_provider_port(self) -> None:
        graph = _gateway_graph(postgres_product_provider_socket="other")
        context = _context(
            activity=PlannedActivity(
                ActivityId("activity-gateway"),
                StartNode(NodeTarget("gateway")),
            ),
            desired_graph=graph,
            registered_products=(
                _registered_product(
                    name="cpk-local-gateway",
                    provider_socket="control",
                    protocol=Protocol.HTTP,
                    port=8000,
                    public_environment=(
                        PublicStaticEnvironmentBinding(
                            "CPK_GATEWAY_TARGETS_JSON",
                            "{}",
                        ),
                    ),
                ),
                _registered_product(
                    name="postgres-server",
                    provider_socket="other",
                    protocol=Protocol.POSTGRES,
                    port=5432,
                ),
                _registered_product(
                    name="http-active-router",
                    provider_socket="internal",
                    protocol=Protocol.HTTP,
                    port=8000,
                ),
            ),
        )

        with self.assertRaisesRegex(InvalidOperationCommand, "provider port"):
            runtime_effect_request_for_context(context)

    def test_target_map_is_not_delivered_to_ordinary_products(self) -> None:
        graph = _gateway_graph()
        context = _context(
            activity=PlannedActivity(
                ActivityId("activity-postgres"),
                StartNode(NodeTarget("postgres")),
            ),
            desired_graph=graph,
            registered_products=(
                _registered_product(
                    name="cpk-local-gateway",
                    provider_socket="control",
                    protocol=Protocol.HTTP,
                    port=8000,
                    public_environment=(
                        PublicStaticEnvironmentBinding(
                            "CPK_GATEWAY_TARGETS_JSON",
                            "{}",
                        ),
                    ),
                ),
                _registered_product(
                    name="postgres-server",
                    provider_socket="postgres",
                    protocol=Protocol.POSTGRES,
                    port=5432,
                ),
                _registered_product(
                    name="http-active-router",
                    provider_socket="internal",
                    protocol=Protocol.HTTP,
                    port=8000,
                ),
            ),
        )

        request = runtime_effect_request_for_context(context)

        environment = {
            binding.name: binding.value
            for binding in request.products[0].public_environment
        }
        self.assertNotIn("CPK_GATEWAY_TARGETS_JSON", environment)


def _context(
    *,
    run_id: str = "run-a",
    activity: PlannedActivity | None = None,
    desired_graph: DeploymentGraph | None = None,
    pull_authorities: tuple[RegisteredImagePullAuthority, ...] = (),
    runtime_authority_deliveries: tuple[RegisteredRuntimeAuthorityDelivery, ...] = (),
    registered_products: tuple[RegisteredProduct, ...] | None = None,
    ingress_authorities: tuple[RegisteredIngressAuthority, ...] = (),
    ingress_resources: tuple[CloudflareOwnedIngressResource, ...] = (),
    generated_ingress_secrets: tuple[GeneratedIngressSecretReference, ...] = (),
) -> ActivityRealizationContext:
    if activity is None:
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
            ClaimIdentity("worker-a", 1, "2026-07-22T10:01:00Z", "2026-07-22T10:30:00Z"),
        ),
        run=ActivityRunRecord(
            run_id,
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
        base_graph=RealizedGraphProjectionRecord.identity_for_authored(
            authored_record=GraphVersionRecord.from_graph(
                graph_id="graph-base",
                workspace_id="workspace-a",
                version=1,
                graph=graph,
                created_by="operator-a",
                created_at="2026-07-22T09:00:00Z",
            )
        ),
        desired_graph=RealizedGraphProjectionRecord.identity_for_authored(
            authored_record=GraphVersionRecord.from_graph(
                graph_id="graph-desired",
                workspace_id="workspace-a",
                version=2,
                graph=graph if desired_graph is None else desired_graph,
                created_by="operator-a",
                created_at="2026-07-22T10:00:00Z",
            )
        ),
        registered_products=(
            (_registered_product(),)
            if registered_products is None
            else registered_products
        ),
        image_pull_authorities=pull_authorities,
        runtime_authority_deliveries=runtime_authority_deliveries,
        ingress_authorities=ingress_authorities,
        ingress_resources=ingress_resources,
        generated_ingress_secrets=generated_ingress_secrets,
        authority=ExecutionWorkerAuthority(
            worker_id="worker-a",
            scopes=(PolicyScope.EXECUTION_OPERATE,),
        ),
        intent_event=ActivityEventRecord(
            event_id="event-started",
            run_id=run_id,
            kind=ActivityEventKind.STEP_STARTED,
            activity_id=activity.activity_id.value,
            occurred_at="2026-07-22T10:02:00Z",
            ordinal=3,
        ),
    )


def _graph(
    *,
    authority_ref: RuntimeAuthorityReference | None = None,
) -> DeploymentGraph:
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
                public_environment=(
                    PublicStaticEnvironmentBinding(
                        "HELLO_MESSAGE",
                        "Hello from selected instance",
                    ),
                ),
                socket_environment=(
                    SocketDerivedEnvironmentBinding(
                        "UPSTREAM_URL",
                        "http://upstream:8080",
                        "upstream.internal->api.upstream",
                    ),
                ),
            )
        },
        runtimes={
            "docker": RuntimeRecord(
                "docker",
                RuntimeKind.DOCKER,
                ("api",),
                authority_ref=authority_ref,
            )
        },
    )


def _public_ingress_graph(
    *,
    public_ingresses: tuple[NamedPublicIngress, ...] | None = None,
    connector_deliveries: tuple[SecretEnvironmentDelivery, ...] = (),
    ingress_lifecycle: PublicIngressLifecycle = PublicIngressLifecycle.EPHEMERAL,
) -> DeploymentGraph:
    gateway = Node(
        node_id="gateway",
        block_family=BlockFamily.APPLICATION,
        block_spec=BlockSpec("gateway"),
        kind="container-server",
        runtime_id="docker",
        sockets=BlockSockets(providers=(ProviderSocket("control", Protocol.HTTP),)),
    )
    cloudflared_reference = _registered_product(name="cloudflared-connector").reference
    ingress = NamedPublicIngress(
        ingress_id="gateway-public",
        authority_ref=IngressAuthorityReference("openj92-public-ingress"),
        target=PublicIngressTarget("gateway", "control"),
        connector_node_id="cloudflared",
        hostname="cpk-gateway-001.openj92.dev",
        lifecycle=ingress_lifecycle,
    )
    return DeploymentGraph(
        name="public-ingress-demo",
        nodes={
            "gateway": gateway,
            "cloudflared": Node(
                node_id="cloudflared",
                block_family=BlockFamily.APPLICATION,
                block_spec=BlockSpec("cloudflared"),
                kind="container-server",
                runtime_id="docker",
                sockets=BlockSockets(),
                metadata={
                    "product_identity": cloudflared_reference.identity.key,
                    "product_descriptor_digest": (
                        cloudflared_reference.descriptor_sha256.value
                    ),
                },
                secret_deliveries=connector_deliveries,
            ),
        },
        public_ingresses=(
            (ingress,) if public_ingresses is None else public_ingresses
        ),
        runtimes={
            "docker": RuntimeRecord(
                "docker",
                RuntimeKind.DOCKER,
                ("gateway", "cloudflared"),
            )
        },
    )


def _gateway_graph(
    *,
    postgres_product_provider_socket: str = "postgres",
) -> DeploymentGraph:
    gateway_reference = _registered_product(
        name="cpk-local-gateway",
        provider_socket="control",
        protocol=Protocol.HTTP,
        port=8000,
        public_environment=(
            PublicStaticEnvironmentBinding("CPK_GATEWAY_TARGETS_JSON", "{}"),
        ),
    ).reference
    postgres_reference = _registered_product(
        name="postgres-server",
        provider_socket=postgres_product_provider_socket,
        protocol=Protocol.POSTGRES,
        port=5432,
    ).reference
    router_reference = _registered_product(
        name="http-active-router",
        provider_socket="internal",
        protocol=Protocol.HTTP,
        port=8000,
    ).reference
    consumer = Node(
        node_id="api",
        block_family=BlockFamily.APPLICATION,
        block_spec=BlockSpec("api"),
        kind="container-server",
        runtime_id="docker",
        sockets=BlockSockets(
            requirements=(
                RequirementSocket(
                    "store",
                    Protocol.POSTGRES,
                    env_bindings=("DATABASE_URL",),
                ),
                RequirementSocket(
                    "http",
                    Protocol.HTTP,
                    env_bindings=("ROUTER_URL",),
                ),
            )
        ),
    )
    return DeploymentGraph(
        name="gateway-demo",
        nodes={
            "gateway": Node(
                node_id="gateway",
                block_family=BlockFamily.APPLICATION,
                block_spec=BlockSpec("gateway"),
                kind="container-server",
                runtime_id="docker",
                sockets=BlockSockets(
                    providers=(ProviderSocket("control", Protocol.HTTP),)
                ),
                metadata={
                    "product_identity": gateway_reference.identity.key,
                    "product_descriptor_digest": (
                        gateway_reference.descriptor_sha256.value
                    ),
                },
                public_environment=(
                    PublicStaticEnvironmentBinding("CPK_GATEWAY_TARGETS_JSON", "{}"),
                ),
            ),
            "postgres": Node(
                node_id="postgres",
                block_family=BlockFamily.APPLICATION,
                block_spec=BlockSpec("postgres"),
                kind="container-server",
                runtime_id="docker",
                sockets=BlockSockets(
                    providers=(ProviderSocket("postgres", Protocol.POSTGRES),)
                ),
                metadata={
                    "product_identity": postgres_reference.identity.key,
                    "product_descriptor_digest": (
                        postgres_reference.descriptor_sha256.value
                    ),
                },
                public_environment=(
                    PublicStaticEnvironmentBinding("POSTGRES_DB", "cpk"),
                    PublicStaticEnvironmentBinding("POSTGRES_USER", "cpk"),
                ),
                secret_deliveries=(
                    SecretEnvironmentDelivery(
                        "POSTGRES_PASSWORD",
                        SecretReference("secret://control-plane-kit/postgres/password"),
                        SecretUseIntent.POSTGRES_PASSWORD,
                    ),
                ),
            ),
            "router": Node(
                node_id="router",
                block_family=BlockFamily.APPLICATION,
                block_spec=BlockSpec("router"),
                kind="container-server",
                runtime_id="docker",
                sockets=BlockSockets(
                    providers=(ProviderSocket("internal", Protocol.HTTP),)
                ),
                metadata={
                    "product_identity": router_reference.identity.key,
                    "product_descriptor_digest": router_reference.descriptor_sha256.value,
                },
            ),
            "api": consumer,
        },
        edges={
            "api.store->postgres.postgres": Edge(
                "api.store->postgres.postgres",
                provider_role="postgres",
                provider_socket="postgres",
                consumer_role="api",
                requirement_socket="store",
                protocol=Protocol.POSTGRES,
                binding=SocketBinding.ENVIRONMENT,
            ),
            "api.http->router.internal": Edge(
                "api.http->router.internal",
                provider_role="router",
                provider_socket="internal",
                consumer_role="api",
                requirement_socket="http",
                protocol=Protocol.HTTP,
                binding=SocketBinding.ENVIRONMENT,
            ),
        },
        runtimes={
            "docker": RuntimeRecord(
                "docker",
                RuntimeKind.DOCKER,
                ("gateway", "postgres", "router", "api"),
            )
        },
    )


def _registered_product(
    *,
    name: str = "hello-server",
    provider_socket: str = "http",
    protocol: Protocol = Protocol.HTTP,
    port: int = 8000,
    public_environment: tuple[PublicStaticEnvironmentBinding, ...] | None = None,
    verification: VerificationContract | None = None,
    requirements: tuple[RequirementSocket, ...] = (),
) -> RegisteredProduct:
    runtime_verification = VerificationContract() if verification is None else verification
    product = ContainerServerProduct(
        identity=ProductReference.from_document(
            ProductDescriptorCodec().encode_document(
                ContainerServerProduct(
                    identity=_identity(name),
                    image=OciImageReference(
                        registry="ghcr.io",
                        repository=f"openj92/control-plane-kit-servers/{name}",
                        digest="sha256:" + "a" * 64,
                    ),
                    runtime_contract=ProductRuntimeContract(
                        sockets=BlockSockets(
                            requirements=requirements,
                            providers=(ProviderSocket(provider_socket, protocol),)
                        ),
                        provider_ports=(ProviderRuntimePort(provider_socket, port),),
                        public_environment=(
                            (
                                PublicStaticEnvironmentBinding(
                                    "HELLO_MESSAGE",
                                    "Hello from descriptor default",
                                ),
                            )
                            if public_environment is None
                            else public_environment
                        ),
                        verification=runtime_verification,
                    ),
                )
            )
        ).identity,
        image=OciImageReference(
            registry="ghcr.io",
            repository=f"openj92/control-plane-kit-servers/{name}",
            digest="sha256:" + "a" * 64,
        ),
        runtime_contract=ProductRuntimeContract(
            sockets=BlockSockets(
                requirements=requirements,
                providers=(ProviderSocket(provider_socket, protocol),),
            ),
            provider_ports=(ProviderRuntimePort(provider_socket, port),),
            public_environment=(
                (
                    PublicStaticEnvironmentBinding(
                        "HELLO_MESSAGE",
                        "Hello from descriptor default",
                    ),
                )
                if public_environment is None
                else public_environment
            ),
            verification=runtime_verification,
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


def _registered_ingress_authority() -> RegisteredIngressAuthority:
    return RegisteredIngressAuthority.from_authority(
        workspace_id="workspace-a",
        authority_ref=IngressAuthorityReference("openj92-public-ingress"),
        authority=CloudflareZoneIngressAuthority(
            account_id="account-openj92",
            zone_id="zone-openj92",
            zone_name="openj92.dev",
            api_token_ref=SecretReference("secret://cloudflare/openj92/api-token"),
            allowed_hostname_pattern="cpk-gateway-*.openj92.dev",

            generated_secret_provider_registration_id="sprov-generated-ingress",

            generated_secret_reference_prefix=SecretReference("secret://generated/ingress"),
        ),
        admitted_by="operator-a",
        admitted_at="2026-07-28T08:00:00Z",
    )


def _cloudflare_resource() -> CloudflareOwnedIngressResource:
    return CloudflareOwnedIngressResource(
        workspace_id="workspace-a",
        runtime_id="docker",
        ingress_id="gateway-public",
        authority_ref=IngressAuthorityReference("openj92-public-ingress"),
        provider_kind=IngressAuthorityProviderKind.CLOUDFLARE,
        tunnel_name="cpk-gateway-001",
        tunnel_id="tunnel-001",
        dns_record_id="dns-001",
        hostname="cpk-gateway-001.openj92.dev",
        zone_id="zone-openj92",
        lifecycle=PublicIngressLifecycle.EPHEMERAL,
        created_at="2026-07-28T08:01:00Z",
        observed_at="2026-07-28T08:01:01Z",
        source_run_id="run-alloc",
        source_activity_id="allocate-ingress",
        source_event_id="event-alloc",
    )


def _generated_ingress_secret() -> GeneratedIngressSecretReference:
    return GeneratedIngressSecretReference(
        workspace_id="workspace-a",
        purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
        secret_ref=SecretReference(
            "secret://generated/ingress/workspace-a/"
            "cloudflared-tunnel-token/run-alloc/allocate-ingress/event-alloc"
        ),
        provider_registration_id="sprov-generated-ingress",
        reference_registration_id="sref-generated-ingress",
        custody_id="scust-generated-ingress",
        provider_version_id="version-generated-ingress",
        provider_version_number=1,
        recorded_at="2026-07-28T08:01:02Z",
        source_run_id="run-alloc",
        source_activity_id="allocate-ingress",
        source_event_id="event-alloc",
    )


def _registered_pull_authority(
    *,
    repository: str | None,
    credential_reference: str = "secret://local/workspace-a/ghcr-read-token",
) -> RegisteredImagePullAuthority:
    return RegisteredImagePullAuthority.from_authority(
        workspace_id="workspace-a",
        authority=ImagePullAuthority(
            registry="ghcr.io",
            repository=repository,
            credential_reference=credential_reference,
        ),
        admitted_by="operator-a",
        admitted_at="2026-07-22T12:00:00Z",
    )


def _identity(name: str = "hello-server"):
    from control_plane_kit_core.products import ProductIdentity

    return ProductIdentity("openj92", name, 1)


if __name__ == "__main__":
    unittest.main()
