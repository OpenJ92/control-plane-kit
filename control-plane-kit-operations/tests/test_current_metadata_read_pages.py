from __future__ import annotations

from dataclasses import dataclass, field
from inspect import getsource, signature
import unittest

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
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
