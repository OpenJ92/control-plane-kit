from __future__ import annotations

import ast
from pathlib import Path
import unittest

from control_plane_kit_core.products import (
    OciImageReference,
    OciImageReferenceCodec,
    OciImageReferenceError,
    OciPlatform,
    PlatformMismatch,
)


VALID_DIGEST = "sha256:" + "a" * 64


class OciImageReferenceTests(unittest.TestCase):
    def test_digest_pinned_reference_uses_digest_as_execution_identity(self) -> None:
        image = OciImageReference(
            registry="ghcr.io",
            repository="openj92/control-plane-kit-servers/hello",
            digest=VALID_DIGEST,
            tag="v1.2.3",
            platforms=(OciPlatform("linux", "amd64"),),
            provenance={"source": "github.com/openj92/control-plane-kit-servers"},
        )

        self.assertEqual(
            image.execution_reference,
            f"ghcr.io/openj92/control-plane-kit-servers/hello@{VALID_DIGEST}",
        )
        self.assertEqual(
            image.human_reference,
            f"ghcr.io/openj92/control-plane-kit-servers/hello:v1.2.3@{VALID_DIGEST}",
        )
        self.assertEqual(image.descriptor()["digest"], VALID_DIGEST)
        self.assertEqual(image.descriptor()["tag"], "v1.2.3")

    def test_mutable_tag_without_digest_is_rejected(self) -> None:
        with self.assertRaisesRegex(OciImageReferenceError, "digest"):
            OciImageReference(
                registry="ghcr.io",
                repository="openj92/control-plane-kit-servers/hello",
                digest="",
                tag="latest",
            )

    def test_digest_registry_repository_tag_and_provenance_are_bounded(self) -> None:
        bad_values = (
            {"digest": "sha256:" + "a" * 63},
            {"digest": "md5:" + "a" * 32},
            {"registry": "https://ghcr.io"},
            {"registry": "operator:secret@ghcr.io"},
            {"repository": "../hello"},
            {"repository": "OpenJ92/hello"},
            {"tag": "bad tag"},
            {"provenance": {"registry_token": "do-not-disclose"}},
        )

        for overrides in bad_values:
            with self.subTest(overrides=overrides):
                with self.assertRaises(OciImageReferenceError):
                    OciImageReference(
                        registry=overrides.get("registry", "ghcr.io"),
                        repository=overrides.get("repository", "openj92/hello"),
                        digest=overrides.get("digest", VALID_DIGEST),
                        tag=overrides.get("tag"),
                        provenance=overrides.get("provenance", {}),
                    )

    def test_secret_like_registry_values_are_rejected_without_echoing_secret(self) -> None:
        secret = "do-not-disclose"

        with self.assertRaises(OciImageReferenceError) as context:
            OciImageReference(
                registry=f"operator:{secret}@ghcr.io",
                repository="openj92/hello",
                digest=VALID_DIGEST,
            )

        self.assertNotIn(secret, str(context.exception))

    def test_invalid_platform_values_raise_oci_error(self) -> None:
        with self.assertRaises(OciImageReferenceError):
            OciPlatform("Linux", "amd64")

    def test_codec_round_trips_strict_descriptor(self) -> None:
        image = OciImageReference(
            registry="ghcr.io",
            repository="openj92/hello",
            digest=VALID_DIGEST,
            tag="v1",
            platforms=(OciPlatform("linux", "amd64"), OciPlatform("linux", "arm64")),
            provenance={"source": "github.com/openj92/control-plane-kit-servers"},
        )
        codec = OciImageReferenceCodec()

        descriptor = codec.encode(image)
        restored = codec.decode(descriptor)

        self.assertEqual(restored, image)
        self.assertEqual(codec.encode(restored), descriptor)

    def test_codec_rejects_unknown_missing_and_wrong_typed_fields(self) -> None:
        codec = OciImageReferenceCodec()
        valid = OciImageReference("ghcr.io", "openj92/hello", VALID_DIGEST).descriptor()

        bad_descriptors = (
            {**valid, "future": "unknown"},
            {key: value for key, value in valid.items() if key != "digest"},
            {**valid, "platforms": [{"os": "linux"}]},
            {**valid, "provenance": {"token": "do-not-disclose"}},
        )

        for descriptor in bad_descriptors:
            with self.subTest(descriptor=descriptor):
                with self.assertRaises(OciImageReferenceError):
                    codec.decode(descriptor)

    def test_platform_matrix_and_mismatch_are_pure(self) -> None:
        image = OciImageReference(
            "ghcr.io",
            "openj92/hello",
            VALID_DIGEST,
            platforms=(OciPlatform("linux", "amd64"), OciPlatform("linux", "arm64")),
        )

        self.assertIsNone(image.require_platform(OciPlatform("linux", "amd64")))
        self.assertIsNone(image.require_platform(OciPlatform("linux", "arm64")))
        with self.assertRaisesRegex(PlatformMismatch, "linux/s390x"):
            image.require_platform(OciPlatform("linux", "s390x"))

    def test_oci_image_reference_module_has_no_effect_boundary(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "control_plane_kit_core"
            / "products.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))

        forbidden_import_roots = {
            "control_plane_kit",
            "docker",
            "fastapi",
            "httpx",
            "importlib",
            "mcp",
            "psycopg",
            "uvicorn",
        }
        roots: set[str] = set()
        dynamic_import_calls: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "__import__":
                    dynamic_import_calls.append("__import__")

        self.assertEqual(roots & forbidden_import_roots, set())
        self.assertEqual(dynamic_import_calls, [])


if __name__ == "__main__":
    unittest.main()
