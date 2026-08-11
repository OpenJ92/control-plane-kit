from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import os
import unittest

import psycopg

from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference, SecretUseIntent
from control_plane_kit_operations.delegation_signing_keys import (
    ActivateDelegationSigningKeyCommand,
    DelegationSigningKeyAuthorizationDenied,
    DelegationSigningKeyConflict,
    DelegationSigningKeyRegistrationService,
    RegisterDelegationSigningKeyCommand,
    RegisteredDelegationSigningKey,
    RegisteredDelegationSigningKeyStatus,
    RetireDelegationSigningKeyCommand,
    RevokeDelegationSigningKeyCommand,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.postgres.delegation_signing_key_store import (
    DelegationSigningKeyStore,
)
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand,
    RegisterSecretReferenceCommand,
    SecretProviderKind,
    SecretProviderRegistrationService,
)
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
)


PUBLIC_KEY_A = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
-----END PUBLIC KEY-----
"""
PUBLIC_KEY_B = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=
-----END PUBLIC KEY-----
"""


class DelegationSigningKeyStoreTests(unittest.TestCase):
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
            VALUES ('workspace-a', 'Workspace A', 'created'),
                   ('workspace-b', 'Workspace B', 'created')
            """
        )
        self._admit_reference("workspace-a", "secret://workspace-secrets/keys/a")
        self._admit_reference("workspace-a", "secret://workspace-secrets/keys/b")

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def service(self) -> DelegationSigningKeyRegistrationService:
        return DelegationSigningKeyRegistrationService(self.unit_of_work)

    def test_registration_is_immutable_workspace_scoped_and_restart_safe(self) -> None:
        service = self.service()
        registered = service.register(self.command())

        self.assertEqual(service.register(self.command()), registered)
        self.assertEqual(
            registered.status,
            RegisteredDelegationSigningKeyStatus.VERIFY_ONLY,
        )
        self.assertNotIn("BEGIN PUBLIC KEY", repr(registered.descriptor()))
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.delegation_signing_keys.get(
                    "workspace-a",
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    "cpk-server",
                    "gateway-a",
                ),
                registered,
            )
            self.assertEqual(
                unit_of_work.stores.delegation_signing_keys.list_for_verification(
                    "workspace-b",
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    "cpk-server",
                ),
                (),
            )

    def test_surface_read_purpose_uses_the_existing_durable_key_lifecycle(self) -> None:
        purpose = getattr(
            DelegationKeyPurpose,
            "WORKLOAD_NODE_CONTROL_SURFACE_READ",
            None,
        )
        self.assertIsNotNone(purpose)
        service = self.service()
        registered = service.register(replace(self.command(), purpose=purpose))

        self.assertIs(registered.purpose, purpose)
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.delegation_signing_keys.get(
                    "workspace-a",
                    purpose,
                    "cpk-server",
                    "gateway-a",
                ),
                registered,
            )
            self.assertEqual(
                unit_of_work.stores.delegation_signing_keys.list_for_verification(
                    "workspace-a",
                    purpose,
                    "cpk-server",
                ),
                (registered,),
            )

    def test_changed_identity_under_same_key_id_fails_closed(self) -> None:
        service = self.service()
        service.register(self.command())

        with self.assertRaises(DelegationSigningKeyConflict):
            service.register(
                self.command(
                    public_key=DelegationPublicKey(
                        key_id="gateway-a",
                        algorithm=DelegationKeyAlgorithm.ED25519,
                        public_key_pem=PUBLIC_KEY_B,
                    )
                )
            )
        with self.assertRaises(DelegationSigningKeyConflict):
            service.register(
                self.command(
                    private_key_reference=SecretReference(
                        "secret://workspace-secrets/keys/b"
                    )
                )
            )

    def test_activation_requires_admitted_reference_and_rotates_to_overlap(self) -> None:
        service = self.service()
        key_a = service.register(self.command())
        key_b = service.register(
            self.command(
                key_id="gateway-b",
                public_key=DelegationPublicKey(
                    key_id="gateway-b",
                    algorithm=DelegationKeyAlgorithm.ED25519,
                    public_key_pem=PUBLIC_KEY_B,
                ),
                private_key_reference=SecretReference(
                    "secret://workspace-secrets/keys/b"
                ),
            )
        )

        active_a = service.activate(self.activate("gateway-a"))
        self.assertEqual(active_a.status, RegisteredDelegationSigningKeyStatus.ACTIVE)
        active_b = service.activate(
            self.activate("gateway-b", activated_at="2026-08-01T12:10:00Z")
        )

        self.assertEqual(active_b.status, RegisteredDelegationSigningKeyStatus.ACTIVE)
        with self.unit_of_work() as unit_of_work:
            overlap = unit_of_work.stores.delegation_signing_keys.list_for_verification(
                "workspace-a",
                DelegationKeyPurpose.GATEWAY_PROBE,
                "cpk-server",
            )
        self.assertEqual([item.key_id for item in overlap], ["gateway-a", "gateway-b"])
        self.assertEqual(
            {item.key_id: item.status for item in overlap},
            {
                key_a.key_id: RegisteredDelegationSigningKeyStatus.VERIFY_ONLY,
                key_b.key_id: RegisteredDelegationSigningKeyStatus.ACTIVE,
            },
        )

        missing = self.command(
            key_id="gateway-missing",
            public_key=DelegationPublicKey(
                key_id="gateway-missing",
                algorithm=DelegationKeyAlgorithm.ED25519,
                public_key_pem=PUBLIC_KEY_B,
            ),
            private_key_reference=SecretReference(
                "secret://workspace-secrets/missing"
            ),
        )
        with self.assertRaises(DelegationSigningKeyConflict):
            service.register(missing)

    def test_retirement_and_revocation_remove_verification_authority(self) -> None:
        service = self.service()
        service.register(self.command())
        service.activate(self.activate("gateway-a"))
        service.register(
            self.command(
                key_id="gateway-b",
                public_key=DelegationPublicKey(
                    key_id="gateway-b",
                    algorithm=DelegationKeyAlgorithm.ED25519,
                    public_key_pem=PUBLIC_KEY_B,
                ),
                private_key_reference=SecretReference(
                    "secret://workspace-secrets/keys/b"
                ),
            )
        )
        service.activate(self.activate("gateway-b"))

        retired = service.retire(
            RetireDelegationSigningKeyCommand(
                workspace_id="workspace-a",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                key_id="gateway-a",
                retired_by="operator-a",
                retired_at="2026-08-01T12:20:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_RETIRE,),
            )
        )
        self.assertEqual(retired.status, RegisteredDelegationSigningKeyStatus.RETIRED)
        revoked = service.revoke(
            RevokeDelegationSigningKeyCommand(
                workspace_id="workspace-a",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                key_id="gateway-a",
                revoked_by="operator-a",
                revoked_at="2026-08-01T12:21:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_REVOKE,),
            )
        )
        self.assertEqual(revoked.status, RegisteredDelegationSigningKeyStatus.REVOKED)
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                [item.key_id for item in unit_of_work.stores.delegation_signing_keys.list_for_verification(
                    "workspace-a", DelegationKeyPurpose.GATEWAY_PROBE, "cpk-server"
                )],
                ["gateway-b"],
            )

    def test_permissions_are_distinct(self) -> None:
        service = self.service()
        with self.assertRaises(DelegationSigningKeyAuthorizationDenied):
            service.register(
                replace(
                    self.command(),
                    actor_scopes=(PolicyScope.DELEGATION_KEY_READ,),
                )
            )

    def test_all_store_mutations_reject_malformed_time_before_connection_access(self) -> None:
        invalid = "2026-02-30T12:00:00Z"

        class FailOnAccessConnection:
            def __init__(self) -> None:
                self.accessed = False

            def execute(self, *_args: object, **_kwargs: object) -> object:
                self.accessed = True
                raise AssertionError("connection access must not occur")

        calls = (
            (
                "register",
                lambda store: store.register(
                    replace(self.command().candidate(), admitted_at=invalid)
                ),
            ),
            (
                "activate",
                lambda store: store.activate(
                    "workspace-a",
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    "cpk-server",
                    "gateway-a",
                    activated_by="operator-a",
                    activated_at=invalid,
                ),
            ),
            (
                "retire",
                lambda store: store.retire(
                    "workspace-a",
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    "cpk-server",
                    "gateway-a",
                    retired_by="operator-a",
                    retired_at=invalid,
                ),
            ),
            (
                "revoke",
                lambda store: store.revoke(
                    "workspace-a",
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    "cpk-server",
                    "gateway-a",
                    revoked_by="operator-a",
                    revoked_at=invalid,
                ),
            ),
        )
        for identity, call in calls:
            with self.subTest(identity=identity):
                connection = FailOnAccessConnection()
                store = DelegationSigningKeyStore(connection)
                with self.assertRaisesRegex(
                    ValueError,
                    "postgres timestamp must be canonical UTC text",
                ) as raised:
                    call(store)
                self.assertIsNone(raised.exception.__context__)
                self.assertIsNone(raised.exception.__cause__)
                self.assertFalse(connection.accessed)

    def test_malformed_duplicate_and_lifecycle_replays_validate_time(self) -> None:
        service = self.service()
        invalid = "2026-02-30T12:00:00Z"
        service.register(self.command())
        with self.assertRaises(ValueError):
            service.register(replace(self.command(), admitted_at=invalid))

        service.activate(self.activate("gateway-a"))
        with self.assertRaises(ValueError):
            service.activate(replace(self.activate("gateway-a"), activated_at=invalid))

        service.register(self.command(key_id="gateway-b"))
        retire = RetireDelegationSigningKeyCommand(
            workspace_id="workspace-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            key_id="gateway-b",
            retired_by="operator-a",
            retired_at="2026-08-01T12:20:00Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_RETIRE,),
        )
        service.retire(retire)
        with self.assertRaises(ValueError):
            service.retire(replace(retire, retired_at=invalid))

        service.register(self.command(key_id="gateway-c"))
        revoke = RevokeDelegationSigningKeyCommand(
            workspace_id="workspace-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            key_id="gateway-c",
            revoked_by="operator-a",
            revoked_at="2026-08-01T12:21:00Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_REVOKE,),
        )
        service.revoke(revoke)
        with self.assertRaises(ValueError):
            service.revoke(replace(revoke, revoked_at=invalid))

    def test_invalid_activation_cannot_demote_the_current_active_signer(self) -> None:
        service = self.service()
        service.register(self.command())
        service.register(self.command(key_id="gateway-b"))
        service.activate(self.activate("gateway-a"))

        with self.assertRaises(ValueError):
            service.activate(
                self.activate("gateway-b", activated_at="2026-02-30T12:00:00Z")
            )

        with self.unit_of_work() as unit_of_work:
            values = unit_of_work.stores.delegation_signing_keys.list_workspace(
                "workspace-a"
            )
        self.assertEqual(
            {value.key_id: value.status for value in values},
            {
                "gateway-a": RegisteredDelegationSigningKeyStatus.ACTIVE,
                "gateway-b": RegisteredDelegationSigningKeyStatus.VERIFY_ONLY,
            },
        )

    def test_invalid_retirement_and_revocation_preserve_complete_evidence(self) -> None:
        service = self.service()
        registered = service.register(self.command())

        with self.assertRaises(ValueError):
            service.retire(
                RetireDelegationSigningKeyCommand(
                    workspace_id="workspace-a",
                    purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                    issuer="cpk-server",
                    key_id="gateway-a",
                    retired_by="operator-a",
                    retired_at="2026-02-30T12:00:00Z",
                    actor_scopes=(PolicyScope.DELEGATION_KEY_RETIRE,),
                )
            )
        with self.unit_of_work() as unit_of_work:
            after_retirement = unit_of_work.stores.delegation_signing_keys.get(
                "workspace-a",
                DelegationKeyPurpose.GATEWAY_PROBE,
                "cpk-server",
                "gateway-a",
            )
        self.assertEqual(after_retirement, registered)

        active = service.activate(self.activate("gateway-a"))
        with self.assertRaises(ValueError):
            service.revoke(
                RevokeDelegationSigningKeyCommand(
                    workspace_id="workspace-a",
                    purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                    issuer="cpk-server",
                    key_id="gateway-a",
                    revoked_by="operator-a",
                    revoked_at="2026-02-30T12:00:00Z",
                    actor_scopes=(PolicyScope.DELEGATION_KEY_REVOKE,),
                )
            )
        with self.unit_of_work() as unit_of_work:
            after_revocation = unit_of_work.stores.delegation_signing_keys.get(
                "workspace-a",
                DelegationKeyPurpose.GATEWAY_PROBE,
                "cpk-server",
                "gateway-a",
            )
        self.assertEqual(after_revocation, active)

    def test_every_row_selector_decodes_seconds_microseconds_and_nulls_in_utc(
        self,
    ) -> None:
        service = self.service()
        service.register(self.command())
        service.register(self.command(key_id="gateway-b"))
        service.register(self.command(key_id="gateway-c"))
        service.register(self.command(key_id="gateway-d"))
        service.activate(
            self.activate("gateway-a", activated_at="2026-08-01T12:05:00.000002Z")
        )
        self.connection.execute(
            """
            UPDATE cpk_delegation_signing_keys
            SET admitted_at = CASE key_id
                  WHEN 'gateway-a' THEN %s
                  ELSE %s
                END,
                activated_at = CASE key_id
                  WHEN 'gateway-a' THEN %s
                  ELSE NULL
                END
            """,
            (
                datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 8, 1, 12, 0, 0, 1, tzinfo=timezone.utc),
                datetime(2026, 8, 1, 12, 5, 0, 2, tzinfo=timezone.utc),
            ),
        )
        self.connection.execute(
            """
            UPDATE cpk_delegation_signing_keys
            SET status = 'retired', retired_by = 'operator-a', retired_at = %s
            WHERE key_id = 'gateway-c'
            """,
            (datetime(2026, 8, 1, 12, 10, 0, 3, tzinfo=timezone.utc),),
        )
        self.connection.execute(
            """
            UPDATE cpk_delegation_signing_keys
            SET status = 'revoked', revoked_by = 'operator-a', revoked_at = %s
            WHERE key_id = 'gateway-d'
            """,
            (datetime(2026, 8, 1, 12, 15, 0, 4, tzinfo=timezone.utc),),
        )
        self.connection.execute("SET TIME ZONE 'Asia/Tokyo'")
        store = DelegationSigningKeyStore(self.connection)

        key_b = store.get(
            "workspace-a",
            DelegationKeyPurpose.GATEWAY_PROBE,
            "cpk-server",
            "gateway-b",
        )
        retired = store.get(
            "workspace-a",
            DelegationKeyPurpose.GATEWAY_PROBE,
            "cpk-server",
            "gateway-c",
        )
        revoked = store.get(
            "workspace-a",
            DelegationKeyPurpose.GATEWAY_PROBE,
            "cpk-server",
            "gateway-d",
        )
        active = store.require_active(
            "workspace-a",
            DelegationKeyPurpose.GATEWAY_PROBE,
            "cpk-server",
        )
        unambiguous = store.require_unambiguous_active(
            "workspace-a",
            DelegationKeyPurpose.GATEWAY_PROBE,
        )
        workspace = store.list_workspace("workspace-a")
        verification = store.list_for_verification(
            "workspace-a",
            DelegationKeyPurpose.GATEWAY_PROBE,
            "cpk-server",
        )

        self.assertEqual(
            self._times(key_b),
            ("2026-08-01T12:00:00.000001Z", None, None, None),
        )
        self.assertEqual(
            self._times(retired),
            (
                "2026-08-01T12:00:00.000001Z",
                None,
                "2026-08-01T12:10:00.000003Z",
                None,
            ),
        )
        self.assertEqual(
            self._times(revoked),
            (
                "2026-08-01T12:00:00.000001Z",
                None,
                None,
                "2026-08-01T12:15:00.000004Z",
            ),
        )
        for selected in (active, unambiguous):
            self.assertEqual(
                self._times(selected),
                (
                    "2026-08-01T12:00:00Z",
                    "2026-08-01T12:05:00.000002Z",
                    None,
                    None,
                ),
            )
        expected = {
            "gateway-a": (
                "2026-08-01T12:00:00Z",
                "2026-08-01T12:05:00.000002Z",
                None,
                None,
            ),
            "gateway-b": ("2026-08-01T12:00:00.000001Z", None, None, None),
            "gateway-c": (
                "2026-08-01T12:00:00.000001Z",
                None,
                "2026-08-01T12:10:00.000003Z",
                None,
            ),
            "gateway-d": (
                "2026-08-01T12:00:00.000001Z",
                None,
                None,
                "2026-08-01T12:15:00.000004Z",
            ),
        }
        self.assertEqual(
            {item.key_id: self._times(item) for item in workspace},
            expected,
        )
        self.assertEqual(
            {item.key_id: self._times(item) for item in verification},
            {key_id: expected[key_id] for key_id in ("gateway-a", "gateway-b")},
        )

    def command(
        self,
        *,
        key_id: str = "gateway-a",
        public_key: DelegationPublicKey | None = None,
        private_key_reference: SecretReference | None = None,
    ) -> RegisterDelegationSigningKeyCommand:
        return RegisterDelegationSigningKeyCommand(
            workspace_id="workspace-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            public_key=public_key
            or DelegationPublicKey(
                key_id=key_id,
                algorithm=DelegationKeyAlgorithm.ED25519,
                public_key_pem=PUBLIC_KEY_A,
            ),
            private_key_reference=private_key_reference
            or SecretReference("secret://workspace-secrets/keys/a"),
            admitted_by="operator-a",
            admitted_at="2026-08-01T12:00:00Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,),
        )

    def activate(
        self,
        key_id: str,
        *,
        activated_at: str = "2026-08-01T12:05:00Z",
    ) -> ActivateDelegationSigningKeyCommand:
        return ActivateDelegationSigningKeyCommand(
            workspace_id="workspace-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            key_id=key_id,
            activated_by="operator-a",
            activated_at=activated_at,
            actor_scopes=(PolicyScope.DELEGATION_KEY_ACTIVATE,),
        )

    @staticmethod
    def _times(value: RegisteredDelegationSigningKey) -> tuple[str | None, ...]:
        return (
            value.admitted_at,
            value.activated_at,
            value.retired_at,
            value.revoked_at,
        )

    def _admit_reference(self, workspace_id: str, reference: str) -> None:
        service = SecretProviderRegistrationService(self.unit_of_work)
        provider = service.register_provider(
            RegisterSecretProviderCommand(
                workspace_id=workspace_id,
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
                allowed_intents=(SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY,),
                admitted_by="operator-a",
                admitted_at="2026-08-01T11:00:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
            )
        )
        service.register_reference(
            RegisterSecretReferenceCommand(
                workspace_id=workspace_id,
                reference=SecretReference(reference),
                provider_registration_id=provider.registration_id,
                allowed_intents=(SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY,),
                admitted_by="operator-a",
                admitted_at="2026-08-01T11:05:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
            )
        )


if __name__ == "__main__":
    unittest.main()
