from __future__ import annotations

import json
from pathlib import Path
import unittest

from tests.architecture import (
    DeclaredPackageEdge,
    ExternalDependencyOwner,
    ForbiddenPackagePath,
    ModulePackageOwnership,
    PackageNode,
    PackageOwnerKind,
    PackageTopologyPolicy,
    analyze_file,
    analyze_source,
    package_cycles,
)


class PackageTopologyPolicyTests(unittest.TestCase):
    def test_declared_edges_cannot_hide_direct_or_multi_node_cycles(self) -> None:
        facts = (
            self._fact("from control_plane_kit.b import value\n", "control_plane_kit.a"),
            self._fact("from control_plane_kit.a import value\n", "control_plane_kit.b"),
            self._fact("from control_plane_kit.d import value\n", "control_plane_kit.c"),
            self._fact("from control_plane_kit.e import value\n", "control_plane_kit.d"),
            self._fact("from control_plane_kit.c import value\n", "control_plane_kit.e"),
        )
        policy = PackageTopologyPolicy(
            package="control_plane_kit",
            nodes=tuple(
                PackageNode(name, PackageOwnerKind.CORE)
                for name in ("a", "b", "c", "d", "e")
            ),
            ownerships=tuple(
                ModulePackageOwnership(f"control_plane_kit.{name}", name)
                for name in ("a", "b", "c", "d", "e")
            ),
            declared_edges=(
                DeclaredPackageEdge("a", "b"),
                DeclaredPackageEdge("b", "a"),
                DeclaredPackageEdge("c", "d"),
                DeclaredPackageEdge("d", "e"),
                DeclaredPackageEdge("e", "c"),
            ),
        )

        findings = policy.evaluate(facts)

        self.assertEqual(
            [value.message for value in findings],
            [
                "package dependency cycle: a -> b -> a",
                "package dependency cycle: c -> d -> e -> c",
            ],
        )

    def test_legal_acyclic_sibling_dependencies_are_representable(self) -> None:
        facts = (
            self._fact("from control_plane_kit.core import value\n", "control_plane_kit.domain"),
            self._fact("from control_plane_kit.core import value\n", "control_plane_kit.product"),
        )
        policy = PackageTopologyPolicy(
            package="control_plane_kit",
            nodes=(
                PackageNode("core", PackageOwnerKind.CORE),
                PackageNode("domain", PackageOwnerKind.DOMAIN),
                PackageNode("product", PackageOwnerKind.PRODUCT),
            ),
            ownerships=(
                ModulePackageOwnership("control_plane_kit.core", "core"),
                ModulePackageOwnership("control_plane_kit.domain", "domain"),
                ModulePackageOwnership("control_plane_kit.product", "product"),
            ),
            declared_edges=(
                DeclaredPackageEdge("domain", "core"),
                DeclaredPackageEdge("product", "core"),
            ),
        )

        self.assertEqual(policy.evaluate(facts), ())

    def test_root_provenance_rejects_optional_dependency_leakage(self) -> None:
        facts = (
            self._fact("import httpx\n", "control_plane_kit"),
            self._fact("import httpx\n", "control_plane_kit.entrypoint"),
        )
        policy = PackageTopologyPolicy(
            package="control_plane_kit",
            nodes=(
                PackageNode("core.root", PackageOwnerKind.CORE),
                PackageNode("entrypoints.http", PackageOwnerKind.ENTRYPOINT),
            ),
            ownerships=(
                ModulePackageOwnership("control_plane_kit.entrypoint", "entrypoints.http"),
                ModulePackageOwnership("control_plane_kit", "core.root"),
            ),
            declared_edges=(),
            external_owners=(
                ExternalDependencyOwner("httpx", ("entrypoints.http",)),
            ),
        )

        findings = policy.evaluate(facts)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "optional-dependency-owner")
        self.assertIn("core.root", findings[0].message)

    def test_root_exports_must_come_from_allowed_package_owners(self) -> None:
        facts = (
            self._fact(
                "from control_plane_kit.operation import Service\n"
                "class PureValue:\n"
                "    pass\n"
                "__all__ = ['Service', 'PureValue']\n",
                "control_plane_kit",
            ),
        )
        policy = PackageTopologyPolicy(
            package="control_plane_kit",
            nodes=(
                PackageNode("core.root", PackageOwnerKind.CORE),
                PackageNode("operations.service", PackageOwnerKind.OPERATION),
            ),
            ownerships=(
                ModulePackageOwnership(
                    "control_plane_kit.operation", "operations.service"
                ),
                ModulePackageOwnership("control_plane_kit", "core.root"),
            ),
            declared_edges=(
                DeclaredPackageEdge("core.root", "operations.service"),
            ),
            root_module="control_plane_kit",
        )

        findings = policy.evaluate(facts)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "root-export-provenance")
        self.assertIn("operations.service", findings[0].message)

    def test_computed_root_exports_fail_closed(self) -> None:
        facts = (
            self._fact(
                "PUBLIC = ('Value',)\n__all__ += PUBLIC\n",
                "control_plane_kit",
            ),
        )
        policy = PackageTopologyPolicy(
            package="control_plane_kit",
            nodes=(PackageNode("core.root", PackageOwnerKind.CORE),),
            ownerships=(
                ModulePackageOwnership("control_plane_kit", "core.root"),
            ),
            declared_edges=(),
            root_module="control_plane_kit",
        )

        findings = policy.evaluate(facts)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "root-export-provenance")
        self.assertIn("computed export", findings[0].message)

    def test_product_may_project_domain_but_not_registry_or_process(self) -> None:
        facts = (
            self._fact(
                "from control_plane_kit.domains.discovery import DiscoveryRecord\n",
                "control_plane_kit.products.coredns",
            ),
            self._fact(
                "from control_plane_kit.products.coredns import project\n",
                "control_plane_kit.entrypoints.coredns",
            ),
        )
        policy = PackageTopologyPolicy(
            package="control_plane_kit",
            nodes=(
                PackageNode("domains.discovery", PackageOwnerKind.DOMAIN),
                PackageNode("operations.discovery", PackageOwnerKind.OPERATION),
                PackageNode("products.coredns", PackageOwnerKind.PRODUCT),
                PackageNode("entrypoints.coredns", PackageOwnerKind.ENTRYPOINT),
            ),
            ownerships=(
                ModulePackageOwnership("control_plane_kit.discovery_registry", "operations.discovery"),
                ModulePackageOwnership("control_plane_kit.domains.discovery", "domains.discovery"),
                ModulePackageOwnership("control_plane_kit.products.coredns", "products.coredns"),
                ModulePackageOwnership("control_plane_kit.entrypoints.coredns", "entrypoints.coredns"),
            ),
            declared_edges=(
                DeclaredPackageEdge("products.coredns", "domains.discovery"),
                DeclaredPackageEdge("entrypoints.coredns", "products.coredns"),
            ),
            forbidden_paths=(
                ForbiddenPackagePath(
                    PackageOwnerKind.PRODUCT,
                    PackageOwnerKind.ENTRYPOINT,
                    "product-process-dependency",
                ),
            ),
        )

        self.assertEqual(policy.evaluate(facts), ())

        invalid = (
            *facts,
            self._fact(
                "from control_plane_kit.discovery_registry import Service\n",
                "control_plane_kit.products.coredns",
            ),
        )
        findings = policy.evaluate(invalid)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "package-topology-edge")

    def test_domain_language_rejects_operations_products_interpreters_and_entrypoints(self) -> None:
        nodes = (
            PackageNode("core", PackageOwnerKind.CORE),
            PackageNode("domains.discovery", PackageOwnerKind.DOMAIN),
            PackageNode("operations.discovery", PackageOwnerKind.OPERATION),
            PackageNode("interpreters.http", PackageOwnerKind.INTERPRETER),
            PackageNode("products.servers", PackageOwnerKind.PRODUCT),
            PackageNode("entrypoints.discovery", PackageOwnerKind.ENTRYPOINT),
        )
        ownerships = tuple(
            ModulePackageOwnership(f"control_plane_kit.{module}", node)
            for module, node in (
                ("core", "core"),
                ("domains.discovery", "domains.discovery"),
                ("operations.discovery", "operations.discovery"),
                ("interpreters.http", "interpreters.http"),
                ("products.servers", "products.servers"),
                ("entrypoints.discovery", "entrypoints.discovery"),
            )
        )
        policy = PackageTopologyPolicy(
            package="control_plane_kit",
            nodes=nodes,
            ownerships=ownerships,
            declared_edges=(DeclaredPackageEdge("domains.discovery", "core"),),
        )

        for forbidden in (
            "operations.discovery",
            "interpreters.http",
            "products.servers",
            "entrypoints.discovery",
        ):
            facts = self._fact(
                f"from control_plane_kit.{forbidden} import value\n",
                "control_plane_kit.domains.discovery.language",
            )
            findings = policy.evaluate((facts,))
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].rule_id, "package-topology-edge")

    def test_product_to_domain_projection_does_not_authorize_reverse_edge(self) -> None:
        policy = PackageTopologyPolicy(
            package="control_plane_kit",
            nodes=(
                PackageNode("domains.discovery", PackageOwnerKind.DOMAIN),
                PackageNode("products.servers", PackageOwnerKind.PRODUCT),
            ),
            ownerships=(
                ModulePackageOwnership(
                    "control_plane_kit.domains.discovery", "domains.discovery"
                ),
                ModulePackageOwnership(
                    "control_plane_kit.products.servers", "products.servers"
                ),
            ),
            declared_edges=(
                DeclaredPackageEdge("products.servers", "domains.discovery"),
            ),
        )

        product = self._fact(
            "from control_plane_kit.domains.discovery import DiscoveryResult\n",
            "control_plane_kit.products.servers.coredns",
        )
        self.assertEqual(policy.evaluate((product,)), ())

        domain = self._fact(
            "from control_plane_kit.products.servers import coredns\n",
            "control_plane_kit.domains.discovery.language",
        )
        findings = policy.evaluate((domain,))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "package-topology-edge")

    def test_transitive_product_to_process_path_is_rejected(self) -> None:
        facts = (
            self._fact("from control_plane_kit.domain import value\n", "control_plane_kit.product"),
            self._fact("from control_plane_kit.entrypoint import app\n", "control_plane_kit.domain"),
        )
        policy = PackageTopologyPolicy(
            package="control_plane_kit",
            nodes=(
                PackageNode("domain", PackageOwnerKind.DOMAIN),
                PackageNode("product", PackageOwnerKind.PRODUCT),
                PackageNode("entrypoint", PackageOwnerKind.ENTRYPOINT),
            ),
            ownerships=(
                ModulePackageOwnership("control_plane_kit.domain", "domain"),
                ModulePackageOwnership("control_plane_kit.product", "product"),
                ModulePackageOwnership("control_plane_kit.entrypoint", "entrypoint"),
            ),
            declared_edges=(
                DeclaredPackageEdge("product", "domain"),
                DeclaredPackageEdge("domain", "entrypoint"),
            ),
            forbidden_paths=(
                ForbiddenPackagePath(
                    PackageOwnerKind.PRODUCT,
                    PackageOwnerKind.ENTRYPOINT,
                    "product-process-dependency",
                ),
            ),
        )

        findings = policy.evaluate(facts)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "product-process-dependency")
        self.assertIn("product -> domain -> entrypoint", findings[0].message)

    @staticmethod
    def _fact(source: str, module: str):
        return analyze_source(
            source,
            path=module.replace(".", "/") + ".py",
            module=module,
        )


class CurrentPackageTopologyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).parents[1]
        document = json.loads(
            (self.root / "docs/architecture/package-module-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        self.records = document["modules"]
        self.facts = tuple(
            analyze_file(path, root=self.root)
            for path in sorted((self.root / "control_plane_kit").rglob("*.py"))
        )
        node_owners: dict[str, PackageOwnerKind] = {}
        ownerships = []
        for record in self.records:
            node = self._package_node(record["destination"])
            owner = PackageOwnerKind(record["owner"])
            prior = node_owners.setdefault(node, owner)
            self.assertIs(prior, owner)
            ownerships.append(ModulePackageOwnership(record["module"], node))
        self.nodes = tuple(
            PackageNode(name, owner) for name, owner in sorted(node_owners.items())
        )
        self.ownerships = tuple(ownerships)

    def test_current_source_has_one_way_recovery_policy_planning_edges(self) -> None:
        discovery = PackageTopologyPolicy(
            package="control_plane_kit",
            nodes=self.nodes,
            ownerships=self.ownerships,
            declared_edges=(),
        )
        graph = discovery.graph(self.facts)
        cycles = package_cycles(graph)

        self.assertTrue(
            any(
                edge.source == "operations.planning"
                and edge.target == "operations.policies"
                for edge in graph.edges
            )
        )
        self.assertTrue(
            any(
                edge.source == "operations.policies"
                and edge.target == "core.planning"
                for edge in graph.edges
            )
        )
        self.assertFalse(
            any(
                "core.planning" in cycle.path
                or "operations.planning" in cycle.path
                for cycle in cycles
            )
        )

    def test_root_pure_facade_has_a_closed_owner_set(self) -> None:
        policy = PackageTopologyPolicy(
            package="control_plane_kit",
            nodes=self.nodes,
            ownerships=self.ownerships,
            declared_edges=(),
            root_module="control_plane_kit",
            root_allowed_owners=(
                PackageOwnerKind.CORE,
                PackageOwnerKind.OPERATION,
            ),
        )
        graph = policy.graph(self.facts)

        self.assertTrue(
            any(
                edge.source == "core.root"
                and edge.target == "operations.contracts"
                for edge in graph.edges
            )
        )
        self.assertFalse(
            any(
                edge.source == "core.root"
                and edge.target == "operations.planning"
                for edge in graph.edges
            )
        )
        self.assertEqual(
            {
                edge.target
                for edge in graph.edges
                if edge.source == "core.root"
                and edge.target.startswith("operations.")
            },
            {
                "operations.contracts",
                "operations.effects",
                "operations.execution",
                "operations.saga",
                "operations.scheduling",
            },
        )
        root_findings = tuple(
            finding
            for finding in policy.evaluate(self.facts)
            if finding.rule_id == "root-export-provenance"
        )
        self.assertEqual(root_findings, ())

    def test_current_package_graph_is_acyclic(self) -> None:
        policy = PackageTopologyPolicy(
            package="control_plane_kit",
            nodes=self.nodes,
            ownerships=self.ownerships,
            declared_edges=(),
        )

        self.assertEqual(package_cycles(policy.graph(self.facts)), ())

    @staticmethod
    def _package_node(destination: str) -> str:
        parts = destination.split(".")
        if destination == "control_plane_kit":
            return "core.root"
        owner = parts[1]
        if owner == "products" and len(parts) >= 3 and parts[2] == "servers":
            return "products.servers"
        if len(parts) >= 3:
            return ".".join(parts[1:3])
        return ".".join(parts[1:])

if __name__ == "__main__":
    unittest.main()
