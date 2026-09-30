"""#1903 new-law: five explicit pins form expected truth, never authority."""

from dataclasses import FrozenInstanceError, replace
import unittest

from control_plane_kit_operations import receiver_lifecycle


class ReceiverLifecycleExpectationTests(unittest.TestCase):
    def value_type(self):
        value = getattr(receiver_lifecycle, "ReceiverLifecycleExpectation", None)
        self.assertTrue(callable(value), "#1903 missing five-pin ReceiverLifecycleExpectation")
        return value

    def values(self):
        return dict(current_graph_id="current", current_realized_projection_id="current-projection",
            desired_graph_id="desired", desired_realized_projection_id="desired-projection",
            desired_graph_revision=3)

    def test_exact_five_key_descriptor_and_immutable_value(self):
        value = self.value_type()(**self.values())
        self.assertEqual(value.descriptor(), self.values())
        with self.assertRaises(FrozenInstanceError):
            value.desired_graph_revision = 4

    def test_absent_desired_pair_requires_zero_generation(self):
        value = self.value_type()(**(self.values() | dict(desired_graph_id=None,
            desired_realized_projection_id=None, desired_graph_revision=0)))
        self.assertIsNone(value.descriptor()["desired_graph_id"])
        self.assertIsNone(value.descriptor()["desired_realized_projection_id"])
        self.assertEqual(value.descriptor()["desired_graph_revision"], 0)
        for changes in (
            dict(desired_graph_id=None), dict(desired_realized_projection_id=None),
            dict(desired_graph_id=None, desired_realized_projection_id=None),
            dict(desired_graph_revision=0), dict(desired_graph_revision=True),
            dict(desired_graph_revision=-1), dict(desired_graph_revision="3"),
            dict(desired_graph_revision=9_223_372_036_854_775_808),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.value_type()(**(self.values() | changes))

    def test_current_pair_and_public_references_cannot_be_missing_or_unbounded(self):
        value_type = self.value_type()
        for key in ("current_graph_id", "current_realized_projection_id",
                    "desired_graph_id", "desired_realized_projection_id"):
            self.assertEqual(value_type(**(self.values() | {key: "x" * 256})).descriptor()[key], "x" * 256)
            for malformed in ("", " ", "bad\nreference", "x" * 257, 42, True):
                with self.subTest(key=key, malformed=malformed), self.assertRaises(ValueError):
                    value_type(**(self.values() | {key: malformed}))
        for key in ("current_graph_id", "current_realized_projection_id"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                value_type(**(self.values() | {key: None}))

    def test_each_pin_is_part_of_the_value_and_descriptor(self):
        original = self.value_type()(**self.values())
        for key, changed in dict(current_graph_id="other-current",
                current_realized_projection_id="other-current-projection",
                desired_graph_id="other-desired", desired_realized_projection_id="other-desired-projection",
                desired_graph_revision=4).items():
            with self.subTest(key=key):
                later = replace(original, **{key: changed})
                self.assertNotEqual(later, original)
                self.assertEqual(later.descriptor(), self.values() | {key: changed})
