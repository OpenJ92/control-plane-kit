"""Cross-document parity validation and reporting."""

from __future__ import annotations

import argparse
from collections import Counter
from enum import Enum
import json
import os
from pathlib import Path
import re
from typing import Iterable

from extraction_parity.manifest import build_manifest, decode_manifest


class ValidationError(ValueError):
    pass


class ValidationPolicy(str, Enum):
    FOUNDATION = "foundation"
    MIGRATION_COMPLETE = "migration-complete"


EVIDENCE_SCHEMA = "cpk.successor-evidence-index"
REPORT_SCHEMA = "cpk.parity-validation-report"
CORE_CLOSEOUT_REPORT_SCHEMA = "cpk.required-core-parity-closeout"
CORE_FAMILY_INVENTORY_SCHEMA = "cpk.required-core-family-inventory"
MAXIMUM_INPUT_BYTES = 16 * 1024 * 1024
MAXIMUM_TEXT_BYTES = 512
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be non-blank text")
    if len(value.encode("utf-8")) > MAXIMUM_TEXT_BYTES:
        raise ValidationError(f"{label} exceeds the byte bound")
    return value


def decode_evidence_index(document: dict[str, object]) -> dict[str, object]:
    if set(document) != {"schema", "evidence"}:
        raise ValidationError("evidence index has unknown or missing root fields")
    if document["schema"] != EVIDENCE_SCHEMA:
        raise ValidationError("unsupported evidence index schema")
    records = document["evidence"]
    if not isinstance(records, list):
        raise ValidationError("evidence must be a list")
    identities: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"id", "status", "digest"}:
            raise ValidationError("evidence record is not closed")
        identity = _text(record["id"], "evidence.id")
        if identity in identities:
            raise ValidationError("evidence identities must be unique")
        identities.add(identity)
        if record["status"] not in {"passing", "failed"}:
            raise ValidationError("unknown evidence status")
        digest = _text(record["digest"], "evidence.digest")
        if _DIGEST.fullmatch(digest) is None:
            raise ValidationError("evidence digest must be canonical SHA-256")
    return document


def _finding(code: str, kind: str, reference: str, detail: str) -> dict[str, str]:
    return {"code": code, "kind": kind, "reference": reference, "detail": detail}


def _completion(
    entry: dict[str, object],
    evidence: dict[str, dict[str, str]],
    findings: list[dict[str, str]],
) -> tuple[bool, int, int]:
    if entry["supersession"] is not None:
        return True, 0, 0
    passing = 0
    failed = 0
    for successor in entry["successors"]:
        proof = evidence.get(successor["evidence"])
        if proof is None:
            findings.append(_finding("missing_evidence", entry["kind"], entry["reference"], successor["evidence"]))
            continue
        if proof["status"] != successor["status"]:
            findings.append(_finding("evidence_status_mismatch", entry["kind"], entry["reference"], successor["evidence"]))
            continue
        if proof["status"] == "passing":
            passing += 1
        else:
            failed += 1
    return passing > 0, passing, failed


