"""Reviewed semantic dispositions layered over the frozen parity manifest."""

from __future__ import annotations

from enum import StrEnum


class ReconciliationError(ValueError):
    """Raised when reviewed migration evidence is incomplete or contradictory."""


SCHEMA = "cpk.semantic-test-reconciliation"
MAXIMUM_TEXT_BYTES = 1024


class ReconciliationDisposition(StrEnum):
    CURRENT_ISOMORPHIC = "current-isomorphic"
    CURRENT_STRENGTHENED = "current-strengthened"
    REVIEWED_SUPERSESSION = "reviewed-supersession"
    FUTURE_ISSUE = "future-issue"
    ARCHIVED_OBSOLETE = "archived-obsolete"


CURRENT_DISPOSITIONS = frozenset(
    {
        ReconciliationDisposition.CURRENT_ISOMORPHIC.value,
        ReconciliationDisposition.CURRENT_STRENGTHENED.value,
    }
)
REVIEW_FIELDS = {
    "reference",
    "law",
    "reviewed_by_issue",
    "owner",
    "disposition",
    "current_tests",
    "future_issue",
    "rationale",
    "negative_case_disposition",
    "obsolete_assumption_disposition",
}
FUTURE_ISSUE_FIELDS = {"repository", "number", "state", "evidence"}
MUTABLE_REVIEW_FIELDS = {
    "source_id",
    "reviewed_by_issue",
    "distribution",
    "disposition",
    "current_tests",
    "rationale",
}


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationError(f"{label} must be non-blank text")
    if len(value.encode("utf-8")) > MAXIMUM_TEXT_BYTES:
        raise ReconciliationError(f"{label} exceeds the byte bound")
    return value


