from unittest import TestCase, main

from control_plane_kit import (
    CapabilityName,
    ControlRouteSetName,
    DRAINABLE,
    HEALTH_CHECKABLE,
    LOG_READABLE,
    METRICS_READABLE,
    OBSERVER_MUTABLE,
    RESTARTABLE,
    SWITCHABLE,
    TARGET_MUTABLE,
    capability_named,
)


class CapabilityTests(TestCase):
    def test_capabilities_reference_expected_route_sets(self):
        self.assertEqual(HEALTH_CHECKABLE.route_set, ControlRouteSetName.COMMON_STATUS)
        self.assertEqual(LOG_READABLE.route_set, ControlRouteSetName.LOGS)
        self.assertEqual(TARGET_MUTABLE.route_set, ControlRouteSetName.TARGETS)
        self.assertEqual(SWITCHABLE.route_set, ControlRouteSetName.TARGETS)
        self.assertEqual(DRAINABLE.route_set, ControlRouteSetName.TARGETS)
        self.assertEqual(OBSERVER_MUTABLE.route_set, ControlRouteSetName.OBSERVERS)
        self.assertEqual(METRICS_READABLE.route_set, ControlRouteSetName.METRICS)
        self.assertEqual(
            capability_named(CapabilityName.CIRCUIT_STATE_READABLE).route_set,
            ControlRouteSetName.CIRCUIT,
        )
        self.assertEqual(
            capability_named(CapabilityName.CIRCUIT_RESETTABLE).route_set,
            ControlRouteSetName.CIRCUIT,
        )

    def test_lifecycle_does_not_claim_a_route_yet(self):
        self.assertIsNone(RESTARTABLE.route_set)

    def test_capability_descriptor_is_json_friendly(self):
        self.assertEqual(
            SWITCHABLE.as_descriptor(),
            {
                "name": "switchable",
                "label": "Switch",
                "description": "Node can switch one active downstream target.",
                "route_set": "targets",
            },
        )

    def test_capability_named_accepts_string_or_enum(self):
        self.assertIs(
            capability_named("switchable"),
            capability_named(CapabilityName.SWITCHABLE),
        )

    def test_unknown_capability_fails_loudly(self):
        with self.assertRaises(KeyError):
            capability_named("teleportable")


if __name__ == "__main__":
    main()
