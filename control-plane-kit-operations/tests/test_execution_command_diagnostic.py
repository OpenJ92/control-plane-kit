"""Closed receipt-owned evidence; not an activity result or new authority."""
from dataclasses import replace
import unittest

from control_plane_kit_operations import records


class ExecutionCommandDiagnosticTests(unittest.TestCase):
    def diagnostic_type(self):
        value = getattr(records, "ExecutionCommandDiagnostic", None)
        self.assertIsNotNone(value, "missing receipt-owned diagnostic value")
        return value

    def test_command_diagnostic_closed_round_trip(self):
        kind = self.diagnostic_type()
        for category in ("evidence-capacity", "material-unavailable"):
            value = kind("context-preparation", category, "2030-01-01T00:00:00Z", 1)
            self.assertEqual(kind.from_descriptor(value.descriptor()), value)
            self.assertEqual(set(value.descriptor()),
                {"phase", "category", "occurred_at", "last_committed_event_ordinal"})

    def test_command_diagnostic_refuses_open_fields_and_invalid_values(self):
        kind = self.diagnostic_type()
        value = kind("context-preparation", "evidence-capacity", "2030-01-01T00:00:00Z", 1)
        for field, invalid in (("phase", "provider"), ("category", "private-message"),
                ("occurred_at", "2030-01-01T00:00:00+00:00"),
                ("last_committed_event_ordinal", True), ("last_committed_event_ordinal", 0),
                ("last_committed_event_ordinal", 2**63)):
            with self.subTest(field=field, invalid=invalid), self.assertRaises(records.OperationsRecordError):
                replace(value, **{field: invalid})
        for descriptor in ({**value.descriptor(), "message": "private"}, {}, [], None):
            with self.assertRaises(records.OperationsRecordError):
                kind.from_descriptor(descriptor)
