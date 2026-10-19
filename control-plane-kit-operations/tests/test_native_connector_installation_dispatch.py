"""#2009: consume accepted connector origin at the fresh native boundary.

Real PostgreSQL/admission/execution/advancement own all positive history.
Explicit negative seams run only after the real STARTED transaction commits.
"""
from contextlib import contextmanager
from dataclasses import replace
from unittest import mock

from control_plane_kit_core.operations import ControlPlaneServiceRole, EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import ObserveManagementBootstrap, ManagementBootstrapStage
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from control_plane_kit_operations.connector_installation import CurrentConnectorInstallation
from control_plane_kit_operations.coordinator import ExecutionCoordinatorDenied
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_outcome_evidence import NativeConnectionOutcome
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.test_current_connector_installation import CurrentConnectorInstallationFixture
from tests import test_managed_configuration_update_execution as content


class NativeConnectorInstallationDispatchTests(CurrentConnectorInstallationFixture):
    application_artifacts = (content.APPLICATION,)
    desired_update = content.ManagedConfigurationUpdateExecutionTests.desired_update
    prepare_content = content.ManagedConfigurationUpdateExecutionTests.prepare_content
    execute_content = content.ManagedConfigurationUpdateExecutionTests.execute_content

    @contextmanager
    def capture_ports(self):
        """Assert captured facts outside the coordinator's provider-error boundary."""
        native, signed = self.health.observe_connection, self.health.observe_signed
        calls = []

        async def capture(kind, delegate, realization, request, authority):
            ledger = _ACCOUNTING.get()
            calls.append((kind, realization, request, authority, self.tracker.active,
                ledger, ledger.active, ledger.used))
            return await delegate(realization, request, authority)

        async def native_probe(*args):
            return await capture("native", native, *args)

        async def signed_probe(*args):
            return await capture("signed", signed, *args)

        with mock.patch.object(self.health, "observe_connection", native_probe), \
                mock.patch.object(self.health, "observe_signed", signed_probe):
            yield calls

    @contextmanager
    def after_native_start(self, action):
        """Negative race seam; delegate genuine start, then alter only its input."""
        execute = EffectAttemptStartService.execute
        starts = []

        def start(service, command):
            result = execute(service, command)
            if (command.intent.source.run_id.value == self.update_run
                    and type(command.intent.operation) is ObserveManagementBootstrap
                    and command.intent.operation.stage is ManagementBootstrapStage.CONNECTOR_CONNECTED):
                starts.append((result.attempt, _ACCOUNTING.get()))
                action(result.attempt)
            return result

        with mock.patch.object(EffectAttemptStartService, "execute", start):
            yield starts

    def assert_attempt_status(self, attempt, status):
        with self.unit_of_work() as uow:
            current = uow.stores.effect_attempts.get(attempt.state.identity)
        self.assertEqual(current.state.status.value, status)

    async def test_update_carries_original_creation_but_fresh_observation_and_no_grants(self):
        with self.capture_ports() as calls:
            await self.prepare_content()
            origin = self.assert_original(self.read_installation())
            result = await self.execute_content()
        self.assertEqual(result["coordinator_status"], "completed")
        retained = []
        for kind, realization, request, authority, active_uows, ledger, active, used in calls:
            self.assertTrue(hasattr(realization, "connector_installation"),
                "realization is missing the accepted connector installation interface")
            self.assertEqual(active_uows, 0)
            self.assertFalse(active)
            self.assertEqual(ledger.owner, request.source.run_id.value)
            if kind != "native" or request.source.run_id.value != self.update_run:
                self.assertIsNone(realization.connector_installation)
                continue
            retained.append(realization)
            installation = realization.connector_installation
            self.assertIs(type(installation), CurrentConnectorInstallation)
            self.assertEqual(installation, origin)
            self.assertEqual(installation.original_request.secret_resolution_grants, ())
            self.assertNotEqual(installation.original_request.effect_id, request.effect_id)
            self.assertNotEqual(installation.original_request.source.run_id, request.source.run_id)
            self.assertEqual(realization.intent_event.event_id, request.effect_id)
            self.assertEqual(realization.activity.activity_id, request.activity_id)
            self.assertEqual(installation.current_graph_id, realization.plan_record.base_graph_id)
            self.assertEqual(installation.current_realized_projection_id,
                realization.plan_record.base_realized_projection_id)
            self.assertGreater(used.records, 0)
            self.assertNotIn("connector_installation=", repr(realization))
        self.assertEqual(len(retained), 1)
        realization, = retained
        # Foreign pins and malformed witnesses must fail at the public value boundary.
        for invalid in (object(), replace(origin, current_graph_id="foreign"),
                replace(origin, current_realized_projection_id="foreign")):
            with self.subTest(invalid_type=type(invalid).__name__):
                with self.assertRaises(InvalidOperationCommand):
                    replace(realization, connector_installation=invalid)
        signed = next(call[1] for call in calls if call[0] == "signed")
        with self.assertRaises(InvalidOperationCommand):
            replace(signed, connector_installation=origin)

    async def test_corrupt_proof_after_start_skips_provider_and_replay_keeps_uncertainty(self):
        await self.prepare_content()
        origin = self.assert_original(self.read_installation())
        revision = origin.current_revision
        preimage, = self.connection.execute(
            "SELECT installation_sources_preimage FROM cpk_configuration_acceptances "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (revision,)).fetchone()

        def corrupt(attempt):
            self.connection.execute("UPDATE cpk_configuration_acceptances "
                "SET installation_sources_preimage='{}' "
                "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (revision,))

        calls = tuple(self.health.native_reads)
        try:
            with self.after_native_start(corrupt) as starts:
                result = await self.execute_content()
            self.assertEqual(len(starts), 1)
            self.assertEqual(tuple(self.health.native_reads), calls,
                "unavailable accepted connector proof must prevent native I/O")
            self.assertEqual(result["coordinator_status"], "uncertain")
            self.assert_attempt_status(starts[0][0], "uncertain")
        finally:
            self.connection.execute("UPDATE cpk_configuration_acceptances "
                "SET installation_sources_preimage=%s "
                "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (preimage, revision))
        self.assertEqual(self.assert_original(self.read_installation()), origin)

    async def proof_failure(self, *, exceptional):
        await self.prepare_content()
        read = ConfigurationAcceptanceStore.read_current_connector_installation
        exit_unit = PostgresUnitOfWork.__exit__
        reads, exits = [], []
        charged = ConfigurationEvidenceFootprint(8192, 0, 0, 0)

        def observe(store, *args, **kwargs):
            ledger = _ACCOUNTING.get()
            reads.append((ledger, ledger.active, ledger.used))
            ledger.used = charged  # Exact exhausted shared-ledger negative seam.
            if exceptional:
                raise RuntimeError("injected proof read interruption")
            return read(store, *args, **kwargs)

        def leave(unit, *args):
            if reads:
                ledger = _ACCOUNTING.get()
                if ledger is reads[0][0]:
                    exits.append((ledger.active, ledger.used))
            return exit_unit(unit, *args)

        calls = tuple(self.health.native_reads)
        with self.after_native_start(lambda attempt: None) as starts, \
                mock.patch.object(ConfigurationAcceptanceStore, "read_current_connector_installation", observe), \
                mock.patch.object(PostgresUnitOfWork, "__exit__", leave):
            result = await self.execute_content()
        self.assertEqual(len(reads), 1, "native dispatch must acquire a fresh installation proof")
        self.assertEqual(len(starts), 1)
        self.assertIs(reads[0][0], starts[0][1])
        self.assertTrue(reads[0][1])
        self.assertGreater(reads[0][2].records, 0)
        self.assertTrue(exits)
        self.assertEqual(exits[0], (False, charged), "proof accounting must restore before UOW exit")
        self.assertEqual(tuple(self.health.native_reads), calls)
        self.assertEqual(result["coordinator_status"], "uncertain")
        self.assert_attempt_status(starts[0][0], "uncertain")

    async def test_exhausted_original_ledger_skips_provider_without_reset(self):
        await self.proof_failure(exceptional=False)

    async def test_exceptional_proof_read_restores_original_inactive_ledger(self):
        await self.proof_failure(exceptional=True)

    async def test_current_pin_drift_after_start_denies_and_keeps_started(self):
        await self.prepare_content()
        current = self.current()

        def drift(attempt):
            self.connection.execute("UPDATE cpk_workspaces SET current_graph_id=%s, "
                "current_realized_projection_id=%s WHERE workspace_id='workspace-a'",
                (self.workspace["current_graph_id"], self.workspace["current_realized_projection_id"]))

        calls = tuple(self.health.native_reads)
        try:
            with self.after_native_start(drift) as starts:
                with self.assertRaises(ExecutionCoordinatorDenied):
                    await self.execute_content()
            self.assertEqual(len(starts), 1)
            self.assertEqual(tuple(self.health.native_reads), calls)
            self.assert_attempt_status(starts[0][0], "started")
        finally:
            self.connection.execute("UPDATE cpk_workspaces SET current_graph_id=%s, "
                "current_realized_projection_id=%s WHERE workspace_id='workspace-a'",
                (current.current_graph_id, current.current_realized_projection_id))
        self.assert_original(self.read_installation())

    async def test_explicit_successors_reacquire_origin_with_distinct_observation_identity(self):
        await self.prepare_content()
        origin = self.assert_original(self.read_installation())
        self.health.responses[:] = [NativeConnectionOutcome.DISCONNECTED,
            NativeConnectionOutcome.UNKNOWN, NativeConnectionOutcome.CONNECTED]
        read = ConfigurationAcceptanceStore.read_current_connector_installation
        reads = []

        def observe(store, *args, **kwargs):
            value = read(store, *args, **kwargs)
            reads.append(value)
            return value

        native, = (activity for activity in self.record.plan.activities
            if type(activity.operation) is ObserveManagementBootstrap
            and activity.operation.stage is ManagementBootstrapStage.CONNECTOR_CONNECTED)
        with self.capture_ports() as calls, \
                mock.patch.object(ConfigurationAcceptanceStore, "read_current_connector_installation", observe):
            for number in range(1, len(self.record.plan.activities) + 3):
                result = await self.invoke("command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
                    path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
                    payload={"claim_generation": self.update_generation, "max_effects": 1,
                        "idempotency_key": f"wait-update-{number}"})
                if result["coordinator_status"] == "blocked":
                    break
            self.assertEqual(result["coordinator_status"], "blocked")
            for prior in (1, 2):
                payload = {"activity_id": native.activity_id.value, "prior_attempt": prior,
                    "claim_generation": self.update_generation, "idempotency_key": f"update-next-{prior}"}
                result = await self.invoke("command.deployment.reobserve-connector", ControlPlaneServiceRole.EXECUTION,
                    path={"workspace_id": "workspace-a", "run_id": self.update_run},
                    payload=payload, principal=self.worker)
                before = self.durable_snapshot(), self.effects(), len(reads)
                replay = await self.invoke("command.deployment.reobserve-connector", ControlPlaneServiceRole.EXECUTION,
                    path={"workspace_id": "workspace-a", "run_id": self.update_run},
                    payload=payload, principal=self.worker, app=self.application())
                self.assertEqual(replay, result)
                self.assertEqual((self.durable_snapshot(), self.effects(), len(reads)), before)
        self.assertEqual(len(reads), 3, "each explicit native read requires its own current proof")
        self.assertTrue(all(value.installation == origin for value in reads))
        native_calls = [call for call in calls if call[0] == "native"]
        self.assertEqual(len({call[2].effect_id for call in native_calls}), 3)
        self.assertEqual(len({id(call[1].connector_installation) for call in native_calls}), 3)
        for number, call in enumerate(native_calls, 1):
            self.assertEqual(call[1].connector_installation, origin)
            with self.unit_of_work() as uow:
                attempt = uow.stores.effect_attempts.get(EffectAttemptIdentity(
                    RunId(self.update_run), native.activity_id.value, number))
            self.assertEqual(attempt.original_start_event.event_id, call[2].effect_id)
