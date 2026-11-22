"""#1851 closed immutable preparation laws, independent of current authority."""
from dataclasses import FrozenInstanceError, fields, replace
import json
import unittest

import rfc8785

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.node_health_reads import NodeHealthReadRequestDigest
from tests.health_effect_preparation_fixture import HealthEffectPreparationFixture, wire_identity


class HealthEffectPreparationTests(HealthEffectPreparationFixture, unittest.TestCase):
    def test_exact_frozen_record_keeps_unsigned_material_out_of_repr(self):
        values = self.material()
        api = self.api()
        record = api.HealthEffectPreparationRecord(**values)
        self.assertEqual({item.name for item in fields(record)}, set(values))
        self.assertEqual(record.request, values["request"])
        for value in (record.request.request_id, record.transit_grant.jti,
                record.workload_grant.jti, record.original_event_id,
                record.request_fingerprint, record.transit_authorization_id):
            self.assertNotIn(value, repr(record))
        with self.assertRaises(FrozenInstanceError):
            record.original_event_id = "changed"
        codec = api.HealthEffectPreparationCodec()
        self.assertEqual(codec.decode_canonical_bytes(codec.encode_canonical_bytes(record)), record)

    def test_wire_identity_covers_entire_structured_attempt_with_legal_maximal_owner_ids(self):
        self.material()
        api = self.api()
        identities = (
            EffectAttemptIdentity(RunId("run-a"), "activity-a", 1),
            EffectAttemptIdentity(RunId("run-b"), "activity-a", 1),
            EffectAttemptIdentity(RunId("run-a"), "activity-b", 1),
            EffectAttemptIdentity(RunId("run-a"), "activity-a", 2),
            EffectAttemptIdentity(RunId("r:" + "x" * 198), "a:" + "y" * 198, 1),
        )
        results = [api.health_effect_attempt_wire_id(value) for value in identities]
        self.assertEqual(results, [wire_identity(value) for value in identities])
        self.assertEqual(len(set(results)), len(identities))
        for result in results:
            self.assertRegex(result, r"\Ahealth_[0-9a-f]{64}\Z")
            self.assertLessEqual(len(result), 128)

    def test_pair_request_interval_and_attempt_substitutions_are_independent(self):
        values = self.material()
        api = self.api()
        base = api.HealthEffectPreparationRecord(**values)
        variants = (
            {"transit_grant": replace(base.transit_grant, attempt_id="wrong-attempt")},
            {"transit_grant": replace(base.transit_grant, request_id="other-request")},
            {"workload_grant": replace(base.workload_grant, request_id="other-request")},
            {"transit_grant": replace(base.transit_grant, request_digest=NodeHealthReadRequestDigest("b" * 64))},
            {"workload_grant": replace(base.workload_grant, request_digest=NodeHealthReadRequestDigest("b" * 64))},
            {"workload_grant": replace(base.workload_grant, expires_at=base.workload_grant.expires_at + 1)},
            {"workload_grant": replace(base.workload_grant, audience="other-workload")},
            {"workload_authorization_id": base.transit_authorization_id},
            {"workload_key_registration_id": base.transit_key_registration_id},
        )
        for change in variants:
            with self.subTest(fields=tuple(change)):
                with self.assertRaises(api.HealthEffectPreparationError) as caught:
                    replace(base, **change)
                self.assert_safe(caught.exception, "other-request", "wrong-attempt", "other-workload")

    def test_nominal_forgery_and_missing_fields_are_revalidated_at_codec_boundary(self):
        base = self.preparation()
        api = self.api()
        codec = api.HealthEffectPreparationCodec()
        class Foreign:
            pass
        forged = object.__new__(api.HealthEffectPreparationRecord)
        for item in fields(base):
            object.__setattr__(forged, item.name, getattr(base, item.name))
        object.__setattr__(forged, "request_fingerprint", "bad-fingerprint-canary")
        missing = object.__new__(api.HealthEffectPreparationRecord)
        for candidate in (Foreign(), forged, missing, None):
            with self.subTest(type=type(candidate).__name__):
                with self.assertRaises(api.HealthEffectPreparationError) as caught:
                    codec.encode_canonical_bytes(candidate)
                self.assert_safe(caught.exception, "bad-fingerprint-canary")

    def test_closed_canonical_bytes_reject_parser_and_independent_payload_drift(self):
        base = self.preparation()
        api = self.api()
        codec = api.HealthEffectPreparationCodec()
        valid = codec.encode_canonical_bytes(base)
        document = json.loads(valid)
        self.assertEqual(document["profile"], "health-effect-preparation.v1")
        self.assertEqual(set(document), {item.name for item in fields(base)} | {"profile"})
        self.assertEqual(valid, rfc8785.dumps(document))
        corrupt = [b"\xff", b"\xef\xbb\xbf" + valid, b" " + valid, valid + b"\n", b"[]",
            valid[:-1] + b',"profile":"health-effect-preparation.v1"}',
            b'{"unexpected":NaN}', b"[" * 1500 + b"]" * 1500,
            b"x" * 16_385, {"not": "bytes"}]
        for key in ("request", "transit_grant", "workload_grant"):
            changed = json.loads(valid)
            changed[key]["request_id"] = "foreign-request-canary"
            corrupt.append(rfc8785.dumps(changed))
        changed = {**document, "extra": "payload-canary"}
        corrupt.append(rfc8785.dumps(changed))
        for candidate in corrupt:
            with self.subTest(kind=type(candidate).__name__, size=len(candidate)):
                with self.assertRaises(api.HealthEffectPreparationError) as caught:
                    codec.decode_canonical_bytes(candidate)
                self.assert_safe(caught.exception, "foreign-request-canary", "payload-canary")

    def test_lawful_large_owner_references_and_expired_grants_round_trip_together(self):
        base = self.preparation()
        api = self.api()
        # 512 Unicode characters are legal owner text; wire correlation does not
        # attempt to reuse those references as a health protocol identifier.
        large = replace(base, original_event_id="😀" * 512,
            base_realized_projection_id="😀" * 512,
            desired_realized_projection_id="🚀" * 512)
        codec = api.HealthEffectPreparationCodec()
        encoded = codec.encode_canonical_bytes(large)
        self.assertLessEqual(len(encoded), 16_384)
        self.assertGreater(len(encoded), 6144)
        self.assertEqual(codec.decode_canonical_bytes(encoded), large)
        # Fixed past timestamps are retained evidence; this path has no clock.
        self.assertEqual(large.workload_grant.expires_at, 1_700_000_060)
        for field in ("original_event_id", "base_realized_projection_id", "desired_realized_projection_id"):
            with self.subTest(field=field):
                with self.assertRaises(api.HealthEffectPreparationError):
                    replace(base, **{field: "x" * 513})

    def test_nested_nominal_and_forged_values_fail_at_codec_and_store_before_sql(self):
        from tests.health_effect_preparation_fixture import STORE_MODULE, forged_copy
        base = self.preparation()
        api = self.api()
        codec = api.HealthEffectPreparationCodec()
        class NoSql:
            def execute(self, *args):
                raise AssertionError("invalid nested material reached SQL")
        store = self.api(STORE_MODULE).HealthEffectPreparationStore(NoSql())
        candidates = [forged_copy(base, subclass=True)]
        for field in ("identity", "request", "transit_grant", "workload_grant"):
            candidates.append(forged_copy(base, **{field: forged_copy(getattr(base, field), subclass=True)}))
        candidates.extend((
            forged_copy(base, identity=forged_copy(base.identity, attempt=True)),
            forged_copy(base, request=forged_copy(base.request, request_id="nested-canary\n")),
            forged_copy(base, transit_grant=forged_copy(base.transit_grant, expires_at=False)),
            forged_copy(base, workload_grant=forged_copy(base.workload_grant, request_digest=None)),
        ))
        for index, candidate in enumerate(candidates):
            with self.subTest(candidate=index):
                for action in (lambda: codec.encode_canonical_bytes(candidate), lambda: store.insert_absent(candidate)):
                    with self.assertRaises(api.HealthEffectPreparationError) as caught:
                        action()
                    self.assert_safe(caught.exception, "nested-canary")

    def test_individually_valid_issued_at_and_not_before_intervals_must_match_pair(self):
        base = self.preparation()
        api = self.api()
        for changed in (
            replace(base.workload_grant, issued_at=base.workload_grant.issued_at - 1),
            replace(base.workload_grant, not_before=base.workload_grant.not_before + 1),
        ):
            with self.assertRaises(api.HealthEffectPreparationError):
                replace(base, workload_grant=changed)
