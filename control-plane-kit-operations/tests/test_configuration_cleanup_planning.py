"""C-N04/06: trusted intent, exact publication, replay and atomicity."""
from dataclasses import fields, replace
import unittest

from control_plane_kit_core.identity import PrincipalKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService
from tests.configuration_cleanup_postgres_fixture import (
    ConfigurationCleanupPostgresFixture, READ_PLAN, command_context,
)


class ConfigurationCleanupPlanningTests(ConfigurationCleanupPostgresFixture, unittest.TestCase):
    def test_trusted_context_scopes_workspace_and_actor_are_checked_before_uow(self):
        query, request = self.query(), self.request()

        def forbidden():
            self.fail("untrusted or unauthorized input opened an evidence UoW")

        service = self.service(factory=forbidden)
        for call, command, scopes in ((service.inspect, query, ()), (service.request_plan, request, ()),
            (service.request_plan, request, (PolicyScope.INSTANCE_WORKSPACE_READ,)),
            (service.request_plan, request, (PolicyScope.PLAN_REQUEST,))):
            with self.subTest(scopes=scopes), self.assertRaises(self.commands.ConfigurationCleanupCommandError):
                call(command, context=command_context(scopes=scopes))
        for context in (None, {}, command_context(workspace="foreign")):
            with self.subTest(context=context), self.assertRaises(self.commands.ConfigurationCleanupCommandError):
                service.inspect(query, context=context)
        mutated = command_context(scopes=())
        object.__setattr__(mutated, "granted_scopes", READ_PLAN)
        with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
            service.request_plan(request, context=mutated)
        for command in (query, request):
            self.assertNotIn("actor_id", {field.name for field in fields(command)})
            self.assertNotIn("actor_scopes", {field.name for field in fields(command)})
            with self.assertRaises(TypeError):
                replace(command, actor_id="caller-override")
            with self.assertRaises(TypeError):
                replace(command, actor_scopes=READ_PLAN)
        self.assertEqual(self.sampled, [])
        for kind in PrincipalKind:
            with self.subTest(kind=kind):
                self.assertEqual(self.inspect(context=command_context(kind=kind)).state, "complete")

    def test_session_ownership_and_authorized_exact_replay_keep_original_receipt(self):
        WorkspaceCommandService(self.unit_of_work, clock=lambda: "2026-10-03T12:00:00Z",
            id_factory=lambda: "foreign-initial").create(CreateWorkspace("workspace-b", "Other workspace",
                "other-operator", IdempotencyKey("create-other")))
        with self.unit_of_work() as uow:
            session = uow.stores.activity_history.get_session("session-config")
            uow.stores.activity_history.add_session(replace(session, session_id="foreign-session",
                                                          workspace_id="workspace-b"))
            uow.commit()
        request = self.request()
        original = self.publish(request)
        self.assertFalse(original.replayed)
        self.assertEqual(original.action.actor_id, command_context().actor_id)
        before = self.truth()
        self.sampled.clear()
        # Replay recovers the immutable receipt after fresh desired truth changes.
        with self.unit_of_work() as uow:
            uow.stores.workspaces.set_desired_graph("workspace-a", "graph-current")
            uow.commit()
        changed = self.truth()
        replayed = self.publish(request)
        self.assertTrue(replayed.replayed)
        self.assertEqual((replayed.plan_record, replayed.action), (original.plan_record, original.action))
        self.assertEqual(self.truth(), changed)
        self.assertEqual(self.sampled, [])
        with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
            self.publish(request, context=command_context(scopes=(PolicyScope.INSTANCE_WORKSPACE_READ,)))
        with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
            self.publish(replace(request, session_id="foreign-session", idempotency_key=IdempotencyKey("foreign")))
        self.assertEqual(self.truth(), changed)
        self.assertNotEqual(before, changed)

    def test_publish_exact_eligible_set_and_refuse_blocked_partial_or_changed_inspection(self):
        query = self.query()
        before = self.truth()
        partial = self.query(self.refs[:1])
        with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
            self.publish(self.request(query=partial))
        self.assertEqual(self.truth(), before)
        self.assertEqual(self.sampled, [])
        command = self.request(query=query)
        for invalid in (replace(command, expected_inspection_fingerprint="f" * 64),
                        replace(command, selectors=command.selectors[:1])):
            with self.subTest(command=invalid), self.assertRaises(self.commands.ConfigurationCleanupCommandError):
                self.publish(invalid)
            self.assertEqual(self.truth(), before)
            self.assertEqual(self.sampled, [])
        result = self.publish(command)
        record = result.plan_record
        self.assertEqual(record.derivation_profile.value, "configuration-cleanup-v1")
        self.assertEqual(len(record.plan.activities), 1)
        self.assertEqual(set(record.plan.activities[0].operation.instances), set(self.refs))
        proposal = record.cleanup_proposal.descriptor()
        self.assertEqual({row["ref"]["allocation_id"] for row in proposal["candidates"]},
                         {ref.allocation_id for ref in self.refs})
        self.assertEqual(self.member.protective_claims(), self.member.claims)
        self.assertEqual(result.action.actor_id, command_context().actor_id)

    def test_stale_occurrence_desired_revision_aba_or_claim_outcome_change_requires_replan(self):
        original = self.request()
        # A -> B -> A desired material still changes the exact revision.
        with self.unit_of_work() as uow:
            uow.stores.workspaces.set_desired_graph("workspace-a", "graph-current")
            uow.stores.workspaces.set_desired_graph("workspace-a", "graph-configured")
            uow.commit()
        before = self.truth()
        with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
            self.publish(original)
        self.assertEqual(self.truth(), before)
        self.assertEqual(self.sampled, [])
        fresh = self.request(key="fresh")
        self.assertNotEqual(fresh.expected_inspection_fingerprint, original.expected_inspection_fingerprint)
        with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
            self.publish(replace(fresh, expected_inspection_fingerprint=original.expected_inspection_fingerprint))
        self.assertEqual(self.truth(), before)
        # Below-owner negative evidence, not a new admitted source: unchanged
        # pins cannot let publication bypass original-source revalidation.
        pins = self.pins()
        retained = self.connection.execute("SELECT preimage FROM cpk_effect_attempt_intents WHERE run_id='run-config'").fetchone()[0]
        try:
            self.connection.execute("UPDATE cpk_effect_attempt_intents SET preimage=%s WHERE run_id='run-config'",
                                    (b"corrupt-original",))
            corrupted = self.truth()
            self.sampled.clear()
            with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
                self.publish(fresh)
            self.assertEqual(self.pins(), pins)
            self.assertEqual(self.truth(), corrupted)
            self.assertEqual(self.sampled, [])
        finally:
            self.connection.execute("UPDATE cpk_effect_attempt_intents SET preimage=%s WHERE run_id='run-config'", (retained,))

    def test_owner_bigint_outside_canonical_domain_returns_whole_capacity_without_rounding(self):
        query = self.query()
        command = self.request(query=query)
        original_revision = query.expected_context.desired_graph_revision
        # A boundary setup on real bigint owner storage; not a claim that a
        # production command chain has performed 2**53 desired edits.
        try:
            self.connection.execute("UPDATE cpk_workspaces SET desired_graph_revision=%s WHERE workspace_id='workspace-a'",
                                    (9007199254740992,))
            before = self.truth()
            self.assert_unavailable(self.inspect(query), "capacity")
            with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
                self.publish(command)
            self.assertEqual(self.truth(), before)
            self.assertEqual(self.sampled, [])
            self.assertEqual(self.connection.execute("SELECT desired_graph_revision FROM cpk_workspaces "
                "WHERE workspace_id='workspace-a'").fetchone(), (9007199254740992,))
        finally:
            self.connection.execute("UPDATE cpk_workspaces SET desired_graph_revision=%s WHERE workspace_id='workspace-a'",
                                    (original_revision,))

    def test_plan_and_action_roll_back_on_late_failure_without_partial_publication(self):
        command = self.request()
        for commit_failure in (False, True):
            before = self.truth()
            with self.subTest(commit=commit_failure), self.assertRaises(RuntimeError):
                self.publish(command, factory=self.failure_factory(commit=commit_failure))
            self.assertEqual(self.truth(), before)
        self.assertFalse(self.publish(command).replayed)


if __name__ == "__main__":
    unittest.main()
