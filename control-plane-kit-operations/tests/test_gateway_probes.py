from __future__ import annotations

from dataclasses import dataclass
import json
import os
import unittest

import psycopg

from control_plane_kit_core.algebra import (
    BlockSockets,
    BlockSpec,
    ProviderSocket,
    RequirementSocket,
)
from control_plane_kit_core.gateway_delegation import (
    GatewayProbeCommandKind,
    GatewayProbeRequest,
)
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    PrincipalIdentity,
    PrincipalKind,
    WorkspaceGrant,
)
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
    RuntimeEndpointObservation,
)
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductReference,
    ProductRuntimeContract,
    ProviderRuntimePort,
)
from control_plane_kit_core.runtime_effects import GatewayTargetId
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretReference,
    SecretResolutionGrant,
)
from control_plane_kit_core.topology import (
    DeploymentGraph,
    Edge,
    Node,
    RuntimeRecord,
)
from control_plane_kit_core.types import BlockFamily, Protocol, RuntimeKind, SocketBinding
from control_plane_kit_operations.cpk_server import CpkServerGatewayProbeService
from control_plane_kit_operations.gateway_probes import (
    GatewayProbeAttemptStatus,
    GatewayProbeAuthorizationDenied,
    GatewayProbeCommandService,
    GatewayProbeConflict,
    GatewayProbeDispatchResult,
    GatewayProbeNotFound,
    GatewayProbeVerifierConfigurationService,
    RequestGatewayProbe,
)
from control_plane_kit_operations.delegation_signing_keys import (
    delegation_signing_key_registration_id_for,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.products import InlineDescriptorSource
from control_plane_kit_operations.records import BoundedEvidence, GraphVersionRecord, WorkspaceRecord
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    SecretProviderAuthorizationDenied,
)


class TrackingUnitOfWorkFactory:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.active = 0
        self.commits = 0

    def __call__(self) -> "TrackingUnitOfWork":
        return TrackingUnitOfWork(
            self,
            PostgresUnitOfWork(lambda: psycopg.connect(self.database_url)),
        )


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
        self._factory.active += 1
        self._inner.__enter__()
        return self

    def commit(self) -> None:
        self._factory.commits += 1
        self._inner.commit()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            self._inner.__exit__(exc_type, exc, traceback)
        finally:
            self._factory.active -= 1


class RecordingDispatcher:
    def __init__(self, tracker: TrackingUnitOfWorkFactory) -> None:
        self._tracker = tracker
        self.requests = []
        self.active_transactions = []

    def dispatch(self, request):
        self.requests.append(request)
        self.active_transactions.append(self._tracker.active)
        return GatewayProbeDispatchResult(
            GatewayProbeAttemptStatus.SUCCEEDED,
            "probe-succeeded",
            BoundedEvidence.from_mapping(
                {
                    "outcome": "healthy",
                    "target_id": request.request.target_id.value,
                }
            ),
        )


class RecordingSecretUseAuthorizer:
    def __init__(self) -> None:
        self.commands: list[AuthorizeSecretUse] = []
        self.denied = False

    def authorize_resolution(
        self,
        command: AuthorizeSecretUse,
    ) -> SecretResolutionGrant:
        self.commands.append(command)
        if self.denied:
            raise SecretProviderAuthorizationDenied("denied")
        return SecretResolutionGrant(
            authorization_id="suse_" + "a" * 64,
            workspace_id=command.workspace_id,
            reference_registration_id="sref_" + "b" * 64,
            provider_registration_id="sprov_" + "c" * 64,
            endpoint_reference=SecretProviderEndpointReference("provider-a"),
            credential_reference=SecretReference(
                "secret://bootstrap/provider-a-token"
            ),
            reference=command.reference,
            intent=command.intent,
            actor_subject=command.actor_subject,
            correlation_id=command.correlation_id,
            intent_fingerprint="d" * 64,
            operation_id=command.operation_id,
            session_id=command.session_id,
            run_id=command.run_id,
            activity_id=command.activity_id,
            effect_id=command.effect_id,
            probe_id=command.probe_id,
        )


