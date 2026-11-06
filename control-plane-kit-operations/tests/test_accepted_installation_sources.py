"""Closed, canonical source commitments, independent of database authority."""
from dataclasses import replace
import json
import unittest

import rfc8785

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_operations._accepted_installation_sources import (
    _GeneratedAllocationSource, _InstallationSource, _InstallationSources,
)


class AcceptedInstallationSourcesTests(unittest.TestCase):
    def source(self, node="api", allocations=()):
        return _InstallationSource("runtime-a", node, EffectAttemptIdentity(RunId("run-a"), "start-" + node, 1),
            2, "a" * 64, "b" * 64, allocations)

    def test_exact_round_trip_and_fingerprints_commit_distinct_facts(self):
        value = _InstallationSources((self.source(),))
        self.assertEqual(_InstallationSources.decode(value.canonical_bytes().decode()), value)
        for changed in (replace(self.source(), original_request_fingerprint="c" * 64),
                replace(self.source(), outcome_fingerprint="c" * 64),
                replace(self.source(), accepted_revision=3)):
            self.assertNotEqual(_InstallationSources((changed,)).digest, value.digest)
        empty = _InstallationSources(())
        self.assertEqual(_InstallationSources.decode(empty.canonical_bytes().decode()), empty)
        self.assertEqual(json.loads(empty.canonical_bytes()),
            {"profile": "accepted-installation-sources.v1", "bindings": []})

    def test_missing_extra_duplicate_or_noncanonical_members_refuse(self):
        original = json.loads(_InstallationSources((self.source(),)).canonical_bytes())
        candidates = []
        for key in original["bindings"][0]:
            value = json.loads(json.dumps(original))
            del value["bindings"][0][key]
            candidates.append(rfc8785.dumps(value).decode())
        value = json.loads(json.dumps(original))
        value["bindings"][0]["untrusted"] = "sensitive-marker"
        candidates.extend((rfc8785.dumps(value).decode(), json.dumps(original),
            '{"profile":"accepted-installation-sources.v1","bindings":[],"bindings":[]}'))
        for candidate in candidates:
            with self.subTest(candidate_length=len(candidate)), self.assertRaises(ValueError) as caught:
                _InstallationSources.decode(candidate)
            self.assertNotIn("sensitive-marker", str(caught.exception))

    def test_unique_ordered_nodes_and_consistent_generated_origins(self):
        generated = _GeneratedAllocationSource("allocation-start", "allocation-success", "d" * 64)
        first = self.source(allocations=(generated,))
        second = self.source("worker", (generated,))
        value = _InstallationSources((first, second))
        self.assertEqual(_InstallationSources.decode(value.canonical_bytes().decode()), value)
        for bindings in ((first, first), (second, first), (first, replace(second,
                generated_allocations=(replace(generated, terminal_event_id="different-success"),)))):
            with self.assertRaises(ValueError):
                _InstallationSources(bindings)
        for revision in (True, -1, 9007199254740992):
            with self.assertRaises(ValueError):
                replace(first, accepted_revision=revision)

    def test_event_identity_and_corrupted_nested_run_refuse(self):
        for identity in ("allocation\nstart", "allocation\tstart", "a" * 513):
            with self.assertRaises(ValueError):
                _GeneratedAllocationSource(identity, "terminal", "d" * 64)
        source = self.source()
        object.__setattr__(source.source.run_id, "value", "invalid\nrun")
        with self.assertRaises(ValueError):
            _InstallationSources((source,)).canonical_bytes()

    def test_binding_count_and_utf8_byte_boundaries(self):
        bindings = tuple(self.source(f"node-{index:04d}") for index in range(1024))
        accepted = _InstallationSources(bindings)
        self.assertEqual(_InstallationSources.decode(accepted.canonical_bytes().decode()), accepted)
        with self.assertRaises(ValueError):
            _InstallationSources(bindings + (self.source("node-1024"),))
        # Valid individual identities can still exceed the total UTF-8 cap.
        wide = _InstallationSources(tuple(replace(value, runtime_id="é" * 512) for value in bindings))
        with self.assertRaises(ValueError):
            wide.canonical_bytes()
        with self.assertRaises(ValueError):
            _InstallationSources.decode("é" * 524289)
