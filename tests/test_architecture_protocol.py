from __future__ import annotations

from pathlib import Path
import unittest

from tests.architecture import (
    ProtocolProjectionPolicy,
    analyze_file,
    analyze_source,
    evaluate_policies,
)


PROTOCOL_PROJECTION_POLICY = ProtocolProjectionPolicy(
    scalar_display_owner_modules=(
        "control_plane_kit.core.topology.compiler",
    )
)


class ProtocolArchitectureTests(unittest.TestCase):
    def test_repository_does_not_erase_protocol_products_in_projections(self) -> None:
        root = Path(__file__).parents[1]
        facts = tuple(
            analyze_file(path, root=root)
            for path in sorted((root / "control_plane_kit").rglob("*.py"))
        )

        self.assertEqual(
            evaluate_policies(facts, (PROTOCOL_PROJECTION_POLICY,)),
            (),
        )

    def test_scalar_protocol_projection_is_rejected_but_display_is_owned(self) -> None:
        projection = analyze_source(
            "def descriptor(socket):\n    return {'protocol': socket.protocol.value}\n",
            path="control_plane_kit/projections/bypass.py",
            module="control_plane_kit.projections.bypass",
        )
        display = analyze_source(
            "def message(socket):\n    return socket.protocol.value\n",
            path="control_plane_kit/topology/compiler.py",
            module="control_plane_kit.core.topology.compiler",
        )

        findings = evaluate_policies(
            (projection, display),
            (PROTOCOL_PROJECTION_POLICY,),
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "protocol-product-erasure")
        self.assertEqual(
            findings[0].location.path,
            "control_plane_kit/projections/bypass.py",
        )


if __name__ == "__main__":
    unittest.main()
