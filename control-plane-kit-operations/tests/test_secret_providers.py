from __future__ import annotations

import os
import unittest

import psycopg

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand,
    RegisterSecretReferenceCommand,
    RegisteredSecretProviderStatus,
    RegisteredSecretReferenceStatus,
    RevokeSecretProviderCommand,
    RevokeSecretReferenceCommand,
    SecretProviderAuthorizationDenied,
    SecretProviderKind,
    SecretProviderRegistrationConflict,
    SecretProviderRegistrationError,
    SecretProviderRegistrationService,
)


class SecretProviderStoreTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def service(self) -> SecretProviderRegistrationService:
        return SecretProviderRegistrationService(self.unit_of_work)

    def test_provider_registration_is_workspace_scoped_idempotent_and_secret_free(
        self,
    ) -> None:
        service = self.service()
        command = self.provider_command()

        registered = service.register_provider(command)

        self.assertEqual(service.register_provider(command), registered)
        self.assertEqual(registered.status, RegisteredSecretProviderStatus.ACTIVE)
        self.assertEqual(
            registered.endpoint_reference,
            SecretProviderEndpointReference("workspace-secrets"),
        )
        rendered = repr(registered.descriptor()).lower()
        for forbidden in (
            "https://secrets.internal",
            "bearer ",
            "plaintext",
            "ciphertext",
        ):
            self.assertNotIn(forbidden, rendered)
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.secret_providers.list_active("workspace-a"),
                (registered,),
            )
            self.assertEqual(
                unit_of_work.stores.secret_providers.list_active("workspace-b"),
                (),
            )
        stored = repr(
            self.connection.execute(
                """
                SELECT provider_id, provider_kind, display_name,
                       endpoint_reference, credential_reference,
                       allowed_reference_prefixes, allowed_intents, metadata
                FROM cpk_secret_providers
                WHERE registration_id = %s
                """,
                (registered.registration_id,),
            ).fetchone()
        ).lower()
        for forbidden in (
            "https://secrets.internal",
            "bearer ",
            "plaintext",
            "ciphertext",
            "raw-provider-credential",
        ):
            self.assertNotIn(forbidden, stored)

    def test_changed_provider_requires_explicit_supersession_and_preserves_history(
        self,
    ) -> None:
        service = self.service()
        original = service.register_provider(self.provider_command())

        with self.assertRaises(SecretProviderRegistrationConflict):
            service.register_provider(
                self.provider_command(display_name="Replacement secrets")
            )

        replacement = service.register_provider(
            self.provider_command(
                display_name="Replacement secrets",
                admitted_at="2026-07-30T12:05:00Z",
                supersedes_registration_id=original.registration_id,
            )
        )

        self.assertEqual(replacement.status, RegisteredSecretProviderStatus.ACTIVE)
        self.assertEqual(
            replacement.supersedes_registration_id,
            original.registration_id,
        )
        with self.unit_of_work() as unit_of_work:
            history = unit_of_work.stores.secret_providers.list_history(
                "workspace-a",
                SecretProviderId("workspace-secrets"),
            )
        self.assertEqual(len(history), 2)
        self.assertEqual(
            {item.status for item in history},
            {
                RegisteredSecretProviderStatus.ACTIVE,
                RegisteredSecretProviderStatus.SUPERSEDED,
            },
        )

    def test_provider_revocation_removes_active_selection_and_preserves_detail(
        self,
    ) -> None:
        service = self.service()
        registered = service.register_provider(self.provider_command())

        revoked = service.revoke_provider(
            RevokeSecretProviderCommand(
                workspace_id="workspace-a",
                provider_id=SecretProviderId("workspace-secrets"),
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,),
            )
        )

        self.assertEqual(revoked.registration_id, registered.registration_id)
        self.assertEqual(revoked.status, RegisteredSecretProviderStatus.REVOKED)
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.secret_providers.list_active("workspace-a"),
                (),
            )
            detail = unit_of_work.stores.secret_providers.get_by_registration(
                "workspace-a",
                registered.registration_id,
            )
        self.assertEqual(detail.status, RegisteredSecretProviderStatus.REVOKED)

    def test_reference_requires_active_same_workspace_provider_prefix_and_intent(
        self,
    ) -> None:
        service = self.service()
        provider = service.register_provider(self.provider_command())

        registered = service.register_reference(
            self.reference_command(provider.registration_id)
        )

        self.assertEqual(registered.status, RegisteredSecretReferenceStatus.ACTIVE)
        self.assertEqual(registered.provider_registration_id, provider.registration_id)
        with self.assertRaises(SecretProviderRegistrationError):
            service.register_reference(
                self.reference_command(
                    provider.registration_id,
                    reference=SecretReference(
                        "secret://workspace-secrets/workspace-b/postgres/password"
                    ),
                )
            )
        with self.assertRaises(SecretProviderRegistrationError):
            service.register_reference(
                self.reference_command(
                    provider.registration_id,
                    reference=SecretReference(
                        "secret://other-provider/workspace-a/postgres/password"
                    ),
                )
            )
        with self.assertRaises(SecretProviderRegistrationError):
            service.register_reference(
                self.reference_command(
                    provider.registration_id,
                    intents=(SecretUseIntent.CLOUDFLARE_API_TOKEN,),
                )
            )
        with self.assertRaises(SecretProviderRegistrationError):
            service.register_reference(
                self.reference_command(
                    provider.registration_id,
                    workspace_id="workspace-b",
                )
            )

    def test_provider_public_metadata_cannot_smuggle_endpoint_or_secret_material(
        self,
    ) -> None:
        with self.assertRaises(SecretProviderRegistrationError):
            self.provider_command(display_name="https://secrets.internal")
        with self.assertRaises(SecretProviderRegistrationError):
            RegisterSecretProviderCommand(
                **{
                    **self.provider_command().__dict__,
                    "metadata": {"api_token": "raw-provider-credential"},
                }
            )
        with self.assertRaises(SecretProviderRegistrationError):
            RegisterSecretProviderCommand(
                **{
                    **self.provider_command().__dict__,
                    "metadata": {"note": "Bearer raw-provider-credential"},
                }
            )

    def test_reference_supersession_revocation_and_rollback_preserve_history(
        self,
    ) -> None:
        service = self.service()
        provider = service.register_provider(self.provider_command())
        original = service.register_reference(
            self.reference_command(provider.registration_id)
        )

        with self.assertRaises(SecretProviderRegistrationConflict):
            service.register_reference(
                self.reference_command(
                    provider.registration_id,
                    intents=(
                        SecretUseIntent.POSTGRES_PASSWORD,
                        SecretUseIntent.APPLICATION_CONTROL_TOKEN,
                    ),
                )
            )
        successor = service.register_reference(
            self.reference_command(
                provider.registration_id,
                intents=(
                    SecretUseIntent.POSTGRES_PASSWORD,
                    SecretUseIntent.APPLICATION_CONTROL_TOKEN,
                ),
                admitted_at="2026-07-30T12:06:00Z",
                supersedes_registration_id=original.registration_id,
            )
        )
        revoked = service.revoke_reference(
            RevokeSecretReferenceCommand(
                workspace_id="workspace-a",
                registration_id=successor.registration_id,
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,),
            )
        )

        self.assertEqual(revoked.status, RegisteredSecretReferenceStatus.REVOKED)
        with self.unit_of_work() as unit_of_work:
            history = unit_of_work.stores.secret_references.list_history(
                "workspace-a",
                original.reference,
            )
        self.assertEqual(
            {item.status for item in history},
            {
                RegisteredSecretReferenceStatus.REVOKED,
                RegisteredSecretReferenceStatus.SUPERSEDED,
            },
        )

        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.secret_providers.register(self.provider_candidate())
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.secret_providers.list_active("workspace-b"),
                (),
            )

    def test_service_requires_focused_permissions_and_schema_is_idempotent(self) -> None:
        service = self.service()
        with self.assertRaises(SecretProviderAuthorizationDenied):
            service.register_provider(
                self.provider_command(actor_scopes=(PolicyScope.PLAN_REQUEST,))
            )

        provider = service.register_provider(self.provider_command())
        with self.assertRaises(SecretProviderAuthorizationDenied):
            service.register_reference(
                self.reference_command(
                    provider.registration_id,
                    actor_scopes=(PolicyScope.SECRET_PROVIDER_READ,),
                )
            )

        install_schema(self.connection)
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.secret_providers.list_active("workspace-a"),
                (provider,),
            )

    def provider_command(
        self,
        *,
        workspace_id: str = "workspace-a",
        display_name: str = "Workspace secrets",
        admitted_at: str = "2026-07-30T12:00:00Z",
        supersedes_registration_id: str | None = None,
        actor_scopes: tuple[PolicyScope, ...] = (
            PolicyScope.SECRET_PROVIDER_REGISTER,
        ),
    ) -> RegisterSecretProviderCommand:
        return RegisterSecretProviderCommand(
            workspace_id=workspace_id,
            provider_id=SecretProviderId("workspace-secrets"),
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
            display_name=display_name,
            endpoint_reference=SecretProviderEndpointReference("workspace-secrets"),
            credential_reference=SecretReference(
                "secret://bootstrap/workspace-secrets/client-token"
            ),
            allowed_reference_prefixes=(
                SecretReference(f"secret://workspace-secrets/{workspace_id}"),
            ),
            allowed_intents=(
                SecretUseIntent.APPLICATION_CONTROL_TOKEN,
                SecretUseIntent.POSTGRES_PASSWORD,
            ),
            admitted_by="operator-a",
            admitted_at=admitted_at,
            actor_scopes=actor_scopes,
            supersedes_registration_id=supersedes_registration_id,
            metadata={"environment": "test"},
        )

    def provider_candidate(self):
        return self.provider_command(workspace_id="workspace-b").candidate()

    def reference_command(
        self,
        provider_registration_id: str,
        *,
        workspace_id: str = "workspace-a",
        reference: SecretReference = SecretReference(
            "secret://workspace-secrets/workspace-a/postgres/password"
        ),
        intents: tuple[SecretUseIntent, ...] = (
            SecretUseIntent.POSTGRES_PASSWORD,
        ),
        admitted_at: str = "2026-07-30T12:01:00Z",
        supersedes_registration_id: str | None = None,
        actor_scopes: tuple[PolicyScope, ...] = (
            PolicyScope.SECRET_PROVIDER_REGISTER,
        ),
    ) -> RegisterSecretReferenceCommand:
        return RegisterSecretReferenceCommand(
            workspace_id=workspace_id,
            reference=reference,
            provider_registration_id=provider_registration_id,
            allowed_intents=intents,
            admitted_by="operator-a",
            admitted_at=admitted_at,
            actor_scopes=actor_scopes,
            supersedes_registration_id=supersedes_registration_id,
            metadata={"purpose": "integration-test"},
        )


if __name__ == "__main__":
    unittest.main()
