import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROLLOUT = ROOT / "SERVER_PRODUCT_ROLLOUT.md"
EXTRACT_E_LEARNING = ROOT / "docs" / "learning" / "extraction" / "extract-e-run-0001.md"
CORE_RELEASE_EVIDENCE = ROOT / "artifacts" / "extraction" / "core-release-candidate-evidence.json"


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

    def test_extract_e_security_data_supply_chain_review_records_core_evidence(self) -> None:
        learning = EXTRACT_E_LEARNING.read_text(encoding="utf-8")

        self.assertIn("## #646 Security, Data, Supply-Chain, And Test-Integrity Review", learning)
        for proof in (
            "extract-e-755-environment-secrets.json",
            "extract-e-763-policy-decisions.json",
            "extract-e-791-persistence-boundary-contract.json",
        ):
            with self.subTest(proof=proof):
                self.assertIn(proof, learning)

        self.assertIn("secret values are never durable core graph data", learning)
        self.assertIn("Postgres and UnitOfWork implementations remain non-core", learning)
        self.assertIn("runtime retention and cleanup proofs remain future interpreter/runtime evidence", learning)
        self.assertIn("no test-integrity weakening was accepted", learning)

    def test_extract_e_core_release_candidate_evidence_is_wheel_not_server_image(self) -> None:
        evidence = json.loads(CORE_RELEASE_EVIDENCE.read_text(encoding="utf-8"))
        learning = EXTRACT_E_LEARNING.read_text(encoding="utf-8")

        self.assertEqual(evidence["schema"], "cpk.extract-e.core-release-candidate-evidence")
        self.assertEqual(evidence["package"]["name"], "control-plane-kit-core")
        self.assertEqual(evidence["package"]["version"], "0.1.0")
        self.assertEqual(evidence["package"]["dependencies"], ["Jinja2>=3.1", "PyYAML>=6.0"])
        self.assertEqual(evidence["validation"]["core_test_command"], "./control-plane-kit-core/test.sh")
        self.assertEqual(evidence["validation"]["core_tests"], 374)
        self.assertEqual(evidence["required_core_closeout"]["incomplete_required_core"], 0)
        self.assertEqual(evidence["foundation_parity"]["incomplete_required"], 100)

        forbidden = set(evidence["forbidden_core_artifacts"])
        for artifact in (
            "cpk-server Dockerfile",
            "cpk-server OCI image",
            "cpk-server product descriptor",
            "hosted FastAPI process",
            "hosted MCP server",
        ):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, forbidden)

        self.assertIn("## #647 Core Wheel And Evidence Manifest", learning)
        self.assertIn("core release-candidate evidence is a wheel/import/manifest proof", learning)


if __name__ == "__main__":
    unittest.main()
