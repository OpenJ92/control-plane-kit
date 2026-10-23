"""Protected-material custody laws at the durable Operations boundary."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
import importlib
import importlib.util
import json
import threading
import unittest
import uuid

import psycopg

from control_plane_kit_core.identity import PrincipalIdentity, PrincipalKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretCustodyReceipt, SecretCustodyStatus, SecretReference, SecretUseIntent,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.postgres.schema import SchemaInstallationError
from control_plane_kit_operations.secret_providers import (
    RevokeSecretProviderCommand, RevokeSecretReferenceCommand,
    SecretProviderRegistrationConflict, generated_secret_reference_candidate,
    secret_custody_grant_for,
)
import test_secret_providers as provider_fixture


NOW = "2026-10-23T12:00:00Z"
LATER = "2026-10-23T12:01:00Z"


class SecretMaterialProvisionAttemptTests(unittest.TestCase):
    def setUp(self):
        module = "control_plane_kit_operations.secret_material_provision_attempts"
        self.assertIsNotNone(importlib.util.find_spec(module),
                             "missing protected-material custody attempt owner")
        self.api = importlib.import_module(module)
        self.fixture = provider_fixture.SecretProviderStoreTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.scopes = (PolicyScope("secret-material:provision"), PolicyScope.SECRET_PROVIDER_REGISTER)
        self.actor = PrincipalIdentity("operator-issuer", "operator-a", PrincipalKind.OPERATOR)
        self.provider = self.fixture.service().register_provider(replace(
            self.fixture.provider_command(), allowed_intents=(
                SecretUseIntent.CLOUDFLARE_API_TOKEN, SecretUseIntent.OCI_PULL_CREDENTIAL,
            ),
        ))
        self.grant = secret_custody_grant_for(
            provider=self.provider, workspace_id="workspace-a",
            reference=SecretReference("secret://workspace-secrets/workspace-a/cloudflare-token"),
            intent=SecretUseIntent.CLOUDFLARE_API_TOKEN, actor_subject=self.actor.subject_id,
            actor_scopes=self.scopes, required_scope=self.scopes[0],
            correlation_id=self.api.provision_attempt_id_for("workspace-a", "request-a"),
        )
        self.intent = self.api.ProvisionAttemptIntent("request-a", self.actor, self.grant, NOW)

    def reserve(self, intent=None):
        with self.fixture.unit_of_work() as uow:
            result = uow.stores.secret_material_provision_attempts.reserve(intent or self.intent)
            uow.commit()
        return result

    def read(self):
        with self.fixture.unit_of_work() as uow:
            return uow.stores.secret_material_provision_attempts.get("workspace-a", "request-a")

    def claim(self):
        with self.fixture.unit_of_work() as uow:
            result = uow.stores.secret_material_provision_attempts.claim_initial(
                "workspace-a", "request-a", claimed_at=LATER)
            uow.commit()
        return result

    def evidence(self):
        return SecretCustodyReceipt(self.grant.custody_id, self.provider.registration_id,
                                    self.grant.reference, "version-a", 1)

    def record_receipt(self, claim, evidence=None):
        with self.fixture.unit_of_work() as uow:
            result = uow.stores.secret_material_provision_attempts.record_receipt(
                claim, evidence or self.evidence(), observed_at=LATER)
            uow.commit()
        return result

    def pending(self):
        self.reserve()
        claim = self.claim()
        self.record_receipt(claim)
        return claim

    def admit(self, claim, *, actor=None, scopes=None, factory=None):
        return self.api.ProvisionAttemptAdmissionService(factory or self.fixture.unit_of_work).admit(
            claim, actor=actor or self.actor,
            actor_scopes=self.scopes if scopes is None else scopes, admitted_at=LATER)

    def candidate(self):
        return generated_secret_reference_candidate(
            grant=self.grant, receipt=self.evidence(), admitted_at=LATER)

    def test_reserve_roundtrip_and_every_duplicate_conflicts(self):
        original = self.reserve()
        self.assertEqual(self.read(), original)
        self.assertEqual(original.state.value, "intended")
        self.assertEqual(original.intent.actor, self.actor)
        for candidate in (self.intent, replace(self.intent, requested_at=LATER),
                          replace(self.intent, actor=replace(self.actor, issuer="another-issuer"))):
            with self.subTest(actor=candidate.actor):
                with self.assertRaises(self.api.ProvisionAttemptConflict):
                    self.reserve(candidate)
                self.assertEqual(self.read(), original)

    def test_concurrent_reservation_and_single_initial_claim(self):
        barrier = threading.Barrier(2)
        def reserve():
            barrier.wait(timeout=10)
            try:
                return self.reserve()
            except self.api.ProvisionAttemptConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(lambda _: reserve(), range(2)))
        self.assertEqual(sum(item is not None for item in results), 1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = tuple(pool.map(lambda _: self.claim(), range(2)))
        self.assertEqual(sum(item is not None for item in claims), 1)
        self.assertEqual(self.read().state.value, "dispatching")
        self.assertIsNone(self.claim(), "restart must not reopen an initial dispatch")

    def test_closed_intent_full_identity_and_no_material_fields(self):
        original = self.reserve()
        self.assertEqual(self.api.decode_intent(original.intent.descriptor()), self.intent)
        self.assertNotEqual(replace(self.intent, actor=replace(self.actor, issuer="other")).fingerprint,
                            self.intent.fingerprint)
        for field in ("value", "value_base64", "value_digest"):
            with self.subTest(field=field), self.assertRaises(self.api.ProvisionAttemptError):
                self.api.decode_intent({**self.intent.descriptor(), field: "private-input-sentinel"})
        with self.assertRaises(self.api.ProvisionAttemptError):
            replace(self.intent, actor=replace(self.actor, kind=PrincipalKind.SERVICE))
        with self.assertRaises(self.api.ProvisionAttemptError):
            replace(self.intent, grant=replace(self.grant, intent=SecretUseIntent.POSTGRES_PASSWORD))

    def test_receipt_requires_exact_active_identity_and_cannot_regress(self):
        self.reserve()
        claim = self.claim()
        receipt = self.evidence()
        invalid = (replace(receipt, custody_id="other"),
                   replace(receipt, provider_registration_id="other"),
                   replace(receipt, reference=SecretReference("secret://workspace-secrets/other")),
                   replace(receipt, status=SecretCustodyStatus.REVOKED))
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(self.api.ProvisionAttemptError):
                self.record_receipt(claim, value)
        retained = self.record_receipt(claim)
        with self.assertRaises(self.api.ProvisionAttemptConflict):
            self.record_receipt(claim, replace(receipt, version_id="another-version"))
        for method in ("record_uncertain", "record_refusal"):
            with self.subTest(method=method), self.fixture.unit_of_work() as uow:
                with self.assertRaises(self.api.ProvisionAttemptConflict):
                    getattr(uow.stores.secret_material_provision_attempts, method)(claim, observed_at=LATER)
        self.assertEqual(self.read(), retained)

    def test_uncertain_and_pre_invocation_refusal_never_reopen_dispatch(self):
        self.reserve()
        claim = self.claim()
        with self.fixture.unit_of_work() as uow:
            uow.stores.secret_material_provision_attempts.record_uncertain(claim, observed_at=LATER)
            uow.commit()
        self.assertEqual(self.read().state.value, "uncertain")
        self.assertIsNone(self.claim())
        with self.fixture.unit_of_work() as uow:
            with self.assertRaises(self.api.ProvisionAttemptConflict):
                uow.stores.secret_material_provision_attempts.record_refusal(claim, observed_at=LATER)
        other = replace(self.intent, request_id="request-b", grant=replace(
            self.grant, correlation_id=self.api.provision_attempt_id_for("workspace-a", "request-b")))
        self.reserve(other)
        with self.fixture.unit_of_work() as uow:
            store = uow.stores.secret_material_provision_attempts
            claim_b = store.claim_initial("workspace-a", "request-b", claimed_at=LATER)
            store.record_refusal(claim_b, observed_at=LATER)
            uow.commit()
        with self.fixture.unit_of_work() as uow:
            self.assertEqual(uow.stores.secret_material_provision_attempts.get(
                "workspace-a", "request-b").state.value, "refused")
            self.assertIsNone(uow.stores.secret_material_provision_attempts.claim_initial(
                "workspace-a", "request-b", claimed_at=LATER))

    def test_admission_requires_current_full_operator_and_both_scopes(self):
        claim = self.pending()
        for actor, scopes in ((replace(self.actor, issuer="other"), self.scopes),
                              (replace(self.actor, kind=PrincipalKind.SERVICE), self.scopes),
                              (self.actor, self.scopes[:1]), (self.actor, self.scopes[1:])):
            with self.subTest(actor=actor, scopes=scopes):
                with self.assertRaises(self.api.ProvisionAttemptAuthorizationDenied):
                    self.admit(claim, actor=actor, scopes=scopes)
        self.assertEqual(self.read().state.value, "receipt-recorded")

    def test_admission_commits_exact_reference_and_safe_public_receipt(self):
        claim = self.pending()
        admitted = self.admit(claim)
        self.assertEqual(self.read(), admitted)
        self.assertEqual(admitted.state.value, "admitted")
        with self.fixture.unit_of_work() as uow:
            reference = uow.stores.secret_references.get_by_registration(
                "workspace-a", admitted.reference_registration_id)
            self.assertEqual(reference.reference, self.grant.reference)
            self.assertEqual(reference.metadata["provider_version_id"], "version-a")
        receipt = self.api.ProvisionedSecretMaterialReceipt.from_attempt(admitted)
        public = json.dumps(asdict(receipt), default=str) + repr(receipt) + json.dumps(receipt.descriptor())
        self.assertNotIn(self.grant.credential_reference.reference_id, public)
        self.assertNotIn(self.actor.issuer, public)
        self.assertNotIn("custody_fingerprint", public)
        self.assertIn("version-a", public)

    def test_create_only_refuses_identical_registration_and_revoked_history(self):
        claim = self.pending()
        with self.fixture.unit_of_work() as uow:
            existing = uow.stores.secret_references.register(self.candidate())
            uow.commit()
        with self.assertRaises((self.api.ProvisionAttemptConflict, SecretProviderRegistrationConflict)):
            self.admit(claim)
        self.fixture.service().revoke_reference(RevokeSecretReferenceCommand(
            workspace_id="workspace-a", registration_id=existing.registration_id,
            revoked_by="operator-a", revoked_at=LATER,
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,)))
        with self.assertRaises((self.api.ProvisionAttemptConflict, SecretProviderRegistrationConflict)):
            self.admit(claim)
        with self.fixture.unit_of_work() as uow:
            history = uow.stores.secret_references.list_history("workspace-a", self.grant.reference)
            self.assertEqual(tuple(row.registration_id for row in history), (existing.registration_id,))
        self.assertEqual(self.read().state.value, "receipt-recorded")

    def test_concurrent_ordinary_registration_first_prevents_intake_adoption(self):
        claim = self.pending()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.fixture.unit_of_work() as uow:
                original = uow.stores.secret_references.register(self.candidate())
                factory = lambda: PostgresUnitOfWork(lambda: psycopg.connect(
                    self.fixture.database_url, application_name="provision-intake-waiter"))
                future = pool.submit(self.admit, claim, factory=factory)
                self.fixture.wait_for_database_lock("provision-intake-waiter")
                uow.commit()
            with self.assertRaises((self.api.ProvisionAttemptConflict, SecretProviderRegistrationConflict)):
                future.result(timeout=15)
        self.assertEqual(self.read().state.value, "receipt-recorded")
        with self.fixture.unit_of_work() as uow:
            self.assertEqual(uow.stores.secret_references.list_history(
                "workspace-a", self.grant.reference), (original,))

    def test_create_only_winner_preserves_ordinary_registration_replay(self):
        candidate = self.candidate()
        def ordinary():
            with PostgresUnitOfWork(lambda: psycopg.connect(
                    self.fixture.database_url, application_name="provision-register-waiter")) as uow:
                value = uow.stores.secret_references.register(candidate)
                uow.commit()
            return value
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.fixture.unit_of_work() as uow:
                created = uow.stores.secret_references.register_new(candidate)
                future = pool.submit(ordinary)
                self.fixture.wait_for_database_lock("provision-register-waiter")
                uow.commit()
            self.assertEqual(future.result(timeout=15), created)

    def test_provider_revocation_preserves_pending_receipt(self):
        claim = self.pending()
        self.fixture.service().revoke_provider(RevokeSecretProviderCommand(
            workspace_id="workspace-a", provider_id=self.provider.provider_id,
            revoked_by="operator-a", revoked_at=LATER,
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,)))
        with self.assertRaises(self.api.ProvisionAttemptConflict):
            self.admit(claim)
        self.assertEqual(self.read().state.value, "receipt-recorded")
        with self.fixture.unit_of_work() as uow:
            self.assertEqual(uow.stores.secret_references.list_history("workspace-a", self.grant.reference), ())

    def test_physical_commit_failure_rolls_back_admission_not_receipt(self):
        claim = self.pending()
        retained = self.read()
        @contextmanager
        def failing_commit():
            with self.fixture.unit_of_work() as uow:
                connection = uow.stores.connection
                connection.execute("CREATE TEMP TABLE provision_commit_guard (value integer UNIQUE DEFERRABLE INITIALLY DEFERRED)")
                yield uow
                connection.execute("INSERT INTO provision_commit_guard VALUES (1),(1)")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.admit(claim, factory=failing_commit)
        self.assertEqual(self.read(), retained)
        with self.fixture.unit_of_work() as uow:
            self.assertEqual(uow.stores.secret_references.list_history("workspace-a", self.grant.reference), ())

    def test_corrupt_persisted_intent_is_not_absence(self):
        self.reserve()
        self.fixture.connection.execute(
            "UPDATE cpk_secret_material_provision_attempts SET intent_descriptor=intent_descriptor || %s::jsonb",
            (json.dumps({"value_base64": "forbidden-input"}),))
        with self.assertRaises(self.api.ProvisionAttemptError):
            self.read()

    def test_pre_intake_schema_refuses_without_reset_or_row_loss(self):
        schema = "provision_attempt_" + uuid.uuid4().hex
        with psycopg.connect(self.fixture.database_url, autocommit=True) as connection:
            connection.execute(f'CREATE SCHEMA "{schema}"')
            try:
                connection.execute(f'SET search_path TO "{schema}"')
                install_schema(connection)
                connection.execute("INSERT INTO cpk_workspaces (workspace_id,name,lifecycle) VALUES ('retained','Retained','created')")
                connection.execute("DROP TABLE cpk_secret_material_provision_attempts")
                with self.assertRaises(SchemaInstallationError):
                    install_schema(connection)
                self.assertEqual(connection.execute("SELECT workspace_id FROM cpk_workspaces").fetchall(), [("retained",)])
                self.assertEqual(connection.execute("SELECT to_regclass('cpk_secret_material_provision_attempts')").fetchone(), (None,))
            finally:
                connection.execute("SET search_path TO public")
                connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
