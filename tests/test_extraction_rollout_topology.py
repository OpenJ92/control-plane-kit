import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROLLOUT = ROOT / "SERVER_PRODUCT_ROLLOUT.md"
EXTRACT_E_LEARNING = ROOT / "docs" / "learning" / "extraction" / "extract-e-run-0001.md"
CORE_RELEASE_EVIDENCE = ROOT / "artifacts" / "extraction" / "core-release-candidate-evidence.json"
EXTRACT_E_CLOSEOUT = ROOT / "artifacts" / "extraction" / "extract-e-closeout-report.json"
EXTRACT_F_LEARNING = ROOT / "docs" / "learning" / "extraction" / "extract-f-run-0001.md"
CPK_SERVER_HANDOFF_INVENTORY = (
    ROOT / "artifacts" / "extraction" / "extract-f-804-cpk-server-handoff-inventory.json"
)


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

    def test_extract_e_closeout_stops_before_server_product_migration(self) -> None:
        closeout = json.loads(EXTRACT_E_CLOSEOUT.read_text(encoding="utf-8"))
        learning = EXTRACT_E_LEARNING.read_text(encoding="utf-8")

        self.assertEqual(closeout["schema"], "cpk.extract-e.closeout-report")
        self.assertEqual(closeout["milestone"], "EXTRACT.E")
        self.assertTrue(closeout["operator_stop_required"])
        self.assertEqual(closeout["required_core"]["incomplete_required_core"], 0)
        self.assertEqual(closeout["foundation_parity"]["incomplete_required"], 100)
        self.assertEqual(closeout["next_allowed_issue_after_operator_approval"], "#600")

        for issue in ("#804", "#805", "#806"):
            with self.subTest(issue=issue):
                self.assertIn(issue, closeout["future_handoffs"])

        for forbidden in (
            "cpk-server process",
            "cpk-server Dockerfile",
            "cpk-server OCI image",
            "cpk-server product descriptor",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, closeout["not_in_core"])

        self.assertIn("## #648 Mandatory EXTRACT.E Stop", learning)
        self.assertIn("operator approval is required before #600 begins", learning)

    def test_extract_f_ingests_cpk_server_control_process_handoff(self) -> None:
        inventory = json.loads(CPK_SERVER_HANDOFF_INVENTORY.read_text(encoding="utf-8"))
        learning = EXTRACT_F_LEARNING.read_text(encoding="utf-8")

        self.assertEqual(inventory["schema"], "cpk.extract-f.cpk-server-handoff-inventory")
        self.assertEqual(inventory["source_issue"], "#743")
        self.assertEqual(inventory["issue"], "#804")
        self.assertEqual(inventory["families"]["test_block_control_fastapi"], 8)
        self.assertEqual(inventory["families"]["test_block_control_state"], 6)
        self.assertEqual(len(inventory["law_cards"]), 14)

        allowed_targets = {"#813", "#814", "#815", "#816", "#817"}
        targets = {card["target_issue"] for card in inventory["law_cards"]}
        self.assertLessEqual(targets, allowed_targets)
        self.assertTrue(targets)
        self.assertNotIn("#654", targets)
        self.assertNotIn("#678", targets)

        for card in inventory["law_cards"]:
            with self.subTest(law=card["law"]):
                self.assertTrue(card["reference"].startswith("tests.test_block_control_"))
                self.assertIn(card["target_issue"], allowed_targets)
                self.assertNotEqual(card["owner"], "control-plane-kit-core")
                self.assertEqual(card["owner"], "control-plane-kit-servers/cpk_server")
                self.assertTrue(card["behavioral_law"])
                self.assertTrue(card["acceptance_boundary"])

        self.assertIn("## #804 cpk-server Control-Process Handoff Inventory", learning)
        self.assertIn("test_block_control_fastapi: 8 laws", learning)
        self.assertIn("test_block_control_state: 6 laws", learning)
        self.assertIn("Hello must not satisfy cpk-server process laws", learning)
        self.assertIn("#813 -> #814 -> #815 -> #816 -> #817", learning)


if __name__ == "__main__":
    unittest.main()
