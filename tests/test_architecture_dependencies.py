from __future__ import annotations

from pathlib import Path
import unittest

from tests.architecture import (
    PackageDependencyPolicy,
    PackageDependencyRule,
    ImportOwner,
    ImportOwnershipPolicy,
    TransportOwner,
    TransportOwnershipPolicy,
    analyze_file,
    analyze_source,
    evaluate_policies,
)


PACKAGE_RULES = (
    PackageDependencyRule("application", ("effects", "topology", "workflows")),
    PackageDependencyRule(
        "adapters",
        (
            "capabilities",
            "control_routes",
            "effects",
            "execution",
            "planning",
            "secrets",
            "types",
            "verification",
        ),
    ),
    PackageDependencyRule(
        "algebra", ("capabilities", "lifecycle", "types", "verification")
    ),
    PackageDependencyRule("capabilities", ("control_routes",)),
    PackageDependencyRule("cli", ()),
    PackageDependencyRule("contracts", ()),
    PackageDependencyRule("configuration", ()),
    PackageDependencyRule("configuration_rendering", ("configuration",)),
    PackageDependencyRule("control_routes", ()),
    PackageDependencyRule("discovery", ("topology", "types")),
    PackageDependencyRule(
        "docker_runtime",
        (
            "configuration",
            "effects",
            "execution",
            "lifecycle",
            "planning",
            "runtimes",
            "secrets",
            "topology",
            "types",
        ),
    ),
    PackageDependencyRule(
        "effects",
        (
            "configuration",
            "execution",
            "lifecycle",
            "planning",
            "secrets",
            "topology",
            "types",
            "verification",
        ),
    ),
    PackageDependencyRule("execution", ()),
    PackageDependencyRule(
        "implementations",
        ("algebra", "configuration", "lifecycle", "secrets", "topology", "types"),
    ),
    PackageDependencyRule("idempotency", ()),
    PackageDependencyRule(
        "idempotency_gateway", ("adapters", "contracts", "idempotency", "servers")
    ),
    PackageDependencyRule("lifecycle", ()),
    PackageDependencyRule("load_generation", ()),
    PackageDependencyRule(
        "load_generator_server", ("adapters", "contracts", "load_generation", "servers")
    ),
    PackageDependencyRule("mcp_read", ("read_services",)),
    PackageDependencyRule("planning", ("lifecycle", "policies", "topology")),
    PackageDependencyRule("policies", ("planning", "types")),
    PackageDependencyRule(
        "projections",
        ("execution", "planning", "saga", "scheduling", "topology"),
    ),
    PackageDependencyRule(
        "read_services",
        ("control_routes", "execution", "planning", "projections", "stores", "topology", "types"),
    ),
    PackageDependencyRule("runtimes", ("topology", "types")),
    PackageDependencyRule("saga", ()),
    PackageDependencyRule("scheduling", ("planning", "saga")),
    PackageDependencyRule("secrets", ()),
    PackageDependencyRule(
        "servers",
        (
            "adapters",
            "algebra",
            "capabilities",
            "contracts",
            "configuration_rendering",
            "control_routes",
            "idempotency",
            "implementations",
            "load_generation",
            "read_services",
            "secrets",
            "types",
        ),
    ),
    PackageDependencyRule("stores", ("execution", "planning", "topology", "types")),
    PackageDependencyRule(
        "topology",
        (
            "algebra",
            "capabilities",
            "configuration",
            "control_routes",
            "lifecycle",
            "secrets",
            "types",
            "verification",
        ),
    ),
    PackageDependencyRule("types", ()),
    PackageDependencyRule("verification", ("types",)),
    PackageDependencyRule(
        "workflows",
        (
            "effects",
            "execution",
            "planning",
            "policies",
            "projections",
            "saga",
            "scheduling",
            "stores",
            "topology",
            "types",
            "verification",
        ),
    ),
)

DEPENDENCY_POLICY = PackageDependencyPolicy("control_plane_kit", PACKAGE_RULES)
TRANSPORT_POLICY = TransportOwnershipPolicy(
    (
        TransportOwner("subprocess", ("control_plane_kit.docker_runtime",)),
        TransportOwner(
            "httpx",
            (
                "control_plane_kit.adapters.control_http.client",
                "control_plane_kit.adapters.http_forwarding",
                "control_plane_kit.adapters.probes.clients",
                "control_plane_kit.adapters.verification",
                "control_plane_kit.cli",
            ),
        ),
        TransportOwner("requests", ("control_plane_kit.cli",)),
        TransportOwner("aiohttp", ()),
        TransportOwner("http.client", ()),
        TransportOwner(
            "socket",
            (
                "control_plane_kit.adapters.probes.clients",
                "control_plane_kit.adapters.verification",
            ),
        ),
        TransportOwner("urllib3", ()),
        TransportOwner("urllib.request", ("control_plane_kit.cli",)),
    )
)
TEMPLATE_ENGINE_POLICY = ImportOwnershipPolicy(
    (
        ImportOwner(
            "jinja2",
            (
                "control_plane_kit.configuration_rendering",
                "control_plane_kit.servers._templates",
            ),
        ),
    )
)


