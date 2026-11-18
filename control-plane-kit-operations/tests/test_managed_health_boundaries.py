"""Review regressions through the real managed application and recording ports."""
import asyncio
from unittest import mock

from control_plane_kit_core.operations import ControlPlaneServiceRole, EffectAttemptIdentity
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.coordinator import ExecutionCoordinatorDenied
from control_plane_kit_operations.delegation_signing_keys import DelegationSigningKeyNotFound
from control_plane_kit_operations.health_signing_authority import HealthSigningAuthorityUnavailable
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.runtime_authorities import RemoteDockerTlsAuthority
from tests.test_managed_application_chain import ManagedApplicationFixture, now


class ManagedHealthBoundaryTests(ManagedApplicationFixture):
    async def first_native_wait(self):
        await self.prepare_and_start()
        for _ in range(len(self.plan.activities) + 1):
            await self.execute_one()
            if self.health.native_reads:
                return
        self.fail("native read was not reached")

    def attempt_for(self, request):
        identity = EffectAttemptIdentity(request.source.run_id, request.activity_id.value, 1)
        with self.unit_of_work() as uow:
            return uow.stores.effect_attempts.get(identity)

    async def replay_last_execution(self):
        return await self.invoke("command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
            path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker,
            payload={"claim_generation": self.generation, "max_effects": 1,
                "idempotency_key": f"execute-{self.execution_number}"}, app=self.application())

    async def cancel_read(self, *, native):
        if native:
            await self.prepare_and_start()
        else:
            await self.first_native_wait()
        entered = asyncio.Event()
        requests = []

        async def held(realization, request, authority):
            self.assertEqual(self.tracker.active, 0)
            requests.append(request)
            entered.set()
            await asyncio.Event().wait()

        async def drive():
            for _ in range(len(self.plan.activities) + 1):
                await self.execute_one()
            self.fail("selected read was not entered")

        method = "observe_connection" if native else "observe_signed"
        with mock.patch.object(self.health, method, side_effect=held):
            task = asyncio.create_task(drive())
            waiter = asyncio.create_task(entered.wait())
            try:
                done, _ = await asyncio.wait((task, waiter), timeout=10,
                    return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    await task
                    self.fail("execution returned before its held read")
                self.assertIn(waiter, done)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                if not task.done():
                    task.cancel()
                waiter.cancel()
                await asyncio.gather(task, waiter, return_exceptions=True)
        self.assertEqual(len(requests), 1)
        attempt = self.attempt_for(requests[0])
        self.assertEqual(attempt.state.status.value, "uncertain")
        with self.unit_of_work() as uow:
            outcome = uow.stores.effect_outcomes.get(attempt.state.identity,
                attempt.latest_transition_event.event_id)
            self.assertEqual(outcome.outcome.status.value, "uncertain")
            receipt = uow.stores.execution.command_receipt_for_idempotency(
                self.run_id, f"execute-{self.execution_number}")
            self.assertEqual(receipt.status.value, "incomplete")
        before, effects = self.durable_snapshot(), self.effects()
        replay = await self.replay_last_execution()
        self.assertEqual(replay["coordinator_status"], "uncertain")
        self.assertEqual(self.durable_snapshot(), before)
        self.assertEqual(self.effects(), effects)

    async def test_cancellation_inside_native_read_retains_uncertainty_and_propagates(self):
        await self.cancel_read(native=True)

    async def test_cancellation_inside_signed_read_retains_uncertainty_and_propagates(self):
        await self.cancel_read(native=False)

    async def test_signed_response_after_lease_expiry_cannot_complete_attempt(self):
        await self.first_native_wait()
        original = self.health.observe_signed
        requests = []

        async def expire(realization, request, authority):
            result = await original(realization, request, authority)
            requests.append(request)
            self.connection.execute("UPDATE cpk_execution_requests SET lease_expires_at='2000-01-01T00:00:00Z' "
                "WHERE request_id=%s", (self.request_id,))
            return result

        before = self.rows("cpk_effect_attempt_outcomes")
        with mock.patch.object(self.health, "observe_signed", side_effect=expire):
            with self.assertRaises(HealthSigningAuthorityUnavailable):
                await self.execute_one()
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.attempt_for(requests[0]).state.status.value, "started")
        self.assertEqual(self.rows("cpk_effect_attempt_outcomes"), before)

    async def test_signed_response_after_key_revocation_cannot_complete_attempt(self):
        await self.first_native_wait()
        original = self.health.observe_signed
        requests = []

        async def revoke(realization, request, authority):
            result = await original(realization, request, authority)
            requests.append(request)
            key = self.keys["transit"]
            with self.unit_of_work() as uow:
                uow.stores.delegation_signing_keys.revoke("workspace-a", key.purpose, key.issuer, key.key_id,
                    revoked_by="operator-a", revoked_at=now())
                uow.commit()
            return result

        before = self.rows("cpk_effect_attempt_outcomes")
        with mock.patch.object(self.health, "observe_signed", side_effect=revoke):
            with self.assertRaises(DelegationSigningKeyNotFound):
                await self.execute_one()
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.attempt_for(requests[0]).state.status.value, "started")
        self.assertEqual(self.rows("cpk_effect_attempt_outcomes"), before)

    async def test_runtime_replacement_after_successor_commit_cannot_receive_admitted_read(self):
        await self.reach_native_wait()
        prior = self.native_attempt(1)
        calls = tuple(self.health.native_reads)
        exit_unit = PostgresUnitOfWork.__exit__
        replaced = []

        def replace_after_commit(unit, exc_type, exc, traceback):
            selected = (not replaced and exc_type is None
                and unit.stores.execution.command_receipt_for_idempotency(self.run_id, "runtime-swap") is not None)
            result = exit_unit(unit, exc_type, exc, traceback)
            if selected:
                replaced.append(True)
                reference = self.graph.runtimes["docker"].authority_ref
                with self.unit_of_work() as uow:
                    uow.stores.runtime_authorities.revoke("workspace-a", reference)
                    uow.stores.runtime_authorities.register(workspace_id="workspace-a", authority_ref=reference,
                        runtime_kind=RuntimeKind.DOCKER, authority=RemoteDockerTlsAuthority(
                            endpoint="tcp://replacement.example.test:2376",
                            ca_certificate=SecretReference("secret://replacement/ca"),
                            client_certificate=SecretReference("secret://replacement/cert"),
                            client_key=SecretReference("secret://replacement/key")),
                        admitted_by="operator-a", admitted_at=now())
                    uow.commit()
            return result

        with mock.patch.object(PostgresUnitOfWork, "__exit__", replace_after_commit):
            with self.assertRaises(ExecutionCoordinatorDenied):
                await self.reobserve(1, "runtime-swap")
        self.assertEqual(replaced, [True])
        self.assertEqual(tuple(self.health.native_reads), calls)
        self.assertEqual(self.native_attempt(1), prior)
        with self.unit_of_work() as uow:
            receipt = uow.stores.execution.command_receipt_for_idempotency(self.run_id, "runtime-swap")
            self.assertEqual(receipt.status.value, "incomplete")
            self.assertEqual(uow.stores.effect_attempts.get(receipt.managed_intent.successor).state.status.value,
                "started")
        before = self.durable_snapshot()
        replay = await self.reobserve(1, "runtime-swap", app=self.application())
        self.assertEqual(replay["coordinator_status"], "uncertain")
        self.assertEqual(self.durable_snapshot(), before)
        self.assertEqual(tuple(self.health.native_reads), calls)
