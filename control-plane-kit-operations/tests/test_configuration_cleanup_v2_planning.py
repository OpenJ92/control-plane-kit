"""B2 stage 2: public planning/approval; recorded transfers grant no producer credit."""
from dataclasses import replace
import unittest

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import NodeTarget, RemoveNodeResource
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.approvals import (
    ApprovalCommandService, ApprovalWorkflowError, DecideApproval, RequestApproval,
)
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile as Profile
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.records import ApprovalDecisionKind
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.configuration_cleanup_postgres_fixture import ConfigurationCleanupPostgresFixture
from tests.configuration_transfer_fixture import (
    ConfigurationTransferFixture, ConfigurationTransferredConsumerFixture,
    historical_transfer_prefix,
)


class _V2PlanningFixture(ConfigurationCleanupPostgresFixture):
    def query(self, *args, **kwargs):
        return replace(super().query(*args, **kwargs), profile=Profile.CONFIGURATION_CLEANUP_V2)

    def request(self, *, key="publish-v2", query=None, inspection=None):
        query = query or self.query()
        return replace(super().request(key=key, query=query, inspection=inspection), profile=query.profile)

    def review(self, query=None):
        try:
            result = self.inspect(query)
        except self.commands.ConfigurationCleanupCommandError:
            self.fail("released v2 public inspection is unavailable")
        self.assertEqual(result.state, "complete")
        self.assertIs(type(result.inspection), self.values.ConfigurationCleanupInspectionV2)
        return result

    def approvals(self):
        return ApprovalCommandService(self.unit_of_work, clock=self.sample_clock, id_factory=self.sample_id)

    def ask(self, plan, *, key="ask-v2"):
        return RequestApproval("session-config", plan.plan_id, "requester", (PolicyScope.PLAN_REQUEST,),
            IdempotencyKey(key))

    def decide(self, request, *, key="decide-v2", actor="approver", scope=PolicyScope.PLAN_APPROVE_DESTRUCTIVE):
        return DecideApproval("session-config", request.request_id, actor, (scope,),
            ApprovalDecisionKind.APPROVED, IdempotencyKey(key))


