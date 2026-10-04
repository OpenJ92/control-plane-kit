"""#1902 source-derived immutable footprint, independent of receiver profile."""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import unittest

import rfc8785

from control_plane_kit_core.planning import (
    ActivityId, ActivityImpact, ActivityPlan, AllocatePublicIngress, DataResourceTarget, DestroyDataResource,
    NodeTarget, PlannedActivity, ReconcileNode, ReconcileRuntime, RemoveNodeResource,
    PublicIngressActivityTarget, RemovePublicIngress, RiskLevel, RuntimeTarget, StartNode, StartRuntime,
    StopNode, StopRuntime, SwitchSocketConnection, WaitForHealthy, compile_activity_plan,
)
from control_plane_kit_core.planning.scenarios import backend_switch
from control_plane_kit_core.topology import diff_graphs, validate_graph
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile
from tests.receiver_execution_scope_fixture import ReceiverExecutionScopeFixture
from tests.test_execution_admission import review_plan
from tests.test_runtime_effect_translation import _public_ingress_graph


class ReceiverExecutionScopeDerivationTests(ReceiverExecutionScopeFixture, unittest.TestCase):
    def test_actual_socket_record_activity_is_positive_empty(self):
        module = self.require_scopes()
        scenario = backend_switch()
        plan = compile_activity_plan(diff_graphs(validate_graph(scenario.current_graph), validate_graph(scenario.desired_graph)))
        operations = tuple(activity.operation for activity in plan.activities
                           if isinstance(activity.operation, SwitchSocketConnection))
        self.assertTrue(operations)
        source = self.source_with_operations(*operations, base_graph=scenario.current_graph,
                                             desired_graph=scenario.desired_graph)
        self.assertEqual(module.derive_execution_receiver_scopes(*source).scopes, ())

    def test_ingress_is_outside_receiver_scope_but_does_not_erase_node_work(self):
        module = self.require_scopes()
        before = _public_ingress_graph(public_ingresses=())
        after = _public_ingress_graph()
        ingress = AllocatePublicIngress(PublicIngressActivityTarget("gateway-public"))
        source = self.source_with_operations(ingress, base_graph=before, desired_graph=after)
        self.assertEqual(module.derive_execution_receiver_scopes(*source).scopes, ())
        source = self.source_with_operations(RemovePublicIngress(PublicIngressActivityTarget("gateway-public")),
                                             base_graph=after, desired_graph=before)
        self.assertEqual(module.derive_execution_receiver_scopes(*source).scopes, ())
        source = self.source_with_operations(ingress, StartNode(NodeTarget("gateway")),
                                             base_graph=before, desired_graph=after)
        self.assertEqual(module.derive_execution_receiver_scopes(*source).scopes,
                         (module.ExecutionReceiverScope("docker", "gateway"),))
        # Empty receiver footprint does not grant ingress effect permission;
        # existing authority-use admission tests remain its governing owner.

    def test_review_data_destruction_and_unknown_variants_never_become_empty_scope(self):
        module = self.require_scopes()
        identity, plan, base, desired = self.source()
        destruction = ActivityPlan((PlannedActivity(ActivityId("destroy"),
            DestroyDataResource(DataResourceTarget("app", "data")),
            risk=RiskLevel.CRITICAL, impact=ActivityImpact.DESTRUCTIVE),))
        unknown = PlannedActivity(ActivityId("unknown"), WaitForHealthy(NodeTarget("app")))
        unknown_plan = ActivityPlan((unknown,))
        # Deliberately malformed in-memory input bypasses the frozen value's
        # constructor; the derivation boundary must still reject it.
        object.__setattr__(unknown, "operation", object())
        for candidate in (review_plan(), destruction, unknown_plan):
            with self.subTest(candidate=candidate):
                with self.assertRaises(module.ReceiverScopeUnavailable):
                    module.derive_execution_receiver_scopes(identity, replace(plan, plan=candidate), base, desired)

    def test_inverse_scopes_share_the_1024_scope_cap_independently_of_activity_count(self):
        module = self.require_scopes()
        nodes = tuple("node-" + str(index) for index in range(512))
        base = self.graph(runtime="old", nodes=nodes)
        desired = self.graph(runtime="new", nodes=(*nodes, "one-extra"))
        operations = tuple(ReconcileNode(NodeTarget(node)) for node in nodes)
        source = self.source_with_operations(*operations, base_graph=base, desired_graph=desired)
        result = module.derive_execution_receiver_scopes(*source)
        self.assertEqual(len(result.scopes), 1024)
        self.assertEqual({scope.runtime_id for scope in result.scopes}, {"old", "new"})
        source = self.source_with_operations(*operations, StartNode(NodeTarget("one-extra")),
                                             base_graph=base, desired_graph=desired)
        self.assertEqual(len(source[1].plan.activities), 513)
        with self.assertRaises(module.ReceiverScopeCapacity):
            module.derive_execution_receiver_scopes(*source)

    def test_legacy_plan_without_receiver_bindings_derives_original_node_scope(self):
        module = self.require_scopes()
        source = self.source()
        self.assertIsNone(source[1].derivation_profile)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_graph_receiver_bindings").fetchone()[0], 0)
        derived = module.derive_execution_receiver_scopes(*source)
        self.assertEqual(derived.scopes, (module.ExecutionReceiverScope("docker", "app"),))
        with self.assertRaises(FrozenInstanceError):
            derived.source_digest = "0" * 64

    def test_all_stored_profiles_preserve_literal_scope_and_legacy_original_identity_rule(self):
        module = self.require_scopes()
        identity, plan, base, desired = self.source()
        for profile in (None, PlanDerivationProfile.STRUCTURAL_V1,
                        PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1):
            with self.subTest(profile=profile):
                original = replace(plan, derivation_profile=profile)
                self.assertEqual(module.derive_execution_receiver_scopes(identity, original, base, desired).scopes,
                                 (module.ExecutionReceiverScope("docker", "app"),))
        legacy = replace(plan, base_realized_projection_id=None, desired_realized_projection_id=None)
        self.assertEqual(module.derive_execution_receiver_scopes(identity, legacy, base, desired).scopes,
                         (module.ExecutionReceiverScope("docker", "app"),))

    def test_witness_matches_exact_original_stored_payload_and_projection_digests(self):
        module = self.require_scopes()
        identity, plan, base, desired = self.source()
        payload = self.connection.execute("SELECT payload FROM cpk_activity_plans WHERE plan_id=%s", (plan.plan_id,)).fetchone()[0]
        expected = {
            "profile": "receiver-execution-scopes.v1",
            "workspace_id": identity.workspace_id, "request_id": identity.request_id,
            "plan_id": plan.plan_id, "base_graph_id": plan.base_graph_id,
            "base_realized_projection_id": base.projection_id,
            "base_realized_projection_digest": base.projection_digest,
            "desired_graph_id": plan.desired_graph_id,
            "desired_realized_projection_id": desired.projection_id,
            "desired_realized_projection_digest": desired.projection_digest,
            "desired_graph_revision": plan.desired_graph_revision,
            "plan_digest": sha256(rfc8785.dumps(payload)).hexdigest(),
            "scopes": [{"scope_kind": "node", "runtime_id": "docker", "node_id": "app"}],
        }
        actual = module.derive_execution_receiver_scopes(identity, plan, base, desired)
        self.assertEqual(actual.source_digest, sha256(rfc8785.dumps(expected)).hexdigest())
        for changed in (
            replace(plan, desired_graph_revision=plan.desired_graph_revision + 1),
            replace(plan, derivation_profile=PlanDerivationProfile.STRUCTURAL_V1),
        ):
            with self.subTest(changed=changed.derivation_profile):
                self.assertNotEqual(module.derive_execution_receiver_scopes(identity, changed, base, desired).source_digest,
                                    actual.source_digest)

    def test_forward_and_base_compensation_preserve_both_relocation_runtimes(self):
        module = self.require_scopes()
        source = self.source_with_operations(
            StopNode(NodeTarget("app")), StartNode(NodeTarget("app")),
            base_graph=self.graph(runtime="old-runtime"),
            desired_graph=self.graph(runtime="new-runtime"),
        )
        derived = module.derive_execution_receiver_scopes(*source)
        self.assertEqual(derived.scopes, (
            module.ExecutionReceiverScope("new-runtime", "app"),
            module.ExecutionReceiverScope("old-runtime", "app"),
        ))

    def test_stop_inverse_uses_base_even_when_desired_node_is_elsewhere(self):
        module = self.require_scopes()
        source = self.source_with_operations(StopNode(NodeTarget("app")),
            base_graph=self.graph(runtime="old-runtime"), desired_graph=self.graph(runtime="new-runtime"))
        self.assertEqual(module.derive_execution_receiver_scopes(*source).scopes,
                         (module.ExecutionReceiverScope("old-runtime", "app"),))

    def test_runtime_and_node_operations_have_distinct_complete_scopes(self):
        module = self.require_scopes()
        graph = self.graph()
        cases = (
            (StartNode(NodeTarget("app")), "app"),
            (ReconcileNode(NodeTarget("app")), "app"),
            (RemoveNodeResource(NodeTarget("app")), "app"),
            (StartRuntime(RuntimeTarget("docker")), None),
            (StopRuntime(RuntimeTarget("docker")), None),
            (ReconcileRuntime(RuntimeTarget("docker")), None),
        )
        for operation, node in cases:
            with self.subTest(operation=operation):
                source = self.source_with_operations(operation, base_graph=graph, desired_graph=graph)
                self.assertEqual(module.derive_execution_receiver_scopes(*source).scopes,
                                 (module.ExecutionReceiverScope("docker", node),))

    def test_duplicate_scopes_are_canonical_without_dropping_distinct_nodes(self):
        module = self.require_scopes()
        source = self.source_with_operations(StartNode(NodeTarget("z")), StartNode(NodeTarget("a")),
            StartNode(NodeTarget("z")), desired_graph=self.graph(nodes=("z", "a")))
        self.assertEqual(module.derive_execution_receiver_scopes(*source).scopes,
                         (module.ExecutionReceiverScope("docker", "a"), module.ExecutionReceiverScope("docker", "z")))

    def test_health_observation_is_positive_empty_with_real_distinct_witness(self):
        module = self.require_scopes()
        source = self.source_with_operations(WaitForHealthy(NodeTarget("app")))
        derived = module.derive_execution_receiver_scopes(*source)
        self.assertEqual(derived.scopes, ())
        self.assertRegex(derived.source_digest, "^[0-9a-f]{64}$")
        self.assertNotEqual(derived.source_digest, "0" * 64)
        identity, plan, base, desired = source
        changed = module.derive_execution_receiver_scopes(replace(identity, request_id="different"), plan, base, desired)
        self.assertNotEqual(changed.source_digest, derived.source_digest)

    def test_missing_original_material_and_crossed_source_never_default_to_empty(self):
        module = self.require_scopes()
        source = self.source_with_operations(StopNode(NodeTarget("app")))
        identity, plan, base, desired = self.source()
        cases = (source, (replace(identity, workspace_id="foreign"), plan, base, desired),
                 (replace(identity, session_id="foreign"), plan, base, desired),
                 (identity, replace(plan, base_graph_id="missing"), base, desired),
                 (identity, plan, desired, base))
        for candidate in cases:
            with self.subTest(identity=candidate[0], base=candidate[2].projection_id):
                with self.assertRaises(module.ReceiverScopeUnavailable):
                    module.derive_execution_receiver_scopes(*candidate)

    def test_1024_activities_fit_and_1025_refuse_instead_of_truncation(self):
        module = self.require_scopes()
        for count in (1024, 1025):
            source = self.source_with_operations(*(WaitForHealthy(NodeTarget("app")) for _ in range(count)))
            with self.subTest(count=count):
                if count == 1024:
                    self.assertEqual(module.derive_execution_receiver_scopes(*source).scopes, ())
                else:
                    with self.assertRaises(module.ReceiverScopeCapacity):
                        module.derive_execution_receiver_scopes(*source)

    def test_combined_lookup_key_uses_utf8_bytes_at_exact_boundary(self):
        module = self.require_scopes()
        identity, plan, base, desired = self.source()
        fixed = len((identity.workspace_id + "docker" + "app").encode())
        for excess in (0, 1):
            request_id = "é" * ((1024 - fixed) // 2) + "x" * ((1024 - fixed) % 2 + excess)
            candidate = replace(identity, request_id=request_id)
            with self.subTest(bytes=len(request_id.encode()) + fixed):
                if excess:
                    with self.assertRaises(module.ReceiverScopeCapacity):
                        module.derive_execution_receiver_scopes(candidate, plan, base, desired)
                else:
                    self.assertEqual(module.derive_execution_receiver_scopes(candidate, plan, base, desired).scopes,
                                     (module.ExecutionReceiverScope("docker", "app"),))

    def cleanup_source(self, *, present=False):
        from control_plane_kit_core.planning import CleanupConfigurationInstances
        from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
        from tests.configuration_instance_fixture import configuration_ref
        graph = self.graph(nodes=("api",) if present else ())
        runtime = replace(graph.runtimes["docker"], authority_ref=RuntimeAuthorityReference("cleanup-runtime"))
        graph = replace(graph, runtimes={"docker": runtime})
        operation = CleanupConfigurationInstances((configuration_ref(),))
        return self.source_with_operations(operation, base_graph=graph, desired_graph=graph)

    def test_cleanup_derives_runtime_scope_with_present_or_departed_node(self):
        from control_plane_kit_core.operations import RunId
        from control_plane_kit_core.runtime_effect_observation import RuntimeEffectIntent, RuntimeEffectIntentSource
        from control_plane_kit_core.runtime_effects import RuntimeEffectKind
        from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
        module = self.require_scopes()
        for present in (False, True):
            with self.subTest(node_present=present):
                source = self.cleanup_source(present=present)
                identity, plan, base, desired = source
                derived = module.derive_execution_receiver_scopes(*source)
                self.assertEqual(derived.scopes, (module.ExecutionReceiverScope("docker"),))
                runtime = DEFAULT_GRAPH_CODEC.decode(desired.graph_descriptor).runtimes["docker"]
                activity = plan.plan.activities[0]
                intent = RuntimeEffectIntent(RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1, runtime.kind,
                    RuntimeEffectIntentSource(identity.workspace_id, identity.request_id, RunId("scope-cleanup-run"),
                        plan.plan_id, plan.base_graph_id, plan.desired_graph_id),
                    activity.activity_id, activity.operation, runtime.authority_ref, (), ())
                scope, _, _ = module._effect_receiver_scope(identity, (plan, base, desired), intent, compensation=False)
                self.assertEqual(scope, derived.scopes[0])
                module._validate_effect_receiver_material(identity, (plan, base, desired), derived, intent, compensation=False)

    def test_cleanup_refuses_crossed_workspace_and_either_pinned_runtime_or_authority(self):
        from control_plane_kit_core.planning import CleanupConfigurationInstances
        from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
        from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
        from control_plane_kit_core.types import RuntimeKind
        from control_plane_kit_core.operations import RunId
        from control_plane_kit_core.runtime_effect_observation import RuntimeEffectIntent, RuntimeEffectIntentSource
        from control_plane_kit_core.runtime_effects import RuntimeEffectKind
        module = self.require_scopes()
        identity, plan, base, desired = self.cleanup_source()
        candidate = plan.plan.activities[0].operation.instances[0]
        crossed = replace(plan, plan=ActivityPlan((replace(plan.plan.activities[0],
            operation=CleanupConfigurationInstances((replace(candidate, workspace_id="other-workspace"),))),)))
        cases = [("workspace", (identity, crossed, base, desired))]
        for side, projection in (("base", base), ("desired", desired)):
            graph = DEFAULT_GRAPH_CODEC.decode(projection.graph_descriptor)
            runtime = graph.runtimes["docker"]
            for name, runtimes in (
                ("missing", {}),
                ("kind", {"docker": replace(runtime, kind=next(value for value in RuntimeKind if value is not runtime.kind))}),
                ("null-authority", {"docker": replace(runtime, authority_ref=None)}),
                ("other-authority", {"docker": replace(runtime, authority_ref=RuntimeAuthorityReference("other-authority"))}),
            ):
                changed = self.projection(projection, replace(graph, runtimes=runtimes))
                cases.append((side + "-" + name, (identity, plan,
                    changed if side == "base" else base, changed if side == "desired" else desired)))
        for name, source in cases:
            with self.subTest(case=name):
                with self.assertRaises(module.ReceiverScopeUnavailable):
                    module.derive_execution_receiver_scopes(*source)
                identity, candidate_plan, candidate_base, candidate_desired = source
                activity = candidate_plan.plan.activities[0]
                runtime = DEFAULT_GRAPH_CODEC.decode(desired.graph_descriptor).runtimes["docker"]
                intent = RuntimeEffectIntent(RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1, runtime.kind,
                    RuntimeEffectIntentSource(identity.workspace_id, identity.request_id, RunId("scope-cleanup-run"),
                        plan.plan_id, plan.base_graph_id, plan.desired_graph_id),
                    activity.activity_id, activity.operation, runtime.authority_ref, (), ())
                with self.assertRaises(module.ReceiverScopeUnavailable):
                    module._effect_receiver_scope(identity, (candidate_plan, candidate_base, candidate_desired), intent,
                        compensation=False)
