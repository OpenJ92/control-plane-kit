from __future__ import annotations

import importlib
from pathlib import Path
import unittest


PACKAGE_NAME = "control_plane_kit_architecture_testing"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _architecture_testing():
    try:
        return importlib.import_module(PACKAGE_NAME)
    except ModuleNotFoundError as error:
        if error.name != PACKAGE_NAME:
            raise
        return None


class ArchitectureTestingDependencyTests(unittest.TestCase):
    def test_shared_facts_and_policies_are_available_without_cpk_policy(self) -> None:
        architecture = _architecture_testing()
        self.assertIsNotNone(
            architecture,
            "exact architecture-testing dependency is not exposed to behavior tests",
        )

        source = (
            "from sample.tools import inspect as inspect_value\n"
            "inspect_value()\n"
        )
        path = "tests/sample_module.py"
        module = "sample_module"
        facts = architecture.analyze_source(source, path=path, module=module)
        import_policy = architecture.ExactImportSurfacePolicy(
            architecture.PolicyId("sample.imports"),
            architecture.RuleId("exact"),
            path,
            module,
            (
                architecture.ImportSurfaceEntry(
                    "sample.tools",
                    "inspect",
                    "inspect_value",
                ),
            ),
            "sample import surface differs",
        )
        call_policy = architecture.ExactCallSurfacePolicy(
            architecture.PolicyId("sample.calls"),
            architecture.RuleId("exact"),
            path,
            module,
            (architecture.ResolvedCallTarget("sample.tools.inspect"),),
            "sample call surface differs",
        )

        self.assertEqual(architecture.evaluate_policy(facts, import_policy), ())
        self.assertEqual(architecture.evaluate_policy(facts, call_policy), ())

        mismatch = architecture.ExactCallSurfacePolicy(
            architecture.PolicyId("sample.mismatch"),
            architecture.RuleId("exact"),
            path,
            module,
            (),
            "sample call surface differs",
        )
        findings = architecture.evaluate_policy(facts, mismatch)
        self.assertEqual(len(findings), 1)
        self.assertIs(type(findings[0]), architecture.PolicyFinding)
        self.assertEqual(findings[0].message, "sample call surface differs")
        self.assertNotIn(source, repr(findings))

    def test_testing_document_names_exact_local_and_ci_acquisition(self) -> None:
        document = (REPOSITORY_ROOT / "docs/TESTING.md").read_text(
            encoding="utf-8"
        )
        required_markers = (
            "control-plane-kit-architecture-testing",
            "7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef",
            "pip install -e",
            "test-only",
            "not a runtime dependency",
        )
        self.assertEqual(
            tuple(marker for marker in required_markers if marker not in document),
            (),
        )


if __name__ == "__main__":
    unittest.main()
