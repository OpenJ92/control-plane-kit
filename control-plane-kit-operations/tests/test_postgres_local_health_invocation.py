"""Endpoint admission on a real retained-update local-readiness activity."""

import asyncio
import os
from datetime import datetime, timezone
from unittest import mock
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind
from control_plane_kit_core.operations import (
    ControlPlaneServiceRole, EffectAttemptIdentity, EffectAttemptTransition, EffectAttemptTransitionKind,
)
from control_plane_kit_core.planning import ManagementBootstrapStage, ObserveManagementBootstrap
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.coordinator import ExecuteActivityRun, _runtime_effect_intent_for_context
from control_plane_kit_operations.effect_attempt_start import StartEffectAttempt, NewlyStarted
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationKind as Kind, ExecutionInvocationDisposition as Disposition,
    ExecutionInvocationOutcome as Outcome,
    ExecutionInvocationDenied,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.health_effect_attempt_start import StartHealthEffectAttempt
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile
from control_plane_kit_operations.records import ManagedExecutionCommandIntent
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.test_managed_observed_installation import ManagedObservedFixture
from tests.test_managed_application_chain import now


class PostgresLocalHealthInvocationTests(ManagedObservedFixture):
    def setUp(self):
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        owner = psycopg.connect(database_url, autocommit=True)
        self.addCleanup(owner.close)
        schema = "local_health_invocation_" + uuid4().hex
        owner.execute(f'CREATE SCHEMA "{schema}"')
        self.addCleanup(owner.execute, f'DROP SCHEMA "{schema}" CASCADE')
        # Reuse the same owning-suite database and inherited setup, with the
        # ordinary isolated namespace used by current-permission fixtures.
        scoped = make_conninfo(database_url, options=f"-c search_path={schema}")
        with mock.patch.dict(os.environ, CPK_OPERATIONS_TEST_DATABASE_URL=scoped):
            super().setUp()

    async def test_retained_update_endpoint_admission_commits_before_call_without_effect_fold(self):
        # These existing owners establish accepted current receiver/configuration
        # truth, then prepare, approve, admit and claim the actual update plan.
        await self.prepare_content()
        self.assertIs(self.record.derivation_profile, PlanDerivationProfile.MANAGED_UPDATE_V1)
        coordinator = self.app.services[ControlPlaneServiceRole.EXECUTION]._service
        with self.unit_of_work() as uow:
            run = uow.stores.execution.get_run(self.update_run)
            request = uow.stores.execution.get_request(run.admission.request_id)
        authority = ExecutionWorkerAuthority(request.claim.worker_id, tuple(PolicyScope))
        fence = ExecutionLeaseFence(request.claim.worker_id, request.claim.generation)
        execution = ExecuteActivityRun(self.update_run, authority, fence, IdempotencyKey("local-invocation"))
        with _configuration_accounting(self.update_run, active=False):
            coordinator._configure_run(self.update_run)
            context = coordinator._load_context(execution)
            activity = context.schedule.ready[0]
            self.assertIs(type(activity.operation), ObserveManagementBootstrap)
            self.assertIs(activity.operation.stage, ManagementBootstrapStage.GATEWAY_LOCAL_READY)
            intent = _runtime_effect_intent_for_context(context, activity)

        installation = "local-invocation-" + uuid4().hex
        admin = PrincipalIdentity("invocation-fixture", "admin", PrincipalKind.OPERATOR)
        policy = operations.WorkspaceAdministrationPolicy(installation, (admin,), (admin, self.worker.identity))
        administration = policy.authorize(AuthenticatedPrincipal(admin))
        permissions = operations.WorkspacePermissionCommandService(self.unit_of_work,
            administration_policy=policy, clock=now, id_factory=lambda: uuid4().hex)
        permissions.initialize(administration, idempotency_key=IdempotencyKey("initialize"))
        permissions.grant(operations.GrantWorkspacePermissions(administration, "workspace-a",
            self.worker.identity, tuple(PolicyScope), 0, IdempotencyKey("grant-worker")))
        original = operations.WorkspaceContextResolver(self.unit_of_work,
            installation_id=installation).resolve(self.worker, "workspace-a")
        capabilities = operations.ManagedRuntimeCapabilityPolicy(installation,
            (operations.ManagedRuntimeCapability("installer-local-docker"),
             operations.ManagedRuntimeCapability("second-installed-docker")))
        with self.unit_of_work() as uow:
            runtime = uow.stores.runtime_authorities.get_active_for_update("workspace-a", intent.authority_ref)
        assignments = operations.ManagedRuntimeAssignmentCommandService(self.unit_of_work,
            administration_policy=policy, capability_policy=capabilities, clock=now,
            id_factory=lambda: uuid4().hex)
        assignments.grant(operations.GrantManagedRuntimeAccess(
                administration, "workspace-a", intent.operation.target.runtime_id,
                runtime.authority_ref, runtime.registration_id, "installer-local-docker", 0,
                IdempotencyKey("grant-management")))
        selection = operations.ManagedRuntimeSelectionService(self.unit_of_work,
            capability_policy=capabilities).select(workspace_id="workspace-a",
                runtime_id=intent.operation.target.runtime_id, authority=runtime)
        self.assertIsNotNone(selection)
        current_policy = operations.CurrentWorkspaceEffectPolicy(installation, capabilities)
        self.assertIsNone(coordinator._admit_command(execution,
            ManagedExecutionCommandIntent.from_context(original)))
        with self.unit_of_work() as uow:
            receipt = uow.stores.execution.command_receipt_for_idempotency(
                self.update_run, execution.idempotency_key.value)
        bound = operations.EffectCommandContext(original, operations.ExecutionCommandReference(
            self.update_run, execution.idempotency_key.value, receipt.intent_fingerprint))
        identity = EffectAttemptIdentity(intent.source.run_id, activity.activity_id.value, 1)
        start = StartHealthEffectAttempt(StartEffectAttempt(request.identity.request_id,
            EffectAttemptTransition(EffectAttemptTransitionKind.STARTED, identity,
                runtime_effect_intent_fingerprint(intent)), intent, authority, fence), original)
        effects = self.effects()
        started = EffectAttemptStartService(self.unit_of_work, id_factory=lambda: uuid4().hex,
            health_receiver_decoders=self.registry, permission_policy=current_policy).execute_health(
                start, command_context=bound, managed_target=selection)
        self.assertIs(type(started.start), NewlyStarted)
        with self.unit_of_work() as uow:
            marker, = uow.stores.execution.command_invocations(bound.receipt).items
        self.assertIs(marker.kind, Kind.SIGNED_HEALTH_TRANSPORT)
        self.assertIs(marker.disposition, Disposition.PLANNED)
        invocations = operations.ExecutionInvocationService(self.unit_of_work,
            permission_policy=current_policy, clock=now, health_receiver_decoders=self.registry).bind_health(
                command_context=bound, start=start, started=started, managed_target=selection)
        # Original grants start at the next integral second, just as in the
        # consuming coordinator. No lease or grant extension is introduced.
        delay = max(0.0, started.preparation.transit_grant.not_before - datetime.now(timezone.utc).timestamp())
        self.assertLess(delay, 1.0)
        if delay:
            await asyncio.sleep(delay)
        permit = invocations.admit("endpoint-observation")
        with self.unit_of_work() as uow:
            admitted = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=activity.activity_id.value, kind=Kind.ENDPOINT_OBSERVATION)
        self.assertIs(admitted.disposition, Disposition.ADMITTED)
        self.assertIs(admitted.outcome, Outcome.PENDING)
        self.assertEqual((admitted.decision.permission_revision, admitted.decision.assignment_id,
            admitted.decision.assignment_revision), (1, selection.assignment_id, selection.revision))
        self.assertEqual(self.tracker.active, 0)
        revoked = assignments.revoke(operations.RevokeManagedRuntimeAccess(administration,
            "workspace-a", selection.runtime_id, runtime.authority_ref, selection.revision,
            IdempotencyKey("revoke-after-endpoint-admission")))
        assignments.grant(operations.GrantManagedRuntimeAccess(administration,
            "workspace-a", selection.runtime_id, runtime.authority_ref, runtime.registration_id,
            "second-installed-docker", revoked.receipt.resulting_assignment.revision,
            IdempotencyKey("regrant-after-endpoint-admission")))
        current = operations.ManagedRuntimeSelectionService(self.unit_of_work,
            capability_policy=capabilities).select(workspace_id="workspace-a",
                runtime_id=selection.runtime_id, authority=runtime)
        self.assertEqual(current.capability_id, "second-installed-docker")
        # The bounded call retains A despite current B; this is a controlled
        # owner capability, not a claim of Docker mapping or endpoint IO.
        calls = [("endpoint-observation", selection)]
        invocations.record_outcome(permit, "returned")
        with self.assertRaises(ExecutionInvocationDenied):
            invocations.admit("secret-resolution",
                authorization_id=started.preparation.transit_authorization_id)
        with self.unit_of_work() as uow:
            retained = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=activity.activity_id.value, kind=Kind.ENDPOINT_OBSERVATION)
            self.assertIs(retained.outcome, Outcome.RETURNED)
            self.assertEqual(uow.stores.execution.command_invocation(bound.receipt, marker.invocation_id), marker)
            self.assertEqual(uow.stores.effect_attempts.get(identity), started.start.attempt)
        self.assertEqual(calls, [("endpoint-observation", selection)])
        self.assertEqual(selection.capability_id, "installer-local-docker")
        self.assertEqual(self.effects(), effects)
