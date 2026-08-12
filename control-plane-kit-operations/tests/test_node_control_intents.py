from __future__ import annotations

import ast
import concurrent.futures
from dataclasses import fields, replace
import inspect
import os
from pathlib import Path
import threading
import unittest

import psycopg
from psycopg.types.json import Jsonb

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
    MapControlState,
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
    RegisterDelegationSigningKeyCommand,
    delegation_signing_key_registration_id_for,
)
from control_plane_kit_operations.delegation_key_generation import (
    AdmitGeneratedDelegationSigningKey,
    DelegationKeyGenerationEvidence,
    DelegationKeyGenerationService,
    GenerateDelegationSigningKey,
)
from control_plane_kit_operations.postgres import (
    DelegationSigningKeyStore,
    PostgresUnitOfWork,
    install_schema,
)
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    RegisterSecretProviderCommand,
    RegisterSecretReferenceCommand,
    SecretProviderKind,
    SecretProviderRegistrationService,
    secret_use_correlation_for,
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
        self.entries = 0
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
        self._factory.entries += 1
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
        try:
            install_schema(self.connection)
            self._reset_fixture()
        except BaseException:
            self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
            self.connection.close()
            raise

    def tearDown(self) -> None:
        try:
            self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        finally:
            self.connection.close()

    def _reset_fixture(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self._seed_graph_truth()
        self._seed_signing_authority()
        self.tracker = TrackingUnitOfWorkFactory(self.database_url)
        self.ids = GeneratedIds()

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

    def test_deferred_signing_families_cannot_substitute(self) -> None:
        result = self.service().execute(self.command())
        error_type = self.contract("NodeControlIntentError")
        transit_type = self.contract(
            "DeferredGatewayNodeControlTransitSigningRequest"
        )
        workload_type = self.contract("DeferredWorkloadNodeControlSigningRequest")
        with self.assertRaises(error_type):
            transit_type(
                result.transit_signing.key_registration_id,
                result.transit_signing.authorization_id,
                result.workload_signing.grant,
            )
        with self.assertRaises(error_type):
            workload_type(
                result.workload_signing.key_registration_id,
                result.workload_signing.authorization_id,
                result.transit_signing.grant,
            )

    def test_exact_authority_commits_one_reference_only_preparation(self) -> None:
        command = self.command()
        result = self.service().execute(command)

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
        self.assertEqual(result.attempt.attempt_id, "attempt-a")
        self.assertEqual(result.attempt.actor_subject, "operator-a")
        self.assertEqual(result.attempt.transit_grant.attempt_id, "attempt-a")
        self.assertEqual(result.attempt.transit_grant.gateway_node_id, command.gateway_node_id)
        self.assertEqual(result.attempt.transit_grant.target, command.request.target)
        self.assertEqual(result.attempt.transit_grant.variable_name, command.request.variable_name)
        self.assertIs(result.attempt.transit_grant.operation, command.request.operation)
        self.assertIs(result.attempt.transit_grant.command_codec, command.request.command_codec)
        self.assertEqual(result.attempt.transit_grant.request_id, command.request.request_id)
        self.assertEqual(result.attempt.transit_grant.jti, "transit-jti-a")
        self.assertEqual(result.attempt.transit_grant.issued_at, 100)
        self.assertEqual(result.attempt.transit_grant.expires_at, 160)
        self.assertEqual(result.attempt.workload_grant.target, command.request.target)
        self.assertEqual(result.attempt.workload_grant.variable_name, command.request.variable_name)
        self.assertIs(result.attempt.workload_grant.operation, command.request.operation)
        self.assertIs(result.attempt.workload_grant.command_codec, command.request.command_codec)
        self.assertEqual(result.attempt.workload_grant.request_id, command.request.request_id)
        self.assertEqual(result.attempt.workload_grant.jti, "workload-jti-a")
        self.assertEqual(result.attempt.workload_grant.issued_at, 100)
        self.assertEqual(result.attempt.workload_grant.expires_at, 160)
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
            SELECT use_intent, actor_subject, correlation_id, authorization_id,
                   secret_reference
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
        by_intent = {row[0]: row for row in rows}
        expected_correlations = {
            SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY.value:
                secret_use_correlation_for(
                    workspace_id="workspace-a",
                    reference=SecretReference(
                        "secret://workspace-secrets/keys/transit"
                    ),
                    intent=SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY,
                    actor_subject="operator-a",
                    operation_id="attempt-a",
                ),
            SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY.value:
                secret_use_correlation_for(
                    workspace_id="workspace-a",
                    reference=SecretReference(
                        "secret://workspace-secrets/keys/workload"
                    ),
                    intent=SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY,
                    actor_subject="operator-a",
                    operation_id="attempt-a",
                ),
        }
        for intent, correlation in expected_correlations.items():
            self.assertEqual(by_intent[intent][2], correlation)
        self.assertEqual(
            by_intent[
                SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY.value
            ][3],
            result.transit_signing.authorization_id,
        )
        self.assertEqual(
            by_intent[
                SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY.value
            ][3],
            result.workload_signing.authorization_id,
        )
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
                    entries_before = self.tracker.entries
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
                    self.assertEqual(self.tracker.entries, entries_before)

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

        for substitute in (
            PolicyScope.INSTANCE_WORKSPACE_EDIT,
            PolicyScope.PLAN_EXECUTE,
            PolicyScope.GATEWAY_PROBE_USE,
        ):
            with self.subTest(unrelated_substitute=substitute):
                entries_before = self.tracker.entries
                with self.assertRaises(denied):
                    self.service().execute(
                        self.command(
                            context=self.context(
                                scopes=(
                                    PolicyScope.NODE_CONTROL_APPLY,
                                    substitute,
                                    PolicyScope.DELEGATION_KEY_USE,
                                    PolicyScope.SECRET_PROVIDER_USE,
                                )
                            )
                        )
                    )
                self.assertEqual(self.tracker.entries, entries_before)

    def test_read_and_apply_both_authorize_their_exact_declared_contract(self) -> None:
        apply_result = self.service().execute(self.command())
        self.assertIs(
            apply_result.attempt.request.operation,
            NodeControlOperation.APPLY_COMMAND,
        )

        self._reset_fixture()
        read_request = self.request(operation=NodeControlOperation.READ_STATE)
        read_result = self.service().execute(
            self.command(
                context=self.context(
                    scopes=(
                        PolicyScope.NODE_CONTROL_READ,
                        PolicyScope.NODE_CONTROL_EXECUTE,
                        PolicyScope.DELEGATION_KEY_USE,
                        PolicyScope.SECRET_PROVIDER_USE,
                    )
                ),
                request=read_request,
            )
        )
        self.assertIs(
            read_result.attempt.request.operation,
            NodeControlOperation.READ_STATE,
        )
        self.assertIsNone(read_result.attempt.transit_grant.command_codec)
        self.assertIsNone(read_result.attempt.workload_grant.command_codec)

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

    def test_realized_graph_target_map_and_contract_drift_are_closed(self) -> None:
        with self.assertRaises(core.NodeControlContractError):
            self._graph(True, include_apply=False)
        with self.assertRaises(ValueError):
            self._graph(True, target_protocol=Protocol.TCP).descriptor()
        with self.assertRaises(ValueError):
            self._graph(True, include_target_socket=False).descriptor()
        cases = (
            ("gateway-protocol", self._graph(True, gateway_protocol=Protocol.TCP)),
            ("runtime", self._graph(True, same_runtime=False)),
            ("edge", self._graph(True, include_edge=False)),
            ("surface", self._graph(True, surface_socket="other")),
        )
        not_found = self.contract("NodeControlIntentNotFound")
        for name, graph in cases:
            with self.subTest(name=name):
                self._reset_fixture()
                self._replace_realized_graph(graph)
                with self.assertRaises(not_found) as caught:
                    self.service().execute(self.command())
                self.assertLessEqual(len(str(caught.exception)), 128)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assert_no_intent_rows()

        self._reset_fixture()
        missing_socket_request = replace(
            self.request(),
            target=replace(
                self.request().target,
                provider_socket_name=self.reference(
                    NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                    "missing-control",
                ),
            ),
        )
        with self.assertRaises(not_found):
            self.service().execute(self.command(request=missing_socket_request))
        self.assert_no_intent_rows()

        self._reset_fixture()
        map_request = replace(
            self.request(),
            command_codec=ControlPlaneCommandCodec.REPLACE_MAP_V1,
            payload=NodeControlPayload(
                ControlPlaneCommandCodec.REPLACE_MAP_V1,
                MapControlState((("route", "blue"),)),
            ),
        )
        with self.assertRaises(not_found):
            self.service().execute(self.command(request=map_request))
        self.assert_no_intent_rows()

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

    def test_dual_signing_authority_rejects_ambiguity_and_substitution(self) -> None:
        mutations = (
            "missing-transit",
            "ambiguous-transit",
            "purpose-substitution",
            "reused-public-material",
            "reused-private-reference",
            "wrong-reference-intent",
            "inactive-provider",
            "cross-paired-references",
        )
        conflict = self.contract("NodeControlIntentConflict")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._reset_fixture()
                self._mutate_signing_authority(mutation)
                with self.assertRaises(conflict) as caught:
                    self.service().execute(self.command())
                self.assertLessEqual(len(str(caught.exception)), 128)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn("secret://", str(caught.exception))
                self.assert_no_intent_rows()

    def test_same_request_serializes_to_one_winner_and_exact_replay(self) -> None:
        entered = threading.Barrier(2)

        def authorize(suffix: str):
            ids = iter(
                (
                    f"attempt-{suffix}",
                    f"transit-jti-{suffix}",
                    f"workload-jti-{suffix}",
                )
            )
            service = self.contract("NodeControlIntentAuthorizationService")(
                TrackingUnitOfWorkFactory(self.database_url),
                epoch_clock=lambda: 100,
                clock=lambda: "2027-01-15T08:00:00Z",
                id_factory=lambda: next(ids),
                grant_lifetime_seconds=60,
            )
            entered.wait(timeout=5)
            return service.execute(self.command())

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = (pool.submit(authorize, "a"), pool.submit(authorize, "b"))
            results = tuple(future.result(timeout=15) for future in futures)

        self.assertEqual(sorted(result.replayed for result in results), [False, True])
        self.assertEqual(results[0].attempt, results[1].attempt)
        self.assertEqual(results[0].transit_signing, results[1].transit_signing)
        self.assertEqual(results[0].workload_signing, results[1].workload_signing)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_node_control_attempts"
            ).fetchone(),
            (1,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_secret_use_authorizations"
            ).fetchone(),
            (2,),
        )

    def test_secret_authority_rows_remain_locked_until_transaction_exit(self) -> None:
        abort = RuntimeError("cancel authorization transaction")
        with self.assertRaisesRegex(RuntimeError, "cancel authorization"):
            with PostgresUnitOfWork(
                lambda: psycopg.connect(self.database_url)
            ) as unit_of_work:
                authorize_in_unit_of_work = getattr(
                    operations.secret_providers,
                    "authorize_secret_use_in_unit_of_work",
                    None,
                )
                self.assertIsNotNone(authorize_in_unit_of_work)
                authorize_in_unit_of_work(
                    unit_of_work,
                    AuthorizeSecretUse(
                        workspace_id="workspace-a",
                        reference=SecretReference(
                            "secret://workspace-secrets/keys/transit"
                        ),
                        intent=(
                            SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY
                        ),
                        actor_subject="operator-a",
                        correlation_id="node-control-lock-test",
                        requested_at="2027-01-15T08:00:00Z",
                        actor_scopes=(PolicyScope.SECRET_PROVIDER_USE,),
                        operation_id="attempt-lock-test",
                    ),
                )
                for relation, predicate in (
                    (
                        "cpk_secret_references",
                        "secret_reference="
                        "'secret://workspace-secrets/keys/transit'",
                    ),
                    (
                        "cpk_secret_providers",
                        "provider_id='workspace-secrets'",
                    ),
                ):
                    contender = psycopg.connect(self.database_url)
                    try:
                        contender.execute("SET LOCAL lock_timeout = '250ms'")
                        with self.assertRaises(psycopg.errors.LockNotAvailable):
                            contender.execute(
                                f"UPDATE {relation} SET metadata=metadata "
                                f"WHERE {predicate}"
                            )
                    finally:
                        contender.rollback()
                        contender.close()
                raise abort

        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_secret_use_authorizations"
            ).fetchone(),
            (0,),
        )
        self.connection.execute(
            "UPDATE cpk_secret_references SET metadata=metadata "
            "WHERE secret_reference="
            "'secret://workspace-secrets/keys/transit'"
        )
        self.connection.execute(
            "UPDATE cpk_secret_providers SET metadata=metadata "
            "WHERE provider_id='workspace-secrets'"
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
        generated_source = inspect.getsource(
            DelegationKeyGenerationService.admit_generated
        )
        self.assertLess(
            generated_source.index("lock_purpose_for_lifecycle"),
            generated_source.index("secret_references.register"),
        )

    def test_key_lifecycle_and_authority_selection_use_opposite_purpose_locks(
        self,
    ) -> None:
        purpose = DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT
        self._insert_verify_only_key("activate-key", "rotation-a")
        self._insert_verify_only_key("retire-key", "rotation-b")
        candidate = RegisterDelegationSigningKeyCommand(
            workspace_id="workspace-a",
            purpose=purpose,
            issuer="rotation-c",
            public_key=DelegationPublicKey(
                key_id="register-key",
                algorithm=DelegationKeyAlgorithm.ED25519,
                public_key_pem=PUBLIC_KEY_B,
            ),
            private_key_reference=SecretReference(
                "secret://workspace-secrets/keys/transit"
            ),
            admitted_by="operator-a",
            admitted_at="2027-01-15T08:00:00Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,),
        ).candidate()

        holder = psycopg.connect(self.database_url)
        try:
            DelegationSigningKeyStore(holder).require_unambiguous_active(
                "workspace-a",
                purpose,
            )
            actions = (
                lambda store: store.register(candidate),
                lambda store: store.activate(
                    "workspace-a",
                    purpose,
                    "rotation-a",
                    "activate-key",
                    activated_by="operator-a",
                    activated_at="2027-01-15T08:00:00Z",
                ),
                lambda store: store.retire(
                    "workspace-a",
                    purpose,
                    "rotation-b",
                    "retire-key",
                    retired_by="operator-a",
                    retired_at="2027-01-15T08:00:00Z",
                ),
                lambda store: store.revoke(
                    "workspace-a",
                    purpose,
                    "cpk-server",
                    "transit-key",
                    revoked_by="operator-a",
                    revoked_at="2027-01-15T08:00:00Z",
                ),
            )
            for action in actions:
                contender = psycopg.connect(self.database_url)
                try:
                    contender.execute("SET LOCAL lock_timeout = '250ms'")
                    with self.assertRaises(psycopg.errors.LockNotAvailable):
                        action(DelegationSigningKeyStore(contender))
                finally:
                    contender.rollback()
                    contender.close()
        finally:
            holder.rollback()
            holder.close()

        retry = psycopg.connect(self.database_url)
        try:
            registered = DelegationSigningKeyStore(retry).register(candidate)
            retry.commit()
        finally:
            retry.close()
        self.assertEqual(registered.key_id, "register-key")
        self.connection.execute(
            "DELETE FROM cpk_delegation_signing_keys WHERE registration_id=%s",
            (registered.registration_id,),
        )

        exclusive = psycopg.connect(self.database_url)
        try:
            DelegationSigningKeyStore(exclusive).lock_purpose_for_lifecycle(
                "workspace-a",
                purpose,
            )
            contender = psycopg.connect(self.database_url)
            try:
                contender.execute("SET LOCAL lock_timeout = '250ms'")
                with self.assertRaises(psycopg.errors.LockNotAvailable):
                    DelegationSigningKeyStore(contender).require_unambiguous_active(
                        "workspace-a",
                        purpose,
                    )
            finally:
                contender.rollback()
                contender.close()
        finally:
            exclusive.rollback()
            exclusive.close()

        retry = psycopg.connect(self.database_url)
        try:
            selected = DelegationSigningKeyStore(retry).require_unambiguous_active(
                "workspace-a",
                purpose,
            )
            retry.rollback()
        finally:
            retry.close()
        self.assertEqual(selected.key_id, "transit-key")

    def test_generated_key_fold_locks_purpose_before_secret_reference(self) -> None:
        reference = SecretReference(
            "secret://workspace-secrets/keys/generated-transit"
        )
        service = DelegationKeyGenerationService(
            lambda: PostgresUnitOfWork(
                lambda: psycopg.connect(self.database_url)
            )
        )
        grant = service.prepare(
            GenerateDelegationSigningKey(
                workspace_id="workspace-a",
                provider_registration_id=self.provider_registration_id,
                reference=reference,
                purpose=DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
                issuer="generated-issuer",
                actor_subject="operator-a",
                correlation_id="generated-transit-a",
                requested_at="2027-01-15T08:00:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_GENERATE,),
            )
        )
        evidence = DelegationKeyGenerationEvidence(
            workspace_id="workspace-a",
            reference=reference,
            purpose=DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
            issuer="generated-issuer",
            correlation_id="generated-transit-a",
            version_id="version-generated-a",
            version_number=1,
            public_key=DelegationPublicKey(
                key_id="generated-transit-key",
                algorithm=DelegationKeyAlgorithm.ED25519,
                public_key_pem=PUBLIC_KEY_B,
            ),
            replayed=False,
        )
        command = AdmitGeneratedDelegationSigningKey(
            grant=grant,
            evidence=evidence,
            admitted_by="operator-a",
            admitted_at="2027-01-15T08:00:01Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,),
        )

        holder = psycopg.connect(self.database_url)
        try:
            DelegationSigningKeyStore(holder).require_unambiguous_active(
                "workspace-a",
                DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
            )

            def timed_connection():
                connection = psycopg.connect(self.database_url)
                connection.execute("SET LOCAL lock_timeout = '250ms'")
                return connection

            timed_service = DelegationKeyGenerationService(
                lambda: PostgresUnitOfWork(timed_connection)
            )
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                timed_service.admit_generated(command)
        finally:
            holder.rollback()
            holder.close()

        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_secret_references "
                "WHERE secret_reference=%s",
                (reference.reference_id,),
            ).fetchone(),
            (0,),
        )
        admitted = service.admit_generated(command)
        self.assertEqual(admitted.secret_reference.reference, reference)
        self.assertEqual(admitted.signing_key.key_id, "generated-transit-key")

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
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names = (node.module or "",)
            else:
                continue
            for name in names:
                imported_names.add(name)
                self.assertFalse(
                    any(name == value or name.startswith(value + ".") for value in forbidden),
                    name,
                )
        self.assertFalse(
            any("product" in name or "metadata" in name for name in imported_names)
        )
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        for forbidden_name in (
            "SecretResolutionGrant",
            "RegisteredProductStore",
            "registered_products",
            "metadata",
        ):
            self.assertNotIn(forbidden_name, identifiers | attributes)
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
        authored = DeploymentGraph("authored-source-without-runtime-truth")
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

    def _graph(
        self,
        with_surface: bool,
        *,
        gateway_protocol: Protocol = Protocol.HTTP,
        target_protocol: Protocol = Protocol.HTTP,
        same_runtime: bool = True,
        include_edge: bool = True,
        include_target_socket: bool = True,
        surface_socket: str = "control",
        include_apply: bool = True,
        apply_codec: ControlPlaneCommandCodec = (
            ControlPlaneCommandCodec.REPLACE_SCALAR_V1
        ),
    ) -> DeploymentGraph:
        operation_contracts = [
            ControlPlaneVariableOperationContract(
                NodeControlOperation.READ_STATE,
                None,
                ControlPlaneResultCodec.STATE_V1,
            )
        ]
        if include_apply:
            operation_contracts.append(
                ControlPlaneVariableOperationContract(
                    NodeControlOperation.APPLY_COMMAND,
                    apply_codec,
                    ControlPlaneResultCodec.TRANSITION_V1,
                )
            )
        variable = ControlPlaneVariableDescriptor(
            variable_name=self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                "routing",
            ),
            kind=ControlPlaneVariableKind.SCALAR,
            state_codec=ControlPlaneStateCodec.SCALAR_V1,
            operation_contracts=tuple(operation_contracts),
        )
        surface = WorkloadNodeControlSurfaceDescriptor(
            self.reference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                surface_socket,
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
                providers=(ProviderSocket("control", gateway_protocol),),
            ),
        )
        target_runtime = "docker-a" if same_runtime else "docker-b"
        router = Node(
            node_id="router",
            block_family=BlockFamily.APPLICATION,
            block_spec=BlockSpec(
                "router",
                capabilities=(CapabilityName.NODE_CONTROLLABLE,) if with_surface else (),
                control_surfaces=(surface,) if with_surface else (),
            ),
            kind="container-server",
            runtime_id=target_runtime,
            sockets=BlockSockets(
                providers=(
                    (ProviderSocket("control", target_protocol),)
                    if include_target_socket
                    else ()
                ),
            ),
        )
        edges = (
            {
                "gateway.router-control->router.control": Edge(
                    "gateway.router-control->router.control",
                    provider_role="router",
                    provider_socket="control",
                    consumer_role="gateway",
                    requirement_socket="router-control",
                    protocol=Protocol.HTTP,
                    binding=SocketBinding.ENVIRONMENT,
                )
            }
            if include_edge
            else {}
        )
        runtimes = {
            "docker-a": RuntimeRecord(
                "docker-a",
                RuntimeKind.DOCKER,
                ("gateway",) if not same_runtime else ("gateway", "router"),
            )
        }
        if not same_runtime:
            runtimes["docker-b"] = RuntimeRecord(
                "docker-b",
                RuntimeKind.DOCKER,
                ("router",),
            )
        return DeploymentGraph(
            "node-control",
            nodes={"gateway": gateway, "router": router},
            edges=edges,
            runtimes=runtimes,
        )

    def _replace_realized_graph(self, graph: DeploymentGraph) -> None:
        projection = RealizedGraphProjectionRecord.from_graph(
            projection_id="projection-current",
            workspace_id="workspace-a",
            source_authored_graph_id="graph-current",
            projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
            projection_key="node-control-current",
            graph=graph,
            created_by="operator-a",
            created_at="2027-01-15T07:56:00Z",
        )
        self.connection.execute(
            "UPDATE cpk_realized_graph_projections "
            "SET projection_digest=%s, graph_descriptor=%s "
            "WHERE projection_id='projection-current'",
            (projection.projection_digest, Jsonb(projection.graph_descriptor)),
        )

    def _mutate_signing_authority(self, mutation: str) -> None:
        transit = DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT.value
        workload = DelegationKeyPurpose.WORKLOAD_NODE_CONTROL.value
        if mutation == "missing-transit":
            self.connection.execute(
                "DELETE FROM cpk_delegation_signing_keys WHERE purpose=%s",
                (transit,),
            )
        elif mutation == "ambiguous-transit":
            self._insert_active_key(
                purpose=DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
                key_id="transit-key-b",
                issuer="other-issuer",
                public_key_pem=PUBLIC_KEY_B,
                reference=SecretReference(
                    "secret://workspace-secrets/keys/transit"
                ),
            )
        elif mutation == "purpose-substitution":
            self.connection.execute(
                "DELETE FROM cpk_delegation_signing_keys WHERE purpose=%s",
                (workload,),
            )
            self.connection.execute(
                "UPDATE cpk_delegation_signing_keys SET purpose=%s "
                "WHERE purpose=%s",
                (workload, transit),
            )
        elif mutation == "reused-public-material":
            transit_row = self.connection.execute(
                "SELECT public_key_pem, public_fingerprint_sha256 "
                "FROM cpk_delegation_signing_keys WHERE purpose=%s",
                (transit,),
            ).fetchone()
            self.connection.execute(
                "UPDATE cpk_delegation_signing_keys "
                "SET public_key_pem=%s, public_fingerprint_sha256=%s "
                "WHERE purpose=%s",
                (*transit_row, workload),
            )
        elif mutation == "reused-private-reference":
            self.connection.execute(
                "UPDATE cpk_delegation_signing_keys "
                "SET private_key_reference="
                "'secret://workspace-secrets/keys/transit' "
                "WHERE purpose=%s",
                (workload,),
            )
        elif mutation == "wrong-reference-intent":
            self.connection.execute(
                "UPDATE cpk_secret_references SET allowed_intents=%s "
                "WHERE secret_reference="
                "'secret://workspace-secrets/keys/workload'",
                (
                    Jsonb(
                        [
                            SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY.value
                        ]
                    ),
                ),
            )
        elif mutation == "inactive-provider":
            self.connection.execute(
                "UPDATE cpk_secret_providers SET status='revoked', "
                "revoked_by='operator-a', revoked_at='2027-01-15T08:00:00Z'"
            )
        elif mutation == "cross-paired-references":
            self.connection.execute(
                "UPDATE cpk_delegation_signing_keys SET private_key_reference="
                "CASE purpose WHEN %s THEN "
                "'secret://workspace-secrets/keys/workload' "
                "ELSE 'secret://workspace-secrets/keys/transit' END "
                "WHERE purpose IN (%s,%s)",
                (transit, transit, workload),
            )
        else:
            raise AssertionError(f"unknown mutation {mutation}")

    def _insert_active_key(
        self,
        *,
        purpose: DelegationKeyPurpose,
        key_id: str,
        issuer: str,
        public_key_pem: str,
        reference: SecretReference,
    ) -> None:
        public_key = DelegationPublicKey(
            key_id=key_id,
            algorithm=DelegationKeyAlgorithm.ED25519,
            public_key_pem=public_key_pem,
        )
        registration_id = delegation_signing_key_registration_id_for(
            workspace_id="workspace-a",
            purpose=purpose,
            issuer=issuer,
            public_key=public_key,
            private_key_reference=reference,
        )
        self.connection.execute(
            """
            INSERT INTO cpk_delegation_signing_keys (
              registration_id, workspace_id, purpose, issuer, key_id, algorithm,
              public_key_pem, public_fingerprint_sha256, private_key_reference,
              admitted_by, admitted_at, status, activated_by, activated_at
            ) VALUES (%s,'workspace-a',%s,%s,%s,%s,%s,%s,%s,'operator-a',
                      '2027-01-15T07:52:00Z','active','operator-a',
                      '2027-01-15T07:53:00Z')
            """,
            (
                registration_id,
                purpose.value,
                issuer,
                key_id,
                public_key.algorithm.value,
                public_key.public_key_pem,
                public_key.fingerprint_sha256,
                reference.reference_id,
            ),
        )

    def _insert_verify_only_key(self, key_id: str, issuer: str) -> None:
        public_key = DelegationPublicKey(
            key_id=key_id,
            algorithm=DelegationKeyAlgorithm.ED25519,
            public_key_pem=PUBLIC_KEY_B,
        )
        reference = SecretReference("secret://workspace-secrets/keys/transit")
        purpose = DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT
        registration_id = delegation_signing_key_registration_id_for(
            workspace_id="workspace-a",
            purpose=purpose,
            issuer=issuer,
            public_key=public_key,
            private_key_reference=reference,
        )
        self.connection.execute(
            """
            INSERT INTO cpk_delegation_signing_keys (
              registration_id, workspace_id, purpose, issuer, key_id, algorithm,
              public_key_pem, public_fingerprint_sha256, private_key_reference,
              admitted_by, admitted_at, status
            ) VALUES (%s,'workspace-a',%s,%s,%s,%s,%s,%s,%s,'operator-a',
                      '2027-01-15T07:52:00Z','verify-only')
            """,
            (
                registration_id,
                purpose.value,
                issuer,
                key_id,
                public_key.algorithm.value,
                public_key.public_key_pem,
                public_key.fingerprint_sha256,
                reference.reference_id,
            ),
        )

    def assert_no_intent_rows(self) -> None:
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

    def _seed_signing_authority(self) -> None:
        intents = (
            SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY,
            SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY,
            SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY,
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
        self.provider_registration_id = provider.registration_id
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
