"""#1902 real admission setup; no alternate admission or scope implementation."""

from dataclasses import replace
from datetime import datetime, timezone
from importlib import import_module
from importlib.util import find_spec

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.planning import ActivityId, ActivityPlan, PlannedActivity
from control_plane_kit_core.products import ProductInstanceConfiguration, instantiate_product
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.operations import RunId, RecoveryScope
from control_plane_kit_core.runtime_effect_observation import RuntimeEffectIntent, RuntimeEffectIntentSource
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_core.topology import compile_topology
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.advancement import AdvanceCurrentGraph, CurrentGraphAdvancementCommandService
from control_plane_kit_operations.coordinator import ExecuteActivityRun, ExecutionCoordinator
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.execution_lease_recovery import RecoveryAuthority
from control_plane_kit_operations.failed_run_compensation import (
    BeginFailedRunCompensation, FailedRunCompensationCommandService, FailedRunCompensationReason,
)
from control_plane_kit_operations.failed_run_compensation_attempt import (
    FailedRunCompensationAttemptStartService, StartFailedRunCompensationAttempt,
)
from control_plane_kit_operations.lifecycle import (
    CancelActivityRun, ClaimAndOpenActivityRun, ExecutionLeaseDuration,
    ExecutionWorkerAuthority, RunLifecycleCommandService, StartActivityRun,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.products import InlineDescriptorSource
from control_plane_kit_operations.records import (
    ExecutionRequestIdentity, RealizedGraphProjectionRecord,
)
from tests import test_execution_admission as admission_tests
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds, RecordingRuntimeAdapter
from tests.postgres_effect_attempt_reconciliation_fixture import FailIfObserver


SCOPES = "cpk_execution_receiver_scopes"


class ReceiverExecutionScopeFixture:
    # Reuse existing setup functions without inheriting or recollecting its tests.
    tearDown = admission_tests.ExecutionAdmissionTests.tearDown
    unit_of_work = admission_tests.ExecutionAdmissionTests.unit_of_work
    operation_service = admission_tests.ExecutionAdmissionTests.operation_service
    admission_service = admission_tests.ExecutionAdmissionTests.admission_service
    command = admission_tests.ExecutionAdmissionTests.command
    seed_graphs = admission_tests.ExecutionAdmissionTests.seed_graphs
    seed_plan_truth = admission_tests.ExecutionAdmissionTests.seed_plan_truth
    product = admission_tests.ExecutionAdmissionTests.product
    product_graph = admission_tests.ExecutionAdmissionTests.product_graph
    empty_graph = admission_tests.ExecutionAdmissionTests.empty_graph

    def setUp(self):
        admission_tests.ExecutionAdmissionTests.setUp(self)
        with self.unit_of_work() as uow:
            uow.stores.registered_products.register(workspace_id="workspace-a",
                descriptor_document=self.document, source=InlineDescriptorSource(),
                imported_by="operator-a", imported_at="2026-07-22T12:01:30Z")
            uow.commit()

    def require_scopes(self):
        name = "control_plane_kit_operations.receiver_execution_scopes"
        self.assertIsNotNone(find_spec(name), "#1902 execution scope derivation/evidence is missing")
        return import_module(name)

    def require_catalog(self):
        relation = self.connection.execute("SELECT to_regclass(%s)", (SCOPES,)).fetchone()[0]
        self.assertIsNotNone(relation, "#1902 immutable execution scope relation is missing")

    def source(self, *, request_id="execution-a"):
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan("plan-a")
            base = uow.stores.realized_graphs.get(plan.base_realized_projection_id)
            desired = uow.stores.realized_graphs.get(plan.desired_realized_projection_id)
        identity = ExecutionRequestIdentity(request_id, "workspace-a", "session-a", "plan-a")
        return identity, plan, base, desired

    def source_with_operations(self, *operations, base_graph=None, desired_graph=None):
        identity, plan, base, desired = self.source()
        if base_graph is not None:
            base = self.projection(base, base_graph)
        if desired_graph is not None:
            desired = self.projection(desired, desired_graph)
        plan = replace(plan, plan=ActivityPlan(tuple(
            PlannedActivity(ActivityId("scope-" + str(i)), operation)
            for i, operation in enumerate(operations)
        )))
        return identity, plan, base, desired

    @staticmethod
    def projection(original, graph):
        return RealizedGraphProjectionRecord.from_graph(
            projection_id=original.projection_id, workspace_id=original.workspace_id,
            source_authored_graph_id=original.source_authored_graph_id,
            projection_kind=original.projection_kind, projection_key=original.projection_key,
            graph=graph, created_by=original.created_by, created_at=original.created_at,
        )

    def graph(self, *, runtime="docker", nodes=("app",)):
        blocks = tuple(instantiate_product(self.document.product, node,
                       ProductInstanceConfiguration()) for node in nodes)
        return compile_topology(DeploymentTopology("scope-material",
                                DockerRuntime(runtime_id=runtime, children=blocks)))

    def admit(self, suffix="a"):
        return self.admission_service("execution-" + suffix, "action-execute-" + suffix).execute(
            self.command(key="execute-" + suffix))

    def admit_operations(self, suffix, *operations):
        _, plan, _, _ = self.source_with_operations(*operations)
        self.seed_plan_truth(plan_id="plan-" + suffix, approval_request_id="approval-" + suffix,
                             approval_decision_id="decision-" + suffix, plan=plan.plan)
        return self.admission_service("execution-" + suffix, "action-execute-" + suffix).execute(
            self.command(plan_id="plan-" + suffix, approval_request_id="approval-" + suffix,
                         key="execute-" + suffix))

    def lifecycle(self, *ids):
        return RunLifecycleCommandService(self.unit_of_work,
            clock=lambda: datetime.now(timezone.utc).isoformat(),
            id_factory=admission_tests.Sequence(*ids))

    def claim(self, suffix="a", *, duration_seconds=600):
        return self.lifecycle("run-" + suffix, "event-open-" + suffix, "action-open-" + suffix).execute(
            ClaimAndOpenActivityRun("execution-" + suffix,
                ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
                ExecutionLeaseDuration(duration_seconds), IdempotencyKey("claim-" + suffix)))

    def cancel(self, claimed, suffix="a"):
        return self.lifecycle("event-cancel-" + suffix, "action-cancel-" + suffix).execute(
            CancelActivityRun(claimed.run.run_id,
                ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
                ExecutionLeaseFence("worker-a", claimed.request.claim.generation),
                IdempotencyKey("cancel-" + suffix)))

    def execute_effects(self, suffix, adapter=None):
        """Compose existing production services; the adapter performs no I/O."""
        claimed = self.claim(suffix)
        return self.execute_claimed_effects(claimed, suffix, adapter)

    def execute_claimed_effects(self, claimed, suffix, adapter=None):
        authority = ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,))
        fence = ExecutionLeaseFence("worker-a", claimed.request.claim.generation)
        self.lifecycle("event-start-" + suffix, "action-start-" + suffix).execute(
            StartActivityRun(claimed.run.run_id, authority, fence, IdempotencyKey("start-" + suffix)))
        ids = GeneratedIds("effect-" + suffix)
        clock = lambda: datetime.now(timezone.utc).isoformat()
        fold = EffectAttemptFoldService(self.unit_of_work, id_factory=ids)
        coordinator = ExecutionCoordinator(self.unit_of_work,
            lifecycle=RunLifecycleCommandService(self.unit_of_work, clock=clock, id_factory=ids),
            adapter=adapter or RecordingRuntimeAdapter(),
            start_service=EffectAttemptStartService(self.unit_of_work, id_factory=ids),
            fold_service=fold,
            reconciliation_service=EffectAttemptReconciliationService(self.unit_of_work, FailIfObserver(), fold),
            clock=clock, id_factory=ids)
        result = coordinator.execute(ExecuteActivityRun(claimed.run.run_id, authority, fence,
            IdempotencyKey("execute-effects-" + suffix), max_effects=1024))
        return claimed, result

    def advance(self, claimed, suffix):
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan(claimed.request.identity.plan_id)
        return CurrentGraphAdvancementCommandService(self.unit_of_work,
            clock=lambda: datetime.now(timezone.utc).isoformat(),
            id_factory=admission_tests.Sequence("event-advance-" + suffix, "action-advance-" + suffix)).execute(
            AdvanceCurrentGraph(workspace_id="workspace-a", run_id=claimed.run.run_id,
                plan_id=plan.plan_id, expected_current_graph_id=plan.base_graph_id,
                expected_current_realized_projection_id=plan.base_realized_projection_id,
                desired_graph_id=plan.desired_graph_id,
                desired_realized_projection_id=plan.desired_realized_projection_id,
                expected_desired_graph_revision=plan.desired_graph_revision,
                authority=ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
                fence=ExecutionLeaseFence("worker-a", claimed.request.claim.generation),
                idempotency_key=IdempotencyKey("advance-" + suffix)))

    def begin_compensation(self, claimed, suffix):
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan(claimed.request.identity.plan_id)
            failure = uow.stores.execution.events_for_run(claimed.run.run_id)[-1].failure
        return FailedRunCompensationCommandService(self.unit_of_work,
            clock=lambda: datetime.now(timezone.utc).isoformat(), id_factory=GeneratedIds("compensate-" + suffix)).execute(
            BeginFailedRunCompensation(workspace_id="workspace-a", request_id=claimed.request.identity.request_id,
                run_id=RunId(claimed.run.run_id), plan_id=plan.plan_id,
                expected_current_graph_id=plan.base_graph_id, desired_graph_id=plan.desired_graph_id,
                expected_desired_graph_revision=plan.desired_graph_revision,
                execution_intent_fingerprint=claimed.request.idempotency.intent_fingerprint,
                authority=RecoveryAuthority("operator-a", "test-recovery-authority", (RecoveryScope.COMPENSATE,)),
                reason=FailedRunCompensationReason.POST_EFFECT_FAILURE, source_failure=failure,
                idempotency_key=IdempotencyKey("compensate-" + suffix)))

    def start_inverse(self, claimed, compensation, suffix, *, position=1):
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan(claimed.request.identity.plan_id)
        step = compensation.program.steps[position - 1]
        intent = RuntimeEffectIntent(kind=RuntimeEffectKind.REALIZE_ACTIVITY, runtime_kind=RuntimeKind.DOCKER,
            source=RuntimeEffectIntentSource("workspace-a", claimed.request.identity.request_id,
                RunId(claimed.run.run_id), plan.plan_id, plan.base_graph_id, plan.desired_graph_id),
            activity_id=ActivityId(step.source_effect.attempt_identity.activity_id), operation=step.operation,
            authority_ref=None, authority_deliveries=(), products=())
        return FailedRunCompensationAttemptStartService(self.unit_of_work, id_factory=GeneratedIds("inverse-" + suffix)).execute(
            StartFailedRunCompensationAttempt(compensation.record.program_id, position, intent,
                ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
                ExecutionLeaseFence("worker-a", claimed.request.claim.generation)))

    def scope_rows(self, request="execution-a"):
        return self.connection.execute(
            "SELECT scope_ordinal,scope_kind,runtime_id,node_id FROM cpk_execution_receiver_scopes "
            "WHERE request_id=%s ORDER BY scope_ordinal", (request,),
        ).fetchall()

    def scope_header(self, request="execution-a"):
        return self.connection.execute(
            "SELECT receiver_scope_count,receiver_scope_digest FROM cpk_execution_requests "
            "WHERE request_id=%s", (request,),
        ).fetchone()

    def evidence(self, *scopes, workspace="workspace-a"):
        module = self.require_scopes()
        with self.unit_of_work() as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle(workspace)
            evidence = uow.stores.execution.receiver_scope_evidence(workspace, tuple(scopes), guard)
        return module.classify_receiver_scope_evidence(evidence)
