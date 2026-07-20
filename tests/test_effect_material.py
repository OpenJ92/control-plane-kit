from __future__ import annotations

import copy
from dataclasses import replace
import unittest

from control_plane_kit.effects import (
    EffectMaterializationError,
    EffectPurpose,
    EnvironmentMaterialSource,
    MaterializationCode,
    MaterializedEffectRequest,
    PinnedGraphSet,
    ReconcileNodeMaterial,
    SecretReferenceMaterialValue,
    effect_request_for_activity,
    effect_request_for_compensation,
    materialize_compensation_effect_request,
    materialize_effect_request,
    materialize_verification_contract,
)
from control_plane_kit.planning import Compensate, ReviewChange, compile_activity_plan
from control_plane_kit.topology import diff_graphs, validate_graph
from examples.scenarios import planning_scenarios
from examples.gate_d_live_smoke import router_recipe
from examples.webhook_delivery_live import (
    IDENTITY_REFERENCE_V1,
    IDENTITY_REFERENCE_V2,
    desired_graph as webhook_graph,
)
from control_plane_kit import HttpCheck, Protocol, VerificationContract, compile_recipe


class EffectMaterialTests(unittest.TestCase):
    def test_reconcile_material_pins_forward_and_compensation_graph_pairs(self) -> None:
        base = webhook_graph(IDENTITY_REFERENCE_V1)
        desired = webhook_graph(IDENTITY_REFERENCE_V2)
        activity = compile_activity_plan(
            diff_graphs(validate_graph(base), validate_graph(desired))
        ).activities[0]
        graphs = PinnedGraphSet("workspace", "plan", "base", "desired")

        forward = materialize_effect_request(
            effect_request_for_activity(
                activity,
                run_id="run",
                attempt=1,
                idempotency_key="reconcile:1",
            ),
            activity,
            graphs,
            base_graph_id="base",
            base_graph=base,
            desired_graph_id="desired",
            desired_graph=desired,
        )
        compensation = materialize_compensation_effect_request(
            effect_request_for_compensation(
                activity,
                run_id="run",
                attempt=1,
                idempotency_key="reconcile:compensate:1",
            ),
            activity,
            graphs,
            base_graph_id="base",
            base_graph=base,
            desired_graph_id="desired",
            desired_graph=desired,
        )

        self.assertIsInstance(forward.material, ReconcileNodeMaterial)
        self.assertIsInstance(compensation.material, ReconcileNodeMaterial)
        self.assertEqual(forward.material_graph_id, "desired")
        self.assertEqual(compensation.material_graph_id, "base")
        self.assertEqual(
            forward.material.before,
            compensation.material.after,
        )
        self.assertEqual(
            forward.material.after,
            compensation.material.before,
        )
        descriptor = forward.descriptor()["material"]
        self.assertEqual(descriptor["type"], "reconcile-node")
        self.assertNotIn("webhook-live-attestation", forward.canonical_json())

    def test_verification_contract_resolves_only_from_pinned_node_material(self) -> None:
        scenario = planning_scenarios()[0]
        desired = scenario.desired_graph
        api = desired.node("api")
        contract = VerificationContract(
            (
                HttpCheck(
                    check_id="api-semantic-check",
                    provider_socket="internal",
                    path="/internal/tests/dependencies",
                ),
            )
        )
        desired = desired.update_node(
            replace(
                api,
                block_spec=replace(api.block_spec, verification=contract),
            )
        )
        plan = compile_activity_plan(
            diff_graphs(
                validate_graph(scenario.current_graph),
                validate_graph(desired),
            )
        )
        activity = next(
            value
            for value in plan.activities
            if type(value.operation).__name__ == "StartNode"
            and value.operation.target.node_id == "api"
        )
        materialized = materialize_effect_request(
            effect_request_for_activity(
                activity,
                run_id="run",
                attempt=1,
                idempotency_key="api-start:1",
            ),
            activity,
            PinnedGraphSet("workspace", "plan", "base", "desired"),
            base_graph_id="base",
            base_graph=scenario.current_graph,
            desired_graph_id="desired",
            desired_graph=desired,
        )

        checks = materialize_verification_contract(materialized)

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].check, contract.checks[0])
        self.assertEqual(checks[0].graph_id, "desired")
        self.assertEqual(checks[0].endpoint.socket_name, "internal")
        self.assertEqual(checks[0].endpoint.protocol, Protocol.HTTP)
        self.assertEqual(
            materialized.descriptor()["material"]["verification"],
            contract.descriptor(),
        )

    def test_verification_material_rejects_missing_pinned_endpoint(self) -> None:
        scenario = planning_scenarios()[0]
        desired = scenario.desired_graph
        api = desired.node("api")
        malformed = replace(
            api,
            block_spec=replace(
                api.block_spec,
                verification=VerificationContract(
                    (
                        HttpCheck(
                            check_id="missing",
                            provider_socket="missing",
                            path="/verify",
                        ),
                    )
                ),
            ),
        )
        plan = compile_activity_plan(
            diff_graphs(
                validate_graph(scenario.current_graph),
                validate_graph(desired),
            )
        )
        activity = next(
            value
            for value in plan.activities
            if type(value.operation).__name__ == "StartNode"
            and value.operation.target.node_id == "api"
        )
        materialized = materialize_effect_request(
            effect_request_for_activity(
                activity,
                run_id="run",
                attempt=1,
                idempotency_key="api-start:1",
            ),
            activity,
            PinnedGraphSet("workspace", "plan", "base", "desired"),
            base_graph_id="base",
            base_graph=scenario.current_graph,
            desired_graph_id="desired",
            desired_graph=desired,
        )
        malformed_request = replace(
            materialized,
            material=replace(
                materialized.material,
                verification=malformed.block_spec.verification,
            ),
        )

        with self.assertRaises(EffectMaterializationError) as raised:
            materialize_verification_contract(malformed_request)

        self.assertIs(
            raised.exception.code,
            MaterializationCode.INVALID_VERIFICATION_TARGET,
        )

    def test_every_executable_scenario_operation_materializes_from_pinned_transition(self) -> None:
        materialized: list[MaterializedEffectRequest] = []
        for scenario in planning_scenarios():
            plan = compile_activity_plan(
                diff_graphs(validate_graph(scenario.current_graph), validate_graph(scenario.desired_graph))
            )
            graphs = PinnedGraphSet(
                "workspace-a",
                f"plan-{scenario.scenario_id}",
                f"base-{scenario.scenario_id}",
                f"desired-{scenario.scenario_id}",
            )
            for activity in plan.activities:
                if isinstance(activity.operation, ReviewChange):
                    continue
                request = effect_request_for_activity(
                    activity,
                    run_id=f"run-{scenario.scenario_id}",
                    attempt=1,
                    idempotency_key=f"{scenario.scenario_id}:{activity.activity_id.value}:1",
                )
                value = materialize_effect_request(
                    request,
                    activity,
                    graphs,
                    base_graph_id=graphs.base_graph_id,
                    base_graph=scenario.current_graph,
                    desired_graph_id=graphs.desired_graph_id,
                    desired_graph=scenario.desired_graph,
                )
                self.assertEqual(value.request, request)
                self.assertEqual(value.graphs, graphs)
                materialized.append(value)

        self.assertGreater(len(materialized), 20)

    def test_removal_material_comes_from_pinned_base_not_mutable_desired_graph(self) -> None:
        scenario = next(value for value in planning_scenarios() if value.scenario_id == "full-teardown")
        plan = compile_activity_plan(diff_graphs(validate_graph(scenario.current_graph), validate_graph(scenario.desired_graph)))
        activity = next(value for value in plan.activities if type(value.operation).__name__ == "StopNode")
        request = effect_request_for_activity(activity, run_id="run", attempt=1, idempotency_key="stop:1")
        graphs = PinnedGraphSet("workspace", "plan", "base", "desired")

        material = materialize_effect_request(
            request,
            activity,
            graphs,
            base_graph_id="base",
            base_graph=scenario.current_graph,
            desired_graph_id="desired",
            desired_graph=scenario.desired_graph,
        )

        self.assertEqual(material.material_graph_id, "base")
        self.assertEqual(material.material.node_id, activity.operation.target.node_id)

    def test_materialization_is_deterministic(self) -> None:
        scenario = planning_scenarios()[0]
        plan = compile_activity_plan(diff_graphs(validate_graph(scenario.current_graph), validate_graph(scenario.desired_graph)))
        activity = plan.activities[0]
        request = effect_request_for_activity(activity, run_id="run", attempt=1, idempotency_key="key")
        graphs = PinnedGraphSet("workspace", "plan", "base", "desired")

        def materialize() -> MaterializedEffectRequest:
            return materialize_effect_request(
                request,
                activity,
                graphs,
                base_graph_id="base",
                base_graph=scenario.current_graph,
                desired_graph_id="desired",
                desired_graph=scenario.desired_graph,
            )

        self.assertEqual(materialize(), materialize())
        self.assertEqual(materialize().canonical_json(), materialize().canonical_json())

    def test_materialization_preserves_and_enforces_effect_purpose(self) -> None:
        scenario = planning_scenarios()[0]
        plan = compile_activity_plan(
            diff_graphs(
                validate_graph(scenario.current_graph),
                validate_graph(scenario.desired_graph),
            )
        )
        activity = next(
            value for value in plan.activities if isinstance(value.compensation, Compensate)
        )
        graphs = PinnedGraphSet("workspace", "plan", "base", "desired")
        request = effect_request_for_compensation(
            activity,
            run_id="run",
            attempt=1,
            idempotency_key="compensate:1",
        )

        materialized = materialize_compensation_effect_request(
            request,
            activity,
            graphs,
            base_graph_id="base",
            base_graph=scenario.current_graph,
            desired_graph_id="desired",
            desired_graph=scenario.desired_graph,
        )

        self.assertIs(materialized.purpose, EffectPurpose.COMPENSATION)
        self.assertEqual(
            materialized.descriptor()["purpose"],
            EffectPurpose.COMPENSATION.value,
        )
        with self.assertRaisesRegex(
            EffectMaterializationError,
            "forward effect request",
        ):
            materialize_effect_request(
                request,
                activity,
                graphs,
                base_graph_id="base",
                base_graph=scenario.current_graph,
                desired_graph_id="desired",
                desired_graph=scenario.desired_graph,
            )

    def test_graph_identity_mismatch_fails_before_materialization(self) -> None:
        scenario = planning_scenarios()[0]
        plan = compile_activity_plan(diff_graphs(validate_graph(scenario.current_graph), validate_graph(scenario.desired_graph)))
        activity = plan.activities[0]
        request = effect_request_for_activity(activity, run_id="run", attempt=1, idempotency_key="key")

        with self.assertRaises(EffectMaterializationError) as raised:
            materialize_effect_request(
                request,
                activity,
                PinnedGraphSet("workspace", "plan", "base", "desired"),
                base_graph_id="foreign-base",
                base_graph=scenario.current_graph,
                desired_graph_id="desired",
                desired_graph=scenario.desired_graph,
            )

        self.assertIs(raised.exception.code, MaterializationCode.GRAPH_IDENTITY)

    def test_plaintext_secret_shaped_environment_is_rejected_without_value_disclosure(self) -> None:
        scenario = planning_scenarios()[0]
        desired = scenario.desired_graph
        node = desired.node("api")
        corrupted_node = copy.copy(node)
        object.__setattr__(
            corrupted_node,
            "metadata",
            {**node.metadata, "environment": {"API_TOKEN": "do-not-disclose"}},
        )
        malformed = desired.update_node(corrupted_node)
        plan = compile_activity_plan(diff_graphs(validate_graph(scenario.current_graph), validate_graph(scenario.desired_graph)))
        activity = next(value for value in plan.activities if type(value.operation).__name__ == "StartNode")
        request = effect_request_for_activity(activity, run_id="run", attempt=1, idempotency_key="key")

        with self.assertRaises(EffectMaterializationError) as raised:
            materialize_effect_request(
                request,
                activity,
                PinnedGraphSet("workspace", "plan", "base", "desired"),
                base_graph_id="base",
                base_graph=scenario.current_graph,
                desired_graph_id="desired",
                desired_graph=malformed,
            )

        self.assertIs(raised.exception.code, MaterializationCode.SECRET_VALUE)
        self.assertNotIn("do-not-disclose", str(raised.exception))

    def test_descriptor_redacts_even_nonsecret_environment_values(self) -> None:
        scenario = planning_scenarios()[0]
        plan = compile_activity_plan(
            diff_graphs(validate_graph(scenario.current_graph), validate_graph(scenario.desired_graph))
        )
        activity = next(value for value in plan.activities if type(value.operation).__name__ == "StartNode")
        request = effect_request_for_activity(activity, run_id="run", attempt=1, idempotency_key="key")
        materialized = materialize_effect_request(
            request,
            activity,
            PinnedGraphSet("workspace", "plan", "base", "desired"),
            base_graph_id="base",
            base_graph=scenario.current_graph,
            desired_graph_id="desired",
            desired_graph=scenario.desired_graph,
        )

        self.assertNotIn("Hello from API", materialized.canonical_json())
        self.assertIn("<redacted>", materialized.canonical_json())

    def test_secret_reference_material_is_opaque(self) -> None:
        value = SecretReferenceMaterialValue("secret://workspace/database")
        self.assertEqual(value.reference_id, "secret://workspace/database")

    def test_implementation_secret_reference_survives_only_as_opaque_material(self) -> None:
        desired = compile_recipe(router_recipe("hello-blue"))
        plan = compile_activity_plan(
            diff_graphs(validate_graph(type(desired)("empty")), validate_graph(desired))
        )
        activity = next(
            value
            for value in plan.activities
            if type(value.operation).__name__ == "StartNode"
            and value.operation.target.node_id == "router"
        )
        materialized = materialize_effect_request(
            effect_request_for_activity(
                activity,
                run_id="run",
                attempt=1,
                idempotency_key="router-start:1",
            ),
            activity,
            PinnedGraphSet("workspace", "plan", "base", "desired"),
            base_graph_id="base",
            base_graph=type(desired)("empty"),
            desired_graph_id="desired",
            desired_graph=desired,
        )
        control_token = next(
            value
            for value in materialized.material.implementation.environment
            if value.name == "CPK_CONTROL_TOKEN"
        )

        self.assertIsInstance(control_token.value, SecretReferenceMaterialValue)
        sources = {
            value.name: (value.source, value.source_id)
            for value in materialized.material.implementation.environment
        }
        self.assertEqual(
            sources["CPK_CONTROL_TOKEN"],
            (
                EnvironmentMaterialSource.SECRET_REFERENCE,
                "secret://gate-d/router-control",
            ),
        )
        self.assertEqual(
            sources["CPK_ROUTER_ACTIVE_TARGET"],
            (EnvironmentMaterialSource.PUBLIC_STATIC, None),
        )
        self.assertEqual(
            sources["CPK_ROUTER_BLUE_URL"],
            (EnvironmentMaterialSource.SOCKET_DERIVED, "router.target-blue"),
        )
        self.assertNotIn("gate-d-synthetic-control-token", materialized.canonical_json())


if __name__ == "__main__":
    unittest.main()
