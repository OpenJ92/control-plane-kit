"""#1939 strengthened original-read laws; cleanup execution remains unsupported."""
from dataclasses import replace
from importlib import import_module
from importlib.util import find_spec
import json
import unittest

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.admission import ExecutionAdmissionCommandService, ExecutionAdmissionConflict
from control_plane_kit_operations.postgres.configuration_evidence import _Capacity
from control_plane_kit_operations.postgres.stores import PostgresStoreBundle
from control_plane_kit_operations.runtime_effects import (
    runtime_effect_request_for_context, _runtime_effect_intent_for_context,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.configuration_cleanup_read_ceilings_fixture import ConfigurationCleanupReadCeilingsFixture
from tests.test_runtime_effect_translation import _context


class PostgresConfigurationCleanupReadCeilingsTests(ConfigurationCleanupReadCeilingsFixture, unittest.TestCase):
    def owner(self, uow):
        name = "control_plane_kit_operations.postgres.configuration_cleanup_read_ceilings"
        self.assertIsNotNone(find_spec(name), "#1939 private cleanup-original read-ceiling issuer is missing")
        module = import_module(name)
        self.assertTrue(hasattr(module, "_CleanupOriginalReadCeilingsOwner"),
            "#1939 private cleanup-original read-ceiling issuer is missing")
        return module._CleanupOriginalReadCeilingsOwner(uow)

    def capture(self, owner, guard, prefix):
        return owner.capture(guard, prefix, self.plan,
            intent_identity=self.identity, prospective_intent=self.intent)

    def independent_reads(self, uow):
        # New store objects prove lexical propagation, not one reader's cache.
        stores = PostgresStoreBundle(uow.stores.connection)
        self.assertEqual(stores.activity_history.get_plan(self.plan.plan_id), self.plan)
        graph = stores.graphs.get(self.plan.base_graph_id)
        projection = stores.realized_graphs.get(self.plan.base_realized_projection_id)
        self.assertEqual(graph.graph_id, projection.source_authored_graph_id)
        self.assertEqual(graph.workspace_id, projection.workspace_id)
        return graph, projection

    def test_00_existing_owner_control_precedes_missing_issuer(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.read_premise() as (uow, guard, prefix, read, observed):
            self.assert_existing_premise(uow, guard, prefix)
            payload_width, graph_widths, intent_width = self.width_evidence(uow.stores.connection)
            self.assertGreater(payload_width, 0)
            self.assertGreater(intent_width, 0)
            self.assertGreater(graph_widths[0], graph_widths[1])
            footprint, transport = read.used, observed["bytes"]
            self.independent_reads(uow)
            self.assertGreater(read.used.statements, footprint.statements)
            self.assertGreaterEqual(read.used.accounted_bytes - footprint.accounted_bytes,
                observed["bytes"] - transport)
        self.assertEqual(self.ceiling_truth(), before)
        calls = []

        def forbidden():
            calls.append("clock-or-id")
            self.fail("cleanup admission reached clock or identity allocation")

        with self.assertRaisesRegex(ExecutionAdmissionConflict, "configuration cleanup.*unsupported"):
            ExecutionAdmissionCommandService(self.unit_of_work, clock=forbidden, id_factory=forbidden).execute(
                self.command(plan_id=self.plan.plan_id, approval_request_id=self.approval.request_id,
                    scopes=tuple(PolicyScope),
                    key="ceilings-public-refusal"))
        self.assertEqual(calls, [])
        activity = self.plan.plan.activities[0]
        context = _context(activity=activity)
        for translate in (lambda: runtime_effect_request_for_context(context),
                lambda: _runtime_effect_intent_for_context(context, activity)):
            with self.assertRaisesRegex(InvalidOperationCommand, "configuration cleanup.*unsupported"):
                translate()
        self.assertEqual(self.ceiling_truth(), before)

    def test_capture_propagates_to_independent_original_readers(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.read_premise() as (uow, guard, prefix, read, observed):
            self.assert_existing_premise(uow, guard, prefix)
            self.width_evidence(uow.stores.connection)
            owner = self.owner(uow)
            issued = self.capture(owner, guard, prefix)
            self.assertNotIn(self.plan.plan_id, repr(issued))
            with owner.bind(issued):
                initial, physical = read.used, observed["bytes"]
                expected = self.independent_reads(uow)
                self.assertEqual(self.independent_reads(uow), expected)
                # Six matching point reads: txid + length + guarded value each.
                self.assertEqual(read.used.statements - initial.statements, 18)
                self.assertGreaterEqual(read.used.accounted_bytes - initial.accounted_bytes,
                    observed["bytes"] - physical)
                for _ in range(2):
                    original, derived = uow.stores.execution._receiver_execution_material(prefix.request.identity, guard)
                    self.assertEqual(original[0], self.plan)
                    self.assertTrue(derived.scopes)
        self.assertEqual(self.ceiling_truth(), before)

    def test_metadata_growth_refuses_before_full_value_transport(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.read_premise() as (uow, guard, prefix, read, observed):
            self.assert_existing_premise(uow, guard, prefix)
            owner = self.owner(uow)
            issued = self.capture(owner, guard, prefix)
            # Explicit same-UoW corrupt-history injection, below the ordinary
            # 1MiB metadata cap. The positive fixture was established above.
            enlarged = json.dumps({"fault-injected": "x" * 65536})
            uow.stores.connection.execute("UPDATE cpk_graph_versions SET metadata=%s::jsonb WHERE graph_id=%s",
                (enlarged, self.plan.base_graph_id))
            observed["largest_cell"] = 0
            with owner.bind(issued):
                with self.assertRaises(_Capacity):
                    PostgresStoreBundle(uow.stores.connection).graphs.get(self.plan.base_graph_id)
            self.assertLess(observed["largest_cell"], 65536)
        self.assertEqual(self.ceiling_truth(), before)

    def test_equal_copy_cannot_borrow_issued_binding(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.read_premise() as (uow, guard, prefix, read, observed):
            self.assert_existing_premise(uow, guard, prefix)
            owner = self.owner(uow)
            issued = self.capture(owner, guard, prefix)
            copied = replace(issued)
            self.assertIsNot(copied, issued)
            with self.assertRaises(ValueError):
                with owner.bind(copied):
                    self.independent_reads(uow)
            with owner.bind(issued):
                self.independent_reads(uow)
        self.assertEqual(self.ceiling_truth(), before)
