"""Isolated admission laws; synthetic budgets never authorize publication.

Real source composition, cold-read fit and publication have separate owning
PostgreSQL targets. These preserve the original three gate/order/one-over laws
while discarding the retired fixed 8MiB and measured-proof-delta arithmetic.
"""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from control_plane_kit_operations.configuration_preparation import (
    ConfigurationEvidenceFootprint as Footprint, configuration_evidence_capacity,
)
from control_plane_kit_operations._configuration_preparation import _OrdinarySuffixBudget as Budget
from control_plane_kit_operations.postgres import configuration_acceptance_store as acceptance


class ConfigurationAcceptancePreflightTests(unittest.TestCase):
    snapshot = Footprint(5, 71, 7, 3)
    future = Budget(Footprint(7, 101, 11, 5), Footprint(9, 151, 13, 6))
    publication = Budget(Footprint(11, 181, 15, 7), Footprint(13, 211, 17, 9))
    prior = Footprint(13, 211, 17, 3)

    def exercise(self, *, snapshot=None, future=None, publication=None, prior=None, refuses=False):
        snapshot = self.snapshot if snapshot is None else snapshot
        future = self.future if future is None else future
        publication = self.publication if publication is None else publication
        prior = self.prior if prior is None else prior
        prepared = SimpleNamespace(evidence_read=SimpleNamespace(used=prior))
        store, decisions = acceptance.ConfigurationAcceptanceStore(None), []

        def decide(value):
            result = configuration_evidence_capacity(value)
            decisions.append((value, result.value))
            return result

        # Only isolate the already-derived values. Exercise the actual gates;
        # no UoW, source proof, owner preparation or write is fabricated.
        with mock.patch.object(store, "_publication_budgets", return_value=(snapshot, future, publication)), \
                mock.patch("control_plane_kit_operations.configuration_preparation.configuration_evidence_capacity", decide), \
                mock.patch.object(acceptance._EvidenceRead, "query", side_effect=AssertionError("admission issued SQL")):
            if refuses:
                with self.assertRaises(acceptance._Capacity):
                    store._preflight(prepared)
            else:
                store._preflight(prepared)
        self.assertEqual(prepared.evidence_read.used, prior)
        return decisions

    def test_snapshot_exact_three_mib_then_one_byte_over(self):
        exact = replace(self.snapshot, value_octets=self.snapshot.value_octets + 3145728 - self.snapshot.accounted_bytes)
        self.assertEqual(len(self.exercise(snapshot=exact)), 4)
        self.assertEqual(self.exercise(snapshot=replace(exact, value_octets=exact.value_octets + 1), refuses=True), [])

    def test_cold_consumer_record_and_byte_edges_use_actual_envelope(self):
        for field, limit, refusal in (("records", 4096, "record-limit"), ("value_octets", 16777216, "byte-limit")):
            with self.subTest(dimension=field):
                peak = self.future.peak
                extra = limit - (peak.records if field == "records" else peak.accounted_bytes)
                peak = replace(peak, **{field: getattr(peak, field) + extra})
                exact = Budget(self.future.settled, peak)
                accepted = self.exercise(future=exact)
                self.assertEqual(accepted[:2], [(exact.settled, "within-limits"), (exact.peak, "within-limits")])
                denied = self.exercise(future=Budget(exact.settled, replace(peak, **{field: getattr(peak, field) + 1})), refuses=True)
                self.assertEqual(len(denied), 2, "consumer peak refusal must precede publication gates")
                self.assertEqual(denied[-1][1], refusal)

    def test_publication_record_and_byte_edges_include_prior_command_work(self):
        for field, limit, refusal in (("records", 4096, "record-limit"), ("value_octets", 16777216, "byte-limit")):
            with self.subTest(dimension=field):
                combined = self.prior.plus(self.publication.peak)
                extra = limit - (combined.records if field == "records" else combined.accounted_bytes)
                prior = replace(self.prior, **{field: getattr(self.prior, field) + extra})
                accepted = self.exercise(prior=prior)
                self.assertEqual(accepted[-2:], [(prior.plus(self.publication.settled), "within-limits"),
                    (prior.plus(self.publication.peak), "within-limits")])
                denied = self.exercise(prior=replace(prior, **{field: getattr(prior, field) + 1}), refuses=True)
                self.assertEqual(len(denied), 4)
                self.assertEqual(denied[:2], accepted[:2], "command prior cannot affect the fresh native consumer")
                self.assertEqual(denied[-1][1], refusal)