@dataclass(frozen=True)
class RouteRequest:
    surface: str
    route_id: str
    service_role: ControlPlaneServiceRole
    path_parameters: dict[str, str]
    payload: dict[str, object]
    principal: AuthenticatedPrincipal


class GeneratedIds:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"gateway-probe-id-{self.value}"


class GatewayProbeCommandServiceTests(unittest.TestCase):
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
        self.tracker = TrackingUnitOfWorkFactory(database_url)
        self.dispatcher = RecordingDispatcher(self.tracker)
        self.secret_use_authorizer = RecordingSecretUseAuthorizer()
        self.signing_key_reference = SecretReference(
            "secret://gateway/probe/signing-key"
        )
        self.service = GatewayProbeCommandService(
            self.tracker,
            dispatcher=self.dispatcher,
            secret_use_authorizer=self.secret_use_authorizer,
            epoch_clock=lambda: 1_800_000_000,
            clock=lambda: "2027-01-15T08:00:00Z",
            id_factory=GeneratedIds(),
        )
        self._seed_current_graph()
        self._seed_signing_key()

    def tearDown(self) -> None:
        self.connection.close()

    def test_commits_intent_before_external_dispatch_and_folds_bounded_result(self) -> None:
        result = self.service.execute(self.command())

        self.assertEqual(self.dispatcher.active_transactions, [0])
        self.assertEqual(self.tracker.commits, 3)  # seed + intent + result
        self.assertEqual(result.attempt.status, GatewayProbeAttemptStatus.SUCCEEDED)
        self.assertEqual(result.attempt.current_graph_id, "graph-current")
        self.assertEqual(result.attempt.gateway_runtime_id, "docker-a")
        self.assertEqual(result.attempt.result_code, "probe-succeeded")
        self.assertFalse(result.replayed)
        endpoint = self.dispatcher.requests[0].gateway_endpoint
        self.assertIsInstance(endpoint, RuntimeEndpointObservation)
        self.assertEqual(endpoint.subject_id, "gateway")
        self.assertEqual(endpoint.socket_name, "control")
        self.assertEqual(endpoint.graph_id, "graph-current")
        self.assertEqual(endpoint.protocol, Protocol.HTTP)
        self.assertEqual(endpoint.context, EndpointContext.RUNTIME_PRIVATE)
        self.assertEqual(
            endpoint.address,
            LiteralEndpointMaterial("http://gateway:8000"),
        )
        self.assertEqual(len(self.secret_use_authorizer.commands), 1)
        self.assertEqual(
            self.dispatcher.requests[0].signing_key_reference,
            self.signing_key_reference,
        )
        self.assertEqual(
            self.dispatcher.requests[0].signing_public_key.key_id,
            "gateway-test-key",
        )
        authorization = self.secret_use_authorizer.commands[0]
        self.assertEqual(authorization.reference, self.signing_key_reference)
        self.assertEqual(authorization.probe_id, result.attempt.probe_id)
        self.assertTrue(
            self.dispatcher.requests[0].secret_resolution_grant.permits(
                self.signing_key_reference,
                authorization.intent,
            )
        )
        descriptor = result.descriptor()
        self.assertNotIn("signature", repr(descriptor).lower())
        self.assertNotIn("compact", repr(descriptor).lower())
        self.assertNotIn("authorization", repr(descriptor).lower())

    def test_duplicate_request_is_idempotent_without_redispatch(self) -> None:
        first = self.service.execute(self.command())
        replay = self.service.execute(self.command())

        self.assertEqual(first.attempt, replay.attempt)
        self.assertTrue(replay.replayed)
        self.assertEqual(len(self.dispatcher.requests), 1)
        self.assertEqual(len(self.secret_use_authorizer.commands), 1)

        with self.assertRaises(GatewayProbeConflict):
            self.service.execute(
                self.command(
                    request=GatewayProbeRequest(
                        GatewayProbeCommandKind.HTTP_STATUS,
                        GatewayTargetId("hello.http"),
                        "/health/live",
                    )
                )
            )

    def test_authority_and_current_graph_checks_reject_before_dispatch(self) -> None:
        with self.assertRaises(GatewayProbeAuthorizationDenied):
            self.service.execute(
                self.command(
                    context=self.context(
                        scopes=(PolicyScope.INSTANCE_WORKSPACE_READ,)
                    )
                )
            )
        with self.assertRaises(GatewayProbeConflict):
            self.service.execute(
                self.command(expected_current_graph_id="graph-stale")
            )
        with self.assertRaises(GatewayProbeNotFound):
            self.service.execute(
                self.command(
                    request=GatewayProbeRequest(
                        GatewayProbeCommandKind.HTTP_STATUS,
                        GatewayTargetId("missing.http"),
                        "/health/ready",
                    )
                )
            )
        self.assertEqual(self.dispatcher.requests, [])

    def test_missing_or_ambiguous_active_key_fails_before_intent_and_dispatch(self) -> None:
        self.connection.execute(
            "UPDATE cpk_delegation_signing_keys SET status = 'retired'"
        )
        with self.assertRaises(GatewayProbeConflict):
            self.service.execute(self.command())
        self.assertEqual(self.dispatcher.requests, [])

    def test_verifier_configuration_contains_exact_active_overlap_set(self) -> None:
        self._seed_verify_only_key()
        configuration = GatewayProbeVerifierConfigurationService(
            self.tracker
        ).for_gateway(
            workspace_id="workspace-a",
            gateway_node_id="renamed-gateway",
        )

        environment = {
            binding.name: binding.value
            for binding in configuration.public_environment()
        }
        self.assertEqual(configuration.issuer, "cpk-test")
        self.assertEqual(
            environment["CPK_GATEWAY_PROBE_AUDIENCE"],
            "gateway:workspace-a:renamed-gateway",
        )
        self.assertEqual(
            environment["CPK_GATEWAY_PROBE_NODE_ID"],
            "renamed-gateway",
        )
        self.assertEqual(
            set(
                json.loads(
                    environment["CPK_GATEWAY_PROBE_VERIFICATION_KEYS_JSON"]
                )
            ),
            {"gateway-test-key", "gateway-test-key-b"},
        )

    def test_signing_secret_denial_is_folded_without_gateway_io(self) -> None:
        self.secret_use_authorizer.denied = True

        result = self.service.execute(self.command())

        self.assertEqual(result.attempt.status, GatewayProbeAttemptStatus.REJECTED)
        self.assertEqual(
            result.attempt.result_code,
            "gateway-signing-secret-not-authorized",
        )
        self.assertEqual(self.dispatcher.requests, [])

    def test_http_and_mcp_shaped_routes_use_the_same_operations_service(self) -> None:
        adapter = CpkServerGatewayProbeService(self.service)
        principal = self.principal(
            scopes=(
                PolicyScope.GATEWAY_PROBE_USE,
                PolicyScope.DELEGATION_KEY_USE,
                PolicyScope.INSTANCE_WORKSPACE_READ,
                PolicyScope.SECRET_PROVIDER_USE,
            )
        )
        http = adapter.handle(self.route_request("http", "request-http", principal))
        mcp = adapter.handle(self.route_request("mcp", "request-mcp", principal))

        self.assertEqual(http["gateway_probe"]["result_code"], "probe-succeeded")
        self.assertEqual(mcp["gateway_probe"]["result_code"], "probe-succeeded")
        self.assertEqual(len(self.dispatcher.requests), 2)

    def command(
        self,
        *,
        context=None,
        expected_current_graph_id: str = "graph-current",
        request: GatewayProbeRequest | None = None,
    ) -> RequestGatewayProbe:
        return RequestGatewayProbe(
            context=self.context() if context is None else context,
            request_id="request-a",
            expected_current_graph_id=expected_current_graph_id,
            gateway_node_id="gateway",
            request=(
                GatewayProbeRequest(
                    GatewayProbeCommandKind.HTTP_STATUS,
                    GatewayTargetId("hello.http"),
                    "/health/ready",
                )
                if request is None
                else request
            ),
        )

    def route_request(
        self,
        surface: str,
        request_id: str,
        principal: AuthenticatedPrincipal,
    ) -> RouteRequest:
        return RouteRequest(
            surface=surface,
            route_id="command.gateway-probe.request",
            service_role=ControlPlaneServiceRole.OBSERVATION,
            path_parameters={
                "workspace_id": "workspace-a",
                "gateway_node_id": "gateway",
            },
            payload={
                "request_id": request_id,
                "expected_current_graph_id": "graph-current",
                "kind": "http-status",
                "target_id": "hello.http",
                "path": "/health/ready",
            },
            principal=principal,
        )

    def context(
        self,
        *,
        scopes: tuple[PolicyScope, ...] = (
            PolicyScope.GATEWAY_PROBE_USE,
            PolicyScope.DELEGATION_KEY_USE,
            PolicyScope.SECRET_PROVIDER_USE,
        ),
    ):
        return self.principal(scopes=scopes).command_context("workspace-a")

    def principal(
        self,
        *,
        scopes: tuple[PolicyScope, ...],
    ) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            PrincipalIdentity(
                issuer="urn:test:identity",
                subject_id="operator-a",
                kind=PrincipalKind.OPERATOR,
            ),
            (WorkspaceGrant("workspace-a", scopes),),
        )

    def _seed_signing_key(self) -> None:
        public_key = DelegationPublicKey(
            key_id="gateway-test-key",
            algorithm=DelegationKeyAlgorithm.ED25519,
            public_key_pem=(
                "-----BEGIN PUBLIC KEY-----\n"
                "MCowBQYDK2VwAyEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
                "-----END PUBLIC KEY-----\n"
            ),
        )
        registration_id = delegation_signing_key_registration_id_for(
            workspace_id="workspace-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-test",
            public_key=public_key,
            private_key_reference=self.signing_key_reference,
        )
        self.connection.execute(
            """
            INSERT INTO cpk_delegation_signing_keys (
              registration_id, workspace_id, purpose, issuer, key_id, algorithm,
              public_key_pem, public_fingerprint_sha256, private_key_reference,
              admitted_by, admitted_at, status, activated_by, activated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s)
            """,
            (
                registration_id,
                "workspace-a",
                DelegationKeyPurpose.GATEWAY_PROBE.value,
                "cpk-test",
                public_key.key_id,
                public_key.algorithm.value,
                public_key.public_key_pem,
                public_key.fingerprint_sha256,
                self.signing_key_reference.reference_id,
                "operator-a",
                "2027-01-15T07:59:00Z",
                "operator-a",
                "2027-01-15T07:59:30Z",
            ),
        )

    def _seed_verify_only_key(self) -> None:
        public_key = DelegationPublicKey(
            key_id="gateway-test-key-b",
            algorithm=DelegationKeyAlgorithm.ED25519,
            public_key_pem=(
                "-----BEGIN PUBLIC KEY-----\n"
                "MCowBQYDK2VwAyEAbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=\n"
                "-----END PUBLIC KEY-----\n"
            ),
        )
        reference = SecretReference("secret://gateway/probe/signing-key-b")
        registration_id = delegation_signing_key_registration_id_for(
            workspace_id="workspace-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-test",
            public_key=public_key,
            private_key_reference=reference,
        )
        self.connection.execute(
            """
            INSERT INTO cpk_delegation_signing_keys (
              registration_id, workspace_id, purpose, issuer, key_id, algorithm,
              public_key_pem, public_fingerprint_sha256, private_key_reference,
              admitted_by, admitted_at, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'verify-only')
            """,
            (
                registration_id,
                "workspace-a",
                DelegationKeyPurpose.GATEWAY_PROBE.value,
                "cpk-test",
                public_key.key_id,
                public_key.algorithm.value,
                public_key.public_key_pem,
                public_key.fingerprint_sha256,
                reference.reference_id,
                "operator-a",
                "2027-01-15T08:00:00Z",
            ),
        )

    def _seed_current_graph(self) -> None:
        gateway_document = _product_document(
            "cpk-local-gateway",
            "control",
            Protocol.HTTP,
            8000,
        )
        hello_document = _product_document(
            "hello-server",
            "http",
            Protocol.HTTP,
            8000,
        )
        gateway_ref = ProductReference.from_document(gateway_document)
        hello_ref = ProductReference.from_document(hello_document)
        graph = DeploymentGraph(
            "gateway-probe",
            nodes={
                "gateway": Node(
                    node_id="gateway",
                    block_family=BlockFamily.PROXY,
                    block_spec=BlockSpec("gateway"),
                    kind="container-server",
                    runtime_id="docker-a",
                    sockets=BlockSockets(
                        requirements=(
                            RequirementSocket(
                                "target",
                                Protocol.HTTP,
                                env_bindings=("TARGET_URL",),
                            ),
                        ),
                        providers=(ProviderSocket("control", Protocol.HTTP),),
                    ),
                    metadata=_product_metadata(gateway_ref),
                ),
                "hello": Node(
                    node_id="hello",
                    block_family=BlockFamily.APPLICATION,
                    block_spec=BlockSpec("hello"),
                    kind="container-server",
                    runtime_id="docker-a",
                    sockets=BlockSockets(
                        providers=(ProviderSocket("http", Protocol.HTTP),)
                    ),
                    metadata=_product_metadata(hello_ref),
                ),
            },
            edges={
                "gateway.target->hello.http": Edge(
                    "gateway.target->hello.http",
                    provider_role="hello",
                    provider_socket="http",
                    consumer_role="gateway",
                    requirement_socket="target",
                    protocol=Protocol.HTTP,
                    binding=SocketBinding.ENVIRONMENT,
                )
            },
            runtimes={
                "docker-a": RuntimeRecord(
                    "docker-a",
                    RuntimeKind.DOCKER,
                    ("gateway", "hello"),
                )
            },
        )
        with self.tracker() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-a", "Workspace A")
            )
            unit_of_work.stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-current",
                    workspace_id="workspace-a",
                    version=1,
                    graph=graph,
                    created_by="operator-a",
                    created_at="2027-01-15T07:59:00Z",
                )
            )
            for document in (gateway_document, hello_document):
                unit_of_work.stores.registered_products.register(
                    workspace_id="workspace-a",
                    descriptor_document=document,
                    source=InlineDescriptorSource(),
                    imported_by="operator-a",
                    imported_at="2027-01-15T07:58:00Z",
                )
            unit_of_work.stores.workspaces.set_current_graph(
                "workspace-a",
                "graph-current",
            )
            unit_of_work.commit()


def _product_document(
    name: str,
    provider_socket: str,
    protocol: Protocol,
    port: int,
):
    return ProductDescriptorCodec().encode_document(
        ContainerServerProduct(
            identity=ProductIdentity("control-plane-kit", name, 1),
            image=OciImageReference(
                registry="ghcr.io",
                repository=f"openj92/control-plane-kit-servers/{name}",
                digest="sha256:" + "a" * 64,
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(
                    providers=(ProviderSocket(provider_socket, protocol),)
                ),
                provider_ports=(ProviderRuntimePort(provider_socket, port),),
            ),
        )
    )


def _product_metadata(reference: ProductReference) -> dict[str, str]:
    return {
        "product_identity": reference.identity.key,
        "product_descriptor_digest": reference.descriptor_sha256.value,
    }


if __name__ == "__main__":
    unittest.main()
