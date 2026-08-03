"""Build reviewed semantic reconciliation slices from explicit decisions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from extraction_parity.manifest import decode_manifest, write_manifest
from extraction_parity.migration_inventory import SourceLane, scan_test_root
from extraction_parity.reconciliation import (
    CURRENT_DISPOSITIONS,
    ReconciliationError,
    decode_reconciliation,
    validate_reconciliation,
)


DECISIONS_SCHEMA = "cpk.semantic-test-reconciliation-decisions"
DECISION_FIELDS = {
    "issue",
    "current_distributions",
    "evidence_id",
    "default_disposition",
    "strengthened_references",
    "successor_overrides",
    "future_issue_reviews",
    "non_current_reviews",
}


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReconciliationError(f"JSON root must be an object: {path}")
    return value


def _write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _decode_decisions(document: dict[str, object]) -> dict[str, object]:
    if set(document) != {"schema", "slices"}:
        raise ReconciliationError("reconciliation decisions root is not closed")
    if document["schema"] != DECISIONS_SCHEMA:
        raise ReconciliationError("unsupported reconciliation decisions schema")
    slices = document["slices"]
    if not isinstance(slices, list):
        raise ReconciliationError("reconciliation decision slices must be a list")
    issues: set[int] = set()
    for value in slices:
        if not isinstance(value, dict) or set(value) != DECISION_FIELDS:
            raise ReconciliationError("reconciliation decision slice is not closed")
        issue = value["issue"]
        if not isinstance(issue, int) or issue <= 0 or issue in issues:
            raise ReconciliationError("decision slice issue must be unique and positive")
        issues.add(issue)
        distributions = value["current_distributions"]
        if (
            not isinstance(distributions, list)
            or not distributions
            or any(
                not isinstance(distribution, str) or not distribution.strip()
                for distribution in distributions
            )
            or len(distributions) != len(set(distributions))
        ):
            raise ReconciliationError(
                "current distributions must be a non-empty unique text list"
            )
        if value["default_disposition"] not in CURRENT_DISPOSITIONS:
            raise ReconciliationError("default disposition must be current")
        if not isinstance(value["strengthened_references"], list):
            raise ReconciliationError("strengthened references must be a list")
        if not isinstance(value["successor_overrides"], dict):
            raise ReconciliationError("successor overrides must be an object")
        if not isinstance(value["future_issue_reviews"], dict):
            raise ReconciliationError("future issue reviews must be an object")
        non_current = value["non_current_reviews"]
        if not isinstance(non_current, dict):
            raise ReconciliationError("non-current reviews must be an object")
        for reference, review in non_current.items():
            if not isinstance(reference, str) or not reference.strip():
                raise ReconciliationError("non-current review reference must be text")
            if not isinstance(review, dict) or set(review) != {
                "disposition",
                "owner",
                "rationale",
                "negative_case_disposition",
                "obsolete_assumption_disposition",
            }:
                raise ReconciliationError("non-current review is not closed")
            if review["disposition"] not in {
                "reviewed-supersession",
                "archived-obsolete",
            }:
                raise ReconciliationError("non-current review disposition is invalid")
            for field in (
                "owner",
                "rationale",
                "negative_case_disposition",
                "obsolete_assumption_disposition",
            ):
                if not isinstance(review[field], str) or not review[field].strip():
                    raise ReconciliationError(
                        f"non-current review {field} must be text"
                    )
    return document


def _method_name(reference: str) -> str:
    return reference.rsplit(".", maxsplit=1)[-1]


def _canonical_current_id(value: str) -> str:
    for distribution in (
        "control-plane-kit-core",
        "control-plane-kit-operations",
        "control-plane-kit-interpreters",
        "control-plane-kit-servers",
        "control-plane-kit-secrets",
    ):
        prefix = f"{distribution}.tests."
        if value.startswith(prefix):
            return f"{distribution}:tests.{value.removeprefix(prefix)}"
    return value


def _negative_case_disposition(assignment: dict[str, object]) -> str:
    hints = assignment.get("source", {}).get("negative_case_hints", [])
    if hints:
        return "Current evidence preserves the frozen negative cases: " + ", ".join(
            str(value) for value in hints
        )
    return "The frozen law has no separate negative-case assertion; its exact observable remains covered."


def _current_test_index(
    root: Path,
    distributions: tuple[str, ...],
) -> tuple[set[str], dict[str, list[str]]]:
    test_roots = {
        "control-plane-kit-core": ("control-plane-kit-core/tests",),
        "control-plane-kit-operations": ("control-plane-kit-operations/tests",),
    }
    methods: list[dict[str, object]] = []
    for distribution in distributions:
        try:
            selected_test_roots = test_roots[distribution]
        except KeyError as error:
            raise ReconciliationError(
                f"working-tree scanner has no configured test root for {distribution}"
            ) from error
        lane = scan_test_root(
            SourceLane(
                distribution=distribution,
                repository="working-tree",
                commit="working-tree",
                gate="focused-unittest",
                root=root,
                test_roots=selected_test_roots,
            )
        )
        methods.extend(lane["methods"])
    identities = {str(value["id"]) for value in methods}
    by_method: dict[str, list[str]] = {}
    for value in methods:
        by_method.setdefault(str(value["method"]), []).append(str(value["id"]))
    return identities, by_method


def _successors_for(
    *,
    reference: str,
    manifest_entry: dict[str, object],
    overrides: dict[str, object],
    current_ids: set[str],
    current_by_method: dict[str, list[str]],
) -> tuple[str, ...]:
    override = overrides.get(reference)
    if override is not None:
        if not isinstance(override, list) or not override:
            raise ReconciliationError(f"successor override must be non-empty: {reference}")
        successors = tuple(str(value) for value in override)
    else:
        recorded = tuple(
            _canonical_current_id(str(value["id"]))
            for value in manifest_entry.get("successors", [])
        )
        if recorded and all(value in current_ids for value in recorded):
            successors = recorded
        else:
            candidates = current_by_method.get(_method_name(reference), [])
            if len(candidates) != 1:
                raise ReconciliationError(
                    f"current successor is ambiguous or missing for {reference}: {candidates}"
                )
            successors = (candidates[0],)
    missing = sorted(set(successors) - current_ids)
    if missing:
        raise ReconciliationError(
            f"successor override names nonexistent current tests for {reference}: {missing}"
        )
    return successors


def apply_issue_slice(
    *,
    root: Path,
    issue: int,
    inventory: dict[str, object],
    manifest: dict[str, object],
    reconciliation: dict[str, object],
    decisions: dict[str, object],
) -> tuple[dict[str, object], dict[str, object], frozenset[str]]:
    _decode_decisions(decisions)
    decision = next(
        (value for value in decisions["slices"] if value["issue"] == issue),
        None,
    )
    if decision is None:
        raise ReconciliationError(f"missing decision slice for issue #{issue}")
    distributions = tuple(str(value) for value in decision["current_distributions"])
    current_ids, current_by_method = _current_test_index(root, distributions)
    manifest_entries = {
        str(value["reference"]): value
        for value in manifest["entries"]
        if value["kind"] == "test"
    }
    assignments = [
        value
        for value in inventory["reference_assignments"]
        if value["provisional_target"]["issue"] == issue
    ]
    future = decision["future_issue_reviews"]
    non_current = decision["non_current_reviews"]
    strengthened = set(decision["strengthened_references"])
    overrides = decision["successor_overrides"]
    reviews: list[dict[str, object]] = []
    for assignment in assignments:
        reference = str(assignment["reference"])
        manifest_entry = manifest_entries[reference]
        non_current_review = non_current.get(reference)
        if non_current_review is not None:
            manifest_entry["successors"] = []
            manifest_entry["supersession"] = {
                "rationale": non_current_review["rationale"],
                "review": f"HARDEN.TESTS.PARITY #{issue}",
                "obsolete_assumption": non_current_review[
                    "obsolete_assumption_disposition"
                ],
                "replacement": non_current_review["owner"],
                "negative_case_disposition": non_current_review[
                    "negative_case_disposition"
                ],
            }
            reviews.append(
                {
                    "reference": reference,
                    "law": assignment["law"],
                    "reviewed_by_issue": issue,
                    "owner": non_current_review["owner"],
                    "disposition": non_current_review["disposition"],
                    "current_tests": [],
                    "future_issue": None,
                    "rationale": non_current_review["rationale"],
                    "negative_case_disposition": non_current_review[
                        "negative_case_disposition"
                    ],
                    "obsolete_assumption_disposition": non_current_review[
                        "obsolete_assumption_disposition"
                    ],
                }
            )
            continue
        future_issue = future.get(reference)
        if future_issue is not None:
            manifest_entry["successors"] = []
            manifest_entry["supersession"] = None
            reviews.append(
                {
                    "reference": reference,
                    "law": assignment["law"],
                    "reviewed_by_issue": issue,
                    "owner": f"{future_issue['repository']}#{future_issue['number']}",
                    "disposition": "future-issue",
                    "current_tests": [],
                    "future_issue": {
                        **future_issue,
                        "state": "open",
                    },
                    "rationale": "The behavioral law remains desired, but its application-level owner is deliberately outside this current package slice.",
                    "negative_case_disposition": _negative_case_disposition(assignment),
                    "obsolete_assumption_disposition": "The frozen application module and aggregate import path are not current package structure.",
                }
            )
            continue
        successors = _successors_for(
            reference=reference,
            manifest_entry=manifest_entry,
            overrides=overrides,
            current_ids=current_ids,
            current_by_method=current_by_method,
        )
        manifest_entry["successors"] = [
            {
                "id": value,
                "status": "passing",
                "evidence": decision["evidence_id"],
            }
            for value in successors
        ]
        manifest_entry["supersession"] = None
        disposition = (
            "current-strengthened"
            if reference in strengthened
            else str(decision["default_disposition"])
        )
        reviews.append(
            {
                "reference": reference,
                "law": assignment["law"],
                "reviewed_by_issue": issue,
                "owner": "+".join(
                    sorted(
                        {
                            value.split(":", maxsplit=1)[0]
                            for value in successors
                        }
                    )
                ),
                "disposition": disposition,
                "current_tests": list(successors),
                "future_issue": None,
                "rationale": (
                    "The current topology compiler preserves and strengthens the old recipe-level observable."
                    if disposition == "current-strengthened"
                    else "The current package test preserves the frozen behavioral law without its obsolete aggregate imports or fixtures."
                ),
                "negative_case_disposition": _negative_case_disposition(assignment),
                "obsolete_assumption_disposition": (
                    "Recipe-specific constructors are replaced by the provider-neutral topology compiler."
                    if disposition == "current-strengthened"
                    else "Frozen aggregate imports, fixtures, and constructor layout do not constrain the current package test."
                ),
            }
        )

    mutable_reviews: list[dict[str, object]] = []
    for assignment in inventory["mutable_only_methods"]:
        if assignment["provisional_target"]["issue"] != issue:
            continue
        candidates = current_by_method.get(str(assignment["method"]), [])
        if len(candidates) != 1:
            raise ReconciliationError(
                f"mutable-only successor is ambiguous or missing: {assignment['id']}"
            )
        mutable_reviews.append(
            {
                "source_id": assignment["id"],
                "reviewed_by_issue": issue,
                "distribution": candidates[0].split(":", maxsplit=1)[0],
                "disposition": "current-strengthened",
                "current_tests": candidates,
                "rationale": "The live provider-neutral authentication contract retains this mutable-only hardening law.",
            }
        )

    retained_reviews = [
        value
        for value in reconciliation.get("reviews", [])
        if value["reviewed_by_issue"] != issue
    ]
    retained_mutable = [
        value
        for value in reconciliation.get("mutable_only_reviews", [])
        if value["reviewed_by_issue"] != issue
    ]
    updated_reconciliation = {
        "schema": "cpk.semantic-test-reconciliation",
        "reviews": sorted(
            [*retained_reviews, *reviews], key=lambda value: str(value["reference"])
        ),
        "mutable_only_reviews": sorted(
            [*retained_mutable, *mutable_reviews],
            key=lambda value: str(value["source_id"]),
        ),
    }
    decode_manifest(manifest)
    decode_reconciliation(updated_reconciliation)
    validate_reconciliation(
        updated_reconciliation,
        inventory,
        manifest,
        current_test_ids=frozenset(current_ids),
        issue=issue,
    )
    return manifest, updated_reconciliation, frozenset(current_ids)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--evidence-digest")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    inventory_path = root / "artifacts/extraction/semantic-test-migration-inventory.json"
    manifest_path = root / "artifacts/extraction/parity-manifest.json"
    reconciliation_path = root / "artifacts/extraction/semantic-test-reconciliation.json"
    decisions_path = root / "artifacts/extraction/semantic-test-reconciliation-decisions.json"
    evidence_path = root / "artifacts/extraction/successor-evidence.json"
    inventory = _load(inventory_path)
    manifest = _load(manifest_path)
    reconciliation = (
        _load(reconciliation_path)
        if reconciliation_path.exists()
        else {
            "schema": "cpk.semantic-test-reconciliation",
            "reviews": [],
            "mutable_only_reviews": [],
        }
    )
    decisions = _load(decisions_path)
    updated_manifest, updated_reconciliation, _ = apply_issue_slice(
        root=root,
        issue=args.issue,
        inventory=inventory,
        manifest=manifest,
        reconciliation=reconciliation,
        decisions=decisions,
    )
    if args.evidence_digest is not None:
        evidence = _load(evidence_path)
        digest = args.evidence_digest
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise ReconciliationError("evidence digest must be canonical SHA-256")
        decision = next(
            value for value in decisions["slices"] if value["issue"] == args.issue
        )
        evidence["evidence"] = [
            value
            for value in evidence["evidence"]
            if value["id"] != decision["evidence_id"]
        ] + [
            {
                "id": decision["evidence_id"],
                "status": "passing",
                "digest": digest,
            }
        ]
        evidence["evidence"].sort(key=lambda value: str(value["id"]))
    else:
        evidence = None

    expected = {
        manifest_path: updated_manifest,
        reconciliation_path: updated_reconciliation,
    }
    if evidence is not None:
        expected[evidence_path] = evidence
    if args.check:
        mismatches = [path for path, value in expected.items() if _load(path) != value]
        if mismatches:
            raise ReconciliationError(
                "generated reconciliation files differ: "
                + ", ".join(str(path.relative_to(root)) for path in mismatches)
            )
    else:
        write_manifest(manifest_path, updated_manifest)
        _write(reconciliation_path, updated_reconciliation)
        if evidence is not None:
            _write(evidence_path, evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