def validate_parity(
    manifest: dict[str, object],
    ownership: dict[str, object],
    demos: dict[str, object],
    evidence_index: dict[str, object],
    *,
    policy: ValidationPolicy,
) -> dict[str, object]:
    if not isinstance(policy, ValidationPolicy):
        raise ValidationError("policy must be a closed ValidationPolicy")
    decode_manifest(manifest)
    decode_evidence_index(evidence_index)
    expected = build_manifest(ownership, demos)
    findings: list[dict[str, str]] = []
    if manifest["reference"] != expected["reference"]:
        findings.append(
            _finding(
                "reference_mismatch",
                "manifest",
                "frozen-reference",
                "tag or commit differs from authoritative inventories",
            )
        )
    expected_entries = {(entry["kind"], entry["reference"]): entry for entry in expected["entries"]}
    actual_entries = {(entry["kind"], entry["reference"]): entry for entry in manifest["entries"]}
    for identity, entry in expected_entries.items():
        actual = actual_entries.get(identity)
        if actual is None:
            findings.append(_finding("missing_mapping", identity[0], identity[1], "frozen reference is absent"))
            continue
        for field in ("law", "owner_kind", "owner", "migration_state"):
            if actual[field] != entry[field]:
                findings.append(_finding("mapping_mismatch", identity[0], identity[1], field))
    for identity in actual_entries.keys() - expected_entries.keys():
        findings.append(_finding("stale_mapping", identity[0], identity[1], "reference is not in frozen inventories"))

    evidence = {record["id"]: record for record in evidence_index["evidence"]}
    required = 0
    deferred = 0
    passing_successors = 0
    failed_successors = 0
    reviewed_supersessions = 0
    incomplete_required = 0
    for entry in manifest["entries"]:
        required += entry["migration_state"] == "required"
        deferred += entry["migration_state"] == "deferred"
        reviewed_supersessions += entry["supersession"] is not None
        complete, passing, failed = _completion(entry, evidence, findings)
        passing_successors += passing
        failed_successors += failed
        if entry["migration_state"] == "required" and not complete:
            incomplete_required += 1
            if policy is ValidationPolicy.MIGRATION_COMPLETE:
                if failed:
                    findings.append(_finding("failed_evidence", entry["kind"], entry["reference"], "no passing successor evidence"))
                findings.append(_finding("required_without_completion", entry["kind"], entry["reference"], "passing successor or reviewed supersession is required"))

    owner_counts = Counter(str(entry["owner_kind"]) for entry in manifest["entries"])
    findings.sort(key=lambda finding: (finding["code"], finding["kind"], finding["reference"], finding["detail"]))
    migration_complete = incomplete_required == 0 and not findings
    return {
        "schema": REPORT_SCHEMA,
        "policy": policy.value,
        "valid": not findings,
        "migration_complete": migration_complete,
        "counts": {
            "entries": len(manifest["entries"]),
            "required": required,
            "deferred": deferred,
            "passing_successors": passing_successors,
            "failed_successors": failed_successors,
            "reviewed_supersessions": reviewed_supersessions,
            "incomplete_required": incomplete_required,
            "findings": len(findings),
            "by_owner": dict(sorted(owner_counts.items())),
        },
        "findings": findings,
    }


def _entry_reference(entry: dict[str, object]) -> dict[str, str]:
    return {
        "kind": str(entry["kind"]),
        "reference": str(entry["reference"]),
        "law": str(entry["law"]),
        "owner_kind": str(entry["owner_kind"]),
        "owner": str(entry["owner"]),
    }


def _family_for_reference(reference: str) -> str:
    if reference.startswith("tests."):
        parts = reference.split(".")
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    if reference.startswith("demo."):
        return "demo"
    if reference.startswith("validation."):
        return "validation"
    return reference.split(".", maxsplit=1)[0]


