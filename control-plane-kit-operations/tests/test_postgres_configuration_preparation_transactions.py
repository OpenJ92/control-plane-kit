"""#1923 strengthened PostgreSQL one-winner and compound-rollback laws."""
from concurrent.futures import ThreadPoolExecutor
import queue
import unittest
from unittest import mock

import psycopg

from control_plane_kit_operations.effect_attempt_start import ExistingAttempt, NewlyStarted
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
from control_plane_kit_operations.effect_attempt_fold import FoldEffectAttempt
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_outcome_evidence import ExecutionEffectOutcome, effect_outcome_failure, effect_outcome_transition
from control_plane_kit_operations.postgres import PostgresExecutionStore, PostgresUnitOfWork, install_schema
from control_plane_kit_operations.postgres.schema import SchemaInstallationError
from control_plane_kit_operations.postgres.effect_attempt_intent_store import EffectAttemptIntentStore
from control_plane_kit_operations.postgres.effect_attempt_store import EffectAttemptStore
from tests.configuration_preparation_fixture import ConfigurationPreparationFixture
from tests.execution_lease_recovery_fixture import Sequence
from tests import test_postgres_effect_attempt_start_concurrency as concurrency
from tests import test_postgres_effect_attempt_start_eligibility_rollback as rollback


class _WriteFailureConnection:
    def __init__(self, connection, relation, fault):
        self.connection, self.relation, self.fault = connection, relation, fault

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, query, *args, **kwargs):
        result = self.connection.execute(query, *args, **kwargs)
        text = query.as_string(self.connection) if hasattr(query, "as_string") else str(query)
        normalized = " ".join(text.lower().replace('"', '').split())
        if "insert into " + self.relation in normalized:
            raise self.fault
        return result


