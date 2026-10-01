"""#1896 outer signed-health fold owns every required run before attempt/runtime."""
from dataclasses import replace
import unittest
from unittest import mock

from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_core.planning import ObserveNodeHealth, compile_graph_activity_plan
from control_plane_kit_operations.effect_attempt_fold import FoldEffectAttempt, GuardedHealthEffectFold, ExistingFold
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_outcome_evidence import ExecutionEffectOutcome, effect_outcome_transition
from control_plane_kit_operations.health_signing_authority import (
    HealthSigningAuthorityReloadService, HealthSigningAuthorityUnavailable, _lock_health_prefix,
)
from control_plane_kit_operations.postgres import PostgresExecutionStore, PostgresUnitOfWork
from control_plane_kit_operations.postgres.runtime_authority_store import RuntimeAuthorityStore
from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority
from control_plane_kit_operations.runtime_management_targets import project_management_health_target
from tests.execution_lease_recovery_fixture import Sequence
from tests.draft_selection_fixture import _ObservedConnection
from tests.health_signing_authority_fixture import PostgresHealthSigningAuthorityFixture
from tests.lifecycle_lock_fixture import LifecycleLockFixture, RUN_LOCK, ATTEMPT_LOCK


class PostgresLifecycleHealthFoldLockTests(
    LifecycleLockFixture, PostgresHealthSigningAuthorityFixture, unittest.TestCase,
):
    def health_context(self, **options):
        _, selected, current, desired, _ = super().health_context(**options)
        graph = desired.graph
        desired = validate_graph(replace(graph, runtimes={**graph.runtimes,
            "docker": replace(graph.runtimes["docker"],
                authority_ref=RuntimeAuthorityReference("health-docker"))}))
        desired.require_valid()
        plan = compile_graph_activity_plan(current, desired)
        # A changed authority changes both activity ID and target graph digest.
        # Match the health purpose/scope, then retain the recompiled target.
        original = selected.operation
        selected, = (item for item in plan.activities
            if type(item.operation) is ObserveNodeHealth
            and item.operation.node_id == original.node_id
            and item.operation.provider_socket_name == original.provider_socket_name
            and item.operation.health_kind is original.health_kind
            and item.operation.transport is original.transport
            and item.operation.target.runtime_id == original.target.runtime_id
            and item.operation.target.graph_side is original.target.graph_side)
        self.assertNotEqual(selected.operation.target.graph_digest, original.target.graph_digest)
        projection = project_management_health_target(
            plan, selected.activity_id, selected.operation, current, desired)
        return plan, selected, current, desired, projection

    def health_start_value(self, **options):
        command = super().health_start_value(**options)
        graph = DEFAULT_GRAPH_CODEC.decode(self.projections["health-desired"].graph_descriptor)
        intent = replace(command.intent, authority_ref=graph.runtimes["docker"].authority_ref)
        return replace(command, intent=intent,
            transition=replace(command.transition, request_fingerprint=runtime_effect_intent_fingerprint(intent)))

    def setUp(self):
        super().setUp()
        with self.unit_of_work() as uow:
            self.runtime = uow.stores.runtime_authorities.register(
                workspace_id="workspace-a", authority_ref=self.start_value.intent.authority_ref,
                runtime_kind=self.start_value.intent.runtime_kind, authority=LocalDockerSocketAuthority(),
                admitted_by="operator-a", admitted_at="2026-08-01T11:00:00Z")
            self.intent = uow.stores.effect_attempt_intents.get(self.preparation.identity)
            uow.commit()

    def health_fold(self):
        outcome = ExecutionEffectOutcome(self.preparation.identity, self.intent.request_fingerprint,
            RuntimeEffectResult.succeeded(self.preparation.original_event_id))
        return GuardedHealthEffectFold(
            FoldEffectAttempt("request-a", effect_outcome_transition(outcome),
                self.start_value.authority, self.start_value.fence, None, outcome),
            self.reload_command().context, self.intent, self.preparation, self.runtime)

    def fold(self, uow, command):
        return EffectAttemptFoldService(uow, id_factory=Sequence("health-folded")).execute_health(
            command, signing_authority=HealthSigningAuthorityReloadService(uow,
                health_receiver_decoders=self.health_receiver_decoders()))

    def test_nested_reload_never_first_locks_distinct_run_after_attempt_or_runtime(self):
        self.seed_distinct_latest_run()
        identity = self.preparation.identity
        command, before = self.health_fold(), self.health_snapshot()
        with self.blocked_command(RUN_LOCK, ("run-later",), lambda uow: self.fold(uow, command)) as future:
            self.assert_row_lockable(ATTEMPT_LOCK,
                (identity.run_id.value, identity.activity_id, identity.attempt))
            with self.unit_of_work() as uow:
                # Actual current runtime owner must also remain available.
                uow.stores.connection.execute("SET LOCAL lock_timeout='250ms'")
                self.assertEqual(uow.stores.runtime_authorities.get_active_for_update(
                    "workspace-a", self.runtime.authority_ref), self.runtime)
        with self.assertRaises(HealthSigningAuthorityUnavailable):
            future.result(timeout=1)
        self.assert_history_unchanged(before)

    def test_terminal_health_replay_never_reads_latest_runtime_or_current_signing_authority(self):
        command = self.health_fold()
        result = self.fold(self.unit_of_work, command)
        self.seed_distinct_latest_run()
        before = self.health_snapshot()
        with mock.patch.object(PostgresExecutionStore, "get_latest_run_for_request_for_update",
                side_effect=AssertionError("terminal replay read latest run")), \
                mock.patch.object(PostgresExecutionStore, "get_latest_run_for_request",
                side_effect=AssertionError("terminal replay located latest run")), \
                mock.patch.object(PostgresExecutionStore, "observe_request_lease_for_update",
                side_effect=AssertionError("terminal replay sampled current clock")), \
                mock.patch.object(RuntimeAuthorityStore, "get_active_for_update",
                side_effect=AssertionError("terminal replay read current runtime")), \
                mock.patch.object(HealthSigningAuthorityReloadService, "in_unit_of_work",
                side_effect=AssertionError("terminal replay reloaded signing authority")):
            replay = self.fold(self.unit_of_work, command)
        self.assertEqual(replay, ExistingFold(result.attempt, result.outcome_record))
        self.assert_history_unchanged(before)

    def test_prepared_reload_rechecks_same_transaction_run_change_without_new_keys(self):
        command, before = self.reload_command(), self.health_snapshot()
        statements = []
        service = HealthSigningAuthorityReloadService(self.unit_of_work,
            health_receiver_decoders=self.health_receiver_decoders())
        with PostgresUnitOfWork(lambda: _ObservedConnection(self.lock_connection(), statements)) as uow:
            # Enter the actual complete health prefix: lifecycle must precede
            # request/run locks even for this caller-owned transaction.
            request = uow.stores.execution.get_request(command.request_id)
            prefix = _lock_health_prefix(uow, request, command.identity)
            accepted, _ = service.in_unit_of_work(uow, command, run_prefix=prefix)
            self.assertEqual(accepted.preparation, self.preparation)
            changed = uow.stores.execution.compare_and_set_run_status(
                command.identity.run_id.value, expected=ActivityRunStatus.RUNNING,
                replacement=ActivityRunStatus.FAILED)
            self.assertIs(changed.status, ActivityRunStatus.FAILED)
            self.assertIs(prefix.runs.requested_run.status, ActivityRunStatus.RUNNING)
            statements.clear()
            with self.forbid_fresh_health(), \
                    mock.patch.object(PostgresExecutionStore, "get_latest_run_for_request",
                        side_effect=AssertionError("prepared reload located a new latest run")), \
                    mock.patch.object(PostgresExecutionStore, "get_latest_run_for_request_for_update",
                        side_effect=AssertionError("prepared reload locked a new latest run")):
                with self.assertRaises(HealthSigningAuthorityUnavailable):
                    service.in_unit_of_work(uow, command, run_prefix=prefix)
            self.assertFalse(any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements))
            self.assertEqual(uow.stores.execution.get_run(changed.run_id), changed)
        # The caller's status change rolls back; reload grants no new authority
        # and changes no retained attempt/preparation/authorization history.
        self.assert_history_unchanged(before)
