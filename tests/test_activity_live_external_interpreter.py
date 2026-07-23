from __future__ import annotations

import ast
from pathlib import Path
import unittest


HARNESS = Path("examples/activity_seeded_live.py")
SCRIPT = Path("activity-seeded-live-test.sh")


class ActivityLiveExternalInterpreterTests(unittest.TestCase):
    def test_live_harness_composes_external_docker_interpreter_at_boundary(self) -> None:
        source = HARNESS.read_text()
        tree = ast.parse(source)

        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        from_imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        self.assertIn("control_plane_kit_interpreters.docker", from_imports)
        self.assertIn("DockerRuntimeInterpreter", calls)
        self.assertIn("RuntimeInterpreterDispatcher", calls)
        self.assertNotIn("control_plane_kit_operations.docker_realization", imports)
        self.assertNotIn(
            "control_plane_kit_operations.docker_realization",
            from_imports,
        )

    def test_live_script_mounts_interpreters_repo_as_composition_input(self) -> None:
        script = SCRIPT.read_text()

        self.assertIn("CPK_INTERPRETERS_REPO", script)
        self.assertIn("/workspace/control-plane-kit-interpreters:ro", script)
        self.assertIn(
            "PYTHONPATH=/workspace/control-plane-kit-interpreters/src",
            script,
        )


if __name__ == "__main__":
    unittest.main()
