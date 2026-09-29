"""#1902 shared all-history row ceilings, using existing semantic owners."""

import unittest

from control_plane_kit_core.operations import ActivityEventKind, ActivityRunStatus, RunId
from control_plane_kit_core.operations.lifecycle import RecoveryScope
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.planning import NodeTarget, StartNode
from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
from control_plane_kit_operations.activity_run_retry import RetryFailedActivityRun
from control_plane_kit_operations.activity_run_retry_interpreter import ActivityRunRetryCommandService
from control_plane_kit_operations.execution_lease_recovery import RecoveryAuthority
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority, PauseActivityRun, StartActivityRun
from control_plane_kit_operations.records import ActivityEventRecord
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds, RecordingRuntimeAdapter
from tests.receiver_execution_scope_fixture import ReceiverExecutionScopeFixture


def failed_adapter():
    return RecordingRuntimeAdapter(lambda context, request: RuntimeEffectResult.failed(
        request.effect_id, RuntimeEffectFailure("budget-fixture-failure", "bounded recorded failure")))


class ReceiverExecutionScopeRowBudgetTests(ReceiverExecutionScopeFixture, unittest.TestCase):
    def test_exact_original_retry_preserves_coverage_while_competing_scope_remains_occupied(self):
        module = self.require_scopes()
        self.admit_operations("retry", StartNode(NodeTarget("app")))
        claimed, failed = self.execute_effects("retry", failed_adapter())
        self.assertEqual(failed.run.status, ActivityRunStatus.FAILED)
        before = self.scope_header("execution-retry"), self.scope_rows("execution-retry")
        scope = module.ExecutionReceiverScope("docker", "app")
        self.assertEqual(self.evidence(scope).disposition, "conflict")
        retried = self.retry(claimed, "lawful-original")
        self.assertEqual(retried.run.status, ActivityRunStatus.CLAIMED)
        self.assertEqual((self.scope_header("execution-retry"), self.scope_rows("execution-retry")), before)
        after = self.evidence(scope)
        self.assertEqual(after.disposition, "conflict")
        self.assertEqual(set(after.run_ids), {claimed.run.run_id, retried.run.run_id})

    def test_compensation_steps_and_bindings_consume_the_same_effect_budget(self):
        module = self.require_scopes()
        self.admit_operations("compensation-budget", *(StartNode(NodeTarget("app")) for _ in range(682)))
        remaining = [681]

        def fail_after_successes(context, request):
            if remaining[0]:
                remaining[0] -= 1
                return RuntimeEffectResult.succeeded(request.effect_id)
            return RuntimeEffectResult.failed(request.effect_id, RuntimeEffectFailure("last-failed", "bounded final failure"))

        adapter = RecordingRuntimeAdapter(*(fail_after_successes for _ in range(682)))
        claimed, failed = self.execute_claimed_effects(self.claim("compensation-budget", duration_seconds=3600),
                                                     "compensation-budget", adapter)
        self.assertEqual(failed.run.status, ActivityRunStatus.FAILED)
        compensation = self.begin_compensation(claimed, "budget")
        self.assertEqual(len(compensation.program.steps), 681)
        # 682 attempts + 682 independent intents +681 program steps =2045.
        inverse = self.start_inverse(claimed, compensation, "budget-one")
        # First inverse contributes attempt + intent + binding, exactly2048.
        self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition, "conflict")
        # Finish this inverse through the existing fold owner before starting
        # the next ordered program step. No whole-run disposal is inferred.
        from control_plane_kit_operations.effect_attempt_fold import FoldEffectAttempt
        from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
        from control_plane_kit_operations.effect_outcome_evidence import ExecutionEffectOutcome, effect_outcome_transition
        outcome = ExecutionEffectOutcome(inverse.binding.inverse_attempt, inverse.intent.request_fingerprint,
                                         RuntimeEffectResult.succeeded("budget-inverse"))
        EffectAttemptFoldService(self.unit_of_work, id_factory=GeneratedIds("budget-inverse-fold")).execute(
            FoldEffectAttempt(claimed.request.identity.request_id, effect_outcome_transition(outcome),
                ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
                ExecutionLeaseFence("worker-a", claimed.request.claim.generation), None, outcome))
        self.start_inverse(claimed, compensation, "budget-two", position=2)
        # A lawful atomic inverse start adds three inseparable evidence rows.
        self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition, "capacity")

    def test_8192_journal_events_fit_and_8193_refuse_without_partial_history(self):
        module = self.require_scopes()
        self.admit_operations("events", StartNode(NodeTarget("app")))
        claimed = self.claim("events", duration_seconds=3600)
        authority = ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,))
        fence = ExecutionLeaseFence("worker-a", claimed.request.claim.generation)
        started = self.lifecycle("events-started", "events-start-action").execute(
            StartActivityRun("run-events", authority, fence, IdempotencyKey("events-start")))
        # Recorded pause/resume history is the fixture data under read test.
        # Keep real admission/open/start and the current RUNNING state; no
        # effect attempts or pretend provider outcomes are manufactured.
        with self.unit_of_work() as uow:
            for ordinal in range(3, 8193):
                kind = ActivityEventKind.RUN_PAUSED if ordinal % 2 else ActivityEventKind.RUN_RESUMED
                uow.stores.execution.add_event(ActivityEventRecord(f"history-{ordinal}", "run-events",
                    ordinal, kind, started.event.occurred_at))
            uow.commit()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_activity_events WHERE run_id='run-events'").fetchone()[0], 8192)
        self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition, "conflict")
        self.lifecycle("events-overflow", "events-overflow-action").execute(
            PauseActivityRun("run-events", authority, fence, IdempotencyKey("events-overflow")))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_activity_events WHERE run_id='run-events'").fetchone()[0], 8193)
        self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition, "capacity")

    def retry(self, previous, suffix):
        return ActivityRunRetryCommandService(self.unit_of_work, id_factory=GeneratedIds("retry-" + suffix)).execute(
            RetryFailedActivityRun(previous.request.identity.request_id, RunId(previous.run.run_id),
                ExecutionLeaseFence("worker-a", previous.request.claim.generation),
                RecoveryAuthority("operator-a", "test-recovery-authority", (RecoveryScope.OPERATE,)),
                IdempotencyKey("retry-" + suffix)))

    def test_256_original_runs_fit_and_257_refuse_without_latest_run_shortcut(self):
        module = self.require_scopes()
        last = None
        for request in range(64):
            suffix = "runs-" + str(request)
            self.admit_operations(suffix, StartNode(NodeTarget("app")))
            claimed = self.claim(suffix, duration_seconds=3600)
            for attempt in range(4):
                if attempt:
                    claimed = self.retry(claimed, f"{request}-{attempt}")
                _, failed = self.execute_claimed_effects(claimed, f"{request}-{attempt}", failed_adapter())
                self.assertEqual(failed.run.status, ActivityRunStatus.FAILED)
                # The retry command uses the retained identity/fence and reads
                # the actual failed run itself; no fixture status transition.
            last = claimed
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_activity_runs").fetchone()[0], 256)
        result = self.evidence(module.ExecutionReceiverScope("docker", "app"))
        self.assertEqual(result.disposition, "conflict")
        self.assertEqual(len(result.request_ids), 64)
        self.assertEqual(len(result.run_ids), 256)
        self.retry(last, "overflow")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_activity_runs").fetchone()[0], 257)
        self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition, "capacity")

    def test_attempts_and_independent_intents_share_2048_row_budget(self):
        module = self.require_scopes()
        self.admit_operations("effects", *(StartNode(NodeTarget("app")) for _ in range(1024)))
        _, complete = self.execute_claimed_effects(self.claim("effects", duration_seconds=3600), "effects")
        self.assertEqual(complete.run.status, ActivityRunStatus.SUCCEEDED)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 1024)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_intents").fetchone()[0], 1024)
        self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition, "conflict")
        # Inspect one extra independently retained intent, after its real start
        # commits and before an outcome exists. The only corruption is a rolled-
        # back attempt deletion; the extra intent remains a real original row.
        self.admit_operations("zz-overflow", StartNode(NodeTarget("app")))
        observed = []

        def inspect_intent_overflow(context, request):
            with self.unit_of_work() as uow:
                guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                connection = uow.stores.connection
                self.assertEqual(connection.execute("DELETE FROM cpk_effect_attempts WHERE run_id='run-zz-overflow'").rowcount, 1)
                counts = connection.execute("SELECT (SELECT count(*) FROM cpk_effect_attempts) + (SELECT count(*) FROM cpk_effect_attempt_intents)").fetchone()[0]
                self.assertEqual(counts, 2049)
                evidence = uow.stores.execution.receiver_scope_evidence("workspace-a",
                    (module.ExecutionReceiverScope("docker", "app"),), guard)
                observed.append(module.classify_receiver_scope_evidence(evidence).disposition)
            return RuntimeEffectResult.succeeded(request.effect_id)

        self.execute_effects("zz-overflow", RecordingRuntimeAdapter(inspect_intent_overflow))
        self.assertEqual(observed, ["capacity"])
        self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition, "capacity")