def _decode_core_closeout_entry(value: object) -> dict[str, str]:
    expected = {"kind", "reference", "law", "owner_kind", "owner"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValidationError("required-core inventory entry is not closed")
    return {
        "kind": _text(value["kind"], "entry.kind"),
        "reference": _text(value["reference"], "entry.reference"),
        "law": _text(value["law"], "entry.law"),
        "owner_kind": _text(value["owner_kind"], "entry.owner_kind"),
        "owner": _text(value["owner"], "entry.owner"),
    }


def inventory_unmapped_required_core_families(
    closeout_report: dict[str, object],
) -> dict[str, object]:
    if closeout_report.get("schema") != CORE_CLOSEOUT_REPORT_SCHEMA:
        raise ValidationError("unsupported required-core closeout schema")
    entries = closeout_report.get("incomplete_required_core_entries")
    if not isinstance(entries, list):
        raise ValidationError("incomplete required-core entries must be a list")

    laws: set[str] = set()
    grouped: dict[str, list[dict[str, str]]] = {}
    for raw_entry in entries:
        entry = _decode_core_closeout_entry(raw_entry)
        if entry["law"] in laws:
            raise ValidationError("required-core law identities must be unique")
        laws.add(entry["law"])
        grouped.setdefault(_family_for_reference(entry["reference"]), []).append(entry)

    families = []
    for family, family_entries in grouped.items():
        ordered_entries = sorted(
            family_entries,
            key=lambda entry: (entry["kind"], entry["reference"], entry["law"]),
        )
        families.append(
            {
                "family": family,
                "count": len(ordered_entries),
                "entries": ordered_entries,
            }
        )
    families.sort(key=lambda family: (-int(family["count"]), str(family["family"])))
    return {
        "schema": CORE_FAMILY_INVENTORY_SCHEMA,
        "valid": True,
        "counts": {
            "entries": len(entries),
            "families": len(families),
        },
        "families": families,
    }


def validate_required_core_closeout(
    manifest: dict[str, object],
    ownership: dict[str, object],
    demos: dict[str, object],
    evidence_index: dict[str, object],
) -> dict[str, object]:
    """Validate the EXTRACT.E required-core parity slice.

    This is deliberately narrower than ``MIGRATION_COMPLETE``: it requires the
    core-owned required entries to be complete while keeping Hello, system, and
    deferred product/server work visible for later milestones.
    """

    foundation = validate_parity(
        manifest,
        ownership,
        demos,
        evidence_index,
        policy=ValidationPolicy.FOUNDATION,
    )
    evidence = {record["id"]: record for record in decode_evidence_index(evidence_index)["evidence"]}
    findings = list(foundation["findings"])
    required_core = 0
    required_non_core = 0
    completed_required_core = 0
    incomplete_required_core = 0
    passing_core_successors = 0
    failed_core_successors = 0
    reviewed_core_supersessions = 0
    deferred_entries: list[dict[str, str]] = []
    incomplete_core_entries: list[dict[str, str]] = []

    for entry in decode_manifest(manifest)["entries"]:
        if entry["migration_state"] == "deferred":
            deferred_entries.append(_entry_reference(entry))
        if entry["migration_state"] != "required":
            continue
        if entry["owner_kind"] != "core":
            required_non_core += 1
            continue
        required_core += 1
        reviewed_core_supersessions += entry["supersession"] is not None
        complete, passing, failed = _completion(entry, evidence, findings)
        passing_core_successors += passing
        failed_core_successors += failed
        if complete:
            completed_required_core += 1
        else:
            incomplete_required_core += 1
            incomplete_core_entries.append(_entry_reference(entry))
            if failed:
                findings.append(
                    _finding(
                        "failed_core_evidence",
                        entry["kind"],
                        entry["reference"],
                        "no passing successor evidence",
                    )
                )
            findings.append(
                _finding(
                    "required_core_without_completion",
                    entry["kind"],
                    entry["reference"],
                    "passing successor or reviewed supersession is required",
                )
            )

    findings.sort(
        key=lambda finding: (
            finding["code"],
            finding["kind"],
            finding["reference"],
            finding["detail"],
        )
    )
    return {
        "schema": CORE_CLOSEOUT_REPORT_SCHEMA,
        "reference": manifest["reference"],
        "valid": not findings and incomplete_required_core == 0,
        "required_core_complete": incomplete_required_core == 0 and not findings,
        "counts": {
            "entries": len(manifest["entries"]),
            "required_core": required_core,
            "required_non_core": required_non_core,
            "deferred": len(deferred_entries),
            "completed_required_core": completed_required_core,
            "incomplete_required_core": incomplete_required_core,
            "passing_core_successors": passing_core_successors,
            "failed_core_successors": failed_core_successors,
            "reviewed_core_supersessions": reviewed_core_supersessions,
            "findings": len(findings),
        },
        "deferred_entries": sorted(
            deferred_entries,
            key=lambda entry: (entry["kind"], entry["reference"]),
        ),
        "incomplete_required_core_entries": sorted(
            incomplete_core_entries,
            key=lambda entry: (entry["kind"], entry["reference"]),
        ),
        "findings": findings,
    }


def read_bounded_json(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    if len(payload) > MAXIMUM_INPUT_BYTES:
        raise ValidationError(f"input exceeds byte bound: {path}")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValidationError(f"input must be a JSON object: {path}")
    return value


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def summary(report: dict[str, object]) -> str:
    counts = report["counts"]
    return (
        f"policy={report['policy']} valid={str(report['valid']).lower()} "
        f"migration_complete={str(report['migration_complete']).lower()} "
        f"entries={counts['entries']} required={counts['required']} "
        f"deferred={counts['deferred']} incomplete_required={counts['incomplete_required']} "
        f"findings={counts['findings']}"
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ownership", type=Path, required=True)
    parser.add_argument("--demos", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--policy", choices=[policy.value for policy in ValidationPolicy], required=True)
    arguments = parser.parse_args(argv)
    report = validate_parity(
        read_bounded_json(arguments.manifest),
        read_bounded_json(arguments.ownership),
        read_bounded_json(arguments.demos),
        read_bounded_json(arguments.evidence),
        policy=ValidationPolicy(arguments.policy),
    )
    write_report(arguments.report, report)
    print(summary(report))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
