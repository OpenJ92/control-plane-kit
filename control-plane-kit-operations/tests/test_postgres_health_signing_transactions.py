"""#1846 read transaction, owner error identity and lock-boundary laws."""
from dataclasses import replace
import threading
import unittest
from unittest import mock

import psycopg

from control_plane_kit_operations.postgres import PostgresExecutionStore, PostgresUnitOfWork
from control_plane_kit_operations.postgres.activity_history import PostgresActivityHistoryStore
from control_plane_kit_operations.postgres.delegation_signing_key_store import DelegationSigningKeyStore
from control_plane_kit_operations.postgres.node_control_signing_authority_store import _NodeControlSigningAuthorityStore
from control_plane_kit_operations.secret_providers import SecretProviderRegistrationError
from tests.health_signing_authority_fixture import PostgresHealthSigningAuthorityFixture, timestamp
from tests.test_postgres_health_effect_start_rollback import _CommitThenRaiseConnection


class PostgresHealthSigningTransactionTests(PostgresHealthSigningAuthorityFixture, unittest.TestCase):
    def test_plan_and_chain_owner_errors_keep_raw_and_domain_named_identity(self):
        for owner, method in ((PostgresActivityHistoryStore, "get_plan_for_share"),
                (_NodeControlSigningAuthorityStore, "get_health_for_share")):
            for error_type in (ValueError, self.reload_api.HealthSigningAuthorityUnavailable):
                failure = error_type("raw-owner-failure")
                before = self.health_snapshot()
                with mock.patch.object(owner, method, side_effect=failure) as read:
                    with self.assertRaises(error_type) as caught:
                        self.reload()
                self.assertIs(caught.exception, failure)
                read.assert_called_once()
                self.assert_history_unchanged(before)

    def test_failed_real_transaction_exit_returns_no_pair_and_preserves_history(self):
        for error_type in (psycopg.OperationalError, self.reload_api.HealthSigningAuthorityUnavailable):
            failure = error_type("lost-read-transaction-acknowledgement")
            connections, returned = [], []
            def connection_factory():
                connection = _CommitThenRaiseConnection(psycopg.connect(self.database_url), failure)
                connections.append(connection)
                return connection
            before = self.health_snapshot()
            with self.observed_time(timestamp(self.current_time)):
                with self.assertRaises(error_type) as caught:
                    returned.append(self.reload(unit_of_work=lambda: PostgresUnitOfWork(connection_factory)))
            self.assertIs(caught.exception, failure)
            self.assertEqual(returned, [])
            self.assertEqual([connection.commits for connection in connections], [1])
            self.assert_history_unchanged(before)

    def test_locked_pair_partial_or_foreign_use_is_denied_without_repair(self):
        original = _NodeControlSigningAuthorityStore.get_health_for_share
        for family in ("transit", "workload"):
            for partial in (False, True):
                reads = []
                def substitute(store, preparation):
                    truth = original(store, preparation)
                    reads.append(preparation.identity)
                    value = getattr(truth, family)
                    changed = None if partial else replace(value,
                        authorization=replace(value.authorization, actor_subject="foreign-actor"))
                    return replace(truth, **{family: changed})
                before = self.health_snapshot()
                with mock.patch.object(_NodeControlSigningAuthorityStore, "get_health_for_share", substitute):
                    with self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable) as caught:
                        self.reload()
                self.assertEqual(reads, [self.preparation.identity])
                self.assert_safe(caught.exception, "foreign-actor", "secret://")
                self.assert_history_unchanged(before)

    def test_one_db_observation_occurs_after_all_current_locks_and_holds_through_exit(self):
        observed, release = threading.Event(), threading.Event()
        returned, failures = [], []
        with self.observed_time(timestamp(self.current_time)) as observations:
            original = PostgresExecutionStore.observe_request_lease_for_update
            def barrier(store, request_id):
                value = original(store, request_id)
                observed.set()
                if not release.wait(10):
                    raise AssertionError("health reload observation barrier was not released")
                return value
            def run():
                try:
                    returned.append(self.reload())
                except BaseException as error:
                    failures.append(error)
            with mock.patch.object(PostgresExecutionStore, "observe_request_lease_for_update", barrier):
                worker = threading.Thread(target=run)
                worker.start()
                try:
                    self.assertTrue(observed.wait(5), "reload did not reach the DB observation")
                    probes = [
                        ("cpk_execution_requests", "status", "request_id", "request-a"),
                        ("cpk_activity_runs", "status", "run_id", "run-a"),
                        ("cpk_effect_attempts", "attempt", "run_id", "run-a"),
                        ("cpk_activity_plans", "status", "plan_id", "plan-a"),
                        ("cpk_secret_providers", "status", "registration_id", self.provider.registration_id),
                    ]
                    for family in ("transit", "workload"):
                        probes.extend([
                            ("cpk_secret_use_authorizations", "actor_subject", "authorization_id", getattr(self.preparation, family + "_authorization_id")),
                            ("cpk_secret_references", "status", "registration_id", self.references[family].registration_id),
                        ])
                    for table, column, selector, identity in probes:
                        with self.subTest(lock=table, identity=identity):
                            contender = psycopg.connect(self.database_url)
                            try:
                                contender.execute("SET LOCAL lock_timeout='250ms'")
                                with self.assertRaises(psycopg.errors.LockNotAvailable):
                                    # Identifiers above are closed literals; values are parameters.
                                    contender.execute(f"UPDATE {table} SET {column}={column} WHERE {selector}=%s", (identity,))
                            finally:
                                contender.rollback()
                                contender.close()
                    for family in ("transit", "workload"):
                        key = self.keys[family]
                        contender = psycopg.connect(self.database_url)
                        try:
                            contender.execute("SET LOCAL lock_timeout='250ms'")
                            with self.assertRaises(psycopg.errors.LockNotAvailable):
                                DelegationSigningKeyStore(contender).revoke("workspace-a", key.purpose, key.issuer, key.key_id,
                                    revoked_by="operator-a", revoked_at="2030-01-01T00:00:00Z")
                        finally:
                            contender.rollback()
                            contender.close()
                    self.assertEqual(returned, [])
                finally:
                    release.set()
                    worker.join(5)
                self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(returned), 1)
        self.assertEqual(len(observations), 1)
        self.assertEqual(returned[0].preparation, self.preparation)

    def test_active_reference_and_provider_must_still_admit_the_health_intent(self):
        original = _NodeControlSigningAuthorityStore.get_health_for_share
        for owner in ("reference", "provider"):
            reads = []
            def substitute(store, preparation):
                truth = original(store, preparation)
                value = getattr(truth.transit, owner)
                candidate = replace(value, allowed_intents=(self.family_intents[1],))
                self.assertEqual(candidate.registration_id, value.registration_id)
                self.assertIs(candidate.status, value.status)
                self.assertNotIn(self.family_intents[0], candidate.allowed_intents)
                reads.append(preparation.identity)
                return replace(truth, transit=replace(truth.transit, **{owner: candidate}))
            before = self.health_snapshot()
            with mock.patch.object(_NodeControlSigningAuthorityStore, "get_health_for_share", substitute):
                with self.assertRaises((self.reload_api.HealthSigningAuthorityUnavailable, SecretProviderRegistrationError)):
                    self.reload()
            self.assertEqual(reads, [self.preparation.identity])
            self.assert_history_unchanged(before)

    def test_closed_query_reuse_preserves_old_api_and_one_read_only_query(self):
        import inspect
        from pathlib import Path
        source = Path(inspect.getfile(_NodeControlSigningAuthorityStore)).read_text()
        self.assertEqual(source.count(".execute("), 1)
        self.assertIn("FOR SHARE", source)
        self.assertEqual(tuple(inspect.signature(_NodeControlSigningAuthorityStore.get_for_share).parameters), ("self", "attempt"))
        for token in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "CREATE "):
            self.assertNotIn(token, source.upper())
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.activity_history.get_plan_for_share("plan-a"), uow.stores.activity_history.get_plan("plan-a"))
