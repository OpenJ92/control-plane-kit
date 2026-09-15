"""#1852 known rollback versus unknown actual commit acknowledgement."""
from contextlib import ExitStack
from dataclasses import replace
import unittest
from unittest import mock

import psycopg

from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartError, ExistingAttempt
from control_plane_kit_operations.health_effect_preparations import HealthEffectPreparationError
from control_plane_kit_operations.postgres import PostgresExecutionStore, PostgresUnitOfWork
from control_plane_kit_operations.postgres.effect_attempt_store import EffectAttemptStore
from control_plane_kit_operations.postgres.delegation_signing_key_store import DelegationSigningKeyStore
from control_plane_kit_operations.postgres.effect_attempt_intent_store import EffectAttemptIntentStore
from control_plane_kit_operations.postgres.health_effect_preparation_store import HealthEffectPreparationStore
from control_plane_kit_operations.postgres.secret_provider_store import SecretReferenceStore, SecretUseAuthorizationStore
from control_plane_kit_operations.secret_providers import SecretProviderRegistrationError
from tests.execution_lease_recovery_fixture import Sequence
from tests.health_effect_preparation_fixture import forged_copy
from tests.postgres_health_effect_start_fixture import PostgresHealthEffectStartFixture


class _CommitThenRaiseConnection:
    """Real driver commit followed by an injected lost-acknowledgement error."""
    def __init__(self, connection, failure):
        self.connection, self.failure = connection, failure
        self.commits = 0

    def execute(self, *args, **kwargs):
        return self.connection.execute(*args, **kwargs)

    def commit(self):
        self.connection.commit()
        self.commits += 1
        raise self.failure

    def rollback(self):
        return self.connection.rollback()

    def close(self):
        return self.connection.close()


