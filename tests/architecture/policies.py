"""Reusable policies over the shared Python-source fact model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib.util import resolve_name
from typing import Iterable

from tests.architecture.source import (
    CallFact,
    ExpressionShape,
    DecoratorFact,
    PolicyFinding,
    SourceFacts,
    SourceLocation,
    evaluate_policies,
)


@dataclass(frozen=True)
class CallKeywordShapePolicy:
    """Reject forbidden source shapes at one named constructor boundary."""

    rule_id: str
    call_names: tuple[str, ...]
    keyword_name: str
    forbidden_shapes: tuple[ExpressionShape, ...]

    def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]:
        findings: list[PolicyFinding] = []
        for call in facts.calls:
            if call.qualified_name.rsplit(".", 1)[-1] not in self.call_names:
                continue
            for keyword in call.keyword_arguments:
                if (
                    keyword.name == self.keyword_name
                    and keyword.shape in self.forbidden_shapes
                ):
                    findings.append(
                        PolicyFinding(
                            self.rule_id,
                            f"{self.keyword_name} must use the closed typed constructor language",
                            call.location,
                        )
                    )
        return tuple(findings)


@dataclass(frozen=True)
class CallKeywordMappingKeyPolicy:
    """Reject forbidden literal keys inside one constructor keyword mapping."""

    rule_id: str
    call_names: tuple[str, ...]
    keyword_name: str
    forbidden_keys: tuple[str, ...]

    def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]:
        return tuple(
            PolicyFinding(
                self.rule_id,
                f"{self.keyword_name} contains a forbidden typed-boundary key",
                call.location,
            )
            for call in facts.calls
            if call.qualified_name.rsplit(".", 1)[-1] in self.call_names
            for keyword in call.keyword_arguments
            if keyword.name == self.keyword_name
            and set(keyword.literal_mapping_keys).intersection(self.forbidden_keys)
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


class PackageOwnerKind(StrEnum):
    """Closed semantic owners used by the target package topology."""

    CORE = "core"
    DOMAIN = "domain"
    OPERATION = "operation"
    INTERPRETER = "interpreter"
    PRODUCT = "product"
    ENTRYPOINT = "entrypoint"


@dataclass(frozen=True, order=True)
class PackageNode:
    """One named node in the semantic package dependency graph."""

    name: str
    owner: PackageOwnerKind


@dataclass(frozen=True, order=True)
class ModulePackageOwnership:
    """Assign one current source module to one target package node."""

    module: str
    package_node: str


@dataclass(frozen=True, order=True)
class DeclaredPackageEdge:
    """One permitted direct edge in the target package graph."""

    source: str
    target: str


@dataclass(frozen=True, order=True)
class ExternalDependencyOwner:
    """Package nodes permitted to import one optional external dependency."""

    import_prefix: str
    package_nodes: tuple[str, ...]


@dataclass(frozen=True, order=True)
class PackageGraphEdge:
    """One observed source import interpreted as a package edge."""

    source: str
    target: str
    location: SourceLocation


@dataclass(frozen=True)
class PackageGraph:
    """Deterministic observed package topology with source evidence."""

    nodes: tuple[PackageNode, ...]
    edges: tuple[PackageGraphEdge, ...]


@dataclass(frozen=True, order=True)
class PackageCycle:
    """One canonical directed cycle, including its repeated start node."""

    path: tuple[str, ...]
    location: SourceLocation


@dataclass(frozen=True, order=True)
class ForbiddenPackagePath:
    """Reject transitive reachability between semantic ownership kinds."""

    source_owner: PackageOwnerKind
    target_owner: PackageOwnerKind
    rule_id: str


@dataclass(frozen=True)
class PackageTopologyPolicy:
    """Interpret all source facts as one typed acyclic package graph."""

    package: str
    nodes: tuple[PackageNode, ...]
    ownerships: tuple[ModulePackageOwnership, ...]
    declared_edges: tuple[DeclaredPackageEdge, ...]
    external_owners: tuple[ExternalDependencyOwner, ...] = ()
    forbidden_paths: tuple[ForbiddenPackagePath, ...] = ()
    root_module: str | None = None
    root_allowed_owners: tuple[PackageOwnerKind, ...] = (PackageOwnerKind.CORE,)

    def graph(self, facts: Iterable[SourceFacts]) -> PackageGraph:
        node_names = {value.name for value in self.nodes}
        ownerships = tuple(sorted(self.ownerships, key=lambda value: (-len(value.module), value.module)))
        external_prefixes = tuple(
            sorted(
                {value.import_prefix for value in self.external_owners},
                key=lambda value: (-len(value), value),
            )
        )
        edges: set[PackageGraphEdge] = set()
        for source in facts:
            source_node = _owned_package_node(source.module, ownerships)
            if source_node is None:
                continue
            if source_node not in node_names:
                raise ValueError("module ownership names an unknown package node")
            for imported in source.imports:
                imported_name = _absolute_import_name(
                    source.module, imported.qualified_name
                )
                target_node = _owned_package_node(imported_name, ownerships)
                if target_node is not None:
                    if target_node != source_node:
                        edges.add(
                            PackageGraphEdge(
                                source_node, target_node, imported.location
                            )
                        )
                    continue
                external = next(
                    (
                        prefix
                        for prefix in external_prefixes
                        if _matches_prefix(imported_name, prefix)
                    ),
                    None,
                )
                if external is not None:
                    edges.add(
                        PackageGraphEdge(
                            source_node,
                            f"external:{external}",
                            imported.location,
                        )
                    )
        return PackageGraph(tuple(sorted(self.nodes)), tuple(sorted(edges)))

    def evaluate(self, facts: Iterable[SourceFacts]) -> tuple[PolicyFinding, ...]:
        sources = tuple(facts)
        graph = self.graph(sources)
        declared = {(value.source, value.target) for value in self.declared_edges}
        external = {
            value.import_prefix: set(value.package_nodes)
            for value in self.external_owners
        }
        findings: list[PolicyFinding] = []
        for edge in graph.edges:
            if edge.target.startswith("external:"):
                prefix = edge.target.removeprefix("external:")
                if edge.source not in external[prefix]:
                    findings.append(
                        PolicyFinding(
                            "optional-dependency-owner",
                            f"{edge.source} does not own optional dependency {prefix}",
                            edge.location,
                        )
                    )
                continue
            if (edge.source, edge.target) not in declared:
                findings.append(
                    PolicyFinding(
                        "package-topology-edge",
                        f"{edge.source} must not depend on undeclared package {edge.target}",
                        edge.location,
                    )
                )

        owners = {value.name: value.owner for value in self.nodes}
        ownerships = tuple(
            sorted(
                self.ownerships,
                key=lambda value: (-len(value.module), value.module),
            )
        )
        if self.root_module is not None:
            for source in sources:
                if source.module != self.root_module:
                    continue
                for exported in source.exports:
                    exported_name = _absolute_import_name(
                        source.module, exported.qualified_name
                    )
                    target = _owned_package_node(exported_name, ownerships)
                    if target is None or owners[target] in self.root_allowed_owners:
                        continue
                    findings.append(
                        PolicyFinding(
                            "root-export-provenance",
                            f"{source.module} must not export {target} owned by {owners[target]}",
                            exported.location,
                        )
                    )
                for unsupported in source.unsupported_exports:
                    findings.append(
                        PolicyFinding(
                            "root-export-provenance",
                            f"{source.module} has a computed export declaration",
                            unsupported.location,
                        )
                    )

        for cycle in package_cycles(graph):
            findings.append(
                PolicyFinding(
                    "package-topology-cycle",
                    "package dependency cycle: " + " -> ".join(cycle.path),
                    cycle.location,
                )
            )

        for forbidden in self.forbidden_paths:
            for source in sorted(
                name for name, owner in owners.items() if owner is forbidden.source_owner
            ):
                path = _first_forbidden_path(
                    graph,
                    source=source,
                    target_nodes={
                        name
                        for name, owner in owners.items()
                        if owner is forbidden.target_owner
                    },
                )
                if path is None:
                    continue
                location = _edge_location(graph, path[0], path[1])
                findings.append(
                    PolicyFinding(
                        forbidden.rule_id,
                        "forbidden transitive package path: " + " -> ".join(path),
                        location,
                    )
                )
        return tuple(sorted(findings))


def package_cycles(graph: PackageGraph) -> tuple[PackageCycle, ...]:
    """Return one deterministic complete path for each cyclic component."""

    adjacency = _internal_adjacency(graph)
    components = _strongly_connected_components(adjacency)
    cycles: list[PackageCycle] = []
    for component in components:
        if len(component) == 1 and component[0] not in adjacency.get(component[0], ()):
            continue
        path = _cycle_path(adjacency, component)
        cycles.append(
            PackageCycle(
                path,
                _edge_location(graph, path[0], path[1]),
            )
        )
    return tuple(sorted(cycles))


def _owned_package_node(
    module: str,
    ownerships: tuple[ModulePackageOwnership, ...],
) -> str | None:
    for ownership in ownerships:
        if _matches_prefix(module, ownership.module):
            return ownership.package_node
    return None


def _internal_adjacency(graph: PackageGraph) -> dict[str, tuple[str, ...]]:
    names = {value.name for value in graph.nodes}
    return {
        name: tuple(
            sorted(
                {
                    edge.target
                    for edge in graph.edges
                    if edge.source == name and edge.target in names
                }
            )
        )
        for name in sorted(names)
    }


def _strongly_connected_components(
    adjacency: dict[str, tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    index = 0
    stack: list[str] = []
    stacked: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    components: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        stacked.add(node)
        for target in adjacency.get(node, ()):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in stacked:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while True:
            value = stack.pop()
            stacked.remove(value)
            component.append(value)
            if value == node:
                break
        components.append(tuple(sorted(component)))

    for node in sorted(adjacency):
        if node not in indices:
            visit(node)
    return tuple(sorted(components))


def _cycle_path(
    adjacency: dict[str, tuple[str, ...]],
    component: tuple[str, ...],
) -> tuple[str, ...]:
    allowed = set(component)
    start = min(component)

    def visit(node: str, path: tuple[str, ...]) -> tuple[str, ...] | None:
        for target in adjacency.get(node, ()):
            if target not in allowed:
                continue
            if target == start:
                return (*path, target)
            if target in path:
                continue
            result = visit(target, (*path, target))
            if result is not None:
                return result
        return None

    result = visit(start, (start,))
    if result is None:
        raise ValueError("strongly connected component did not yield a cycle")
    return result


def _first_forbidden_path(
    graph: PackageGraph,
    *,
    source: str,
    target_nodes: set[str],
) -> tuple[str, ...] | None:
    adjacency = _internal_adjacency(graph)
    queue: list[tuple[str, ...]] = [(source,)]
    seen = {source}
    while queue:
        path = queue.pop(0)
        for target in adjacency.get(path[-1], ()):
            if target in target_nodes:
                return (*path, target)
            if target in seen:
                continue
            seen.add(target)
            queue.append((*path, target))
    return None


def _edge_location(graph: PackageGraph, source: str, target: str) -> SourceLocation:
    return min(
        edge.location
        for edge in graph.edges
        if edge.source == source and edge.target == target
    )


@dataclass(frozen=True)
class SourceBoundaryPolicy:
    """Constrain one module family to composition over declared dependencies."""

    rule_prefix: str
    module_prefix: str
    forbidden_import_prefixes: tuple[str, ...] = ()
    forbidden_call_names: tuple[str, ...] = ()
    forbidden_call_prefixes: tuple[str, ...] = ()
    forbidden_class_names: tuple[str, ...] = ()

    def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]:
        if not (
            facts.module == self.module_prefix
            or facts.module.startswith(f"{self.module_prefix}.")
        ):
            return ()
        findings: list[PolicyFinding] = []
        for imported in facts.imports:
            imported_name = _absolute_import_name(facts.module, imported.qualified_name)
            if any(
                _matches_prefix(imported_name, prefix)
                for prefix in self.forbidden_import_prefixes
            ):
                findings.append(
                    PolicyFinding(
                        f"{self.rule_prefix}-import",
                        f"{facts.module} imports forbidden boundary {imported_name}",
                        imported.location,
                    )
                )
        for call in facts.calls:
            call_name = call.qualified_name.rsplit(".", 1)[-1]
            if call_name in self.forbidden_call_names or any(
                _matches_prefix(call.qualified_name, prefix)
                for prefix in self.forbidden_call_prefixes
            ):
                findings.append(
                    PolicyFinding(
                        f"{self.rule_prefix}-call",
                        f"{facts.module} calls forbidden boundary {call.qualified_name}",
                        call.location,
                    )
                )
        for declared in facts.classes:
            class_name = declared.qualified_name.rsplit(".", 1)[-1]
            if class_name in self.forbidden_class_names:
                findings.append(
                    PolicyFinding(
                        f"{self.rule_prefix}-duplicate-type",
                        f"{facts.module} redeclares canonical type {class_name}",
                        declared.location,
                    )
                )
        return tuple(sorted(findings))


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


@dataclass(frozen=True, order=True)
class ImportOwner:
    """Modules permitted to import one architecture-significant dependency."""

    import_prefix: str
    owner_modules: tuple[str, ...]


@dataclass(frozen=True)
class ImportOwnershipPolicy:
    """Reserve an external interpreter dependency to explicit adapter owners."""

    owners: tuple[ImportOwner, ...]

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
                        "import-ownership",
                        f"{imported_name} is not owned by {facts.module}",
                        imported.location,
                    )
                )
        return tuple(sorted(findings))


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


@dataclass(frozen=True, order=True)
class CallOwner:
    """Modules permitted to spell one application-significant call."""

    call_name: str
    owner_modules: tuple[str, ...]
    owner_module_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CallOwnershipPolicy:
    """Reserve significant call spellings to declared application owners.

    This is intentionally a source-boundary proof. It does not infer receiver
    types; application tests still prove the behavior of each allowed call.
    """

    owners: tuple[CallOwner, ...]

    def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]:
        findings: list[PolicyFinding] = []
        for call in facts.calls:
            call_name = call.qualified_name.rsplit(".", 1)[-1]
            for owner in self.owners:
                if call_name != owner.call_name:
                    continue
                if _module_owned(
                    facts.module,
                    modules=owner.owner_modules,
                    prefixes=owner.owner_module_prefixes,
                ):
                    continue
                findings.append(
                    PolicyFinding(
                        "call-ownership",
                        f"{owner.call_name} calls are not owned by {facts.module}",
                        call.location,
                    )
                )
        return tuple(sorted(findings))


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


@dataclass(frozen=True)
class ProtocolProjectionPolicy:
    """Reject scalar protocol projection outside explicit display-only owners."""

    scalar_display_owner_modules: tuple[str, ...] = ()

    def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]:
        if facts.module in self.scalar_display_owner_modules:
            return ()
        return tuple(
            PolicyFinding(
                "protocol-product-erasure",
                f"{facts.module} erases protocol product structure through .protocol.value",
                reference.location,
            )
            for reference in facts.references
            if reference.qualified_name.endswith(".protocol.value")
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


class HttpRouteMethod(StrEnum):
    """Closed HTTP route methods understood by static route policy."""

    GET = "GET"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


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


@dataclass(frozen=True)
class ReadOnlyRoutePolicy:
    """Reject mutation and ambiguous decorators in declared read modules.

    This policy proves route declaration shape only. Runtime route-table and
    authorization behavior require separate application-level tests.
    """

    modules: tuple[str, ...]
    allowed_methods: tuple[HttpRouteMethod, ...] = (
        HttpRouteMethod.GET,
        HttpRouteMethod.HEAD,
        HttpRouteMethod.OPTIONS,
    )

    def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]:
        if facts.module not in self.modules:
            return ()
        findings: list[PolicyFinding] = []
        for decorator in facts.decorators:
            method = _route_decorator_method(decorator.qualified_name)
            if method is None:
                continue
            if method in self.allowed_methods:
                continue
            findings.append(
                PolicyFinding(
                    "read-only-route",
                    f"{facts.module} declares prohibited {method.value} route",
                    decorator.location,
                )
            )
        for decorator in facts.decorators:
            if decorator.qualified_name.endswith((".route", ".api_route", ".websocket")):
                findings.append(
                    PolicyFinding(
                        "ambiguous-read-route",
                        f"{facts.module} must use an explicit read-only HTTP decorator",
                        decorator.location,
                    )
                )
        return tuple(sorted(findings))


def declared_route_methods(facts: SourceFacts) -> tuple[HttpRouteMethod, ...]:
    """Return explicit HTTP methods declared by decorators in one module."""

    return tuple(
        method
        for decorator in facts.decorators
        if (method := _route_decorator_method(decorator.qualified_name)) is not None
    )


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


def _route_decorator_method(name: str) -> HttpRouteMethod | None:
    suffix = name.rsplit(".", 1)[-1].upper()
    try:
        return HttpRouteMethod(suffix)
    except ValueError:
        return None