class ConfigurationCleanupV2PlanningTests(_V2PlanningFixture, unittest.TestCase):
    def test_genuine_completion_publishes_exact_profile_and_destructive_approval(self):
        inspected = self.review()
        document = inspected.inspection.descriptor()
        self.assertEqual(document["profile"], "configuration-cleanup-inspection.v2")
        self.assertEqual(document["accepted_transfers"], [])
        self.assertEqual(len(document["invocation_accounting"]), 1)
        self.assertTrue(all(not row["blockers"] for row in document["candidates"]))
        command = self.request(inspection=inspected)
        published = self.assert_accounted_command(lambda factory: self.publish(command, factory=factory))
        plan = published.plan_record
        self.assertIs(plan.derivation_profile, Profile.CONFIGURATION_CLEANUP_V2)
        self.assertIs(type(plan.cleanup_proposal), self.values.ConfigurationCleanupProposalV2)
        proposal = plan.cleanup_proposal.descriptor()
        self.assertEqual(proposal["accepted_transfers"], [])
        self.assertEqual(len(proposal["invocations"]), 1)
        self.assertEqual(set(plan.plan.activities[0].operation.instances), set(self.refs))
        self.assertEqual(self.values.configuration_cleanup_inspection_from_proposal(plan.cleanup_proposal),
            inspected.inspection)
        self.assertEqual(published.action.payload["derivation_profile"], Profile.CONFIGURATION_CLEANUP_V2.value)
        requested = self.approvals().execute(self.ask(plan))
        self.assertEqual(requested.request.subject.proposal_fingerprint,
            self.values.configuration_cleanup_proposal_fingerprint(plan.cleanup_proposal))
        self.assertTrue(requested.request.destructive)
        self.assertIs(requested.request.required_scope, PolicyScope.PLAN_APPROVE_DESTRUCTIVE)
        before = self.truth()
        self.sampled.clear()
        for command in (self.decide(requested.request, actor="requester"),
                self.decide(requested.request, scope=PolicyScope.PLAN_APPROVE)):
            with self.subTest(command=command), self.assertRaises(ApprovalWorkflowError):
                self.approvals().execute(command)
            self.assertEqual(self.truth(), before)
            self.assertEqual(self.sampled, [])
        approved = self.approvals().execute(self.decide(requested.request))
        self.assertIs(approved.decision.decision, ApprovalDecisionKind.APPROVED)
        before = self.truth()
        self.sampled.clear()
        self.assertEqual(self.publish(self.request(inspection=inspected)).plan_record, plan)
        replay = self.approvals().execute(self.decide(requested.request))
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.decision, approved.decision)
        self.assertEqual(self.truth(), before)
        self.assertEqual(self.sampled, [])
        self.assertEqual(self.member.protective_claims(), self.member.claims)
        install_schema(self.connection)

    def test_uncovered_siblings_are_count_only_and_current_membership_still_blocks(self):
        self.review()
        query = self.query(self.refs[:1])
        inspected = self.review(query)
        row = inspected.inspection.descriptor()["candidates"][0]
        self.assertEqual(row["blockers"], ["incomplete-invocation-selection"])
        self.assertEqual(row["invocations"][0]["uncovered_outstanding_count"], len(self.refs) - 1)
        self.assertEqual(inspected.inspection.descriptor()["invocation_accounting"], [])
        self.assertNotIn(self.refs[1].allocation_id, repr(inspected.inspection.descriptor()))
        before = self.truth()
        with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
            self.publish(self.request(query=query, inspection=inspected))
        self.assertEqual(self.truth(), before)
        self.assertEqual(self.sampled, [])
        self.member.advance()
        current = self.review()
        self.assertTrue(all(row["blockers"] == ["current-selected-use"]
            for row in current.inspection.descriptor()["candidates"]))
        before = self.truth()
        with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
            self.publish(self.request(inspection=current))
        self.assertEqual(self.truth(), before)
        self.assertEqual(self.sampled, [])

    def test_both_profiles_refuse_stale_fresh_commands_but_replay_original_receipts(self):
        self.review()
        histories = []
        for profile in (Profile.CONFIGURATION_CLEANUP_V1, Profile.CONFIGURATION_CLEANUP_V2):
            query = replace(self.query(), profile=profile)
            command = self.request(query=query, key=profile.value)
            published = self.publish(command)
            ask = self.ask(published.plan_record, key="ask-" + profile.value)
            requested = self.approvals().execute(ask)
            histories.append((command, published, ask, requested))
            with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
                self.publish(replace(command, profile=(Profile.CONFIGURATION_CLEANUP_V2
                    if profile is Profile.CONFIGURATION_CLEANUP_V1 else Profile.CONFIGURATION_CLEANUP_V1)))
        with self.unit_of_work() as uow:
            uow.stores.workspaces.set_desired_graph("workspace-a", "graph-current")
            uow.commit()
        before = self.truth()
        self.sampled.clear()
        for command, published, ask, requested in histories:
            with self.subTest(profile=command.profile):
                with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
                    self.publish(replace(command, idempotency_key=IdempotencyKey("fresh-" + command.profile.value)))
                for fresh in (replace(ask, idempotency_key=IdempotencyKey("fresh-" + ask.idempotency_key.value)),
                        self.decide(requested.request, key="decision-" + command.profile.value)):
                    with self.assertRaises(ApprovalWorkflowError):
                        self.approvals().execute(fresh)
                replay = self.publish(command)
                self.assertTrue(replay.replayed)
                self.assertEqual((replay.plan_record, replay.action), (published.plan_record, published.action))
                replay = self.approvals().execute(ask)
                self.assertTrue(replay.replayed)
                self.assertEqual(replay.request, requested.request)
                self.assertEqual(self.truth(), before)
                self.assertEqual(self.sampled, [])

    def test_independent_source_and_outcome_corruption_refuse_publication_and_fresh_approval(self):
        inspected = self.review()
        command = self.request(inspection=inspected)
        plan = self.publish(command).plan_record
        pending = self.approvals().execute(self.ask(plan)).request
        for table in ("cpk_effect_attempt_intents", "cpk_effect_attempt_outcomes"):
            self.review()
            original = self.connection.execute(f"SELECT preimage FROM {table} WHERE run_id='run-config'").fetchone()[0]
            try:
                self.connection.execute(f"UPDATE {table} SET preimage=%s WHERE run_id='run-config'", (b"invalid-CANARY",))
                before = self.truth()
                self.sampled.clear()
                self.assert_unavailable(self.inspect())
                with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
                    self.publish(replace(command, idempotency_key=IdempotencyKey("corrupt-" + table)))
                for fresh in (self.ask(plan, key="corrupt-" + table), self.decide(pending, key="corrupt-" + table)):
                    with self.assertRaises(ApprovalWorkflowError):
                        self.approvals().execute(fresh)
                self.assertEqual(self.truth(), before)
                self.assertEqual(self.sampled, [])
            finally:
                self.connection.execute(f"UPDATE {table} SET preimage=%s WHERE run_id='run-config'", (original,))
        self.review()


