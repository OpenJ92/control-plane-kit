from __future__ import annotations

from pathlib import Path
import unittest

from control_plane_kit.servers import (
    create_instance_read_app,
)
from tests.architecture import (
    HttpRouteMethod,
    ReadOnlyRoutePolicy,
    analyze_file,
    analyze_source,
    declared_route_methods,
)


READ_MODULES = (
    "control_plane_kit.servers.instance_read",
    "examples.read_interface_demo_server",
)
READ_ROUTE_POLICY = ReadOnlyRoutePolicy(READ_MODULES)


class ArchitectureReadRouteTests(unittest.TestCase):
    def test_read_modules_declare_only_read_methods(self) -> None:
        root = Path(__file__).parents[1]
        facts = (
            analyze_file(root / "control_plane_kit/servers/instance_read.py", root=root),
            analyze_file(root / "examples/read_interface_demo_server.py", root=root),
        )

        self.assertEqual(
            tuple(
                finding
                for source in facts
                for finding in READ_ROUTE_POLICY.evaluate(source)
            ),
            (),
        )

    def test_mutation_and_generic_route_decorators_fail_closed(self) -> None:
        mutation = analyze_source(
            "@app.post('/workspaces')\n"
            "def create_workspace():\n"
            "    return {}\n",
            path="control_plane_kit/servers/instance_read.py",
            module="control_plane_kit.servers.instance_read",
        )
        generic = analyze_source(
            "@app.api_route('/workspaces', methods=['GET'])\n"
            "def workspace():\n"
            "    return {}\n",
            path="control_plane_kit/servers/instance_read.py",
            module="control_plane_kit.servers.instance_read",
        )

        findings = (
            *READ_ROUTE_POLICY.evaluate(mutation),
            *READ_ROUTE_POLICY.evaluate(generic),
        )

        self.assertEqual(len(findings), 2)
        self.assertEqual(
            {value.rule_id for value in findings},
            {"ambiguous-read-route", "read-only-route"},
        )

    def test_static_declarations_match_realized_fastapi_workspace_routes(self) -> None:
        root = Path(__file__).parents[1]
        facts = analyze_file(
            root / "control_plane_kit/servers/instance_read.py",
            root=root,
        )
        declared = declared_route_methods(facts)
        app = create_instance_read_app(object())
        realized = tuple(
            method
            for route in app.routes
            if route.path.startswith("/workspaces/")
            for method in sorted(route.methods)
        )

        self.assertEqual(len(realized), len(declared))
        self.assertEqual(set(realized), {HttpRouteMethod.GET.value})
        self.assertEqual(set(declared), {HttpRouteMethod.GET})


if __name__ == "__main__":
    unittest.main()
