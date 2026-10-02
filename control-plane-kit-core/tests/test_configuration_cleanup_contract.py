"""#1918 new-law targets for exact cleanup planning and conserved outcomes."""
from copy import deepcopy
from dataclasses import replace
import unittest

import rfc8785

import control_plane_kit_core.planning as planning
import control_plane_kit_core.runtime_effects as effects
from control_plane_kit_core.probe_intents import RuntimeEndpointObservation, LiteralEndpointMaterial, EndpointContext
from control_plane_kit_core.types import Protocol
from tests.test_configuration_instances import instance, language, ref_descriptor
from tests.test_runtime_effect_intent import _request


def cleanup(*refs):
    if not hasattr(planning, "CleanupConfigurationInstances"):
        raise AssertionError("missing #1918 CleanupConfigurationInstances operation")
    return planning.CleanupConfigurationInstances(instances=refs or (instance(),))


def cleanup_request(*refs):
    if not hasattr(effects.RuntimeEffectKind, "CONFIGURATION_ACTIVITY_V1"):
        raise AssertionError("missing #1918 configuration-activity.v1 capability")
    return replace(_request(complete=False), kind=effects.RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1,
                   operation=cleanup(*refs))


def outcome(ref, status, reason=None):
    m = language()
    return m.ConfigurationCleanupOutcome(ref, m.ConfigurationCleanupStatus(status),
        None if reason is None else m.ConfigurationCleanupReason(reason))


def helpers():
    for name in ("configuration_cleanup_result", "configuration_cleanup_outcomes"):
        if not hasattr(effects, name):
            raise AssertionError(f"missing #1918 {name}")
    return effects.configuration_cleanup_result, effects.configuration_cleanup_outcomes


class ConfigurationCleanupPlanTests(unittest.TestCase):
    def test_cleanup_preserves_same_slot_incarnations_and_one_candidate_source(self):
        a, b = instance(allocation_id="a"), instance(allocation_id="b")
        operation = cleanup(b, a)
        self.assertEqual(operation.instances, (a, b))
        descriptor = planning.activity_operation_descriptor(operation)
        self.assertEqual(descriptor, {"kind": "cleanup-configuration-instances",
            "profile": "configuration-cleanup.v1", "instances": [
                ref_descriptor(allocation_id="a"), ref_descriptor(allocation_id="b")]})
        self.assertEqual(planning.activity_operation_from_descriptor(descriptor), operation)
        for change in ({"profile": "unknown"}, {"target": {"node_id": "api"}},
                       {"instances": []}, {"instances": None}, {"instances": descriptor["instances"] * 2}):
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                planning.activity_operation_from_descriptor({**descriptor, **change})
        for values in ((a, a), (a, replace(a, content_digest="b" * 64)),
                       (a, replace(b, workspace_id="foreign")),
                       (a, replace(b, runtime_id="foreign")), (a, replace(b, node_id="foreign"))):
            with self.subTest(values=values), self.assertRaises((ValueError, TypeError)):
                cleanup(*values)
        with self.assertRaises((ValueError, TypeError)):
            planning.CleanupConfigurationInstances(instances=())
        with self.assertRaises((ValueError, TypeError)):
            cleanup(*(instance(allocation_id=f"allocation-{i}") for i in range(33)))

    def test_cleanup_plan_requires_destructive_high_risk_and_never_compensates(self):
        operation = cleanup()
        for risk in planning.RiskLevel:
            for impact in planning.ActivityImpact:
                activity = planning.PlannedActivity(planning.ActivityId("cleanup"), operation,
                                                     risk=risk, impact=impact)
                if risk in (planning.RiskLevel.HIGH, planning.RiskLevel.CRITICAL) and impact is planning.ActivityImpact.DESTRUCTIVE:
                    plan = planning.ActivityPlan((activity,))
                    self.assertEqual(activity.compensation,
                        planning.NonCompensatable(planning.NonCompensatableReason.RESOURCE_REMOVAL))
                    codec = planning.ActivityPlanDescriptorCodec()
                    self.assertEqual(codec.decode(codec.encode(plan)), plan)
                else:
                    with self.subTest(risk=risk, impact=impact), self.assertRaises(planning.InvalidActivityPlan):
                        planning.ActivityPlan((activity,))


