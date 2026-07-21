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


if __name__ == "__main__":
    unittest.main()
