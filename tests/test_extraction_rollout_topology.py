from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROLLOUT = ROOT / "SERVER_PRODUCT_ROLLOUT.md"
EXTRACT_E_LEARNING = ROOT / "docs" / "learning" / "extraction" / "extract-e-run-0001.md"


class ExtractionRolloutTopologyTests(unittest.TestCase):
    def test_extract_e_topology_includes_refresh_before_core_children(self) -> None:
        rollout = ROLLOUT.read_text(encoding="utf-8")
        learning = EXTRACT_E_LEARNING.read_text(encoding="utf-8")

        for document in (rollout, learning):
            with self.subTest(document=document[:32]):
                self.assertIn("#642 -> #725", document)
                self.assertIn("#725 -> #643", document)
                self.assertIn("#725 -> #644", document)
                self.assertIn("#643 + #644 -> #645 -> #646 -> #647 -> #648", document)

    def test_extract_e_artifacts_are_core_wheel_not_cpk_server_process(self) -> None:
        rollout = ROLLOUT.read_text(encoding="utf-8")
        learning = EXTRACT_E_LEARNING.read_text(encoding="utf-8")
        combined = f"{rollout}\n{learning}".lower()

        self.assertIn("core wheel", combined)
        self.assertIn("parity", combined)
        self.assertIn("handoff", combined)
        self.assertIn("control-plane-kit-servers/cpk-server", combined)
        self.assertIn("owns dockerfile and oci image", combined)
        self.assertIn("must not create cpk-server image or descriptor", combined)

    def test_rollout_no_longer_assigns_cpi_image_to_core_tests(self) -> None:
        rollout = ROLLOUT.read_text(encoding="utf-8")

        self.assertNotIn(
            "CPI image build, external self-descriptor, and import-isolation tests",
            rollout,
        )
        self.assertNotIn("#599 -> #643-#648", rollout)
        self.assertIn("#599 -> #725 plus #643-#648", rollout)

    def test_extract_e_architecture_review_records_guards_and_future_handoffs(self) -> None:
        learning = EXTRACT_E_LEARNING.read_text(encoding="utf-8")

        self.assertIn("## #645 Architecture, Packaging, Import, And Public-Language Review", learning)
        for guard in (
            "tests/test_package_topology.py",
            "tests/test_package_inventory.py",
            "tests/test_root_api.py",
            "control-plane-kit-core/tests/test_package_boundary.py",
        ):
            with self.subTest(guard=guard):
                self.assertIn(guard, learning)

        self.assertIn("core contains handoff contracts, not process entrypoints", learning)
        self.assertIn("cpk-server remains a future server product/process wrapper", learning)
        self.assertIn("Docker/cloud/runtime interpreters remain future interpreter/runtime package work", learning)
        for issue in ("#804", "#805", "#806"):
            with self.subTest(issue=issue):
                self.assertIn(issue, learning)


if __name__ == "__main__":
    unittest.main()
