from pathlib import Path
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY = PACKAGE_ROOT / "docs" / "EXTRACT_D_TOPOLOGY.md"


class ExtractDTopologyTests(unittest.TestCase):
    def test_core_docs_record_d_phase_topology(self) -> None:
        text = TOPOLOGY.read_text(encoding="utf-8")

        required = (
            "D.0 Topology Refresh",
            "D.1 Core Application Service Composition",
            "D.2 Core HTTP/MCP Contract Language",
            "D.3 Core Parity Laws",
            "D.4 cpk-server Product Handoff Contract",
            "D.5 Mandatory Stop",
            "do not build the canonical cpk-server process",
        )

        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_extract_d_topology_classifies_each_child_once(self) -> None:
        text = TOPOLOGY.read_text(encoding="utf-8")

        expected_rows = {
            "#631": "Core application service composition",
            "#632": "Core application service composition",
            "#633": "Core HTTP/MCP contract language",
            "#634": "Core HTTP/MCP contract language",
            "#635": "Core HTTP/MCP contract language",
            "#636": "Core parity law",
            "#637": "Core parity law",
            "#638": "Core parity law",
            "#639": "cpk-server handoff contract",
            "#640": "cpk-server handoff contract",
            "#641": "cpk-server handoff contract",
            "#642": "Mandatory stop and closeout",
        }

        for issue, classification in expected_rows.items():
            with self.subTest(issue=issue):
                self.assertIn(f"| {issue} | {classification} |", text)

    def test_extract_d_topology_forbids_process_packaging_in_core(self) -> None:
        text = TOPOLOGY.read_text(encoding="utf-8")

        forbidden_claims = (
            "core owns the canonical cpk-server Dockerfile",
            "core owns the canonical cpk-server OCI image",
            "core owns the canonical cpk-server product descriptor",
            "core imports cpk-server",
        )

        for claim in forbidden_claims:
            with self.subTest(claim=claim):
                self.assertNotIn(claim, text)

        required_claims = (
            "`cpk-server` imports core",
            "core never imports `cpk-server`",
            "process packaging is deferred to `control-plane-kit-servers/cpk-server`",
        )
        for claim in required_claims:
            with self.subTest(claim=claim):
                self.assertIn(claim, text)


if __name__ == "__main__":
    unittest.main()
