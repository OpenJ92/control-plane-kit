from __future__ import annotations

import importlib
import unittest


class OperationsScaffoldTests(unittest.TestCase):
    def test_operations_package_imports_after_core(self) -> None:
        module = importlib.import_module("control_plane_kit_operations")

        self.assertEqual(module.__version__, "0.1.0")
        self.assertEqual(
            module.OPERATIONS_PACKAGE_BOUNDARY.import_package,
            "control_plane_kit_operations",
        )


if __name__ == "__main__":
    unittest.main()
