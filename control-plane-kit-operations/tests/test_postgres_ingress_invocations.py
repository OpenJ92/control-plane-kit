"""Real owning-store admission, result retention and bounded history laws."""

from dataclasses import replace
import json
from unittest import mock

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.planning import AllocatePublicIngress, derive_schedule, project_activity_journal
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.cpk_server import CpkServerAdvancementFailure
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

    async def allocate_native(self, *, max_effects=1, qualified_runtime=False,
            command_key="native-ingress-acceptance"):
        factory = self.unit_of_work
        fold = managed.EffectAttemptFoldService(factory, id_factory=self.ids["effect-fold"])
        import control_plane_kit_operations as operations
        runtime = (managed.RuntimeInterpreterDispatcher({managed.RuntimeKind.DOCKER:
            managed.RuntimeAdapterRegistration(self.runtime, exact_version_execute_with_authority=True,
                invocation_profile="cpk.semantic-invocations.v1")}, managed.SecretUseAuthorizationService(factory))
            if qualified_runtime else self.runtime)
        owner = ExecutionCoordinator(factory,
            lifecycle=managed.RunLifecycleCommandService(factory, clock=now, id_factory=self.ids["lifecycle"]),
            adapter=ActivityExecutionDispatcher(runtime=runtime,
                ingress=IngressRealizationAdapter(factory,
                    {IngressAuthorityProviderKind.CLOUDFLARE: IngressProviderRegistration(
                        self.ingress, "cpk.ingress-invocations.v1")}, now,
                    managed.SecretUseAuthorizationService(factory), invocation_service=IngressInvocationService(
                        factory, permission_policy=self.policy, clock=now))),
            start_service=managed.EffectAttemptStartService(factory,
                id_factory=self.ids["effect-start"], permission_policy=self.policy,
                health_receiver_decoders=self.registry), fold_service=fold,
            reconciliation_service=managed.EffectAttemptReconciliationService(
                factory, managed.ForbiddenRecovery(), fold),
            clock=now, id_factory=self.ids["execution"], permission_policy=self.policy,
            managed_health=self.health, health_signing_authority=managed.HealthSigningAuthorityReloadService(
                factory, health_receiver_decoders=self.registry),
            invocation_service=(operations.ExecutionInvocationService(factory, permission_policy=self.policy,
                clock=now, health_receiver_decoders=self.registry) if qualified_runtime else None))
        with factory() as uow:
            request = uow.stores.execution.get_request(self.request_id)
        execution = ExecuteActivityRun(self.run_id,
            ExecutionWorkerAuthority(request.claim.worker_id, self.original.granted_scopes),
            request.claim.fence, IdempotencyKey(command_key), max_effects=max_effects)
        return (await owner.execute_managed(ExecuteManagedActivityRun(execution, self.original))).descriptor()

    async def qualified_ingress_boundary(self, *, connector_configuration=False):
        import control_plane_kit_operations as operations
        from tests.test_postgres_accepted_installation_sources import _TwoIngressRuntime
        if connector_configuration:
            self.connector_artifacts = (managed.ConfigurationArtifact("connector-settings",
                "/etc/cpk/connector/settings.json", managed.ConfigurationMediaType.JSON, '{"configured":true}'),)
            self.graph, self.documents, self.registry = self.authored_graph()
        self.runtime = _TwoIngressRuntime(self)
        await self.prepare_and_start()
        for _ in range(len(self.plan.activities)):
            journal = project_activity_journal(self.plan, activity_journal_events(self.history()))
            ready = derive_schedule(self.plan, journal.state).ready
            if ready and type(ready[0].operation) is AllocatePublicIngress:
                self.allocation_activity = ready[0].activity_id.value
                break
            result = await self.execute_one()
            self.assertEqual(result["coordinator_status"], "progressed", result)
        else:
            self.fail("allocation never became ready")
        self.configure_permissions(self.worker.identity, installation="coordinator-permission")
        with self.unit_of_work() as uow:
            runtime = uow.stores.runtime_authorities.get_active_for_update(
                "workspace-a", self.graph.runtimes["docker"].authority_ref)
        assignments = operations.ManagedRuntimeAssignmentCommandService(self.unit_of_work,
            administration_policy=self.admin_policy, capability_policy=self.capabilities,
            clock=now, id_factory=self.ids["session"])
        assignments.grant(operations.GrantManagedRuntimeAccess(self.admin_context,
            "workspace-a", "docker", runtime.authority_ref, runtime.registration_id,
            "local-docker", 0, IdempotencyKey("qualified-ingress-management")))

    async def test_qualified_ingress_connector_separate_commands_positive_control(self):
        await self.qualified_ingress_boundary()
        first = await self.allocate_native(qualified_runtime=True, command_key="qualified-allocation")
        self.assertEqual(first["coordinator_status"], "progressed", first)
        from tests.ingress_materialization_diagnostic import inspect_materialization
        before = self.history()
        observed = inspect_materialization(self.tracker._connect, workspace_id="workspace-a",
            request_id=self.request_id, run_id=self.run_id, plan_id=self.plan_id,
            command_key="qualified-allocation", expected_plan={name: self.plan_descriptor[name] for name in (
                "base_graph_id", "base_realized_projection_id", "desired_graph_id",
                "desired_realized_projection_id", "desired_graph_revision", "derivation_profile")})
        self.assertTrue(observed["transaction_read_only"], observed)
        self.assertTrue(observed["configuration_context_ready"], observed)
        self.assertEqual(self.history(), before)
        second = await self.allocate_native(qualified_runtime=True, command_key="qualified-connector")
        self.assertEqual(second["coordinator_status"], "progressed", second)
        self.assertIn("connector", self.runtime.containers)
        self.assertEqual(self.ingress.calls, ["secret-resolution", "ingress-provider-create", "ingress-custody-write"])

    async def test_qualified_ingress_connector_single_command_preserves_owner_transition(self):
        await self.qualified_ingress_boundary()
        result = await self.allocate_native(qualified_runtime=True, max_effects=2,
            command_key="qualified-allocation-and-connector")
        self.assertEqual(result["coordinator_status"], "progressed", result)
        self.assertEqual(result["effects_attempted"], 2)
        self.assertIn("connector", self.runtime.containers)
        self.assertEqual(self.ingress.calls, ["secret-resolution", "ingress-provider-create", "ingress-custody-write"])

    async def test_post_allocation_preparation_capacity_failure_retains_command_diagnostic(self):
        """New law: diagnostic evidence never invents a terminal activity outcome."""
        from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict
        from control_plane_kit_operations.postgres.configuration_evidence import _Capacity

        await self.qualified_ingress_boundary(connector_configuration=True)
        before = self.history()
        current_before = await self.invoke("read.current-graph", ControlPlaneServiceRole.READS,
            path={"workspace_id": "workspace-a"})
        boundary = {}
        original = ExecutionCoordinator._configuration_context
        command_key = "post-allocation-preparation-diagnostic"

        def refuse_next_context(owner, stores, command, request, run, **kwargs):
            history = self.history()
            if any(event.activity_id == self.allocation_activity
                    and event.kind is ActivityEventKind.STEP_SUCCEEDED for event in history):
                boundary["history"] = history
                boundary["run"] = run
                boundary["request"] = request
                raise _Capacity("diagnostic-test-private-material-must-not-be-recorded")
            return original(owner, stores, command, request, run, **kwargs)

        with mock.patch.object(ExecutionCoordinator, "_configuration_context", refuse_next_context):
            with self.assertRaises(ExecutionCoordinatorConflict) as raised:
                await self.allocate_native(qualified_runtime=True, max_effects=2,
                    command_key=command_key)

        self.assertIn("history", boundary, "fault must follow committed allocation success")
        self.assertEqual(boundary["history"][:len(before)], before)
        self.assertEqual(self.history(), boundary["history"])
        self.assertNotIn("connector", self.runtime.containers)
        self.assertEqual(self.ingress.calls,
            ["secret-resolution", "ingress-provider-create", "ingress-custody-write"])
        with self.unit_of_work() as uow:
            receipt = uow.stores.execution.command_receipt_for_idempotency(self.run_id, command_key)
            self.assertEqual(uow.stores.execution.get_run(self.run_id), boundary["run"])
            self.assertEqual(uow.stores.execution.get_request(self.request_id), boundary["request"])
        self.assertEqual(receipt.status.value, "incomplete")
        self.assertIsNone(receipt.result)
        self.assertIsNone(receipt.completed_at)
        diagnostic = getattr(receipt, "diagnostic", None)
        self.assertIsNotNone(diagnostic,
            "missing durable command diagnostic after committed allocation and typed preparation refusal")
        self.assertEqual(diagnostic.phase, "context-preparation")
        self.assertEqual(diagnostic.category, "evidence-capacity")
        self.assertEqual(diagnostic.last_committed_event_ordinal, boundary["history"][-1].ordinal)
        self.assertNotIn("diagnostic-test-private-material", repr(diagnostic))
        self.assertEqual(raised.exception.category, "evidence-capacity")
        self.assertEqual(raised.exception.diagnostic_status, "recorded")
        self.assertEqual(await self.invoke("read.current-graph", ControlPlaneServiceRole.READS,
            path={"workspace_id": "workspace-a"}), current_before)
        replay = await self.allocate_native(qualified_runtime=True, max_effects=2,
            command_key=command_key)
        self.assertEqual(replay["coordinator_status"], "uncertain")
        self.assertEqual(self.history(), boundary["history"])
        self.assertNotIn("connector", self.runtime.containers)

    async def test_claimed_preparation_unavailable_records_opened_frontier_without_effects(self):
        from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict
        from control_plane_kit_operations.postgres.configuration_evidence import _Unavailable
        self.connector_artifacts = (managed.ConfigurationArtifact("connector-settings",
            "/etc/cpk/connector/settings.json", managed.ConfigurationMediaType.JSON, '{"configured":true}'),)
        self.graph, self.documents, self.registry = self.authored_graph()
        await self.prepare_and_start(start_run=False)
        self.configure_permissions(self.worker.identity, installation="coordinator-permission")
        before = self.history()
        self.assertEqual([event.kind for event in before], [ActivityEventKind.RUN_OPENED])
        with self.unit_of_work() as uow:
            run_before = uow.stores.execution.get_run(self.run_id)
            request_before = uow.stores.execution.get_request(self.request_id)
        with mock.patch.object(ExecutionCoordinator, "_configuration_context",
                side_effect=_Unavailable("private-preparation-material")):
            with self.assertRaises(ExecutionCoordinatorConflict) as raised:
                await self.allocate_native(qualified_runtime=True, command_key="early-unavailable")
        with self.unit_of_work() as uow:
            receipt = uow.stores.execution.command_receipt_for_idempotency(self.run_id, "early-unavailable")
            self.assertEqual(uow.stores.execution.get_run(self.run_id), run_before)
            self.assertEqual(uow.stores.execution.get_request(self.request_id), request_before)
        diagnostic = getattr(receipt, "diagnostic", None)
        self.assertIsNotNone(diagnostic, "early CLAIMED failure needs bounded command evidence")
        self.assertEqual(diagnostic.category, "material-unavailable")
        self.assertEqual(diagnostic.last_committed_event_ordinal, before[-1].ordinal)
        self.assertEqual(raised.exception.diagnostic_status, "recorded")
        self.assertNotIn("private-preparation-material", repr(raised.exception))
        self.assertEqual(self.history(), before)
        self.assertEqual(self.effects(), ((), (), (), (), ()))
        from control_plane_kit_operations.read_services.operator_overview import _OperatorOverviewReadProjection
        with self.unit_of_work() as uow:
            projection = _OperatorOverviewReadProjection(uow.stores.workspaces, uow.stores.graphs,
                uow.stores.activity_history, uow.stores.execution)
            command_view, _ = projection._read_command(run_before)
            self.assertEqual(command_view["diagnostic"], diagnostic.descriptor())
            uow.stores.execution.add_command_receipt(replace(receipt,
                idempotency_key="second-unresolved", diagnostic=None))
            ambiguous, _ = projection._read_command(run_before)
            self.assertEqual(ambiguous["receipt_state"], "ambiguous")
            self.assertNotIn("diagnostic", ambiguous)
        from control_plane_kit_operations.postgres.execution import validate_command_diagnostic_rows
        validate_command_diagnostic_rows(self.connection)
        # No invocation children are needed for strict retained diagnostic validation.
        self.connection.execute("UPDATE cpk_execution_command_receipts SET diagnostic=jsonb_set("
            "diagnostic,'{category}','\"unknown\"'::jsonb) WHERE run_id=%s AND idempotency_key=%s",
            (self.run_id, "early-unavailable"))
        from control_plane_kit_operations.postgres.current_data_validation import CurrentRowDrift
        try:
            with self.assertRaises(CurrentRowDrift):
                validate_command_diagnostic_rows(self.connection)
        finally:
            from psycopg.types.json import Jsonb
            self.connection.execute("UPDATE cpk_execution_command_receipts SET diagnostic=%s "
                "WHERE run_id=%s AND idempotency_key=%s", (Jsonb(diagnostic.descriptor()),
                    self.run_id, "early-unavailable"))

    async def test_command_diagnostic_store_first_write_fencing_and_rollback(self):
        from control_plane_kit_operations import records
        from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict
        await self.prepare_and_start(start_run=False)
        self.configure_permissions(self.worker.identity, installation="coordinator-permission")
        key = "store-diagnostic"
        with mock.patch.object(ExecutionCoordinator, "_load_context",
                side_effect=ExecutionCoordinatorConflict("admission-only boundary")):
            with self.assertRaises(ExecutionCoordinatorConflict):
                await self.allocate_native(qualified_runtime=True, command_key=key)
        with self.unit_of_work() as uow:
            receipt = uow.stores.execution.command_receipt_for_idempotency(self.run_id, key)
            request = uow.stores.execution.get_request(self.request_id)
            writer = getattr(uow.stores.execution, "record_command_diagnostic", None)
            self.assertIsNotNone(writer, "missing immutable fenced diagnostic mutation")
        value = records.ExecutionCommandDiagnostic("context-preparation", "evidence-capacity",
            now(), self.history()[-1].ordinal)
        args = dict(intent_fingerprint=receipt.intent_fingerprint, fence=request.claim.fence,
            diagnostic=value)
        # An uncommitted caller cannot persist evidence.
        with self.unit_of_work() as uow:
            uow.stores.execution.record_command_diagnostic(self.run_id, key, **args)
        with self.unit_of_work() as uow:
            self.assertIsNone(uow.stores.execution.command_receipt_for_idempotency(self.run_id, key).diagnostic)
        for changed in ({"intent_fingerprint": "0" * 64},
                {"fence": replace(request.claim.fence, generation=request.claim.generation + 1)},
                {"fence": replace(request.claim.fence, worker_id="foreign-worker")},
                {"diagnostic": replace(value, last_committed_event_ordinal=value.last_committed_event_ordinal + 1)},
                {"diagnostic": replace(value, occurred_at="2000-01-01T00:00:00Z")}):
            with self.subTest(changed=changed), self.assertRaises(records.OperationsRecordError):
                with self.unit_of_work() as uow:
                    uow.stores.execution.record_command_diagnostic(self.run_id, key, **(args | changed))
                    uow.commit()
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2, timeout=10)
        def first_writer():
            barrier.wait()
            with self.unit_of_work() as uow:
                record = uow.stores.execution.record_command_diagnostic(self.run_id, key, **args)
                uow.commit()
            return record
        with ThreadPoolExecutor(max_workers=2) as workers:
            first_future, second_future = workers.submit(first_writer), workers.submit(first_writer)
            first, second = first_future.result(timeout=20), second_future.result(timeout=20)
        self.assertEqual(first, second)
        await self.invoke("command.run.start", ControlPlaneServiceRole.EXECUTION,
            path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker,
            payload={"claim_generation": self.generation, "idempotency_key": "later-explicit-start"})
        self.assertGreater(self.history()[-1].ordinal, value.last_committed_event_ordinal)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.record_command_diagnostic(self.run_id, key, **args), first)
            uow.commit()
        with self.assertRaises(records.OperationsRecordError):
            with self.unit_of_work() as uow:
                uow.stores.execution.record_command_diagnostic(self.run_id, key,
                    **(args | {"diagnostic": replace(value, category="material-unavailable")}))
                uow.commit()
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.command_receipt_for_idempotency(self.run_id, key).diagnostic, value)
        # Even an exact duplicate cannot use an expired live lease.
        self.connection.execute("UPDATE cpk_execution_requests SET lease_expires_at=clock_timestamp() "
            "WHERE request_id=%s", (self.request_id,))
        with self.assertRaises(records.OperationsRecordError):
            with self.unit_of_work() as uow:
                uow.stores.execution.record_command_diagnostic(self.run_id, key, **args)

    async def test_preparation_diagnostic_exit_uncertainty_preserves_primary_category(self):
        from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict
        from control_plane_kit_operations.postgres.configuration_evidence import _Unavailable
        from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
        from control_plane_kit_operations.postgres.unit_of_work import PostgresUnitOfWork
        self.connector_artifacts = (managed.ConfigurationArtifact("connector-settings",
            "/etc/cpk/connector/settings.json", managed.ConfigurationMediaType.JSON, '{"configured":true}'),)
        self.graph, self.documents, self.registry = self.authored_graph()
        await self.prepare_and_start(start_run=False)
        self.configure_permissions(self.worker.identity, installation="coordinator-permission")
        before = self.history()
        original_exit = PostgresUnitOfWork.__exit__
        original_write = PostgresExecutionStore.record_command_diagnostic
        for mode in ("preparation-exit", "before-commit", "commit-before-ack"):
            boundary = {}
            key = "uncertain-" + mode
            def unavailable(owner, stores, *args, **kwargs):
                boundary["preparation"] = id(stores.connection)
                raise _Unavailable("private-preparation-cause")
            def write(store, *args, **kwargs):
                boundary["diagnostic"] = id(store._connection)
                return original_write(store, *args, **kwargs)
            def exit_with_fault(uow, *args):
                target = "preparation" if mode == "preparation-exit" else "diagnostic"
                selected = id(uow._connection) == boundary.get(target)
                if selected and mode == "before-commit":
                    uow.rollback()
                result = original_exit(uow, *args)
                if selected:
                    raise RuntimeError("private-exit-cause")
                return result
            with self.subTest(mode=mode), mock.patch.object(ExecutionCoordinator, "_configuration_context", unavailable), \
                    mock.patch.object(PostgresExecutionStore, "record_command_diagnostic", write), \
                    mock.patch.object(PostgresUnitOfWork, "__exit__", exit_with_fault):
                with self.assertRaises(ExecutionCoordinatorConflict) as raised:
                    await self.allocate_native(qualified_runtime=True, command_key=key)
            self.assertEqual(raised.exception.category, "material-unavailable")
            self.assertEqual(raised.exception.diagnostic_status, "unacknowledged")
            self.assertIsNone(raised.exception.__context__)
            with self.unit_of_work() as uow:
                receipt = uow.stores.execution.command_receipt_for_idempotency(self.run_id, key)
            self.assertEqual(receipt.diagnostic is not None, mode == "commit-before-ack")
            self.assertEqual(receipt.status.value, "incomplete")
            self.assertEqual(self.history(), before)
        self.assertEqual(self.effects(), ((), (), (), (), ()))

    async def test_qualified_ingress_connector_configuration_and_generated_token_cross_same_command(self):
        await self.qualified_ingress_boundary(connector_configuration=True)
        result = await self.allocate_native(qualified_runtime=True, max_effects=2,
            command_key="qualified-configured-connector")
        self.assertEqual(result["coordinator_status"], "progressed", result)
        self.assertEqual(result["effects_attempted"], 2)
        request, = (r for r in self.runtime.calls
            if getattr(r.operation.target, "node_id", None) == "connector")
        self.assertEqual(request.kind.value, "configuration-activity.v1")
        self.assertEqual(len(request.products[0].secret_selections), 1)
        self.assertIn("connector", self.runtime.containers)



    async def test_native_allocation_reaches_current_graph_acceptance_with_original_evidence(self):
        await self.prepare_and_start()
        self.configure_permissions(self.worker.identity, installation="coordinator-permission")
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
        try:
            await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
                path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker,
                payload={"plan_id": self.plan_id, "expected_current_graph_id": self.workspace["current_graph_id"],
                    "expected_current_realized_projection_id": self.workspace["current_realized_projection_id"],
                    "desired_graph_id": plan["desired_graph_id"],
                    "desired_realized_projection_id": plan["desired_realized_projection_id"],
                    "expected_desired_graph_revision": plan["desired_graph_revision"],
                    "claim_generation": self.generation, "idempotency_key": "native-ingress-advance"})
        except CpkServerAdvancementFailure as error:
            error.add_note(f"native ingress acceptance: stage={error.diagnostic.stage}, reason={error.diagnostic.reason}")
            raise
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