class PostgresHealthEffectStartRollbackTests(PostgresHealthEffectStartFixture, unittest.TestCase):
    def test_every_precommit_write_and_commit_request_failure_restores_all_facts(self):
        self.health_start_api()
        boundaries = (
            (PostgresExecutionStore, "add_event", 1),
            (EffectAttemptIntentStore, "insert", 1),
            (EffectAttemptStore, "insert_absent", 1),
            (SecretUseAuthorizationStore, "add", 1),
            (SecretUseAuthorizationStore, "add", 2),
            (HealthEffectPreparationStore, "insert_absent", 1),
            (None, "commit-request", 1),
        )
        for owner, method, occurrence in boundaries:
            with self.subTest(method=method, occurrence=occurrence):
                self.reset_health()
                before = self.health_snapshot()
                failure = RuntimeError("precommit-failure-canary")
                def unit_of_work():
                    uow = self.unit_of_work()
                    if owner is None:
                        uow.commit = mock.Mock(side_effect=failure)
                    return uow
                with ExitStack() as stack:
                    if owner is not None:
                        original = getattr(owner, method)
                        calls = 0
                        def write(store, *args, **kwargs):
                            nonlocal calls
                            calls += 1
                            if calls == occurrence:
                                raise failure
                            return original(store, *args, **kwargs)
                        stack.enter_context(mock.patch.object(owner, method, write))
                    with self.assertRaises(RuntimeError) as caught:
                        self.execute_health(unit_of_work=unit_of_work)
                self.assertIs(caught.exception, failure)
                self.assertEqual(self.health_snapshot(), before)
                self.assertEqual(self.health_counts(), (0, 0, 0, 0))

    def test_added_owner_errors_keep_original_identity_including_domain_named_type(self):
        self.health_start_api()
        boundaries = (
            (SecretUseAuthorizationStore, "add", 1),
            (SecretUseAuthorizationStore, "add", 2),
            (DelegationSigningKeyStore, "require_unambiguous_active", 1),
            (SecretReferenceStore, "get_by_registration", 1),
            (SecretUseAuthorizationStore, "get", 1),
        )
        for error_type in (ValueError, KeyError, TypeError, SecretProviderRegistrationError):
            for owner, method, occurrence in boundaries:
                with self.subTest(error_type=error_type.__name__, method=method, occurrence=occurrence):
                    self.reset_health()
                    before = self.health_snapshot()
                    failure = error_type("raw-owner-error-must-not-be-translated")
                    original = getattr(owner, method)
                    calls = 0
                    def operation(store, *args, **kwargs):
                        nonlocal calls
                        calls += 1
                        if calls == occurrence:
                            raise failure
                        return original(store, *args, **kwargs)
                    with mock.patch.object(owner, method, operation):
                        with self.assertRaises(error_type) as caught:
                            self.execute_health()
                    self.assertIs(caught.exception, failure)
                    # Raw adapter exceptions are not classified as display-safe.
                    self.assertEqual(self.health_snapshot(), before)

    def test_changed_use_readback_and_preparation_acknowledgements_roll_back(self):
        self.health_start_api()
        for boundary in ("use-readback", "preparation-none", "preparation-hostile"):
            with self.subTest(boundary=boundary):
                self.reset_health()
                before = self.health_snapshot()
                if boundary == "use-readback":
                    original = SecretUseAuthorizationStore.get
                    def acknowledgement(store, *args):
                        return replace(original(store, *args), actor_subject="other-actor")
                    owner, method = SecretUseAuthorizationStore, "get"
                else:
                    original = HealthEffectPreparationStore.insert_absent
                    def acknowledgement(store, value):
                        original(store, value)
                        if boundary == "preparation-none":
                            return None
                        return forged_copy(value, subclass=True)
                    owner, method = HealthEffectPreparationStore, "insert_absent"
                with mock.patch.object(owner, method, acknowledgement):
                    with self.assertRaises((EffectAttemptStartError, HealthEffectPreparationError)):
                        self.execute_health()
                self.assertEqual(self.health_snapshot(), before)

    def test_actual_driver_commit_then_raise_retains_complete_observation_only_replay(self):
        self.health_start_api()
        failure = RuntimeError("lost-commit-acknowledgement")
        connections = []
        def unit_of_work():
            def connect():
                connection = _CommitThenRaiseConnection(psycopg.connect(self.database_url), failure)
                connections.append(connection)
                return connection
            return PostgresUnitOfWork(connect)
        ids = Sequence("health-original", "health-request", "health-transit-jti", "health-workload-jti")
        with self.assertRaises(RuntimeError) as caught:
            self.execute_health(ids=ids, unit_of_work=unit_of_work)
        self.assertIs(caught.exception, failure)
        self.assertEqual([connection.commits for connection in connections], [1])
        self.assertEqual(ids.calls, ["health-original", "health-request", "health-transit-jti", "health-workload-jti"])
        self.assertEqual(self.health_counts(), (1, 1, 2, 1))
        with self.unit_of_work() as uow:
            retained = uow.stores.health_effect_preparations.get(self.start_value.transition.identity)
            attempt = uow.stores.effect_attempts.get(retained.identity)
            intent = uow.stores.effect_attempt_intents.get(retained.identity)
            self.assertEqual(attempt.original_start_event.event_id, "health-original")
            self.assertEqual(intent.original_start_event, attempt.original_start_event)
            self.assertEqual(intent.intent, self.start_value.intent)
            self.assertEqual(retained.original_event_id, "health-original")
            for family in ("transit", "workload"):
                use = uow.stores.secret_use_authorizations.get("workspace-a", getattr(retained, family + "_authorization_id"))
                self.assertEqual(use.actor_subject, "health-operator")
                self.assertEqual(use.activity_id, self.health_activity.activity_id.value)
        before = self.health_snapshot()
        replay_ids = Sequence("must-not-allocate")
        with self.forbid_fresh_health():
            replay, _ = self.execute_health(ids=replay_ids)
        self.assertEqual(replay.start, ExistingAttempt(attempt))
        self.assertEqual(replay.preparation, retained)
        self.assertEqual(replay_ids.calls, [])
        self.assertEqual(self.health_snapshot(), before)

    def test_invalid_logical_request_and_grant_ids_leave_no_partial_start(self):
        self.health_start_api()
        for index in (1, 2, 3):
            with self.subTest(index=index):
                self.reset_health()
                values = ["health-original", "health-request", "health-transit-jti", "health-workload-jti"]
                values[index] = "invalid-id-canary/" + "x" * 256
                before = self.health_snapshot()
                with self.assertRaises((EffectAttemptStartError, HealthEffectPreparationError, ValueError)) as caught:
                    self.execute_health(ids=Sequence(*values))
                self.assert_safe_error(caught.exception, "invalid-id-canary")
                self.assertEqual(self.health_snapshot(), before)
