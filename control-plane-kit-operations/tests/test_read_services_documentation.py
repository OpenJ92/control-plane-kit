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


class ReadServicesDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = DOCUMENT_PATH.read_text(encoding="utf-8")

    def test_projection_ownership_is_exactly_documented(self) -> None:
        self.assertEqual(
            _single_text_block(self.document, "Projection Ownership"),
            EXPECTED_OWNERSHIP,
        )

    def test_public_material_disclosure_is_exactly_documented(self) -> None:
        self.assertEqual(
            _single_text_block(self.document, "Public Material Disclosure"),
            EXPECTED_PUBLIC_MATERIAL,
        )


if __name__ == "__main__":
    unittest.main()
