"""Real owning-store admission, result retention and bounded history laws."""

from dataclasses import replace
import json
from unittest import mock

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.planning import AllocatePublicIngress, derive_schedule, project_activity_journal
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.coordinator import (
    ActivityExecutionDispatcher, ExecuteActivityRun, ExecuteManagedActivityRun, ExecutionCoordinator,
)
from control_plane_kit_operations.ingress_authorities import IngressAuthorityProviderKind
from control_plane_kit_operations.ingress_realization import IngressProviderRegistration, IngressRealizationAdapter
from control_plane_kit_operations.ingress_invocation_service import IngressInvocationService
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.execution_invocations import ExecutionInvocationControlError
from control_plane_kit_operations.ingress_invocations import (
    IngressInvocationContextRecord, validate_ingress_result, MAX_CANONICAL_INTEGER,
)
from control_plane_kit_operations.execution_invocations import ExecutionInvocationKind as Kind, ExecutionInvocationOutcome as Outcome
from control_plane_kit_operations.read_pages import ReadCollection, ReadPageRequest, RunReadScope
from tests.ingress_invocation_fixture import StagedIngressPort
from tests.test_postgres_coordinator_permissions import _CoordinatorManagementPermissionFixture, _CurrentCoordinatorPermission
from tests.test_managed_application_chain import now
from tests import test_managed_application_chain as managed
from tests import test_postgres_configuration_transfer_producer_receiver as profiled


class PostgresNativeIngressAcceptanceTests(_CurrentCoordinatorPermission, managed.ManagedApplicationFixture):
    """Actual native allocation evidence must satisfy the existing acceptance owner."""

    def setUp(self):
        super().setUp()
        self.runtime = profiled.ProfiledRecordingRuntime(self)
        self.ingress = StagedIngressPort(self)
        self.health.responses = [managed.NativeConnectionOutcome.CONNECTED]

    async def allocate_native(self):
        factory = self.unit_of_work
        fold = managed.EffectAttemptFoldService(factory, id_factory=self.ids["effect-fold"])
        owner = ExecutionCoordinator(factory,
            lifecycle=managed.RunLifecycleCommandService(factory, clock=now, id_factory=self.ids["lifecycle"]),
            adapter=ActivityExecutionDispatcher(runtime=self.runtime,
                ingress=IngressRealizationAdapter(factory,
                    {IngressAuthorityProviderKind.CLOUDFLARE: IngressProviderRegistration(
                        self.ingress, "cpk.ingress-invocations.v1")}, now,
                    managed.SecretUseAuthorizationService(factory), invocation_service=IngressInvocationService(
                        factory, permission_policy=self.policy, clock=now))),
            start_service=managed.EffectAttemptStartService(factory,
                id_factory=self.ids["effect-start"], permission_policy=self.policy), fold_service=fold,
            reconciliation_service=managed.EffectAttemptReconciliationService(
                factory, managed.ForbiddenRecovery(), fold),
            clock=now, id_factory=self.ids["execution"], permission_policy=self.policy,
            managed_health=self.health, health_signing_authority=managed.HealthSigningAuthorityReloadService(
                factory, health_receiver_decoders=self.registry))
        with factory() as uow:
            request = uow.stores.execution.get_request(self.request_id)
        execution = ExecuteActivityRun(self.run_id,
            ExecutionWorkerAuthority(request.claim.worker_id, self.original.granted_scopes),
            request.claim.fence, IdempotencyKey("native-ingress-acceptance"))
        return (await owner.execute_managed(ExecuteManagedActivityRun(execution, self.original))).descriptor()

    async def test_native_allocation_reaches_current_graph_acceptance_with_original_evidence(self):
        await self.prepare_and_start()
        self.configure_permissions(self.worker.identity, installation="native-ingress-acceptance")
        for _ in range(len(self.plan.activities) + 2):
            journal = project_activity_journal(self.plan, activity_journal_events(self.history()))
            ready = derive_schedule(self.plan, journal.state).ready
            if ready and type(ready[0].operation) is AllocatePublicIngress:
                result = await self.allocate_native()
            else:
                result = await self.execute_one()
            if result["coordinator_status"] == "completed":
                break
            self.assertEqual(result["coordinator_status"], "progressed", result)
        self.assertEqual(result["run_status"], "succeeded")
        self.assertEqual(self.ingress.calls, ["secret-resolution", "ingress-provider-create", "ingress-custody-write"])
        plan = self.plan_descriptor
        await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker,
            payload={"plan_id": self.plan_id, "expected_current_graph_id": self.workspace["current_graph_id"],
                "expected_current_realized_projection_id": self.workspace["current_realized_projection_id"],
                "desired_graph_id": plan["desired_graph_id"],
                "desired_realized_projection_id": plan["desired_realized_projection_id"],
                "expected_desired_graph_revision": plan["desired_graph_revision"],
                "claim_generation": self.generation, "idempotency_key": "native-ingress-advance"})
        current = await self.invoke("read.current-graph", ControlPlaneServiceRole.READS,
            path={"workspace_id": "workspace-a"})
        self.assertEqual(current["graph_id"], plan["desired_graph_id"])
        preimage = self.connection.execute("SELECT installation_sources_preimage FROM cpk_configuration_acceptances "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (plan["desired_graph_revision"],)).fetchone()[0]
        binding, = json.loads(preimage)["bindings"]
        allocation, = binding["generated_allocations"]
        with self.unit_of_work() as uow:
            generated, = uow.stores.generated_ingress_secrets.list_for_workspace("workspace-a")
            start = uow.stores.execution.get_event(allocation["source_event_id"])
            terminal = uow.stores.execution.get_event(allocation["terminal_event_id"])
        self.assertEqual(binding["node_id"], "connector")
        self.assertEqual(start.event_id, generated.source_event_id)
        self.assertEqual(start.run_id, self.run_id)
        self.assertEqual(terminal.run_id, self.run_id)
        self.assertEqual(start.activity_id, generated.source_activity_id)
        self.assertIs(start.kind, ActivityEventKind.STEP_STARTED)
        self.assertIs(terminal.kind, ActivityEventKind.STEP_SUCCEEDED)
        self.assertEqual(start.activity_id, terminal.activity_id)
        self.assertIs(terminal.evidence.descriptor()["details"]["connector_material_recorded"], True)


