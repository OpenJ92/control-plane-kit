"""Validation for frozen live-demonstration contracts."""

from __future__ import annotations


class DemoInventoryError(ValueError):
    pass


def validate_demo_inventory(
    document: dict[str, object], *, discovered_scripts: frozenset[str], discovered_fixtures: frozenset[str] = frozenset()
) -> None:
    if set(document) != {"schema", "reference", "demos"}:
        raise DemoInventoryError("demo inventory has unknown or missing root fields")
    if document["schema"] != "cpk.reference-demo-inventory":
        raise DemoInventoryError("unsupported demo inventory schema")
    demos = document["demos"]
    if not isinstance(demos, list) or not demos:
        raise DemoInventoryError("demo inventory must be a non-empty list")
    required = {
        "id", "scripts", "fixtures", "documentation", "kind", "owner_kind", "owner",
        "bootstrap_state", "prerequisites", "inputs", "observables", "normalization", "cleanup",
    }
    ids: list[str] = []
    scripts: list[str] = []
    fixtures: list[str] = []
    for demo in demos:
        if not isinstance(demo, dict) or set(demo) != required:
            raise DemoInventoryError("demo contract has unknown or missing fields")
        ids.append(_text(demo["id"], "id"))
        scripts.extend(_strings(demo["scripts"], "scripts"))
        fixtures.extend(_strings(demo["fixtures"], "fixtures"))
        _strings(demo["documentation"], "documentation")
        if demo["kind"] not in {"validation", "live-demo", "lifecycle"}:
            raise DemoInventoryError("unknown demo kind")
        if demo["owner_kind"] not in {"core", "hello", "deferred-product", "system"}:
            raise DemoInventoryError("unknown owner kind")
        _text(demo["owner"], "owner")
        if demo["bootstrap_state"] not in {"required", "deferred"}:
            raise DemoInventoryError("unknown bootstrap state")
        for field in ("prerequisites", "inputs", "observables", "cleanup"):
            if not _strings(demo[field], field):
                raise DemoInventoryError(f"{field} cannot be empty")
        normalization = _strings(demo["normalization"], "normalization")
        if not set(normalization).issubset(
            {"allocated-port", "generated-id", "timestamp", "container-name"}
        ):
            raise DemoInventoryError("semantic normalization is forbidden")
    if len(ids) != len(set(ids)):
        raise DemoInventoryError("demo identities must be unique")
    if len(scripts) != len(set(scripts)) or frozenset(scripts) != discovered_scripts:
        raise DemoInventoryError("every executable script must be accounted for exactly once")
    if len(fixtures) != len(set(fixtures)) or frozenset(fixtures) != discovered_fixtures:
        raise DemoInventoryError("every live fixture must be accounted for exactly once")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise DemoInventoryError(f"{label} must be bounded non-empty text")
    return value


def _strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DemoInventoryError(f"{label} must be a string list")
    return tuple(_text(item, label) for item in value)
