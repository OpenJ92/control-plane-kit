from __future__ import annotations

import unittest

from control_plane_kit import (
    BoundedEvidence,
    DeploymentGraph,
    EffectCapability,
    EffectSucceeded,
    PinnedGraphSet,
    RuntimeEndpointObservation,
    StartRuntime,
    compile_activity_plan,
    diff_graphs,
    effect_request_for_activity,
    materialize_effect_request,
    validate_graph,
)
from control_plane_kit.effects import (
    CapabilityInterpreterRegistry,
    MaterializedEffectRequest,
)
from examples.service_infrastructure_live import (
    DISCOVERY_TOKEN,
    WEBHOOK_SIGNING_SECRET,
    WEBHOOK_TOKEN,
    RecordingEffectInterpreter,
    TransactionTracker,
    _effects,
    _endpoint_policy,
    desired_graph,
)

class ServiceInfrastructureLiveCompositionTests(unittest.TestCase):
    def test_real_registry_covers_graph_lifecycle_and_health_capabilities(self) -> None:
        graph_id = "service-live-desired"
        registry = _effects({graph_id: desired_graph()})

        self.assertIsInstance(registry, CapabilityInterpreterRegistry)
        self.assertEqual(
            registry.capabilities,
            frozenset(
                {
                    EffectCapability.NODE_LIFECYCLE,
                    EffectCapability.NODE_RECONCILIATION,
                    EffectCapability.RUNTIME_LIFECYCLE,
                    EffectCapability.DATA_DESTRUCTION,
                    EffectCapability.HEALTH_PROBE,
                }
            ),
        )

    def test_endpoint_policy_is_derived_from_exact_graph_identity(self) -> None:
        graph_id = "service-live-desired"
        policy, endpoints = _endpoint_policy({graph_id: desired_graph()})

        self.assertGreater(len(endpoints), 0)
        self.assertTrue(
            all(
                isinstance(value, RuntimeEndpointObservation)
                and value.graph_id == graph_id
                for value in endpoints.values()
            )
        )
        rendered = str((policy, endpoints))
        self.assertNotIn(DISCOVERY_TOKEN, rendered)
        self.assertNotIn(WEBHOOK_TOKEN, rendered)
        self.assertNotIn(WEBHOOK_SIGNING_SECRET, rendered)

    def test_recorder_observes_but_never_dispatches_inside_unit_of_work(self) -> None:
        tracker = TransactionTracker("postgresql://unused")
        inner = _PassingEffectInterpreter()
        recorder = RecordingEffectInterpreter(inner, tracker)
        request = _materialized_request()

        tracker.active = 1
        with self.assertRaisesRegex(AssertionError, "inside a Postgres UnitOfWork"):
            recorder.execute(request)
        self.assertEqual(inner.requests, [])
        self.assertEqual(recorder.requests, [])

        tracker.active = 0
        result = recorder.execute(request)
        self.assertIsInstance(result, EffectSucceeded)
        self.assertEqual(inner.requests, [request])
        self.assertEqual(recorder.requests, [request])

class _PassingEffectInterpreter:
    def __init__(self) -> None:
        self.requests: list[MaterializedEffectRequest] = []

    @property
    def capabilities(self) -> frozenset[EffectCapability]:
        return frozenset((EffectCapability.RUNTIME_LIFECYCLE,))

    def execute(self, request: MaterializedEffectRequest) -> EffectSucceeded:
        self.requests.append(request)
        return EffectSucceeded(
            request.identity,
            BoundedEvidence.from_mapping({"result": "passed"}),
        )


def _materialized_request() -> MaterializedEffectRequest:
    current = DeploymentGraph("service-live-empty")
    desired = desired_graph()
    plan = compile_activity_plan(
        diff_graphs(validate_graph(current), validate_graph(desired))
    )
    activity = next(
        value for value in plan.activities if isinstance(value.operation, StartRuntime)
    )
    request = effect_request_for_activity(
        activity,
        run_id="service-live-run",
        attempt=1,
        idempotency_key="service-live:start-runtime",
    )
    return materialize_effect_request(
        request,
        activity,
        PinnedGraphSet(
            "service-live-workspace",
            "service-live-plan",
            "service-live-base",
            "service-live-desired",
        ),
        base_graph_id="service-live-base",
        base_graph=current,
        desired_graph_id="service-live-desired",
        desired_graph=desired,
    )


if __name__ == "__main__":
    unittest.main()
