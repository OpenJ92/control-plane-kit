from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from extraction_parity.manifest import (
    ManifestError,
    build_manifest,
    decode_manifest,
    write_manifest,
)


def reviewed_supersession() -> dict[str, str]:
    return {
        "rationale": "obsolete structural assertion replaced by stronger boundary",
        "review": "issue-732",
        "obsolete_assumption": "frozen test asserted the old package path",
        "replacement": "successor law asserts the public core import boundary",
        "negative_case_disposition": "invalid imports still fail in architecture tests",
    }


class ParityManifestTests(unittest.TestCase):
    def test_closed_manifest_round_trips_required_and_deferred_entries(self) -> None:
        document = {
            "schema": "cpk.parity-manifest",
            "reference": {"tag": "tag", "commit": "commit"},
            "entries": [
                {"kind": "test", "reference": "tests.a", "law": "behavior.a", "owner_kind": "core", "owner": "control-plane-kit-core", "migration_state": "required", "successors": [], "supersession": None},
                {"kind": "demo", "reference": "demo.x", "law": "demo.x", "owner_kind": "deferred-product", "owner": "control-plane-kit-servers:x", "migration_state": "deferred", "successors": [], "supersession": None},
            ],
        }
        self.assertEqual(decode_manifest(document), document)

    def test_unknown_fields_duplicates_and_unreviewed_supersession_fail_closed(self) -> None:
        base = {"schema": "cpk.parity-manifest", "reference": {"tag": "tag", "commit": "commit"}, "entries": []}
        with self.assertRaises(ManifestError):
            decode_manifest({**base, "extra": True})
        entry = {"kind": "test", "reference": "tests.a", "law": "behavior.a", "owner_kind": "core", "owner": "control-plane-kit-core", "migration_state": "required", "successors": [], "supersession": None}
        with self.assertRaises(ManifestError):
            decode_manifest({**base, "entries": [entry, entry]})
        with self.assertRaises(ManifestError):
            decode_manifest({**base, "entries": [{**entry, "supersession": {"rationale": "removed"}}]})
        incomplete = reviewed_supersession()
        incomplete.pop("negative_case_disposition")
        with self.assertRaises(ManifestError):
            decode_manifest({**base, "entries": [{**entry, "supersession": incomplete}]})

    def test_successor_and_supersession_states_are_closed_bounded_and_exclusive(self) -> None:
        base = {"schema": "cpk.parity-manifest", "reference": {"tag": "tag", "commit": "commit"}, "entries": []}
        entry = {"kind": "test", "reference": "tests.a", "law": "behavior.a", "owner_kind": "core", "owner": "control-plane-kit-core", "migration_state": "required", "successors": [], "supersession": None}
        valid_successor = {"id": "core:test-a", "status": "passing", "evidence": "sha256:abc"}
        self.assertEqual(
            decode_manifest({**base, "entries": [{**entry, "successors": [valid_successor]}]})["entries"][0]["successors"],
            [valid_successor],
        )
        for invalid in (
            {**valid_successor, "status": "unknown"},
            {**valid_successor, "id": ""},
            {**valid_successor, "evidence": ""},
            {**valid_successor, "extra": True},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ManifestError):
                    decode_manifest({**base, "entries": [{**entry, "successors": [invalid]}]})
        self.assertEqual(
            decode_manifest(
                {**base, "entries": [{**entry, "supersession": reviewed_supersession()}]}
            )["entries"][0]["supersession"],
            reviewed_supersession(),
        )
        with self.assertRaises(ManifestError):
            decode_manifest(
                {
                    **base,
                    "entries": [
                        {
                            **entry,
                            "successors": [valid_successor],
                            "supersession": reviewed_supersession(),
                        }
                    ],
                }
            )
        with self.assertRaises(ManifestError):
            decode_manifest({**base, "reference": {"tag": "t" * 513, "commit": "commit"}})

    def test_builder_preserves_owner_and_deferred_state(self) -> None:
        ownership = {"schema": "cpk.reference-law-ownership", "reference": {"tag": "tag", "commit": "commit"}, "count": 1, "law_count": 1, "owner_counts": {"core": 1, "hello": 0, "deferred-product": 0, "system": 0}, "laws": [{"reference": "tests.a", "law": "behavior.a", "collection_occurrences": 1, "owner_kind": "core", "owner": "control-plane-kit-core"}]}
        demos = {"schema": "cpk.reference-demo-inventory", "reference": {"tag": "tag", "commit": "commit"}, "demos": [{"id": "demo.x", "scripts": ["x.sh"], "fixtures": [], "documentation": [], "kind": "live-demo", "owner_kind": "deferred-product", "owner": "control-plane-kit-servers:x", "bootstrap_state": "deferred", "prerequisites": ["Docker"], "inputs": ["input"], "observables": ["result"], "normalization": [], "cleanup": ["clean"]}]}
        manifest = build_manifest(ownership, demos)
        self.assertEqual([entry["migration_state"] for entry in manifest["entries"]], ["deferred", "required"])

    def test_writer_is_deterministic_atomic_and_creates_its_parent(self) -> None:
        document = {"schema": "cpk.parity-manifest", "reference": {"tag": "tag", "commit": "commit"}, "entries": []}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "manifest.json"
            write_manifest(path, document)
            first = path.read_bytes()
            write_manifest(path, document)
            self.assertEqual(path.read_bytes(), first)
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
