"""Configuration commands retain graph-origin and accepted-history read budgets.

The existing recorded-acceptance fixture is a history premise, not deployment
evidence. No provider, runtime execution or accepted-current mutation is tested.
"""
import unittest

from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from control_plane_kit_operations.postgres.configuration_evidence import _Capacity
from control_plane_kit_operations.receiver_lifecycle import ReceiverLifecycleStorageError
from tests.receiver_admission_fixture import ReceiverAdmissionFixture
from tests.receiver_recorded_acceptance_fixture import record_accepted_current


class PostgresConfigurationReceiverAccountingTests(ReceiverAdmissionFixture, unittest.TestCase):
    def test_original_receiver_material_refuses_when_command_budget_is_already_full(self):
        self.desired_service().execute(self.desired_command())
        origin = self.introduction()
        before = self.admission_truth()
        with self.unit_of_work() as uow:
            stores = uow.stores
            operations = (
                lambda: stores.graphs.get(origin.introducing_graph_id),
                lambda: stores.realized_graphs.get(origin.introducing_realized_projection_id),
                lambda: stores.graphs.receiver_introduction(origin.workspace_id, origin.receiver_id),
                lambda: stores.graphs._require_receiver_origin_action(origin),
            )
            for operation in operations:
                # These are distinct existing owner boundaries reached by a
                # receiver-bearing configuration start, not SQL shape tests.
                with _configuration_accounting("configuration-command") as accounting:
                    operation()
                    self.assertGreater(accounting.used.accounted_bytes, 0)
                with _configuration_accounting("configuration-command") as accounting:
                    accounting.used = ConfigurationEvidenceFootprint(4096, 0, 0, 0)
                    with self.assertRaises(_Capacity):
                        operation()
        self.assertEqual(self.admission_truth(), before)

    def test_prior_acceptance_cannot_start_an_independent_command_budget(self):
        record_accepted_current(self)
        origin = self.introduction()
        before = self.admission_truth()
        with self.unit_of_work() as uow:
            with _configuration_accounting("configuration-command") as accounting:
                evidence = uow.stores.execution._receiver_acceptance_evidence((origin,))
                self.assertTrue(evidence)
                self.assertGreater(accounting.used.accounted_bytes, 0)
            with _configuration_accounting("configuration-command") as accounting:
                accounting.used = ConfigurationEvidenceFootprint(4096, 0, 0, 0)
                with self.assertRaises((ReceiverLifecycleStorageError, _Capacity)):
                    uow.stores.execution._receiver_acceptance_evidence((origin,))
        self.assertEqual(self.admission_truth(), before)
