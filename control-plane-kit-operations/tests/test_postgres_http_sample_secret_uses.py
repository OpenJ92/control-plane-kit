"""Real store/UoW suffix proof; never an admitted execution-history fixture."""
import concurrent.futures
from dataclasses import replace
import importlib
import importlib.util
import unittest

import psycopg

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import http_verification_sample_signing_intent_for
from control_plane_kit_operations.delegation_signing_keys import (
    ActivateDelegationSigningKeyCommand, DelegationSigningKeyRegistrationService,
    RegisterDelegationSigningKeyCommand, RetireDelegationSigningKeyCommand,
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse, RegisterSecretProviderCommand, RegisterSecretReferenceCommand,
    SecretProviderRegistrationService, SecretUseAuthorizationService,
    authorize_secret_use_in_unit_of_work,
)
from tests import test_secret_providers as providers
from tests.test_http_sample_sender_input import supplied_case, PURPOSES, NOW
from tests.health_receiver_trust_fixture import public_key

MODULE = "control_plane_kit_operations.http_sample_secret_uses"
SCOPES = (PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE,
    PolicyScope.DELEGATION_KEY_USE, PolicyScope.NODE_CONTROL_READ, PolicyScope.NODE_CONTROL_EXECUTE)


class _SecondInsertFails:
    """Driver fault injection over the real owning PostgreSQL connection."""
    def __init__(self, connection):
        self.connection, self.writes = connection, 0

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, query, *args, **kwargs):
        if "INSERT INTO cpk_secret_use_authorizations" in query:
            self.writes += 1
            if self.writes == 2:
                raise RuntimeError("second role insert refused")
        return self.connection.execute(query, *args, **kwargs)


