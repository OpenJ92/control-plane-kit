"""N5 PostgreSQL schedules; all are upstream-blocked until actual R2 succeeds."""

from unittest import mock

from control_plane_kit_operations.health_signing_authority import (
    HealthSigningAuthorityReloadService, HealthSigningAuthorityUnavailable,
)
from control_plane_kit_operations.delegation_signing_keys import DelegationSigningKeyNotFound
from control_plane_kit_operations.effect_run_prefix import _lock_effect_run_prefix
from control_plane_kit_operations.postgres import PostgresExecutionStore
from tests.lifecycle_lock_fixture import (
    LifecycleLockFixture, LIFECYCLE_LOCK, REQUEST_LOCK, RUN_LOCK, ATTEMPT_LOCK,
    SESSION_LOCK, WORKSPACE_LOCK,
)
from tests.receiver_health_execution_fixture import ReceiverHealthExecutionFixture


class ReceiverHealthLifecycleLockTests(LifecycleLockFixture, ReceiverHealthExecutionFixture):
    def assert_later_rows_free(self, preparation):
        identity = preparation.identity
        for query, arguments in ((REQUEST_LOCK, (self.request_id,)), (RUN_LOCK, (self.run_id,)),
                (ATTEMPT_LOCK, (identity.run_id.value, identity.activity_id, identity.attempt)),
                (SESSION_LOCK, (self.plan_descriptor["session_id"],)), (WORKSPACE_LOCK, ("workspace-a",))):
            self.assert_row_lockable(query, arguments)
        with self.unit_of_work() as uow:
            uow.stores.connection.execute("SET LOCAL lock_timeout='250ms'")
            self.assertIsNotNone(uow.stores.runtime_authorities.get_active_for_update(
                "workspace-a", self.start_command.start.intent.authority_ref))

    async def lifecycle_schedule(self, *, fold, revoke=False, corrupt=False):
        await self.prepare_health()
        preparation = self.start_service().execute_health(self.start_command).preparation
        # Positive precondition reaches current V2 authority before scheduling.
        self.assertEqual(self.reload_service().execute(self.reload_command()).preparation, preparation)
        command = self.health_fold(preparation) if fold else self.reload_command()
        execute = (lambda factory: self.fold(command, factory)) if fold else (
            lambda factory: self.reload_service(factory).execute(command))
        before = self.durable_snapshot()
        original_revision = None
        try:
            with self.blocked_command(LIFECYCLE_LOCK, ("receiver-lifecycle:workspace-a",), execute) as future:
                self.assert_later_rows_free(preparation)
                if revoke:
                    # Real supported key owner transition while the waiter owns
                    # no key rows. This is not a synthetic graph transition.
                    self.revoke_workload_key()
                if corrupt:
                    # Deliberate bounded pin corruption, not lawful selection
                    # under STARTED work. Restore its exact value in finally.
                    original_revision = self.connection.execute("SELECT desired_graph_revision FROM cpk_workspaces "
                        "WHERE workspace_id='workspace-a'").fetchone()[0]
                    self.connection.execute("UPDATE cpk_workspaces SET desired_graph_revision=desired_graph_revision+1 "
                        "WHERE workspace_id='workspace-a'")
            if revoke or corrupt:
                with self.assertRaises(DelegationSigningKeyNotFound if revoke else HealthSigningAuthorityUnavailable):
                    future.result(timeout=1)
                self.assertEqual(self.durable_snapshot(), before)
            else:
                result = future.result(timeout=1)
                if fold:
                    self.assertEqual(result.attempt.state.status.value, "succeeded")
                else:
                    self.assertEqual(result.preparation, preparation)
                    self.assertEqual(self.durable_snapshot(), before)
        finally:
            if original_revision is not None:
                self.connection.execute("UPDATE cpk_workspaces SET desired_graph_revision=%s "
                    "WHERE workspace_id='workspace-a'", (original_revision,))

    async def test_standalone_waits_on_lifecycle_before_later_rows_and_then_succeeds(self):
        await self.lifecycle_schedule(fold=False)

    async def test_nested_fold_waits_on_lifecycle_before_runtime_and_then_succeeds(self):
        await self.lifecycle_schedule(fold=True)

    async def test_standalone_rechecks_real_key_revocation_after_lifecycle_wait(self):
        await self.lifecycle_schedule(fold=False, revoke=True)

    async def test_nested_fold_rechecks_real_key_revocation_after_lifecycle_wait(self):
        await self.lifecycle_schedule(fold=True, revoke=True)

    async def test_standalone_refuses_injected_stale_pin_after_lifecycle_wait(self):
        await self.lifecycle_schedule(fold=False, corrupt=True)

    async def test_nested_fold_refuses_injected_stale_pin_after_lifecycle_wait(self):
        await self.lifecycle_schedule(fold=True, corrupt=True)

    async def test_bare_run_prefix_cannot_acquire_lifecycle_after_its_row_locks(self):
        await self.prepare_health()
        preparation = self.start_service().execute_health(self.start_command).preparation
        self.assertEqual(self.reload_service().execute(self.reload_command()).preparation, preparation)
        before = self.durable_snapshot()
        with self.unit_of_work() as uow:
            request = uow.stores.execution.get_request_for_update(self.request_id)
            prefix = _lock_effect_run_prefix(uow, request, self.run_id, latest_required=True)
            with mock.patch.object(type(uow.stores.graphs), "lock_receiver_lifecycle",
                    side_effect=AssertionError("nested reload took lifecycle late")) as lifecycle, \
                    self.forbid_current_health():
                with self.assertRaises(HealthSigningAuthorityUnavailable):
                    self.reload_service().in_unit_of_work(uow, self.reload_command(), run_prefix=prefix)
            self.assertEqual(lifecycle.call_count, 0)
        self.assertEqual(self.durable_snapshot(), before)

    async def test_actual_fold_prefix_rechecks_same_transaction_truth_without_new_locks(self):
        await self.prepare_health()
        preparation = self.start_service().execute_health(self.start_command).preparation
        command = self.health_fold(preparation)
        original = HealthSigningAuthorityReloadService.in_unit_of_work
        before = self.durable_snapshot()
        class ProbeComplete(RuntimeError):
            pass
        # Capture/reuse the actual caller's prefix; no test-only constructor or
        # fake permission boolean chooses the production representation.
        changes = (
            ("UPDATE cpk_workspaces SET desired_graph_revision=desired_graph_revision+1 WHERE workspace_id=%s", ("workspace-a",)),
            ("UPDATE cpk_execution_requests SET claim_generation=claim_generation+1 WHERE request_id=%s", (self.request_id,)),
            ("UPDATE cpk_activity_runs SET status='failed' WHERE run_id=%s", (self.run_id,)),
        )
        for sql, arguments in changes:
            reached = []
            def changed(service, uow, reload, **prepared):
                pair, observation = original(service, uow, reload, **prepared)
                self.assertEqual(pair.preparation, preparation)
                self.assertTrue(prepared, "fold must supply complete held-lock evidence")
                # Deliberate corruption under the caller transaction; every
                # case exits by exception and rolls back to the exact premise.
                self.assertEqual(uow.stores.connection.execute(sql, arguments).rowcount, 1)
                with self.forbid_current_health(), \
                        mock.patch.object(type(uow.stores.graphs), "lock_receiver_lifecycle",
                            side_effect=AssertionError("prefix reentry acquired lifecycle")) as lifecycle:
                    with self.assertRaises(HealthSigningAuthorityUnavailable):
                        original(service, uow, reload, **prepared)
                self.assertEqual(lifecycle.call_count, 0)
                reached.append(True)
                raise ProbeComplete()
            with self.subTest(statement=sql), \
                    mock.patch.object(HealthSigningAuthorityReloadService, "in_unit_of_work", changed):
                with self.assertRaises(ProbeComplete):
                    self.fold(command)
            self.assertEqual(reached, [True])
            self.assertEqual(self.durable_snapshot(), before)

    async def test_actual_fold_prefix_cannot_be_reused_after_its_transaction_ends(self):
        await self.prepare_health()
        preparation = self.start_service().execute_health(self.start_command).preparation
        original = HealthSigningAuthorityReloadService.in_unit_of_work
        captured = []
        class CapturedBeforeFold(RuntimeError):
            pass
        def capture(service, uow, command, **prepared):
            pair, _ = original(service, uow, command, **prepared)
            self.assertEqual(pair.preparation, preparation)
            captured.append((command, prepared))
            # Abort before the fold changes the attempt. A terminal attempt
            # would make refusal possible without checking prefix provenance.
            raise CapturedBeforeFold()
        with mock.patch.object(HealthSigningAuthorityReloadService, "in_unit_of_work", capture):
            with self.assertRaises(CapturedBeforeFold):
                self.fold(self.health_fold(preparation))
        self.assertEqual(len(captured), 1)
        self.assertEqual(self.reload_service().execute(self.reload_command()).preparation, preparation)
        command, prepared = captured[0]
        self.assertTrue(prepared)
        before = self.durable_snapshot()
        with self.unit_of_work() as uow:
            with self.forbid_current_health(), \
                    mock.patch.object(type(uow.stores.graphs), "lock_receiver_lifecycle",
                        side_effect=AssertionError("expired prefix reacquired lifecycle")) as lifecycle:
                with self.assertRaises(HealthSigningAuthorityUnavailable):
                    original(self.reload_service(), uow, command, **prepared)
            self.assertEqual(lifecycle.call_count, 0)
        self.assertEqual(self.durable_snapshot(), before)

    async def test_actual_prefix_from_another_live_transaction_refuses_before_late_locks(self):
        await self.prepare_health()
        preparation = self.start_service().execute_health(self.start_command).preparation
        original = HealthSigningAuthorityReloadService.in_unit_of_work
        reached = []
        before = self.durable_snapshot()
        class LivePrefixChecked(RuntimeError):
            pass
        def another_transaction(service, owner, command, **prepared):
            pair, _ = original(service, owner, command, **prepared)
            self.assertEqual(pair.preparation, preparation)
            self.assertTrue(prepared)
            with self.unit_of_work() as foreign:
                self.assertIsNot(owner, foreign)
                with self.forbid_current_health(), \
                        mock.patch.object(type(foreign.stores.graphs), "lock_receiver_lifecycle",
                            side_effect=AssertionError("foreign prefix acquired lifecycle")) as lifecycle, \
                        mock.patch.object(PostgresExecutionStore, "get_request_for_update",
                            side_effect=AssertionError("foreign prefix acquired request row")) as request:
                    with self.assertRaises(HealthSigningAuthorityUnavailable):
                        original(service, foreign, command, **prepared)
                self.assertEqual((lifecycle.call_count, request.call_count), (0, 0))
            reached.append(True)
            raise LivePrefixChecked()
        with mock.patch.object(HealthSigningAuthorityReloadService, "in_unit_of_work", another_transaction):
            with self.assertRaises(LivePrefixChecked):
                self.fold(self.health_fold(preparation))
        self.assertEqual(reached, [True])
        self.assertEqual(self.durable_snapshot(), before)
        self.assertEqual(self.reload_service().execute(self.reload_command()).preparation, preparation)
