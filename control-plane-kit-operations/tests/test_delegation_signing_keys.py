from __future__ import annotations

from dataclasses import replace
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
    RegisteredDelegationSigningKeyStatus,
    RetireDelegationSigningKeyCommand,
    RevokeDelegationSigningKeyCommand,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
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
