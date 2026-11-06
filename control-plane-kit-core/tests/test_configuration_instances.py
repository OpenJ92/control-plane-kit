"""#1918 new-law targets: public allocation values are bounded, pure and exact."""
from dataclasses import FrozenInstanceError, fields, replace
import importlib
import unittest

import rfc8785

from control_plane_kit_core.configuration import ConfigurationFileMode, ConfigurationMediaType

MODULE = "control_plane_kit_core.configuration_instances"


def language():
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError as error:
        if error.name != MODULE:
            raise
        raise AssertionError("missing #1918 configuration-instance language") from error
    names = (
        "ConfigurationInstanceRef", "ConfigurationInstanceRefCodec",
        "ConfigurationInstanceSelection", "ConfigurationInstanceSelectionCodec",
        "ConfigurationCleanupOutcome", "ConfigurationCleanupOutcomeSet",
        "ConfigurationCleanupOutcomeSetCodec", "ConfigurationCleanupStatus",
        "ConfigurationCleanupReason",
    )
    missing = [name for name in names if not hasattr(module, name)]
    if missing:
        raise AssertionError(f"missing #1918 capabilities: {missing}")
    return module


def instance(**changes):
    arguments = dict(
        allocation_id="allocation-a", workspace_id="workspace-a", runtime_id="docker",
        node_id="api", artifact_id="service-config", target_path="/etc/service/config.json",
        media_type=ConfigurationMediaType.JSON, file_mode=ConfigurationFileMode.READ_ONLY,
        content_digest="a" * 64,
    )
    arguments.update(changes)
    return language().ConfigurationInstanceRef(**arguments)


def ref_descriptor(**changes):
    descriptor = dict(
        profile="configuration-instance.v1", allocation_id="allocation-a",
        workspace_id="workspace-a", runtime_id="docker", node_id="api",
        artifact_id="service-config", target_path="/etc/service/config.json",
        media_type="application/json", file_mode="0444", content_digest="a" * 64,
    )
    descriptor.update(changes)
    return descriptor


