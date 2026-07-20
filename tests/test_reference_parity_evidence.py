from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extraction_parity.reference import (
    ReferenceEvidenceError,
    TestSummary,
    added_resource_ids,
    build_reference_evidence,
    parse_unittest_summary,
    write_evidence,
)


class ReferenceParityEvidenceTests(unittest.TestCase):
    def test_successful_unittest_summary_is_closed_and_bounded(self) -> None:
        output = "Ran 1043 tests in 19.250s\n\nOK (skipped=4)\n"

        self.assertEqual(
            parse_unittest_summary(output, maximum_bytes=1024),
            TestSummary(tests_run=1043, skipped=4, successful=True),
        )

    def test_summary_rejects_failure_missing_summary_and_oversized_output(self) -> None:
        with self.assertRaises(ReferenceEvidenceError):
            parse_unittest_summary("Ran 1 test in 0.1s\nFAILED (failures=1)\n", maximum_bytes=1024)
        with self.assertRaises(ReferenceEvidenceError):
            parse_unittest_summary("OK\n", maximum_bytes=1024)
        with self.assertRaises(ReferenceEvidenceError):
            parse_unittest_summary("x" * 17, maximum_bytes=16)

    def test_resource_inventory_reports_additions_without_cleanup_instructions(self) -> None:
        self.assertEqual(
            added_resource_ids(("pottery-api", "existing"), ("new-cpk", "pottery-api", "existing")),
            ("new-cpk",),
        )

    def test_evidence_requires_exact_reference_commit_and_contains_no_raw_log(self) -> None:
        summary = TestSummary(tests_run=1043, skipped=0, successful=True)
        with self.assertRaises(ReferenceEvidenceError):
            build_reference_evidence(
                reference_tag="pre-server-product-extraction-2026-07-20",
                reference_commit="wrong",
                expected_commit="expected",
                python_image="python:3.14-slim",
                python_image_id="sha256:python",
                postgres_image_id="sha256:postgres",
                test_image_id="sha256:test",
                dependency_inputs={"pyproject.toml": "sha256:input"},
                test_command=("./test.sh",),
                compile_command=("python", "-m", "compileall"),
                summary=summary,
                added_containers=(),
                added_networks=(),
                added_volumes=(),
            )

        evidence = build_reference_evidence(
            reference_tag="pre-server-product-extraction-2026-07-20",
            reference_commit="abc123",
            expected_commit="abc123",
            python_image="python:3.14-slim",
            python_image_id="sha256:python",
            postgres_image_id="sha256:postgres",
            test_image_id="sha256:test",
            dependency_inputs={"pyproject.toml": "sha256:input"},
            test_command=("./test.sh",),
            compile_command=("python", "-m", "compileall", "-q", "control_plane_kit", "tests", "examples"),
            summary=summary,
            added_containers=(),
            added_networks=(),
            added_volumes=(),
            cleaned_owned_volumes=("run-local-volume",),
        )

        self.assertEqual(evidence["schema"], "cpk.frozen-reference-evidence")
        self.assertEqual(evidence["reference"]["commit"], "abc123")
        self.assertEqual(evidence["tests"], {"run": 1043, "skipped": 0, "successful": True})
        self.assertEqual(
            evidence["runtime"]["python_image"],
            {"reference": "python:3.14-slim", "id": "sha256:python"},
        )
        self.assertEqual(evidence["dependency_inputs"], {"pyproject.toml": "sha256:input"})
        self.assertNotIn("output", evidence)
        self.assertNotIn("environment", json.dumps(evidence))
        self.assertEqual(evidence["owned_cleanup"], {"volumes": ["run-local-volume"]})

    def test_evidence_encoding_is_deterministic_and_atomic(self) -> None:
        evidence = {
            "schema": "cpk.frozen-reference-evidence",
            "reference": {"tag": "tag", "commit": "commit"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.json"
            write_evidence(path, evidence)
            first = path.read_bytes()
            write_evidence(path, evidence)

            self.assertEqual(path.read_bytes(), first)
            self.assertEqual(json.loads(first), evidence)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_runner_uses_archived_source_and_has_no_global_cleanup(self) -> None:
        runner = (Path(__file__).parents[1] / "reference-test.sh").read_text(encoding="utf-8")

        self.assertIn('git -C "$ROOT_DIR" archive "$REFERENCE_TAG"', runner)
        self.assertIn('CPK_TEST_IMAGE_NAME="$TEST_IMAGE"', runner)
        self.assertNotIn("docker system prune", runner)
        self.assertNotIn("docker container prune", runner)
        self.assertNotIn("docker volume prune", runner)
        self.assertNotIn("docker network prune", runner)
        self.assertIn('docker volume rm "$volume_id"', runner)
        self.assertIn('docker ps -aq --filter "volume=$volume_id"', runner)


if __name__ == "__main__":
    unittest.main()
