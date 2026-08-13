from __future__ import annotations

import unittest


def _route_cases() -> tuple[object, ...]:
    raise NotImplementedError("large read route parity ledger is not implemented")


class LargeReadRouteParityTests(unittest.TestCase):
    def test_literal_route_inventory_matches_public_collection_specs(self) -> None:
        _route_cases()

    def test_direct_http_and_mcp_pages_have_exact_projection_parity(self) -> None:
        _route_cases()

    def test_workspace_denial_precedes_cursor_and_store_for_every_route(self) -> None:
        _route_cases()


if __name__ == "__main__":
    unittest.main()
