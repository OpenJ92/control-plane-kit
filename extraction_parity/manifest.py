"""Closed isomorphic parity-manifest language."""

from __future__ import annotations

import json
import os
from pathlib import Path


class ManifestError(ValueError):
    pass


MAXIMUM_TEXT_LENGTH = 512
MANIFEST_SCHEMA = "cpk.parity-manifest"
ENTRY_FIELDS = {
    "kind",
    "reference",
    "law",
    "owner_kind",
    "owner",
    "migration_state",
    "successors",
    "supersession",
}


def _bounded_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be non-blank text")
    if len(value.encode("utf-8")) > MAXIMUM_TEXT_LENGTH:
        raise ManifestError(f"{field} exceeds the byte bound")
    return value


def decode_manifest(document: dict[str, object]) -> dict[str, object]:
    if set(document) != {"schema", "reference", "entries"}:
        raise ManifestError("manifest has unknown or missing root fields")
    if document["schema"] != MANIFEST_SCHEMA:
        raise ManifestError("unsupported manifest schema")
    reference = document["reference"]
    if not isinstance(reference, dict) or set(reference) != {"tag", "commit"}:
        raise ManifestError("reference identity is not closed")
    _bounded_text(reference["tag"], "reference.tag")
    _bounded_text(reference["commit"], "reference.commit")
    entries = document["entries"]
    if not isinstance(entries, list):
        raise ManifestError("entries must be a list")
    identities: set[tuple[str, str]] = set()
    laws: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS:
            raise ManifestError("manifest entry has unknown or missing fields")
        if entry["kind"] not in {"test", "demo"}:
            raise ManifestError("unknown manifest entry kind")
        if entry["owner_kind"] not in {"core", "hello", "deferred-product", "system"}:
            raise ManifestError("unknown owner kind")
        if entry["migration_state"] not in {"required", "deferred"}:
            raise ManifestError("unknown migration state")
        for field in ("reference", "law", "owner"):
            _bounded_text(entry[field], field)
        identity = (entry["kind"], entry["reference"])
        if identity in identities or entry["law"] in laws:
            raise ManifestError("duplicate manifest reference or law")
        identities.add(identity)
        laws.add(entry["law"])
        successors = entry["successors"]
        if not isinstance(successors, list):
            raise ManifestError("successors must be a list")
        for successor in successors:
            if not isinstance(successor, dict) or set(successor) != {"id", "status", "evidence"}:
                raise ManifestError("successor evidence is not closed")
            _bounded_text(successor["id"], "successor.id")
            if successor["status"] not in {"passing", "failed"}:
                raise ManifestError("unknown successor status")
            _bounded_text(successor["evidence"], "successor.evidence")
        supersession = entry["supersession"]
        if supersession is not None:
            if not isinstance(supersession, dict) or set(supersession) != {"rationale", "review"}:
                raise ManifestError("supersession requires rationale and review")
            if successors:
                raise ManifestError("superseded entries cannot also have successors")
            _bounded_text(supersession["rationale"], "supersession.rationale")
            _bounded_text(supersession["review"], "supersession.review")
    return document


def build_manifest(
    ownership: dict[str, object], demos: dict[str, object]
) -> dict[str, object]:
    if ownership.get("schema") != "cpk.reference-law-ownership":
        raise ManifestError("unsupported ownership inventory")
    if demos.get("schema") != "cpk.reference-demo-inventory":
        raise ManifestError("unsupported demo inventory")
    if ownership.get("reference") != demos.get("reference"):
        raise ManifestError("reference inventories do not identify the same frozen source")
    entries: list[dict[str, object]] = []
    for law in ownership["laws"]:
        entries.append(
            {
                "kind": "test",
                "reference": law["reference"],
                "law": law["law"],
                "owner_kind": law["owner_kind"],
                "owner": law["owner"],
                "migration_state": "deferred" if law["owner_kind"] == "deferred-product" else "required",
                "successors": [],
                "supersession": None,
            }
        )
    for demo in demos["demos"]:
        entries.append(
            {
                "kind": "demo",
                "reference": demo["id"],
                "law": demo["id"],
                "owner_kind": demo["owner_kind"],
                "owner": demo["owner"],
                "migration_state": demo["bootstrap_state"],
                "successors": [],
                "supersession": None,
            }
        )
    document = {
        "schema": MANIFEST_SCHEMA,
        "reference": ownership["reference"],
        "entries": sorted(entries, key=lambda item: (str(item["kind"]), str(item["reference"]))),
    }
    return decode_manifest(document)


def write_manifest(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(decode_manifest(document), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
