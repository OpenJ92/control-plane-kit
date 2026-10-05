"""Atomic cleanup writes through public owners; faults never grant permission."""
import unittest

import psycopg

from control_plane_kit_core.configuration_instances import (
    ConfigurationCleanupOutcome, ConfigurationCleanupOutcomeSet, ConfigurationCleanupStatus,
)
from control_plane_kit_core.operations import EffectAttemptStatus
from control_plane_kit_core.runtime_effect_observation import runtime_effect_request_for_intent
from control_plane_kit_core.runtime_effects import configuration_cleanup_result
from control_plane_kit_operations.effect_attempt_start import ExistingAttempt, NewlyStarted
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_attempt_fold import ExistingFold, FoldEffectAttempt, NewlyFolded
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_outcome_evidence import (
    ExecutionEffectOutcome, effect_outcome_failure, effect_outcome_transition,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from tests.configuration_cleanup_execution_fixture import ConfigurationCleanupExecutionFixture
from tests.test_execution_admission import Sequence
from tests.test_postgres_configuration_completion import _AfterWriteFailure
from tests.test_postgres_effect_attempt_start_eligibility_rollback import _CommitFailureConnection


class _CommitThenRaiseConnection:
    """Negative transport premise: durable commit succeeded before ack was lost."""
    def __init__(self, connection, fault):
        self.connection, self.fault = connection, fault

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        self.connection.commit()
        raise self.fault


class PostgresConfigurationCleanupTransactionTests(ConfigurationCleanupExecutionFixture, unittest.TestCase):
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
