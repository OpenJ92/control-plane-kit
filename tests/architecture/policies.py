"""Reusable policies over the shared Python-source fact model."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import resolve_name

from tests.architecture.source import PolicyFinding, SourceFacts


@dataclass(frozen=True, order=True)
class PackageDependencyRule:
    """Allowed internal package roots for one source package root."""

    source_root: str
    allowed_roots: tuple[str, ...]


@dataclass(frozen=True)
class PackageDependencyPolicy:
    """Reject undeclared internal dependency edges."""

    package: str
    rules: tuple[PackageDependencyRule, ...]

    def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]:
        source_root = _package_root(facts.module, self.package)
        if source_root is None or facts.module == self.package:
            return ()

        rules = {value.source_root: value for value in self.rules}
        findings: list[PolicyFinding] = []
        for imported in facts.imports:
            imported_name = _absolute_import_name(facts.module, imported.qualified_name)
            target_root = _package_root(imported_name, self.package)
            if target_root is None or target_root == source_root:
                continue
            rule = rules.get(source_root)
            if rule is not None and target_root in rule.allowed_roots:
                continue
            findings.append(
                PolicyFinding(
                    "package-dependency",
                    f"{source_root} must not depend on undeclared package {target_root}",
                    imported.location,
                )
            )
        return tuple(findings)


@dataclass(frozen=True, order=True)
class TransportOwner:
    """Modules permitted to import one transport or process API."""

    import_prefix: str
    owner_modules: tuple[str, ...]


@dataclass(frozen=True)
class TransportOwnershipPolicy:
    """Keep side-effecting transport imports inside declared adapters."""

    owners: tuple[TransportOwner, ...]

    def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]:
        findings: list[PolicyFinding] = []
        for imported in facts.imports:
            imported_name = _absolute_import_name(facts.module, imported.qualified_name)
            for owner in self.owners:
                if not _matches_prefix(imported_name, owner.import_prefix):
                    continue
                if facts.module in owner.owner_modules:
                    continue
                findings.append(
                    PolicyFinding(
                        "transport-ownership",
                        _transport_owner_message(imported_name, owner.owner_modules),
                        imported.location,
                    )
                )
        return tuple(findings)


def _package_root(module: str, package: str) -> str | None:
    if module == package:
        return "<facade>"
    prefix = f"{package}."
    if not module.startswith(prefix):
        return None
    return module[len(prefix) :].split(".", 1)[0]


def _absolute_import_name(source_module: str, imported_name: str) -> str:
    if not imported_name.startswith("."):
        return imported_name
    source_package = source_module.rpartition(".")[0]
    return resolve_name(imported_name, source_package)


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _transport_owner_message(module: str, owners: tuple[str, ...]) -> str:
    if not owners:
        return f"{module} has no declared transport adapter owner"
    return f"{module} is owned by {', '.join(owners)}"
