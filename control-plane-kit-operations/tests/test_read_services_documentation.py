from __future__ import annotations

import os
from pathlib import Path
import unittest


DEFAULT_DOCUMENT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "READ_INTERFACES.md"
)
DOCUMENT_PATH = Path(
    os.environ.get(
        "CPK_READ_INTERFACES_DOCUMENT",
        DEFAULT_DOCUMENT_PATH,
    )
)

EXPECTED_OWNERSHIP = """Postgres stores
  -> private projection families
    - workspace_graph
    - operations_history
    - observations
    - authority_secrets
    - gateway_security
  -> InstanceReadService (explicit composition only)
    -> CpkServerReadService
      -> shared CpkServerOperationsApplication
        -> HTTP routes
        -> MCP resources/tools"""

EXPECTED_PUBLIC_MATERIAL = """delegation signing-key inventory
  includes: bounded public metadata
  omits: public_key_pem, private_key_reference
gateway verifier configuration
  includes: bounded public_key_pem, derived public environment
  disclosure: purpose-limited public verification material, not a secret
all read surfaces
  forbid: private-key bytes, private-key references, provider credentials, resolved secret values"""

FORBIDDEN_CLAIMS = (
    "Postgres stores\n  -> InstanceReadService",
    "delegation signing-key inventory\n  includes: public_key_pem",
    "gateway verifier configuration\n  omits: public_key_pem",
)


def _single_text_block(document: str, heading: str) -> str:
    marker = f"## {heading}\n"
    if document.count(marker) != 1:
        raise AssertionError(f"expected exactly one {heading!r} section")
    section = document.split(marker, 1)[1].split("\n## ", 1)[0]
    opening = "```text\n"
    if section.count(opening) != 1:
        raise AssertionError(
            f"expected exactly one structured text block in {heading!r}"
        )
    block = section.split(opening, 1)[1]
    if block.count("\n```") != 1:
        raise AssertionError(
            f"expected exactly one closed text block in {heading!r}"
        )
    return block.split("\n```", 1)[0]


def _validate_document(document: str) -> None:
    if _single_text_block(document, "Projection Ownership") != EXPECTED_OWNERSHIP:
        raise AssertionError("projection ownership declaration is not exact")
    if (
        _single_text_block(document, "Public Material Disclosure")
        != EXPECTED_PUBLIC_MATERIAL
    ):
        raise AssertionError("public material declaration is not exact")
    for claim in FORBIDDEN_CLAIMS:
        if claim in document:
            raise AssertionError(f"superseded read-interface claim remains: {claim!r}")


class ReadServicesDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = DOCUMENT_PATH.read_text(encoding="utf-8")

    def test_projection_ownership_is_exactly_documented(self) -> None:
        _validate_document(self.document)

    def test_public_material_disclosure_is_exactly_documented(self) -> None:
        _validate_document(self.document)

    def test_duplicate_declarations_and_blocks_are_rejected(self) -> None:
        for heading in ("Projection Ownership", "Public Material Disclosure"):
            marker = f"## {heading}\n"
            section = self.document.split(marker, 1)[1].split("\n## ", 1)[0]
            with self.subTest(heading=heading, duplicate="declaration"):
                with self.assertRaisesRegex(AssertionError, "exactly one"):
                    _validate_document(
                        f"{self.document}\n\n{marker}{section}"
                    )

            opening = "```text\n"
            block = section.split(opening, 1)[1].split("\n```", 1)[0]
            duplicate_block = section.replace(
                f"{opening}{block}\n```",
                f"{opening}{block}\n```\n\n{opening}{block}\n```",
                1,
            )
            with self.subTest(heading=heading, duplicate="block"):
                with self.assertRaisesRegex(AssertionError, "exactly one"):
                    _validate_document(
                        self.document.replace(section, duplicate_block, 1)
                    )

    def test_superseded_topology_and_disclosure_claims_are_rejected(self) -> None:
        for claim in FORBIDDEN_CLAIMS:
            with self.subTest(claim=claim):
                with self.assertRaisesRegex(AssertionError, "superseded"):
                    _validate_document(f"{self.document}\n\n{claim}\n")


if __name__ == "__main__":
    unittest.main()