class ArchitectureDependencyTests(unittest.TestCase):
    def test_repository_obeys_declared_dependency_and_transport_ownership(self) -> None:
        root = Path(__file__).parents[1]
        facts = tuple(
            analyze_file(path, root=root)
            for path in sorted((root / "control_plane_kit").rglob("*.py"))
        )
        discovered_roots = {
            value.module.split(".", 2)[1]
            for value in facts
            if value.module.startswith("control_plane_kit.")
        }

        self.assertEqual(
            discovered_roots,
            {value.source_root for value in PACKAGE_RULES},
        )
        self.assertEqual(
            evaluate_policies(
                facts,
                (DEPENDENCY_POLICY, TRANSPORT_POLICY, TEMPLATE_ENGINE_POLICY),
            ),
            (),
        )

    def test_reverse_dependency_is_rejected_for_import_and_from_import(self) -> None:
        direct = analyze_source(
            "import control_plane_kit.stores as persistence\n",
            path="control_plane_kit/saga/direct.py",
            module="control_plane_kit.saga.direct",
        )
        selected = analyze_source(
            "from control_plane_kit.workflows import OperationCommandService as Service\n",
            path="control_plane_kit/planning/selected.py",
            module="control_plane_kit.planning.selected",
        )

        findings = evaluate_policies((direct, selected), (DEPENDENCY_POLICY,))

        self.assertEqual(len(findings), 2)
        self.assertEqual(
            {value.location.path for value in findings},
            {
                "control_plane_kit/saga/direct.py",
                "control_plane_kit/planning/selected.py",
            },
        )

    def test_effect_materialization_and_interpreters_cannot_import_stores(self) -> None:
        facts = analyze_source(
            "from control_plane_kit.stores import GraphTopologyStore\n",
            path="control_plane_kit/effects/provider.py",
            module="control_plane_kit.effects.provider",
        )

        findings = DEPENDENCY_POLICY.evaluate(facts)

        self.assertEqual(len(findings), 1)
        self.assertIn("stores", findings[0].message)

    def test_relative_same_package_import_is_allowed(self) -> None:
        facts = analyze_source(
            "from .state import evolve\n",
            path="control_plane_kit/saga/program.py",
            module="control_plane_kit.saga.program",
        )

        self.assertEqual(DEPENDENCY_POLICY.evaluate(facts), ())

    def test_process_and_http_clients_are_adapter_owned(self) -> None:
        process = analyze_source(
            "import subprocess as process\n",
            path="control_plane_kit/saga/process.py",
            module="control_plane_kit.saga.process",
        )
        http = analyze_source(
            "from urllib.request import urlopen as send\n",
            path="control_plane_kit/planning/http.py",
            module="control_plane_kit.planning.http",
        )
        adapter = analyze_source(
            "import subprocess\n",
            path="control_plane_kit/docker_runtime.py",
            module="control_plane_kit.docker_runtime",
        )

        findings = evaluate_policies((process, http, adapter), (TRANSPORT_POLICY,))

        self.assertEqual(len(findings), 2)
        self.assertEqual(
            {value.location.path for value in findings},
            {
                "control_plane_kit/planning/http.py",
                "control_plane_kit/saga/process.py",
            },
        )

    def test_jinja_environment_is_owned_by_declared_template_interpreters(self) -> None:
        product = analyze_source(
            "from jinja2 import Environment\n",
            path="control_plane_kit/servers/product.py",
            module="control_plane_kit.servers.product",
        )
        renderer = analyze_source(
            "from jinja2.sandbox import ImmutableSandboxedEnvironment\n",
            path="control_plane_kit/configuration_rendering.py",
            module="control_plane_kit.configuration_rendering",
        )

        findings = evaluate_policies((product, renderer), (TEMPLATE_ENGINE_POLICY,))

        self.assertEqual(len(findings), 1)
        self.assertEqual(
            findings[0].location.path,
            "control_plane_kit/servers/product.py",
        )


if __name__ == "__main__":
    unittest.main()
