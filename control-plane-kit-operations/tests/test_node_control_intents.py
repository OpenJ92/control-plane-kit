from __future__ import annotations

import ast
from dataclasses import fields, replace
import inspect
import os
from pathlib import Path
import unittest

import psycopg

import control_plane_kit_core as core
import control_plane_kit_operations as operations
from control_plane_kit_core.algebra import (
    BlockSockets,
    BlockSpec,
    ProviderSocket,
    RequirementSocket,
)
from control_plane_kit_core.capabilities import CapabilityName
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
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    ControlPlaneTransitionPrecondition,
    ControlPlaneVariableDescriptor,
    ControlPlaneVariableKind,
    ControlPlaneVariableOperationContract,
    ControlPlaneResultCodec,
    ControlPlaneStateCodec,
    NodeControlCommandRequest,
    NodeControlGraphReference,
    NodeControlGraphReferenceRole,
    NodeControlOperation,
    NodeControlPayload,
    NodeControlTarget,
    ScalarControlState,
    WorkloadNodeControlSurfaceDescriptor,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_core.topology import (
    DeploymentGraph,
    Edge,
    Node,
    RuntimeRecord,
)
from control_plane_kit_core.types import BlockFamily, Protocol, RuntimeKind, SocketBinding
from control_plane_kit_operations.delegation_signing_keys import (
    delegation_signing_key_registration_id_for,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand,
    RegisterSecretReferenceCommand,
    SecretProviderKind,
    SecretProviderRegistrationService,
)


PUBLIC_KEY_A = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
-----END PUBLIC KEY-----
"""
PUBLIC_KEY_B = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=
-----END PUBLIC KEY-----
"""


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


class GeneratedIds:
    def __init__(self) -> None:
        self.values = iter(("attempt-a", "transit-jti-a", "workload-jti-a"))
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return next(self.values)


class ForbiddenCall:
    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self):
        raise AssertionError(f"replay called {self.name}")


class NodeControlIntentAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required through the "
                "Docker-first Operations test harness"
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self._seed_graph_truth()
        self._seed_signing_authority()
        self.tracker = TrackingUnitOfWorkFactory(database_url)
        self.ids = GeneratedIds()

    def tearDown(self) -> None:
        self.connection.close()

    def contract(self, name: str):
        value = getattr(operations, name, None)
        self.assertIsNotNone(value, f"{name} is not implemented")
        return value

    def service(self, *, replay: bool = False):
        service_type = self.contract("NodeControlIntentAuthorizationService")
        return service_type(
            self.tracker,
            epoch_clock=(ForbiddenCall("epoch clock") if replay else lambda: 100),
            clock=(ForbiddenCall("wall clock") if replay else lambda: "2027-01-15T08:00:00Z"),
            id_factory=(ForbiddenCall("id factory") if replay else self.ids),
            grant_lifetime_seconds=60,
        )

    def test_public_shape_and_workload_audience_are_nominal(self) -> None:
        helper = getattr(core, "workload_node_control_audience", None)
        self.assertIsNotNone(helper, "workload audience derivation is not implemented")
        request = self.request()
        self.assertEqual(helper(request.target), "workload:router:control")

        expected = {
            "DeferredGatewayNodeControlTransitSigningRequest": (
                "key_registration_id", "authorization_id", "grant",
            ),
            "DeferredWorkloadNodeControlSigningRequest": (
                "key_registration_id", "authorization_id", "grant",
            ),
            "NodeControlIntentPreparation": (
                "attempt", "replayed", "transit_signing", "workload_signing",
            ),
        }
        for name, names in expected.items():
            with self.subTest(name=name):
                contract = self.contract(name)
                self.assertEqual(tuple(field.name for field in fields(contract)), names)

    def test_exact_authority_commits_one_reference_only_preparation(self) -> None:
        result = self.service().execute(self.command())

        self.assertEqual(self.tracker.active, 0)
        self.assertEqual(self.tracker.commits, 1)
        self.assertFalse(result.replayed)
        self.assertEqual(result.attempt.current_graph_id, "graph-current")
        self.assertEqual(
            result.attempt.current_realized_projection_id,
            "projection-current",
        )
        self.assertEqual(result.attempt.gateway_runtime_id, "docker-a")
        self.assertEqual(result.attempt.request, self.request())
        self.assertEqual(
            result.attempt.workload_grant.audience,
            "workload:router:control",
        )
        self.assertIs(
            result.attempt.transit_grant.purpose,
            DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
        )
        self.assertNotEqual(
            result.transit_signing.key_registration_id,
            result.workload_signing.key_registration_id,
        )
        self.assertNotEqual(
            result.transit_signing.authorization_id,
            result.workload_signing.authorization_id,
        )
        self.assertEqual(result.transit_signing.grant, result.attempt.transit_grant)
        self.assertEqual(result.workload_signing.grant, result.attempt.workload_grant)
        self.assertNotIn("secret://", repr(result))
        self.assertNotIn("endpoint", repr(result).lower())
        self.assertNotIn("credential", repr(result).lower())

        rows = self.connection.execute(
            """
            SELECT use_intent, actor_subject, correlation_id
            FROM cpk_secret_use_authorizations
            ORDER BY use_intent
            """
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row[0] for row in rows},
            {
                SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY.value,
                SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY.value,
            },
        )
        self.assertEqual({row[1] for row in rows}, {"operator-a"})
        self.assertEqual(len({row[2] for row in rows}), 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_node_control_attempts"
            ).fetchone(),
            (1,),
        )

    def test_read_and_apply_require_the_exact_scope_products(self) -> None:
        required = {
            NodeControlOperation.READ_STATE: (
                PolicyScope.NODE_CONTROL_READ,
                PolicyScope.NODE_CONTROL_EXECUTE,
                PolicyScope.DELEGATION_KEY_USE,
                PolicyScope.SECRET_PROVIDER_USE,
            ),
            NodeControlOperation.APPLY_COMMAND: (
                PolicyScope.NODE_CONTROL_APPLY,
                PolicyScope.NODE_CONTROL_EXECUTE,
                PolicyScope.DELEGATION_KEY_USE,
                PolicyScope.SECRET_PROVIDER_USE,
            ),
        }
        denied = self.contract("NodeControlIntentAuthorizationDenied")
        for operation, scopes in required.items():
            for missing in scopes:
                with self.subTest(operation=operation, missing=missing):
                    granted = tuple(scope for scope in scopes if scope is not missing)
                    command = self.command(
                        context=self.context(scopes=granted),
                        request=self.request(operation=operation),
                    )
                    with self.assertRaises(denied) as caught:
                        self.service().execute(command)
                    self.assertLessEqual(len(str(caught.exception)), 128)
                    self.assertIsNone(caught.exception.__cause__)
                    self.assertIsNone(caught.exception.__context__)

        substitutions = (
            (NodeControlOperation.READ_STATE, PolicyScope.NODE_CONTROL_APPLY),
            (NodeControlOperation.APPLY_COMMAND, PolicyScope.NODE_CONTROL_READ),
        )
        for operation, substitute in substitutions:
            with self.subTest(operation=operation, substitute=substitute):
                with self.assertRaises(denied):
                    self.service().execute(
                        self.command(
                            context=self.context(
                                scopes=(
                                    substitute,
                                    PolicyScope.NODE_CONTROL_EXECUTE,
                                    PolicyScope.DELEGATION_KEY_USE,
                                    PolicyScope.SECRET_PROVIDER_USE,
                                )
                            ),
                            request=self.request(operation=operation),
                        )
                    )

    def test_graph_and_surface_drift_fail_before_durable_authorization(self) -> None:
        not_found = self.contract("NodeControlIntentNotFound")
        conflict = self.contract("NodeControlIntentConflict")
        cases = (
            (
                "foreign-workspace",
                self.command(
                    context=self.context(workspace_id="workspace-b"),
                ),
                conflict,
            ),
            (
                "stale-graph",
                self.command(
                    request=replace(
                        self.request(),
                        target=replace(
                            self.request().target,
                            graph_revision=self.reference(
                                NodeControlGraphReferenceRole.GRAPH_REVISION,
                                "graph-stale",
                            ),
                        ),
                    )
                ),
                conflict,
            ),
            (
                "missing-gateway",
                self.command(
                    gateway_node_id=self.reference(
                        NodeControlGraphReferenceRole.NODE,
                        "missing-gateway",
                    )
                ),
                not_found,
            ),
            (
                "missing-variable",
                self.command(
                    request=replace(
                        self.request(),
                        variable_name=self.reference(
                            NodeControlGraphReferenceRole.VARIABLE,
                            "missing-variable",
                        ),
                    )
                ),
                not_found,
            ),
        )
        for name, command, error_type in cases:
            with self.subTest(name=name):
                with self.assertRaises(error_type) as caught:
                    self.service().execute(command)
                self.assertLessEqual(len(str(caught.exception)), 128)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_secret_use_authorizations"
            ).fetchone(),
            (0,),
        )

    def test_exact_replay_precedes_clocks_graph_reads_and_key_rebinding(self) -> None:
        first = self.service().execute(self.command())
        self.connection.execute(
            "UPDATE cpk_workspaces SET current_graph_id=NULL, "
            "current_realized_projection_id=NULL WHERE workspace_id='workspace-a'"
        )
        self.connection.execute(
            "UPDATE cpk_delegation_signing_keys SET status='revoked', "
            "revoked_by='operator-a', revoked_at='2027-01-15T08:01:00Z'"
        )

        replay = self.service(replay=True).execute(self.command())
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.attempt, first.attempt)
        self.assertEqual(replay.transit_signing, first.transit_signing)
        self.assertEqual(replay.workload_signing, first.workload_signing)
        self.assertEqual(self.tracker.commits, 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_secret_use_authorizations"
            ).fetchone(),
            (2,),
        )

    def test_changed_actor_gateway_or_request_conflicts_without_refresh(self) -> None:
        self.service().execute(self.command())
        conflict = self.contract("NodeControlIntentConflict")
        changed = (
            self.command(context=self.context(actor="operator-b")),
            self.command(
                gateway_node_id=self.reference(
                    NodeControlGraphReferenceRole.NODE,
                    "gateway-b",
                )
            ),
            self.command(
                request=replace(self.request(), idempotency_key="changed-key")
            ),
        )
        for command in changed:
            with self.subTest(command=command):
                with self.assertRaises(conflict):
                    self.service(replay=True).execute(command)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_secret_use_authorizations"
            ).fetchone(),
            (2,),
        )

    def test_second_secret_authorization_failure_rolls_back_every_row(self) -> None:
        self.connection.execute(
            "UPDATE cpk_secret_references SET status='revoked', "
            "revoked_by='operator-a', revoked_at='2027-01-15T08:00:00Z' "
            "WHERE secret_reference='secret://workspace-secrets/keys/workload'"
        )
        error_type = self.contract("NodeControlIntentConflict")
        with self.assertRaises(error_type) as caught:
            self.service().execute(self.command())
        self.assertLessEqual(len(str(caught.exception)), 128)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_secret_use_authorizations"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_node_control_attempts"
            ).fetchone(),
            (0,),
        )

    def test_supporting_store_seams_are_bounded_and_lock_current_authority(self) -> None:
        key_source = inspect.getsource(
            operations.postgres.delegation_signing_key_store.DelegationSigningKeyStore
        )
        self.assertIn("LIMIT 2", key_source)
        self.assertIn("pg_advisory_xact_lock_shared", key_source)
        self.assertIn("pg_advisory_xact_lock(", key_source)

        secret_module = operations.secret_providers
        self.assertTrue(
            hasattr(secret_module, "authorize_secret_use_in_unit_of_work")
        )
        reference_store = operations.postgres.secret_provider_store.SecretReferenceStore
        provider_store = operations.postgres.secret_provider_store.SecretProviderStore
        self.assertTrue(hasattr(reference_store, "get_active_for_update"))
        self.assertTrue(
            hasattr(provider_store, "require_active_registration_for_update")
        )

    def test_service_has_no_outer_effect_or_framework_boundary(self) -> None:
        module_path = (
            Path(operations.__file__).parent / "node_control_intents.py"
        )
        self.assertTrue(module_path.is_file())
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        forbidden = (
            "fastapi", "httpx", "requests", "urllib", "socket",
            "control_plane_kit_server_sdk", "control_plane_kit_interpreters",
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names = (node.module or "",)
            else:
                continue
            for name in names:
                self.assertFalse(
                    any(name == value or name.startswith(value + ".") for value in forbidden),
                    name,
                )
        service_type = self.contract("NodeControlIntentAuthorizationService")
        parameters = inspect.signature(service_type).parameters
        for name in ("signer", "dispatcher", "resolver", "relay", "client"):
            self.assertNotIn(name, parameters)

    def command(
        self,
        *,
        context=None,
        gateway_node_id: NodeControlGraphReference | None = None,
        request: NodeControlCommandRequest | None = None,
    ):
        command_type = self.contract("RequestNodeControlIntent")
        return command_type(
            context=self.context() if context is None else context,
            gateway_node_id=(
                self.reference(NodeControlGraphReferenceRole.NODE, "gateway")
                if gateway_node_id is None
                else gateway_node_id
            ),
            request=self.request() if request is None else request,
        )

    def request(
        self,
        *,
        operation: NodeControlOperation = NodeControlOperation.APPLY_COMMAND,
    ) -> NodeControlCommandRequest:
        apply = operation is NodeControlOperation.APPLY_COMMAND
        return NodeControlCommandRequest(
            target=NodeControlTarget(
                workspace_id=self.reference(
                    NodeControlGraphReferenceRole.WORKSPACE,
                    "workspace-a",
                ),
                graph_revision=self.reference(
                    NodeControlGraphReferenceRole.GRAPH_REVISION,
                    "graph-current",
                ),
                node_id=self.reference(
                    NodeControlGraphReferenceRole.NODE,
                    "router",
                ),
                provider_socket_name=self.reference(
                    NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                    "control",
                ),
            ),
            variable_name=self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                "routing",
            ),
            operation=operation,
            request_id="request-a",
            idempotency_key="idempotency-a",
            command_codec=(
                ControlPlaneCommandCodec.REPLACE_SCALAR_V1 if apply else None
            ),
            precondition=ControlPlaneTransitionPrecondition(4) if apply else None,
            payload=(
                NodeControlPayload(
                    ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                    ScalarControlState("blue"),
                )
                if apply
                else None
            ),
        )

    def context(
        self,
        *,
        actor: str = "operator-a",
        workspace_id: str = "workspace-a",
        scopes: tuple[PolicyScope, ...] = (
            PolicyScope.NODE_CONTROL_APPLY,
            PolicyScope.NODE_CONTROL_EXECUTE,
            PolicyScope.DELEGATION_KEY_USE,
            PolicyScope.SECRET_PROVIDER_USE,
        ),
    ):
        principal = AuthenticatedPrincipal(
            PrincipalIdentity(
                issuer="urn:test:identity",
                subject_id=actor,
                kind=PrincipalKind.OPERATOR,
            ),
            (WorkspaceGrant(workspace_id, scopes),),
        )
        return principal.command_context(workspace_id)

    @staticmethod
    def reference(
        role: NodeControlGraphReferenceRole,
        value: str,
    ) -> NodeControlGraphReference:
        return NodeControlGraphReference(role, value)

    def _seed_graph_truth(self) -> None:
        authored = self._graph(with_surface=False)
        realized = self._graph(with_surface=True)
        with PostgresUnitOfWork(lambda: psycopg.connect(self.database_url)) as unit:
            unit.stores.workspaces.create(WorkspaceRecord("workspace-a", "Workspace A"))
            record = GraphVersionRecord.from_graph(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph=authored,
                created_by="operator-a",
                created_at="2027-01-15T07:55:00Z",
            )
            unit.stores.graphs.save(record)
            projection = RealizedGraphProjectionRecord.from_graph(
                projection_id="projection-current",
                workspace_id="workspace-a",
                source_authored_graph_id="graph-current",
                projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
                projection_key="node-control-current",
                graph=realized,
                created_by="operator-a",
                created_at="2027-01-15T07:56:00Z",
            )
            unit.stores.realized_graphs.save(projection)
            unit.stores.workspaces.set_current_graph(
                "workspace-a",
                "graph-current",
                "projection-current",
            )
            unit.commit()
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id,name,lifecycle,metadata) "
            "VALUES ('workspace-b','Workspace B','created','{}')"
        )

    def _graph(self, *, with_surface: bool) -> DeploymentGraph:
        variable = ControlPlaneVariableDescriptor(
            variable_name=self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                "routing",
            ),
            kind=ControlPlaneVariableKind.SCALAR,
            state_codec=ControlPlaneStateCodec.SCALAR_V1,
            operation_contracts=(
                ControlPlaneVariableOperationContract(
                    NodeControlOperation.READ_STATE,
                    None,
                    ControlPlaneResultCodec.STATE_V1,
                ),
                ControlPlaneVariableOperationContract(
                    NodeControlOperation.APPLY_COMMAND,
                    ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                    ControlPlaneResultCodec.TRANSITION_V1,
                ),
            ),
        )
        surface = WorkloadNodeControlSurfaceDescriptor(
            self.reference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                "control",
            ),
            (variable,),
        )
        gateway = Node(
            node_id="gateway",
            block_family=BlockFamily.PROXY,
            block_spec=BlockSpec("gateway"),
            kind="container-server",
            runtime_id="docker-a",
            sockets=BlockSockets(
                requirements=(
                    RequirementSocket(
                        "router-control",
                        Protocol.HTTP,
                        env_bindings=("ROUTER_CONTROL_URL",),
                    ),
                ),
                providers=(ProviderSocket("control", Protocol.HTTP),),
            ),
        )
        router = Node(
            node_id="router",
            block_family=BlockFamily.APPLICATION,
            block_spec=BlockSpec(
                "router",
                capabilities=(CapabilityName.NODE_CONTROLLABLE,) if with_surface else (),
                control_surfaces=(surface,) if with_surface else (),
            ),
            kind="container-server",
            runtime_id="docker-a",
            sockets=BlockSockets(
                providers=(ProviderSocket("control", Protocol.HTTP),),
            ),
        )
        return DeploymentGraph(
            "node-control",
            nodes={"gateway": gateway, "router": router},
            edges={
                "gateway.router-control->router.control": Edge(
                    "gateway.router-control->router.control",
                    provider_role="router",
                    provider_socket="control",
                    consumer_role="gateway",
                    requirement_socket="router-control",
                    protocol=Protocol.HTTP,
                    binding=SocketBinding.ENVIRONMENT,
                )
            },
            runtimes={
                "docker-a": RuntimeRecord(
                    "docker-a",
                    RuntimeKind.DOCKER,
                    ("gateway", "router"),
                )
            },
        )

    def _seed_signing_authority(self) -> None:
        intents = (
            SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY,
            SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY,
        )
        service = SecretProviderRegistrationService(
            lambda: PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))
        )
        provider = service.register_provider(
            RegisterSecretProviderCommand(
                workspace_id="workspace-a",
                provider_id=SecretProviderId("workspace-secrets"),
                provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
                display_name="Workspace secrets",
                endpoint_reference=SecretProviderEndpointReference("secrets-endpoint"),
                credential_reference=SecretReference(
                    "secret://workspace-secrets/provider-token"
                ),
                allowed_reference_prefixes=(
                    SecretReference("secret://workspace-secrets/keys"),
                ),
                allowed_intents=intents,
                admitted_by="operator-a",
                admitted_at="2027-01-15T07:50:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
            )
        )
        references = (
            (
                SecretReference("secret://workspace-secrets/keys/transit"),
                SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY,
            ),
            (
                SecretReference("secret://workspace-secrets/keys/workload"),
                SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY,
            ),
        )
        for reference, intent in references:
            service.register_reference(
                RegisterSecretReferenceCommand(
                    workspace_id="workspace-a",
                    reference=reference,
                    provider_registration_id=provider.registration_id,
                    allowed_intents=(intent,),
                    admitted_by="operator-a",
                    admitted_at="2027-01-15T07:51:00Z",
                    actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
                )
            )
        for purpose, key_id, pem, reference in (
            (
                DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
                "transit-key",
                PUBLIC_KEY_A,
                references[0][0],
            ),
            (
                DelegationKeyPurpose.WORKLOAD_NODE_CONTROL,
                "workload-key",
                PUBLIC_KEY_B,
                references[1][0],
            ),
        ):
            public_key = DelegationPublicKey(
                key_id=key_id,
                algorithm=DelegationKeyAlgorithm.ED25519,
                public_key_pem=pem,
            )
            registration_id = delegation_signing_key_registration_id_for(
                workspace_id="workspace-a",
                purpose=purpose,
                issuer="cpk-server",
                public_key=public_key,
                private_key_reference=reference,
            )
            self.connection.execute(
                """
                INSERT INTO cpk_delegation_signing_keys (
                  registration_id, workspace_id, purpose, issuer, key_id, algorithm,
                  public_key_pem, public_fingerprint_sha256, private_key_reference,
                  admitted_by, admitted_at, status, activated_by, activated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s)
                """,
                (
                    registration_id,
                    "workspace-a",
                    purpose.value,
                    "cpk-server",
                    key_id,
                    public_key.algorithm.value,
                    public_key.public_key_pem,
                    public_key.fingerprint_sha256,
                    reference.reference_id,
                    "operator-a",
                    "2027-01-15T07:52:00Z",
                    "operator-a",
                    "2027-01-15T07:53:00Z",
                ),
            )


if __name__ == "__main__":
    unittest.main()
