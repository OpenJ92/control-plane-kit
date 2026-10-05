"""Atomic cleanup writes through public owners; faults never grant permission."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import queue
from unittest import mock

import psycopg

from control_plane_kit_core.configuration_instances import (
    ConfigurationCleanupOutcome, ConfigurationCleanupOutcomeSet, ConfigurationCleanupStatus,
)
from control_plane_kit_core.operations import EffectAttemptStatus
from control_plane_kit_core.runtime_effect_observation import runtime_effect_request_for_intent
from control_plane_kit_core.runtime_effects import configuration_cleanup_result
from control_plane_kit_operations.effect_attempt_start import (
    ExistingAttempt, NewlyStarted, EffectAttemptStartDenied, EffectAttemptStartConflict,
)
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_attempt_fold import (
    ExistingFold, FoldEffectAttempt, NewlyFolded, EffectAttemptFoldConflict,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_outcome_evidence import (
    ExecutionEffectOutcome, effect_outcome_failure, effect_outcome_transition,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.effect_attempt_intent_store import EffectAttemptIntentStore
from control_plane_kit_operations.records import OperationsRecordError
from tests.configuration_cleanup_execution_fixture import ConfigurationCleanupExecutionFixture
from tests.test_execution_admission import Sequence
from tests.test_postgres_configuration_completion import _AfterWriteFailure
from tests.test_postgres_effect_attempt_start_eligibility_rollback import _CommitFailureConnection
from tests import test_postgres_effect_attempt_start_concurrency as concurrency


class _CommitThenRaiseConnection:
    """Negative transport premise: durable commit succeeded before ack was lost."""
    def __init__(self, connection, fault):
        self.connection, self.fault = connection, fault

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        self.connection.commit()
        raise self.fault


class _RedirectCleanupApproval:
    """Valid foreign FK pair introduced after writes; no malformed-row shortcut."""
    def __init__(self, connection, foreign, observed):
        self.connection, self.foreign, self.observed = connection, foreign, observed

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, query, params=(), **kwargs):
        sql = query.as_string(self.connection) if hasattr(query, "as_string") else str(query)
        normalized = " ".join(sql.lower().split())
        if self.observed["redirected"] and ("cpk_approval_requests" in normalized
                or "cpk_approval_decisions" in normalized) and any(value in params for value in self.foreign):
            self.observed["foreign_reads"] += 1
        result = self.connection.execute(query, params, **kwargs)
        if normalized.startswith("update cpk_configuration_claims"):
            changed = self.connection.execute("UPDATE cpk_configuration_cleanup_reservations "
                "SET approval_request_id=%s,approval_decision_id=%s RETURNING 1", self.foreign).fetchall()
            if changed != [(1,)]:
                raise AssertionError("foreign approval fault did not change exactly one reservation")
            self.observed["redirected"] = True
        return result


class _AlterCleanupMemberResult:
    def __init__(self, connection, changed):
        self.connection, self.changed = connection, changed

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, query, params=(), **kwargs):
        result = self.connection.execute(query, params, **kwargs)
        if " ".join(str(query).lower().split()).startswith("insert into cpk_configuration_cleanup_member_outcomes"):
            rows = self.connection.execute("UPDATE cpk_configuration_cleanup_member_outcomes "
                "SET status='already-absent' WHERE status='removed' RETURNING 1").fetchall()
            if rows != [(1,)]:
                raise AssertionError("member result corruption must reach the actual written row")
            self.changed.append(True)
        return result


class PostgresConfigurationCleanupTransactionTests(ConfigurationCleanupExecutionFixture, unittest.TestCase):
    _factory_with_pids = concurrency.PostgresEffectAttemptStartConcurrencyTests._factory_with_pids
    _wait_until_blocked_by = concurrency.PostgresEffectAttemptStartConcurrencyTests._wait_until_blocked_by

    def fault_factory(self, statement, fault):
        def connect():
            connection = psycopg.connect(self.database_url)
            if statement == "commit":
                return _CommitFailureConnection(connection, fault)
            if statement == "commit-acknowledgment":
                return _CommitThenRaiseConnection(connection, fault)
            return _AfterWriteFailure(connection, statement, fault)
        return lambda: PostgresUnitOfWork(connect)

    def assert_no_ids(self):
        self.fail("retained original replay allocated a new identity")

    def removed_fold(self, original, started):
        request = runtime_effect_request_for_intent(original.intent,
            effect_id=started.attempt.original_start_event.event_id)
        rows = ConfigurationCleanupOutcomeSet((ConfigurationCleanupOutcome(
            self.selected_ref, ConfigurationCleanupStatus.REMOVED, None),))
        outcome = ExecutionEffectOutcome(started.attempt.state.identity,
            started.attempt.state.request_fingerprint, configuration_cleanup_result(request, rows))
        return FoldEffectAttempt(original.request_id, effect_outcome_transition(outcome),
            original.authority, original.fence, effect_outcome_failure(outcome), outcome)

    def test_each_start_write_and_confirmed_commit_fault_rolls_back_whole_reservation(self):
        claimed = self.ready_cleanup()
        command = self.native_start_command(claimed, "cleanup-execution")
        statements = (
            "insert into cpk_activity_events", "insert into cpk_effect_attempt_intents",
            "insert into cpk_effect_attempts", "insert into cpk_configuration_cleanup_reservations",
            "insert into cpk_configuration_cleanup_members", "insert into cpk_configuration_invocation_closures",
            "insert into cpk_configuration_claim_closures", "update cpk_effect_configuration_refs",
            "update cpk_configuration_claims", "commit",
        )
        for statement in statements:
            with self.subTest(stage=statement):
                before = self.ceiling_truth()
                fault = RuntimeError("injected cleanup start fault")
                with self.assertRaises(RuntimeError) as raised:
                    EffectAttemptStartService(self.fault_factory(statement, fault),
                        id_factory=Sequence("cleanup-original")).execute(command)
                self.assertIs(raised.exception, fault, "fault must reach the selected actual write")
                self.assertEqual(self.ceiling_truth(), before)
        started = EffectAttemptStartService(self.unit_of_work,
            id_factory=Sequence("cleanup-original")).execute(command)
        self.assertIs(type(started), NewlyStarted)
        with self.unit_of_work() as uow:
            reservation = uow.stores.configuration_cleanup_ownership.get(command.transition.identity)
            self.assertIs(reservation.status, EffectAttemptStatus.STARTED)
            self.assertEqual((len(reservation.members), len(reservation.completions), len(reservation.claims)), (1, 1, 1))

    def test_each_fold_write_and_confirmed_commit_fault_rolls_back_outcome_cas_and_members(self):
        claimed = self.ready_cleanup()
        original, started = self.start_cleanup(claimed)
        command = self.removed_fold(original, started)
        for statement in ("insert into cpk_activity_events", "insert into cpk_effect_attempt_outcomes",
                          "update cpk_effect_attempts", "insert into cpk_configuration_cleanup_member_outcomes", "commit"):
            with self.subTest(stage=statement):
                before = self.ceiling_truth()
                fault = RuntimeError("injected cleanup fold fault")
                with self.assertRaises(RuntimeError) as raised:
                    EffectAttemptFoldService(self.fault_factory(statement, fault),
                        id_factory=Sequence("cleanup-terminal")).execute(command)
                self.assertIs(raised.exception, fault, "fault must reach the selected actual write")
                self.assertEqual(self.ceiling_truth(), before)
                with self.unit_of_work() as uow:
                    reservation = uow.stores.configuration_cleanup_ownership.get(started.attempt.state.identity)
                    self.assertIs(reservation.status, EffectAttemptStatus.STARTED)
                    self.assertIsNone(reservation.outcomes)
        folded = EffectAttemptFoldService(self.unit_of_work,
            id_factory=Sequence("cleanup-terminal")).execute(command)
        self.assertIs(type(folded), NewlyFolded)

    def test_lost_start_commit_ack_is_resolved_by_original_replay_without_new_permission(self):
        claimed = self.ready_cleanup()
        command = self.native_start_command(claimed, "cleanup-execution")
        fault = RuntimeError("lost start commit acknowledgment")
        with self.assertRaises(RuntimeError) as raised:
            EffectAttemptStartService(self.fault_factory("commit-acknowledgment", fault),
                id_factory=Sequence("cleanup-original")).execute(command)
        self.assertIs(raised.exception, fault)
        with self.unit_of_work() as uow:
            reservation = uow.stores.configuration_cleanup_ownership.get(command.transition.identity)
            self.assertIsNotNone(reservation, "lost acknowledgment must not be treated as confirmed rollback")
            self.assertIs(reservation.status, EffectAttemptStatus.STARTED)
        before = self.ceiling_truth()
        replay = EffectAttemptStartService(self.unit_of_work, id_factory=self.assert_no_ids).execute(command)
        self.assertIs(type(replay), ExistingAttempt)
        self.assertEqual(self.ceiling_truth(), before)

    def test_lost_fold_commit_ack_replays_original_total_without_rewriting(self):
        claimed = self.ready_cleanup()
        original, started = self.start_cleanup(claimed)
        command = self.removed_fold(original, started)
        fault = RuntimeError("lost fold commit acknowledgment")
        with self.assertRaises(RuntimeError) as raised:
            EffectAttemptFoldService(self.fault_factory("commit-acknowledgment", fault),
                id_factory=Sequence("cleanup-terminal")).execute(command)
        self.assertIs(raised.exception, fault)
        with self.unit_of_work() as uow:
            reservation = uow.stores.configuration_cleanup_ownership.get(started.attempt.state.identity)
            self.assertIs(reservation.status, EffectAttemptStatus.SUCCEEDED)
            self.assertEqual(tuple(row.status for row in reservation.outcomes.outcomes),
                (ConfigurationCleanupStatus.REMOVED,))
        before = self.ceiling_truth()
        replay = EffectAttemptFoldService(self.unit_of_work, id_factory=self.assert_no_ids).execute(command)
        self.assertIs(type(replay), ExistingFold)
        self.assertEqual(self.ceiling_truth(), before)

    def test_identical_concurrent_starts_return_one_new_permission_and_one_original(self):
        claimed = self.ready_cleanup()
        command = self.native_start_command(claimed, "cleanup-execution")
        pids = queue.Queue()
        first_id = concurrency._BlockingId("cleanup-original")
        other_ids = Sequence("must-not-be-used")
        first = EffectAttemptStartService(self._factory_with_pids(pids), id_factory=first_id)
        second = EffectAttemptStartService(self._factory_with_pids(pids), id_factory=other_ids)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(first.execute, command)
            try:
                if not first_id.entered.wait(timeout=30):
                    first_future.result(timeout=1)
                    self.fail("first cleanup start never reached its event allocation")
                first_pid = pids.get(timeout=5)
                second_future = executor.submit(second.execute, command)
                second_pid = pids.get(timeout=5)
                self._wait_until_blocked_by(second_pid, first_pid)
                first_id.release.set()
                results = first_future.result(timeout=30), second_future.result(timeout=30)
            finally:
                first_id.release.set()
        self.assertCountEqual(tuple(type(value) for value in results), (NewlyStarted, ExistingAttempt))
        self.assertEqual(results[0].attempt, results[1].attempt)
        self.assertEqual(other_ids.calls, [])
        with self.unit_of_work() as uow:
            reservation = uow.stores.configuration_cleanup_ownership.get(command.transition.identity)
            self.assertEqual((len(reservation.members), len(reservation.completions), len(reservation.claims)), (1, 1, 1))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_activity_events WHERE run_id=%s AND event_type='step_started'",
            (claimed.run.run_id,)).fetchone(), (1,))

    def test_stale_fence_and_expired_lease_refuse_before_ids_and_reservation(self):
        claimed = self.ready_cleanup()
        command = self.native_start_command(claimed, "cleanup-execution")
        stale = replace(command, fence=replace(command.fence, generation=command.fence.generation + 1))
        before = self.ceiling_truth()
        with self.assertRaises(EffectAttemptStartDenied):
            EffectAttemptStartService(self.unit_of_work, id_factory=self.assert_no_ids).execute(stale)
        self.assertEqual(self.ceiling_truth(), before)
        # Below-owner expiry premise, distinct from a competing lease claim.
        self.connection.execute("UPDATE cpk_execution_requests SET lease_expires_at="
            "clock_timestamp()-interval '1 second' WHERE request_id=%s", (command.request_id,))
        expired = self.ceiling_truth()
        with self.assertRaises(EffectAttemptStartDenied):
            EffectAttemptStartService(self.unit_of_work, id_factory=self.assert_no_ids).execute(command)
        self.assertEqual(self.ceiling_truth(), expired)

    def test_final_start_proof_refuses_valid_foreign_approval_before_payload_fetch(self):
        claimed = self.ready_cleanup()
        command = self.native_start_command(claimed, "cleanup-execution")
        foreign = self.connection.execute("SELECT request_id,decision_id FROM cpk_approval_decisions "
            "WHERE request_id<>%s ORDER BY decision_id LIMIT 1", (self.approval.request_id,)).fetchone()
        self.assertIsNotNone(foreign, "chronology must contain an independently valid approval")
        self.assertTrue(all(type(value) is str and 1 <= len(value.encode()) <= 2048 for value in foreign))
        with self.unit_of_work() as uow:
            self.assertIsNotNone(uow.stores.activity_history.get_approval_request(foreign[0]))
            self.assertEqual(uow.stores.activity_history.approval_decision_for_request(foreign[0]).decision_id,
                foreign[1])
        before = self.ceiling_truth()
        observed = dict(redirected=False, foreign_reads=0)
        factory = lambda: PostgresUnitOfWork(lambda: _RedirectCleanupApproval(
            psycopg.connect(self.database_url), foreign, observed))
        with self.assertRaises(EffectAttemptStartConflict):
            EffectAttemptStartService(factory, id_factory=Sequence("cleanup-original")).execute(command)
        self.assertTrue(observed["redirected"], "fault must pass the real composite FK checks")
        self.assertEqual(observed["foreign_reads"], 0, "issued row correspondence must precede foreign payload")
        self.assertEqual(self.ceiling_truth(), before)

    def test_start_preparation_rejects_copy_foreign_uow_thread_and_finished_lifetime(self):
        claimed = self.ready_cleanup()
        command = self.native_start_command(claimed, "cleanup-execution")
        real_insert = EffectAttemptIntentStore._insert
        captured = []
        for mode in ("copy", "foreign-uow", "foreign-thread"):
            before = self.ceiling_truth()
            reached = []

            def altered(store, record, *, configuration_preparation=None):
                self.assertIsNotNone(configuration_preparation)
                reached.append(mode)
                if mode == "copy":
                    return real_insert(store, record, configuration_preparation=replace(configuration_preparation))
                if mode == "foreign-uow":
                    with self.unit_of_work() as foreign:
                        return real_insert(foreign.stores.effect_attempt_intents, record,
                            configuration_preparation=configuration_preparation)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    return executor.submit(real_insert, store, record,
                        configuration_preparation=configuration_preparation).result(timeout=5)

            with self.subTest(mode=mode), mock.patch.object(EffectAttemptIntentStore, "_insert", altered):
                with self.assertRaises(EffectAttemptStartConflict):
                    EffectAttemptStartService(self.unit_of_work,
                        id_factory=Sequence("cleanup-original")).execute(command)
            self.assertEqual(reached, [mode])
            self.assertEqual(self.ceiling_truth(), before)

        def retained(store, record, *, configuration_preparation=None):
            captured.append((store, record, configuration_preparation))
            return real_insert(store, record, configuration_preparation=configuration_preparation)

        with mock.patch.object(EffectAttemptIntentStore, "_insert", retained):
            started = EffectAttemptStartService(self.unit_of_work,
                id_factory=Sequence("cleanup-original")).execute(command)
        self.assertIs(type(started), NewlyStarted)
        self.assertEqual(len(captured), 1)
        store, record, prepared = captured[0]
        before = self.ceiling_truth()
        with self.assertRaises(OperationsRecordError):
            real_insert(store, record, configuration_preparation=prepared)
        self.assertEqual(self.ceiling_truth(), before)

    def test_final_fold_proof_refusal_rolls_back_event_outcome_cas_and_member_result(self):
        claimed = self.ready_cleanup()
        original, started = self.start_cleanup(claimed)
        command = self.removed_fold(original, started)
        before, changed = self.ceiling_truth(), []
        factory = lambda: PostgresUnitOfWork(lambda: _AlterCleanupMemberResult(
            psycopg.connect(self.database_url), changed))
        with self.assertRaises(EffectAttemptFoldConflict):
            EffectAttemptFoldService(factory, id_factory=Sequence("cleanup-terminal")).execute(command)
        self.assertEqual(changed, [True])
        self.assertEqual(self.ceiling_truth(), before)

    def test_start_and_fold_preflight_preserve_prefix_and_refuse_before_event_ids(self):
        from control_plane_kit_operations import _configuration_cleanup_ownership as cleanup
        from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
        claimed = self.ready_cleanup()
        command = self.native_start_command(claimed, "cleanup-execution")

        def run_depleted(name, invoke, conflict):
            actual = getattr(cleanup, name)
            reached = []
            before = self.ceiling_truth()

            def depleted(prepared):
                accounting = prepared.owner.accounting
                prefix = accounting.used
                self.assertGreater(prefix.records, 0)
                # Below-owner capacity fault: retain every real prefix charge
                # and consume all but 100 KiB of remaining bytes. No SQL,
                # record limit or future declaration is replaced.
                extra = 16 * 1024 * 1024 - prefix.accounted_bytes - 100 * 1024
                self.assertGreater(extra, 0)
                accounting.used = prefix.plus(ConfigurationEvidenceFootprint(0, extra, 0, 0))
                reached.append(accounting)
                actual(prepared)

            with mock.patch.object(cleanup, name, depleted), self.assertRaises(conflict):
                invoke()
            self.assertEqual(len(reached), 1)
            self.assertEqual(self.ceiling_truth(), before)

        run_depleted("_preflight_start", lambda: EffectAttemptStartService(self.unit_of_work,
            id_factory=self.assert_no_ids).execute(command), EffectAttemptStartConflict)
        started = EffectAttemptStartService(self.unit_of_work,
            id_factory=Sequence("cleanup-original")).execute(command)
        fold = self.removed_fold(command, started)
        run_depleted("_preflight_fold", lambda: EffectAttemptFoldService(self.unit_of_work,
            id_factory=self.assert_no_ids).execute(fold), EffectAttemptFoldConflict)

    def test_fresh_inspection_requires_admitted_d1_and_unreserved_candidates(self):
        from control_plane_kit_operations.configuration_cleanup_planning import ConfigurationCleanupPlanningService
        from tests.configuration_cleanup_postgres_fixture import command_context
        claimed = self.ready_cleanup()
        service = ConfigurationCleanupPlanningService(self.unit_of_work,
            clock=self.now, id_factory=self.assert_no_ids)
        # A retained provider completion profile cannot replace its D1 link.
        with self.unit_of_work() as uow:
            source = self.source_identity
            uow.stores.connection.execute("DELETE FROM cpk_configuration_invocation_completions "
                "WHERE run_id=%s AND activity_id=%s AND attempt=%s",
                (source.run_id.value, source.activity_id, source.attempt))
            result, proposal = uow.stores.configuration_cleanup.read(self.cleanup_query)
            self.assertEqual(result.state, "unavailable")
            self.assertIsNone(proposal)
            # Deliberately no commit: restore the genuine ordinary D1 premise.
        self.assertEqual(service.inspect(self.cleanup_query, context=command_context()).state, "complete")
        self.start_cleanup(claimed)
        before = self.ceiling_truth()
        self.assertEqual(service.inspect(self.cleanup_query, context=command_context()).state, "unavailable")
        self.assertEqual(self.ceiling_truth(), before)

    def test_copied_preparation_cannot_bind_start_or_fold_owner_state(self):
        from control_plane_kit_operations.postgres.configuration_cleanup_ownership_store import ConfigurationCleanupOwnershipStore
        claimed = self.ready_cleanup()
        command = self.native_start_command(claimed, "cleanup-execution")

        def refuse_copy(name, invoke, conflict):
            bind = getattr(ConfigurationCleanupOwnershipStore, name)
            reached = []
            before = self.ceiling_truth()

            def copied(store, prepared, original):
                reached.append(True)
                try:
                    return bind(store, replace(prepared), original)
                finally:
                    self.assertIsNone(prepared.owner.bound, "copy must not poison the original owner's binding")

            with mock.patch.object(ConfigurationCleanupOwnershipStore, name, copied), self.assertRaises(conflict):
                invoke()
            self.assertEqual(reached, [True])
            self.assertEqual(self.ceiling_truth(), before)

        refuse_copy("_bind_start", lambda: EffectAttemptStartService(self.unit_of_work,
            id_factory=Sequence("cleanup-original")).execute(command), EffectAttemptStartConflict)
        started = EffectAttemptStartService(self.unit_of_work,
            id_factory=Sequence("cleanup-original")).execute(command)
        fold = self.removed_fold(command, started)
        refuse_copy("_bind_fold", lambda: EffectAttemptFoldService(self.unit_of_work,
            id_factory=Sequence("cleanup-terminal")).execute(fold), EffectAttemptFoldConflict)
