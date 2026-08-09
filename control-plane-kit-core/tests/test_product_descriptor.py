from __future__ import annotations

from collections.abc import Iterator, Mapping
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

    def test_mapping_input_is_semantic_and_normalizes_deep_order(self) -> None:
        document = ProductDescriptorCodec().encode_document(self.product())
        mapping = self._reverse_mappings(
            json.loads(document.content.decode("utf-8"))
        )
        encoded = json.dumps(
            mapping,
            ensure_ascii=True,
            separators=(",", ":"),
        )

        self.assertNotEqual(encoded.encode("utf-8"), document.content)
        self.assertEqual(
            ProductDescriptorCodec(max_bytes=len(document.content)).decode_document(
                mapping
            ),
            document,
        )
        for source in (encoded, encoded.encode("utf-8")):
            with self.subTest(source_type=type(source).__name__):
                with self.assertRaisesRegex(ProductDescriptorError, "canonical"):
                    ProductDescriptorCodec().decode_document(source)

    def test_mapping_bound_accounts_for_exact_ascii_escape_expansion(self) -> None:
        product = self.product()
        escaped_product = ContainerServerProduct(
            identity=product.identity,
            image=product.image,
            runtime_contract=product.runtime_contract,
            display_name=product.display_name,
            description="quote:\" slash:\\ del:\x7f bmp:\u00e9 non-bmp:\U0001f4a9",
        )
        document = ProductDescriptorCodec().encode_document(escaped_product)
        mapping = self._reverse_mappings(
            json.loads(document.content.decode("utf-8"))
        )

        self.assertEqual(
            ProductDescriptorCodec(max_bytes=len(document.content)).decode_document(
                mapping
            ),
            document,
        )
        with self.assertRaises(ProductDescriptorError) as raised:
            ProductDescriptorCodec(
                max_bytes=len(document.content) - 1
            ).decode_document(mapping)
        self.assertLessEqual(len(str(raised.exception)), 128)
        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)

    def test_mapping_admission_snapshots_once_and_rejects_hostile_shape(self) -> None:
        document = ProductDescriptorCodec().encode_document(self.product())
        ordinary = json.loads(document.content.decode("utf-8"))
        one_shot = _OneShotMapping(ordinary)

        self.assertEqual(
            ProductDescriptorCodec().decode_document(one_shot),
            document,
        )
        self.assertEqual(one_shot.iterations, 1)

        cyclic: dict[str, object] = {}
        cyclic["cycle"] = cyclic
        nested: object = None
        for _ in range(66):
            nested = [nested]
        marker = "private-hostile-product-material"
        hostile_values = (
            cyclic,
            {"too-deep": nested},
            {"too-many": [None] * 300},
            {"not-finite": float("inf")},
            {"subclass": _HostileText(marker)},
            _DuplicateKeyMapping(),
            _FailingMapping(marker),
        )
        for candidate in hostile_values:
            with self.subTest(candidate_type=type(candidate).__name__):
                with self.assertRaises(ProductDescriptorError) as raised:
                    ProductDescriptorCodec(max_bytes=256).decode_document(candidate)
                self.assertLessEqual(len(str(raised.exception)), 128)
                self.assertNotIn(marker, str(raised.exception))
                self.assertIsNone(raised.exception.__context__)
                self.assertIsNone(raised.exception.__cause__)

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

    def _reverse_mappings(self, value):
        if isinstance(value, dict):
            return {
                key: self._reverse_mappings(value[key])
                for key in reversed(tuple(value))
            }
        if isinstance(value, list):
            return [self._reverse_mappings(item) for item in value]
        return value


class _OneShotMapping(Mapping[str, object]):
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values
        self.iterations = 0

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        self.iterations += 1
        if self.iterations > 1:
            raise RuntimeError("mapping was traversed more than once")
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class _DuplicateKeyMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        return "control-plane-kit.product"

    def __iter__(self) -> Iterator[str]:
        return iter(("schema", "schema"))

    def __len__(self) -> int:
        return 2


class _FailingMapping(Mapping[str, object]):
    def __init__(self, marker: str) -> None:
        self._marker = marker

    def __getitem__(self, key: str) -> object:
        raise RuntimeError(self._marker)

    def __iter__(self) -> Iterator[str]:
        raise RuntimeError(self._marker)

    def __len__(self) -> int:
        raise RuntimeError(self._marker)


class _HostileText(str):
    def __str__(self) -> str:
        raise RuntimeError(super().__str__())


if __name__ == "__main__":
    unittest.main()
