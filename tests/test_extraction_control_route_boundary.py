from __future__ import annotations

from pathlib import Path
import unittest

from extraction_parity.validation import read_bounded_json


ARTIFACT_ROOT = Path("artifacts/extraction")


class ControlRouteBoundaryClassificationTests(unittest.TestCase):
    def test_control_route_boundary_classification_is_closed_and_complete(self) -> None:
        artifact = read_bounded_json(
            ARTIFACT_ROOT / "control-route-boundary-classification.json"
        )

        self.assertEqual(
            artifact["schema"],
            "cpk.control-route-boundary-classification",
        )
        self.assertEqual(artifact["issue"], "#762")
        self.assertEqual(
            artifact["pure_core_successors"],
            [
                {
                    "family": "test_control_routes",
                    "owner": "control-plane-kit-core",
                    "successor_evidence": "extract-e-762-control-routes.unittest",
                    "reason": (
                        "The family asserts closed route-set names, route methods, "
                        "paths, scopes, descriptors, configurable prefix "
                        "construction, and fail-closed lookup. These are protocol "
                        "values, not a web-framework implementation."
                    ),
                }
            ],
        )

        handoffs = {
            handoff["family"]: handoff["owner_issue"]
            for handoff in artifact["downstream_handoffs"]
        }
        self.assertEqual(
            handoffs,
            {
                "test_block_control_fastapi": "#740",
                "test_block_control_state": "#740",
                "test_capability_interpreter_registry": "#743",
            },
        )

    def test_process_and_interpreter_families_are_not_claimed_as_core_successors(self) -> None:
        artifact = read_bounded_json(
            ARTIFACT_ROOT / "control-route-boundary-classification.json"
        )
        core_families = {
            successor["family"]
            for successor in artifact["pure_core_successors"]
        }

        self.assertNotIn("test_block_control_fastapi", core_families)
        self.assertNotIn("test_block_control_state", core_families)
        self.assertNotIn("test_capability_interpreter_registry", core_families)


if __name__ == "__main__":
    unittest.main()
