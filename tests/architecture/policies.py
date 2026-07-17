"""Reusable policies over the shared Python-source fact model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib.util import resolve_name

from tests.architecture.source import (
    CallFact,
    DecoratorFact,
    PolicyFinding,
    SourceFacts,
    SourceLocation,
    evaluate_policies,
)


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


@dataclass(frozen=True)
class CommitOwnershipPolicy:
    """Restrict commit call sites without pretending to infer receiver types.

    The policy proves where a call spelled ``*.commit()`` is written. Runtime
    receiver identity and transaction behavior remain the responsibility of
    UnitOfWork integration tests.
    """

    owner_modules: tuple[str, ...]
    owner_module_prefixes: tuple[str, ...] = ()

    def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]:
        if _module_owned(
            facts.module,
            modules=self.owner_modules,
            prefixes=self.owner_module_prefixes,
        ):
            return ()
        return tuple(
            PolicyFinding(
                "commit-ownership",
                f"commit calls are not owned by {facts.module}",
                call.location,
            )
            for call in facts.calls
            if call.qualified_name == "commit" or call.qualified_name.endswith(".commit")
        )


@dataclass(frozen=True)
class EnvironmentAccessPolicy:
    """Keep direct process-environment access at declared boundaries."""

    owner_modules: tuple[str, ...]

    def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]:
        if facts.module in self.owner_modules:
            return ()
        locations = {
            reference.location
            for reference in facts.references
            if _is_environment_reference(reference.qualified_name)
        }
        return tuple(
            PolicyFinding(
                "environment-access-ownership",
                f"direct process-environment access is not owned by {facts.module}",
                location,
            )
            for location in sorted(locations)
        )


@dataclass(frozen=True, order=True)
class AllowedSkip:
    """One reviewed conditional-skip declaration and its justification."""

    module: str
    decorator: str
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("allowed skip requires a reviewable reason")


class IntegrityEvidenceKind(StrEnum):
    """Non-failing declarations surfaced by a test-integrity audit."""

    APPROVED_SKIP = "approved-skip"
    TEST_DOUBLE = "test-double"


@dataclass(frozen=True, order=True)
class IntegrityEvidence:
    """One reviewable declaration that is not automatically a violation."""

    kind: IntegrityEvidenceKind
    name: str
    reason: str
    location: SourceLocation


@dataclass(frozen=True)
class TestIntegrityReport:
    """Deterministic violations and non-failing review evidence."""

    violations: tuple[PolicyFinding, ...]
    evidence: tuple[IntegrityEvidence, ...]


@dataclass(frozen=True)
class TestIntegrityPolicy:
    """Reject declarations that can silently weaken executable evidence."""

    allowed_skips: tuple[AllowedSkip, ...] = ()

    def __post_init__(self) -> None:
        identities = tuple(
            (value.module, value.decorator)
            for value in self.allowed_skips
        )
        if len(identities) != len(set(identities)):
            raise ValueError("allowed skip declarations must be unique")

    def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]:
        findings: list[PolicyFinding] = []
        allowed = {
            (value.module, value.decorator): value
            for value in self.allowed_skips
        }
        for decorator in facts.decorators:
            if not _is_skip_decorator(decorator.qualified_name):
                continue
            if (facts.module, decorator.qualified_name) in allowed:
                if not _approved_skip_is_disabled(decorator):
                    continue
            findings.append(
                PolicyFinding(
                    "unapproved-test-skip",
                    f"{decorator.qualified_name} is not approved in {facts.module}",
                    decorator.location,
                )
            )
        for call in facts.calls:
            if _is_runtime_skip(call.qualified_name):
                findings.append(
                    PolicyFinding(
                        "runtime-test-skip",
                        f"runtime skip {call.qualified_name} is prohibited",
                        call.location,
                    )
                )
            if _is_placeholder_assertion(call):
                findings.append(
                    PolicyFinding(
                        "placeholder-assertion",
                        f"{call.qualified_name} uses a constant passing value",
                        call.location,
                    )
                )
        findings.extend(
            PolicyFinding(
                "placeholder-assertion",
                "literal `assert True` does not test behavior",
                assertion.location,
            )
            for assertion in facts.boolean_assertions
            if assertion.value
        )
        findings.extend(
            PolicyFinding(
                "empty-test",
                f"{function.qualified_name} has no behavioral statements",
                function.location,
            )
            for function in facts.functions
            if function.qualified_name.rsplit(".", 1)[-1].startswith("test")
            and function.empty_body
        )
        findings.extend(
            PolicyFinding(
                "swallowed-exception",
                "pass-only exception handler hides failure evidence",
                handler.location,
            )
            for handler in facts.except_handlers
            if handler.pass_only
        )
        return tuple(sorted(findings))


def audit_test_integrity(
    facts: tuple[SourceFacts, ...],
    policy: TestIntegrityPolicy,
) -> TestIntegrityReport:
    """Evaluate weakening rules while retaining skips and doubles as evidence."""

    allowed = {
        (value.module, value.decorator): value
        for value in policy.allowed_skips
    }
    evidence: list[IntegrityEvidence] = []
    for source in facts:
        for decorator in source.decorators:
            declaration = allowed.get((source.module, decorator.qualified_name))
            if declaration is not None and not _approved_skip_is_disabled(decorator):
                evidence.append(
                    IntegrityEvidence(
                        IntegrityEvidenceKind.APPROVED_SKIP,
                        decorator.qualified_name,
                        declaration.reason,
                        decorator.location,
                    )
                )
        for call in source.calls:
            if _is_test_double(call.qualified_name):
                evidence.append(
                    IntegrityEvidence(
                        IntegrityEvidenceKind.TEST_DOUBLE,
                        call.qualified_name,
                        "review test-double scope against application behavior",
                        call.location,
                    )
                )
    return TestIntegrityReport(
        violations=evaluate_policies(facts, (policy,)),
        evidence=tuple(sorted(evidence)),
    )


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


def _module_owned(
    module: str,
    *,
    modules: tuple[str, ...],
    prefixes: tuple[str, ...],
) -> bool:
    return module in modules or any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in prefixes
    )


def _is_environment_reference(name: str) -> bool:
    return (
        name == "os.environ"
        or name.startswith("os.environ.")
        or name == "os.getenv"
        or name.startswith("os.getenv.")
    )


def _is_skip_decorator(name: str) -> bool:
    return name in {
        "unittest.skip",
        "unittest.skipIf",
        "unittest.skipUnless",
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.mark.xfail",
    }


def _is_runtime_skip(name: str) -> bool:
    return (
        name == "pytest.skip"
        or name == "pytest.xfail"
        or name == "unittest.SkipTest"
        or name.endswith(".skipTest")
    )


def _is_placeholder_assertion(call: CallFact) -> bool:
    first = next(
        (value.value for value in call.boolean_arguments if value.position == 0),
        None,
    )
    return (
        call.qualified_name.endswith(".assertTrue") and first is True
    ) or (
        call.qualified_name.endswith(".assertFalse") and first is False
    ) or (
        call.qualified_name.endswith(".assertEqual")
        and call.first_two_constants_equal
    )


def _is_test_double(name: str) -> bool:
    return name in {
        "unittest.mock.Mock",
        "unittest.mock.MagicMock",
        "unittest.mock.create_autospec",
        "unittest.mock.patch",
        "mock.Mock",
        "mock.MagicMock",
        "mock.patch",
    }


def _approved_skip_is_disabled(decorator: DecoratorFact) -> bool:
    first = next(
        (value.value for value in decorator.boolean_arguments if value.position == 0),
        None,
    )
    return first is False
