from __future__ import annotations

from dataclasses import dataclass, field
import unittest

from control_plane_kit.effects import (
    CapabilityInterpreterRegistry,
    EffectCapability,
    EffectDispatchError,
    EffectSucceeded,
    MaterializedEffectRequest,
    dispatch_effect,
)
from tests.test_effect_dispatch import EffectDispatchTests


@dataclass
class RecordingInterpreter:
    capabilities: frozenset[EffectCapability]
    requests: list[MaterializedEffectRequest] = field(default_factory=list)

    def execute(self, request: MaterializedEffectRequest):
        self.requests.append(request)
        return EffectSucceeded(request.identity)


class CapabilityInterpreterRegistryTests(unittest.TestCase):
    def test_dispatches_by_explicit_capability_assignment(self) -> None:
        request = EffectDispatchTests._first_executable_request()
        selected = RecordingInterpreter(frozenset({request.capability}))
        other = RecordingInterpreter(frozenset({request.capability}))
        registry = CapabilityInterpreterRegistry({request.capability: selected})

        self.assertIsInstance(dispatch_effect(request, registry), EffectSucceeded)
        self.assertEqual(selected.requests, [request])
        self.assertEqual(other.requests, [])

    def test_rejects_assignment_to_interpreter_without_capability(self) -> None:
        with self.assertRaisesRegex(EffectDispatchError, "does not advertise"):
            CapabilityInterpreterRegistry(
                {EffectCapability.HEALTH_PROBE: RecordingInterpreter(frozenset())}
            )


if __name__ == "__main__":
    unittest.main()
