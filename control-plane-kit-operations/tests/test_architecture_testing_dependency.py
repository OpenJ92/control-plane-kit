from __future__ import annotations

import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


PACKAGE_NAME = "control_plane_kit_architecture_testing"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TESTING_DOCUMENT_ENV = "CPK_TESTING_DOCUMENT_PATH"


def _testing_document_path() -> Path:
    return Path(
        os.environ.get(
            TESTING_DOCUMENT_ENV,
            str(REPOSITORY_ROOT / "docs/TESTING.md"),
        )
    )


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
        document = " ".join(_testing_document_path().read_text(encoding="utf-8").split())
        required_markers = (
            "control-plane-kit-architecture-testing",
            "exact clean sibling",
            "declared by the harness",
            "used by CI",
            "Establish that checkout before invoking the suite",
            "mounts it read-only into the test container",
            "do not install it into host Python",
            "substitute another coordinate",
        )
        self.assertEqual(
            tuple(marker for marker in required_markers if marker not in document),
            (),
        )

    def test_testing_document_uses_explicit_copied_package_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            document = Path(temporary_directory) / "TESTING.md"
            with mock.patch.dict(
                os.environ,
                {TESTING_DOCUMENT_ENV: str(document)},
            ):
                self.assertEqual(_testing_document_path(), document)


if __name__ == "__main__":
    unittest.main()
