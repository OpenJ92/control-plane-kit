import unittest

from control_plane_kit.policies import (
    ApprovalPolicy,
    DestructiveActivityPolicy,
    HubAccessPolicy,
    InstanceAccessPolicy,
    retention_for,
)
from control_plane_kit.stores import WorkspaceLifecycle


class PolicyTests(unittest.TestCase):
    def test_hub_access_policy_returns_decisions_not_effects(self):
        policy = HubAccessPolicy()

        denied = policy.can_register_instance(["hub:instance:read"])
        allowed = policy.can_register_instance(["hub:instance:create"])

        self.assertFalse(denied.allowed)
        self.assertEqual(denied.required_scope, "hub:instance:create")
        self.assertTrue(allowed.allowed)

    def test_instance_access_policy_separates_read_and_edit_scopes(self):
        policy = InstanceAccessPolicy()

        self.assertTrue(policy.can_read_workspace(["instance:workspace:read"]).allowed)
        self.assertFalse(policy.can_edit_workspace(["instance:workspace:read"]).allowed)

    def test_approval_policy_requires_stronger_scope_for_destructive_plans(self):
        policy = ApprovalPolicy()

        ordinary = policy.can_approve_plan(["plan:approve"])
        destructive = policy.can_approve_plan(["plan:approve"], destructive=True)

        self.assertTrue(ordinary.allowed)
        self.assertFalse(destructive.allowed)
        self.assertEqual(destructive.required_scope, "plan:approve-destructive")

    def test_destructive_activity_policy_classifies_consequential_actions(self):
        policy = DestructiveActivityPolicy()

        safe = policy.classify("start_runtime")
        dangerous = policy.classify("drop_database")

        self.assertTrue(safe.allowed)
        self.assertFalse(dangerous.allowed)
        self.assertEqual(dangerous.required_scope, "plan:approve-destructive")

    def test_lifecycle_retention_represents_destructive_delete(self):
        stopped = retention_for(WorkspaceLifecycle.STOPPED)
        deleted = retention_for(WorkspaceLifecycle.DELETED)

        self.assertTrue(stopped.keeps_graph_history)
        self.assertFalse(stopped.keeps_runtime_resources)
        self.assertTrue(deleted.destructive)
        self.assertFalse(deleted.keeps_activity_history)


if __name__ == "__main__":
    unittest.main()
