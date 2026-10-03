"""Local production-envelope edges; synthetic values never authorize a write.

These are isolated arithmetic/assembly tests, not owner-valid retained history
or naturally reachable exhaustion. Real owner wiring has separate PostgreSQL
positive and explicitly fault-injected refusal witnesses.
"""
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from control_plane_kit_operations.configuration_preparation import (
    ConfigurationEvidenceFootprint as Footprint, configuration_evidence_capacity,
)
from control_plane_kit_operations.postgres import configuration_acceptance_store as acceptance


class ConfigurationAcceptancePreflightTests(unittest.TestCase):
    def exercise(self, *, owner=Footprint(7, 101, 11, 5), prior=Footprint(13, 211, 17, 3),
                 proof=Footprint(5, 31, 3, 2), refuses=False):
        # No database, UoW, owner-issued value, publication or persistence. The
        # actual _preflight method assembles every envelope and makes decisions.
        read = SimpleNamespace(used=prior, query=lambda *args, **kwargs: [(2, 2)])
        action = SimpleNamespace(action_id="action", session_id="session", actor_id="actor",
            idempotency_key="key", intent_fingerprint="fingerprint", payload={})
        event = SimpleNamespace(event_id="event", run_id="run", activity_id=None,
            evidence=SimpleNamespace(descriptor=lambda: {}))
        plan = SimpleNamespace(plan_id="plan", session_id="session", desired_graph_id="graph")
        run = SimpleNamespace(run_id="run")
        request = SimpleNamespace(identity=SimpleNamespace(request_id="request"))
        slots = tuple(("r", "n", artifact, "source", "act", 1, artifact,
            "birth", "act", 1, artifact, "0" * 64) for artifact in ("a", "b"))
        local = SimpleNamespace(action=action, event=event, plan=plan, run=run, request=request,
            workspace=SimpleNamespace(workspace_id="workspace"), slots=slots,
            desired_projection=SimpleNamespace(projection_id="projection", projection_digest="digest"),
            evidence_read=read, proof_footprint=proof)
        execution = SimpleNamespace(get_run=lambda _: run, get_request=lambda _: request)
        history = SimpleNamespace(get_plan=lambda _: plan, get_session=lambda _: None)
        store = acceptance.ConfigurationAcceptanceStore(None)
        decisions = []

        def projection(*args):
            read.used = read.used.plus(owner)

        def decide(footprint):
            result = configuration_evidence_capacity(footprint)
            decisions.append((footprint, result.value))
            return result

        with ExitStack() as stack:
            for name, replacement in (("_EvidenceRead", lambda _: read),
                    ("PostgresExecutionStore", lambda _: execution), ("PostgresActivityHistoryStore", lambda _: history)):
                stack.enter_context(mock.patch.object(acceptance, name, replacement))
            stack.enter_context(mock.patch.object(store, "_projection", projection))
            stack.enter_context(mock.patch.object(store, "_ref", lambda *args: (b"abc", True, None, 7)))
            stack.enter_context(mock.patch(
                "control_plane_kit_operations.configuration_preparation.configuration_evidence_capacity", decide))
            if refuses:
                with self.assertRaises(acceptance._Capacity):
                    store._preflight(local)
            else:
                store._preflight(local)
        return decisions

    def test_snapshot_exact_three_mib_then_one_byte_over(self):
        # Two88-octet slots; two12-octet conservative ref rows. Baseline snapshot
        # is (33,528975,381,25), accounting to545695 bytes, independent of prior.
        baseline = self.exercise()
        self.assertEqual(baseline, [
            (Footprint(102, 1053294, 1408, 59), "within-limits"),
            (Footprint(540, 8393482, 8298, 140), "within-limits")])
        exact = self.exercise(owner=Footprint(7, 2600134, 11, 5))
        self.assertEqual(exact[0][0].accounted_bytes, 3145728 + 557056 + 1231)
        self.assertEqual(len(exact), 2)
        # Snapshot failure precedes BOTH production global-budget decisions.
        self.assertEqual(self.exercise(owner=Footprint(7, 2600135, 11, 5), refuses=True), [])

    def test_cold_consumer_record_and_byte_edges_use_actual_envelope(self):
        for field, boundary, expected in (("records", 3999, "record-limit"),
                ("value_octets", 15673265, "byte-limit")):
            with self.subTest(dimension=field):
                proof = replace(Footprint(5, 31, 3, 2), **{field: boundary})
                exact = self.exercise(proof=proof)
                self.assertEqual(len(exact), 2)
                self.assertEqual(exact[0][1], "within-limits")
                self.assertEqual(exact[0][0].records if field == "records" else exact[0][0].accounted_bytes,
                    4096 if field == "records" else 16777216)
                over = self.exercise(proof=replace(proof, **{field: boundary + 1}), refuses=True)
                self.assertEqual(len(over), 1, "consumer refusal must precede publication reserve")
                self.assertEqual(over[0][1], expected)

    def test_publication_record_and_byte_edges_include_prior_command_work(self):
        for field, boundary, expected in (("records", 3569, "record-limit"),
                ("value_octets", 8146217, "byte-limit")):
            with self.subTest(dimension=field):
                prior = replace(Footprint(13, 211, 17, 3), **{field: boundary})
                exact = self.exercise(prior=prior)
                self.assertEqual(len(exact), 2)
                self.assertEqual(exact[1][1], "within-limits")
                self.assertEqual(exact[1][0].records if field == "records" else exact[1][0].accounted_bytes,
                    4096 if field == "records" else 16777216)
                over = self.exercise(prior=replace(prior, **{field: boundary + 1}), refuses=True)
                self.assertEqual(len(over), 2)
                self.assertEqual(over[0], exact[0], "prior work must not leak into cold-consumer snapshot")
                self.assertEqual(over[1][1], expected)
