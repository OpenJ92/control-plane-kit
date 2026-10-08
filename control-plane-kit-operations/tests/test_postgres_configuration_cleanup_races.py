"""Actual cleanup starts compete with real acceptance and other cleanup starts."""
import queue
import unittest
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptTransition, EffectAttemptTransitionKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict
from control_plane_kit_operations.approvals import ApprovalCommandService, RequestApproval, DecideApproval
from control_plane_kit_operations.effect_attempt_start import StartEffectAttempt, NewlyStarted, EffectAttemptStartConflict, EffectAttemptStartDenied
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.lifecycle import ClaimAndOpenActivityRun, ExecutionLeaseDuration, StartActivityRun
from control_plane_kit_operations.records import ApprovalDecisionKind
from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.configuration_cleanup_postgres_fixture import ConfigurationCleanupPostgresFixture
from tests.receiver_scope_history_fixture import admit_fixture_plan
from tests.receiver_fresh_execution_fixture import load_execution_context
from tests import test_postgres_effect_attempt_start_concurrency as concurrency
from tests import test_execution_coordinator as coordinator_fixture


class PostgresConfigurationCleanupRaceTests(ConfigurationCleanupPostgresFixture, unittest.TestCase):
    single_artifact = True
    runtime_authority_ref = RuntimeAuthorityReference("cleanup-race-docker")
    _factory_with_pids = concurrency.PostgresEffectAttemptStartConcurrencyTests._factory_with_pids
    _wait_until_blocked_by = concurrency.PostgresEffectAttemptStartConcurrencyTests._wait_until_blocked_by

    def no_ids(self):
        self.fail("losing operation must refuse before clock/identity allocation")

    def ready_cleanup(self, suffix="race"):
        plan = self.publish(self.request(key="publish-" + suffix)).plan_record
        approvals = ApprovalCommandService(self.unit_of_work, clock=self.sample_clock, id_factory=self.sample_id)
        requested = approvals.execute(RequestApproval(plan.session_id, plan.plan_id, "requester",
            tuple(PolicyScope), IdempotencyKey("ask-" + suffix))).request
        approvals.execute(DecideApproval(plan.session_id, requested.request_id, "approver",
            tuple(PolicyScope), ApprovalDecisionKind.APPROVED, IdempotencyKey("decide-" + suffix)))
        admitted = admit_fixture_plan(self, request_id="request-" + suffix, session_id=plan.session_id,
            plan_id=plan.plan_id, approval_request_id=requested.request_id, key="admit-" + suffix,
            actor_scopes=tuple(PolicyScope))
        engine = self.base.engine
        claimed = engine.lifecycle_with_ids("run-" + suffix, "open-" + suffix, "claim-" + suffix).execute(
            ClaimAndOpenActivityRun(admitted.request.identity.request_id, engine.authority(),
                ExecutionLeaseDuration(600), IdempotencyKey("claim-" + suffix)))
        engine.lifecycle_with_ids("ready-" + suffix, "ready-action-" + suffix).execute(StartActivityRun(
            claimed.run.run_id, engine.authority(), claimed.request.claim.fence, IdempotencyKey("ready-" + suffix)))
        coordinator = engine.coordinator(coordinator_fixture.RecordingAdapter(engine.tracker))
        execution = replace(engine.command(generation=claimed.request.claim.generation), run_id=claimed.run.run_id)
        context = load_execution_context(coordinator, execution)
        activity = context.plan.activities[0]
        intent = _runtime_effect_intent_for_context(context, activity)
        return StartEffectAttempt(admitted.request.identity.request_id,
            EffectAttemptTransition(EffectAttemptTransitionKind.STARTED,
                EffectAttemptIdentity(intent.source.run_id, activity.activity_id.value, 1),
                request_fingerprint=runtime_effect_intent_fingerprint(intent)),
            intent, execution.authority, execution.fence)

    def race(self, first, second, blocker, pids, conflict):
        with ThreadPoolExecutor(max_workers=2) as executor:
            winner = executor.submit(first)
            try:
                if not blocker.entered.wait(timeout=30):
                    winner.result(timeout=1)
                    self.fail("winner did not reach allocation under its lifecycle lock")
                first_pid = pids.get(timeout=5)
                loser = executor.submit(second)
                second_pid = pids.get(timeout=5)
                self._wait_until_blocked_by(second_pid, first_pid)
                blocker.release.set()
                result = winner.result(timeout=30)
                with self.assertRaises(conflict):
                    loser.result(timeout=30)
                return result
            finally:
                blocker.release.set()

    def test_cleanup_wins_before_pending_real_membership_advancement(self):
        command = self.ready_cleanup()
        advancement = self.member.command()
        pids = queue.Queue()
        blocker = concurrency._BlockingId("cleanup-original")
        start = EffectAttemptStartService(self._factory_with_pids(pids), id_factory=blocker)
        advance = CurrentGraphAdvancementCommandService(self._factory_with_pids(pids),
            clock=self.no_ids, id_factory=self.no_ids)
        result = self.race(lambda: start.execute(command), lambda: advance.execute(advancement),
            blocker, pids, CurrentGraphAdvancementConflict)
        self.assertIs(type(result), NewlyStarted)
        with self.unit_of_work() as uow:
            reservation = uow.stores.configuration_cleanup_ownership.get(command.transition.identity)
            self.assertEqual(tuple(row.ref for row in reservation.members), self.refs)
            workspace = uow.stores.workspaces.get("workspace-a")
            self.assertEqual(workspace.current_graph_id, advancement.expected_current_graph_id)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_configuration_accepted_slots "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s",
            (self.member.workspace.desired_graph_revision,)).fetchone(), (0,))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_configuration_claim_transfers").fetchone(), (0,))
        # The winning cleanup legitimately creates ownership; the losing
        # advancement must not introduce accepted-current dispositions.
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(self.connection.execute("SELECT artifact_id,accepted_revision FROM " + table
                + " WHERE run_id=%s ORDER BY artifact_id", (self.original.identity.run_id.value,)).fetchall(),
                [(ref.artifact_id, None) for ref in self.refs])

    def test_real_membership_advancement_wins_before_cleanup_start(self):
        command = self.ready_cleanup()
        advancement = self.member.command()
        pids = queue.Queue()
        blocker = concurrency._BlockingId("advance-first-event")
        identifiers = iter(("advance-first-event", "advance-first-action"))

        def identity():
            if blocker.calls == 0:
                blocker()
            return next(identifiers)

        advance = CurrentGraphAdvancementCommandService(self._factory_with_pids(pids),
            clock=self.sample_clock, id_factory=identity)
        start = EffectAttemptStartService(self._factory_with_pids(pids), id_factory=self.no_ids)
        accepted = self.race(lambda: advance.execute(advancement), lambda: start.execute(command),
            blocker, pids, (EffectAttemptStartConflict, EffectAttemptStartDenied))
        with self.unit_of_work() as uow:
            self.assertIsNone(uow.stores.configuration_cleanup_ownership.get(command.transition.identity))
            self.assertEqual(uow.stores.workspaces.get("workspace-a").current_graph_id, "graph-configured")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_configuration_accepted_slots "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s",
            (self.member.workspace.desired_graph_revision,)).fetchone(), (1,))
        with self.unit_of_work() as uow:
            completion = uow.stores.configuration_completions.get(self.original.identity)
        self.assertIsNotNone(completion)
        identity = self.original.identity
        self.assertEqual(self.connection.execute("SELECT run_id,activity_id,attempt,artifact_id,workspace_id,allocation_id,"
            "runtime_id,node_id,ref_digest,request_fingerprint,selection_fingerprint,outcome_fingerprint,"
            "acceptance_revision FROM cpk_configuration_claim_transfers ORDER BY artifact_id").fetchall(), [(
                identity.run_id.value, identity.activity_id, identity.attempt, ref.artifact_id, ref.workspace_id,
                ref.allocation_id, ref.runtime_id, ref.node_id,
                sha256(ConfigurationInstanceRefCodec().encode_canonical_bytes(ref)).hexdigest(),
                completion.request_fingerprint, completion.selection_fingerprint, completion.outcome_fingerprint,
                accepted.desired_graph_revision) for ref in self.refs])
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(self.connection.execute("SELECT artifact_id,protective,accepted_revision,disposition_kind "
                "FROM " + table + " ORDER BY artifact_id").fetchall(),
                [(ref.artifact_id, False, accepted.desired_graph_revision, "accepted-current") for ref in self.refs])

    def competing_cleanup(self, *, right_first):
        left, right = self.ready_cleanup("left"), self.ready_cleanup("right")
        winner, loser = (right, left) if right_first else (left, right)
        pids = queue.Queue()
        blocker = concurrency._BlockingId("cleanup-winner-original")
        first = EffectAttemptStartService(self._factory_with_pids(pids), id_factory=blocker)
        second = EffectAttemptStartService(self._factory_with_pids(pids), id_factory=self.no_ids)
        result = self.race(lambda: first.execute(winner), lambda: second.execute(loser),
            blocker, pids, (EffectAttemptStartConflict, EffectAttemptStartDenied))
        self.assertIs(type(result), NewlyStarted)
        with self.unit_of_work() as uow:
            retained = uow.stores.configuration_cleanup_ownership.get(winner.transition.identity)
            self.assertEqual(tuple(row.ref for row in retained.members), self.refs)
            self.assertIsNone(uow.stores.configuration_cleanup_ownership.get(loser.transition.identity))
        for table in ("cpk_configuration_cleanup_reservations", "cpk_configuration_cleanup_members",
                "cpk_configuration_invocation_closures", "cpk_configuration_claim_closures"):
            self.assertEqual(self.connection.execute("SELECT count(*) FROM " + table).fetchone(), (1,))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_activity_events "
            "WHERE run_id=%s AND event_type='step_started'", (loser.transition.identity.run_id.value,)).fetchone(), (0,))

    def test_first_cleanup_wins_over_distinct_competing_cleanup(self):
        self.competing_cleanup(right_first=False)

    def test_second_cleanup_wins_over_distinct_competing_cleanup(self):
        self.competing_cleanup(right_first=True)