class ConfigurationCleanupOutcomeTests(unittest.TestCase):
    def test_exact_total_outcomes_determine_status_and_fixed_redacted_failure(self):
        make, read = helpers()
        m = language()
        a, b = instance(allocation_id="a"), instance(allocation_id="b")
        request = cleanup_request(b, a)
        cases = (("already-absent", None, effects.EffectResultKind.SUCCEEDED, None, None),
            ("retained-in-use", "in-use", effects.EffectResultKind.FAILED,
             "configuration.cleanup-incomplete", "Configuration cleanup is incomplete."),
            ("refused", "provenance-unproven", effects.EffectResultKind.FAILED,
             "configuration.cleanup-incomplete", "Configuration cleanup is incomplete."),
            ("unknown", "not-attempted", effects.EffectResultKind.UNCERTAIN,
             "configuration.cleanup-uncertain", "Configuration cleanup outcome is uncertain."))
        for status, reason, kind, code, message in cases:
            with self.subTest(status=status):
                rows = m.ConfigurationCleanupOutcomeSet((outcome(b, status, reason), outcome(a, "removed")))
                result = make(request, rows)
                self.assertEqual(result.effect_id, request.effect_id)
                self.assertIs(result.kind, kind)
                self.assertEqual(result.observations, ())
                if code is None:
                    self.assertIsNone(result.failure)
                else:
                    self.assertEqual(result.failure.descriptor(), {"code": code, "message": message, "details": {}})
                expected = {"profile": "configuration-cleanup-outcomes.v1", "outcomes": [
                    {"ref": ref_descriptor(allocation_id="a"), "status": "removed", "reason": None},
                    {"ref": ref_descriptor(allocation_id="b"), "status": status, "reason": reason}]}
                self.assertEqual(result.descriptor()["evidence"], {"configuration_cleanup": expected})
                self.assertEqual(read(request, result), rows)
                codec = m.ConfigurationCleanupOutcomeSetCodec()
                self.assertEqual(codec.encode(rows), expected)
                self.assertEqual(codec.decode_canonical_bytes(codec.encode_canonical_bytes(rows)), rows)
        mixed = m.ConfigurationCleanupOutcomeSet((outcome(a, "refused", "authority-refused"),
                                                  outcome(b, "unknown", "provider-uncertain")))
        self.assertIs(make(request, mixed).kind, effects.EffectResultKind.UNCERTAIN)

    def test_missing_duplicate_extra_altered_or_empty_outcomes_cannot_mean_success(self):
        make, _ = helpers()
        m = language()
        a, b = instance(allocation_id="a"), instance(allocation_id="b")
        request = cleanup_request(a, b)
        for refs in ((), (a,), (a, a), (a, b, instance(allocation_id="c")),
                     (a, replace(b, content_digest="b" * 64)), (a, replace(b, workspace_id="foreign"))):
            with self.subTest(refs=refs), self.assertRaises((ValueError, TypeError)):
                make(request, m.ConfigurationCleanupOutcomeSet(tuple(outcome(ref, "removed") for ref in refs)))

    def test_reader_rejects_foreign_correlation_forged_success_and_extra_evidence(self):
        make, read = helpers()
        m = language()
        request = cleanup_request()
        result = make(request, m.ConfigurationCleanupOutcomeSet((outcome(instance(), "unknown", "not-attempted"),)))
        for changed in (
            replace(result, effect_id="foreign-event"),
            effects.RuntimeEffectResult.succeeded(result.effect_id, evidence=result.evidence),
            replace(result, evidence={**result.evidence, "provider_debug": "unexpected"}),
            replace(result, failure=effects.RuntimeEffectFailure("wrong", "Wrong failure.")),
            replace(result, failure=replace(result.failure, details={"extra": True})),
            replace(result, observations=(RuntimeEndpointObservation(
                subject_id="api", socket_name="http", graph_id="graph-desired",
                protocol=Protocol.HTTP, context=EndpointContext.RUNTIME_PRIVATE,
                address=LiteralEndpointMaterial("http://api:8000"),
            ),)),
            effects.RuntimeEffectResult.unsupported(result.effect_id, effects.RuntimeEffectFailure("unsupported", "Unsupported.")),
        ):
            with self.subTest(changed=changed.kind), self.assertRaises((ValueError, TypeError)):
                read(request, changed)
        with self.assertRaises((ValueError, TypeError)):
            read(_request(complete=False), result)
        payload = result.descriptor()["evidence"]["configuration_cleanup"]
        altered = deepcopy(payload)
        altered["outcomes"][0]["ref"]["content_digest"] = "b" * 64
        duplicate = {**payload, "outcomes": payload["outcomes"] * 2}
        alias = deepcopy(payload)
        alias["outcomes"][0]["instance"] = alias["outcomes"][0].pop("ref")
        for malformed in (altered, duplicate, alias, {**payload, "profile": "unknown"},
                          {**payload, "outcomes": []}, {**payload, "extra": True}):
            with self.subTest(payload=malformed), self.assertRaises((ValueError, TypeError)):
                read(request, replace(result, evidence={"configuration_cleanup": malformed}))
        codec = m.ConfigurationCleanupOutcomeSetCodec()
        row = payload["outcomes"][0]
        # Public decoding itself is strict; correlation is the reader's separate law.
        for malformed in (None, [], duplicate, alias, {**payload, "profile": "unknown"},
                          {"outcomes": payload["outcomes"]}, {**payload, "outcomes": None},
                          {**payload, "outcomes": []}, {**payload, "extra": True},
                          {**payload, "outcomes": [{**row, "extra": True}]},
                          {**payload, "outcomes": [{**row, "status": "unsupported"}]},
                          {**payload, "outcomes": [{**row, "reason": "unbounded-provider-text"}]},
                          {**payload, "outcomes": [{"ref": row["ref"], "status": "removed"}]}):
            with self.subTest(payload=malformed), self.assertRaises((ValueError, TypeError)):
                codec.decode(malformed)
        canonical = m.ConfigurationCleanupOutcomeSetCodec().encode_canonical_bytes(
            m.ConfigurationCleanupOutcomeSet((outcome(instance(), "removed"),)))
        for document in (b" " + canonical, canonical + b"\n", b'{"outcomes":[],"outcomes":[]}', b" " * 65537):
            with self.assertRaises((ValueError, TypeError)):
                m.ConfigurationCleanupOutcomeSetCodec().decode_canonical_bytes(document)

    def test_maximum_candidates_fit_existing_generic_envelope_without_larger_bounds(self):
        make, read = helpers()
        m = language()
        path = "/" + "/".join(["a" * 127] * 4)
        refs = tuple(instance(allocation_id=f"{i:02d}" + "a" * 126, workspace_id="w" * 128,
            runtime_id="r" * 128, node_id="n" * 128, artifact_id="a" * 63,
            target_path=path) for i in range(32))
        # One scope; the original request supplies membership, not current products.
        base = _request(complete=False)
        request = replace(base, kind=effects.RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1,
            source=replace(base.source, workspace_id="w" * 128), operation=cleanup(*refs))
        rows = m.ConfigurationCleanupOutcomeSet(tuple(outcome(ref, "removed") for ref in reversed(refs)))
        result = make(request, rows)
        self.assertEqual(read(request, result), rows)
        document = m.ConfigurationCleanupOutcomeSetCodec().encode_canonical_bytes(rows)
        self.assertLessEqual(len(document), 65536)
        self.assertEqual(len(result.descriptor()["evidence"]["configuration_cleanup"]["outcomes"]), 32)
        with self.assertRaises(effects.RuntimeEffectContractError):
            effects.RuntimeEffectResult.succeeded("event", evidence={"items": list(range(33))})
        with self.assertRaises(effects.RuntimeEffectContractError):
            effects.RuntimeEffectResult.succeeded("event", evidence={"text": "x" * 513})
