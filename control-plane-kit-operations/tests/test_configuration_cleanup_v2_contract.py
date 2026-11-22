"""#1944 E4-L01/02/03/07/08/11: syntax laws, never database authority."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import unittest

import rfc8785

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject, approval_subject_from_descriptor
from control_plane_kit_core.planning import ActivityPlan
from control_plane_kit_core.topology import DeploymentGraph, validate_graph
from control_plane_kit_operations import configuration_cleanup as values
from control_plane_kit_operations import plan_derivation as plans
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.records import ActivityPlanRecord, ActivityPlanStatus
from tests.configuration_cleanup_contract_fixture import cleanup_plan, proposal_wire, proposal_digest
from tests.configuration_cleanup_v2_fixture import key, v2_wire, v2_inspection
from tests.configuration_instance_fixture import configuration_ref


class ConfigurationCleanupV2ContractTests(unittest.TestCase):
    def codec(self, *, inspection=False):
        name = "ConfigurationCleanupInspectionV2Codec" if inspection else "ConfigurationCleanupProposalV2Codec"
        self.assertTrue(hasattr(values, name), "#1944 separate v2 closed value/codec is missing")
        return getattr(values, name)()

    def refused(self, codec, wire):
        with self.assertRaises(ValueError) as caught:
            codec.decode(wire)
        self.assertLessEqual(len(str(caught.exception)), 160)
        self.assertNotIn("CANARY", str(caught.exception))
        self.assertIsNone(caught.exception.__context__)

    def two(self):
        a = replace(configuration_ref(), artifact_id="alpha", allocation_id="z-allocation", target_path="/etc/alpha")
        b = replace(a, artifact_id="beta", allocation_id="a-allocation", target_path="/etc/beta")
        return proposal_wire(refs=(a, b)), a, b

    def test_v1_bytes_types_digests_and_rejections_remain_exact(self):
        original = proposal_wire()
        v1 = values.ConfigurationCleanupProposalCodec().decode(original)
        self.assertEqual(v1.canonical, rfc8785.dumps(original))
        self.assertEqual(values.configuration_cleanup_proposal_fingerprint(v1), proposal_digest(original))
        codec = self.codec()
        v2 = codec.decode(v2_wire(original))
        self.assertEqual(codec.encode(v2), v2_wire(original))
        self.assertNotEqual(type(v1), type(v2))
        self.assertEqual(values.configuration_cleanup_proposal_fingerprint(v2), sha256(
            b"control-plane-kit.configuration-cleanup-proposal.v2\x00" + v2.canonical).hexdigest())
        self.assertNotEqual(values.configuration_cleanup_proposal_fingerprint(v2), proposal_digest(original))
        self.refused(codec, original)
        self.refused(values.ConfigurationCleanupProposalCodec(), v2_wire(original))
        for wrong in (v1, object(), v1.canonical):
            with self.assertRaises(ValueError):
                codec.encode(wrong)
        for malformed in (b'{"CANARY":', b'\xff', b'[' * 2000, b' {} '):
            with self.assertRaises(ValueError) as caught:
                type(v2)(malformed)
            self.assertIsNone(caught.exception.__context__)

    def test_shared_use_partition_orders_members_by_artifact_and_candidates_by_allocation(self):
        original, a, b = self.two()
        identity = key(original["candidates"][0]["protecting_uses"][0])
        wire = v2_wire(original, transferred=((identity, a.artifact_id),))
        codec = self.codec()
        self.assertEqual(codec.encode(codec.decode(wire)), wire)
        self.assertEqual([row["ref"]["allocation_id"] for row in wire["candidates"]], [b.allocation_id, a.allocation_id])
        self.assertEqual([row["artifact_id"] for row in wire["invocations"][0]["selection_members"]], ["alpha", "beta"])
        self.assertEqual(wire["candidates"][1]["protecting_uses"], [])
        self.assertEqual(len(wire["accepted_transfers"]), 1)
        for mutate in (
            lambda d: d["accepted_transfers"].clear(),
            lambda d: d["accepted_transfers"].append(deepcopy(d["accepted_transfers"][0])),
            lambda d: d["candidates"][1]["protecting_uses"].append(deepcopy(d["invocations"][0]["source_identity"])),
            lambda d: d["accepted_transfers"][0].update(allocation_id="foreign"),
            lambda d: d["accepted_transfers"][0].update(artifact_id="foreign"),
            lambda d: d["accepted_transfers"][0].update(ref_fingerprint="f" * 64),
            lambda d: d["invocations"][0]["selection_members"].reverse(),
            lambda d: d["invocations"][0]["selection_members"][1].update(artifact_id="alpha"),
            lambda d: d["invocations"][0]["selection_members"][1].update(allocation_id=a.allocation_id),
            lambda d: d["invocations"].append(deepcopy(d["invocations"][0])),
            lambda d: d["invocations"][0]["selection_members"].pop(),
            lambda d: d["invocations"][0].update(extra="CANARY"),
        ):
            changed = deepcopy(wire)
            mutate(changed)
            self.refused(codec, changed)

    def test_zero_use_then_sibling_cleanup_preserves_full_accounting_without_second_deletion(self):
        original, a, b = self.two()
        identity = key(original["candidates"][0]["protecting_uses"][0])
        transfer = ((identity, a.artifact_id),)
        codec = self.codec()
        first = v2_wire(original, physical=(a.allocation_id,), transferred=transfer)
        later = v2_wire(original, physical=(b.allocation_id,), transferred=transfer)
        self.assertEqual(codec.encode(codec.decode(first)), first)
        self.assertEqual(first["invocations"], [])
        self.assertEqual(first["candidates"][0]["proposed_closures"], [])
        self.assertEqual(codec.encode(codec.decode(later)), later)
        self.assertEqual([row["ref"]["allocation_id"] for row in later["candidates"]], [b.allocation_id])
        self.assertEqual(first["accepted_transfers"], later["accepted_transfers"])
        # Pure syntax intentionally cannot assert transfer admission or retirement.
        # The same immutable proof key remains usable after a's separate retirement.
        self.assertNotIn("/etc/alpha", repr(later))
        changed = deepcopy(later)
        changed["accepted_transfers"][0]["source_identity"]["attempt"] += 1
        self.refused(codec, changed)
        for value in ("retired", "absent", "cleanup-closed"):
            changed = deepcopy(later)
            changed["accepted_transfers"][0] = {"kind": value}
            self.refused(codec, changed)

    def test_root_only_proofs_require_exact_ref_identity_and_closed_revision(self):
        original = proposal_wire()
        row = original["candidates"][0]
        transfer = ((key(row["protecting_uses"][0]), row["ref"]["artifact_id"]),)
        codec = self.codec()
        for revision in (0, 9007199254740991):
            wire = v2_wire(original, transferred=transfer, revision=revision)
            self.assertEqual(codec.encode(codec.decode(wire)), wire)
        wire = v2_wire(original, transferred=transfer)
        for revision in (-1, True, 0.0, "0", 9007199254740992, None):
            changed = deepcopy(wire)
            changed["accepted_transfers"][0]["acceptance_revision"] = revision
            self.refused(codec, changed)
        for field, value in (("allocation_id", "foreign"), ("artifact_id", "foreign"),
                             ("ref_fingerprint", "f" * 64)):
            changed = deepcopy(wire)
            changed["accepted_transfers"][0][field] = value
            self.refused(codec, changed)
        changed = deepcopy(wire)
        changed["accepted_transfers"][0]["source_identity"]["run_id"] = "foreign"
        self.refused(codec, changed)

    def test_eligible_inspection_is_deterministic_proposal_projection_and_digest(self):
        original, a, b = self.two()
        identity = key(original["candidates"][0]["protecting_uses"][0])
        wire = v2_wire(original, physical=(b.allocation_id,), transferred=((identity, a.artifact_id),))
        proposal = self.codec().decode(wire)
        expected = v2_inspection(wire)
        inspection_codec = self.codec(inspection=True)
        inspection = inspection_codec.decode(expected)
        self.assertTrue(hasattr(values, "configuration_cleanup_inspection_from_proposal"), "#1944 pure v2 projection is missing")
        self.assertEqual(values.configuration_cleanup_inspection_from_proposal(proposal), inspection)
        for invalid in (values.ConfigurationCleanupProposalCodec().decode(proposal_wire()),
                        object(), proposal.canonical, wire):
            with self.assertRaises(ValueError):
                values.configuration_cleanup_inspection_from_proposal(invalid)
        self.assertEqual(inspection.evidence_digest, sha256(
            b"control-plane-kit.configuration-cleanup-inspection.v2\x00" + rfc8785.dumps(expected)).hexdigest())
        self.assertEqual(values.ConfigurationCleanupInspectionResult("complete", inspection).inspection, inspection)
        self.assertEqual(expected["candidates"][0]["invocations"][0]["uncovered_outstanding_count"], 0)
        self.assertNotIn("/etc/alpha", repr(expected))
        for mutate in (lambda d: d["invocation_accounting"].clear(),
                       lambda d: d["accepted_transfers"].clear(),
                       lambda d: d["invocation_accounting"].append(deepcopy(d["invocation_accounting"][0])),
                       lambda d: d["candidates"][0]["invocations"][0].update(unselected_count=0)):
            changed = deepcopy(expected)
            mutate(changed)
            self.refused(inspection_codec, changed)

    def test_complete_inspection_may_be_blocked_and_uncovered_siblings_remain_count_only(self):
        original, a, b = self.two()
        identity = key(original["candidates"][0]["protecting_uses"][0])
        wire = v2_inspection(v2_wire(original, physical=(b.allocation_id,), transferred=((identity, a.artifact_id),)))
        codec = self.codec(inspection=True)
        blocked = deepcopy(wire)
        blocked["accepted_transfers"] = []
        blocked["invocation_accounting"] = []
        row = blocked["candidates"][0]
        row["invocations"][0]["uncovered_outstanding_count"] = 1
        row["blockers"] = ["incomplete-invocation-selection"]
        inspection = codec.decode(blocked)
        self.assertEqual(values.ConfigurationCleanupInspectionResult("complete", inspection).state, "complete")
        self.assertNotIn(a.allocation_id, repr(blocked))
        self.assertNotIn("/etc/alpha", repr(blocked))
        for bad_count in (-1, True, 1.0, 3):
            changed = deepcopy(blocked)
            changed["candidates"][0]["invocations"][0]["uncovered_outstanding_count"] = bad_count
            self.refused(codec, changed)
        current = deepcopy(wire)
        current["candidates"][0]["blockers"] = ["current-selected-use"]
        self.assertEqual(codec.encode(codec.decode(current)), current)
        active = v2_inspection(v2_wire(original))
        for candidate in active["candidates"]:
            summary = candidate["invocations"][0]
            summary["kind"] = "active"
            for field in ("direct_event_id", "direct_event_ordinal", "result_kind", "outcome_fingerprint", "attempt_status"):
                summary.pop(field)
            candidate["blockers"] = ["unresolved-invocation"]
        self.assertEqual(codec.encode(codec.decode(active)), active)

    def test_transfers_cannot_contradict_the_same_represented_completion(self):
        original, a, b = self.two()
        identity = key(original["candidates"][0]["protecting_uses"][0])
        proposal_codec, inspection_codec = self.codec(), self.codec(inspection=True)
        all_outstanding = v2_wire(original)
        all_outstanding["invocations"][0]["result_kind"] = "failed"
        self.assertEqual(proposal_codec.encode(proposal_codec.decode(all_outstanding)), all_outstanding)
        transferred = v2_wire(original, physical=(b.allocation_id,), transferred=((identity, a.artifact_id),))
        self.assertEqual(proposal_codec.encode(proposal_codec.decode(transferred)), transferred)
        changed = deepcopy(transferred)
        changed["invocations"][0]["result_kind"] = "failed"
        with self.subTest(proposal="failed-with-transfer"):
            self.refused(proposal_codec, changed)

        # The blocked form retains a root-only T entry for represented u, but
        # withholds full accounting because a third sibling is outstanding.
        c = replace(b, artifact_id="gamma", allocation_id="c-allocation", target_path="/etc/gamma")
        three = proposal_wire(refs=(a, b, c))
        root_only = v2_inspection(v2_wire(three, physical=(a.allocation_id, b.allocation_id),
            transferred=((identity, a.artifact_id), (identity, c.artifact_id))))
        root_only["accepted_transfers"] = [row for row in root_only["accepted_transfers"] if row["artifact_id"] == a.artifact_id]
        root_only["invocation_accounting"] = []
        root_only["candidates"][0]["invocations"][0]["uncovered_outstanding_count"] = 1
        root_only["candidates"][0]["blockers"] = ["incomplete-invocation-selection"]
        for label, inspection in (("complement", v2_inspection(transferred)), ("root-only", root_only)):
            self.assertEqual(inspection_codec.encode(inspection_codec.decode(inspection)), inspection)
            for kind, result in (("completed", "failed"), ("active", None),
                                 ("terminal-unprofiled", "succeeded"), ("terminal-unprofiled", "failed")):
                def observed(document):
                    for candidate in document["candidates"]:
                        for summary in candidate["invocations"]:
                            summary["kind"] = kind
                            if kind == "active":
                                for field in ("direct_event_id", "direct_event_ordinal", "result_kind", "outcome_fingerprint", "attempt_status"):
                                    summary.pop(field)
                            else:
                                summary.update(result_kind=result, attempt_status=result)
                            if kind != "completed":
                                candidate["blockers"] = sorted(set(candidate["blockers"]) | {"unresolved-invocation"})
                    return document
                positive = observed(v2_inspection(v2_wire(original)))
                self.assertEqual(inspection_codec.encode(inspection_codec.decode(positive)), positive)
                with self.subTest(inspection=label, kind=kind, result=result):
                    self.refused(inspection_codec, observed(deepcopy(inspection)))

    def test_closed_envelope_records_and_existing_approval_bind_exact_v2_digest(self):
        wire = v2_wire()
        proposal = self.codec().decode(wire)
        self.assertTrue(hasattr(plans.PlanDerivationProfile, "CONFIGURATION_CLEANUP_V2"), "#1944 v2 plan profile is missing")
        profile = plans.PlanDerivationProfile.CONFIGURATION_CLEANUP_V2
        plan = cleanup_plan(wire)
        encoded = plans.encode_stored_activity_plan(plan, profile=profile, cleanup_proposal=proposal)
        self.assertEqual(encoded["version"], 2)
        self.assertEqual(encoded["derivation_profile"], "configuration-cleanup-v2")
        self.assertEqual(plans.decode_stored_activity_plan_record(encoded).cleanup_proposal, proposal)
        for wrong_profile in (None, plans.PlanDerivationProfile.CONFIGURATION_CLEANUP_V1,
                              plans.PlanDerivationProfile.STRUCTURAL_V1):
            with self.subTest(profile=wrong_profile), self.assertRaises(ValueError):
                plans.StoredActivityPlan(plan, wrong_profile, proposal)
        digest = values.configuration_cleanup_proposal_fingerprint(proposal)
        subject = ActivityPlanApprovalSubject("plan", proposal_fingerprint=digest)
        self.assertEqual(approval_subject_from_descriptor(subject.descriptor()), subject)
        self.assertEqual(subject.descriptor()["profile"], "configuration-cleanup-approval.v1")
        self.assertNotEqual(subject.review_digest, ActivityPlanApprovalSubject(
            "plan", proposal_fingerprint=proposal_digest(proposal_wire())).review_digest)
        record = ActivityPlanRecord("plan", wire["context"]["session_id"], wire["context"]["base_graph_id"],
            wire["context"]["desired_graph_id"], ActivityPlanStatus.PLANNED, "2026-10-05T12:00:00Z", plan,
            base_realized_projection_id=wire["context"]["base_realized_projection_id"],
            desired_realized_projection_id=wire["context"]["desired_realized_projection_id"],
            desired_graph_revision=wire["context"]["desired_graph_revision"], derivation_profile=profile,
            cleanup_proposal=proposal)
        with self.assertRaises(ValueError):
            replace(record, derivation_profile=plans.PlanDerivationProfile.CONFIGURATION_CLEANUP_V1)
        with self.assertRaises(ValueError):
            replace(record, desired_graph_revision=record.desired_graph_revision + 1)
        with self.assertRaises(ValueError):
            plans.decode_stored_activity_plan(encoded)
        empty = validate_graph(DeploymentGraph("empty"))
        with self.assertRaises(ValueError):
            plans.derive_activity_plan(Deploy(empty, empty), profile=profile)
        for field, value in (("version", True), ("version", "2"), ("version", 3),
            ("derivation_profile", "configuration-cleanup-v1"), ("derivation_profile", "unknown"),
            ("cleanup_proposal_fingerprint", proposal_digest(proposal_wire())), ("extra", "CANARY")):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                plans.decode_stored_activity_plan_record({**encoded, field: value})
        for missing in encoded:
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                plans.decode_stored_activity_plan_record({key: value for key, value in encoded.items() if key != missing})
        with self.assertRaises(ValueError):
            plans.encode_stored_activity_plan(ActivityPlan(()), profile=profile, cleanup_proposal=proposal)

    def test_transferred_root_does_not_constrain_another_outstanding_invocation(self):
        original = proposal_wire(claims=2)
        row = original["candidates"][0]
        root = key(row["protecting_uses"][0]), row["ref"]["artifact_id"]
        wire = v2_wire(original, transferred=(root,))
        wire["invocations"][0]["result_kind"] = "failed"
        self.assertNotEqual(key(wire["invocations"][0]["source_identity"]), root[0])
        proposal = self.codec().decode(wire)
        self.assertEqual(self.codec().encode(proposal), wire)
        inspection = self.codec(inspection=True).decode(v2_inspection(wire))
        self.assertEqual(values.configuration_cleanup_inspection_from_proposal(proposal), inspection)

    def test_supported_64_use_values_fit_without_widening_any_v1_or_document_limit(self):
        codec = self.codec()
        for accepted in (False, True):
            for text in ('"' * 512, '\\' * 512, '\U0001f680' * 512):
                original = proposal_wire(accepted=accepted, claims=64, maximum=True, text=text)
                wire = v2_wire(original)
                value = codec.decode(wire)
                self.assertLessEqual(len(value.canonical), 512 * 1024)
                self.assertEqual(len(wire["invocations"]), 64)
                inspection = self.codec(inspection=True).decode(v2_inspection(wire))
                self.assertLessEqual(len(inspection.canonical), 512 * 1024)
                profile = plans.PlanDerivationProfile.CONFIGURATION_CLEANUP_V2
                envelope = plans.encode_stored_activity_plan(cleanup_plan(wire), profile=profile, cleanup_proposal=value)
                self.assertLessEqual(len(rfc8785.dumps(envelope)), 512 * 1024)
        self.refused(codec, v2_wire(proposal_wire(claims=65)))
        original = proposal_wire(claims=65)
        row = original["candidates"][0]
        transferred = ((key(row["protecting_uses"][0]), row["ref"]["artifact_id"]),)
        independent_root = v2_wire(original, transferred=transferred)
        self.assertEqual(len(independent_root["invocations"]), 64)
        self.assertLessEqual(len(codec.decode(independent_root).canonical), 512 * 1024)

    def test_total_outstanding_pair_limit_and_closed_versions_are_independent_of_dedup(self):
        ref = configuration_ref()
        refs = tuple(replace(ref, allocation_id=f"allocation-{i}", artifact_id=f"artifact-{i}",
            target_path=f"/etc/config-{i}") for i in range(5))
        codec = self.codec()
        at_limit = v2_wire(proposal_wire(refs=refs[:4], claims=64))
        self.assertEqual(codec.encode(codec.decode(at_limit)), at_limit)
        self.assertEqual(len(at_limit["invocations"]), 64)
        self.assertEqual(sum(len(row["protecting_uses"]) for row in at_limit["candidates"]), 256)
        self.refused(codec, v2_wire(proposal_wire(refs=refs, claims=64)))
        for profile in (None, True, "configuration-cleanup-proposal.v1", "configuration-cleanup-proposal.v3"):
            changed = v2_wire()
            changed["profile"] = profile
            self.refused(codec, changed)
        changed = v2_wire()
        changed["context"]["session_id"] = "CANARY" * (512 * 1024)
        self.refused(codec, changed)


if __name__ == "__main__":
    unittest.main()