class PostgresIngressInvocationTests(_CoordinatorManagementPermissionFixture):
    async def prepared_ingress(self):
        _, command, _ = await self.prepared(legacy=True)
        self.ingress = StagedIngressPort(self)
        self.ingress_service = IngressInvocationService(self.unit_of_work, permission_policy=self.policy, clock=now)
        return self.permission_coordinator(ingress_invocations=self.ingress_service), command

    def ingress_history(self):
        with self.unit_of_work().run_events_snapshot() as snapshot:
            page = snapshot.run_events(ReadPageRequest(ReadCollection.RUN_EVENTS,
                RunReadScope("workspace-a", run_id=self.run_id), 100))
            return next(event["ingress"] for event in page.items if "ingress" in event)

    async def test_unqualified_keyword_wrapper_cannot_enter_before_admission(self):
        _, command = await self.prepared_ingress()
        entered = []
        class Unqualified:
            def create(self, *args, **kwargs):
                entered.append("create")
            def teardown(self, **kwargs):
                entered.append("teardown")
        self.ingress = Unqualified()
        owner = self.permission_coordinator(ingress_invocations=self.ingress_service, qualify_ingress=False)
        result = await owner.execute_managed(command)
        self.assertEqual(result.status.value, "unsupported")
        self.assertEqual(entered, [])
        self.assertEqual(len(self.rows("cpk_ingress_invocation_contexts")), 0)
        self.assertEqual(len(self.rows("cpk_secret_use_authorizations")), 0)

    async def test_success_retains_three_units_and_two_results_without_replay(self):
        owner, command = await self.prepared_ingress()
        result = await owner.execute_managed(command)
        self.assertEqual(result.status.value, "progressed")
        self.assertEqual(self.ingress.calls, ["secret-resolution", "ingress-provider-create", "ingress-custody-write"])
        self.assertEqual(len(self.rows("cpk_ingress_invocation_results")), 2)
        history = self.ingress_history()
        self.assertEqual(history["owner_fold"], "active")
        terminal, = (event for event in self.history() if event.activity_id == self.health_activity.activity_id.value
            and event.kind is ActivityEventKind.STEP_SUCCEEDED)
        self.assertEqual(terminal.run_id, self.run_id)
        self.assertIs(terminal.evidence.descriptor()["details"]["connector_material_recorded"], True)
        self.assertNotIn("secret://", str(history))
        self.assertNotIn("credential_reference", str(history))
        self.assertEqual(await owner.execute_managed(command), result)
        self.assertEqual(len(self.ingress.calls), 3)

    async def test_revocation_after_token_denies_provider_and_retains_token_result(self):
        owner, command = await self.prepared_ingress()
        self.ingress.after_token = self.revoke_operate
        result = await owner.execute_managed(command)
        self.assertEqual(result.status.value, "unsupported")
        self.assertEqual(self.ingress.calls, ["secret-resolution"])
        history = self.ingress_history()
        self.assertEqual([unit["disposition"] for unit in history["units"]], ["admitted", "denied"])
        self.assertEqual(history["owner_fold"], "not-recorded")
        self.assertEqual(len(self.rows("cpk_ingress_invocation_results")), 0)

    async def test_revocation_after_provider_retains_ids_but_does_not_call_custody_or_cleanup(self):
        owner, command = await self.prepared_ingress()
        self.ingress.after_provider = self.revoke_operate
        result = await owner.execute_managed(command)
        self.assertEqual(result.status.value, "unsupported")
        self.assertEqual(self.ingress.calls, ["secret-resolution", "ingress-provider-create"])
        history = self.ingress_history()
        self.assertEqual(history["units"][1]["result"]["tunnel_id"], "tunnel-001")
        self.assertEqual(history["units"][2]["disposition"], "denied")
        self.assertEqual(history["partial_allocation_cleanup"], "unsupported")
        self.assertEqual(len(self.rows("cpk_cloudflare_ingress_resources")), 0)

    async def test_final_fold_failure_keeps_acknowledged_results_without_compensation(self):
        owner, command = await self.prepared_ingress()
        with mock.patch.object(self.ingress_service, "fold", side_effect=RuntimeError("not returned")):
            result = await owner.execute_managed(command)
        self.assertEqual(result.status.value, "uncertain")
        self.assertEqual(len(self.ingress.calls), 3)
        history = self.ingress_history()
        self.assertEqual(history["owner_fold"], "not-recorded")
        self.assertEqual([unit["outcome"] for unit in history["units"]], ["returned"] * 3)
        allocation_events = tuple(event for event in self.history()
            if event.activity_id == self.health_activity.activity_id.value)
        self.assertTrue(allocation_events)
        self.assertTrue(all(event.run_id == self.run_id for event in allocation_events))
        self.assertFalse(any(event.kind is ActivityEventKind.STEP_SUCCEEDED for event in allocation_events))
        self.assertFalse(any(event.evidence.descriptor().get("details", {}).get("connector_material_recorded")
            for event in allocation_events if event.evidence is not None))
        self.assertEqual(await owner.execute_managed(command), result)
        self.assertEqual(len(self.ingress.calls), 3)

    async def test_result_acknowledgement_failure_stops_next_unit_and_keeps_committed_facts(self):
        owner, command = await self.prepared_ingress()
        original = self.ingress_service._complete
        def lost_ack(bound, permit, outcome, payload):
            original(bound, permit, outcome, payload)
            if permit.record.kind is Kind.INGRESS_PROVIDER_CREATE:
                raise ExecutionInvocationControlError()
        with mock.patch.object(self.ingress_service, "_complete", side_effect=lost_ack):
            result = await owner.execute_managed(command)
        self.assertEqual(result.status.value, "uncertain")
        self.assertEqual(self.ingress.calls, ["secret-resolution", "ingress-provider-create"])
        history = self.ingress_history()
        self.assertEqual(history["units"][1]["outcome"], "returned")
        self.assertEqual(history["next_unit"]["state"], "not-admitted")
        self.assertEqual(history["callback_acknowledgement"], "delivery-not-established-by-history")

    async def test_exact_result_repeat_keeps_time_and_changed_result_conflicts(self):
        owner, command = await self.prepared_ingress()
        self.ingress.after_provider = self.revoke_operate
        await owner.execute_managed(command)
        port = self.ingress.port
        permit = self.ingress.permits["ingress-provider-create"]
        result = self.ingress.results["ingress-provider-create"]
        before = self.rows("cpk_ingress_invocation_results")
        with mock.patch.object(self.ingress_service, "_clock", side_effect=AssertionError("must retain original time")):
            port.record_result(permit, "returned", result)
        self.assertEqual(self.rows("cpk_ingress_invocation_results"), before)
        with self.assertRaises(ExecutionInvocationControlError):
            port.record_result(permit, "returned", dict(result, tunnel_id="another-tunnel"))
        with self.assertRaises(ExecutionInvocationControlError):
            port.admit("ingress-provider-create")

    async def test_closed_result_and_canonical_integer_bound(self):
        owner, command = await self.prepared_ingress()
        await owner.execute_managed(command)
        profile = self.ingress.port.profile
        self.assertEqual(IngressInvocationContextRecord.from_descriptor(profile.descriptor()), profile)
        result = self.ingress.results["ingress-custody-write"]
        oversized = dict(result, receipt=dict(result["receipt"], version_number=MAX_CANONICAL_INTEGER + 1))
        with self.assertRaises(ValueError):
            validate_ingress_result(profile, Kind.INGRESS_CUSTODY_WRITE, Outcome.RETURNED, oversized)
        with self.assertRaises(ValueError):
            validate_ingress_result(profile, Kind.INGRESS_PROVIDER_CREATE, Outcome.RETURNED,
                dict(version=1, tag="provider-created", tunnel_id="tunnel-001", dns_record_id="dns-001", token="forbidden"))
        stopped = dict(version=1, tag="provider-create-stopped", stage="tunnel-configuration",
            category="provider-status", mutation_certainty="tunnel-created", cleanup_result="complete",
            tunnel_id="tunnel-001", dns_record_id=None, dns_provenance="none")
        validate_ingress_result(profile, Kind.INGRESS_PROVIDER_CREATE, Outcome.FAILED, stopped)
        validate_ingress_result(profile, Kind.INGRESS_PROVIDER_CREATE, Outcome.UNKNOWN,
            dict(stopped, category="transport", mutation_certainty="uncertain"))
        with self.assertRaises(ValueError):
            validate_ingress_result(profile, Kind.INGRESS_PROVIDER_CREATE, Outcome.FAILED,
                dict(stopped, category="transport"))
        occupied = dict(stopped, stage="dns-pre-mutation-observation", category="hostname-occupied",
            dns_record_id="foreign-dns", dns_provenance="observed-only")
        validate_ingress_result(profile, Kind.INGRESS_PROVIDER_CREATE, Outcome.FAILED, occupied)
        validate_ingress_result(profile, Kind.INGRESS_PROVIDER_CREATE, Outcome.UNKNOWN,
            dict(occupied, category="dns-conflict", mutation_certainty="uncertain", cleanup_result="withheld"))
        with self.assertRaises(ValueError):
            validate_ingress_result(profile, Kind.INGRESS_PROVIDER_CREATE, Outcome.FAILED,
                dict(occupied, category="dns-conflict"))

    async def test_run_event_snapshot_never_mixes_later_unit_and_fold_commits(self):
        from control_plane_kit_operations.postgres.unit_of_work import PostgresUnitOfWork
        owner, command = await self.prepared_ingress()
        request = ReadPageRequest(ReadCollection.RUN_EVENTS, RunReadScope("workspace-a", self.run_id), 100)
        retained = {}
        def before_custody():
            scope = PostgresUnitOfWork(self.tracker._connect).run_events_snapshot()
            snapshot = scope.__enter__()
            retained.update(scope=scope, snapshot=snapshot, before=snapshot.run_events(request).descriptor())
        self.ingress.after_provider = before_custody
        try:
            result = await owner.execute_managed(command)
            self.assertEqual(result.status.value, "progressed")
            self.assertEqual(retained["snapshot"].run_events(request).descriptor(), retained["before"])
            before = next(event["ingress"] for event in retained["before"]["items"] if "ingress" in event)
            self.assertEqual(len(before["units"]), 2)
            self.assertEqual(before["owner_fold"], "not-recorded")
            self.assertEqual(self.ingress_history()["owner_fold"], "active")
        finally:
            if "scope" in retained:
                retained["scope"].__exit__(None, None, None)

    async def test_invalid_result_timestamp_is_a_bounded_read_conflict(self):
        from control_plane_kit_operations.read_services import ReadModelError
        owner, command = await self.prepared_ingress()
        await owner.execute_managed(command)
        row = self.connection.execute("SELECT invocation_id, recorded_at FROM cpk_ingress_invocation_results LIMIT 1").fetchone()
        try:
            self.connection.execute("UPDATE cpk_ingress_invocation_results SET recorded_at='infinity' WHERE invocation_id=%s", (row[0],))
            with self.assertRaisesRegex(ReadModelError, "run ingress history is unavailable"):
                self.ingress_history()
        finally:
            self.connection.execute("UPDATE cpk_ingress_invocation_results SET recorded_at=%s WHERE invocation_id=%s", (row[1], row[0]))
