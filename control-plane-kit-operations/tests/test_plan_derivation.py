"""Closed provenance and stored-plan wire laws; no effects or fallback."""

from copy import deepcopy
from dataclasses import fields, replace
import importlib
import importlib.util
import unittest

from control_plane_kit_core.planning import (
    ActivityPlan, DEFAULT_ACTIVITY_PLAN_CODEC, compile_activity_plan,
    compile_graph_activity_plan, compile_managed_update_activity_plan,
)
from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationMediaType
from control_plane_kit_core.topology import DeploymentGraph, validate_graph
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.records import ActivityPlanRecord, ActivityPlanStatus, OperationsRecordError
from tests.runtime_management_fixtures import bootstrap_management_graph, management_graph


def require_derivation(test):
    name = "control_plane_kit_operations.plan_derivation"
    test.assertIsNotNone(importlib.util.find_spec(name), "plan derivation provenance interface is missing")
    return importlib.import_module(name)


def managed_update_pair(test):
    """Generic Core values derived from accepted SDK-only Servers #241 shape."""
    def routes(*node_ids):
        import json
        return ConfigurationArtifact(
            "management-routes", "/etc/cpk/gateway/routes.json",
            ConfigurationMediaType.JSON,
            json.dumps({"targets": list(node_ids)}, sort_keys=True, separators=(",", ":")),
        )

    current = bootstrap_management_graph(test)
    gateway = replace(current.node("gateway"), configuration_artifacts=(routes("api"),))
    current = replace(current, nodes={**current.nodes, "gateway": gateway})
    added = replace(
        current.node("api"),
        node_id="api-y",
        block_spec=replace(current.node("api").block_spec, role_id="api-y"),
    )
    desired_gateway = replace(gateway, configuration_artifacts=(routes("api", "api-y"),))
    runtime = current.runtimes["docker"]
    desired = replace(
        current,
        nodes={**current.nodes, "gateway": desired_gateway, "api-y": added},
        runtimes={**current.runtimes, "docker": replace(runtime, children=runtime.children + ("api-y",))},
    )
    current = validate_graph(current)
    desired = validate_graph(desired)
    test.assertTrue(current.valid, current.descriptor())
    test.assertTrue(desired.valid, desired.descriptor())
    return current, desired


