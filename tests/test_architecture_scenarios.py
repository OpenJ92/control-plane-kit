from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import unittest

from control_plane_kit.effects import EffectCapability
from examples.scenarios.runner import ScenarioEffectInterpreter
from tests.architecture import (
    PolicyFinding,
    SourceFacts,
    analyze_file,
    analyze_source,
    evaluate_policies,
)


_FORBIDDEN_IMPORT_PREFIXES = (
    "control_plane_kit.projections",
    "control_plane_kit.saga",
    "control_plane_kit.scheduling",
    "tests",
    "unittest.mock",
)
_STORE_MUTATION_CALLS = frozenset(
    {
        "add_action",
        "add_event",
        "add_request",
        "add_run",
        "add_session",
        "commit",
        "compare_and_set_current_graph",
        "put",
        "rollback",
        "save",
        "set_current_graph",
        "set_desired_graph",
    }
)


@dataclass(frozen=True)
class ScenarioBoundaryPolicy:
    """Keep acceptance scenarios outside application and persistence internals."""

    module_prefix: str = "examples.scenarios"

    def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]:
        if not (
            facts.module == self.module_prefix
            or facts.module.startswith(f"{self.module_prefix}.")
        ):
            return ()
        findings: list[PolicyFinding] = []
        for imported in facts.imports:
            if imported.qualified_name.startswith(_FORBIDDEN_IMPORT_PREFIXES):
                findings.append(
                    PolicyFinding(
                        "scenario-import-boundary",
                        f"scenario module imports forbidden internal {imported.qualified_name}",
                        imported.location,
                    )
                )
        for call in facts.calls:
            call_name = call.qualified_name.rsplit(".", 1)[-1]
            if call_name in _STORE_MUTATION_CALLS:
                findings.append(
                    PolicyFinding(
                        "scenario-mutation-boundary",
                        f"scenario module calls forbidden mutation {call_name}",
                        call.location,
                    )
                )
            if ".coordinator._" in call.qualified_name:
                findings.append(
                    PolicyFinding(
                        "scenario-coordinator-boundary",
                        "scenario module calls a private coordinator operation",
                        call.location,
                    )
                )
        for declared in facts.classes:
            if declared.qualified_name.rsplit(".", 1)[-1].endswith("ReadModel"):
                findings.append(
                    PolicyFinding(
                        "scenario-read-model-boundary",
                        "scenario modules must consume canonical read models",
                        declared.location,
                    )
                )
        return tuple(sorted(findings))


SCENARIO_BOUNDARY_POLICY = ScenarioBoundaryPolicy()


class ScenarioArchitectureTests(unittest.TestCase):
    def test_scenario_modules_obey_application_and_persistence_boundaries(self):
        root = Path(__file__).parents[1]
        facts = tuple(
            analyze_file(path, root=root)
            for path in sorted((root / "examples" / "scenarios").rglob("*.py"))
        )

        self.assertEqual(
            evaluate_policies(facts, (SCENARIO_BOUNDARY_POLICY,)),
            (),
        )

    def test_policy_rejects_store_scheduler_mock_and_private_coordinator_bypass(self):
        bypass = analyze_source(
            "from control_plane_kit.scheduling import derive_schedule\n"
            "from unittest.mock import Mock\n"
            "def bypass(stores, services):\n"
            "    stores.execution.add_event(event)\n"
            "    stores.connection.commit()\n"
            "    services.coordinator._load_context(command)\n"
            "class ScenarioReadModel:\n"
            "    pass\n",
            path="examples/scenarios/bypass.py",
            module="examples.scenarios.bypass",
        )

        findings = SCENARIO_BOUNDARY_POLICY.evaluate(bypass)

        self.assertEqual(len(findings), 6)
        self.assertEqual(
            {finding.rule_id for finding in findings},
            {
                "scenario-import-boundary",
                "scenario-mutation-boundary",
                "scenario-coordinator-boundary",
                "scenario-read-model-boundary",
            },
        )

    def test_fake_effect_is_a_typed_capability_provider_not_an_application_mock(self):
        interpreter = ScenarioEffectInterpreter()

        self.assertEqual(interpreter.capabilities, frozenset(EffectCapability))
        self.assertTrue(callable(interpreter.execute))
        self.assertEqual(interpreter.requests, [])

    def test_atomic_contract_suites_remain_independent_of_scenario_runner(self):
        root = Path(__file__).parents[1]
        required = (
            "test_execution_admission.py",
            "test_execution_concurrency.py",
            "test_execution_coordinator.py",
            "test_run_lifecycle.py",
            "test_saga_journal.py",
            "test_scheduling.py",
            "test_unit_of_work.py",
        )

        for name in required:
            source = root / "tests" / name
            self.assertTrue(source.is_file(), name)
            facts = analyze_file(source, root=root)
            self.assertFalse(
                any(
                    imported.qualified_name.startswith("examples.scenarios.runner")
                    for imported in facts.imports
                ),
                name,
            )


if __name__ == "__main__":
    unittest.main()
