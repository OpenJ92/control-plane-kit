"""Saved input laws extend the existing preparation value, not graph algebra."""
from dataclasses import replace
from importlib import import_module
import unittest

from control_plane_kit_operations.records import GraphProjectionLineage
from control_plane_kit_operations.workflows import IdempotencyKey
from draft_catalogue_fixture import principal


class SavedPreparationValueTests(unittest.TestCase):
    def test_saved_input_is_exact_bounded_and_requires_complete_selected_lineage(self):
        module = import_module("control_plane_kit_operations.deployment_program")
        self.assertTrue(callable(getattr(module, "SavedDesiredTopologyRevision", None)),
                        "missing saved desired revision input")
        saved = module.SavedDesiredTopologyRevision("draft-a", 1)
        for draft, revision in (("", 1), ("d" * 513, 1), ("bad\nprivate", 1),
                                ("draft", True), ("draft", 0), ("draft", -1), ("draft", "1"),
                                ("draft", 9223372036854775808)):
            with self.subTest(draft=draft, revision=revision), self.assertRaises(module.InvalidDeploymentProgramContract):
                module.SavedDesiredTopologyRevision(draft, revision)
        command = module.PrepareDeploymentProgram(
            context=principal().command_context("workspace-a"), desired=saved,
            expected_current=GraphProjectionLineage("current", "current-projection"),
            expected_desired=GraphProjectionLineage("desired", "desired-projection"),
            expected_desired_graph_revision=1, title="private-title",
            idempotency_key=IdempotencyKey("prepare"), approval_comment="private-comment")
        for changes in ({"expected_desired": None, "expected_desired_graph_revision": 0},
                        {"expected_desired_graph_revision": 9223372036854775808}):
            with self.subTest(changes=changes), self.assertRaises(module.InvalidDeploymentProgramContract):
                replace(command, **changes)
        self.assertEqual(command.desired, saved)
        for canary in ("private-title", "private-comment", "operator-a"):
            self.assertNotIn(canary, repr(command) + repr(command.descriptor()))
