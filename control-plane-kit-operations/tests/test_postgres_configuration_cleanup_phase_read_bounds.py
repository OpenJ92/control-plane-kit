"""#1941 fixed phase read targets; public cleanup execution stays closed."""
from dataclasses import replace
from importlib import import_module
from importlib.util import find_spec
import json
import unittest

from control_plane_kit_operations.postgres.configuration_evidence import _Capacity, _Unavailable
from control_plane_kit_operations.postgres.stores import PostgresStoreBundle
from tests.configuration_cleanup_phase_read_bounds_fixture import ConfigurationCleanupPhaseReadBoundsFixture


class PostgresConfigurationCleanupPhaseReadBoundsTests(ConfigurationCleanupPhaseReadBoundsFixture, unittest.TestCase):
    def phase_owner(self, uow):
        name = "control_plane_kit_operations.postgres.configuration_cleanup_phase_read_bounds"
        self.assertIsNotNone(find_spec(name), "#1941 fixed phase read-bound enforcement is missing")
        module = import_module(name)
        self.assertTrue(hasattr(module, "_CleanupPhaseReadBoundsOwner"),
            "#1941 fixed phase read-bound enforcement is missing")
        return module._CleanupPhaseReadBoundsOwner(uow)

    def capture_phase(self, owner, guard, prefix):
        return owner.capture(guard, prefix, self.plan,
            intent_identity=self.identity, prospective_intent=self.intent)

    def test_00_existing_receiver_read_rehearsal_precedes_missing_bounds(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            self.read_chain(uow, guard, prefix, read, observed)
        self.assertEqual(self.ceiling_truth(), before)

    def test_phase_binding_reaches_independent_cold_proof_readers(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            self.read_chain(uow, guard, prefix, read, observed)
            owner = self.phase_owner(uow)
            issued = self.capture_phase(owner, guard, prefix)
            self.assertNotIn(self.plan.plan_id, repr(issued))
            with owner.bind(issued):
                self.read_chain(uow, guard, prefix, read, observed)
        self.assertEqual(self.ceiling_truth(), before)

    def test_raw_material_growth_is_rejected_before_full_transport(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            self.read_chain(uow, guard, prefix, read, observed)
            owner = self.phase_owner(uow)
            issued = self.capture_phase(owner, guard, prefix)
            uow.stores.connection.execute("UPDATE cpk_graph_versions SET metadata=%s::jsonb WHERE graph_id=%s",
                (json.dumps({"fault-injected": "x" * 65536}), self.plan.base_graph_id))
            observed["largest_cell"] = 0
            with owner.bind(issued):
                # This independent lifecycle reader bypasses the #1939 getter.
                with self.assertRaises(_Capacity):
                    PostgresStoreBundle(uow.stores.connection).graphs.receiver_bindings(
                        "workspace-a", self.plan.base_graph_id, self.plan.base_realized_projection_id)
            self.assertLess(observed["largest_cell"], 65536)
        self.assertEqual(self.ceiling_truth(), before)

    def test_equal_copy_and_spent_phase_binding_refuse(self):
        self.prepare_ceiling_premise()
        before = self.ceiling_truth()
        with self.phase_premise() as (uow, guard, prefix, read, observed):
            self.read_chain(uow, guard, prefix, read, observed)
            owner = self.phase_owner(uow)
            issued = self.capture_phase(owner, guard, prefix)
            for invalid in (None, replace(issued)):
                with self.assertRaises(_Unavailable):
                    with owner.bind(invalid):
                        self.fail("unissued phase value entered the binding")
            with owner.bind(issued):
                self.read_chain(uow, guard, prefix, read, observed)
            with self.assertRaises(_Unavailable):
                with owner.bind(issued):
                    self.fail("spent phase value entered the binding")
        self.assertEqual(self.ceiling_truth(), before)
