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
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_operations.delegation_key_generation import (
    AdmitGeneratedDelegationSigningKey,
    DelegationKeyGenerationAuthorizationDenied,
    DelegationKeyGenerationConflict,
    DelegationKeyGenerationEvidence,
    DelegationKeyGenerationService,
    GenerateDelegationSigningKey,
)
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyConflict,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand,
    SecretProviderKind,
    SecretProviderRegistrationService,
)


PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
-----END PUBLIC KEY-----
"""
PUBLIC_KEY_B = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=
-----END PUBLIC KEY-----
"""


class _ProviderMetadata:
    version_id = "version-a"
    version_number = 1


class _ProviderResult:
    reference = SecretReference("secret://workspace-secrets/keys/gateway-b")
    metadata = _ProviderMetadata()
    purpose = DelegationKeyPurpose.GATEWAY_PROBE
    issuer = "cpk-server"
    correlation_id = "rotation-gateway-b"
    public_key = DelegationPublicKey(
        key_id="gateway-b",
        algorithm=DelegationKeyAlgorithm.ED25519,
        public_key_pem=PUBLIC_KEY,
    )
    replayed = False


class DelegationKeyGenerationTests(unittest.TestCase):
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
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        self.provider = SecretProviderRegistrationService(
            self.unit_of_work
        ).register_provider(
            RegisterSecretProviderCommand(
                workspace_id="workspace-a",
                provider_id=SecretProviderId("workspace-secrets"),
                provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
                display_name="Workspace secrets",
                endpoint_reference=SecretProviderEndpointReference(
                    "secrets-endpoint"
                ),
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

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def service(self) -> DelegationKeyGenerationService:
        return DelegationKeyGenerationService(self.unit_of_work)

    def test_prepare_requires_distinct_generation_permission(self) -> None:
        command = self.generate_command()
        for insufficient_scope in (
            PolicyScope.SECRET_PROVIDER_USE,
            PolicyScope.DELEGATION_KEY_REGISTER,
            PolicyScope.DELEGATION_KEY_USE,
        ):
            with self.subTest(scope=insufficient_scope):
                with self.assertRaises(DelegationKeyGenerationAuthorizationDenied):
                    self.service().prepare(
                        replace(command, actor_scopes=(insufficient_scope,))
                    )

        grant = self.service().prepare(command)

        self.assertEqual(grant.workspace_id, "workspace-a")
        self.assertEqual(grant.purpose, DelegationKeyPurpose.GATEWAY_PROBE)
        self.assertEqual(grant.issuer, "cpk-server")
        self.assertEqual(grant.reference, command.reference)
        self.assertEqual(
            grant.provider_registration_id,
            self.provider.registration_id,
        )
        self.assertNotIn("private", repr(grant).lower())
        self.assertNotIn("token-value", repr(grant).lower())

    def test_fold_atomically_admits_reference_and_public_identity(self) -> None:
        grant = self.service().prepare(self.generate_command())
        evidence = DelegationKeyGenerationEvidence.from_provider_result(
            grant,
            _ProviderResult(),
        )

        admitted = self.service().admit_generated(
            AdmitGeneratedDelegationSigningKey(
                grant=grant,
                evidence=evidence,
                admitted_by="operator-a",
                admitted_at="2026-08-01T12:01:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,),
            )
        )

        self.assertEqual(admitted.secret_reference.reference, evidence.reference)
        self.assertEqual(admitted.signing_key.public_key, evidence.public_key)
        self.assertEqual(admitted.provider_version_id, "version-a")
        self.assertEqual(admitted.provider_version_number, 1)
        self.assertFalse(admitted.replayed)
        with self.unit_of_work() as unit_of_work:
            stored_reference = unit_of_work.stores.secret_references.get_active(
                "workspace-a",
                evidence.reference,
            )
            stored_key = unit_of_work.stores.delegation_signing_keys.get(
                "workspace-a",
                DelegationKeyPurpose.GATEWAY_PROBE,
                "cpk-server",
                evidence.public_key.key_id,
            )
        self.assertEqual(stored_reference, admitted.secret_reference)
        self.assertEqual(stored_key, admitted.signing_key)

        replay = self.service().admit_generated(
            AdmitGeneratedDelegationSigningKey(
                grant=grant,
                evidence=replace(evidence, replayed=True),
                admitted_by="operator-a",
                admitted_at="2026-08-01T12:01:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,),
            )
        )
        self.assertEqual(replay.secret_reference, admitted.secret_reference)
        self.assertEqual(replay.signing_key, admitted.signing_key)
        self.assertTrue(replay.replayed)

    def test_second_write_conflict_rolls_back_generated_reference(self) -> None:
        first_grant = self.service().prepare(self.generate_command())
        first_admitted = self.service().admit_generated(
            AdmitGeneratedDelegationSigningKey(
                grant=first_grant,
                evidence=self.evidence(),
                admitted_by="operator-a",
                admitted_at="2026-08-01T12:01:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,),
            )
        )
        second_reference = SecretReference(
            "secret://workspace-secrets/keys/gateway-b-replacement"
        )
        second_grant = self.service().prepare(
            replace(
                self.generate_command(),
                reference=second_reference,
                correlation_id="rotation-gateway-b-replacement",
            )
        )
        conflicting_evidence = DelegationKeyGenerationEvidence(
            workspace_id="workspace-a",
            reference=second_reference,
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            correlation_id="rotation-gateway-b-replacement",
            version_id="version-b",
            version_number=1,
            public_key=DelegationPublicKey(
                key_id="gateway-b",
                algorithm=DelegationKeyAlgorithm.ED25519,
                public_key_pem=PUBLIC_KEY_B,
            ),
            replayed=False,
        )

        with self.assertRaises(DelegationSigningKeyConflict):
            self.service().admit_generated(
                AdmitGeneratedDelegationSigningKey(
                    grant=second_grant,
                    evidence=conflicting_evidence,
                    admitted_by="operator-a",
                    admitted_at="2026-08-01T12:02:00Z",
                    actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,),
                )
            )

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.secret_references.list_active("workspace-a"),
                (first_admitted.secret_reference,),
            )

    def test_fold_rejects_mismatched_or_unadmitted_provider_evidence(self) -> None:
        grant = self.service().prepare(self.generate_command())
        mismatches = (
            replace(self.evidence(), workspace_id="workspace-b"),
            replace(self.evidence(), issuer="other-issuer"),
            replace(self.evidence(), correlation_id="rotation-other"),
            replace(
                self.evidence(),
                reference=SecretReference("secret://workspace-secrets/other/key-b"),
            ),
        )
        for evidence in mismatches:
            with self.subTest(evidence=evidence):
                with self.assertRaises(DelegationKeyGenerationConflict):
                    self.service().admit_generated(
                        AdmitGeneratedDelegationSigningKey(
                            grant=grant,
                            evidence=evidence,
                            admitted_by="operator-a",
                            admitted_at="2026-08-01T12:01:00Z",
                            actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,),
                        )
                    )

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.secret_references.list_active("workspace-a"),
                (),
            )

    def test_fold_requires_registration_permission(self) -> None:
        grant = self.service().prepare(self.generate_command())
        with self.assertRaises(DelegationKeyGenerationAuthorizationDenied):
            self.service().admit_generated(
                AdmitGeneratedDelegationSigningKey(
                    grant=grant,
                    evidence=self.evidence(),
                    admitted_by="operator-a",
                    admitted_at="2026-08-01T12:01:00Z",
                    actor_scopes=(PolicyScope.DELEGATION_KEY_GENERATE,),
                )
            )

    def generate_command(self) -> GenerateDelegationSigningKey:
        return GenerateDelegationSigningKey(
            workspace_id="workspace-a",
            provider_registration_id=self.provider.registration_id,
            reference=SecretReference(
                "secret://workspace-secrets/keys/gateway-b"
            ),
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            actor_subject="operator-a",
            correlation_id="rotation-gateway-b",
            requested_at="2026-08-01T12:00:00Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_GENERATE,),
        )

    def evidence(self) -> DelegationKeyGenerationEvidence:
        return DelegationKeyGenerationEvidence(
            workspace_id="workspace-a",
            reference=SecretReference(
                "secret://workspace-secrets/keys/gateway-b"
            ),
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            correlation_id="rotation-gateway-b",
            version_id="version-a",
            version_number=1,
            public_key=DelegationPublicKey(
                key_id="gateway-b",
                algorithm=DelegationKeyAlgorithm.ED25519,
                public_key_pem=PUBLIC_KEY,
            ),
            replayed=False,
        )


if __name__ == "__main__":
    unittest.main()
