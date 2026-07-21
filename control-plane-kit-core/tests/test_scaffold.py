import importlib
import unittest


class ScaffoldTests(unittest.TestCase):
    def test_core_package_imports_without_frozen_package(self) -> None:
        module = importlib.import_module("control_plane_kit_core")

        self.assertEqual(module.__version__, "0.1.0")
        self.assertNotIn("control_plane_kit", module.__dict__)


if __name__ == "__main__":
    unittest.main()
