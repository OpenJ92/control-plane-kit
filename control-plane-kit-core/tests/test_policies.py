from __future__ import annotations

import unittest

from control_plane_kit_core.planning import (
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    DataResourceTarget,
    DestroyDataResource,
    NodeTarget,
    PlannedActivity,
    RiskLevel,
    StopNode,
)
from control_plane_kit_core.policies import (
    ApprovalPolicy,
    DestructiveActivityPolicy,
    HubAccessPolicy,
    InstanceAccessPolicy,
    PolicyScope,
    retention_for,
)
from control_plane_kit_core.types import WorkspaceLifecycle


class PolicyDecisionTests(unittest.TestCase):
    def test_hub_access_policy_returns_decisions_not_effects(self) -> None:
        policy = HubAccessPolicy()

        denied = policy.can_register_instance((PolicyScope.HUB_INSTANCE_READ,))
        allowed = policy.can_register_instance((PolicyScope.HUB_INSTANCE_CREATE,))

        self.assertFalse(denied.allowed)
        self.assertEqual(denied.required_scope, PolicyScope.HUB_INSTANCE_CREATE)
        self.assertEqual(
            denied.descriptor(),
            {
                "allowed": False,
                "reason": "scope 'hub:instance:create' is missing",
                "required_scope": "hub:instance:create",
            },
        )
        self.assertTrue(allowed.allowed)

    def test_instance_access_policy_separates_read_and_edit_scopes(self) -> None:
        policy = InstanceAccessPolicy()

        read_only = (PolicyScope.INSTANCE_WORKSPACE_READ,)

        self.assertTrue(policy.can_read_workspace(read_only).allowed)
        self.assertFalse(policy.can_edit_workspace(read_only).allowed)
        self.assertEqual(
            policy.can_edit_workspace(read_only).required_scope,
            PolicyScope.INSTANCE_WORKSPACE_EDIT,
        )

    def test_approval_policy_requires_stronger_scope_for_destructive_plans(self) -> None:
        policy = ApprovalPolicy()

        ordinary = policy.can_approve_plan((PolicyScope.PLAN_APPROVE,))
        destructive = policy.can_approve_plan(
            (PolicyScope.PLAN_APPROVE,),
            destructive=True,
        )

        self.assertTrue(ordinary.allowed)
        self.assertFalse(destructive.allowed)
        self.assertEqual(
            destructive.required_scope,
            PolicyScope.PLAN_APPROVE_DESTRUCTIVE,
        )

    def test_approval_policy_derives_scope_and_risk_from_plan_data(self) -> None:
        policy = ApprovalPolicy()
        requirement = policy.requirement_for(
            ActivityPlan(
                (
                    PlannedActivity(
                        ActivityId("stop-api"),
                        StopNode(NodeTarget("api")),
                        risk=RiskLevel.CRITICAL,
                        impact=ActivityImpact.DESTRUCTIVE,
                    ),
                )
            )
        )

        self.assertEqual(requirement.max_risk, RiskLevel.CRITICAL)
        self.assertTrue(requirement.destructive)
        self.assertEqual(
            requirement.required_scope,
            PolicyScope.PLAN_APPROVE_DESTRUCTIVE,
        )
        self.assertEqual(
            requirement.descriptor(),
            {
                "required_scope": "plan:approve-destructive",
                "max_risk": "critical",
                "destructive": True,
            },
        )
        self.assertTrue(
            policy.can_request_plan((PolicyScope.PLAN_REQUEST,)).allowed,
        )

    def test_destructive_activity_policy_classifies_consequential_actions(self) -> None:
        policy = DestructiveActivityPolicy()

        safe = policy.classify("start_runtime")
        dangerous = policy.classify("drop_database")

        self.assertTrue(safe.allowed)
        self.assertFalse(dangerous.allowed)
        self.assertEqual(
            dangerous.required_scope,
            PolicyScope.PLAN_APPROVE_DESTRUCTIVE,
        )
        self.assertEqual(
            policy.classify_operation(
                DestroyDataResource(DataResourceTarget("postgres", "volume"))
            ).required_scope,
            PolicyScope.PLAN_APPROVE_DESTRUCTIVE,
        )

    def test_lifecycle_retention_represents_destructive_delete(self) -> None:
        stopped = retention_for(WorkspaceLifecycle.STOPPED)
        deleted = retention_for(WorkspaceLifecycle.DELETED)

        self.assertTrue(stopped.keeps_graph_history)
        self.assertFalse(stopped.keeps_runtime_resources)
        self.assertTrue(deleted.destructive)
        self.assertFalse(deleted.keeps_activity_history)
        self.assertEqual(
            deleted.descriptor(),
            {
                "lifecycle": "deleted",
                "keeps_workspace_record": False,
                "keeps_graph_history": False,
                "keeps_activity_history": False,
                "keeps_observed_state": False,
                "keeps_runtime_resources": False,
                "destructive": True,
            },
        )

    def test_policy_scopes_reject_open_strings_at_the_pure_boundary(self) -> None:
        policy = ApprovalPolicy()

        with self.assertRaises(TypeError):
            policy.can_approve_plan(("plan:approve",))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
