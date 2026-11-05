"""Named HTTP samples bind existing whole-check authority without performing I/O."""
from dataclasses import replace
import hashlib
import importlib
import importlib.util
import unittest

import rfc8785

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.receiver_identity import NodeControlAuthorityContext
from control_plane_kit_core.verification import (
    HttpCheck, HttpVerificationEvidence, VerificationOutcome, VerificationPolicy,
)
from tests.test_receiver_health_reads import ReceiverHealthFixtures


class HttpVerificationSampleTests(ReceiverHealthFixtures, unittest.TestCase):
    module_name = "http_verification_samples"

    def check(self):
        return HttpCheck("ready", "http-api", "/health/ready",
                         policy=VerificationPolicy(maximum_attempts=2,
                                                   maximum_evidence_bytes=1024))

    def sample(self, **changes):
        target = self.target()
        gateway = replace(target, node_id=replace(target.node_id, value="gateway"),
                          receiver_id="b" * 32)
        values = dict(target=target, authority_context=self.context(),
                      declaration_identity=self.declaration().identity().value,
                      gateway_target=gateway, route_id="child-control", run_id="run-1",
                      activity_id="activity-1", outer_attempt=1, effect_id="start-1",
                      request_fingerprint="c" * 64, check_id="ready",
                      check_digest=self.api.http_verification_check_digest(self.check()),
                      sample_ordinal=1, request_id="sample-1")
        return self.api.HttpVerificationSampleRequest(**(values | changes))

    def grant(self, request=None, **changes):
        values = dict(request=self.sample() if request is None else request,
                      purpose=DelegationKeyPurpose.WORKLOAD_HTTP_VERIFICATION_SAMPLE,
                      issuer="controller", key_id="sample-key", audience="workload:router:control",
                      issued_at=100, not_before=100, expires_at=120, jti="grant-1")
        return self.api.HttpVerificationSampleGrant(**(values | changes))

    def verify(self, grant, request=None, **changes):
        values = dict(expected_purpose=DelegationKeyPurpose.WORKLOAD_HTTP_VERIFICATION_SAMPLE,
                      expected_issuer="controller", expected_key_id="sample-key",
                      expected_audience="workload:router:control", now=110)
        return self.api.verify_http_verification_sample_grant(
            grant, self.sample() if request is None else request, **(values | changes))

    def refusal(self, action):
        with self.assertRaises(self.api.HttpVerificationSampleError) as caught:
            action()
        self.assertEqual(str(caught.exception), "HTTP verification sample is invalid")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_original_complete_check_and_request_have_canonical_vectors(self):
        check, request = self.check(), self.sample()
        expected_digest = hashlib.sha256(rfc8785.dumps(check.descriptor())).hexdigest()
        self.assertEqual(request.check_digest, expected_digest)
        codec = self.api.HttpVerificationSampleRequestCodec()
        document = request.descriptor()
        self.assertEqual(document["profile"], "http-verification-sample-request.v1")
        self.assertEqual(document["operation"], "http-verification-sample")
        self.assertEqual(document["sample_ordinal"], 1)
        self.assertNotIn("path", document)
        self.assertNotIn("policy", document)
        self.assertEqual(request.canonical_bytes(), rfc8785.dumps(document))
        self.assertEqual(request.canonical_digest(), hashlib.sha256(rfc8785.dumps(document)).hexdigest())
        self.assertEqual(codec.decode(document), request)
        self.assertEqual(codec.decode_canonical_bytes(request.canonical_bytes()), request)
        changes = (replace(check, path="/other"), replace(check, expected_statuses=(201,)),
                   replace(check, expected_body_sha256="e" * 64))
        changes += tuple(replace(check, policy=replace(check.policy, **{name: value})) for name, value in (
            ("timeout_seconds", 6), ("interval_seconds", 2),
            ("maximum_attempts", 3), ("maximum_evidence_bytes", 1025)))
        for changed in changes:
            self.assertNotEqual(self.api.http_verification_check_digest(changed), expected_digest)
            self.refusal(lambda: self.api.require_http_verification_sample_check(
                request, expected_target=request.target,
                expected_declaration_identity=request.declaration_identity, installed_check=changed))

    def test_installed_check_and_ordinal_are_independent_of_request(self):
        request = self.sample(sample_ordinal=2)
        self.assertEqual(self.api.require_http_verification_sample_check(
            request, expected_target=request.target,
            expected_declaration_identity=request.declaration_identity, installed_check=self.check()), self.check())
        for changed in (replace(request, sample_ordinal=3),
                        replace(request, declaration_identity="f" * 64),
                        replace(request, target=replace(request.target, receiver_id="f" * 32))):
            self.refusal(lambda: self.api.require_http_verification_sample_check(
                changed, expected_target=request.target,
                expected_declaration_identity=request.declaration_identity, installed_check=self.check()))

    def test_grant_binds_purpose_route_attempt_ordinal_and_window(self):
        request, grant = self.sample(), self.grant()
        codec = self.api.HttpVerificationSampleGrantCodec()
        self.assertEqual(codec.decode(grant.descriptor()), grant)
        self.assertEqual(codec.decode_canonical_bytes(grant.canonical_bytes()), grant)
        self.assertTrue(self.verify(grant))
        for change in ({"route_id": "other"}, {"effect_id": "other"}, {"run_id": "other"},
                       {"activity_id": "other"}, {"outer_attempt": 2}, {"sample_ordinal": 2},
                       {"request_id": "sample-2"}, {"request_fingerprint": "f" * 64},
                       {"check_digest": "f" * 64}, {"check_id": "other"},
                       {"authority_context": NodeControlAuthorityContext("other", "projection-A")},
                       {"gateway_target": replace(request.gateway_target, receiver_id="f" * 32)},
                       {"target": replace(request.target, receiver_id="f" * 32)}):
            with self.subTest(change=tuple(change)):
                self.assertFalse(self.verify(grant, replace(request, **change)))
        for change in ({"expected_purpose": DelegationKeyPurpose.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT},
                       {"expected_issuer": "other"}, {"expected_key_id": "other"},
                       {"expected_audience": "other"}, {"now": 99}, {"now": 120}):
            self.assertFalse(self.verify(grant, **change))
        self.assertTrue(self.verify(grant, now=100))
        self.assertFalse(self.verify(ReceiverHealthFixtures.grant(self)))
        self.refusal(lambda: self.grant(expires_at=121))
        self.refusal(lambda: self.grant(purpose=DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ))
        transit = self.grant(purpose=DelegationKeyPurpose.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT)
        self.assertTrue(self.verify(transit,
                                   expected_purpose=DelegationKeyPurpose.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT))

    def test_service_results_preserve_500_and_capped_malformed(self):
        request, check = self.sample(), self.check()
        codec = self.api.HttpVerificationSampleResultCodec(request, check)
        for outcome, status in ((VerificationOutcome.PASSED, 200),
                                (VerificationOutcome.FAILED, 500),
                                (VerificationOutcome.MALFORMED, 200)):
            with self.subTest(outcome=outcome):
                result = self.api.HttpVerificationSampleResult(
                    request, outcome, HttpVerificationEvidence(status, 1024))
                document = result.descriptor()
                self.assertEqual(document["kind"], "service-sample")
                self.assertEqual(document["evidence"]["status_code"], status)
                self.assertEqual(codec.decode(document), result)
                self.assertEqual(codec.decode_canonical_bytes(result.canonical_bytes()), result)
                self.assertEqual(codec.decode(document).outcome, outcome)
        for outcome, evidence in ((VerificationOutcome.PASSED, HttpVerificationEvidence(500, 21)),
                                  (VerificationOutcome.PASSED, HttpVerificationEvidence(200, 1025)),
                                  (VerificationOutcome.FAILED, HttpVerificationEvidence(200, 21))):
            self.refusal(lambda: codec.encode(self.api.HttpVerificationSampleResult(request, outcome, evidence)))
        digest_check = replace(check, expected_body_sha256="d" * 64)
        digest_request = replace(request, check_digest=self.api.http_verification_check_digest(digest_check))
        digest_codec = self.api.HttpVerificationSampleResultCodec(digest_request, digest_check)
        capped = self.api.HttpVerificationSampleResult(digest_request, VerificationOutcome.MALFORMED,
            HttpVerificationEvidence(200, 1024, "d" * 64, True))
        self.assertIs(digest_codec.decode(capped.descriptor()).outcome, VerificationOutcome.MALFORMED)

    def test_unavailable_never_has_service_evidence_and_old_reply_cannot_satisfy_new(self):
        request = self.sample()
        codec = self.api.HttpVerificationSampleResultCodec(request, self.check())
        result = self.api.HttpVerificationSampleResult(request, VerificationOutcome.TIMED_OUT)
        self.assertEqual(result.descriptor()["kind"], "sample-unavailable")
        self.assertIsNone(result.descriptor()["evidence"])
        self.assertEqual(codec.decode(result.descriptor()), result)
        self.refusal(lambda: self.api.HttpVerificationSampleResult(request, VerificationOutcome.PASSED))
        next_codec = self.api.HttpVerificationSampleResultCodec(
            replace(request, request_id="sample-2", sample_ordinal=2), self.check())
        self.refusal(lambda: next_codec.decode(result.descriptor()))
        self.refusal(lambda: next_codec.encode(result))
        self.refusal(lambda: codec.decode(result.descriptor() | {"kind": "service-sample"}))
        self.refusal(lambda: codec.decode(result.descriptor() | {"status_code": 500}))

    def test_closed_wire_and_categorical_refusals(self):
        request = self.sample()
        codec = self.api.HttpVerificationSampleRequestCodec()
        for change in ({"url": "http://private-test-value"}, {"path": "/other"},
                       {"maximum_attempts": 10}, {"profile": "workload-node-health-read-request.v2"},
                       {"operation": "arbitrary-get"}, {"sample_ordinal": True},
                       {"outer_attempt": 0}):
            self.refusal(lambda: codec.decode(request.descriptor() | change))
        raw = request.canonical_bytes()
        self.refusal(lambda: codec.decode_canonical_bytes(b" " + raw))
        self.refusal(lambda: codec.decode_canonical_bytes(raw[:-1] + b',"request_id":"duplicate"}'))
        self.refusal(lambda: codec.decode_canonical_bytes(b"x" * 4097))
        self.assertNotIn("run-1", repr(request))
        self.assertNotIn("sample-key", repr(self.grant()))

