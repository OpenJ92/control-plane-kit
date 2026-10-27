"""Dynamic removal of a real owner allocation through the managed coordinator."""

from dataclasses import replace
from uuid import uuid4

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.planning import RemovePublicIngress, derive_schedule, project_activity_journal
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_operations.coordinator import ActivityExecutionDispatcher, ExecuteActivityRun, ExecuteManagedActivityRun, ExecutionCoordinator
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.ingress_authorities import IngressAuthorityProviderKind, OwnedIngressResourceStatus
from control_plane_kit_operations.ingress_realization import IngressRealizationAdapter, IngressProviderRegistration
from control_plane_kit_operations.ingress_invocation_service import IngressInvocationService
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority, RunLifecycleCommandService
from control_plane_kit_operations.records import ActivityEventRecord
from control_plane_kit_operations.secret_providers import SecretUseAuthorizationService
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.test_managed_application_chain import ManagedApplicationFixture, ManagedUpdateExecutionTests, ForbiddenRecovery, now, operator_principal
from tests.test_postgres_coordinator_permissions import _CurrentCoordinatorPermission
from tests.ingress_invocation_fixture import StagedIngressPort


class PostgresIngressRemovalInvocationTests(_CurrentCoordinatorPermission, ManagedApplicationFixture):
    async def prepared_removal(self):
        # Establish the existing successful allocation through its owning public
        # workflow. This is a fixture premise, not dynamic create adoption evidence.
        await ManagedUpdateExecutionTests.complete_initial(self)
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
            self.original_resource, = uow.stores.ingress_resources.list_cloudflare("workspace-a")
        prepared = await self.invoke("command.deployment.prepare", ControlPlaneServiceRole.PLANNING,
            path={"workspace_id": "workspace-a"}, payload={
                "desired_graph": DEFAULT_GRAPH_CODEC.encode(DeploymentGraph("empty")),
                "expected_current": {"authored_graph_id": workspace.current_graph_id,
                    "realized_projection_id": workspace.current_realized_projection_id},
                "expected_desired": {"authored_graph_id": workspace.desired_graph_id,
                    "realized_projection_id": workspace.desired_realized_projection_id},
                "expected_desired_graph_revision": workspace.desired_graph_revision,
                "title": "Remove ingress", "idempotency_key": "remove-ingress-prepare"})
        self.plan_id = prepared["plan_id"]
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan(self.plan_id)
        await self.invoke("command.approval.decide", ControlPlaneServiceRole.APPROVAL,
            path={"workspace_id": "workspace-a", "approval_id": prepared["approval_request_id"]},
            payload={"session_id": plan.session_id, "decision": "approved", "idempotency_key": "remove-ingress-approve"},
            principal=operator_principal(subject_id="manager-a", scopes=(PolicyScope.PLAN_APPROVE_DESTRUCTIVE,)))
        admitted = await self.invoke("command.deployment.admit", ControlPlaneServiceRole.ADMISSION,
            path={"workspace_id": "workspace-a", "plan_id": self.plan_id}, payload={"session_id": plan.session_id,
                "approval_request_id": prepared["approval_request_id"], "readiness": [], "idempotency_key": "remove-ingress-admit"})
        self.request_id = admitted["execution_request_id"]
        claimed = await self.invoke("command.run.claim", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": self.request_id}, principal=self.worker,
            payload={"lease_duration_seconds": 1800, "idempotency_key": "remove-ingress-claim"})
        self.run_id, self.generation = claimed["run_id"], claimed["claim_generation"]
        await self.invoke("command.run.start", ControlPlaneServiceRole.EXECUTION,
            path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker,
            payload={"claim_generation": self.generation, "idempotency_key": "remove-ingress-start"})
        selected, = (activity for activity in plan.plan.activities if type(activity.operation) is RemovePublicIngress)
        from control_plane_kit_operations.activity_journal import activity_journal_events
        with self.unit_of_work() as uow:
            events = list(uow.stores.execution.events_for_run(self.run_id))
            # Upstream-ready journal premise, using the existing Core schedule.
            # No provider deletion or allocation ownership is fabricated here.
            for _ in range(len(plan.plan.activities) + 1):
                journal = project_activity_journal(plan.plan, activity_journal_events(events))
                ready = derive_schedule(plan.plan, journal.state).ready
                self.assertTrue(ready)
                if ready[0].activity_id == selected.activity_id:
                    break
                for kind in (ActivityEventKind.STEP_STARTED, ActivityEventKind.STEP_SUCCEEDED):
                    event = ActivityEventRecord(uuid4().hex, self.run_id, len(events) + 1,
                        kind, now(), activity_id=ready[0].activity_id.value)
                    uow.stores.execution.add_event(event)
                    events.append(event)
            else:
                self.fail("ingress removal did not become ready")
            request = uow.stores.execution.get_request(self.request_id)
            uow.commit()
        self.configure_permissions(self.worker.identity, installation="coordinator-permission")
        self.ingress = StagedIngressPort(self)
        service = IngressInvocationService(self.unit_of_work, permission_policy=self.policy, clock=now)
        adapter = ActivityExecutionDispatcher(self.runtime, IngressRealizationAdapter(self.unit_of_work,
            {IngressAuthorityProviderKind.CLOUDFLARE: IngressProviderRegistration(self.ingress, "cpk.ingress-invocations.v1")}, now,
            SecretUseAuthorizationService(self.unit_of_work), service))
        fold = EffectAttemptFoldService(self.unit_of_work, id_factory=lambda: uuid4().hex)
        owner = ExecutionCoordinator(self.unit_of_work,
            lifecycle=RunLifecycleCommandService(self.unit_of_work, clock=now, id_factory=lambda: uuid4().hex),
            adapter=adapter, start_service=EffectAttemptStartService(self.unit_of_work,
                id_factory=lambda: uuid4().hex, permission_policy=self.policy), fold_service=fold,
            reconciliation_service=EffectAttemptReconciliationService(self.unit_of_work, ForbiddenRecovery(), fold),
            clock=now, id_factory=lambda: uuid4().hex, permission_policy=self.policy)
        command = ExecuteManagedActivityRun(ExecuteActivityRun(self.run_id,
            ExecutionWorkerAuthority(request.claim.worker_id, self.original.granted_scopes), request.claim.fence,
            IdempotencyKey("remove-ingress-execute")), self.original)
        return owner, command

    def resource(self):
        with self.unit_of_work() as uow:
            return next(resource for resource in uow.stores.ingress_resources.list_cloudflare("workspace-a")
                if resource.epoch == self.original_resource.epoch)

    def ingress_history(self):
        from control_plane_kit_operations.read_pages import ReadCollection, ReadPageRequest, RunReadScope
        with self.unit_of_work().run_events_snapshot() as snapshot:
            page = snapshot.run_events(ReadPageRequest(ReadCollection.RUN_EVENTS,
                RunReadScope("workspace-a", self.run_id), 100))
            return next(event["ingress"] for event in page.items if "ingress" in event)

    async def test_success_requires_revoke_then_remove_and_folds_exact_epoch(self):
        owner, command = await self.prepared_removal()
        result = await owner.execute_managed(command)
        self.assertEqual(result.status.value, "progressed")
        self.assertEqual(self.ingress.calls, ["secret-resolution", "ingress-custody-revoke", "ingress-provider-remove"])
        self.assertEqual(self.resource().status, OwnedIngressResourceStatus.REMOVED)
        self.assertEqual(self.resource().removed_by_run_id, self.run_id)
        history = self.ingress_history()
        self.assertEqual(history["owner_fold"], "removed")
        self.assertEqual(history["current_owner_state"], "removed")
        self.assertEqual(await owner.execute_managed(command), result)
        self.assertEqual(len(self.ingress.calls), 3)

    async def test_token_completion_after_revoke_does_not_mark_resource_removing(self):
        owner, command = await self.prepared_removal()
        self.ingress.after_token = self.revoke_operate
        result = await owner.execute_managed(command)
        self.assertEqual(result.status.value, "unsupported")
        self.assertEqual(self.ingress.calls, ["secret-resolution"])
        self.assertEqual(self.resource(), self.original_resource)

    async def test_later_owner_removal_preserves_earlier_denial_history(self):
        owner, command = await self.prepared_removal()
        self.ingress.after_token = self.revoke_operate
        result = await owner.execute_managed(command)
        self.assertEqual(result.status.value, "unsupported")
        before = self.ingress_history()
        self.assertEqual(before["current_owner_state"], "active")
        self.assertEqual([unit["disposition"] for unit in before["units"]], ["admitted", "denied"])
        # Model the later owner's successful resource transitions with the real
        # resource store. This read regression neither executes nor authorizes B.
        with self.unit_of_work() as uow:
            uow.stores.ingress_resources.mark_removing("workspace-a", self.original_resource.ingress_id,
                source_run_id="later-removal-run")
            uow.stores.ingress_resources.mark_removed("workspace-a", self.original_resource.ingress_id,
                removed_at=now(), removed_by_run_id="later-removal-run")
            uow.commit()
        after = self.ingress_history()
        self.assertEqual(after["units"], before["units"])
        self.assertEqual(after["owner_fold"], "not-folded-by-this-command")
        self.assertEqual(after["current_owner_state"], "removed")
        self.assertEqual(self.ingress.calls, ["secret-resolution"])
        self.assertEqual(await owner.execute_managed(command), result)

    async def test_custody_completion_after_revoke_retains_removing_and_stops_provider(self):
        owner, command = await self.prepared_removal()
        observed = []
        def revoke():
            observed.append(self.resource().status)
            self.revoke_operate()
        self.ingress.after_custody = revoke
        result = await owner.execute_managed(command)
        self.assertEqual(result.status.value, "unsupported")
        self.assertEqual(observed, [OwnedIngressResourceStatus.REMOVING])
        self.assertEqual(self.ingress.calls, ["secret-resolution", "ingress-custody-revoke"])
        self.assertEqual(self.resource().status, OwnedIngressResourceStatus.REMOVING)
        self.assertEqual(self.resource().source_run_id, self.run_id)
