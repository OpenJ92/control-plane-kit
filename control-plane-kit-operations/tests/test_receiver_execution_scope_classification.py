"""#1902 pure evidence disposition; C-N11 never earns C3 closure here."""

import unittest
from psycopg.types.json import Jsonb

from control_plane_kit_core.operations import (
    ActivityRunStatus, EffectAttemptIdentity, EffectAttemptTransition,
    EffectAttemptTransitionKind, EffectRecoveryDecision, EffectRecoveryResolution,
    FailureCategory, RunId,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.planning import NodeTarget, RuntimeTarget, StartNode, StartRuntime, WaitForHealthy
from control_plane_kit_core.planning import SocketConnectionTarget, SwitchSocketConnection
from control_plane_kit_operations.coordinator import ActivityExecutionOutcome
from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
from control_plane_kit_operations.effect_attempt_fold import FoldEffectAttempt
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_outcome_evidence import ExecutionEffectOutcome, effect_outcome_transition
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority, PauseActivityRun, ResumeActivityRun, StartActivityRun
from control_plane_kit_operations.records import FailureEvidence
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.receiver_execution_scope_fixture import ReceiverExecutionScopeFixture
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds, RecordingRuntimeAdapter


class ReceiverExecutionScopeClassificationTests(ReceiverExecutionScopeFixture, unittest.TestCase):
    def test_real_active_renewal_then_cancel_retains_complete_nondispatch_history(self):
        from dataclasses import replace
        from control_plane_kit_core.operations import RecoveryScope
        from control_plane_kit_operations.execution_lease_recovery import RecoveryAuthority, RenewActiveExecutionClaim
        from control_plane_kit_operations.execution_lease_recovery_interpreter import ExecutionLeaseRecoveryCommandService
        from control_plane_kit_operations.lifecycle import CancelActivityRun, ExecutionLeaseDuration
        module = self.require_scopes()
        self.admit()
        claimed = self.claim()
        renewed = ExecutionLeaseRecoveryCommandService(self.unit_of_work,
            id_factory=GeneratedIds("renew-before-cancel")).execute(RenewActiveExecutionClaim(
                "execution-a", RunId("run-a"), ExecutionLeaseFence("worker-a", claimed.request.claim.generation),
                RecoveryAuthority("operator-a", "test-recovery-authority", (RecoveryScope.RENEW_CLAIM,)),
                ExecutionLeaseDuration(600), IdempotencyKey("renew-before-cancel")))
        self.assertGreater(renewed.request.claim.generation, claimed.request.claim.generation)
        cancelled = self.lifecycle("renew-cancel-event", "renew-cancel-action").execute(CancelActivityRun(
            "run-a", ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
            renewed.request.claim.fence, IdempotencyKey("renew-cancel")))
        scope = module.ExecutionReceiverScope("docker", "app")
        self.assertEqual(self.evidence(scope).disposition, "requires-fresh-gate-closure")
        # Deliberate retained corruption: an internally congruent additional
        # renewal pair cannot be erased after the original terminal cancellation.
        with self.unit_of_work() as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            recovery = replace(renewed.decision_event.recovery, prior_fence=renewed.request.claim.fence,
                replacement_fence=ExecutionLeaseFence("worker-a", renewed.request.claim.generation + 1))
            uow.stores.execution.add_event(replace(renewed.decision_event,
                event_id="post-cancel-decision", ordinal=cancelled.event.ordinal + 1,
                occurred_at=cancelled.event.occurred_at, recovery=recovery))
            uow.stores.execution.add_event(replace(renewed.consequence_event,
                event_id="post-cancel-consequence", ordinal=cancelled.event.ordinal + 2,
                occurred_at=cancelled.event.occurred_at))
            evidence = uow.stores.execution.receiver_scope_evidence("workspace-a", (scope,), guard)
            self.assertEqual(module.classify_receiver_scope_evidence(evidence).disposition, "unavailable")
        self.assertEqual(self.evidence(scope).disposition, "requires-fresh-gate-closure")
        with self.unit_of_work() as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            self.assertEqual(uow.stores.connection.execute(
                "UPDATE cpk_activity_events SET event_type='request_claim_taken_over' WHERE event_id=%s",
                (renewed.consequence_event.event_id,)).rowcount, 1)
            evidence = uow.stores.execution.receiver_scope_evidence("workspace-a", (scope,), guard)
            self.assertEqual(module.classify_receiver_scope_evidence(evidence).disposition, "unavailable")
        self.assertEqual(self.evidence(scope).disposition, "requires-fresh-gate-closure")

    def test_retry_chain_rejects_foreign_predecessor_gap_and_backward_time(self):
        module = self.require_scopes()
        self.admit_operations("chain", StartNode(NodeTarget("app")))
        failed_adapter = lambda: RecordingRuntimeAdapter(lambda context, request: RuntimeEffectResult.failed(
            request.effect_id, RuntimeEffectFailure("chain-failure", "bounded failure")))
        original, failed = self.execute_effects("chain", failed_adapter())
        self.assertEqual(failed.run.status, ActivityRunStatus.FAILED)
        retried = self.retry(original, "chain")
        self.admit_operations("foreign-chain", StartNode(NodeTarget("app")))
        foreign, _ = self.execute_effects("foreign-chain", failed_adapter())
        scope = module.ExecutionReceiverScope("docker", "app")
        self.assertEqual(self.evidence(scope).disposition, "conflict")
        mutations = (
            ("prior_run_id=%s", (foreign.run.run_id,)),
            ("attempt=%s", (3,)),
            ("created_at=%s", ("1999-01-01T00:00:00Z",)),
        )
        for assignment, values in mutations:
            with self.subTest(assignment=assignment), self.unit_of_work() as uow:
                guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                self.assertEqual(uow.stores.connection.execute(
                    "UPDATE cpk_activity_runs SET " + assignment + " WHERE run_id=%s",
                    (*values, retried.run.run_id)).rowcount, 1)
                evidence = uow.stores.execution.receiver_scope_evidence("workspace-a", (scope,), guard)
                self.assertEqual(module.classify_receiver_scope_evidence(evidence).disposition, "unavailable")
        self.assertEqual(self.evidence(scope).disposition, "conflict")

    def test_cancel_witness_without_original_opening_is_unavailable(self):
        module = self.require_scopes()
        self.admit()
        self.cancel(self.claim())
        scope = module.ExecutionReceiverScope("docker", "app")
        self.assertEqual(self.evidence(scope).disposition, "requires-fresh-gate-closure")
        with self.unit_of_work() as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            self.assertEqual(uow.stores.connection.execute(
                "UPDATE cpk_activity_events SET event_type='run_resumed' "
                "WHERE run_id='run-a' AND event_type='run_opened'").rowcount, 1)
            evidence = uow.stores.execution.receiver_scope_evidence("workspace-a", (scope,), guard)
            self.assertEqual(module.classify_receiver_scope_evidence(evidence).disposition, "unavailable")
        self.assertEqual(self.evidence(scope).disposition, "requires-fresh-gate-closure")

    def test_cancel_after_lawful_pause_resume_without_dispatch_keeps_closure_obligation(self):
        module = self.require_scopes()
        self.admit()
        claimed = self.claim()
        authority = ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,))
        fence = ExecutionLeaseFence("worker-a", claimed.request.claim.generation)
        for suffix, command in (("start", StartActivityRun), ("pause", PauseActivityRun),
                                ("resume", ResumeActivityRun), ("pause-again", PauseActivityRun)):
            self.lifecycle("envelope-event-" + suffix, "envelope-action-" + suffix).execute(
                command(claimed.run.run_id, authority, fence, IdempotencyKey("envelope-" + suffix)))
        self.cancel(claimed)
        self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition,
                         "requires-fresh-gate-closure")

    def test_missing_required_direct_outcome_is_unavailable_not_recovered_evidence(self):
        module = self.require_scopes()
        self.admit_operations("missing-outcome", StartNode(NodeTarget("app")))
        _, completed = self.execute_effects("missing-outcome")
        self.assertEqual(completed.run.status, ActivityRunStatus.SUCCEEDED)
        scope = module.ExecutionReceiverScope("docker", "app")
        self.assertEqual(self.evidence(scope).disposition, "conflict")
        with self.unit_of_work() as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            # The no-observation adapter created no dependent observation rows.
            # Delete only its direct outcome inside this rolled-back corruption.
            self.assertEqual(uow.stores.connection.execute(
                "DELETE FROM cpk_effect_attempt_outcomes WHERE run_id='run-missing-outcome'").rowcount, 1)
            evidence = uow.stores.execution.receiver_scope_evidence("workspace-a", (scope,), guard)
            self.assertEqual(module.classify_receiver_scope_evidence(evidence).disposition, "unavailable")
        self.assertEqual(self.evidence(scope).disposition, "conflict")

    def test_cancellation_after_uncertain_dispatch_does_not_prove_no_dispatch(self):
        module = self.require_scopes()
        self.admit_operations("dispatched", StartNode(NodeTarget("app")))
        adapter = RecordingRuntimeAdapter(lambda context, request: RuntimeEffectResult.uncertain(
            request.effect_id, RuntimeEffectFailure("lost-result", "bounded uncertain dispatch")))
        claimed, _ = self.execute_effects("dispatched", adapter)
        self.lifecycle("event-pause-dispatched", "action-pause-dispatched").execute(
            PauseActivityRun(claimed.run.run_id,
                ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
                ExecutionLeaseFence("worker-a", claimed.request.claim.generation),
                IdempotencyKey("pause-dispatched")))
        cancelled = self.cancel(claimed, "dispatched")
        self.assertEqual(cancelled.run.status, ActivityRunStatus.CANCELLED)
        result = self.evidence(module.ExecutionReceiverScope("docker", "app"))
        self.assertEqual(result.disposition, "conflict")
        self.assertIn("run-dispatched", result.run_ids)

    def test_later_accepted_retry_does_not_account_for_older_run_of_same_request(self):
        module = self.require_scopes()
        self.admit_operations("same-request", StartNode(NodeTarget("app")))
        adapter = RecordingRuntimeAdapter(lambda context, request: RuntimeEffectResult.failed(
            request.effect_id, RuntimeEffectFailure("first-failed", "bounded earlier failure")))
        claimed, first = self.execute_effects("same-request", adapter)
        self.assertEqual(first.run.status, ActivityRunStatus.FAILED)
        retried = self.retry(claimed, "same-request")
        _, completed = self.execute_claimed_effects(retried, "same-request-retry")
        self.assertEqual(completed.run.status, ActivityRunStatus.SUCCEEDED)
        self.advance(retried, "same-request-retry")
        result = self.evidence(module.ExecutionReceiverScope("docker", "app"))
        self.assertEqual(result.disposition, "conflict")
        self.assertEqual(result.request_ids, ("execution-same-request",))
        self.assertIn(claimed.run.run_id, result.run_ids)

    def test_successful_inverse_does_not_invent_whole_run_disposal(self):
        module = self.require_scopes()
        self.admit_operations("inverse", StartNode(NodeTarget("app")), StartNode(NodeTarget("app")))
        adapter = RecordingRuntimeAdapter(
            lambda context, request: RuntimeEffectResult.succeeded(request.effect_id),
            lambda context, request: RuntimeEffectResult.failed(request.effect_id,
                RuntimeEffectFailure("after-success", "bounded later failure")))
        claimed, failed = self.execute_effects("inverse", adapter)
        self.assertEqual(failed.run.status, ActivityRunStatus.FAILED)
        compensation = self.begin_compensation(claimed, "inverse")
        self.assertEqual(len(compensation.program.steps), 1)
        inverse = self.start_inverse(claimed, compensation, "inverse")
        outcome = ExecutionEffectOutcome(inverse.binding.inverse_attempt, inverse.intent.request_fingerprint,
                                         RuntimeEffectResult.succeeded(inverse.attempt.original_start_event.event_id))
        EffectAttemptFoldService(self.unit_of_work, id_factory=GeneratedIds("inverse-fold")).execute(
            FoldEffectAttempt("execution-inverse", effect_outcome_transition(outcome),
                ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
                ExecutionLeaseFence("worker-a", claimed.request.claim.generation), None, outcome))
        result = self.evidence(module.ExecutionReceiverScope("docker", "app"))
        self.assertEqual(result.disposition, "conflict")
        self.assertIn("run-inverse", result.run_ids)

    def test_independent_orphan_intent_refuses_even_when_attempt_prefix_is_empty(self):
        module = self.require_scopes()
        self.admit_operations("orphan", StartNode(NodeTarget("app")))
        observed = []

        def inspect_orphan(context, request):
            # Start service has committed a real intent/attempt/start event.
            # The adapter has not returned an outcome, so no outcome FK is
            # removed or bypassed. Corruption lives only in this rolled-back UoW.
            with self.unit_of_work() as uow:
                guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                connection = uow.stores.connection
                self.assertEqual(connection.execute("SELECT count(*) FROM cpk_effect_attempt_intents WHERE run_id='run-orphan'").fetchone()[0], 1)
                self.assertEqual(connection.execute("DELETE FROM cpk_effect_attempts WHERE run_id='run-orphan'").rowcount, 1)
                evidence = uow.stores.execution.receiver_scope_evidence("workspace-a",
                    (module.ExecutionReceiverScope("docker", "app"),), guard)
                observed.append(module.classify_receiver_scope_evidence(evidence).disposition)
            return RuntimeEffectResult.succeeded(request.effect_id)

        self.execute_effects("orphan", RecordingRuntimeAdapter(inspect_orphan))
        self.assertEqual(observed, ["unavailable"])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts WHERE run_id='run-orphan'").fetchone()[0], 1)

    def test_recorded_recovery_without_new_direct_outcome_is_known_conflict_not_unavailable(self):
        module = self.require_scopes()
        self.admit_operations("recovered", StartNode(NodeTarget("app")))
        adapter = RecordingRuntimeAdapter(lambda context, request: RuntimeEffectResult.uncertain(
            request.effect_id, RuntimeEffectFailure("lost-response", "bounded uncertain result")))
        claimed, _ = self.execute_effects("recovered", adapter)
        with self.unit_of_work() as uow:
            row = uow.stores.connection.execute(
                "SELECT activity_id,attempt FROM cpk_effect_attempts WHERE run_id='run-recovered'").fetchone()
            identity = EffectAttemptIdentity(RunId("run-recovered"), row[0], row[1])
            attempt = uow.stores.effect_attempts.get(identity)
        # This is recorded recovery evidence in a package fixture; it makes no
        # assertion about actual external provider reality or scope disposal.
        decision = EffectRecoveryDecision("recovery-decision", identity,
            EffectRecoveryResolution.FAILED, attempt.state.outcome_fingerprint, "d" * 64)
        transition = EffectAttemptTransition(EffectAttemptTransitionKind.RECONCILED, identity,
                                            recovery_decision=decision)
        EffectAttemptFoldService(self.unit_of_work, id_factory=GeneratedIds("recovery")).execute(
            FoldEffectAttempt(request_id="execution-recovered", transition=transition,
                authority=ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
                fence=ExecutionLeaseFence("worker-a", claimed.request.claim.generation),
                failure=FailureEvidence(FailureCategory.TERMINAL, "recovered-failure", "bounded recorded failure"),
                outcome=None))
        result = self.evidence(module.ExecutionReceiverScope("docker", "app"))
        self.assertEqual(result.disposition, "conflict")
        self.assertIn("run-recovered", result.run_ids)
        self.assertNotIn("uncertain", result.reason)

    def test_complete_affecting_success_is_conflict_until_exact_acceptance(self):
        module = self.require_scopes()
        self.admit_operations("success", StartNode(NodeTarget("app")))
        claimed, completed = self.execute_effects("success")
        self.assertEqual(completed.run.status, ActivityRunStatus.SUCCEEDED)
        scope = module.ExecutionReceiverScope("docker", "app")
        before = self.evidence(scope)
        self.assertEqual(before.disposition, "conflict")
        self.assertEqual(before.run_ids, ("run-success",))
        self.advance(claimed, "success")
        self.assertEqual(self.evidence(scope).disposition, "nonconflicting")
        # Exact historical accounting survives later pointers; it is not a
        # fresh eligibility decision derived from today's graph selection.
        with self.unit_of_work() as uow:
            uow.stores.workspaces.set_desired_graph("workspace-a", "graph-current")
            uow.commit()
        self.assertEqual(self.evidence(scope).disposition, "nonconflicting")

    def test_mixed_effect_and_journal_only_operations_retain_exact_acceptance(self):
        module = self.require_scopes()
        self.admit_operations("mixed", StartNode(NodeTarget("app")),
                              SwitchSocketConnection(SocketConnectionTarget("edge-a")))
        adapter = RecordingRuntimeAdapter(
            lambda context, request: RuntimeEffectResult.succeeded(request.effect_id),
            ActivityExecutionOutcome.succeeded())
        claimed, completed = self.execute_effects("mixed", adapter)
        self.assertEqual(completed.run.status, ActivityRunStatus.SUCCEEDED)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual(len(adapter.legacy_calls), 1)
        scope = module.ExecutionReceiverScope("docker", "app")
        self.assertEqual(self.evidence(scope).disposition, "conflict")
        self.advance(claimed, "mixed")
        self.assertEqual(self.evidence(scope).disposition, "nonconflicting")

    def test_acceptance_receipt_requires_its_original_complete_success_journal(self):
        module = self.require_scopes()
        self.admit_operations("accepted-history", StartNode(NodeTarget("app")))
        claimed, completed = self.execute_effects("accepted-history")
        self.assertEqual(completed.run.status, ActivityRunStatus.SUCCEEDED)
        self.advance(claimed, "accepted-history")
        scope = module.ExecutionReceiverScope("docker", "app")
        self.assertEqual(self.evidence(scope).disposition, "nonconflicting")
        with self.unit_of_work() as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            self.assertEqual(uow.stores.connection.execute(
                "UPDATE cpk_activity_events SET event_type='run_resumed' "
                "WHERE run_id='run-accepted-history' AND event_type='run_succeeded'").rowcount, 1)
            evidence = uow.stores.execution.receiver_scope_evidence("workspace-a", (scope,), guard)
            self.assertEqual(module.classify_receiver_scope_evidence(evidence).disposition, "unavailable")
        self.assertEqual(self.evidence(scope).disposition, "nonconflicting")

    def test_known_failure_is_conflict_without_generic_disposal(self):
        module = self.require_scopes()
        self.admit_operations("failed", StartNode(NodeTarget("app")))
        adapter = RecordingRuntimeAdapter(lambda context, request: RuntimeEffectResult.failed(
            request.effect_id, RuntimeEffectFailure("provider-failed", "bounded failure")))
        _, failed = self.execute_effects("failed", adapter)
        self.assertEqual(failed.run.status, ActivityRunStatus.FAILED)
        result = self.evidence(module.ExecutionReceiverScope("docker", "app"))
        self.assertEqual(result.disposition, "conflict")
        self.assertEqual(result.run_ids, ("run-failed",))
        self.assertNotIn("uncertain", result.reason)

    def test_newer_acceptance_does_not_account_for_older_failed_request(self):
        module = self.require_scopes()
        self.admit_operations("old", StartNode(NodeTarget("app")))
        adapter = RecordingRuntimeAdapter(lambda context, request: RuntimeEffectResult.failed(
            request.effect_id, RuntimeEffectFailure("old-failure", "bounded old failure")))
        self.execute_effects("old", adapter)
        self.admit_operations("new", StartNode(NodeTarget("app")))
        claimed, completed = self.execute_effects("new")
        self.assertEqual(completed.run.status, ActivityRunStatus.SUCCEEDED)
        self.advance(claimed, "new")
        result = self.evidence(module.ExecutionReceiverScope("docker", "app"))
        self.assertEqual(result.disposition, "conflict")
        self.assertIn("run-old", result.run_ids)

    def test_runtime_wide_original_request_overlaps_any_node_on_that_runtime(self):
        module = self.require_scopes()
        self.admit_operations("runtime", StartRuntime(RuntimeTarget("docker")))
        result = self.evidence(module.ExecutionReceiverScope("docker", "historical-node"))
        self.assertEqual(result.disposition, "conflict")
        self.assertEqual(result.request_ids, ("execution-runtime",))
        self.assertEqual(self.evidence(module.ExecutionReceiverScope("another-runtime", "historical-node")).disposition,
                         "nonconflicting")

    def test_positive_observation_request_does_not_conflict_without_acceptance(self):
        module = self.require_scopes()
        self.admit_operations("observe", WaitForHealthy(NodeTarget("app")))
        self.assertEqual(self.scope_header("execution-observe")[0], 0)
        result = self.evidence(module.ExecutionReceiverScope("docker", None))
        self.assertEqual(result.disposition, "nonconflicting")
        self.assertEqual(result.request_ids, ())

    def test_exact_cancel_before_dispatch_yields_only_unresolved_closure_requirement(self):
        module = self.require_scopes()
        self.admit()
        cancelled = self.cancel(self.claim())
        # Existing cancel semantics set started_at even when CLAIMED; it must
        # not be mistaken for provider dispatch or required to be NULL.
        self.assertIsNotNone(cancelled.run.started_at)
        self.assertEqual(cancelled.run.started_at, cancelled.event.occurred_at)
        result = self.evidence(module.ExecutionReceiverScope("docker", "app"))
        self.assertEqual(result.disposition, "requires-fresh-gate-closure")
        self.assertEqual(result.request_ids, ("execution-a",))
        self.assertEqual(result.run_ids, ("run-a",))

    def test_cancelled_label_without_exact_action_is_unavailable(self):
        module = self.require_scopes()
        self.admit()
        cancelled = self.cancel(self.claim())
        # No FK requires this action; removing it deliberately models retained
        # incomplete evidence, never a lawful execution mutation.
        self.connection.execute("DELETE FROM cpk_operation_actions WHERE action_id=%s", (cancelled.action.action_id,))
        try:
            self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition, "unavailable")
        finally:
            with self.unit_of_work() as uow:
                uow.stores.activity_history.add_action(cancelled.action)
                uow.commit()

    def test_cancel_witness_must_match_every_original_identity_and_event_coordinate(self):
        module = self.require_scopes()
        self.admit()
        cancelled = self.cancel(self.claim())
        original = dict(cancelled.action.payload)
        for key, value in (("execution_request_id", "foreign-request"), ("plan_id", "foreign-plan"),
                           ("run_id", "foreign-run"), ("event_id", "foreign-event"),
                           ("event_type", "run_failed"), ("event_ordinal", 999), ("run_status", "failed")):
            with self.subTest(key=key):
                self.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE action_id=%s",
                                        (Jsonb({**original, key: value}), cancelled.action.action_id))
                try:
                    self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition,
                                     "unavailable")
                finally:
                    self.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE action_id=%s",
                                            (Jsonb(original), cancelled.action.action_id))

    def test_no_caller_flag_or_ignored_run_can_supply_clearance(self):
        module = self.require_scopes()
        self.admit()
        self.cancel(self.claim())
        scope = module.ExecutionReceiverScope("docker", "app")
        with self.unit_of_work() as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            evidence = uow.stores.execution.receiver_scope_evidence("workspace-a", (scope,), guard)
            self.assertEqual(module.classify_receiver_scope_evidence(evidence).disposition, "requires-fresh-gate-closure")
            with self.assertRaises(TypeError):
                module.classify_receiver_scope_evidence(evidence, reactivation_closed=True)
            with self.assertRaises(TypeError):
                uow.stores.execution.receiver_scope_evidence("workspace-a", (scope,), guard, excluded_run_ids=("run-a",))

    def test_cancel_action_time_mismatch_cannot_supply_no_dispatch_proof(self):
        module = self.require_scopes()
        self.admit()
        cancelled = self.cancel(self.claim())
        self.connection.execute("UPDATE cpk_operation_actions SET created_at=created_at + interval '1 second' WHERE action_id=%s",
                                (cancelled.action.action_id,))
        try:
            self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition, "unavailable")
        finally:
            self.connection.execute("UPDATE cpk_operation_actions SET created_at=%s WHERE action_id=%s",
                                    (cancelled.action.created_at, cancelled.action.action_id))

    def test_duplicate_cancel_action_candidates_are_unavailable(self):
        module = self.require_scopes()
        self.admit()
        cancelled = self.cancel(self.claim())
        from dataclasses import replace
        with self.unit_of_work() as uow:
            uow.stores.activity_history.add_action(replace(cancelled.action,
                action_id="duplicate-cancel", ordinal=cancelled.action.ordinal + 1,
                idempotency_key=None, intent_fingerprint=None))
            uow.commit()
        try:
            self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition, "unavailable")
        finally:
            self.connection.execute("DELETE FROM cpk_operation_actions WHERE action_id='duplicate-cancel'")

    def test_mixed_nonaffecting_history_does_not_erase_unresolved_cancellation(self):
        module = self.require_scopes()
        self.admit()
        self.cancel(self.claim())
        self.admit_operations("observe", WaitForHealthy(NodeTarget("app")))
        result = self.evidence(module.ExecutionReceiverScope("docker", "app"))
        self.assertEqual(result.disposition, "requires-fresh-gate-closure")
        self.admit("pending")
        result = self.evidence(module.ExecutionReceiverScope("docker", "app"))
        self.assertEqual(result.disposition, "conflict")
        self.assertIn("execution-pending", result.request_ids)
