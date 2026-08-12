from __future__ import annotations

from dataclasses import dataclass, field
from inspect import getsource, signature
import os
import unittest
import uuid

import psycopg

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
from control_plane_kit_core.operations import (
    ControlPlaneServiceRole,
    canonical_operator_read_projection_set,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityReference,
)
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.delegation_signing_keys import (
    RegisterDelegationSigningKeyCommand,
)
from control_plane_kit_operations.ingress_authorities import (
    CloudflareZoneIngressAuthority,
)
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.cpk_server import (
    CpkServerApplicationError,
    CpkServerReadService,
)
from control_plane_kit_operations.postgres.current_schema_contract import (
    CURRENT_POSTGRES_SCHEMA_CONTRACT,
)
from control_plane_kit_operations.postgres.delegation_signing_key_store import (
    DelegationSigningKeyStore,
)
from control_plane_kit_operations.postgres.ingress_authority_store import (
    IngressAuthorityStore,
)
from control_plane_kit_operations.postgres.observed_state import (
    PostgresObservedStateStore,
)
from control_plane_kit_operations.postgres.runtime_authority_store import (
    RuntimeAuthorityDeliveryStore,
    RuntimeAuthorityStore,
)
from control_plane_kit_operations.postgres.secret_provider_store import (
    SecretProviderStore,
    SecretReferenceStore,
)
from control_plane_kit_operations.read_pages import (
    DelegationKeyReadCursor,
    IdentityReadCursor,
    ReadCollection,
    ReadPageRequest,
    WorkspaceReadScope,
)
from control_plane_kit_operations.read_services import InstanceReadService
from control_plane_kit_operations.records import (
    ObservationRecord,
    ObservationStatus,
)
from control_plane_kit_operations.runtime_authorities import (
    LocalDockerSocketAuthority,
)
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand,
    RegisterSecretReferenceCommand,
    SecretProviderKind,
)


_PAGED_OPERATIONS = frozenset(
    {
        "read.delegation-keys",
        "read.ingress-authorities",
        "read.observed-state",
        "read.runtime-authorities",
        "read.runtime-authority-deliveries",
        "read.secret-providers",
        "read.secret-references",
    }
)


class _Rows:
    def fetchall(self) -> tuple[tuple[object, ...], ...]:
        return ()


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, parameters: tuple[object, ...]) -> _Rows:
        self.calls.append((query, parameters))
        return _Rows()


def _identity_request(
    collection: ReadCollection,
    after: str,
) -> ReadPageRequest:
    scope = WorkspaceReadScope("workspace-a")
    return ReadPageRequest(
        collection,
        scope,
        2,
        IdentityReadCursor(collection, scope, after),
    )


