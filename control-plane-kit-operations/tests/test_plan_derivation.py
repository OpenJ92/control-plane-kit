"""Closed provenance and stored-plan wire laws; no effects or fallback."""

from copy import deepcopy
from dataclasses import fields
import importlib
import importlib.util
import unittest

from control_plane_kit_core.planning import (
    ActivityPlan, DEFAULT_ACTIVITY_PLAN_CODEC, compile_activity_plan,
    compile_graph_activity_plan,
)
from control_plane_kit_core.topology import DeploymentGraph, validate_graph
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.records import ActivityPlanRecord, ActivityPlanStatus, OperationsRecordError
from tests.runtime_management_fixtures import management_graph


def require_derivation(test):
    name = "control_plane_kit_operations.plan_derivation"
    test.assertIsNotNone(importlib.util.find_spec(name), "plan derivation provenance interface is missing")
    return importlib.import_module(name)


class PlanDerivationTests(unittest.TestCase):
    def test_legacy_wire_remains_literal_core_descriptor(self):
        module = require_derivation(self)
        wire = {"schema": "control-plane-kit.activity-plan", "version": 1, "activities": []}
        self.assertEqual(module.decode_stored_activity_plan(wire), (ActivityPlan(()), None))
        self.assertEqual(module.encode_stored_activity_plan(ActivityPlan(()), profile=None), wire)

    def test_profiles_have_distinct_strict_envelopes_and_unchanged_nested_plan(self):
        module = require_derivation(self)
        plan = compile_activity_plan(Deploy(validate_graph(DeploymentGraph("empty")), validate_graph(management_graph(self))).diff)
        for profile in module.PlanDerivationProfile:
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
                             {"derivation_profile": "structural-v1"}, {"derivation_profile": "management-graph-pair-v1"}):
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

    def test_record_profile_is_nominal_keyword_only_and_absence_is_preserved(self):
        module = require_derivation(self)
        field = next(value for value in fields(ActivityPlanRecord) if value.name == "derivation_profile")
        self.assertTrue(field.kw_only)
        args = ("plan", "session", "base", "desired", ActivityPlanStatus.PLANNED, "2026-09-14T00:00:00Z", ActivityPlan(()))
        self.assertIsNone(ActivityPlanRecord(*args).derivation_profile)
        for profile in module.PlanDerivationProfile:
            self.assertIs(ActivityPlanRecord(*args, derivation_profile=profile).derivation_profile, profile)
        for profile in ("structural-v1", "bad", False):
            with self.subTest(profile=profile), self.assertRaises(OperationsRecordError):
                ActivityPlanRecord(*args, derivation_profile=profile)


if __name__ == "__main__":
    unittest.main()
