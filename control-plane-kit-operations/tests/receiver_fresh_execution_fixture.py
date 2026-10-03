"""C3 composes existing command owners; it never seeds receiver acceptance."""

from dataclasses import replace
from datetime import datetime, timezone
import os
import uuid

from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptTransition, EffectAttemptTransitionKind
from control_plane_kit_core.planning import ActivityId
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.coordinator import ExecuteActivityRun, ExecutionCoordinator
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_attempt_start import StartEffectAttempt
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority, RunLifecycleCommandService, StartActivityRun
from control_plane_kit_operations.planning import DesiredGraphCommandService
from control_plane_kit_operations.postgres.temporal import decode_postgres_timestamp
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
from tests.receiver_execution_scope_fixture import ReceiverExecutionScopeFixture
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds
from tests.postgres_effect_attempt_reconciliation_fixture import FailIfObserver
from tests.test_receiver_admission_execution_evidence import ReceiverAdmissionExecutionEvidenceTests


def load_execution_context(coordinator, command):
    """Read real pinned context under the public command's accounting precondition.

    These tests use the private loader to prepare retained or health inputs;
    public execution normally establishes this scope before reaching it. Join
    any same-command outer ledger rather than resetting its budget.
    """
    from control_plane_kit_operations._configuration_preparation import _configuration_accounting
    with _configuration_accounting(command.run_id, active=False, join=True):
        coordinator._configure_run(command.run_id)
        return coordinator._load_context(command)


class ReceiverFreshExecutionFixture(ReceiverExecutionScopeFixture):
    receiver_graph = ReceiverAdmissionExecutionEvidenceTests.receiver_graph
    receiver_command = ReceiverAdmissionExecutionEvidenceTests.receiver_command
    graph_truth = ReceiverAdmissionExecutionEvidenceTests.truth
    assert_competing_refused = ReceiverAdmissionExecutionEvidenceTests.assert_refused
    truth = ReceiverAdmissionExecutionEvidenceTests.truth

    def setUp(self, *, accepted_origin=None):
        super().setUp(accepted_origin=accepted_origin)
        self.database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]

    def desired_service(self, factory=None):
        return DesiredGraphCommandService(factory or self.unit_of_work,
            clock=lambda: decode_postgres_timestamp(datetime.now(timezone.utc)),
            id_factory=lambda: uuid.uuid4().hex)

    def desired_receiver(self, suffix, *, graph=None):
        command = self.receiver_command()
        command = replace(command, idempotency_key=IdempotencyKey("receiver-" + suffix),
            graph=command.graph if graph is None else graph)
        return self.desired_service().execute(command)

    def receiver_origin(self):
        with self.unit_of_work() as uow:
            return uow.stores.graphs.receiver_introduction("workspace-a", "a" * 32)

    def ready_run(self, suffix="a"):
        claimed = self.claim(suffix)
        self.lifecycle("ready-event-" + suffix, "ready-action-" + suffix).execute(StartActivityRun(
            claimed.run.run_id, ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
            claimed.request.claim.fence, IdempotencyKey("ready-" + suffix)))
        return claimed

    def coordinator(self, factory, adapter, suffix):
        ids = GeneratedIds("coordinator-" + suffix)
        clock = lambda: decode_postgres_timestamp(datetime.now(timezone.utc))
        fold = EffectAttemptFoldService(factory, id_factory=ids)
        return ExecutionCoordinator(factory,
            lifecycle=RunLifecycleCommandService(factory, clock=clock, id_factory=ids),
            adapter=adapter, start_service=EffectAttemptStartService(factory, id_factory=ids),
            fold_service=fold,
            reconciliation_service=EffectAttemptReconciliationService(factory, FailIfObserver(), fold),
            clock=clock, id_factory=ids)

    def execution_command(self, claimed, suffix):
        return ExecuteActivityRun(claimed.run.run_id,
            ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
            ExecutionLeaseFence("worker-a", claimed.request.claim.generation),
            IdempotencyKey("coordinator-" + suffix), max_effects=1)

    def native_start_command(self, claimed, suffix="native", *, activity_id=None):
        # Read the real coordinator's pinned context and existing translator.
        # Scheduling tests call the single-UoW start owner directly, so their
        # PostgreSQL blocker identifies the actual lifecycle-lock connection.
        from tests.postgres_effect_attempt_coordinator_fixture import RecordingRuntimeAdapter
        coordinator = self.coordinator(self.unit_of_work, RecordingRuntimeAdapter(), suffix)
        command = self.execution_command(claimed, suffix)
        context = load_execution_context(coordinator, command)
        activity = context.plan.activities[0] if activity_id is None else context.plan.activity(ActivityId(activity_id))
        intent = _runtime_effect_intent_for_context(context, activity)
        transition = EffectAttemptTransition(EffectAttemptTransitionKind.STARTED,
            EffectAttemptIdentity(intent.source.run_id, activity.activity_id.value, 1),
            request_fingerprint=runtime_effect_intent_fingerprint(intent))
        return StartEffectAttempt(context.request.identity.request_id, transition,
            intent, command.authority, command.fence)

    def execution_truth(self):
        return {table: self.connection.execute("SELECT to_jsonb(t) FROM " + table
            + " AS t ORDER BY to_jsonb(t)::text").fetchall() for table in (
                "cpk_execution_requests", "cpk_activity_runs", "cpk_activity_events",
                "cpk_operation_actions", "cpk_execution_receiver_scopes", "cpk_effect_attempts",
                "cpk_effect_attempt_intents", "cpk_effect_attempt_outcomes")}
