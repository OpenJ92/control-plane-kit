from __future__ import annotations

import hashlib
import json
import unittest

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductDescriptorError,
    ProductIdentity,
    ProductRuntimeContract,
)
from control_plane_kit_core.types import Protocol


VALID_DIGEST = "sha256:" + "c" * 64


class ProductDescriptorCodecTests(unittest.TestCase):
    def product(self) -> ContainerServerProduct:
        return ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", "hello", 1),
            image=OciImageReference(
                "ghcr.io",
                "openj92/control-plane-kit-servers/hello",
                VALID_DIGEST,
                tag="v1",
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
            display_name="Hello server",
            description="Small HTTP server product used for live acceptance tests.",
        )

    def test_encodes_canonical_product_cpk_json_bytes(self) -> None:
        product = self.product()
        codec = ProductDescriptorCodec()

        document = codec.encode_document(product)

        self.assertEqual(document.filename, "product.cpk.json")
        self.assertEqual(document.product, product)
        self.assertEqual(document.media_type, "application/vnd.cpk.product+json")
        self.assertEqual(document.size_bytes, len(document.content))
        self.assertEqual(
            document.content_digest,
            hashlib.sha256(document.content).hexdigest(),
        )
        self.assertFalse(document.content.endswith(b"\n"))

        decoded = json.loads(document.content.decode("utf-8"))
        self.assertEqual(list(decoded), ["schema", "product"])
        self.assertEqual(decoded["schema"], "control-plane-kit.product")
        self.assertEqual(decoded["product"]["kind"], "container-server")
        self.assertEqual(document.content, codec.encode_document(product).content)

    def test_round_trips_from_bytes_text_and_mapping(self) -> None:
        codec = ProductDescriptorCodec()
        document = codec.encode_document(self.product())

        self.assertEqual(codec.decode_document(document.content), document)
        self.assertEqual(
            codec.decode_document(document.content.decode("utf-8")),
            document,
        )
        self.assertEqual(
            codec.decode_document(json.loads(document.content.decode("utf-8"))),
            document,
        )

    def test_rejects_unknown_schema_keys_and_product_escape_hatches(self) -> None:
        codec = ProductDescriptorCodec()
        document = codec.encode_document(self.product())
        descriptor = json.loads(document.content.decode("utf-8"))

        with self.assertRaisesRegex(ProductDescriptorError, "unknown keys"):
            codec.decode_document({**descriptor, "catalogue": "builtin"})

        escaped = dict(descriptor)
        escaped["product"] = {**descriptor["product"], "class_path": "pkg.Product"}
        with self.assertRaisesRegex(ProductDescriptorError, "malformed"):
            codec.decode_document(escaped)

    def test_rejects_malformed_json_and_oversized_documents(self) -> None:
        codec = ProductDescriptorCodec(max_bytes=64)

        with self.assertRaisesRegex(ProductDescriptorError, "malformed JSON"):
            codec.decode_document(b'{"schema":')

        with self.assertRaisesRegex(ProductDescriptorError, "exceeds"):
            codec.decode_document(b"{" + (b" " * 64) + b"}")

    def test_rejects_non_canonical_json_input(self) -> None:
        codec = ProductDescriptorCodec()
        document = codec.encode_document(self.product())
        pretty = json.dumps(
            json.loads(document.content.decode("utf-8")),
            indent=2,
            sort_keys=True,
        )

        with self.assertRaisesRegex(ProductDescriptorError, "canonical"):
            codec.decode_document(pretty)

    def test_rejects_wrong_schema_name_and_non_container_product(self) -> None:
        codec = ProductDescriptorCodec()
        descriptor = json.loads(codec.encode_document(self.product()).content.decode("utf-8"))

        with self.assertRaisesRegex(ProductDescriptorError, "schema"):
            codec.decode_document({**descriptor, "schema": "example.other"})

        replaced = dict(descriptor)
        replaced["product"] = {"kind": "lambda-container"}
        with self.assertRaisesRegex(ProductDescriptorError, "malformed"):
            codec.decode_document(replaced)


if __name__ == "__main__":
    unittest.main()
