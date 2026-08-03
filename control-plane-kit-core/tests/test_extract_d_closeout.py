from pathlib import Path
import unittest

from control_plane_kit_core.operations import (
    CpkServerEntrypointHandoffContract,
    CpkServerMaterialHandoffContract,
    CpkServerPublicationHandoffContract,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PACKAGE_ROOT / "src" / "control_plane_kit_core"

FORBIDDEN_PROCESS_ARTIFACT_NAMES = (
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "control-plane-instance.product.cpk.json",
    "cpk-server.product.cpk.json",
)

FORBIDDEN_PROCESS_MODULE_NAMES = (
    "fastapi",
    "uvicorn",
    "server",
    "entrypoint",
    "app",
)


class ExtractDCloseoutTests(unittest.TestCase):
    def test_core_exports_handoff_contracts_not_process_packaging(self) -> None:
        self.assertIsNotNone(CpkServerEntrypointHandoffContract)
        self.assertIsNotNone(CpkServerMaterialHandoffContract)
        self.assertIsNotNone(CpkServerPublicationHandoffContract)

        present = {
            path.name
            for path in PACKAGE_ROOT.rglob("*")
            if path.is_file()
        }

        for artifact in FORBIDDEN_PROCESS_ARTIFACT_NAMES:
            with self.subTest(artifact=artifact):
                self.assertNotIn(artifact, present)

    def test_core_has_no_cpk_server_process_module(self) -> None:
        module_parts = {
            part
            for path in SRC_ROOT.rglob("*.py")
            for part in path.relative_to(SRC_ROOT).with_suffix("").parts
        }

        for name in FORBIDDEN_PROCESS_MODULE_NAMES:
            with self.subTest(name=name):
                self.assertNotIn(name, module_parts)

    def test_closeout_document_names_extract_e_rewrite_boundary(self) -> None:
        learning = (
            PACKAGE_ROOT
            / "docs"
            / "EXTRACT_D_TOPOLOGY.md"
        ).read_text(encoding="utf-8")

        normalized = learning.lower()
        self.assertIn("extract.e must be refreshed before execution", normalized)
        self.assertIn("core wheel", normalized)
        self.assertIn("cpk-server image and descriptor remain", normalized)
        self.assertIn("external", normalized)


if __name__ == "__main__":
    unittest.main()