class ConfigurationCleanupV2RecordedTransferPlanningTests(
        _V2PlanningFixture, ConfigurationTransferFixture, unittest.TestCase):
    """Real completion/acceptance prefix; B1's existing recorded transfer suffix."""
    def recorded_prefix(self, refs=None):
        self.review()
        self.membership = self.member
        with self.unit_of_work() as uow:
            self.completion = uow.stores.configuration_completions.get(self.original.identity)
        self.assertIsNotNone(self.completion)
        with historical_transfer_prefix(self):
            self.acceptance = self.member.advance()
        self.revision = self.acceptance.desired_graph_revision
        self.record_transfer(refs)
        self.member.claims = self.member.protective_claims()

    def depart(self):
        operator = self.carry_operator()
        empty = replace(operator.graph, nodes={}, runtimes={"runtime-a": replace(
            operator.graph.runtimes["runtime-a"], children=())})
        departed = operator.prepare("v2-depart", "graph-v2-depart", RemoveNodeResource(NodeTarget("api")), graph=empty)
        operator.advance(departed)

    def test_recorded_zero_uses_require_positive_roots_and_preserve_current_exclusion(self):
        self.recorded_prefix()
        current = self.review()
        self.assertTrue(all(row["blockers"] == ["current-selected-use"]
            for row in current.inspection.descriptor()["candidates"]))
        self.depart()
        query = self.query()
        zero = self.review(query)
        document = zero.inspection.descriptor()
        self.assertTrue(all(row["invocations"] == [] and row["blockers"] == [] for row in document["candidates"]))
        self.assertEqual(document["invocation_accounting"], [])
        self.assertEqual(len(document["accepted_transfers"]), len(self.refs))
        plan = self.publish(self.request(query=query, inspection=zero)).plan_record
        self.assertEqual(plan.cleanup_proposal.descriptor()["invocations"], [])
        self.assertTrue(all(row["proposed_closures"] == [] for row in plan.cleanup_proposal.descriptor()["candidates"]))
        requested = self.approvals().execute(self.ask(plan))
        self.assertIs(self.approvals().execute(self.decide(requested.request)).decision.decision,
            ApprovalDecisionKind.APPROVED)
        foreign = replace(query, selectors=(replace(query.selectors[0],
            source_identity=EffectAttemptIdentity(RunId("missing-seed"), "missing", 1)), *query.selectors[1:]))
        self.assert_unavailable(self.inspect(foreign))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_configuration_invocation_closures").fetchone(), (0,))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_configuration_claim_closures").fetchone(), (0,))

    def test_recorded_a_only_then_b_preserves_transfer_provenance_under_rollback_only_member_exclusion(self):
        a, b = self.refs
        self.recorded_prefix((a,))
        self.depart()
        a_query, b_query = self.query((a,)), self.query((b,))
        a_plan = self.publish(self.request(query=a_query, inspection=self.review(a_query), key="only-a")).plan_record
        self.assertEqual(a_plan.cleanup_proposal.descriptor()["invocations"], [])
        self.assertEqual(a_plan.cleanup_proposal.descriptor()["candidates"][0]["proposed_closures"], [])
        b_review = self.review(b_query)
        b_plan = self.publish(self.request(query=b_query, inspection=b_review, key="only-b")).plan_record
        proposal = b_plan.cleanup_proposal.descriptor()
        self.assertEqual(tuple(b_plan.plan.activities[0].operation.instances), (b,))
        self.assertEqual(len(proposal["invocations"]), 1)
        self.assertEqual({row["allocation_id"] for row in proposal["invocations"][0]["selection_members"]},
            {a.allocation_id, b.allocation_id})
        self.assertEqual([row["allocation_id"] for row in proposal["accepted_transfers"]], [a.allocation_id])
        self.assertEqual(len(proposal["candidates"][0]["proposed_closures"]), 1)
        before = self.truth(), self.transfer_snapshot()
        # No stage-2 cleanup execution or retirement is claimed. The existing
        # rollback-only exclusion premise isolates physical candidates from T.
        with self.unit_of_work() as uow:
            with ConfigurationTransferredConsumerFixture.recorded_exclusion(self, uow.stores.connection):
                a_result, a_proposal = uow.stores.configuration_cleanup.read(a_query)
                self.assert_unavailable(a_result)
                self.assertIsNone(a_proposal)
                b_result, b_proposal = uow.stores.configuration_cleanup.read(b_query)
                self.assertEqual(b_result, b_review)
                self.assertEqual(b_proposal, b_plan.cleanup_proposal)
        self.assertEqual((self.truth(), self.transfer_snapshot()), before)

    def test_recorded_missing_transfer_or_completion_refuses_from_independent_valid_zero_use_premises(self):
        self.recorded_prefix()
        self.depart()
        query = self.query()
        for missing in ("transfer", "completion"):
            self.review(query)
            before = self.truth(), self.transfer_snapshot()
            with self.subTest(missing=missing), self.unit_of_work() as uow:
                connection = uow.stores.connection
                if missing == "transfer":
                    removed = connection.execute("DELETE FROM cpk_configuration_claim_transfers "
                        "WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", self.key(self.refs[0]))
                else:
                    connection.execute("ALTER TABLE cpk_configuration_claim_transfers "
                        "DROP CONSTRAINT cpk_claim_transfers_completion_fk")
                    removed = connection.execute("DELETE FROM cpk_configuration_invocation_completions "
                        "WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", self.key(self.refs[0])[:3])
                self.assertEqual(removed.rowcount, 1)
                result, proposal = uow.stores.configuration_cleanup.read(query)
                self.assert_unavailable(result)
                self.assertIsNone(proposal)
            self.assertEqual((self.truth(), self.transfer_snapshot()), before)
        self.review(query)


if __name__ == "__main__":
    unittest.main()
