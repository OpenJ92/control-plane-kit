from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from extraction_parity.retirement import (
    BASELINE_COMMIT,
    COMPLETED_OWNER_PROMOTIONS,
    CURRENT_INSTRUCTION_PATHS,
    POST_BASELINE_ADDITIONS,
    RetirementError,
    build_manifest,
    classify_path,
    validate_future_owner_refresh,
    validate_manifest,
    validate_no_live_legacy_references,
)


LIVE_SHELLS = frozenset({"legacy-live-test.sh"})


def _future_refresh() -> dict[str, object]:
    owners = []
    for number in range(1, 11):
        owners.append(
            {
                "repository": "OpenJ92/control-plane-kit",
                "number": number,
                "state": "open",
                "disposition": "future-owned",
                "title": f"Future {number}",
                "url": f"https://github.com/OpenJ92/control-plane-kit/issues/{number}",
                "checked_at": "2026-08-03T00:00:00Z",
            }
        )
    for number in (1316, 1317):
        owners.append(
            {
                "repository": "OpenJ92/control-plane-kit",
                "number": number,
                "state": "closed",
                "disposition": "implemented-current",
                "title": f"Completed {number}",
                "url": f"https://github.com/OpenJ92/control-plane-kit/issues/{number}",
                "checked_at": "2026-08-03T00:00:00Z",
                "evidence": {"merge_commits": ["a" * 40]},
            }
        )
    return {
        "schema": "cpk.legacy-retirement-future-owner-refresh",
        "issue": 1318,
        "owners": owners,
    }


