"""Real metadata transaction suffix; not a reached/admitted SQL invocation."""
from dataclasses import replace
import unittest
import psycopg

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretProviderId, SecretReference, SecretUseIntent, health_signing_intent_for
from control_plane_kit_operations.delegation_signing_keys import (
    ActivateDelegationSigningKeyCommand, RegisterDelegationSigningKeyCommand,
    DelegationSigningKeyRegistrationService,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres_readiness_uses import (
    PostgresReadinessUseConflict, postgres_readiness_use_correlations,
    prepare_postgres_readiness_uses_in_unit_of_work, read_postgres_readiness_uses,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse, RegisterSecretReferenceCommand, SecretProviderRegistrationService,
    SecretUseAuthorizationService,
    SecretProviderRegistrationConflict,
)
from tests import test_secret_providers as providers
from tests import test_managed_verification_execution as managed_fixtures
from tests import test_postgres_readiness_binding as original_fixtures
from tests.test_postgres_http_sample_secret_uses import _SecondInsertFails
from tests.health_receiver_trust_fixture import public_key

SCOPES = (PolicyScope.EXECUTION_OPERATE, PolicyScope.NODE_CONTROL_READ, PolicyScope.NODE_CONTROL_EXECUTE,
    PolicyScope.DELEGATION_KEY_USE, PolicyScope.SECRET_PROVIDER_USE)
NOW = "2026-11-08T12:00:00Z"


class PostgresReadinessUseTests(unittest.TestCase):
    setUpDatabase = providers.SecretProviderStoreTests.setUp
    tearDown = providers.SecretProviderStoreTests.tearDown
    unit_of_work = providers.SecretProviderStoreTests.unit_of_work
    provider_command = providers.SecretProviderStoreTests.provider_command

    def setUp(self):
        self.setUpDatabase()
        supplied = managed_fixtures.ManagedVerificationRouteTests().prepared()
        records = original_fixtures.OriginalPostgresReadinessRecordTests().supplied_records()
        self.context = dict(binding=supplied.binding, route=supplied.route,
            actor_subject="operator-a", session_id=records["request"].identity.session_id,
            requested_at=records["wait_original"].original_start_event.occurred_at,
            original=records["wait_original"], execution_request=records["request"])
        registration = SecretProviderRegistrationService(self.unit_of_work)
        intents = (SecretUseIntent.POSTGRES_PASSWORD, supplied.transit.resolution_grant.intent,
            supplied.workload.resolution_grant.intent)
        self.provider = registration.register_provider(replace(self.provider_command(),
            provider_id=SecretProviderId("local"),
            allowed_reference_prefixes=(SecretReference("secret://local/workspace-a"),
                SecretReference("secret://local/signing")), allowed_intents=intents))
        service = DelegationSigningKeyRegistrationService(self.unit_of_work)
        self.keys = {}
        for role in ("password", "transit", "workload"):
            grant = supplied.password_grant if role == "password" else getattr(supplied, role).resolution_grant
            reference = grant.selection.reference if role == "password" else grant.reference
            intent = grant.selection.intent if role == "password" else grant.intent
            registration.register_reference(RegisterSecretReferenceCommand(workspace_id="workspace-a", reference=reference,
                provider_registration_id=self.provider.registration_id, allowed_intents=(intent,),
                admitted_by="operator-a", admitted_at=NOW, actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
            if role == "password":
                continue
            claim = getattr(supplied, role + "_grant")
            key = service.register(RegisterDelegationSigningKeyCommand(workspace_id="workspace-a",
                purpose=claim.purpose, issuer=claim.issuer, public_key=getattr(supplied, role).public_key,
                private_key_reference=reference, admitted_by="operator-a", admitted_at=NOW,
                actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,)))
            self.keys[role] = service.activate(ActivateDelegationSigningKeyCommand("workspace-a", key.purpose,
                key.issuer, key.key_id, "operator-a", NOW, (PolicyScope.DELEGATION_KEY_ACTIVATE,)))

    def prepare(self, unit, **changes):
        return prepare_postgres_readiness_uses_in_unit_of_work(unit, context=self.context, actor_scopes=SCOPES, **changes)

    def count(self):
        return self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0]

    def test_three_roles_commit_together_and_exact_reauthorization_adds_no_rows(self):
        with self.unit_of_work() as unit:
            prepared = self.prepare(unit)
            self.assertEqual(self.count(), 0)
            unit.commit()
        self.assertEqual(self.count(), 3)
        with self.unit_of_work() as unit:
            retained, rows = read_postgres_readiness_uses(unit.stores, context=self.context)
            self.assertEqual(retained, prepared)
            self.assertEqual(len(rows), 3)
            correlations = tuple(row.correlation_id for row in rows)
            self.assertEqual(unit.stores.secret_use_authorizations.for_three_correlations("workspace-b", correlations), ())
            with self.assertRaises(SecretProviderRegistrationConflict):
                unit.stores.secret_use_authorizations.for_correlations("workspace-a", correlations)
            self.assertEqual(self.prepare(unit, allow_create=False), prepared)
            unit.commit()
        self.assertEqual(self.count(), 3)
        self.assertEqual(prepared.password_grant.selection.version_id, "selected-version-1")
        self.assertEqual(prepared.password_grant.effect_id, prepared.binding.wait.effect_id)
        self.assertIsNone(prepared.transit.resolution_grant.effect_id)
        self.assertIsNone(prepared.workload.resolution_grant.probe_id)

    def test_second_insert_failure_rolls_back_all_roles(self):
        connection = _SecondInsertFails(psycopg.connect(self.database_url))
        with self.assertRaisesRegex(RuntimeError, "second role insert refused"):
            with PostgresUnitOfWork(lambda: connection) as unit:
                self.prepare(unit)
                unit.commit()
        self.assertEqual(connection.writes, 2)
        self.assertEqual(self.count(), 0)

    def test_missing_consumed_or_partial_roles_are_never_completed(self):
        with self.unit_of_work() as unit, self.assertRaises(PostgresReadinessUseConflict):
            self.prepare(unit, allow_create=False)
        key = self.keys["transit"]
        binding = self.context["binding"]
        correlation = postgres_readiness_use_correlations(binding=binding)["transit"]
        from control_plane_kit_operations.health_effect_preparations import health_effect_attempt_wire_id
        SecretUseAuthorizationService(self.unit_of_work).authorize(AuthorizeSecretUse(
            workspace_id="workspace-a", reference=key.private_key_reference, intent=health_signing_intent_for(key.purpose),
            actor_subject="operator-a", session_id=self.context["session_id"], correlation_id=correlation, requested_at=NOW,
            actor_scopes=SCOPES, operation_id=health_effect_attempt_wire_id(binding.attempt),
            run_id=binding.attempt.run_id.value, activity_id=binding.attempt.activity_id))
        with self.unit_of_work() as unit, self.assertRaises(PostgresReadinessUseConflict):
            self.prepare(unit)
        self.assertEqual(self.count(), 1)

    def test_replacement_cannot_rebind_original_history_and_replay_issues_no_new_window(self):
        with self.unit_of_work() as unit:
            prepared = self.prepare(unit)
            unit.commit()
        key = self.keys["workload"]
        service = DelegationSigningKeyRegistrationService(self.unit_of_work)
        replacement = service.register(RegisterDelegationSigningKeyCommand("workspace-a", key.purpose,
            key.issuer, public_key("other"), key.private_key_reference, "operator-a", NOW,
            (PolicyScope.DELEGATION_KEY_REGISTER,)))
        service.activate(ActivateDelegationSigningKeyCommand("workspace-a", key.purpose, key.issuer,
            replacement.key_id, "operator-a", NOW, (PolicyScope.DELEGATION_KEY_ACTIVATE,)))
        with self.unit_of_work() as unit:
            retained, _ = read_postgres_readiness_uses(unit.stores, context=self.context)
            self.assertEqual(retained, prepared)
            with self.assertRaises(PostgresReadinessUseConflict):
                self.prepare(unit, allow_create=False)
        self.assertEqual(self.count(), 3)

    def test_changed_actor_session_or_start_cannot_rekey_or_mint_another_set(self):
        with self.unit_of_work() as unit:
            prepared = self.prepare(unit)
            unit.commit()
        for change in (dict(actor_subject="other-operator"), dict(session_id="other-session"),
                dict(requested_at="2026-11-08T12:00:01Z")):
            with self.subTest(fields=tuple(change)), self.unit_of_work() as unit, self.assertRaises(PostgresReadinessUseConflict):
                prepare_postgres_readiness_uses_in_unit_of_work(unit, context=self.context | change, actor_scopes=SCOPES)
            self.assertEqual(self.count(), 3)
        self.assertEqual(prepared.transit_grant.issued_at, prepared.binding.started_at_epoch_ms // 1000)