class CurrentMetadataPageContractTests(unittest.TestCase):
    def test_core_advertises_all_seven_current_metadata_reads_as_pages(self) -> None:
        projections = canonical_operator_read_projection_set()

        for operation_id in _PAGED_OPERATIONS:
            with self.subTest(operation_id=operation_id):
                projection = projections.projection(operation_id)
                self.assertTrue(projection.paged)
                self.assertEqual(projection.max_page_size, 100)

    def test_identity_page_queries_use_exclusive_ascending_seek_and_limit_plus_one(self) -> None:
        cases = (
            (
                PostgresObservedStateStore,
                "latest_page",
                ReadCollection.LATEST_OBSERVATIONS,
                "subject-a",
                "subject_id > %s",
                "ORDER BY subject_id ASC, observed_at DESC, observation_id DESC",
            ),
            (
                RuntimeAuthorityStore,
                "active_page",
                ReadCollection.RUNTIME_AUTHORITIES,
                "runtime-a",
                "authority_ref > %s",
                "ORDER BY authority_ref ASC",
            ),
            (
                RuntimeAuthorityDeliveryStore,
                "active_page",
                ReadCollection.RUNTIME_AUTHORITY_DELIVERIES,
                "runtime-a",
                "authority_ref > %s",
                "ORDER BY authority_ref ASC",
            ),
            (
                IngressAuthorityStore,
                "active_page",
                ReadCollection.INGRESS_AUTHORITIES,
                "ingress-a",
                "authority_ref > %s",
                "ORDER BY authority_ref ASC",
            ),
            (
                SecretProviderStore,
                "active_page",
                ReadCollection.SECRET_PROVIDERS,
                "provider-a",
                "provider_id > %s",
                "ORDER BY provider_id ASC",
            ),
            (
                SecretReferenceStore,
                "active_page",
                ReadCollection.SECRET_REFERENCES,
                "registration-a",
                "registration_id > %s",
                "ORDER BY registration_id ASC",
            ),
        )
        for store_type, method_name, collection, after, seek, order in cases:
            with self.subTest(collection=collection.value):
                connection = _RecordingConnection()
                page = getattr(store_type(connection), method_name)(
                    _identity_request(collection, after)
                )

                self.assertEqual(page.items, ())
                self.assertIsNone(page.next_cursor)
                self.assertEqual(len(connection.calls), 1)
                query, parameters = connection.calls[0]
                normalized = " ".join(query.split())
                self.assertIn(seek, normalized)
                self.assertIn(order, normalized)
                self.assertIn("LIMIT %s", normalized)
                self.assertEqual(parameters[-3:], ("workspace-a", after, 3))

    def test_observation_page_selects_exactly_one_latest_row_per_subject(self) -> None:
        connection = _RecordingConnection()

        PostgresObservedStateStore(connection).latest_page(
            _identity_request(ReadCollection.LATEST_OBSERVATIONS, "subject-a")
        )

        normalized = " ".join(connection.calls[0][0].split())
        self.assertIn("SELECT DISTINCT ON (subject_id)", normalized)
        self.assertIn("WHERE workspace_id = %s AND subject_id > %s", normalized)

    def test_delegation_page_uses_complete_unique_tuple_position(self) -> None:
        connection = _RecordingConnection()
        scope = WorkspaceReadScope("workspace-a")
        request = ReadPageRequest(
            ReadCollection.DELEGATION_SIGNING_KEYS,
            scope,
            2,
            DelegationKeyReadCursor(
                ReadCollection.DELEGATION_SIGNING_KEYS,
                scope,
                DelegationKeyPurpose.GATEWAY_PROBE,
                "issuer-a",
                "key-a",
            ),
        )

        page = DelegationSigningKeyStore(connection).workspace_page(request)

        self.assertEqual(page.items, ())
        query, parameters = connection.calls[0]
        normalized = " ".join(query.split())
        self.assertIn("(purpose, issuer, key_id) > (%s, %s, %s)", normalized)
        self.assertIn("ORDER BY purpose ASC, issuer ASC, key_id ASC", normalized)
        self.assertIn("LIMIT %s", normalized)
        self.assertEqual(
            parameters,
            ("workspace-a", "gateway-probe", "issuer-a", "key-a", 3),
        )

    def test_cursor_positions_are_unique_in_the_selected_relational_views(self) -> None:
        indexes = {value.name: value for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.indexes}
        constraints = {
            value.name: value for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints
        }
        expected_indexes = {
            "cpk_runtime_authorities_active_ref": ("workspace_id", "authority_ref"),
            "cpk_runtime_authority_deliveries_active_ref": (
                "workspace_id",
                "authority_ref",
            ),
            "cpk_ingress_authorities_active_ref": ("workspace_id", "authority_ref"),
            "cpk_secret_providers_active_identity": ("workspace_id", "provider_id"),
        }
        for name, columns in expected_indexes.items():
            with self.subTest(name=name):
                self.assertTrue(indexes[name].unique)
                self.assertEqual(indexes[name].key_entries, columns)
                self.assertIn("status = 'active'", indexes[name].predicate or "")
        self.assertEqual(
            constraints["cpk_secret_references_pkey"].local_columns,
            ("registration_id",),
        )
        self.assertEqual(
            constraints[
                "cpk_delegation_signing_keys_workspace_id_purpose_issuer_key_key"
            ].local_columns,
            ("workspace_id", "purpose", "issuer", "key_id"),
        )

    def test_public_read_service_owns_only_page_selectors(self) -> None:
        expected = {
            "observed_state": "latest_page",
            "runtime_authorities": "active_page",
            "runtime_authority_deliveries": "active_page",
            "ingress_authorities": "active_page",
            "secret_providers": "active_page",
            "secret_references": "active_page",
            "delegation_signing_keys": "workspace_page",
        }
        forbidden = {
            "latest_for_workspace",
            "list_active",
            "list_workspace",
        }
        for method_name, selector in expected.items():
            with self.subTest(method=method_name):
                method = getattr(InstanceReadService, method_name)
                self.assertEqual(tuple(signature(method).parameters), ("self", "request"))
                source = getsource(method)
                self.assertIn(f".{selector}(", source)
                self.assertTrue(all(f".{name}(" not in source for name in forbidden))

    def test_collection_only_wrapper_types_are_retired(self) -> None:
        from control_plane_kit_operations import read_services

        for name in (
            "ObservedStateReadModel",
            "RuntimeAuthorityCollectionReadModel",
            "IngressAuthorityCollectionReadModel",
            "SecretMetadataCollectionReadModel",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(read_services, name))