class LegacyRetirementTests(unittest.TestCase):
    def test_path_policy_is_closed_and_product_independent(self) -> None:
        self.assertEqual(
            classify_path("control_plane_kit/core/value.py", LIVE_SHELLS),
            "delete-legacy-package",
        )
        self.assertEqual(
            classify_path("tests/test_value.py", LIVE_SHELLS),
            "delete-legacy-tests",
        )
        self.assertEqual(
            classify_path("legacy-live-test.sh", LIVE_SHELLS),
            "delete-legacy-live-shell",
        )
        self.assertEqual(
            classify_path("control-plane-kit-core/src/value.py", LIVE_SHELLS),
            "retain-current-distribution",
        )
        self.assertEqual(
            classify_path("reference-test.sh", LIVE_SHELLS),
            "retain-immutable-reference-runner",
        )
        with self.assertRaisesRegex(RetirementError, "no retirement disposition"):
            classify_path("mystery/value.py", LIVE_SHELLS)

    def test_future_owner_refresh_accepts_only_two_implemented_transitions(self) -> None:
        validate_future_owner_refresh(_future_refresh())

        malformed = _future_refresh()
        malformed["owners"][-1]["state"] = "open"
        with self.assertRaisesRegex(RetirementError, "state and disposition"):
            validate_future_owner_refresh(malformed)

        malformed = _future_refresh()
        malformed["owners"][-1]["evidence"] = {}
        with self.assertRaisesRegex(RetirementError, "lacks merged evidence"):
            validate_future_owner_refresh(malformed)

    @patch("extraction_parity.retirement._git")
    @patch("extraction_parity.retirement.baseline_entries")
    def test_manifest_is_exhaustive_and_validates_pre_and_post_deletion(
        self, baseline, git
    ) -> None:
        baseline.return_value = (
            ("control_plane_kit/value.py", "100644", "a" * 40),
            ("tests/test_value.py", "100644", "b" * 40),
            ("legacy-live-test.sh", "100755", "c" * 40),
            ("README.md", "100644", "d" * 40),
        )
        git.return_value = "e" * 40 + "\n"
        completion = {"valid": True, "zero_unowned": True}
        live = {
            "scripts": [{"legacy_script": "legacy-live-test.sh"}],
        }
        future = _future_refresh()
        manifest = build_manifest(
            root=Path("/repository"),
            baseline_commit=BASELINE_COMMIT,
            live_script_dispositions=live,
            completion_report=completion,
            future_owner_refresh=future,
        )
        self.assertEqual(manifest["counts"]["baseline-files"], 4)
        self.assertEqual(manifest["counts"]["delete-files"], 3)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for entry in manifest["entries"]:
                path = root / entry["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("value", encoding="utf-8")
            for value in POST_BASELINE_ADDITIONS:
                path = root / value
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("value", encoding="utf-8")
            for relative in CURRENT_INSTRUCTION_PATHS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_text("current backend\n", encoding="utf-8")

            pre = validate_manifest(
                root=root,
                manifest=manifest,
                live_script_dispositions=live,
                completion_report=completion,
                future_owner_refresh=future,
                require_deleted=False,
            )
            self.assertFalse(pre["legacy_deleted"])

            for entry in manifest["entries"]:
                if str(entry["disposition"]).startswith("delete-"):
                    (root / entry["path"]).unlink()
            post = validate_manifest(
                root=root,
                manifest=manifest,
                live_script_dispositions=live,
                completion_report=completion,
                future_owner_refresh=future,
                require_deleted=True,
            )
            self.assertTrue(post["legacy_deleted"])

            (root / "README.md").unlink()
            with self.assertRaisesRegex(RetirementError, "retained path is missing"):
                validate_manifest(
                    root=root,
                    manifest=manifest,
                    live_script_dispositions=live,
                    completion_report=completion,
                    future_owner_refresh=future,
                    require_deleted=True,
                )

    def test_live_artifact_manifest_covers_every_baseline_path_once(self) -> None:
        root = Path(__file__).resolve().parents[2]
        manifest_path = root / (
            "artifacts/extraction/harden-tests-parity-1318-retirement-manifest.json"
        )
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        paths = [value["path"] for value in manifest["entries"]]
        self.assertEqual(len(paths), len(set(paths)))

    def test_completed_owner_promotions_are_exact_and_current(self) -> None:
        root = Path(__file__).resolve().parents[2]
        artifact_root = root / "artifacts/extraction"
        reconciliation = json.loads(
            (artifact_root / "semantic-test-reconciliation.json").read_text()
        )
        manifest = json.loads((artifact_root / "parity-manifest.json").read_text())
        closeout = json.loads(
            (artifact_root / "semantic-migration-closeout.json").read_text()
        )
        reviews = {value["reference"]: value for value in reconciliation["reviews"]}
        entries = {value["reference"]: value for value in manifest["entries"]}

        self.assertEqual(len(COMPLETED_OWNER_PROMOTIONS), 24)
        for reference, tests in COMPLETED_OWNER_PROMOTIONS.items():
            self.assertEqual(reviews[reference]["reviewed_by_issue"], 1318)
            self.assertEqual(reviews[reference]["disposition"], "current-strengthened")
            self.assertEqual(tuple(reviews[reference]["current_tests"]), tests)
            self.assertEqual(
                tuple(value["id"] for value in entries[reference]["successors"]),
                tests,
            )
        self.assertTrue(
            all(value["number"] not in {1316, 1317} for value in closeout["future_issues"])
        )

    def test_current_instructions_reject_retired_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in CURRENT_INSTRUCTION_PATHS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("current backend\n", encoding="utf-8")
            for relative in (
                "control-plane-kit-core/src/value.py",
                "control-plane-kit-operations/src/value.py",
                "current_backend/value.py",
                "extraction_parity/value.py",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("VALUE = 1\n", encoding="utf-8")

            validate_no_live_legacy_references(root, LIVE_SHELLS)
            (root / "README.md").write_text("./legacy-live-test.sh\n")
            with self.assertRaisesRegex(RetirementError, "retired surface"):
                validate_no_live_legacy_references(root, LIVE_SHELLS)


if __name__ == "__main__":
    unittest.main()
