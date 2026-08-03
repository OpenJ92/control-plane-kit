"""Completion policy for reviewed semantic migration evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Iterable

from extraction_parity.manifest import decode_manifest
from extraction_parity.migration_inventory import decode_migration_inventory
from extraction_parity.reconciliation import (
    CURRENT_DISPOSITIONS,
    ReconciliationDisposition,
    decode_reconciliation,
)
from extraction_parity.validation import decode_evidence_index


class CompletionError(ValueError):
    """Raised when the semantic migration closeout is incomplete or stale."""


SCHEMA = "cpk.semantic-migration-closeout"
REPORT_SCHEMA = "cpk.semantic-migration-completion-report"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_TOP_LEVEL_FIELDS = {
    "schema",
    "issue",
    "reference",
    "input_digests",
    "additional_current_tests",
    "future_issues",
    "demo_reviews",
    "current_live_laws",
    "expected_counts",
}
_INPUT_DIGEST_FIELDS = {
    "manifest",
    "reconciliation",
    "inventory",
    "evidence",
    "aggregate",
}
_ADDITIONAL_TEST_FIELDS = {
    "id",
    "distribution",
    "path",
    "source_digest",
    "gate",
}
_FUTURE_ISSUE_FIELDS = {
    "repository",
    "number",
    "state",
    "url",
    "title",
    "content_digest",
    "law_references",
}
_DEMO_REVIEW_FIELDS = {
    "reference",
    "law",
    "reviewed_by_issue",
    "disposition",
    "current_evidence",
    "future_issue",
    "rationale",
    "negative_case_disposition",
    "obsolete_assumption_disposition",
}
_DEMO_FUTURE_FIELDS = {"repository", "number"}
_LIVE_LAW_FIELDS = {
    "distribution",
    "path",
    "classification",
    "owner",
    "law",
    "evidence",
}
_LIVE_CLASSIFICATIONS = {
    "authoritative-live",
    "diagnostic-live",
    "image-smoke",
    "package-gate",
    "publication-smoke",
    "publication-tool",
    "resource-audit",
}
_COUNT_FIELDS = {
    "manifest_entries",
    "test_reviews",
    "demo_reviews",
    "mutable_only_reviews",
    "current_test_identities",
    "additional_current_tests",
    "current_live_laws",
    "future_owned",
    "required_future_owned",
    "unowned",
    "stale_successors",
}


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompletionError(f"{label} must be non-blank text")
    if len(value.encode("utf-8")) > 2048:
        raise CompletionError(f"{label} exceeds the byte bound")
    return value


def _issue(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CompletionError(f"{label} must be a positive integer")
    return value


def _digest(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _digest_text(value: object, label: str) -> str:
    text = _text(value, label)
    if _DIGEST.fullmatch(text) is None:
        raise CompletionError(f"{label} must be a canonical SHA-256 digest")
    return text


def _strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CompletionError(f"{label} must be a list")
    strings = tuple(_text(item, label) for item in value)
    if len(strings) != len(set(strings)):
        raise CompletionError(f"{label} must be unique")
    return strings


def decode_semantic_closeout(document: dict[str, object]) -> dict[str, object]:
    if set(document) != _TOP_LEVEL_FIELDS or document.get("schema") != SCHEMA:
        raise CompletionError("semantic closeout has unknown or missing fields")
    if document["issue"] != 1326:
        raise CompletionError("semantic closeout must be owned by issue 1326")
    reference = document["reference"]
    if not isinstance(reference, dict) or set(reference) != {"tag", "commit"}:
        raise CompletionError("semantic closeout reference is not closed")
    _text(reference["tag"], "reference.tag")
    _text(reference["commit"], "reference.commit")

    input_digests = document["input_digests"]
    if not isinstance(input_digests, dict) or set(input_digests) != _INPUT_DIGEST_FIELDS:
        raise CompletionError("semantic closeout input digests are not closed")
    for name, value in input_digests.items():
        _digest_text(value, f"input_digests.{name}")

    additional_ids: set[str] = set()
    additional = document["additional_current_tests"]
    if not isinstance(additional, list):
        raise CompletionError("additional current tests must be a list")
    for value in additional:
        if not isinstance(value, dict) or set(value) != _ADDITIONAL_TEST_FIELDS:
            raise CompletionError("additional current test is not closed")
        identity = _text(value["id"], "additional_current_test.id")
        if identity in additional_ids:
            raise CompletionError("additional current test identities must be unique")
        additional_ids.add(identity)
        if value["distribution"] != "control-plane-kit-parity":
            raise CompletionError("only parity tests may be additional current tests")
        for field in ("path", "gate"):
            _text(value[field], f"additional_current_test.{field}")
        _digest_text(
            value["source_digest"], "additional_current_test.source_digest"
        )

    future_keys: set[tuple[str, int]] = set()
    future = document["future_issues"]
    if not isinstance(future, list):
        raise CompletionError("future issues must be a list")
    for value in future:
        if not isinstance(value, dict) or set(value) != _FUTURE_ISSUE_FIELDS:
            raise CompletionError("future issue snapshot is not closed")
        repository = _text(value["repository"], "future_issue.repository")
        number = _issue(value["number"], "future_issue.number")
        key = (repository, number)
        if key in future_keys:
            raise CompletionError("future issue snapshots must be unique")
        future_keys.add(key)
        if value["state"] != "open":
            raise CompletionError("future issue must be open")
        expected_url = f"https://github.com/{repository}/issues/{number}"
        if value["url"] != expected_url:
            raise CompletionError("future issue URL differs from its identity")
        _text(value["title"], "future_issue.title")
        _digest_text(value["content_digest"], "future_issue.content_digest")
        _strings(value["law_references"], "future_issue.law_references")

    demo_references: set[str] = set()
    demos = document["demo_reviews"]
    if not isinstance(demos, list):
        raise CompletionError("demo reviews must be a list")
    for value in demos:
        if not isinstance(value, dict) or set(value) != _DEMO_REVIEW_FIELDS:
            raise CompletionError("demo review is not closed")
        reference_id = _text(value["reference"], "demo_review.reference")
        if reference_id in demo_references:
            raise CompletionError("demo review references must be unique")
        demo_references.add(reference_id)
        _text(value["law"], "demo_review.law")
        _issue(value["reviewed_by_issue"], "demo_review.reviewed_by_issue")
        disposition = value["disposition"]
        if disposition not in {
            ReconciliationDisposition.CURRENT_STRENGTHENED.value,
            ReconciliationDisposition.REVIEWED_SUPERSESSION.value,
            ReconciliationDisposition.FUTURE_ISSUE.value,
        }:
            raise CompletionError("demo review disposition is invalid")
        current_evidence = _strings(
            value["current_evidence"], "demo_review.current_evidence"
        )
        future_issue = value["future_issue"]
        if disposition == ReconciliationDisposition.CURRENT_STRENGTHENED.value:
            if not current_evidence or future_issue is not None:
                raise CompletionError("current demo review must name only current evidence")
        elif disposition == ReconciliationDisposition.FUTURE_ISSUE.value:
            if current_evidence:
                raise CompletionError("future demo review cannot name current evidence")
            if not isinstance(future_issue, dict) or set(future_issue) != _DEMO_FUTURE_FIELDS:
                raise CompletionError("future demo issue reference is not closed")
            _text(future_issue["repository"], "demo_review.future_issue.repository")
            _issue(future_issue["number"], "demo_review.future_issue.number")
        elif current_evidence or future_issue is not None:
            raise CompletionError("superseded demo cannot name current or future evidence")
        for field in (
            "rationale",
            "negative_case_disposition",
            "obsolete_assumption_disposition",
        ):
            _text(value[field], f"demo_review.{field}")

    live_keys: set[tuple[str, str]] = set()
    live = document["current_live_laws"]
    if not isinstance(live, list):
        raise CompletionError("current live laws must be a list")
    for value in live:
        if not isinstance(value, dict) or set(value) != _LIVE_LAW_FIELDS:
            raise CompletionError("current live law is not closed")
        key = (
            _text(value["distribution"], "current_live_law.distribution"),
            _text(value["path"], "current_live_law.path"),
        )
        if key in live_keys:
            raise CompletionError("current live law identities must be unique")
        live_keys.add(key)
        if value["classification"] not in _LIVE_CLASSIFICATIONS:
            raise CompletionError("current live law classification is invalid")
        for field in ("owner", "law", "evidence"):
            _text(value[field], f"current_live_law.{field}")

    counts = document["expected_counts"]
    if not isinstance(counts, dict) or set(counts) != _COUNT_FIELDS:
        raise CompletionError("expected counts are not closed")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in counts.values()
    ):
        raise CompletionError("expected counts must be non-negative integers")
    return document


def _assert_passing_successors(
    entry: dict[str, object],
    expected_ids: set[str],
    evidence: dict[str, dict[str, object]],
) -> None:
    actual_ids = {str(value["id"]) for value in entry["successors"]}
    if actual_ids != expected_ids:
        raise CompletionError(f"manifest successors differ: {entry['reference']}")
    for successor in entry["successors"]:
        proof = evidence.get(str(successor["evidence"]))
        if proof is None or proof["status"] != "passing" or successor["status"] != "passing":
            raise CompletionError(f"successor evidence is not passing: {entry['reference']}")


def validate_semantic_completion(
    manifest: dict[str, object],
    reconciliation: dict[str, object],
    inventory: dict[str, object],
    evidence_index: dict[str, object],
    aggregate: dict[str, object],
    closeout: dict[str, object],
) -> dict[str, object]:
    decode_manifest(manifest)
    decode_reconciliation(reconciliation)
    decode_migration_inventory(inventory)
    decode_evidence_index(evidence_index)
    decode_semantic_closeout(closeout)

    documents = {
        "manifest": manifest,
        "reconciliation": reconciliation,
        "inventory": inventory,
        "evidence": evidence_index,
        "aggregate": aggregate,
    }
    for name, document in documents.items():
        if closeout["input_digests"][name] != _digest(document):
            raise CompletionError(f"{name} digest differs from closeout evidence")
    if (
        closeout["reference"] != manifest["reference"]
        or inventory["reference"] != manifest["reference"]
    ):
        raise CompletionError("reference identities differ")
    if inventory["parity_manifest_digest"] != _digest(manifest):
        raise CompletionError("inventory manifest digest differs")
    if (
        aggregate.get("schema")
        != "cpk.harden-tests-parity.cross-repository-aggregate"
        or aggregate.get("issue") != 1348
    ):
        raise CompletionError("cross-repository aggregate identity differs")
    package_evidence = aggregate.get("package_gate_evidence")
    if not isinstance(package_evidence, dict):
        raise CompletionError("cross-repository package evidence is missing")
    current_sources = {
        str(value["distribution"]): value
        for value in inventory["sources"]
        if value["distribution"]
        in {
            "control-plane-kit-core",
            "control-plane-kit-operations",
            "control-plane-kit-interpreters",
            "control-plane-kit-servers",
            "control-plane-kit-secrets",
        }
    }
    if set(current_sources) != set(package_evidence):
        raise CompletionError("current source coordinates differ from package evidence")
    for distribution, package in package_evidence.items():
        source = current_sources[distribution]
        if source["method_count"] != package.get("tests"):
            raise CompletionError(f"current test count differs: {distribution}")
        package_commit = package.get("commit")
        if package_commit is not None and source["commit"] != package_commit:
            raise CompletionError(f"current source commit differs: {distribution}")

    entries = {str(value["reference"]): value for value in manifest["entries"]}
    test_entries = {
        reference: value for reference, value in entries.items() if value["kind"] == "test"
    }
    demo_entries = {
        reference: value for reference, value in entries.items() if value["kind"] == "demo"
    }
    reviews = {str(value["reference"]): value for value in reconciliation["reviews"]}
    if set(reviews) != set(test_entries):
        raise CompletionError("test reviews differ from manifest tests")
    demo_reviews = {str(value["reference"]): value for value in closeout["demo_reviews"]}
    if set(demo_reviews) != set(demo_entries):
        raise CompletionError("demo reviews differ from manifest demos")

    current_test_ids = {str(value["id"]) for value in inventory["current_tests"]}
    additional_test_ids = {str(value["id"]) for value in closeout["additional_current_tests"]}
    if current_test_ids.intersection(additional_test_ids):
        raise CompletionError("additional current tests duplicate inventory tests")
    current_test_ids.update(additional_test_ids)
    proof_by_id = {str(value["id"]): value for value in evidence_index["evidence"]}
    future_snapshots = {
        (str(value["repository"]), int(value["number"])): value
        for value in closeout["future_issues"]
    }
    assigned_future: dict[tuple[str, int], set[str]] = {
        key: set() for key in future_snapshots
    }
    future_owned: set[str] = set()
    required_future_owned: set[str] = set()

    for reference, review in reviews.items():
        entry = test_entries[reference]
        if review["law"] != entry["law"]:
            raise CompletionError(f"review law differs: {reference}")
        disposition = str(review["disposition"])
        if disposition in CURRENT_DISPOSITIONS:
            missing = set(review["current_tests"]) - current_test_ids
            if missing:
                raise CompletionError(
                    f"nonexistent current test for {reference}: {sorted(missing)}"
                )
            _assert_passing_successors(entry, set(review["current_tests"]), proof_by_id)
        elif disposition == ReconciliationDisposition.FUTURE_ISSUE.value:
            future = review["future_issue"]
            key = (str(future["repository"]), int(future["number"]))
            if key not in future_snapshots:
                raise CompletionError(f"missing future issue snapshot: {reference}")
            assigned_future[key].add(reference)
            future_owned.add(reference)
            if entry["migration_state"] == "required":
                required_future_owned.add(reference)
            if entry["successors"] or entry["supersession"] is not None:
                raise CompletionError(f"future-owned law retains completion evidence: {reference}")
        elif entry["supersession"] is None:
            raise CompletionError(f"unreviewed supersession: {reference}")

    for reference, review in demo_reviews.items():
        entry = demo_entries[reference]
        if review["law"] != entry["law"]:
            raise CompletionError(f"demo review law differs: {reference}")
        disposition = str(review["disposition"])
        if disposition == ReconciliationDisposition.CURRENT_STRENGTHENED.value:
            _assert_passing_successors(
                entry, set(review["current_evidence"]), proof_by_id
            )
        elif disposition == ReconciliationDisposition.FUTURE_ISSUE.value:
            future = review["future_issue"]
            key = (str(future["repository"]), int(future["number"]))
            if key not in future_snapshots:
                raise CompletionError(f"missing future issue snapshot: {reference}")
            assigned_future[key].add(reference)
            future_owned.add(reference)
            if entry["migration_state"] == "required":
                required_future_owned.add(reference)
            if entry["successors"] or entry["supersession"] is not None:
                raise CompletionError(f"future-owned demo retains completion evidence: {reference}")
        elif entry["supersession"] is None:
            raise CompletionError(f"unreviewed demo supersession: {reference}")

    for review in reconciliation["mutable_only_reviews"]:
        missing = set(review["current_tests"]) - current_test_ids
        if missing:
            raise CompletionError(
                f"nonexistent current test for {review['source_id']}: {sorted(missing)}"
            )

    for key, snapshot in future_snapshots.items():
        if set(snapshot["law_references"]) != assigned_future[key]:
            raise CompletionError(
                f"future issue laws differ: {key[0]}#{key[1]}"
            )

    expected_live = {
        (str(value["distribution"]), str(value["path"]))
        for value in inventory["current_scripts"]
    }
    actual_live = {
        (str(value["distribution"]), str(value["path"]))
        for value in closeout["current_live_laws"]
    }
    if actual_live != expected_live:
        raise CompletionError("current live laws differ from current script inventory")

    counts = {
        "manifest_entries": len(entries),
        "test_reviews": len(reviews),
        "demo_reviews": len(demo_reviews),
        "mutable_only_reviews": len(reconciliation["mutable_only_reviews"]),
        "current_test_identities": len(current_test_ids),
        "additional_current_tests": len(additional_test_ids),
        "current_live_laws": len(actual_live),
        "future_owned": len(future_owned),
        "required_future_owned": len(required_future_owned),
        "unowned": 0,
        "stale_successors": 0,
    }
    if closeout["expected_counts"] != counts:
        raise CompletionError("expected counts differ from exact semantic evidence")
    return {
        "schema": REPORT_SCHEMA,
        "valid": True,
        "zero_unowned": True,
        "counts": {
            **counts,
            "implemented_or_superseded": len(entries) - len(future_owned),
            "required_implemented_or_superseded": sum(
                value["migration_state"] == "required"
                for value in entries.values()
            )
            - len(required_future_owned),
        },
    }


def _read(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompletionError(f"cannot read completion input: {path.name}") from error
    if not isinstance(value, dict):
        raise CompletionError(f"completion input must be an object: {path.name}")
    return value


def _write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--reconciliation", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--aggregate", type=Path, required=True)
    parser.add_argument("--closeout", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args(argv)
    report = validate_semantic_completion(
        _read(arguments.manifest),
        _read(arguments.reconciliation),
        _read(arguments.inventory),
        _read(arguments.evidence),
        _read(arguments.aggregate),
        _read(arguments.closeout),
    )
    _write(arguments.report, report)
    counts = report["counts"]
    print(
        "valid=true zero_unowned=true "
        f"entries={counts['manifest_entries']} "
        f"future_owned={counts['future_owned']} "
        f"required_future_owned={counts['required_future_owned']} "
        f"current_tests={counts['current_test_identities']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