_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
-----END PUBLIC KEY-----
"""


class CurrentMetadataPostgresPageTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.schema = f"metadata_pages_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')
        install_schema(self.connection)
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.connection.close()

    def test_all_seven_collections_traverse_real_rows_without_duplicate_or_skip(
        self,
    ) -> None:
        observed = PostgresObservedStateStore(self.connection)
        runtimes = RuntimeAuthorityStore(self.connection)
        deliveries = RuntimeAuthorityDeliveryStore(self.connection)
        ingresses = IngressAuthorityStore(self.connection)
        providers = SecretProviderStore(self.connection)
        references = SecretReferenceStore(self.connection)
        keys = DelegationSigningKeyStore(self.connection)

        for identity in ("a", "b", "c"):
            observed.put(
                ObservationRecord(
                    observation_id=f"observation-{identity}",
                    workspace_id="workspace-a",
                    subject_id=f"subject-{identity}",
                    status=ObservationStatus.HEALTHY,
                    observed_at="2026-08-12T12:00:00Z",
                )
            )
            runtimes.register(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference(f"runtime-{identity}"),
                runtime_kind=RuntimeKind.DOCKER,
                authority=LocalDockerSocketAuthority(),
                admitted_by="operator-a",
                admitted_at="2026-08-12T12:01:00Z",
            )
            deliveries.register(
                workspace_id="workspace-a",
                delivery=RuntimeAuthorityAccessDelivery(
                    RuntimeAuthorityReference(f"runtime-{identity}"),
                    RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
                ),
                admitted_by="operator-a",
                admitted_at="2026-08-12T12:02:00Z",
            )
            ingresses.register(
                workspace_id="workspace-a",
                authority_ref=IngressAuthorityReference(f"ingress-{identity}"),
                authority=self._ingress_authority(identity),
                admitted_by="operator-a",
                admitted_at="2026-08-12T12:03:00Z",
            )
            provider = providers.register(self._provider(identity).candidate())
            references.register(
                self._reference(identity, provider.registration_id).candidate()
            )
            keys.register(self._delegation_key(identity).candidate())

        cases = (
            (
                ReadCollection.LATEST_OBSERVATIONS,
                observed.latest_page,
                lambda value: value.subject_id,
                ["subject-a", "subject-b", "subject-c"],
            ),
            (
                ReadCollection.RUNTIME_AUTHORITIES,
                runtimes.active_page,
                lambda value: value.authority_ref.reference_id,
                ["runtime-a", "runtime-b", "runtime-c"],
            ),
            (
                ReadCollection.RUNTIME_AUTHORITY_DELIVERIES,
                deliveries.active_page,
                lambda value: value.authority_ref.reference_id,
                ["runtime-a", "runtime-b", "runtime-c"],
            ),
            (
                ReadCollection.INGRESS_AUTHORITIES,
                ingresses.active_page,
                lambda value: value.authority_ref.reference_id,
                ["ingress-a", "ingress-b", "ingress-c"],
            ),
            (
                ReadCollection.SECRET_PROVIDERS,
                providers.active_page,
                lambda value: value.provider_id.value,
                ["provider-a", "provider-b", "provider-c"],
            ),
            (
                ReadCollection.SECRET_REFERENCES,
                references.active_page,
                lambda value: value.registration_id,
                sorted(
                    value[0]
                    for value in self.connection.execute(
                        "SELECT registration_id FROM cpk_secret_references"
                    ).fetchall()
                ),
            ),
            (
                ReadCollection.DELEGATION_SIGNING_KEYS,
                keys.workspace_page,
                lambda value: (value.purpose.value, value.issuer, value.key_id),
                [
                    ("gateway-probe", "issuer-a", "key-a"),
                    ("gateway-probe", "issuer-a", "key-b"),
                    ("gateway-probe", "issuer-a", "key-c"),
                ],
            ),
        )
        for collection, fetch_page, identity, expected in cases:
            with self.subTest(collection=collection.value):
                actual = self._traverse(collection, fetch_page, identity)
                self.assertEqual(actual, expected)
                self.assertEqual(len(actual), len(set(actual)))

    def test_live_membership_behind_cursor_waits_for_fresh_traversal(self) -> None:
        providers = SecretProviderStore(self.connection)
        for identity in ("b", "d", "f"):
            providers.register(self._provider(identity).candidate())

        scope = WorkspaceReadScope("workspace-a")
        first = providers.active_page(
            ReadPageRequest(ReadCollection.SECRET_PROVIDERS, scope, 2)
        )
        self.assertEqual(
            [value.provider_id.value for value in first.items],
            ["provider-b", "provider-d"],
        )

        providers.register(self._provider("a").candidate())
        providers.register(self._provider("e").candidate())
        second = providers.active_page(
            ReadPageRequest(
                ReadCollection.SECRET_PROVIDERS,
                scope,
                2,
                first.next_cursor,
            )
        )
        self.assertEqual(
            [value.provider_id.value for value in second.items],
            ["provider-e", "provider-f"],
        )
        self.assertEqual(
            self._traverse(
                ReadCollection.SECRET_PROVIDERS,
                providers.active_page,
                lambda value: value.provider_id.value,
            ),
            [
                "provider-a",
                "provider-b",
                "provider-d",
                "provider-e",
                "provider-f",
            ],
        )

    @staticmethod
    def _traverse(collection, fetch_page, identity):
        scope = WorkspaceReadScope("workspace-a")
        cursor = None
        values = []
        while True:
            page = fetch_page(ReadPageRequest(collection, scope, 2, cursor))
            values.extend(identity(value) for value in page.items)
            if page.next_cursor is None:
                return values
            cursor = page.next_cursor

    @staticmethod
    def _provider(identity: str) -> RegisterSecretProviderCommand:
        return RegisterSecretProviderCommand(
            workspace_id="workspace-a",
            provider_id=SecretProviderId(f"provider-{identity}"),
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
            display_name=f"Provider {identity}",
            endpoint_reference=SecretProviderEndpointReference(
                f"endpoint-{identity}"
            ),
            credential_reference=SecretReference(
                f"secret://bootstrap/provider-{identity}/token"
            ),
            allowed_reference_prefixes=(
                SecretReference(f"secret://provider-{identity}/workspace-a"),
            ),
            allowed_intents=(SecretUseIntent.POSTGRES_PASSWORD,),
            admitted_by="operator-a",
            admitted_at="2026-08-12T12:04:00Z",
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
        )

    @staticmethod
    def _reference(
        identity: str,
        provider_registration_id: str,
    ) -> RegisterSecretReferenceCommand:
        return RegisterSecretReferenceCommand(
            workspace_id="workspace-a",
            reference=SecretReference(
                f"secret://provider-{identity}/workspace-a/password"
            ),
            provider_registration_id=provider_registration_id,
            allowed_intents=(SecretUseIntent.POSTGRES_PASSWORD,),
            admitted_by="operator-a",
            admitted_at="2026-08-12T12:05:00Z",
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
        )

    @staticmethod
    def _delegation_key(identity: str) -> RegisterDelegationSigningKeyCommand:
        return RegisterDelegationSigningKeyCommand(
            workspace_id="workspace-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="issuer-a",
            public_key=DelegationPublicKey(
                key_id=f"key-{identity}",
                algorithm=DelegationKeyAlgorithm.ED25519,
                public_key_pem=_PUBLIC_KEY,
            ),
            private_key_reference=SecretReference(
                f"secret://private-canary/key-{identity}"
            ),
            admitted_by="operator-a",
            admitted_at="2026-08-12T12:06:00Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,),
        )

    @staticmethod
    def _ingress_authority(identity: str) -> CloudflareZoneIngressAuthority:
        return CloudflareZoneIngressAuthority(
            account_id=f"account-{identity}",
            zone_id=f"zone-{identity}",
            zone_name="openj92.dev",
            api_token_ref=SecretReference(
                f"secret://cloudflare/{identity}/api-token"
            ),
            allowed_hostname_pattern="cpk-gateway-*.openj92.dev",
            generated_secret_provider_registration_id=f"provider-{identity}",
            generated_secret_reference_prefix=SecretReference(
                f"secret://generated/{identity}/ingress"
            ),
        )


def _principal(*, authorized: bool) -> AuthenticatedPrincipal:
    grants = (
        (WorkspaceGrant("workspace-a", tuple(PolicyScope)),)
        if authorized
        else ()
    )
    return AuthenticatedPrincipal(
        PrincipalIdentity(
            issuer="urn:test:current-metadata-pages",
            subject_id="operator-a",
            kind=PrincipalKind.OPERATOR,
        ),
        grants,
    )


@dataclass(frozen=True)
class _RouteRequest:
    surface: str = "http"
    route_id: str = "read.observed-state"
    service_role: ControlPlaneServiceRole = ControlPlaneServiceRole.READS
    path_parameters: dict[str, str] = field(
        default_factory=lambda: {"workspace_id": "workspace-a"}
    )
    payload: dict[str, object] = field(
        default_factory=lambda: {"after": {"hostile": "cursor"}}
    )
    principal: AuthenticatedPrincipal = field(default_factory=lambda: _principal(authorized=True))


class ReadAdapterOrderingTests(unittest.TestCase):
    def test_authorized_malformed_cursor_fails_before_uow_factory(self) -> None:
        calls: list[str] = []

        def factory() -> object:
            calls.append("uow")
            raise AssertionError("malformed cursor must not acquire a UoW")

        with self.assertRaises(CpkServerApplicationError) as raised:
            CpkServerReadService(factory).handle(_RouteRequest())

        self.assertEqual(raised.exception.status, 400)
        self.assertEqual(calls, [])

    def test_unauthorized_request_fails_before_hostile_cursor_decode_and_uow(self) -> None:
        calls: list[str] = []

        def factory() -> object:
            calls.append("uow")
            raise AssertionError("unauthorized request must not acquire a UoW")

        request = _RouteRequest(principal=_principal(authorized=False))
        with self.assertRaises(CpkServerApplicationError) as raised:
            CpkServerReadService(factory).handle(request)

        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
