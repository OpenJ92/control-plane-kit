"""#1923 configuration compensation/restore remains explicitly unsupported."""
from dataclasses import replace
import unittest

from control_plane_kit_core.operations import ActivityEventKind
from control_plane_kit_core.planning import ActivityId, NodeTarget, PlannedActivity, ReconcileNode, StopNode
from control_plane_kit_operations.runtime_effects import runtime_effect_request_for_context
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.test_runtime_effect_translation import _configuration_graph, _configuration_product, _context


class ConfigurationCompensationRefusalTests(unittest.TestCase):
    def test_configuration_inverse_stop_and_restore_refuse_before_request_creation(self):
        product = _configuration_product()
        current = _configuration_graph(product, selection="original-current")
        desired = _configuration_graph(product, selection="failed-desired")
        for operation in (StopNode(NodeTarget("api")), ReconcileNode(NodeTarget("api"))):
            with self.subTest(operation=operation):
                context = _context(activity=PlannedActivity(ActivityId("inverse-api"), operation),
                    base_graph=current, desired_graph=desired, registered_products=(product,))
                context = replace(context, intent_event=replace(context.intent_event,
                    kind=ActivityEventKind.STEP_COMPENSATION_STARTED))
                with self.assertRaisesRegex(InvalidOperationCommand, "configuration.*unsupported"):
                    runtime_effect_request_for_context(context)
