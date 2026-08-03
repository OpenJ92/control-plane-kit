from __future__ import annotations

import concurrent.futures
from dataclasses import replace
import os
import time
import unittest

import psycopg

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    AuthorizedSecretUse,
    RegisterSecretProviderCommand,
    RegisterSecretReferenceCommand,
    RegisteredSecretProvider,
    RegisteredSecretProviderStatus,
    RegisteredSecretReference,
    RegisteredSecretReferenceStatus,
    RevokeSecretProviderCommand,
    RevokeSecretReferenceCommand,
    SecretProviderAuthorizationDenied,
    SecretProviderKind,
    SecretProviderRegistrationConflict,
    SecretProviderRegistrationError,
    SecretProviderRegistrationService,
    SecretUseAuthorizationConflict,
    SecretUseAuthorizationService,
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

    def authorization_service(self) -> SecretUseAuthorizationService:
        return SecretUseAuthorizationService(self.unit_of_work)

    def concurrent_service(
        self,
        application_name: str,
    ) -> SecretProviderRegistrationService:
        return SecretProviderRegistrationService(
            lambda: PostgresUnitOfWork(
                lambda: psycopg.connect(
                    self.database_url,
                    application_name=application_name,
                )
            )
        )

    def wait_for_database_lock(self, application_name: str) -> None:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            row = self.connection.execute(
                """
                SELECT wait_event_type
                FROM pg_stat_activity
                WHERE application_name = %s
                  AND pid <> pg_backend_pid()
                """,
                (application_name,),
            ).fetchone()
            if row is not None and row[0] == "Lock":
                return
            time.sleep(0.01)
        self.fail(f"{application_name} did not wait on the admission lock")

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

    def test_concurrent_identical_provider_registration_converges(self) -> None:
        command = self.provider_command()
        application_name = "cpk-secret-provider-registration-race"

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            with self.unit_of_work() as first_unit_of_work:
                first = first_unit_of_work.stores.secret_providers.register(
                    command.candidate()
                )
                competing = executor.submit(
                    self.concurrent_service(application_name).register_provider,
                    command,
                )
                self.wait_for_database_lock(application_name)
                first_unit_of_work.commit()
            second = competing.result(timeout=5)

        self.assertEqual(second, first)
        row = self.connection.execute(
            """
            SELECT count(*)
            FROM cpk_secret_providers
            WHERE workspace_id = %s
              AND provider_id = %s
            """,
            ("workspace-a", "workspace-secrets"),
        ).fetchone()
        self.assertEqual(row, (1,))

    def test_provider_revocation_removes_active_selection_and_preserves_detail(
        self,
    ) -> None:
        service = self.service()
        registered = service.register_provider(self.provider_command())

        revoked = service.revoke_provider(
            RevokeSecretProviderCommand(
                workspace_id="workspace-a",
                provider_id=SecretProviderId("workspace-secrets"),
                revoked_by="operator-a",
                revoked_at="2026-07-30T12:10:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,),
            )
        )

        self.assertEqual(revoked.registration_id, registered.registration_id)
        self.assertEqual(revoked.status, RegisteredSecretProviderStatus.REVOKED)
        self.assertEqual(revoked.revoked_by, "operator-a")
        self.assertEqual(revoked.revoked_at, "2026-07-30T12:10:00Z")
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
                revoked_by="operator-a",
                revoked_at="2026-07-30T12:10:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,),
            )
        )

        self.assertEqual(revoked.status, RegisteredSecretReferenceStatus.REVOKED)
        self.assertEqual(revoked.revoked_by, "operator-a")
        self.assertEqual(revoked.revoked_at, "2026-07-30T12:10:00Z")
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

    def test_concurrent_identical_reference_registration_converges(self) -> None:
        provider = self.service().register_provider(self.provider_command())
        command = self.reference_command(provider.registration_id)
        application_name = "cpk-secret-reference-registration-race"

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            with self.unit_of_work() as first_unit_of_work:
                first = first_unit_of_work.stores.secret_references.register(
                    command.candidate()
                )
                competing = executor.submit(
                    self.concurrent_service(application_name).register_reference,
                    command,
                )
                self.wait_for_database_lock(application_name)
                first_unit_of_work.commit()
            second = competing.result(timeout=5)

        self.assertEqual(second, first)
        row = self.connection.execute(
            """
            SELECT count(*)
            FROM cpk_secret_references
            WHERE workspace_id = %s
              AND secret_reference = %s
            """,
            ("workspace-a", command.reference.reference_id),
        ).fetchone()
        self.assertEqual(row, (1,))

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

    def test_authorized_use_is_workspace_scoped_idempotent_and_secret_free(
        self,
    ) -> None:
        provider, reference = self.admit_reference()
        command = self.authorize_command(reference.reference)

        authorized = self.authorization_service().authorize(command)

        self.assertIsInstance(authorized, AuthorizedSecretUse)
        self.assertEqual(
            self.authorization_service().authorize(command),
            authorized,
        )
        self.assertEqual(
            authorized.provider_registration_id,
            provider.registration_id,
        )
        self.assertEqual(
            authorized.reference_registration_id,
            reference.registration_id,
        )
        self.assertEqual(authorized.intent, SecretUseIntent.POSTGRES_PASSWORD)
        self.assertEqual(authorized.correlation_id, "correlation-a")
        self.assertEqual(authorized.run_id, "run-a")
        self.assertEqual(authorized.effect_id, "effect-a")
        rendered = repr(authorized.descriptor()).lower()
        for forbidden in (
            "resolved-value",
            "raw-provider-credential",
            "plaintext",
            "ciphertext",
            "bearer ",
        ):
            self.assertNotIn(forbidden, rendered)
        row = self.connection.execute(
            """
            SELECT secret_reference, use_intent, actor_subject,
                   correlation_id, intent_fingerprint
            FROM cpk_secret_use_authorizations
            WHERE authorization_id = %s
            """,
            (authorized.authorization_id,),
        ).fetchone()
        self.assertEqual(row[:4], (
            reference.reference.reference_id,
            SecretUseIntent.POSTGRES_PASSWORD.value,
            "operator-a",
            "correlation-a",
        ))
        self.assertNotIn("resolved-value", repr(row).lower())

        replay = self.authorization_service().authorize(
            replace(command, requested_at="2026-07-30T12:30:00Z")
        )
        self.assertEqual(replay, authorized)

        grant = self.authorization_service().authorize_resolution(
            replace(command, requested_at="2026-07-30T12:31:00Z")
        )
        self.assertIsInstance(grant, SecretResolutionGrant)
        self.assertEqual(grant.authorization_id, authorized.authorization_id)
        self.assertEqual(
            grant.endpoint_reference,
            provider.endpoint_reference,
        )
        self.assertEqual(
            grant.credential_reference,
            provider.credential_reference,
        )
        self.assertTrue(
            grant.permits(
                reference.reference,
                SecretUseIntent.POSTGRES_PASSWORD,
            )
        )
        self.assertNotIn("resolved-value", repr(grant.descriptor()).lower())

    def test_use_permission_is_independent_and_conflicting_replay_fails(self) -> None:
        _, reference = self.admit_reference()
        service = self.authorization_service()

        for scope in (
            PolicyScope.SECRET_PROVIDER_REGISTER,
            PolicyScope.SECRET_PROVIDER_READ,
            PolicyScope.SECRET_PROVIDER_REVOKE,
            PolicyScope.EXECUTION_OPERATE,
        ):
            with self.assertRaises(SecretProviderAuthorizationDenied):
                service.authorize(
                    self.authorize_command(
                        reference.reference,
                        actor_scopes=(scope,),
                    )
                )

        first = service.authorize(self.authorize_command(reference.reference))
        with self.assertRaises(SecretUseAuthorizationConflict):
            service.authorize(
                self.authorize_command(
                    reference.reference,
                    run_id="run-b",
                )
            )
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.secret_use_authorizations.get(
                    "workspace-a",
                    first.authorization_id,
                ),
                first,
            )

    def test_stale_cross_workspace_and_wrong_intent_fail_before_provider_io(
        self,
    ) -> None:
        provider, reference = self.admit_reference()
        service = self.authorization_service()

        with self.assertRaises(SecretProviderRegistrationError):
            service.authorize(
                self.authorize_command(
                    reference.reference,
                    workspace_id="workspace-b",
                )
            )
        with self.assertRaises(SecretProviderRegistrationError):
            service.authorize(
                self.authorize_command(
                    reference.reference,
                    intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
                )
            )

        self.service().revoke_reference(
            RevokeSecretReferenceCommand(
                workspace_id="workspace-a",
                registration_id=reference.registration_id,
                revoked_by="operator-a",
                revoked_at="2026-07-30T12:10:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,),
            )
        )
        with self.assertRaises(SecretProviderRegistrationError):
            service.authorize(
                self.authorize_command(
                    reference.reference,
                    correlation_id="correlation-after-reference-revoke",
                )
            )

        replacement_reference = self.service().register_reference(
            self.reference_command(
                provider.registration_id,
                admitted_at="2026-07-30T12:11:00Z",
                supersedes_registration_id=reference.registration_id,
            )
        )
        self.service().revoke_provider(
            RevokeSecretProviderCommand(
                workspace_id="workspace-a",
                provider_id=provider.provider_id,
                revoked_by="operator-a",
                revoked_at="2026-07-30T12:12:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,),
            )
        )
        with self.assertRaises(SecretProviderRegistrationError):
            service.authorize(
                self.authorize_command(
                    replacement_reference.reference,
                    correlation_id="correlation-after-provider-revoke",
                )
            )

    def test_authorized_history_survives_later_revocation(self) -> None:
        provider, reference = self.admit_reference()
        authorized = self.authorization_service().authorize(
            self.authorize_command(reference.reference)
        )

        self.service().revoke_provider(
            RevokeSecretProviderCommand(
                workspace_id="workspace-a",
                provider_id=provider.provider_id,
                revoked_by="operator-a",
                revoked_at="2026-07-30T12:10:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,),
            )
        )

        with self.unit_of_work() as unit_of_work:
            stored = unit_of_work.stores.secret_use_authorizations.get(
                "workspace-a",
                authorized.authorization_id,
            )
        self.assertEqual(stored, authorized)

    def test_provider_supersession_invalidates_handle_pinned_to_old_registration(
        self,
    ) -> None:
        provider, reference = self.admit_reference()
        self.service().register_provider(
            self.provider_command(
                display_name="Replacement secrets",
                admitted_at="2026-07-30T12:10:00Z",
                supersedes_registration_id=provider.registration_id,
            )
        )

        with self.assertRaises(SecretProviderRegistrationError):
            self.authorization_service().authorize(
                self.authorize_command(reference.reference)
            )

    def test_authorization_correlation_fields_are_bounded(self) -> None:
        _, reference = self.admit_reference()
        with self.assertRaises(SecretProviderRegistrationError):
            self.authorize_command(
                reference.reference,
                activity_id="x" * 201,
            )
        with self.assertRaises(SecretProviderRegistrationError):
            self.authorize_command(
                reference.reference,
                correlation_id="not/a/correlation",
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

    def admit_reference(
        self,
    ) -> tuple[RegisteredSecretProvider, RegisteredSecretReference]:
        provider = self.service().register_provider(self.provider_command())
        reference = self.service().register_reference(
            self.reference_command(provider.registration_id)
        )
        return provider, reference

    def authorize_command(
        self,
        reference: SecretReference,
        *,
        workspace_id: str = "workspace-a",
        intent: SecretUseIntent = SecretUseIntent.POSTGRES_PASSWORD,
        correlation_id: str = "correlation-a",
        operation_id: str | None = "operation-a",
        session_id: str | None = "session-a",
        run_id: str | None = "run-a",
        activity_id: str | None = "activity-a",
        effect_id: str | None = "effect-a",
        probe_id: str | None = "probe-a",
        actor_scopes: tuple[PolicyScope, ...] = (
            PolicyScope.SECRET_PROVIDER_USE,
        ),
    ) -> AuthorizeSecretUse:
        return AuthorizeSecretUse(
            workspace_id=workspace_id,
            reference=reference,
            intent=intent,
            actor_subject="operator-a",
            correlation_id=correlation_id,
            requested_at="2026-07-30T12:02:00Z",
            actor_scopes=actor_scopes,
            operation_id=operation_id,
            session_id=session_id,
            run_id=run_id,
            activity_id=activity_id,
            effect_id=effect_id,
            probe_id=probe_id,
        )


if __name__ == "__main__":
    unittest.main()
