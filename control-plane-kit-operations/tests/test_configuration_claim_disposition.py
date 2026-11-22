"""B1 closed disposition values describe evidence; they never issue authority."""
from dataclasses import FrozenInstanceError, replace
import unittest

from control_plane_kit_core.configuration import ConfigurationFileMode, ConfigurationMediaType
from control_plane_kit_core.configuration_instances import ConfigurationInstanceRef
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_operations import configuration_preparation as values
from control_plane_kit_operations.records import OperationsRecordError


class ConfigurationClaimDispositionTests(unittest.TestCase):
    def disposition_type(self):
        value = getattr(values, "_ConfigurationClaimDisposition", None)
        self.assertTrue(callable(value), "missing closed original-claim disposition value")
        return value

    def test_closed_variants_preserve_zero_revision_and_exact_cleanup_identity(self):
        value = self.disposition_type()
        cleanup = EffectAttemptIdentity(RunId("cleanup-run"), "cleanup", 1)
        outstanding = value("outstanding")
        self.assertIsNone(outstanding.cleanup_identity)
        self.assertIsNone(outstanding.acceptance_revision)
        for revision in (0, 9007199254740991):
            accepted = value("accepted-current", acceptance_revision=revision)
            self.assertEqual(accepted.acceptance_revision, revision)
            self.assertIsNone(accepted.cleanup_identity)
        closed = value("cleanup-closed", cleanup_identity=cleanup)
        self.assertEqual(closed.cleanup_identity, cleanup)
        self.assertIsNone(closed.acceptance_revision)
        with self.assertRaises(FrozenInstanceError):
            outstanding.kind = "accepted-current"

    def test_unknown_mixed_and_incomplete_variants_refuse(self):
        value = self.disposition_type()
        cleanup = EffectAttemptIdentity(RunId("cleanup-run"), "cleanup", 1)
        cases = (
            ("unknown", {}), (True, {}), ("outstanding", {"acceptance_revision": 0}),
            ("outstanding", {"cleanup_identity": cleanup}), ("accepted-current", {}),
            ("accepted-current", {"acceptance_revision": 0, "cleanup_identity": cleanup}),
            ("cleanup-closed", {}), ("cleanup-closed", {"cleanup_identity": object()}),
            ("cleanup-closed", {"cleanup_identity": cleanup, "acceptance_revision": 0}),
        )
        for kind, fields in cases:
            with self.subTest(kind=kind, fields=fields), self.assertRaises(OperationsRecordError):
                value(kind, **fields)
        for revision in (True, False, -1, 9007199254740992, 0.0, "0"):
            with self.subTest(revision=revision), self.assertRaises(OperationsRecordError):
                value("accepted-current", acceptance_revision=revision)

    def test_transfer_record_validates_domains_is_frozen_and_redacts_material(self):
        record = getattr(values, "ConfigurationAcceptedTransferRecord", None)
        self.assertTrue(callable(record), "missing nonauthorizing accepted-transfer evidence record")
        identity = EffectAttemptIdentity(RunId("source-run"), "source", 1)
        ref = ConfigurationInstanceRef("allocation-a", "workspace-a", "runtime-a", "node-a",
            "settings", "/private/material.json", ConfigurationMediaType.JSON,
            ConfigurationFileMode.READ_ONLY, "a" * 64)
        original = record(identity, ref, 0, "b" * 64, "c" * 64, "d" * 64)
        self.assertEqual((original.identity, original.ref, original.acceptance_revision), (identity, ref, 0))
        for marker in (ref.target_path, ref.content_digest, "b" * 64, "c" * 64, "d" * 64):
            self.assertNotIn(marker, repr(original))
        with self.assertRaises(FrozenInstanceError):
            original.acceptance_revision = 1
        for field, invalid in (("identity", object()), ("ref", object()),
                ("acceptance_revision", True), ("acceptance_revision", -1),
                ("acceptance_revision", 9007199254740992),
                ("request_fingerprint", "B" * 64), ("selection_fingerprint", "bad"),
                ("outcome_fingerprint", None)):
            with self.subTest(field=field), self.assertRaises(OperationsRecordError):
                replace(original, **{field: invalid})
