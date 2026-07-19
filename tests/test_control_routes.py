from unittest import TestCase, main

from control_plane_kit import (
    COMMON_STATUS_ROUTES,
    CONTROL_ROUTE_SETS,
    ControlRouteMethod,
    ControlRouteScope,
    ControlRouteSetName,
    control_path,
    route_set_named,
)


class ControlRouteTests(TestCase):
    def test_common_status_routes_match_protocol_contract(self):
        routes = {
            route.name: (route.method, route.path, route.scope)
            for route in COMMON_STATUS_ROUTES.routes
        }

        self.assertEqual(
            routes,
            {
                "capabilities": (
                    ControlRouteMethod.GET,
                    "/__deploy/capabilities",
                    ControlRouteScope.READ_STATE,
                ),
                "health": (
                    ControlRouteMethod.GET,
                    "/__deploy/health",
                    ControlRouteScope.READ_STATE,
                ),
                "status": (
                    ControlRouteMethod.GET,
                    "/__deploy/status",
                    ControlRouteScope.READ_STATE,
                ),
            },
        )

    def test_all_required_route_sets_are_present(self):
        descriptors = {
            route_set.name: {
                (route.method, route.path)
                for route in route_set.routes
            }
            for route_set in CONTROL_ROUTE_SETS
        }

        self.assertEqual(
            set(descriptors),
            {
                ControlRouteSetName.COMMON_STATUS,
                ControlRouteSetName.LOGS,
                ControlRouteSetName.TARGETS,
                ControlRouteSetName.OBSERVERS,
                ControlRouteSetName.METRICS,
            },
        )
        self.assertEqual(
            descriptors[ControlRouteSetName.LOGS],
            {(ControlRouteMethod.GET, "/__deploy/logs")},
        )
        self.assertEqual(
            descriptors[ControlRouteSetName.TARGETS],
            {
                (ControlRouteMethod.GET, "/__deploy/targets"),
                (ControlRouteMethod.POST, "/__deploy/targets"),
                (ControlRouteMethod.POST, "/__deploy/active-target"),
                (ControlRouteMethod.POST, "/__deploy/drain-target"),
            },
        )
        self.assertEqual(
            descriptors[ControlRouteSetName.OBSERVERS],
            {
                (ControlRouteMethod.GET, "/__deploy/observers"),
                (ControlRouteMethod.POST, "/__deploy/observers"),
            },
        )
        self.assertEqual(
            descriptors[ControlRouteSetName.METRICS],
            {(ControlRouteMethod.GET, "/__deploy/metrics")},
        )

    def test_route_set_named_accepts_string_or_enum(self):
        self.assertIs(
            route_set_named("targets"),
            route_set_named(ControlRouteSetName.TARGETS),
        )

    def test_unknown_route_set_fails_loudly(self):
        with self.assertRaises(KeyError):
            route_set_named("nope")

    def test_route_sets_have_json_friendly_descriptors(self):
        descriptor = route_set_named("logs").as_descriptor()

        self.assertEqual(descriptor["name"], "logs")
        self.assertEqual(
            descriptor["routes"],
            [
                {
                    "name": "logs",
                    "method": "GET",
                    "path": "/__deploy/logs",
                    "scope": "logs:read",
                    "description": "Read block logs through the control-plane path.",
                }
            ],
        )

    def test_control_path_uses_configurable_prefix(self):
        self.assertEqual(control_path("health"), "/__deploy/health")
        self.assertEqual(control_path("/health", prefix="/private"), "/private/health")


if __name__ == "__main__":
    main()