def _issue(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ReconciliationError(f"{label} must be a positive integer")
    return value


def _test_ids(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ReconciliationError(f"{label} must be a list")
    tests = tuple(_text(item, label) for item in value)
    if len(tests) != len(set(tests)):
        raise ReconciliationError(f"{label} must not contain duplicates")
    return tests


def _decode_future_issue(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != FUTURE_ISSUE_FIELDS:
        raise ReconciliationError("future issue record is not closed")
    _text(value["repository"], "future_issue.repository")
    _issue(value["number"], "future_issue.number")
    if value["state"] != "open":
        raise ReconciliationError("future issue must be open")
    _text(value["evidence"], "future_issue.evidence")
    return value


def _decode_review(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != REVIEW_FIELDS:
        raise ReconciliationError("semantic review is not closed")
    for field in (
        "reference",
        "law",
        "owner",
        "rationale",
        "negative_case_disposition",
        "obsolete_assumption_disposition",
    ):
        _text(value[field], f"review.{field}")
    _issue(value["reviewed_by_issue"], "review.reviewed_by_issue")
    try:
        disposition = ReconciliationDisposition(value["disposition"])
    except (TypeError, ValueError) as error:
        raise ReconciliationError("unknown semantic review disposition") from error
    tests = _test_ids(value["current_tests"], "review.current_tests")
    future_issue = value["future_issue"]
    if disposition.value in CURRENT_DISPOSITIONS:
        if not tests:
            raise ReconciliationError("current disposition must name current tests")
        if future_issue is not None:
            raise ReconciliationError("current disposition cannot name a future issue")
    elif disposition is ReconciliationDisposition.FUTURE_ISSUE:
        if tests:
            raise ReconciliationError("future issue disposition cannot name current tests")
        _decode_future_issue(future_issue)
    else:
        if tests or future_issue is not None:
            raise ReconciliationError(
                "reviewed non-current disposition cannot name tests or a future issue"
            )
    return value


def _decode_mutable_review(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != MUTABLE_REVIEW_FIELDS:
        raise ReconciliationError("mutable-only review is not closed")
    _text(value["source_id"], "mutable_review.source_id")
    _text(value["distribution"], "mutable_review.distribution")
    _text(value["rationale"], "mutable_review.rationale")
    _issue(value["reviewed_by_issue"], "mutable_review.reviewed_by_issue")
    if value["disposition"] not in {
        "current-new-law",
        "current-strengthened",
        "reviewed-archived",
    }:
        raise ReconciliationError("unknown mutable-only review disposition")
    tests = _test_ids(value["current_tests"], "mutable_review.current_tests")
    if value["disposition"] != "reviewed-archived" and not tests:
        raise ReconciliationError("current mutable-only review must name tests")
    if value["disposition"] == "reviewed-archived" and tests:
        raise ReconciliationError("archived mutable-only review cannot name tests")
    return value


def decode_reconciliation(document: dict[str, object]) -> dict[str, object]:
    if set(document) != {"schema", "reviews", "mutable_only_reviews"}:
        raise ReconciliationError(
            "semantic reconciliation has unknown or missing root fields"
        )
    if document["schema"] != SCHEMA:
        raise ReconciliationError("unsupported semantic reconciliation schema")
    reviews = document["reviews"]
    mutable_reviews = document["mutable_only_reviews"]
    if not isinstance(reviews, list) or not isinstance(mutable_reviews, list):
        raise ReconciliationError("semantic reconciliation reviews must be lists")
    references: set[str] = set()
    for raw_review in reviews:
        review = _decode_review(raw_review)
        reference = str(review["reference"])
        if reference in references:
            raise ReconciliationError("duplicate review reference")
        references.add(reference)
    sources: set[str] = set()
    for raw_review in mutable_reviews:
        review = _decode_mutable_review(raw_review)
        source_id = str(review["source_id"])
        if source_id in sources:
            raise ReconciliationError("duplicate mutable-only review source")
        sources.add(source_id)
    return document


def validate_reconciliation(
    document: dict[str, object],
    inventory: dict[str, object],
    manifest: dict[str, object],
    *,
    current_test_ids: frozenset[str],
    issue: int,
) -> dict[str, object]:
    decoded = decode_reconciliation(document)
    _issue(issue, "issue")
    if inventory.get("schema") != "cpk.semantic-test-migration-inventory":
        raise ReconciliationError("unsupported semantic migration inventory")
    if manifest.get("schema") != "cpk.parity-manifest":
        raise ReconciliationError("unsupported parity manifest")

    assignments = {
        str(entry["reference"]): entry
        for entry in inventory.get("reference_assignments", [])
        if entry.get("provisional_target", {}).get("issue") == issue
    }
    reviews = {
        str(entry["reference"]): entry
        for entry in decoded["reviews"]
        if entry["reviewed_by_issue"] == issue
    }
    missing = sorted(set(assignments) - set(reviews))
    unexpected = sorted(set(reviews) - set(assignments))
    if missing:
        raise ReconciliationError(f"missing assigned reviews: {missing}")
    if unexpected:
        raise ReconciliationError(f"issue reviews are not assigned here: {unexpected}")

    manifest_entries = {
        str(entry["reference"]): entry
        for entry in manifest.get("entries", [])
        if entry.get("kind") == "test"
    }
    for reference, review in reviews.items():
        assignment = assignments[reference]
        manifest_entry = manifest_entries.get(reference)
        if manifest_entry is None:
            raise ReconciliationError(f"review is absent from parity manifest: {reference}")
        expected_law = str(assignment["law"])
        if review["law"] != expected_law or manifest_entry.get("law") != expected_law:
            raise ReconciliationError(f"review law differs from inventory: {reference}")
        tests = tuple(str(value) for value in review["current_tests"])
        for test_id in tests:
            if test_id not in current_test_ids:
                raise ReconciliationError(
                    f"nonexistent current test {test_id!r} for {reference}"
                )
        disposition = str(review["disposition"])
        manifest_successors = tuple(
            str(value["id"]) for value in manifest_entry.get("successors", [])
        )
        if disposition in CURRENT_DISPOSITIONS:
            if set(manifest_successors) != set(tests):
                raise ReconciliationError(
                    f"manifest successors differ from reviewed current tests: {reference}"
                )
            if manifest_entry.get("supersession") is not None:
                raise ReconciliationError(
                    f"current review retains a manifest supersession: {reference}"
                )
        elif disposition == ReconciliationDisposition.FUTURE_ISSUE.value:
            if manifest_successors or manifest_entry.get("supersession") is not None:
                raise ReconciliationError(
                    f"future issue review cannot retain completion evidence: {reference}"
                )
        elif manifest_entry.get("supersession") is None:
            raise ReconciliationError(
                f"reviewed non-current law lacks manifest supersession: {reference}"
            )

    mutable_assignments = {
        str(entry["id"]): entry
        for entry in inventory.get("mutable_only_methods", [])
        if entry.get("provisional_target", {}).get("issue") == issue
    }
    mutable_reviews = {
        str(entry["source_id"]): entry
        for entry in decoded["mutable_only_reviews"]
        if entry["reviewed_by_issue"] == issue
    }
    if set(mutable_assignments) != set(mutable_reviews):
        raise ReconciliationError("mutable-only reviews differ from assigned inputs")
    for source_id, review in mutable_reviews.items():
        expected_distribution = mutable_assignments[source_id]["provisional_target"][
            "distribution"
        ]
        if review["distribution"] != expected_distribution:
            raise ReconciliationError(
                f"mutable-only review distribution differs: {source_id}"
            )
        for test_id in review["current_tests"]:
            if test_id not in current_test_ids:
                raise ReconciliationError(
                    f"nonexistent current test {test_id!r} for {source_id}"
                )

    return {
        "schema": "cpk.semantic-test-reconciliation-validation",
        "issue": issue,
        "valid": True,
        "counts": {
            "reviews": len(reviews),
            "mutable_only_reviews": len(mutable_reviews),
            "current": sum(
                review["disposition"] in CURRENT_DISPOSITIONS
                for review in reviews.values()
            ),
            "future_issue": sum(
                review["disposition"]
                == ReconciliationDisposition.FUTURE_ISSUE.value
                for review in reviews.values()
            ),
            "reviewed_non_current": sum(
                review["disposition"]
                not in CURRENT_DISPOSITIONS
                and review["disposition"]
                != ReconciliationDisposition.FUTURE_ISSUE.value
                for review in reviews.values()
            ),
        },
    }