class PostgresHttpSampleSecretUseTests(unittest.TestCase):
    setUpDatabase = providers.SecretProviderStoreTests.setUp
    tearDown = providers.SecretProviderStoreTests.tearDown
    unit_of_work = providers.SecretProviderStoreTests.unit_of_work
    wait_for_database_lock = providers.SecretProviderStoreTests.wait_for_database_lock

    def setUp(self):
        self.setUpDatabase()
        self.context, supplied = supplied_case(self)
        source = supplied["transit_provider"]
        registration = SecretProviderRegistrationService(self.unit_of_work)
        self.provider = registration.register_provider(RegisterSecretProviderCommand(
            workspace_id=source.workspace_id, provider_id=source.provider_id, provider_kind=source.provider_kind,
            display_name=source.display_name, endpoint_reference=source.endpoint_reference,
            credential_reference=source.credential_reference, allowed_reference_prefixes=source.allowed_reference_prefixes,
            allowed_intents=source.allowed_intents, admitted_by=source.admitted_by, admitted_at=NOW,
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        self.keys = {}
        service = DelegationSigningKeyRegistrationService(self.unit_of_work)
        for role, purpose in PURPOSES.items():
            key = supplied[role + "_key"]
            registration.register_reference(RegisterSecretReferenceCommand(workspace_id=key.workspace_id,
                reference=key.private_key_reference, provider_registration_id=self.provider.registration_id,
                allowed_intents=(http_verification_sample_signing_intent_for(purpose),),
                admitted_by="operator-a", admitted_at=NOW, actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
            service.register(RegisterDelegationSigningKeyCommand(workspace_id=key.workspace_id,
                purpose=purpose, issuer=key.issuer, public_key=key.public_key, private_key_reference=key.private_key_reference,
                admitted_by="operator-a", admitted_at=NOW, actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,)))
            self.keys[role] = service.activate(ActivateDelegationSigningKeyCommand(key.workspace_id,
                purpose, key.issuer, key.key_id, "operator-a", NOW, (PolicyScope.DELEGATION_KEY_ACTIVATE,)))
        self.assertIsNotNone(importlib.util.find_spec(MODULE), "atomic sample-use transaction suffix is missing")
        self.api = importlib.import_module(MODULE)

    def prepare(self, unit):
        return self.api.prepare_http_sample_uses_in_unit_of_work(unit, context=self.context,
            actor_scopes=SCOPES, requested_at=NOW)

    def count(self):
        return self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0]

    def test_pair_visible_only_after_caller_commit_and_exact_reauthorization_adds_no_third_row(self):
        with self.unit_of_work() as unit:
            pair = self.prepare(unit)
            self.assertEqual(self.count(), 0)
            unit.commit()
        self.assertEqual(self.count(), 2)
        for role in PURPOSES:
            grant = getattr(pair, role + "_grant")
            with self.unit_of_work() as unit:
                retained = self.api.reauthorize_http_sample_use_in_unit_of_work(unit,
                    context=self.context, actor_scopes=SCOPES, authorization_id=grant.authorization_id)
                self.assertEqual(retained, grant)
                unit.commit()
        with self.unit_of_work() as unit:
            self.assertEqual(self.prepare(unit), pair)
            rows = unit.stores.secret_use_authorizations.for_correlations("workspace-a",
                (pair.transit_grant.correlation_id, pair.workload_grant.correlation_id))
            self.assertEqual(len(rows), 2)
        self.assertEqual(self.count(), 2)

    def test_second_insert_failure_rolls_back_first_from_another_connection(self):
        connection = _SecondInsertFails(psycopg.connect(self.database_url))
        with self.assertRaisesRegex(RuntimeError, "second role insert refused"):
            with PostgresUnitOfWork(lambda: connection) as unit:
                self.prepare(unit)
                unit.commit()
        self.assertEqual(connection.writes, 2)
        self.assertEqual(self.count(), 0)

    def test_missing_retained_pair_cannot_be_created_by_resolution_and_partial_pair_is_not_repaired(self):
        with self.unit_of_work() as unit, self.assertRaises(self.api.HttpSampleSecretUseConflict):
            self.api.reauthorize_http_sample_use_in_unit_of_work(unit, context=self.context,
                actor_scopes=SCOPES, authorization_id="suse_" + "a" * 64)
        self.assertEqual(self.count(), 0)
        command = self.api.http_sample_authorization_command(context=self.context,
            key=self.keys["transit"], actor_scopes=SCOPES, requested_at=NOW)
        SecretUseAuthorizationService(self.unit_of_work).authorize(command)
        with self.unit_of_work() as unit, self.assertRaises(self.api.HttpSampleSecretUseConflict):
            self.prepare(unit)
        self.assertEqual(self.count(), 1)

    def test_original_key_retirement_refuses_fresh_use_but_exact_history_remains_readable(self):
        with self.unit_of_work() as unit:
            pair = self.prepare(unit)
            unit.commit()
        key = self.keys["workload"]
        service = DelegationSigningKeyRegistrationService(self.unit_of_work)
        replacement = service.register(RegisterDelegationSigningKeyCommand(
            workspace_id=key.workspace_id, purpose=key.purpose, issuer=key.issuer,
            public_key=public_key("other"),
            private_key_reference=key.private_key_reference,
            admitted_by="operator-a", admitted_at=NOW,
            actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,)))
        replacement = service.activate(ActivateDelegationSigningKeyCommand(
            key.workspace_id, key.purpose, key.issuer, replacement.key_id,
            "operator-a", NOW, (PolicyScope.DELEGATION_KEY_ACTIVATE,)))
        with self.unit_of_work() as unit:
            original = unit.stores.delegation_signing_keys.get_by_registration(
                key.workspace_id, key.purpose, key.registration_id)
            self.assertIs(original.status, RegisteredDelegationSigningKeyStatus.VERIFY_ONLY)
        retired = service.retire(
            RetireDelegationSigningKeyCommand(key.workspace_id, key.purpose, key.issuer, key.key_id,
                "operator-a", NOW, (PolicyScope.DELEGATION_KEY_RETIRE,)))
        with self.unit_of_work() as unit:
            self.assertEqual(unit.stores.delegation_signing_keys.get_by_registration(
                key.workspace_id, key.purpose, key.registration_id), retired)
            self.assertEqual(unit.stores.delegation_signing_keys.require_active(
                key.workspace_id, key.purpose, key.issuer), replacement)
            with self.assertRaises(self.api.HttpSampleSecretUseConflict):
                self.prepare(unit)
            with self.assertRaises(self.api.HttpSampleSecretUseConflict):
                self.api.reauthorize_http_sample_use_in_unit_of_work(unit, context=self.context,
                    actor_scopes=SCOPES, authorization_id=pair.workload_grant.authorization_id)
        self.assertEqual(self.count(), 2)

    def test_overlap_with_single_role_authorizer_does_not_hold_provider_while_waiting_for_second_reference(self):
        key = self.keys["workload"]
        command = AuthorizeSecretUse(workspace_id="workspace-a", reference=key.private_key_reference,
            intent=http_verification_sample_signing_intent_for(key.purpose), actor_subject="operator-a",
            correlation_id="ordinary-overlap", requested_at=NOW, actor_scopes=SCOPES)
        def producer():
            with PostgresUnitOfWork(lambda: psycopg.connect(self.database_url,
                    application_name="http-sample-pair-producer", options="-c statement_timeout=4000")) as unit:
                result = self.prepare(unit)
                unit.commit()
            return result
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            with PostgresUnitOfWork(lambda: psycopg.connect(self.database_url,
                    options="-c statement_timeout=4000")) as holder:
                holder.stores.secret_references.get_active_for_update("workspace-a", key.private_key_reference)
                future = executor.submit(producer)
                self.wait_for_database_lock("http-sample-pair-producer")
                authorize_secret_use_in_unit_of_work(holder, command)
                holder.commit()
            pair = future.result(timeout=10)
        self.assertEqual(self.count(), 3)  # one ordinary use, plus the atomic pair
        self.assertNotEqual(pair.transit_grant.authorization_id, pair.workload_grant.authorization_id)
