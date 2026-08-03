from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "apply_coordinates.py"
PRODUCT_IMAGE_SCRIPT = ROOT / "scripts" / "product_image_coordinate.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("apply_coordinates", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load coordinate script")
    module = importlib.util.module_from_spec(spec)
    sys.modules["apply_coordinates"] = module
    spec.loader.exec_module(module)
    return module


def load_product_image_script_module():
    spec = importlib.util.spec_from_file_location(
        "product_image_coordinate",
        PRODUCT_IMAGE_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("could not load product image coordinate script")
    module = importlib.util.module_from_spec(spec)
    sys.modules["product_image_coordinate"] = module
    spec.loader.exec_module(module)
    return module


class CoordinateGenerationTests(unittest.TestCase):
    def test_coordinate_manifest_is_the_source_for_generated_files(self) -> None:
        module = load_script_module()
        coordinates = module.load_coordinates(module.COORDINATES)
        updates = module.generate_updates(coordinates)

        stale = [
            path.relative_to(ROOT).as_posix()
            for path, content in updates.items()
            if path.read_bytes() != content
        ]

        self.assertEqual(stale, [])

    def test_coordinates_drive_every_generated_dependency_pin(self) -> None:
        module = load_script_module()
        coordinates = module.load_coordinates(module.COORDINATES)
        cpk_commit = coordinates["upstreams"]["control_plane_kit_commit"]
        interpreters_commit = coordinates["upstreams"][
            "control_plane_kit_interpreters_commit"
        ]
        secrets_commit = coordinates["upstreams"][
            "control_plane_kit_secrets_commit"
        ]

        for path in (module.PYPROJECT, module.CPK_SERVER_DOCKERFILE):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertIn(
                    "https://github.com/OpenJ92/control-plane-kit/archive/"
                    f"{cpk_commit}.zip",
                    text,
                )
                self.assertIn(
                    "https://github.com/OpenJ92/control-plane-kit-interpreters/"
                    f"archive/{interpreters_commit}.zip",
                    text,
                )
        gateway_dockerfile = module.CPK_LOCAL_GATEWAY_DOCKERFILE.read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "https://github.com/OpenJ92/control-plane-kit/archive/"
            f"{cpk_commit}.zip",
            gateway_dockerfile,
        )
        secrets_dockerfile = module.SECRETS_SERVER_DOCKERFILE.read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "https://github.com/OpenJ92/control-plane-kit-secrets/archive/"
            f"{secrets_commit}.zip",
            secrets_dockerfile,
        )

    def test_published_product_smokes_resolve_digest_from_coordinates(self) -> None:
        module = load_product_image_script_module()

        self.assertEqual(
            module.image_execution_reference(
                module.COORDINATES,
                "http-active-router",
            ),
            "ghcr.io/openj92/control-plane-kit-servers/http-active-router@"
            "sha256:a58938fdc5c37bfda1b2b0dbd95fc0bf3ba7391f5ce3b8fdfb3956dccf0a01c8",
        )
        self.assertEqual(
            module.image_execution_reference(
                module.COORDINATES,
                "http-multiplexer",
            ),
            "ghcr.io/openj92/control-plane-kit-servers/http-multiplexer@"
            "sha256:7fd15d9477db02c122e834d62074268a3b947b49b31fa3cad10d6a7737ca4fcb",
        )
        for path, product_id in (
            (
                ROOT / "scripts/http_active_router_published_image_smoke.sh",
                "http-active-router",
            ),
            (
                ROOT / "scripts/http_multiplexer_published_image_smoke.sh",
                "http-multiplexer",
            ),
        ):
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIn(
                    f"python3 scripts/product_image_coordinate.py {product_id}",
                    source,
                )
                self.assertNotIn("DIGEST=", source)


if __name__ == "__main__":
    unittest.main()