class PostgresConfigurationPreparationTransactionTests(ConfigurationPreparationFixture, unittest.TestCase):
    # Reuse the established real-PostgreSQL blocker apparatus, not its tests.
    _factory_with_pids = concurrency.PostgresEffectAttemptStartConcurrencyTests._factory_with_pids
    _wait_until_blocked_by = concurrency.PostgresEffectAttemptStartConcurrencyTests._wait_until_blocked_by

    def test_fresh_configuration_fold_waits_for_lifecycle_lock_before_ids(self):
        started = self.start_service("configuration-start").execute(self.configuration_command())
        original_protection = self.protection_rows()
        outcome = ExecutionEffectOutcome(started.attempt.state.identity, started.attempt.state.request_fingerprint,
            RuntimeEffectResult.failed(started.attempt.original_start_event.event_id,
                RuntimeEffectFailure("configuration.test-failure", "Bounded test outcome.")))
        command = FoldEffectAttempt("request-a", effect_outcome_transition(outcome), self.authority(), self.fence(),
            effect_outcome_failure(outcome), outcome)
        pids = queue.Queue()
        ids = Sequence("configuration-fold")
        service = EffectAttemptFoldService(self._factory_with_pids(pids), id_factory=ids)
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.unit_of_work() as held:
                held.stores.graphs.lock_receiver_lifecycle("workspace-a")
                holder_pid = held.stores.connection.execute("SELECT pg_backend_pid()").fetchone()[0]
                future = executor.submit(service.execute, command)
                try:
                    self._wait_until_blocked_by(pids.get(timeout=5), holder_pid)
                except AssertionError:
                    # A worker error is not evidence of the missing lock law.
                    if future.done():
                        future.result(timeout=1)
                    raise
                self.assertEqual(ids.calls, [])
            result = future.result(timeout=10)
        self.assertEqual(result.attempt.state.identity, started.attempt.state.identity)
        self.assertEqual(self.protection_rows(), original_protection)

    def test_current_schema_verifies_prepared_rows_and_refuses_drift_without_repair(self):
        self.start_service("configuration-start").execute(self.configuration_command())
        self.protection_rows()
        before = self.complete_start_snapshot()
        install_schema(self.connection)
        self.assertEqual(self.complete_start_snapshot(), before)

        class RollBackDrift(Exception):
            pass

        with self.assertRaises(RollBackDrift):
            with self.connection.transaction():
                self.connection.execute("ALTER TABLE cpk_configuration_claims ADD COLUMN unexpected_column text")
                with self.assertRaises(SchemaInstallationError):
                    install_schema(self.connection)
                self.assertEqual(self.connection.execute(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() "
                    "AND table_name='cpk_configuration_claims' AND column_name='unexpected_column'").fetchone(), (1,))
                raise RollBackDrift()
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_deleting_reciprocal_claim_cannot_commit_a_prepared_ref_without_protection(self):
        command = self.configuration_command()
        self.start_service("configuration-start").execute(command)
        self.protection_rows()
        before = self.complete_start_snapshot()
        with self.assertRaises(psycopg.IntegrityError):
            with psycopg.connect(self.database_url) as connection:
                connection.execute(
                    "DELETE FROM cpk_configuration_claims WHERE run_id='run-a' "
                    "AND activity_id='start-api' AND attempt=1 AND artifact_id=%s",
                    (command.intent.configuration_instances.instances[0].artifact_id,))
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_concurrent_identical_starts_commit_one_complete_protection_set(self):
        command = self.configuration_command()
        pids = queue.Queue()
        first_id = concurrency._BlockingId("configuration-winner")
        other_ids = Sequence("must-not-be-used")
        first = EffectAttemptStartService(self._factory_with_pids(pids), id_factory=first_id)
        second = EffectAttemptStartService(self._factory_with_pids(pids), id_factory=other_ids)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(first.execute, command)
            try:
                if not first_id.entered.wait(timeout=5):
                    first_future.result(timeout=1)
                    self.fail("first start did not reach its durable event allocation")
                first_pid = pids.get(timeout=5)
                second_future = executor.submit(second.execute, command)
                second_pid = pids.get(timeout=5)
                self._wait_until_blocked_by(second_pid, first_pid)
                first_id.release.set()
                results = first_future.result(timeout=10), second_future.result(timeout=10)
            finally:
                first_id.release.set()
        self.assertEqual(sum(isinstance(value, NewlyStarted) for value in results), 1)
        self.assertEqual(sum(isinstance(value, ExistingAttempt) for value in results), 1)
        self.assertEqual(results[0].attempt, results[1].attempt)
        self.assertEqual(other_ids.calls, [])
        refs, claims = self.protection_rows()
        self.assertEqual(len(refs), len(command.intent.configuration_instances.instances))
        self.assertEqual([row[:6] for row in refs], claims)
        self.assertEqual({row[10] for row in refs}, {"configuration-winner"})
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_activity_events WHERE run_id='run-a' "
            "AND event_type='step_started'").fetchone(), (1,))

    def test_event_intent_attempt_and_commit_faults_leave_no_partial_protection(self):
        stages = ((PostgresExecutionStore, "add_event"),
                  (EffectAttemptIntentStore, "_insert"), (EffectAttemptStore, "_insert_absent"))
        for owner, method in stages:
            with self.subTest(stage=method):
                before = self.attempt_snapshot()
                fault = RuntimeError("injected compound-write fault")
                original = getattr(owner, method)

                def write_then_fail(store, *args, **kwargs):
                    original(store, *args, **kwargs)
                    raise fault

                with mock.patch.object(owner, method, write_then_fail):
                    with self.assertRaises(RuntimeError) as caught:
                        self.start_service("rolled-back-start").execute(self.configuration_command())
                self.assertIs(caught.exception, fault)
                self.assertEqual(self.attempt_snapshot(), before)
                self.assertEqual(self.protection_rows(), ([], []))

        fault = RuntimeError("injected commit fault")
        def failing_uow():
            return PostgresUnitOfWork(lambda: rollback._CommitFailureConnection(
                psycopg.connect(self.database_url), fault))
        before = self.attempt_snapshot()
        with self.assertRaises(RuntimeError) as caught:
            EffectAttemptStartService(failing_uow, id_factory=Sequence("failed-commit")).execute(
                self.configuration_command())
        self.assertIs(caught.exception, fault)
        self.assertEqual(self.attempt_snapshot(), before)
        self.assertEqual(self.protection_rows(), ([], []))

    def test_ref_and_claim_write_faults_roll_back_the_entire_start(self):
        for relation in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            with self.subTest(relation=relation):
                before = self.complete_start_snapshot()
                fault = RuntimeError("injected protective-write fault")
                def failing_uow():
                    return PostgresUnitOfWork(lambda: _WriteFailureConnection(
                        psycopg.connect(self.database_url), relation, fault))
                with self.assertRaises(RuntimeError) as caught:
                    EffectAttemptStartService(failing_uow, id_factory=Sequence("failed-protection")).execute(
                        self.configuration_command())
                self.assertIs(caught.exception, fault)
                self.assertEqual(self.complete_start_snapshot(), before)
