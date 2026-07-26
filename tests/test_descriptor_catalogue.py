import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class DescriptorCatalogueTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(SRC))

    def tearDown(self) -> None:
        sys.path.remove(str(SRC))
        for name in list(sys.modules):
            if name == "control_plane_kit_servers" or name.startswith(
                "control_plane_kit_servers."
            ):
                sys.modules.pop(name, None)

    def test_default_catalogue_loads_completed_declarations(self) -> None:
        from control_plane_kit_servers.catalogue import load_catalogue

        catalogue = load_catalogue()

        self.assertEqual(
            [item.product_id for item in catalogue],
            [
                "cpk-local-gateway",
                "cpk-server",
                "cpk-server-docker",
                "hello-server",
                "http-active-router",
                "http-multiplexer",
                "postgres-server",
            ],
        )
        self.assertTrue(all(item.status == "completed" for item in catalogue))
        self.assertIsInstance(catalogue, tuple)

    def test_catalogue_rejects_duplicate_product_ids(self) -> None:
        from control_plane_kit_servers.catalogue import CatalogueError, load_catalogue

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "products.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "cpk-servers.descriptor-catalogue",
                        "products": [
                            self._product("hello"),
                            self._product("hello"),
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(CatalogueError, "duplicate product_id"):
                load_catalogue(path)

    def test_catalogue_rejects_incomplete_and_unknown_declarations(self) -> None:
        from control_plane_kit_servers.catalogue import CatalogueError, load_catalogue

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "products.json"
            product = self._product("hello")
            product["status"] = "reserved"
            path.write_text(
                json.dumps(
                    {
                        "schema": "cpk-servers.descriptor-catalogue",
                        "products": [product],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(CatalogueError, "completed declarations"):
                load_catalogue(path)

            product = self._product("hello")
            product["extra"] = "not part of the publication language"
            path.write_text(
                json.dumps(
                    {
                        "schema": "cpk-servers.descriptor-catalogue",
                        "products": [product],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(CatalogueError, "unknown product keys"):
                load_catalogue(path)

    def test_declaration_carries_descriptor_image_and_source_digests(self) -> None:
        from control_plane_kit_servers.catalogue import load_catalogue

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "products.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "cpk-servers.descriptor-catalogue",
                        "products": [self._product("hello")],
                    }
                ),
                encoding="utf-8",
            )

            (declaration,) = load_catalogue(path)

        self.assertEqual(declaration.product_id, "hello")
        self.assertEqual(declaration.owner_directory, "products/hello")
        self.assertEqual(declaration.descriptor_path, "products/hello/cpk.json")
        self.assertEqual(declaration.descriptor_sha256, "a" * 64)
        self.assertEqual(declaration.source_commit, "b" * 40)
        self.assertEqual(declaration.image_ref, "ghcr.io/openj92/control-plane-kit-hello:0.1.0")
        self.assertEqual(declaration.image_digest, "sha256:" + "c" * 64)
        self.assertEqual(declaration.status, "completed")
        self.assertEqual(
            declaration.descriptor()["image_digest"], "sha256:" + "c" * 64
        )

    def test_publication_artifact_is_deterministic_and_checksummed(self) -> None:
        from control_plane_kit_servers.catalogue import publish_catalogue

        declarations = (self._product("beta"), self._product("alpha"))

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "products.json"
            out = Path(directory) / "server-products.json"
            source.write_text(
                json.dumps(
                    {
                        "schema": "cpk-servers.descriptor-catalogue",
                        "products": declarations,
                    }
                ),
                encoding="utf-8",
            )

            report = publish_catalogue(source, out)

            first = out.read_bytes()
            checksum = (out.with_suffix(out.suffix + ".sha256")).read_text(
                encoding="utf-8"
            )

            publish_catalogue(source, out)
            second = out.read_bytes()

        self.assertEqual(first, second)
        self.assertEqual(
            checksum,
            f"{hashlib.sha256(first).hexdigest()}  {out.name}\n",
        )
        self.assertEqual(report["product_ids"], ["alpha", "beta"])

    def test_catalogue_module_does_not_import_product_or_process_code(self) -> None:
        source = (SRC / "control_plane_kit_servers" / "catalogue.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("control_plane_kit_servers.products", source)
        self.assertNotIn("fastapi", source)
        self.assertNotIn("httpx", source)
        self.assertNotIn("docker", source)

    def _product(self, product_id: str) -> dict[str, str]:
        return {
            "product_id": product_id,
            "owner_directory": f"products/{product_id}",
            "descriptor_path": f"products/{product_id}/cpk.json",
            "descriptor_sha256": "a" * 64,
            "source_commit": "b" * 40,
            "image_ref": f"ghcr.io/openj92/control-plane-kit-{product_id}:0.1.0",
            "image_digest": "sha256:" + "c" * 64,
            "status": "completed",
        }


if __name__ == "__main__":
    unittest.main()