class ConfigurationInstanceTests(unittest.TestCase):
    def test_ref_has_only_frozen_public_coordinates_and_exact_canonical_codec(self):
        m = language()
        ref = instance()
        self.assertEqual(tuple(f.name for f in fields(ref)), (
            "allocation_id", "workspace_id", "runtime_id", "node_id", "artifact_id",
            "target_path", "media_type", "file_mode", "content_digest",
        ))
        with self.assertRaises(FrozenInstanceError):
            ref.allocation_id = "other"
        codec = m.ConfigurationInstanceRefCodec()
        expected = ref_descriptor()
        self.assertEqual(codec.encode(ref), expected)
        self.assertEqual(codec.decode(expected), ref)
        self.assertEqual(codec.encode_canonical_bytes(ref), rfc8785.dumps(expected))
        self.assertEqual(codec.decode_canonical_bytes(rfc8785.dumps(expected)), ref)
        for forbidden in ("content", "provider_locator", "authority", "secret", "current"):
            self.assertNotIn(forbidden, expected)

    def test_ref_rejects_bad_identity_path_digest_and_untyped_enums(self):
        language()
        cases = [(name, value) for name in (
            "allocation_id", "workspace_id", "runtime_id", "node_id"
        ) for value in ("", "x" * 129, " a", "a/b", "é", "a\x00", "a\ud800", 1, None)]
        cases += [("artifact_id", v) for v in ("A", "a" * 64, "a_b", "", None)]
        cases += [("target_path", v) for v in (
            "relative", "/etc/../config", "/etc//config", "/run/secrets/token",
            "/var/run/docker.sock", "/etc/config,readonly", "/etc/config file",
            "/" + "/".join(["a" * 127] * 4) + "a", None,
        )]
        cases += [("content_digest", v) for v in ("A" * 64, "a" * 63, "g" * 64, None)]
        cases += [("media_type", "application/json"), ("file_mode", "0444")]
        for name, value in cases:
            with self.subTest(name=name, value=value), self.assertRaises((ValueError, TypeError)):
                instance(**{name: value})
        boundary = instance(allocation_id="a" * 128, artifact_id="a" * 63,
            target_path="/" + "/".join(["a" * 127] * 4))
        self.assertEqual(len(boundary.target_path.encode()), 512)

    def test_ref_descriptor_and_canonical_bytes_are_strict_not_lossy(self):
        codec = language().ConfigurationInstanceRefCodec()
        good = ref_descriptor()
        candidates = [None, [], {**good, "profile": "configuration-instance.v2"},
            {**good, "extra": "ignored"}, {**good, "content": "workers=2"}]
        candidates += [{k: v for k, v in good.items() if k != missing} for missing in good]
        candidates += [{**good, k: None} for k in good]
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises((ValueError, TypeError)):
                codec.decode(candidate)
        canonical = rfc8785.dumps(good)
        for document in (b" " + canonical, canonical + b"\n", b'{"profile":"x","profile":"y"}',
                         b'{"profile":NaN}', b"\xff", b" " * 65537, canonical.decode()):
            with self.subTest(document_type=type(document)), self.assertRaises((ValueError, TypeError)):
                codec.decode_canonical_bytes(document)

    def test_ordinary_selection_is_exact_sorted_and_permutation_invariant(self):
        m = language()
        a = instance(artifact_id="alpha", allocation_id="z", target_path="/etc/a")
        b = instance(artifact_id="beta", allocation_id="a", target_path="/etc/b")
        selection = m.ConfigurationInstanceSelection((b, a))
        self.assertEqual(selection.instances, (a, b))
        self.assertEqual(selection, m.ConfigurationInstanceSelection((a, b)))
        codec = m.ConfigurationInstanceSelectionCodec()
        expected = {"profile": "configuration-instance-selection.v1", "instances": [
            m.ConfigurationInstanceRefCodec().encode(a), m.ConfigurationInstanceRefCodec().encode(b)]}
        self.assertEqual(codec.encode(selection), expected)
        self.assertEqual(codec.decode(expected), selection)
        self.assertEqual(codec.decode_canonical_bytes(codec.encode_canonical_bytes(selection)), selection)
        for malformed in ({**expected, "extra": True}, {**expected, "profile": "unknown"},
                          {**expected, "instances": None}, {"instances": expected["instances"]}):
            with self.subTest(malformed=malformed), self.assertRaises((ValueError, TypeError)):
                codec.decode(malformed)
        document = codec.encode_canonical_bytes(selection)
        for malformed in (b" " + document, document + b"\n", b'{"instances":[],"instances":[]}',
                          b'{"instances":NaN}', b"\xff", b" " * 65537, document.decode()):
            with self.subTest(document_type=type(malformed)), self.assertRaises((ValueError, TypeError)):
                codec.decode_canonical_bytes(malformed)

    def test_selection_refuses_duplicates_conflicts_cross_scope_and_cardinality(self):
        m = language()
        a = instance()
        for values in ((), (a, a), (a, replace(a, allocation_id="b")),
            (a, replace(a, artifact_id="other", target_path="/etc/other")),
            (a, replace(a, allocation_id="b", artifact_id="other")),
            (a, replace(a, allocation_id="b", target_path="/etc/other")),
            *((a, replace(a, allocation_id="b", artifact_id="other", target_path="/etc/other", **{scope: "other"}))
              for scope in ("workspace_id", "runtime_id", "node_id")),
            tuple(instance(allocation_id=f"a-{i}", artifact_id=f"slot-{i}", target_path=f"/etc/{i}") for i in range(33))):
            with self.subTest(count=len(values)), self.assertRaises((ValueError, TypeError)):
                m.ConfigurationInstanceSelection(values)
        maximum = tuple(instance(allocation_id=f"a-{i}", artifact_id=f"slot-{i}", target_path=f"/etc/{i}") for i in range(32))
        self.assertEqual(len(m.ConfigurationInstanceSelection(maximum).instances), 32)

    def test_outcome_reason_matrix_is_closed_and_not_provider_text(self):
        m = language()
        legal = {"removed": (None,), "already-absent": (None,), "retained-in-use": ("in-use",),
            "refused": ("ownership-mismatch", "provenance-unproven", "authority-refused"),
            "unknown": ("provider-uncertain", "not-attempted")}
        self.assertEqual({s.value for s in m.ConfigurationCleanupStatus}, set(legal))
        self.assertEqual({r.value for r in m.ConfigurationCleanupReason}, {r for rs in legal.values() for r in rs if r})
        for status, reasons in legal.items():
            for reason in (None, *m.ConfigurationCleanupReason):
                value = None if reason is None else reason.value
                if value in reasons:
                    row = m.ConfigurationCleanupOutcome(instance(), m.ConfigurationCleanupStatus(status), reason)
                    self.assertEqual(row.reason, reason)
                else:
                    with self.subTest(status=status, reason=value), self.assertRaises((ValueError, TypeError)):
                        m.ConfigurationCleanupOutcome(instance(), m.ConfigurationCleanupStatus(status), reason)
        for status, reason in (("removed", None), (m.ConfigurationCleanupStatus.UNKNOWN, "provider-uncertain"),
                               (m.ConfigurationCleanupStatus.REFUSED, "provider secret message")):
            with self.assertRaises((ValueError, TypeError)):
                m.ConfigurationCleanupOutcome(instance(), status, reason)
