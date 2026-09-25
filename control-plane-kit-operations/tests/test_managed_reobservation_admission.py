"""Public reobserve admission, interruption and PostgreSQL contention laws.

Async overlap holds the first external read after admission; the separate
threaded case observes actual database contention before releasing a row lock.
No attempts, outcomes, receipts or successful creation events are seeded.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
import threading
from unittest import mock

import psycopg

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import ManagementBootstrapStage, ObserveManagementBootstrap
from control_plane_kit_operations import effect_outcome_evidence
from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict, ExecutionCoordinatorDenied
from control_plane_kit_operations.effect_outcome_evidence import NativeConnectionOutcome
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_core.operations import ControlPlaneServiceRole
from tests.test_managed_application_chain import ManagedApplicationFixture
from tests.test_ingress_realization import TrackingUnitOfWorkFactory


class InterruptedAdmission(BaseException):
    """Worker interruption, outside normal provider-exception classification."""


class ConcurrentTrackingUnitOfWorkFactory(TrackingUnitOfWorkFactory):
    """Keep the existing effect-outside-UoW check local to each competing caller."""

    def __init__(self, database_url):
        self._local = threading.local()
        self._pid_lock = threading.Lock()
        self._pids = {}
        super().__init__(database_url)

    @property
    def active(self):
        return getattr(self._local, "active", 0)

    @active.setter
    def active(self, value):
        self._local.active = value

    def _connect(self):
        connection = super()._connect()
        connection.execute("SET lock_timeout = '10s'")
        connection.execute("SET statement_timeout = '12s'")
        with self._pid_lock:
            self._pids[threading.current_thread().name] = connection.info.backend_pid
        return connection

    def worker_pids(self):
        with self._pid_lock:
            return {name: pid for name, pid in self._pids.items() if name.startswith("managed-next")}


class ManagedReobservationAdmissionTests(ManagedApplicationFixture):
    async def first_native_wait_before_path(self):
        await self.prepare_and_start()
        native, = (activity for activity in self.plan.activities
            if activity.activity_id.value == self.native_id)
        path, = (activity for activity in self.plan.activities
            if type(activity.operation) is ObserveManagementBootstrap
            and activity.operation.stage is ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)
        # Fixture prerequisite, checked before any external effects: these are
        # independent siblings and this authored graph must schedule native first.
        # Do not alter dependency edges or seed history to reach this scenario.
        self.assertEqual(native.dependencies, path.dependencies)
        self.assertLess(self.plan.activities.index(native), self.plan.activities.index(path),
            "authored fixture must schedule native before PATH for this overlap law")
        self.assertEqual(self.effects(), ((), (), (), (), ()))
        for _ in range(len(self.plan.activities) + 2):
            result = await self.execute_one()
            if self.health.native_reads:
                break
            self.assertEqual(result["coordinator_status"], "progressed", result)
        self.assertEqual(len(self.health.native_reads), 1)
        self.assertEqual(self.native_attempt(1)[0].state.status.value, "not_ready")
        identity = EffectAttemptIdentity(RunId(self.run_id), path.activity_id.value, 1)
        with self.unit_of_work() as uow:
            with self.assertRaises(KeyError):
                uow.stores.effect_attempts.get(identity)
        return identity

    async def assert_competing_path_blocks_native_successor(self, key):
        before, effects = self.durable_snapshot(), self.effects()
        predecessor = self.native_attempt(1)
        with self.assertRaises(ExecutionCoordinatorConflict):
            await self.reobserve(1, key, app=self.application())
        self.assertEqual((self.durable_snapshot(), self.effects()), (before, effects))
        self.assertEqual(self.native_attempt(1), predecessor)
        self.assertEqual(len(self.health.native_reads), 1)
        with self.unit_of_work() as uow:
            self.assertIsNone(uow.stores.execution.command_receipt_for_idempotency(self.run_id, key))
            with self.assertRaises(KeyError):
                uow.stores.effect_attempts.get(
                    EffectAttemptIdentity(RunId(self.run_id), self.native_id, 2))

    async def test_awaiting_signed_path_prevents_native_successor_admission(self):
        path_identity = await self.first_native_wait_before_path()
        entered, release = asyncio.Event(), asyncio.Event()
        entries = []
        observe = self.health.observe_signed

        async def held(realization, request, authority):
            if request.activity_id.value == path_identity.activity_id:
                self.assertEqual(self.tracker.active, 0)
                entries.append(request)
                entered.set()
                await release.wait()
            return await observe(realization, request, authority)

        async def drive_to_path():
            for _ in range(len(self.plan.activities) + 2):
                result = await self.execute_one()
                if entries:
                    return result
                self.assertEqual(result["coordinator_status"], "progressed", result)
            self.fail("ordinary execution did not reach the independent signed PATH")

        with mock.patch.object(self.health, "observe_signed", side_effect=held):
            task = asyncio.create_task(drive_to_path())
            waiter = asyncio.create_task(entered.wait())
            try:
                done, _ = await asyncio.wait((task, waiter), timeout=10,
                    return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    await task
                    self.fail("signed PATH returned without awaiting the held port")
                self.assertIn(waiter, done, "signed PATH did not reach the external port")
                with self.unit_of_work() as uow:
                    self.assertEqual(uow.stores.effect_attempts.get(path_identity).state.status.value, "started")
                await self.assert_competing_path_blocks_native_successor("path-in-flight-next")
                self.assertEqual(len(entries), 1)
            finally:
                release.set()
                if not waiter.done():
                    waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
                await asyncio.wait_for(task, timeout=10)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.effect_attempts.get(path_identity).state.status.value, "succeeded")

    async def test_uncertain_signed_path_prevents_native_successor_admission(self):
        path_identity = await self.first_native_wait_before_path()
        entries = []
        observe = self.health.observe_signed

        async def interrupted(realization, request, authority):
            if request.activity_id.value == path_identity.activity_id:
                self.assertEqual(self.tracker.active, 0)
                entries.append(request)
                raise RuntimeError("recording signed PATH transport interrupted")
            return await observe(realization, request, authority)

        with mock.patch.object(self.health, "observe_signed", side_effect=interrupted):
            for _ in range(len(self.plan.activities) + 2):
                result = await self.execute_one()
                if entries:
                    break
                self.assertEqual(result["coordinator_status"], "progressed", result)
        self.assertEqual(len(entries), 1)
        self.assertEqual(result["coordinator_status"], "uncertain")
        with self.unit_of_work() as uow:
            path_attempt = uow.stores.effect_attempts.get(path_identity)
            self.assertEqual(path_attempt.state.status.value, "uncertain")
            outcome = uow.stores.effect_outcomes.get(path_identity, path_attempt.latest_transition_event.event_id)
            self.assertEqual(outcome.attempt, path_attempt)
        await self.assert_competing_path_blocks_native_successor("path-uncertain-next")
        self.assertEqual(len(entries), 1)

    @asynccontextmanager
    async def held_successor_read(self, key):
        entered, release = asyncio.Event(), asyncio.Event()
        entries = []
        observe = self.health.observe_connection

        async def held(realization, request, authority):
            self.assertEqual(self.tracker.active, 0)
            entries.append(request)
            entered.set()
            await release.wait()
            return await observe(realization, request, authority)

        with mock.patch.object(self.health, "observe_connection", side_effect=held):
            task = asyncio.create_task(self.reobserve(1, key, app=self.application()))
            waiter = asyncio.create_task(entered.wait())
            try:
                done, _ = await asyncio.wait((task, waiter), timeout=10,
                    return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    await task
                    self.fail("explicit successor returned without awaiting the held read")
                self.assertIn(waiter, done, "explicit successor did not reach the external read")
                yield task, release, entries
            finally:
                release.set()
                if not waiter.done():
                    waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
                if not task.done():
                    await asyncio.wait_for(task, timeout=10)

    def receipt(self, key):
        with self.unit_of_work() as uow:
            result = uow.stores.execution.command_receipt_for_idempotency(self.run_id, key)
        self.assertIsNotNone(result)
        return result

    def assert_one_successor(self):
        attempts = self.connection.execute(
            "SELECT attempt FROM cpk_effect_attempts WHERE run_id=%s AND activity_id=%s ORDER BY attempt",
            (self.run_id, self.native_id)).fetchall()
        self.assertEqual(attempts, [(1,), (2,)])

    def admission_commit_fault(self, key, *, after_commit):
        commit = PostgresUnitOfWork.commit
        exit_unit = PostgresUnitOfWork.__exit__
        fired = []
        pending = []

        def interrupt(unit):
            receipt = unit.stores.execution.command_receipt_for_idempotency(self.run_id, key)
            if not fired and not pending and receipt is not None and receipt.status.value == "incomplete":
                # Deliberately do not require successor presence to trigger: a
                # split receipt-before-start commit must fail the atomicity law.
                if after_commit:
                    commit(unit)
                    pending.append((unit, receipt))
                    return
                fired.append(receipt)
                raise InterruptedAdmission("before commit")
            return commit(unit)

        def lose_acknowledgement(unit, exc_type, exc, traceback):
            selected = pending and pending[0][0] is unit
            # commit() only requests commit. The real database commit and close
            # happen here; interrupting earlier would merely test rollback.
            result = exit_unit(unit, exc_type, exc, traceback)
            if selected and exc_type is None:
                fired.append(pending[0][1])
                raise InterruptedAdmission("lost acknowledgement after database commit")
            return result

        @contextmanager
        def fault():
            with mock.patch.object(PostgresUnitOfWork, "commit", interrupt), \
                    mock.patch.object(PostgresUnitOfWork, "__exit__", lose_acknowledgement):
                yield

        return fault(), fired

    def successor_admission(self):
        identity = EffectAttemptIdentity(RunId(self.run_id), self.native_id, 2)
        with self.unit_of_work() as uow:
            attempt = uow.stores.effect_attempts.get(identity)
            intent = uow.stores.effect_attempt_intents.get(identity)
        self.assertEqual(attempt.state.status.value, "started")
        self.assertEqual(attempt.state.prior_attempt,
            EffectAttemptIdentity(RunId(self.run_id), self.native_id, 1))
        self.assertEqual(attempt.original_start_event.kind.value, "step_observation_restarted")
        self.assertEqual(attempt.latest_transition_event, attempt.original_start_event)
        self.assertEqual(intent.original_start_event, attempt.original_start_event)
        self.assertEqual(intent.identity, attempt.state.identity)
        self.assertEqual(intent.request_fingerprint, attempt.state.request_fingerprint)
        return attempt, intent

    async def test_overlapping_different_keys_create_one_successor_and_one_read(self):
        await self.reach_native_wait()
        predecessor = self.native_attempt(1)
        async with self.held_successor_read("winning-next") as (first, release, entries):
            self.assert_one_successor()
            self.assertEqual(self.receipt("winning-next").status.value, "incomplete")
            before = self.durable_snapshot()
            with self.assertRaises(ExecutionCoordinatorConflict):
                await asyncio.wait_for(self.reobserve(1, "competing-next", app=self.application()), timeout=10)
            self.assertEqual(self.durable_snapshot(), before)
            self.assertEqual(len(entries), 1)
            self.assertEqual(len(self.health.native_reads), 1)
            self.assertEqual(self.native_attempt(1), predecessor)
            release.set()
            completed = await asyncio.wait_for(first, timeout=10)
        self.assert_one_successor()
        self.assertEqual(len(self.health.native_reads), 2)
        self.assertEqual(self.native_attempt(1), predecessor)
        successor, _ = self.native_attempt(2)
        self.assertEqual(successor.state.status.value, "not_ready")
        self.assertEqual(successor.state.prior_attempt, predecessor[0].state.identity)
        before, effects = self.durable_snapshot(), self.effects()
        self.assertEqual(await self.reobserve(1, "winning-next", app=self.application()), completed)
        self.assertEqual((self.durable_snapshot(), self.effects()), (before, effects))

    async def test_overlapping_same_key_reports_incomplete_then_replays_exact_completion(self):
        await self.reach_native_wait()
        predecessor = self.native_attempt(1)
        async with self.held_successor_read("same-next") as (first, release, entries):
            self.assert_one_successor()
            self.assertEqual(self.receipt("same-next").status.value, "incomplete")
            before, effects = self.durable_snapshot(), self.effects()
            replay = await asyncio.wait_for(self.reobserve(1, "same-next", app=self.application()), timeout=10)
            self.assertEqual(replay["coordinator_status"], "uncertain")
            self.assertEqual((self.durable_snapshot(), self.effects()), (before, effects))
            self.assertEqual(len(entries), 1)
            self.assertEqual(self.native_attempt(1), predecessor)
            release.set()
            completed = await asyncio.wait_for(first, timeout=10)
        self.assertEqual(self.receipt("same-next").status.value, "completed")
        self.assert_one_successor()
        self.assertEqual(len(self.health.native_reads), 2)
        before, effects = self.durable_snapshot(), self.effects()
        self.assertEqual(await self.reobserve(1, "same-next", app=self.application()), completed)
        self.assertEqual((self.durable_snapshot(), self.effects()), (before, effects))

    async def test_distinct_keys_contend_on_postgres_and_admit_only_one_successor(self):
        await self.reach_native_wait()
        predecessor = self.native_attempt(1)
        before_effects = self.effects()
        receipt_count = len(self.rows("cpk_execution_command_receipts"))
        self.tracker = ConcurrentTrackingUnitOfWorkFactory(self.tracker.database_url)
        self.unit_of_work = self.tracker
        id_lock = threading.Lock()

        def synchronized(factory):
            def allocate():
                with id_lock:
                    return factory()
            return allocate

        self.ids = {name: synchronized(factory) for name, factory in self.ids.items()}
        applications = (self.application(), self.application())
        keys = ("contending-next-a", "contending-next-b")
        loop = asyncio.get_running_loop()
        blocker = psycopg.connect(self.tracker.database_url)
        pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="managed-next")
        futures = []

        def invoke(index):
            return asyncio.run(self.reobserve(1, keys[index], app=applications[index]))

        try:
            row = blocker.execute(
                "SELECT request_id FROM cpk_execution_requests WHERE request_id=%s FOR UPDATE",
                (self.request_id,)).fetchone()
            self.assertEqual(row, (self.request_id,))
            futures = [loop.run_in_executor(pool, invoke, index) for index in range(2)]
            deadline = loop.time() + 5
            blocked = set()
            while loop.time() < deadline:
                blocked = set()
                for name, pid in self.tracker.worker_pids().items():
                    # A tuple-lock waiter may be behind the first waiter rather
                    # than list the controlling transaction as its direct blocker.
                    reaches_control = self.connection.execute("""
                        WITH RECURSIVE blockers(pid) AS (
                          SELECT unnest(pg_blocking_pids(%s))
                          UNION
                          SELECT unnest(pg_blocking_pids(blockers.pid)) FROM blockers
                        )
                        SELECT EXISTS (SELECT 1 FROM blockers WHERE pid=%s)
                        """, (pid, blocker.info.backend_pid)).fetchone()[0]
                    if reaches_control:
                        blocked.add(name)
                if len(blocked) == 2:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(len(blocked), 2, "both public calls must actually wait behind the request-row lock")
            self.assertEqual(len(self.health.native_reads), 1)
            blocker.commit()
            results = await asyncio.wait_for(asyncio.gather(*futures, return_exceptions=True), timeout=20)
        finally:
            blocker.rollback()
            blocker.close()
            try:
                if futures:
                    await asyncio.wait_for(asyncio.gather(*futures, return_exceptions=True), timeout=20)
            finally:
                pool.shutdown(wait=True)
        successes = [index for index, result in enumerate(results) if type(result) is dict]
        conflicts = [index for index, result in enumerate(results) if type(result) is ExecutionCoordinatorConflict]
        self.assertEqual((len(successes), len(conflicts)), (1, 1), results)
        self.assert_one_successor()
        self.assertEqual(len(self.health.native_reads), 2)
        self.assertEqual(self.native_attempt(1), predecessor)
        self.assertEqual(self.effects()[:3], before_effects[:3])
        self.assertEqual(self.effects()[4], before_effects[4])
        self.assertEqual(len(self.rows("cpk_execution_command_receipts")), receipt_count + 1)
        self.assertEqual(self.receipt(keys[successes[0]]).status.value, "completed")
        with self.unit_of_work() as uow:
            self.assertIsNone(uow.stores.execution.command_receipt_for_idempotency(
                self.run_id, keys[conflicts[0]]))

    async def test_lost_admission_commit_acknowledgement_never_dispatches_on_replay(self):
        await self.reach_native_wait()
        predecessor = self.native_attempt(1)
        effects = self.effects()
        patch, fired = self.admission_commit_fault("lost-ack-next", after_commit=True)
        with patch, self.assertRaises(InterruptedAdmission):
            await self.reobserve(1, "lost-ack-next")
        self.assertEqual(len(fired), 1)
        self.assertEqual(self.effects(), effects)
        self.assert_one_successor()
        admission = self.successor_admission()
        receipt = self.receipt("lost-ack-next")
        self.assertEqual(receipt.status.value, "incomplete")
        self.assertEqual(receipt, fired[0])
        self.assertEqual(self.native_attempt(1), predecessor)
        before = self.durable_snapshot()
        replay = await self.reobserve(1, "lost-ack-next", app=self.application())
        self.assertEqual(replay["coordinator_status"], "uncertain")
        self.assertEqual((self.durable_snapshot(), self.effects()), (before, effects))
        self.assertEqual(self.successor_admission(), admission)
        self.assertEqual(self.receipt("lost-ack-next"), receipt)

    async def test_precommit_interruption_rolls_back_receipt_start_and_intent_together(self):
        await self.reach_native_wait()
        predecessor = self.native_attempt(1)
        before, effects = self.durable_snapshot(), self.effects()
        patch, fired = self.admission_commit_fault("rollback-next", after_commit=False)
        with patch, self.assertRaises(InterruptedAdmission):
            await self.reobserve(1, "rollback-next")
        self.assertEqual(len(fired), 1)
        self.assertEqual((self.durable_snapshot(), self.effects()), (before, effects))
        with self.unit_of_work() as uow:
            self.assertIsNone(uow.stores.execution.command_receipt_for_idempotency(self.run_id, "rollback-next"))
        # An authoritative rollback left no admission to replay. A fresh public
        # invocation may now create exactly one successor, using the same key.
        await self.reobserve(1, "rollback-next", app=self.application())
        self.assert_one_successor()
        self.assertEqual(len(self.health.native_reads), 2)
        self.assertEqual(self.native_attempt(1), predecessor)

    async def first_native_result(self, status):
        await self.prepare_and_start()
        if status == "succeeded":
            self.health.responses = [NativeConnectionOutcome.CONNECTED]
        refusal = None
        if status == "unsupported":
            refusal = getattr(effect_outcome_evidence, "NativeConnectionRefused", None)
            self.assertIsNotNone(refusal, "closed native refusal marker is missing")
        observe = self.health.observe_connection

        async def respond(realization, request, authority):
            if status == "succeeded":
                return await observe(realization, request, authority)
            self.assertEqual(self.tracker.active, 0)
            self.assertEqual(request.effect_id, realization.intent_event.event_id)
            self.health.native_reads.append(request)
            if status == "uncertain":
                raise RuntimeError("recording native transport interrupted")
            return refusal()

        with mock.patch.object(self.health, "observe_connection", side_effect=respond):
            for _ in range(len(self.plan.activities) + 2):
                result = await self.execute_one()
                if self.health.native_reads:
                    break
                self.assertEqual(result["coordinator_status"], "progressed", result)
        self.assertEqual(len(self.health.native_reads), 1)
        attempt, outcome = self.native_attempt(1)
        self.assertEqual(attempt.state.status.value, status)
        self.assertEqual(outcome.attempt, attempt)
        return attempt

    async def assert_predecessor_refused(self, activity_id, error_type):
        before, effects = self.durable_snapshot(), self.effects()
        with self.assertRaises(error_type):
            await self.invoke("command.deployment.reobserve-connector", ControlPlaneServiceRole.EXECUTION,
                path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker,
                payload={"activity_id": activity_id, "prior_attempt": 1,
                    "claim_generation": self.generation, "idempotency_key": "invalid-predecessor"},
                app=self.application())
        self.assertEqual((self.durable_snapshot(), self.effects()), (before, effects))

    async def test_successful_native_predecessor_cannot_authorize_another_read(self):
        await self.first_native_result("succeeded")
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.get_run(self.run_id).status.value, "running")
        await self.assert_predecessor_refused(self.native_id, ExecutionCoordinatorConflict)

    async def test_uncertain_native_predecessor_cannot_authorize_another_read(self):
        await self.first_native_result("uncertain")
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.get_run(self.run_id).status.value, "running")
        await self.assert_predecessor_refused(self.native_id, ExecutionCoordinatorConflict)

    async def test_unsupported_native_predecessor_cannot_authorize_another_read(self):
        await self.first_native_result("unsupported")
        with self.unit_of_work() as uow:
            request = uow.stores.execution.get_request(self.request_id)
        claim_held = (request.status.value == "claimed" and request.claim is not None
            and request.claim.fence.worker_id == self.worker.identity.subject_id
            and request.claim.fence.generation == self.generation)
        # Ordinary unsupported settlement may remove the claim. Current
        # authority is checked before inspecting predecessor eligibility.
        await self.assert_predecessor_refused(self.native_id,
            ExecutionCoordinatorConflict if claim_held else ExecutionCoordinatorDenied)

    async def test_successful_signed_path_is_not_a_native_predecessor(self):
        await self.reach_native_wait()
        path, = (activity for activity in self.plan.activities
            if type(activity.operation) is ObserveManagementBootstrap
            and activity.operation.stage is ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)
        with self.unit_of_work() as uow:
            attempt = uow.stores.effect_attempts.get(
                EffectAttemptIdentity(RunId(self.run_id), path.activity_id.value, 1))
            self.assertEqual(attempt.state.status.value, "succeeded")
            self.assertEqual(uow.stores.execution.get_run(self.run_id).status.value, "running")
        await self.assert_predecessor_refused(path.activity_id.value, ExecutionCoordinatorConflict)
