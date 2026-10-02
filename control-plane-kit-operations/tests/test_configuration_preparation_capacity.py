"""#1923 production capacity algebra; arithmetic fit grants no authority."""
import importlib
import unittest

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_operations.records import OperationsRecordError


class ConfigurationPreparationCapacityTests(unittest.TestCase):
    def language(self):
        name = "control_plane_kit_operations.configuration_preparation"
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as error:
            if error.name != name:
                raise
            self.fail("missing #1923 production configuration capacity contract")
        for field in ("ConfigurationEvidenceFootprint", "ConfigurationCapacityDecision",
                      "configuration_evidence_capacity", "configuration_preparation_capacity"):
            self.assertTrue(hasattr(module, field), f"missing #1923 capacity capability: {field}")
        return module

    @staticmethod
    def key(index):
        return EffectAttemptIdentity(RunId("run-a"), f"activity-{index:03d}", 1), "settings"

    def test_footprint_counts_queries_nulls_sentinels_and_distinct_relational_identities(self):
        value = self.language().ConfigurationEvidenceFootprint
        cases = (
            ((0, 0, 0, 0), 0),       # cache hit: no SQL and no returned evidence
            ((0, 0, 0, 1), 256),     # zero-row/refusal SQL is not free
            ((0, 0, 1, 0), 16),      # null/invalid scalar marker
            ((1, 7, 2, 1), 423),     # one returned sentinel, its cells and SQL
            ((2, 17, 2, 1), 561),    # two relational identities in one joined row
        )
        for fields, expected in cases:
            with self.subTest(fields=fields):
                self.assertEqual(value(*fields).accounted_bytes, expected)
        fetch = value(1, 17, 2, 1)
        self.assertEqual(fetch.accounted_bytes, 433)
        repeated = fetch.plus(fetch)
        self.assertEqual((repeated.records, repeated.value_octets, repeated.scalar_markers,
                          repeated.statements, repeated.accounted_bytes), (2, 34, 4, 2, 866))
        self.assertEqual(fetch.plus(value(0, 0, 0, 0)), fetch)
        self.assertEqual(fetch.accounted_bytes, 433)

    def test_closed_evidence_results_cannot_advertise_partial_or_missing_complete_proof(self):
        module = self.language()
        for name in ("ConfigurationSourceEvidence", "ConfigurationAllocationEvidence"):
            self.assertTrue(hasattr(module, name), f"missing #1923 evidence contract: {name}")
        for state in ("unavailable", "capacity"):
            self.assertIsNone(module.ConfigurationSourceEvidence(state).source)
            result = module.ConfigurationAllocationEvidence(state)
            self.assertIsNone(result.birth)
            self.assertEqual(result.claims, ())
        for state in ("unknown", "complete"):
            with self.assertRaises(OperationsRecordError):
                module.ConfigurationSourceEvidence(state)
            with self.assertRaises(OperationsRecordError):
                module.ConfigurationAllocationEvidence(state)
        for state in ("unavailable", "capacity"):
            with self.assertRaises(OperationsRecordError):
                module.ConfigurationSourceEvidence(state, source=object())
            with self.assertRaises(OperationsRecordError):
                module.ConfigurationAllocationEvidence(state, birth=object(), claims=())
            with self.assertRaises(OperationsRecordError):
                module.ConfigurationAllocationEvidence(state, claims=(object(),))

    def test_exact_record_and_accounted_byte_edges_and_precedence(self):
        module = self.language()
        value, decision = module.ConfigurationEvidenceFootprint, module.ConfigurationCapacityDecision
        cases = (
            (value(4096, 0, 0, 0), decision.WITHIN_LIMITS),
            (value(4097, 0, 0, 0), decision.RECORD_LIMIT),
            # Nonzero overhead is 560; literal octet values straddle 16MiB.
            (value(2, 16_776_655, 3, 1), decision.WITHIN_LIMITS),
            (value(2, 16_776_656, 3, 1), decision.WITHIN_LIMITS),
            (value(2, 16_776_657, 3, 1), decision.BYTE_LIMIT),
            (value(4097, 16_777_217, 0, 0), decision.RECORD_LIMIT),
        )
        self.assertEqual(value(2, 16_776_656, 3, 1).accounted_bytes, 16_777_216)
        for footprint, expected in cases:
            with self.subTest(footprint=footprint):
                self.assertIs(module.configuration_evidence_capacity(footprint), expected)

    def test_footprint_rejects_negative_coerced_and_boolean_fields(self):
        module = self.language()
        for position in range(4):
            for invalid in (-1, True, 1.0, "1"):
                with self.subTest(position=position, invalid=invalid):
                    fields = [0, 0, 0, 0]
                    fields[position] = invalid
                    with self.assertRaises(OperationsRecordError):
                        module.ConfigurationEvidenceFootprint(*fields)

    def test_unique_claim_delta_at_63_64_and_total_256_is_capacity_only(self):
        module = self.language()
        value, decision = module.ConfigurationEvidenceFootprint, module.ConfigurationCapacityDecision
        zero = value(0, 0, 0, 0)
        keys = tuple(self.key(index) for index in range(64))
        cases = (
            (keys[:63], self.key(64), 63, decision.WITHIN_LIMITS),
            (keys, self.key(64), 64, decision.PER_REF_CLAIM_LIMIT),
            (keys, keys[0], 64, decision.WITHIN_LIMITS),
            (keys[:63], self.key(64), 255, decision.WITHIN_LIMITS),
            (keys[:63], self.key(64), 256, decision.TOTAL_CLAIM_LIMIT),
            (keys, self.key(64), 256, decision.PER_REF_CLAIM_LIMIT),
            (keys[:63], keys[0], 257, decision.TOTAL_CLAIM_LIMIT),
        )
        for existing, proposed, total, expected in cases:
            with self.subTest(local=len(existing), total=total, proposed=proposed):
                self.assertIs(module.configuration_preparation_capacity(current=zero, reserved_future=zero,
                    existing_claim_keys=existing, proposed_claim_key=proposed, existing_total_claims=total), expected)

    def test_owner_reserved_future_counts_even_when_current_alone_fits(self):
        module = self.language()
        value, decision = module.ConfigurationEvidenceFootprint, module.ConfigurationCapacityDecision
        future = value(1, 0, 0, 1)  # 384 accounted bytes
        for octets, expected in ((16_776_704, decision.WITHIN_LIMITS), (16_776_705, decision.BYTE_LIMIT)):
            current = value(1, octets, 0, 0)
            self.assertIs(module.configuration_evidence_capacity(current), decision.WITHIN_LIMITS)
            self.assertIs(module.configuration_preparation_capacity(current=current, reserved_future=future,
                existing_claim_keys=(), proposed_claim_key=self.key(0), existing_total_claims=0), expected)
        self.assertEqual(value(1, 16_776_704, 0, 0).plus(future).accounted_bytes, 16_777_216)

    def test_preflight_validates_shapes_before_reporting_claim_or_byte_capacity(self):
        module = self.language()
        value = module.ConfigurationEvidenceFootprint
        arguments = dict(current=value(4097, 16_777_217, 0, 0), reserved_future=value(0, 0, 0, 0),
            existing_claim_keys=(self.key(0),), proposed_claim_key=self.key(1), existing_total_claims=1)
        for changes in (
                {"existing_claim_keys": (self.key(0), self.key(0)), "existing_total_claims": 2},
                {"existing_total_claims": 0}, {"existing_total_claims": True},
                {"existing_total_claims": -1}, {"existing_claim_keys": [self.key(0)]},
                {"proposed_claim_key": (self.key(0)[0], "INVALID-ARTIFACT")},
                {"proposed_claim_key": ("not-an-identity", "settings")}):
            with self.subTest(changes=changes):
                with self.assertRaises(OperationsRecordError):
                    module.configuration_preparation_capacity(**(arguments | changes))
        keys = tuple(self.key(index) for index in range(64))
        self.assertIs(module.configuration_preparation_capacity(**(arguments | {
            "existing_claim_keys": keys, "proposed_claim_key": self.key(64), "existing_total_claims": 256})),
            module.ConfigurationCapacityDecision.PER_REF_CLAIM_LIMIT)
