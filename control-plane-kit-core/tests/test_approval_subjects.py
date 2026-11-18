from __future__ import annotations

import unittest
from copy import deepcopy
from dataclasses import fields
from hashlib import sha256

import rfc8785

from control_plane_kit_core.approval_subjects import (
    ActivityPlanApprovalSubject,
    GatewayKeyRotationApprovalSubject,
    approval_subject_from_descriptor,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.policies import ApprovalPolicy, PolicyScope


class ApprovalSubjectTests(unittest.TestCase):
    def cleanup_subject(self, digest="a" * 64):
        member = next((item for item in fields(ActivityPlanApprovalSubject)
                       if item.name == "proposal_fingerprint"), None)
        self.assertIsNotNone(member, "#1928 exact cleanup approval subject is missing")
        self.assertTrue(member.kw_only)
        return ActivityPlanApprovalSubject("plan-a", proposal_fingerprint=digest)

    def test_cleanup_subject_binds_exact_proposal_without_changing_legacy_digest(self):
        subject = self.cleanup_subject()
        descriptor = {"kind": "activity-plan", "profile": "configuration-cleanup-approval.v1",
                      "plan_id": "plan-a", "proposal_fingerprint": "a" * 64}
        self.assertEqual(subject.descriptor(), descriptor)
        self.assertEqual(approval_subject_from_descriptor(descriptor), subject)
        self.assertEqual(subject.review_digest, sha256(
            b"control-plane-kit.configuration-cleanup-approval.v1\x00" + rfc8785.dumps(descriptor)).hexdigest())
        self.assertNotEqual(subject.review_digest, self.cleanup_subject("b" * 64).review_digest)
        self.assertEqual(subject.subject_id, "plan-a")
        legacy = ActivityPlanApprovalSubject("plan-a")
        self.assertEqual(legacy.descriptor(), {"kind": "activity-plan", "plan_id": "plan-a"})
        self.assertEqual(legacy.review_digest, sha256(b"activity-plan:plan-a").hexdigest())
        self.assertNotEqual(subject.review_digest, legacy.review_digest)

    def test_cleanup_subject_rejects_partial_unknown_and_mutated_profiles(self):
        original = self.cleanup_subject().descriptor()
        cases = [{**original, "profile": "UNKNOWN-CANARY"}, {**original, "extra": "EXTRA-CANARY"},
                 {**original, "proposal_fingerprint": True}, {**original, "proposal_fingerprint": "A" * 64},
                 {**original, "proposal_fingerprint": None}]
        for key in original:
            candidate = deepcopy(original)
            del candidate[key]
            cases.append(candidate)
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises((TypeError, ValueError)):
                approval_subject_from_descriptor(candidate)
        for invalid in (True, "x", "A" * 64, [], {}):
            with self.subTest(invalid=invalid):
                subject = self.cleanup_subject()
                object.__setattr__(subject, "proposal_fingerprint", invalid)
                with self.assertRaises((TypeError, ValueError)):
                    subject.descriptor()
                with self.assertRaises((TypeError, ValueError)):
                    _ = subject.review_digest

    def test_activity_plan_subject_has_stable_identity_digest_and_round_trip(self) -> None:
        subject = ActivityPlanApprovalSubject("plan-a")

        self.assertEqual(subject.subject_id, "plan-a")
        self.assertEqual(len(subject.review_digest), 64)
        self.assertEqual(
            approval_subject_from_descriptor(subject.descriptor()),
            subject,
        )

    def test_rotation_subject_is_closed_review_detail_without_secret_material(self) -> None:
        subject = _rotation_subject()
        descriptor = subject.descriptor()

        self.assertEqual(descriptor["overlap_verifier_roles"], ["old", "new"])
        self.assertEqual(descriptor["retirement_verifier_roles"], ["new"])
        self.assertEqual(
            approval_subject_from_descriptor(descriptor),
            subject,
        )
        leak_surface = repr(descriptor).lower()
        for forbidden in ("secret://", "private", "public_key_pem", "version_id"):
            self.assertNotIn(forbidden, leak_surface)

    def test_rotation_review_digest_changes_with_reviewed_policy(self) -> None:
        original = _rotation_subject()
        changed = GatewayKeyRotationApprovalSubject(
            rotation_id=original.rotation_id,
            workspace_id=original.workspace_id,
            gateway_node_id=original.gateway_node_id,
            purpose=original.purpose,
            issuer=original.issuer,
            old_key_id=original.old_key_id,
            maximum_grant_lifetime_seconds=121,
            clock_skew_seconds=original.clock_skew_seconds,
            rotation_intent_digest=original.rotation_intent_digest,
        )

        self.assertNotEqual(original.review_digest, changed.review_digest)

    def test_rotation_approval_has_distinct_request_and_decision_authority(self) -> None:
        self.assertNotEqual(
            PolicyScope.DELEGATION_KEY_ROTATE,
            PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,
        )
        policy = ApprovalPolicy()
        self.assertTrue(
            policy.can_request_gateway_key_rotation(
                (PolicyScope.DELEGATION_KEY_ROTATE,)
            ).allowed
        )
        self.assertFalse(
            policy.can_approve_gateway_key_rotation(
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                requested_by="operator-a",
                decided_by="manager-a",
            ).allowed
        )
        self.assertTrue(
            policy.can_approve_gateway_key_rotation(
                (PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,),
                requested_by="operator-a",
                decided_by="manager-a",
            ).allowed
        )


def _rotation_subject() -> GatewayKeyRotationApprovalSubject:
    return GatewayKeyRotationApprovalSubject(
        rotation_id="gkrot_a",
        workspace_id="workspace-a",
        gateway_node_id="gateway-a",
        purpose=DelegationKeyPurpose.GATEWAY_PROBE,
        issuer="cpk-server",
        old_key_id="key-a",
        maximum_grant_lifetime_seconds=120,
        clock_skew_seconds=10,
        rotation_intent_digest="a" * 64,
    )


if __name__ == "__main__":
    unittest.main()
