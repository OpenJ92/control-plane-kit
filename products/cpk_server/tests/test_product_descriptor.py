from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from control_plane_kit_core.products import (
    ProductCatalog,
    ProductDescriptorCodec,
    ProductDescriptorError,
    ProductFamily,
    ProductIdentity,
)
from control_plane_kit_core.types import Protocol


ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "products" / "cpk_server"
DESCRIPTOR = PRODUCT / "product.cpk.json"
DOCKER_DESCRIPTOR = PRODUCT / "product.docker.cpk.json"
CATALOGUE = ROOT / "catalogue" / "products.json"
COORDINATES = ROOT / "coordinates" / "server-products.json"


def _product_coordinates(product_id: str) -> dict[str, object]:
    coordinates = json.loads(COORDINATES.read_text(encoding="utf-8"))
    for product in coordinates["products"]:
        if product["product_id"] == product_id:
            return product
    raise AssertionError(f"missing product coordinates: {product_id}")


def _public_env(product, name: str) -> str:
    for item in product.runtime_contract.public_environment:
        if item.name == name:
            return item.value
    raise AssertionError(f"missing public environment value: {name}")


class CpkServerProductDescriptorTests(unittest.TestCase):
    def decode(self, path: Path = DESCRIPTOR):
        return ProductDescriptorCodec().decode_document(path.read_bytes())

    def test_descriptor_round_trips_through_core_product_language(self) -> None:
        document = self.decode()
        product = document.product

        self.assertEqual(
            product.identity,
            ProductIdentity("control-plane-kit", "cpk-server", 1),
        )
        self.assertEqual(product.display_name, "cpk-server")
        self.assertIs(product.product_family, ProductFamily.SERVER)
        coordinates = _product_coordinates("cpk-server")
        image = coordinates["image"]
        self.assertEqual(product.image.registry, image["registry"])
        self.assertEqual(product.image.repository, image["repository"])
        self.assertEqual(product.image.tag, image["tag"])
        self.assertEqual(str(product.image.digest), image["digest"])
        self.assertEqual(
            dict(product.image.provenance)["source-commit"],
            coordinates["source_commit"],
        )
        self.assertEqual(
            product.image.execution_reference,
            f"ghcr.io/openj92/control-plane-kit-servers/cpk-server@{product.image.digest}",
        )
        self.assertEqual(
            ProductDescriptorCodec().encode_document(product).content,
            DESCRIPTOR.read_bytes(),
        )


    def test_descriptor_variants_separate_interpreter_availability_from_authority(self) -> None:
        base = self.decode(DESCRIPTOR).product
        docker = self.decode(DOCKER_DESCRIPTOR).product

        self.assertEqual(base.identity, ProductIdentity("control-plane-kit", "cpk-server", 1))
        self.assertEqual(
            docker.identity,
            ProductIdentity("control-plane-kit", "cpk-server-docker", 1),
        )
        self.assertEqual(_public_env(base, "CPK_RUNTIME_INTERPRETERS"), "none")
        self.assertEqual(_public_env(docker, "CPK_RUNTIME_INTERPRETERS"), "docker")
        self.assertIn("Docker runtime interpreter software", docker.description)
        self.assertIn("Runtime authority endpoints and credentials remain", docker.description)

        for path in (DESCRIPTOR, DOCKER_DESCRIPTOR):
            rendered = path.read_text(encoding="utf-8").lower()
            with self.subTest(path=path.name):
                self.assertNotIn("docker_host", rendered)
                self.assertNotIn("/var/run/docker.sock", rendered)
                self.assertNotIn("client-cert", rendered)
                self.assertNotIn("client-key", rendered)
                self.assertNotIn("tls", rendered)
                self.assertNotIn("registeredruntimeauthority", rendered)

    def test_descriptor_declares_http_mcp_and_store_contracts_without_values(self) -> None:
        product = self.decode().product
        sockets = product.runtime_contract.sockets

        self.assertEqual(sockets.provider("http-api").protocol, Protocol.HTTP)
        self.assertEqual(sockets.provider("mcp").protocol, Protocol.MCP_STREAMABLE_HTTP)
        self.assertEqual(
            {
                value.provider_socket: value.container_port
                for value in product.runtime_contract.provider_ports
            },
            {"http-api": 8080, "mcp": 8080},
        )
        self.assertEqual(
            sockets.requirement_names(),
            (
                "activity-history-store",
                "graph-topology-store",
                "observer-state-store",
                "workplace-store",
            ),
        )
        for name in sockets.requirement_names():
            requirement = sockets.requirement(name)
            self.assertEqual(requirement.protocol, Protocol.POSTGRES)
            self.assertEqual(len(requirement.env_bindings), 1)
            self.assertTrue(requirement.env_bindings[0].startswith("CPK_"))
            self.assertTrue(requirement.env_bindings[0].endswith("_DATABASE_URL"))

        raw_descriptor = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
        runtime_contract = raw_descriptor["product"]["runtime_contract"]
        self.assertEqual(
            runtime_contract["secret_deliveries"],
            [
                {
                    "kind": "environment",
                    "environment_name": "PGPASSWORD",
                    "reference_id": "secret://control-plane-kit/postgres/password",
                }
            ],
        )
        rendered = json.dumps(
            {key: value for key, value in runtime_contract.items() if key != "secret_deliveries"},
            sort_keys=True,
        ).lower()
        self.assertNotIn("postgres://", rendered)
        self.assertNotIn("password", rendered)
        self.assertNotIn("token", rendered)
        self.assertNotIn("secret://", rendered)
        secret_descriptor = json.dumps(runtime_contract["secret_deliveries"])
        self.assertNotIn('"value"', secret_descriptor)
        self.assertNotIn("postgresql://", secret_descriptor)

    def test_endpoint_contracts_are_direct_child_http_and_mcp_surfaces(self) -> None:
        product = self.decode().product
        verification = product.runtime_contract.verification
        checks = {check.check_id: check for check in verification.checks}

        self.assertEqual(set(checks), {"live", "ready"})
        self.assertEqual(checks["live"].provider_socket, "http-api")
        self.assertEqual(checks["live"].path, "/health/live")
        self.assertEqual(checks["live"].policy.maximum_attempts, 10)
        self.assertEqual(checks["ready"].provider_socket, "http-api")
        self.assertEqual(checks["ready"].path, "/health/ready")
        self.assertEqual(checks["ready"].policy.maximum_attempts, 10)
        self.assertIn("direct child", product.description)
        self.assertIn("Recursive proxying is not part", product.description)

    def test_catalogue_admits_descriptor_and_core_catalog_lookup(self) -> None:
        from control_plane_kit_servers.catalogue import load_catalogue, load_product_catalog

        declarations = {
            declaration.product_id: declaration
            for declaration in load_catalogue(CATALOGUE)
        }
        document = self.decode()
        docker_document = self.decode(DOCKER_DESCRIPTOR)
        catalog = load_product_catalog(CATALOGUE, root=ROOT)

        self.assertEqual(
            sorted(declarations),
            [
                "cpk-server",
                "cpk-server-docker",
                "hello-server",
                "http-active-router",
                "http-multiplexer",
                "postgres-server",
            ],
        )
        for product_id, expected in (
            ("cpk-server", document),
            ("cpk-server-docker", docker_document),
        ):
            declaration = declarations[product_id]
            with self.subTest(product_id=product_id):
                self.assertEqual(declaration.product_id, product_id)
                self.assertEqual(declaration.descriptor_sha256, expected.content_digest)
                self.assertEqual(declaration.image_digest, expected.product.image.digest)

        self.assertEqual(
            catalog.lookup(ProductIdentity("control-plane-kit", "cpk-server", 1)),
            document,
        )
        self.assertEqual(
            catalog.lookup(ProductIdentity("control-plane-kit", "cpk-server-docker", 1)),
            docker_document,
        )
        self.assertEqual(
            ProductCatalog.empty().add(document).add(docker_document).products,
            (document, docker_document),
        )

    def test_catalogue_rejects_descriptor_and_image_digest_mismatch(self) -> None:
        from control_plane_kit_servers.catalogue import CatalogueError, load_product_catalog

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            descriptor_path = root / "products" / "cpk_server"
            descriptor_path.mkdir(parents=True)
            descriptor_copy = descriptor_path / "product.cpk.json"
            descriptor_copy.write_bytes(DESCRIPTOR.read_bytes())
            catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
            catalogue["products"][0]["descriptor_sha256"] = "0" * 64
            source = root / "products.json"
            source.write_text(json.dumps(catalogue), encoding="utf-8")
            with self.assertRaisesRegex(CatalogueError, "descriptor digest mismatch"):
                load_product_catalog(source, root=root)

            catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
            catalogue["products"][0]["image_digest"] = "sha256:" + "1" * 64
            source.write_text(json.dumps(catalogue), encoding="utf-8")
            with self.assertRaisesRegex(CatalogueError, "image digest mismatch"):
                load_product_catalog(source, root=root)

    def test_descriptor_rejects_unknown_fields(self) -> None:
        descriptor = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
        descriptor["product"]["python_module"] = "control_plane_kit_servers_cpk_server.server"

        with self.assertRaises(ProductDescriptorError):
            ProductDescriptorCodec().decode_document(descriptor)

    def test_descriptor_and_catalogue_loading_do_not_import_process_code(self) -> None:
        script = f"""
import sys
from pathlib import Path
from control_plane_kit_servers.catalogue import load_catalogue, load_product_catalog

root = Path({str(ROOT)!r})
catalogue = Path({str(CATALOGUE)!r})
load_catalogue(catalogue)
load_product_catalog(catalogue, root=root)
for module in (
    "control_plane_kit_servers_cpk_server.server",
    "fastapi",
    "httpx",
    "docker",
):
    if module in sys.modules:
        raise SystemExit(f"imported process module: {{module}}")
"""
        subprocess.run([sys.executable, "-c", script], check=True)

    def test_generated_catalogue_checksum_matches_publication_artifact(self) -> None:
        package_catalogue = ROOT / "src" / "control_plane_kit_servers" / "catalogue.json"
        checksum = package_catalogue.with_suffix(package_catalogue.suffix + ".sha256")
        content = package_catalogue.read_bytes()

        self.assertEqual(
            checksum.read_text(encoding="utf-8"),
            f"{hashlib.sha256(content).hexdigest()}  {package_catalogue.name}\n",
        )


if __name__ == "__main__":
    unittest.main()
