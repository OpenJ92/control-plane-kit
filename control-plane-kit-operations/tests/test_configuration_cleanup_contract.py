"""C-L01/02/07, C-N04/05/06: exact cleanup values, never stored authority."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import importlib
import unittest

import rfc8785

from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import ActivityId, ActivityPlan, RiskLevel
from control_plane_kit_core.topology import DeploymentGraph, validate_graph
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.records import ActivityPlanRecord, ActivityPlanStatus
from tests.configuration_cleanup_contract_fixture import (
    cleanup_plan, inspection_wire, proposal_digest, proposal_wire, require_cleanup,
)
from tests.configuration_instance_fixture import configuration_ref


class ConfigurationCleanupContractTests(unittest.TestCase):
    def codec(self):
        return require_cleanup(self).ConfigurationCleanupProposalCodec()

    def assert_refused(self, codec, document):
        with self.assertRaises(ValueError) as raised:
            codec.decode(document)
        self.assertLessEqual(len(str(raised.exception)), 160)
        self.assertNotIn("CANARY", str(raised.exception))

    def test_selectors_preserve_distinct_incarnations_and_refuse_duplicate_seeds(self):
        module = require_cleanup(self)
        original = configuration_ref()
        later = replace(original, allocation_id="allocation-b")
        identity = EffectAttemptIdentity(RunId("run-a"), "start-api", 1)
        selector = module.ConfigurationCleanupSourceSelector(identity, original.artifact_id, original)
        self.assertEqual(selector.descriptor(), {"source_identity": {
            "run_id": "run-a", "activity_id": "start-api", "attempt": 1},
            "artifact_id": original.artifact_id, "expected_ref": ConfigurationInstanceRefCodec().encode(original)})
        other = module.ConfigurationCleanupSourceSelector(
            EffectAttemptIdentity(RunId("run-b"), "start-api", 1), later.artifact_id, later)
        command_module = importlib.import_module("control_plane_kit_operations.configuration_cleanup_planning")
        pins = module.ConfigurationCleanupExpectedContext("g", "p", "g2", "p2", 1)
        command = command_module.InspectConfigurationCleanup("session", "workspace-a", pins, (other, selector))
        self.assertEqual(command.selectors, (selector, other))
        for invalid in ((), (selector, selector), (selector, replace(selector, source_identity=other.source_identity)),
                        (selector, replace(other, expected_ref=replace(later, workspace_id="foreign")))):
            with self.subTest(selectors=invalid), self.assertRaises((TypeError, ValueError)):
                command_module.InspectConfigurationCleanup("session", "workspace-a", pins, invalid)
        with self.assertRaises(ValueError):
            module.ConfigurationCleanupSourceSelector(identity, "different-artifact", original)

    def test_inspection_union_is_closed_bounded_and_redacted(self):
        module = require_cleanup(self)
        codec = module.ConfigurationCleanupInspectionCodec()
        for kind in ("active", "terminal-unprofiled", "completed"):
            document = inspection_wire(kind=kind, unselected=1)
            with self.subTest(kind=kind):
                decoded = codec.decode(document)
                self.assertEqual(codec.encode(decoded), document)
                self.assertEqual(decoded.evidence_digest, sha256(
                    b"control-plane-kit.configuration-cleanup-inspection.v1\x00" + rfc8785.dumps(document)).hexdigest())
                self.assertNotIn("selection_allocations", repr(document))
                self.assertNotIn("completion_witnesses", repr(document))
                for field, value in (("kind", "unknown"), ("selection_count", True),
                    ("selection_count", 33), ("unselected_count", -1), ("unselected_count", 3),
                    ("extra", "CANARY")):
                    changed = deepcopy(document)
                    changed["candidates"][0]["invocations"][0][field] = value
                    self.assert_refused(codec, changed)
                changed = deepcopy(document)
                summary = changed["candidates"][0]["invocations"][0]
                if kind == "active":
                    summary["direct_event_id"] = "CANARY"
                else:
                    del summary["outcome_fingerprint"]
                self.assert_refused(codec, changed)
        for kind, results in (("completed", ("failed",)),
                              ("terminal-unprofiled", ("failed", "unsupported", "uncertain"))):
            for result in results:
                document = inspection_wire(kind=kind)
                document["candidates"][0]["invocations"][0].update(result_kind=result, attempt_status=result)
                with self.subTest(kind=kind, result=result):
                    decoded = codec.decode(document)
                    self.assertEqual(codec.encode(decoded), document)
                    self.assertEqual(decoded.evidence_digest, sha256(
                        b"control-plane-kit.configuration-cleanup-inspection.v1\x00" + rfc8785.dumps(document)).hexdigest())
        for result, status in (("succeeded", "failed"), ("unsupported", "unsupported"),
                               ("uncertain", "uncertain")):
            document = inspection_wire()
            document["candidates"][0]["invocations"][0].update(result_kind=result, attempt_status=status)
            self.assert_refused(codec, document)

    def test_proposal_roundtrip_binds_every_ref_context_and_whole_selection(self):
        module = require_cleanup(self)
        codec = self.codec()
        first = configuration_ref()
        second = replace(first, allocation_id="allocation-b", artifact_id="second", target_path="/etc/service/second.json")
        document = proposal_wire(refs=(second, first))
        proposal = codec.decode(document)
        self.assertEqual(codec.encode(proposal), document)
        self.assertEqual(module.configuration_cleanup_proposal_fingerprint(proposal), proposal_digest(document))
        for mutate in (
            lambda value: value["candidates"].pop(),
            lambda value: value["candidates"][0]["ref"].update(content_digest="f" * 64),
            lambda value: value["context"].update(base_graph_id="CANARY"),
            lambda value: value["candidates"][0]["proposed_closures"].clear(),
            lambda value: value["candidates"][0]["protecting_uses"].clear(),
            lambda value: value["candidates"][0]["seed"]["source_identity"].update(attempt=2),
            lambda value: value["candidates"][0]["birth"].update(artifact_id="different"),
            lambda value: value["candidates"][0]["completion_witnesses"][0].update(result_kind="uncertain"),
            lambda value: value.update(extra="CANARY"),
        ):
            changed = deepcopy(document)
            mutate(changed)
            self.assert_refused(codec, changed)

    def test_repeated_witnesses_require_identical_complete_commitments(self):
        codec = self.codec()
        first = configuration_ref()
        second = replace(first, allocation_id="allocation-b", artifact_id="second", target_path="/etc/second.json")
        original = proposal_wire(refs=(first, second))
        self.assertEqual(codec.encode(codec.decode(original)), original)
        missing = deepcopy(original)
        independent = proposal_wire(refs=(second,))
        row = independent["candidates"][0]
        for field in ("protecting_uses", "proposed_closures"):
            row[field][0]["run_id"] = "run-independent"
        for field in ("seed", "birth"):
            row[field]["source_identity"]["run_id"] = "run-independent"
        row["completion_witnesses"][0]["source_identity"]["run_id"] = "run-independent"
        missing["candidates"][1] = row
        self.assert_refused(codec, missing)
        for key, value in (("request_fingerprint", "e" * 64), ("outcome_fingerprint", "f" * 64),
                           ("direct_event_id", "another-event"), ("original_event_ordinal", 3)):
            changed = deepcopy(original)
            changed["candidates"][1]["completion_witnesses"][0][key] = value
            self.assert_refused(codec, changed)

    def test_canonical_values_redact_parser_failures_and_occurrence_run_ids(self):
        module = require_cleanup(self)
        for cls in (module.ConfigurationCleanupProposal, module.ConfigurationCleanupInspection):
            for malformed in (b'{"CANARY":', b'\xff', b'[' * 2000):
                with self.subTest(cls=cls, malformed=malformed[:10]):
                    with self.assertRaises(module.ConfigurationCleanupContractError) as raised:
                        cls(malformed)
                    self.assertIsNone(raised.exception.__context__)
                    self.assertNotIn("CANARY", str(raised.exception))
        for invalid in ("bad run", "r" * 201, "\U0001f680"):
            document = proposal_wire(accepted=True)
            document["context"]["current_occurrence"]["run_id"] = invalid
            self.assert_refused(self.codec(), document)

    def test_stored_cleanup_envelope_preserves_proposal_and_refuses_legacy_fallback(self):
        proposal = self.codec().decode(proposal_wire())
        module = importlib.import_module("control_plane_kit_operations.plan_derivation")
        profile = module.PlanDerivationProfile.CONFIGURATION_CLEANUP_V1
        plan = cleanup_plan(proposal_wire())
        wire = module.encode_stored_activity_plan(plan, profile=profile, cleanup_proposal=proposal)
        self.assertEqual(set(wire), {"schema", "version", "derivation_profile", "plan", "cleanup_proposal",
                                    "cleanup_proposal_fingerprint"})
        self.assertEqual((wire["version"], wire["derivation_profile"], wire["cleanup_proposal_fingerprint"]),
                         (2, "configuration-cleanup-v1", proposal_digest(proposal_wire())))
        decoded = module.decode_stored_activity_plan_record(wire)
        self.assertEqual((decoded.plan, decoded.profile, decoded.cleanup_proposal), (plan, profile, proposal))
        with self.assertRaises(ValueError):
            module.decode_stored_activity_plan(wire)
        transition = Deploy(validate_graph(DeploymentGraph("empty")), validate_graph(DeploymentGraph("empty")))
        with self.assertRaises(ValueError):
            module.derive_activity_plan(transition, profile=profile)
        for key, value in (("version", 1), ("cleanup_proposal_fingerprint", "f" * 64),
                           ("derivation_profile", "structural-v1"), ("extra", "CANARY")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                module.decode_stored_activity_plan_record({**wire, key: value})
        for legacy_profile in (None, module.PlanDerivationProfile.STRUCTURAL_V1,
                               module.PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1):
            legacy = module.encode_stored_activity_plan(plan, profile=legacy_profile)
            self.assertEqual(module.decode_stored_activity_plan(legacy), (plan, legacy_profile))
            self.assertIsNone(module.decode_stored_activity_plan_record(legacy).cleanup_proposal)
            with self.assertRaises(ValueError):
                module.encode_stored_activity_plan(plan, profile=legacy_profile, cleanup_proposal=proposal)

    def test_cleanup_plan_shape_and_record_pins_are_exact(self):
        wire = proposal_wire()
        proposal = self.codec().decode(wire)
        module = importlib.import_module("control_plane_kit_operations.plan_derivation")
        profile = module.PlanDerivationProfile.CONFIGURATION_CLEANUP_V1
        plan = cleanup_plan(wire)
        pins = wire["context"]
        record = ActivityPlanRecord("plan", pins["session_id"], pins["base_graph_id"], pins["desired_graph_id"],
            ActivityPlanStatus.PLANNED, "2026-10-03T12:00:00Z", plan,
            base_realized_projection_id=pins["base_realized_projection_id"],
            desired_realized_projection_id=pins["desired_realized_projection_id"],
            desired_graph_revision=pins["desired_graph_revision"], derivation_profile=profile, cleanup_proposal=proposal)
        for changes in (dict(session_id="foreign"), dict(base_graph_id="different"), dict(desired_graph_revision=3),
                        dict(base_realized_projection_id=None), dict(cleanup_proposal=None), dict(derivation_profile=None)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(record, **changes)
        activity = plan.activities[0]
        for invalid in (ActivityPlan(()), ActivityPlan((replace(activity, risk=RiskLevel.HIGH),)),
                        ActivityPlan((activity, replace(activity, activity_id=ActivityId("extra"))))):
            with self.subTest(plan=invalid), self.assertRaises(ValueError):
                module.encode_stored_activity_plan(invalid, profile=profile, cleanup_proposal=proposal)

    def test_64_claim_complete_envelopes_fit_for_both_occurrences_with_valid_escaping(self):
        codec = self.codec()
        module = importlib.import_module("control_plane_kit_operations.plan_derivation")
        for accepted in (False, True):
            for text in ("\U0001f680" * 512, '"' * 512, "\\" * 512):
                with self.subTest(accepted=accepted, text_kind=text[0]):
                    document = proposal_wire(accepted=accepted, claims=64, maximum=True, text=text)
                    proposal = codec.decode(document)
                    envelope = module.encode_stored_activity_plan(cleanup_plan(document),
                        profile=module.PlanDerivationProfile.CONFIGURATION_CLEANUP_V1, cleanup_proposal=proposal)
                    encoded = rfc8785.dumps(envelope)
                    self.assertLessEqual(len(encoded), 512 * 1024)
                    self.assertEqual(len(codec.encode(proposal)["candidates"][0]["completion_witnesses"]), 64)
                    self.assertEqual(module.decode_stored_activity_plan_record(envelope).cleanup_proposal, proposal)

    def test_canonical_revision_and_whole_document_capacity_never_round_or_filter(self):
        codec = self.codec()
        ref = configuration_ref()
        total_refs = tuple(replace(ref, allocation_id=f"allocation-{i}", artifact_id=f"artifact-{i}",
                                   target_path=f"/etc/config-{i}.json") for i in range(5))
        at_total_limit = proposal_wire(claims=64, refs=total_refs[:4])
        self.assertEqual(codec.encode(codec.decode(at_total_limit)), at_total_limit)
        self.assert_refused(codec, proposal_wire(claims=64, refs=total_refs))
        original = proposal_wire(accepted=True)
        for field in ("desired_graph_revision", "pinned_revision"):
            for invalid in (True, -1, 9007199254740992, 1.5):
                document = deepcopy(original)
                target = document["context"] if field == "desired_graph_revision" else document["context"]["current_occurrence"]
                target[field] = invalid
                with self.subTest(field=field, invalid=invalid):
                    self.assert_refused(codec, document)
        one = proposal_wire(accepted=True, claims=64, maximum=True)
        ref = ConfigurationInstanceRefCodec().decode(one["candidates"][0]["ref"])
        other = replace(ref, allocation_id="b" * 128, artifact_id="b" * 63,
                        target_path="/" + "/".join(["q" * 127] * 4))
        oversized = proposal_wire(accepted=True, claims=64, maximum=True, refs=(ref, other))
        self.assertGreater(len(rfc8785.dumps(oversized)), 512 * 1024)
        self.assert_refused(codec, oversized)


if __name__ == "__main__":
    unittest.main()
