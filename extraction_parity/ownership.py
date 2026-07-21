"""Closed future-ownership classification for frozen behavioral laws."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path


class OwnershipError(ValueError):
    pass


class OwnerKind(str, Enum):
    CORE = "core"
    HELLO = "hello"
    DEFERRED_PRODUCT = "deferred-product"
    SYSTEM = "system"


@dataclass(frozen=True)
class LawOwner:
    kind: OwnerKind
    owner: str


def classify_module(module: str, rules: dict[str, object]) -> LawOwner:
    hello = _string_set(rules, "hello")
    system = _string_set(rules, "system")
    deferred = rules.get("deferred_products")
    if not isinstance(deferred, dict):
        raise OwnershipError("deferred_products must be an object")
    matches = [
        product
        for product, modules in deferred.items()
        if isinstance(product, str) and module in _strings(modules, f"deferred product {product}")
    ]
    memberships = int(module in hello) + int(module in system) + len(matches)
    if memberships > 1:
        raise OwnershipError(f"module has multiple owners: {module}")
    if module in hello:
        return LawOwner(OwnerKind.HELLO, "control-plane-kit-servers:hello")
    if module in system:
        return LawOwner(OwnerKind.SYSTEM, "control-plane-kit-test:cross-repository")
    if matches:
        return LawOwner(
            OwnerKind.DEFERRED_PRODUCT, f"control-plane-kit-servers:{matches[0]}"
        )
    guarded = _string_set(rules, "guarded_product_terms")
    if any(term in module for term in guarded):
        raise OwnershipError(f"unlisted product-like module cannot default to core: {module}")
    return LawOwner(OwnerKind.CORE, "control-plane-kit-core")


def classify_inventory(
    inventory: dict[str, object], rules: dict[str, object]
) -> dict[str, object]:
    if inventory.get("schema") != "cpk.reference-test-inventory":
        raise OwnershipError("unsupported reference inventory")
    raw_tests = inventory.get("tests")
    if not isinstance(raw_tests, list):
        raise OwnershipError("reference tests must be a list")
    laws: list[dict[str, object]] = []
    for test in raw_tests:
        if not isinstance(test, dict):
            raise OwnershipError("reference test must be an object")
        reference = test.get("reference")
        if not isinstance(reference, str):
            raise OwnershipError("reference identity must be a string")
        parts = reference.split(".")
        if len(parts) < 4 or parts[0] != "tests":
            raise OwnershipError(f"invalid canonical reference: {reference}")
        owner = classify_module(parts[1], rules)
        laws.append(
            {
                "reference": reference,
                "law": test["law"],
                "collection_occurrences": test["collection_occurrences"],
                "owner_kind": owner.kind.value,
                "owner": owner.owner,
            }
        )
    counts = {
        kind.value: sum(entry["owner_kind"] == kind.value for entry in laws)
        for kind in OwnerKind
    }
    return {
        "schema": "cpk.reference-law-ownership",
        "reference": inventory["reference"],
        "count": inventory["count"],
        "law_count": len(laws),
        "owner_counts": counts,
        "laws": sorted(laws, key=lambda entry: str(entry["reference"])),
    }


def _strings(value: object, label: str) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise OwnershipError(f"{label} must be a string list")
    return frozenset(value)


def _string_set(rules: dict[str, object], name: str) -> frozenset[str]:
    return _strings(rules.get(name), name)


def write_ownership(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
