"""O2 R1/P1-P4/N4: exact closed profiles, without live authority or a clock."""

from dataclasses import FrozenInstanceError, fields, replace
import json
import unittest

import rfc8785

from control_plane_kit_core.receiver_identity import NodeControlAuthorityContext
from control_plane_kit_core.receiver_health_reads import ReceiverHealthReadRequestDigest
from tests.health_effect_preparation_fixture import forged_copy
from tests.receiver_health_preparation_fixture import ReceiverHealthPreparationFixture


class ReceiverHealthPreparationTests(ReceiverHealthPreparationFixture, unittest.TestCase):
    def test_receiver_preparation_profile_is_closed_and_round_trips_exact_v2(self):
        values = self.receiver_material()
        # R1: missing Operations API is asserted in this body after valid Core
        # V2 inputs, never at import time or in setUp.
        record = self.receiver_record(values)
        codec = self.api().HealthEffectPreparationCodec()
        encoded = codec.encode_canonical_bytes(record)
        document = json.loads(encoded)
        self.assertEqual(document["profile"], "health-effect-preparation.v2")
        self.assertEqual(set(document), {field.name for field in fields(record)} | {"profile"})
        self.assertEqual(encoded, rfc8785.dumps(document))
        self.assertLessEqual(len(encoded), 16_384)
        self.assertEqual(codec.decode_canonical_bytes(encoded), record)
        self.assertIs(type(codec.decode_canonical_bytes(encoded)), type(record))
        self.assertEqual(record.request.authority_context, values["request"].authority_context)
        self.assertEqual(record.transit_grant.gateway_target, values["transit_grant"].gateway_target)
        self.assertEqual(record.request_digest, record.request.canonical_digest())
        self.assertIs(type(record.request_digest), ReceiverHealthReadRequestDigest)
        with self.assertRaises(FrozenInstanceError):
            record.original_event_id = "replacement"
        for value in (record.request.request_id, record.transit_grant.jti,
                record.workload_grant.jti, record.original_event_id,
                record.request_fingerprint, record.transit_authorization_id):
            self.assertNotIn(value, repr(record))

    def test_v1_canonical_bytes_keep_the_original_profile_and_nominal_values(self):
        # Independent V1 guard can execute even while R1 is red. Existing V1
        # tests are unchanged; this checks the dispatcher retains its old arm.
        values = self.material()
        api = self.api()
        record = api.HealthEffectPreparationRecord(**values)
        codec = api.HealthEffectPreparationCodec()
        expected = {"profile": "health-effect-preparation.v1",
            **{key: value for key, value in values.items()
               if key not in ("identity", "request", "transit_grant", "workload_grant")},
            "identity": record.identity.descriptor(),
            "request": record.request.descriptor(),
            "transit_grant": record.transit_grant.descriptor(),
            "workload_grant": record.workload_grant.descriptor()}
        encoded = codec.encode_canonical_bytes(record)
        self.assertEqual(encoded, rfc8785.dumps(expected))
        self.assertIs(type(codec.decode_canonical_bytes(encoded)), api.HealthEffectPreparationRecord)
        self.assertEqual(codec.decode_canonical_bytes(encoded), record)

    def test_mixed_unknown_and_renamed_old_fields_have_no_profile_fallback(self):
        record = self.receiver_record()
        api = self.api()
        codec = api.HealthEffectPreparationCodec()
        document = json.loads(codec.encode_canonical_bytes(record))
        legacy = json.loads(codec.encode_canonical_bytes(self.preparation()))
        candidates = []
        for profile in ("health-effect-preparation.v1", "health-effect-preparation.v3", None):
            candidates.append({**document, "profile": profile})
        for field in ("request", "transit_grant", "workload_grant"):
            candidates.append({**document, field: legacy[field]})
            candidates.append({**legacy, field: document[field]})
        old_target = json.loads(json.dumps(document))
        old_target["request"]["target"]["graph_revision"] = "health-desired"
        candidates.append(old_target)
        for candidate in candidates:
            with self.subTest(profile=candidate["profile"]):
                with self.assertRaises(api.HealthEffectPreparationError) as caught:
                    codec.decode_canonical_bytes(rfc8785.dumps(candidate))
                self.assert_safe(caught.exception, "health-desired")

    def test_exact_request_context_interval_and_attempt_bind_both_grants(self):
        base = self.receiver_record()
        api = self.api()
        context = NodeControlAuthorityContext("another-graph", "another-projection")
        for family in ("transit_grant", "workload_grant"):
            grant = getattr(base, family)
            for changed in (replace(grant, authority_context=context),
                    replace(grant, request_id="foreign-request"),
                    replace(grant, issued_at=grant.issued_at - 1),
                    replace(grant, not_before=grant.not_before + 1),
                    replace(grant, expires_at=grant.expires_at + 1)):
                with self.subTest(family=family, changed=changed):
                    with self.assertRaises(api.HealthEffectPreparationError):
                        replace(base, **{family: changed})
        with self.assertRaises(api.HealthEffectPreparationError):
            replace(base, transit_grant=replace(base.transit_grant, attempt_id="foreign-attempt"))
        for field in ("workload_authorization_id", "workload_key_registration_id"):
            with self.assertRaises(api.HealthEffectPreparationError):
                replace(base, **{field: getattr(base, field.replace("workload", "transit"))})

    def test_v2_canonical_parser_and_nominal_revalidation_preserve_the_bound(self):
        base = self.receiver_record()
        api = self.api()
        codec = api.HealthEffectPreparationCodec()
        encoded = codec.encode_canonical_bytes(base)
        for raw in (b"\xff", b"\xef\xbb\xbf" + encoded, b" " + encoded, encoded + b"\n",
                encoded[:-1] + b',"profile":"health-effect-preparation.v2"}',
                b'{"unexpected":NaN}', b"[" * 1500 + b"]" * 1500, b"x" * 16_385):
            with self.subTest(size=len(raw)):
                with self.assertRaises(api.HealthEffectPreparationError):
                    codec.decode_canonical_bytes(raw)
        for candidate in (forged_copy(base, subclass=True),
                forged_copy(base, request=forged_copy(base.request, subclass=True)),
                forged_copy(base, transit_grant=forged_copy(base.transit_grant, expires_at=False)),
                forged_copy(base, workload_grant=forged_copy(base.workload_grant, request_digest=None))):
            with self.assertRaises(api.HealthEffectPreparationError):
                codec.encode_canonical_bytes(candidate)
        # Expired grants are immutable history. No current clock is consulted.
        large = replace(base, original_event_id="😀" * 512,
            base_realized_projection_id="😀" * 512)
        raw = codec.encode_canonical_bytes(large)
        self.assertLessEqual(len(raw), 16_384)
        self.assertEqual(codec.decode_canonical_bytes(raw), large)
