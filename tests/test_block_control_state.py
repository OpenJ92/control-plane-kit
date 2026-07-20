from unittest import TestCase, main

from control_plane_kit import (
    CapabilityName,
)
from control_plane_kit.servers import (
    BlockControlState,
)


class BlockControlStateTests(TestCase):
    def test_capability_payload_uses_descriptors(self):
        state = BlockControlState(
            "router",
            capabilities=(CapabilityName.HEALTH_CHECKABLE, CapabilityName.SWITCHABLE),
        )

        self.assertEqual(
            state.capabilities_payload()["capabilities"],
            [
                {
                    "name": "health-checkable",
                    "label": "Health",
                    "description": "Node exposes health and status state through the control protocol.",
                    "route_set": "common-status",
                },
                {
                    "name": "switchable",
                    "label": "Switch",
                    "description": "Node can switch one active downstream target.",
                    "route_set": "targets",
                },
            ],
        )

    def test_target_switch_rejects_unknown_targets(self):
        state = BlockControlState("router", targets={"api-v1": "http://api-v1"})

        with self.assertRaises(KeyError):
            state.set_active_target("api-v2")

    def test_replacing_targets_clears_stale_active_target(self):
        state = BlockControlState(
            "router",
            targets={"api-v1": "http://api-v1"},
            active_target="api-v1",
        )

        payload = state.replace_targets({"api-v2": "http://api-v2"})

        self.assertEqual(payload["active_target"], "")
        self.assertEqual(payload["targets"], {"api-v2": "http://api-v2"})

    def test_observer_mutation_updates_observer_state(self):
        state = BlockControlState("mux")

        payload = state.replace_observers({"logger": "http://logger"})

        self.assertEqual(payload, {"block_id": "mux", "observers": {"logger": "http://logger"}})
        self.assertEqual(state.observers, {"logger": "http://logger"})

    def test_control_state_is_backed_by_runtime_contract(self):
        state = BlockControlState(
            "router",
            targets={"api-v1": "http://api-v1"},
            active_target="api-v1",
        )

        state.set_active_target("api-v1")

        self.assertEqual(state.runtime.get("active_target"), "api-v1")
        self.assertEqual(state.runtime.get("targets"), {"api-v1": "http://api-v1"})
        self.assertEqual(state.runtime.descriptor()["variables"]["active_target"]["value"], {
            "present": True,
            "redacted": True,
        })

    def test_status_and_log_providers_are_used_when_present(self):
        state = BlockControlState(
            "logger",
            status_provider=lambda: {"status": "custom"},
            log_provider=lambda: ["line one"],
        )

        self.assertEqual(state.status_payload(), {"status": "custom"})
        self.assertEqual(state.logs_payload(), {"block_id": "logger", "lines": ["line one"]})


if __name__ == "__main__":
    main()
