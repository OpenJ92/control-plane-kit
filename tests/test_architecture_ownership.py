from __future__ import annotations

from pathlib import Path
import unittest

from tests.architecture import (
    CallOwner,
    CallOwnershipPolicy,
    CommitOwnershipPolicy,
    EnvironmentAccessPolicy,
    analyze_file,
    analyze_source,
    evaluate_policies,
)


COMMIT_POLICY = CommitOwnershipPolicy(
    owner_modules=("control_plane_kit.stores.unit_of_work",),
    owner_module_prefixes=("control_plane_kit.workflows",),
)
ENVIRONMENT_POLICY = EnvironmentAccessPolicy(
    owner_modules=(
        "control_plane_kit.cli",
        "control_plane_kit.contracts",
        "control_plane_kit.docker_runtime",
    )
)
CURRENT_GRAPH_MUTATION_POLICY = CallOwnershipPolicy(
    owners=(
        CallOwner("set_current_graph", ()),
        CallOwner(
            "compare_and_set_current_graph",
            ("control_plane_kit.workflows.current_graph",),
        ),
    )
)


class ArchitectureOwnershipTests(unittest.TestCase):
    def test_repository_obeys_commit_environment_and_graph_mutation_ownership(
        self,
    ) -> None:
        root = Path(__file__).parents[1]
        facts = tuple(
            analyze_file(path, root=root)
            for path in sorted((root / "control_plane_kit").rglob("*.py"))
        )

        self.assertEqual(
            evaluate_policies(
                facts,
                (
                    COMMIT_POLICY,
                    ENVIRONMENT_POLICY,
                    CURRENT_GRAPH_MUTATION_POLICY,
                ),
            ),
            (),
        )

    def test_raw_current_graph_mutation_is_reserved_to_guarded_workflow(self) -> None:
        bypass = analyze_source(
            "def bypass(stores):\n"
            "    stores.workspace.set_current_graph('workspace', 'graph')\n"
            "    stores.workspace.compare_and_set_current_graph(\n"
            "        'workspace', expected_graph_id='a', replacement_graph_id='b'\n"
            "    )\n",
            path="control_plane_kit/workflows/bypass.py",
            module="control_plane_kit.workflows.bypass",
        )
        owner = analyze_source(
            "def advance(stores):\n"
            "    stores.workspace.compare_and_set_current_graph(\n"
            "        'workspace', expected_graph_id='a', replacement_graph_id='b'\n"
            "    )\n",
            path="control_plane_kit/workflows/current_graph.py",
            module="control_plane_kit.workflows.current_graph",
        )

        findings = evaluate_policies(
            (bypass, owner),
            (CURRENT_GRAPH_MUTATION_POLICY,),
        )

        self.assertEqual(len(findings), 2)
        self.assertEqual(
            {finding.location.line for finding in findings},
            {2, 3},
        )

    def test_observation_projection_cannot_mutate_graph_truth(self) -> None:
        observed_state = analyze_source(
            "def project(stores):\n"
            "    stores.workspace.set_current_graph('workspace', 'graph')\n",
            path="control_plane_kit/execution/observations.py",
            module="control_plane_kit.execution.observations",
        )

        findings = evaluate_policies(
            (observed_state,),
            (CURRENT_GRAPH_MUTATION_POLICY,),
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].location.line, 2)

    def test_store_commit_is_rejected_but_unit_of_work_commit_is_allowed(self) -> None:
        store = analyze_source(
            "def save(connection):\n    connection.commit()\n",
            path="control_plane_kit/stores/catalog.py",
            module="control_plane_kit.stores.catalog",
        )
        unit = analyze_source(
            "def finish(connection):\n    connection.commit()\n",
            path="control_plane_kit/stores/unit_of_work.py",
            module="control_plane_kit.stores.unit_of_work",
        )

        findings = evaluate_policies((store, unit), (COMMIT_POLICY,))

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].location.path, "control_plane_kit/stores/catalog.py")

    def test_workflow_may_request_commit_but_pure_package_may_not(self) -> None:
        workflow = analyze_source(
            "def execute(work):\n    work.commit()\n",
            path="control_plane_kit/workflows/command.py",
            module="control_plane_kit.workflows.command",
        )
        pure = analyze_source(
            "def evolve(work):\n    work.commit()\n",
            path="control_plane_kit/saga/state.py",
            module="control_plane_kit.saga.state",
        )

        findings = evaluate_policies((workflow, pure), (COMMIT_POLICY,))

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].location.path, "control_plane_kit/saga/state.py")

    def test_environment_aliases_are_rejected_outside_declared_boundaries(self) -> None:
        module_alias = analyze_source(
            "import os as operating\nVALUE = operating.environ['VALUE']\n",
            path="control_plane_kit/planning/env.py",
            module="control_plane_kit.planning.env",
        )
        selected_alias = analyze_source(
            "from os import getenv as read\nVALUE = read('VALUE')\n",
            path="control_plane_kit/effects/env.py",
            module="control_plane_kit.effects.env",
        )

        findings = evaluate_policies(
            (module_alias, selected_alias),
            (ENVIRONMENT_POLICY,),
        )

        self.assertEqual(len(findings), 2)
        self.assertEqual(
            {value.location.path for value in findings},
            {
                "control_plane_kit/effects/env.py",
                "control_plane_kit/planning/env.py",
            },
        )

    def test_contract_boundary_may_read_process_environment(self) -> None:
        facts = analyze_source(
            "import os\nVALUE = os.environ.get('VALUE')\n",
            path="control_plane_kit/contracts.py",
            module="control_plane_kit.contracts",
        )

        self.assertEqual(ENVIRONMENT_POLICY.evaluate(facts), ())

    def test_packaged_servers_do_not_bypass_contract_mutation_boundary(self) -> None:
        root = Path(__file__).parents[1]
        facts = tuple(
            analyze_file(path, root=root)
            for path in sorted((root / "control_plane_kit" / "servers").rglob("*.py"))
        )

        bypasses = tuple(
            reference
            for source in facts
            for reference in source.references
            if reference.qualified_name == "_projection"
            or reference.qualified_name.endswith("._projection")
        )

        self.assertEqual(bypasses, ())


if __name__ == "__main__":
    unittest.main()
