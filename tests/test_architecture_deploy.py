from __future__ import annotations

from pathlib import Path
import unittest

from tests.architecture import (
    SourceBoundaryPolicy,
    analyze_file,
    analyze_source,
    evaluate_policies,
)


DEPLOY_APPLICATION_POLICY = SourceBoundaryPolicy(
    rule_prefix="deploy-application-boundary",
    module_prefix="control_plane_kit.application.deploy",
    forbidden_import_prefixes=(
        "control_plane_kit.adapters",
        "control_plane_kit.stores",
        "docker",
        "httpx",
        "os",
        "psycopg",
        "sqlalchemy",
        "subprocess",
        "urllib",
    ),
    forbidden_call_names=("commit", "rollback"),
    forbidden_call_prefixes=(
        "control_plane_kit.effects.dispatch_prepared_effect",
        "control_plane_kit.effects.prepare_effect",
    ),
    forbidden_class_names=(
        "ActivityEventRecord",
        "ActivityPlan",
        "ActivityRunRecord",
        "DeploymentGraph",
        "ObservationRecord",
        "RecoveryDecision",
        "SagaState",
    ),
)


class DeploymentApplicationArchitectureTests(unittest.TestCase):
    def test_deployment_application_obeys_composition_boundary(self) -> None:
        root = Path(__file__).parents[1]
        facts = tuple(
            analyze_file(path, root=root)
            for path in sorted(
                (root / "control_plane_kit" / "application" / "deploy").rglob("*.py")
            )
        )

        self.assertEqual(evaluate_policies(facts, (DEPLOY_APPLICATION_POLICY,)), ())
        self.assertTrue(
            {
                "Admit",
                "Advance",
                "Approve",
                "Claim",
                "Deploy",
                "DeploymentProgram",
                "StoredDeployment",
                "Execute",
                "ExecuteApprovedDeployment",
                "Plan",
                "PrepareDeployment",
            }.issubset(
                {
                    declared.qualified_name.rsplit(".", 1)[-1]
                    for facts_for_module in facts
                    for declared in facts_for_module.classes
                }
            )
        )

    def test_policy_resolves_aliases_and_rejects_boundary_bypasses(self) -> None:
        facts = analyze_source(
            "from control_plane_kit.stores import PostgresUnitOfWork as Work\n"
            "from control_plane_kit.effects import dispatch_prepared_effect as dispatch\n"
            "import os as process\n"
            "class ActivityPlan:\n"
            "    pass\n"
            "def bypass(effect, connection):\n"
            "    process.environ.get('TOKEN')\n"
            "    dispatch(effect)\n"
            "    connection.commit()\n",
            path="control_plane_kit/application/deploy/bypass.py",
            module="control_plane_kit.application.deploy.bypass",
        )

        findings = DEPLOY_APPLICATION_POLICY.evaluate(facts)

        self.assertEqual(len(findings), 5)
        self.assertEqual(
            {finding.rule_id for finding in findings},
            {
                "deploy-application-boundary-call",
                "deploy-application-boundary-duplicate-type",
                "deploy-application-boundary-import",
            },
        )


if __name__ == "__main__":
    unittest.main()
