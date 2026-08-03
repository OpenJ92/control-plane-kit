from __future__ import annotations

import unittest

from extraction_parity.demos import DemoInventoryError, validate_demo_inventory


def _document() -> dict[str, object]:
    return {
        "schema": "cpk.reference-demo-inventory",
        "reference": {"tag": "tag", "commit": "commit"},
        "demos": [
            {
                "id": "demo.read-interface",
                "scripts": ["scripts/up.sh", "scripts/down.sh"],
                "fixtures": [],
                "documentation": ["docs/demo.md"],
                "kind": "live-demo",
                "owner_kind": "core",
                "owner": "control-plane-kit-core",
                "bootstrap_state": "required",
                "prerequisites": ["Docker"],
                "inputs": ["authenticated HTTP request"],
                "observables": ["HTTP 200 projection"],
                "normalization": ["allocated-port"],
                "cleanup": ["owned containers removed"],
            }
        ],
    }


class ReferenceDemoInventoryTests(unittest.TestCase):
    def test_every_discovered_script_is_accounted_for_exactly_once(self) -> None:
        validate_demo_inventory(
            _document(), discovered_scripts=frozenset({"scripts/up.sh", "scripts/down.sh"}), discovered_fixtures=frozenset()
        )

        with self.assertRaises(DemoInventoryError):
            validate_demo_inventory(_document(), discovered_scripts=frozenset({"scripts/up.sh"}), discovered_fixtures=frozenset())

    def test_unknown_fields_states_and_semantic_normalization_fail_closed(self) -> None:
        document = _document()
        document["demos"][0]["extra"] = True
        with self.assertRaises(DemoInventoryError):
            validate_demo_inventory(
                document, discovered_scripts=frozenset({"scripts/up.sh", "scripts/down.sh"}), discovered_fixtures=frozenset()
            )

        document = _document()
        document["demos"][0]["normalization"] = ["http-status"]
        with self.assertRaises(DemoInventoryError):
            validate_demo_inventory(
                document, discovered_scripts=frozenset({"scripts/up.sh", "scripts/down.sh"}), discovered_fixtures=frozenset()
            )


if __name__ == "__main__":
    unittest.main()