class PlanDerivationTests(unittest.TestCase):
    def test_managed_intent_is_distinct_typed_and_never_an_arbitrary_profile(self):
        from control_plane_kit_operations import planning
        from control_plane_kit_operations.workflows import IdempotencyKey, InvalidOperationCommand
        module = require_derivation(self)
        command = planning.RequestActivityPlan("session", "workspace", "actor", "current", "desired", IdempotencyKey("key"))
        value = module.ManagedVerificationPlanningIntent.AUTHENTICATED_LOCAL_POSTGRES_AND_HTTP
        selected = replace(command, managed_verification=value)
        self.assertEqual(selected.descriptor(), {**command.descriptor(), "managed_verification": "authenticated-local-postgres-and-http"})
        for bad in ("authenticated-local-postgres-and-http", True, "managed-sql-http-bootstrap-v1"):
            with self.assertRaises(InvalidOperationCommand):
                replace(command, managed_verification=bad)
        with self.assertRaises(InvalidOperationCommand):
            replace(selected, http_verification_intent=module.HttpVerificationPlanningIntent.AUTHENTICATED_SAMPLES)

    def test_managed_profile_action_markers_are_exact_and_old_profiles_do_not_upgrade(self):
        module = require_derivation(self)
        profile = module.PlanDerivationProfile.MANAGED_SQL_HTTP_BOOTSTRAP_V1
        evidence = dict(derivation_profile=profile.value, managed_verification="authenticated-local-postgres-and-http")
        self.assertTrue(module.planning_derivation_matches_action(profile, evidence))
        for changed in ({"derivation_profile": profile.value}, {**evidence, "managed_verification": None},
                {**evidence, "http_verification_intent": "authenticated-samples"},
                {**evidence, "managed_verification": "authenticated-samples"}):
            self.assertFalse(module.planning_derivation_matches_action(profile, changed))
        for old in (None, module.PlanDerivationProfile.MANAGED_HTTP_SAMPLE_BOOTSTRAP_V1):
            self.assertFalse(module.planning_derivation_matches_action(old, evidence))
        plan = ActivityPlan(())
        self.assertEqual(module.decode_stored_activity_plan(module.encode_stored_activity_plan(plan, profile=profile)), (plan, profile))
        from control_plane_kit_operations.runtime_management_admission import runtime_management_execution_is_unsupported
        self.assertTrue(runtime_management_execution_is_unsupported(DeploymentGraph("empty"), DeploymentGraph("empty"),
            plan, derivation_profile=profile))
        empty = validate_graph(DeploymentGraph("empty"))
        self.assertFalse(module.derive_activity_plan(Deploy(empty, empty), profile=profile).ready_for_execution)

    def test_inline_and_saved_prepare_keep_one_managed_intent_and_refuse_mixed(self):
        from control_plane_kit_operations import deployment_program as program
        from tests.test_deployment_program import _prepare, _lineage
        module = require_derivation(self)
        value = module.ManagedVerificationPlanningIntent.AUTHENTICATED_LOCAL_POSTGRES_AND_HTTP
        inline = _prepare(program, managed_verification=value)
        saved = _prepare(program, managed_verification=value, desired=program.SavedDesiredTopologyRevision("draft-a", 1),
            expected_desired=_lineage("desired"), expected_desired_graph_revision=1)
        for command in (inline, saved):
            self.assertEqual(command.descriptor()["managed_verification"], value.value)
            self.assertNotIn("http_verification_intent", command.descriptor())
            with self.assertRaises(ValueError):
                replace(command, http_verification_intent=module.HttpVerificationPlanningIntent.AUTHENTICATED_SAMPLES)

    def test_wire_managed_selector_refuses_null_mixed_and_free_profile(self):
        from control_plane_kit_operations.cpk_server import _managed_verification_planning_intent, _http_verification_planning_intent, CpkServerApplicationError
        module = require_derivation(self)
        self.assertIsNone(_managed_verification_planning_intent({}))
        self.assertIs(_managed_verification_planning_intent({"managed_verification": "authenticated-local-postgres-and-http"}),
            module.ManagedVerificationPlanningIntent.AUTHENTICATED_LOCAL_POSTGRES_AND_HTTP)
        for payload in ({"managed_verification": None}, {"managed_verification": True},
                {"managed_verification": "managed-sql-http-bootstrap-v1"},
                {"managed_verification": "authenticated-local-postgres-and-http", "http_verification_intent": "authenticated-samples"}):
            with self.assertRaises(CpkServerApplicationError):
                _managed_verification_planning_intent(payload)
        with self.assertRaises(CpkServerApplicationError):
            _http_verification_planning_intent({"derivation_profile": "managed-sql-http-bootstrap-v1"})

    def test_http_sample_intent_is_typed_and_omission_preserves_old_descriptor(self):
        import hashlib
        import json
        from control_plane_kit_operations import planning
        from control_plane_kit_operations.workflows import IdempotencyKey, InvalidOperationCommand
        command = planning.RequestActivityPlan("session", "workspace", "actor", "current", "desired", IdempotencyKey("key"))
        literal = {"command": "request-activity-plan", "session_id": "session", "workspace_id": "workspace",
            "actor_id": "actor", "expected_current_graph_id": "current", "expected_desired_graph_id": "desired",
            "expected_current_realized_projection_id": None, "expected_desired_realized_projection_id": None,
            "expected_desired_graph_revision": None, "idempotency_key": "key"}
        self.assertEqual(command.descriptor(), literal)
        expected = hashlib.sha256(json.dumps(literal, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(planning._activity_plan_fingerprint(command), expected)
        intent = getattr(planning, "HttpVerificationPlanningIntent", None)
        self.assertIsNotNone(intent, "typed sample planning intent is missing")
        selected = replace(command, http_verification_intent=intent.AUTHENTICATED_SAMPLES)
        self.assertEqual(selected.descriptor(), {**literal, "http_verification_intent": "authenticated-samples"})
        self.assertNotEqual(planning._activity_plan_fingerprint(selected), expected)
        for value in ("authenticated-samples", "managed-http-sample-bootstrap-v1", False, {}):
            with self.subTest(value=value), self.assertRaises(InvalidOperationCommand):
                replace(command, http_verification_intent=value)

    def test_http_sample_profile_roundtrips_and_denies_execution_before_generic_exemptions(self):
        module = require_derivation(self)
        profile = getattr(module.PlanDerivationProfile, "MANAGED_HTTP_SAMPLE_BOOTSTRAP_V1", None)
        self.assertIsNotNone(profile, "explicit sample profile is missing")
        self.assertEqual(profile.value, "managed-http-sample-bootstrap-v1")
        plan = ActivityPlan(())
        self.assertEqual(module.decode_stored_activity_plan(module.encode_stored_activity_plan(plan, profile=profile)), (plan, profile))
        from control_plane_kit_operations.runtime_management_admission import runtime_management_execution_is_unsupported
        empty = DeploymentGraph("empty")
        self.assertTrue(runtime_management_execution_is_unsupported(empty, empty, plan, derivation_profile=profile))
        derived = module.derive_activity_plan(Deploy(validate_graph(empty), validate_graph(empty)), profile=profile)
        self.assertFalse(derived.ready_for_execution)

    def test_legacy_wire_remains_literal_core_descriptor(self):
        module = require_derivation(self)
        wire = {"schema": "control-plane-kit.activity-plan", "version": 1, "activities": []}
        self.assertEqual(module.decode_stored_activity_plan(wire), (ActivityPlan(()), None))
        self.assertEqual(module.encode_stored_activity_plan(ActivityPlan(()), profile=None), wire)

    def test_profiles_have_distinct_strict_envelopes_and_unchanged_nested_plan(self):
        module = require_derivation(self)
        plan = compile_activity_plan(Deploy(validate_graph(DeploymentGraph("empty")), validate_graph(management_graph(self))).diff)
        for profile in (module.PlanDerivationProfile.STRUCTURAL_V1,
                        module.PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1,
                        module.PlanDerivationProfile.MANAGED_UPDATE_V1):
            with self.subTest(profile=profile):
                expected = {"schema": "control-plane-kit.operations.activity-plan-record", "version": 1,
                            "derivation_profile": profile.value, "plan": DEFAULT_ACTIVITY_PLAN_CODEC.encode(plan)}
                self.assertEqual(module.encode_stored_activity_plan(plan, profile=profile), expected)
                self.assertEqual(module.decode_stored_activity_plan(expected), (plan, profile))
                with self.assertRaises(ValueError):
                    DEFAULT_ACTIVITY_PLAN_CODEC.decode(expected)

    def test_unknown_and_malformed_envelopes_never_fall_back_or_leak_context(self):
        module = require_derivation(self)
        valid = {"schema": "control-plane-kit.operations.activity-plan-record", "version": 1,
                 "derivation_profile": "structural-v1", "plan": DEFAULT_ACTIVITY_PLAN_CODEC.encode(ActivityPlan(()))}
        cases = [None, [], 1, {}, {**valid, "schema": "WIRE-CANARY"},
                 {**valid, "version": True}, {**valid, "version": 2},
                 {**valid, "derivation_profile": None}, {**valid, "derivation_profile": "PROFILE-CANARY"},
                 {**valid, "extra": "EXTRA-CANARY"}, {**valid, "plan": valid},
                 {**valid, "plan": {"schema": "control-plane-kit.activity-plan", "version": 1, "activities": ["NESTED-CANARY"]}}]
        for key in valid:
            missing = deepcopy(valid)
            del missing[key]
            cases.append(missing)
        for candidate in cases:
            with self.subTest(candidate=candidate):
                with self.assertRaises(module.PlanDerivationError) as error:
                    module.decode_stored_activity_plan(candidate)
                self.assertLessEqual(len(str(error.exception)), 120)
                self.assertNotIn("CANARY", str(error.exception))
                self.assertIsNone(error.exception.__cause__)
                self.assertIsNone(error.exception.__context__)

    def test_presence_binding_does_not_conflate_legacy_null_or_equal_profiles(self):
        module = require_derivation(self)
        for profile in (None, *module.PlanDerivationProfile):
            for evidence in ({}, {"derivation_profile": None}, {"derivation_profile": "unknown"},
                             {"derivation_profile": "structural-v1"},
                             {"derivation_profile": "management-graph-pair-v1"},
                             {"derivation_profile": "managed-update-v1"}):
                with self.subTest(profile=profile, evidence=evidence):
                    expected = ("derivation_profile" not in evidence if profile is None
                                else evidence.get("derivation_profile") == profile.value)
                    self.assertEqual(module.planning_derivation_matches_action(profile, evidence), expected)

    def test_profile_selects_observably_different_derivation_before_comparison(self):
        module = require_derivation(self)
        transition = Deploy(validate_graph(DeploymentGraph("empty")), validate_graph(management_graph(self)))
        structural = compile_activity_plan(transition.diff)
        managed = compile_graph_activity_plan(transition.current, transition.desired)
        self.assertNotEqual(structural, managed)
        self.assertEqual(module.derive_activity_plan(transition, profile=None), structural)
        self.assertEqual(module.derive_activity_plan(transition, profile=module.PlanDerivationProfile.STRUCTURAL_V1), structural)
        self.assertEqual(module.derive_activity_plan(transition, profile=module.PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1), managed)
        for profile in ("structural-v1", "unknown", 1):
            with self.subTest(profile=profile), self.assertRaises(module.PlanDerivationError):
                module.derive_activity_plan(transition, profile=profile)

    def test_managed_update_profile_dispatches_exact_pure_core_derivation(self):
        module = require_derivation(self)
        current, desired = managed_update_pair(self)
        transition = Deploy(current, desired)
        expected = compile_managed_update_activity_plan(current, desired)
        profile = module.PlanDerivationProfile.MANAGED_UPDATE_V1
        self.assertEqual(module.derive_activity_plan(transition, profile=profile), expected)
        self.assertEqual(profile.value, "managed-update-v1")
        encoded = module.encode_stored_activity_plan(expected, profile=profile)
        self.assertEqual(encoded["derivation_profile"], "managed-update-v1")
        self.assertEqual(module.decode_stored_activity_plan(encoded), (expected, profile))

    def test_typed_profiles_and_exact_wire_strings_are_distinct_boundaries(self):
        module = require_derivation(self)
        profile = module.PlanDerivationProfile.STRUCTURAL_V1
        for invalid in ("structural-v1", True, False):
            with self.subTest(invalid=invalid), self.assertRaises(module.PlanDerivationError):
                module.encode_stored_activity_plan(ActivityPlan(()), profile=invalid)
        self.assertFalse(module.planning_derivation_matches_action(profile, {"derivation_profile": profile}))
        wire = {"schema": "control-plane-kit.operations.activity-plan-record", "version": 1,
                "derivation_profile": profile, "plan": DEFAULT_ACTIVITY_PLAN_CODEC.encode(ActivityPlan(()))}
        with self.assertRaises(module.PlanDerivationError) as error:
            module.decode_stored_activity_plan(wire)
        self.assertIsNone(error.exception.__cause__)
        self.assertIsNone(error.exception.__context__)

    def test_record_profile_is_nominal_keyword_only_and_absence_is_preserved(self):
        module = require_derivation(self)
        field = next(value for value in fields(ActivityPlanRecord) if value.name == "derivation_profile")
        self.assertTrue(field.kw_only)
        args = ("plan", "session", "base", "desired", ActivityPlanStatus.PLANNED, "2026-09-14T00:00:00Z", ActivityPlan(()))
        self.assertIsNone(ActivityPlanRecord(*args).derivation_profile)
        for profile in (module.PlanDerivationProfile.STRUCTURAL_V1,
                        module.PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1,
                        module.PlanDerivationProfile.MANAGED_UPDATE_V1):
            self.assertIs(ActivityPlanRecord(*args, derivation_profile=profile).derivation_profile, profile)
        for profile in ("structural-v1", "bad", False):
            with self.subTest(profile=profile), self.assertRaises(OperationsRecordError):
                ActivityPlanRecord(*args, derivation_profile=profile)


if __name__ == "__main__":
    unittest.main()
