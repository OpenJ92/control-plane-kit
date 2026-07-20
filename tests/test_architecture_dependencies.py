from __future__ import annotations

from pathlib import Path
import unittest

from tests.architecture import (
    CallKeywordShapePolicy,
    CallKeywordMappingKeyPolicy,
    ExpressionShape,
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
    PackageDependencyRule("_optional", ()),
    PackageDependencyRule("application", ("core", "effects", "workflows")),
    PackageDependencyRule(
        "adapters",
        (
            "_optional",
            "core",
            "effects",
            "execution",
        ),
    ),
    PackageDependencyRule("cli", ()),
    PackageDependencyRule("core", ()),
    PackageDependencyRule("contracts", ()),
    PackageDependencyRule("configuration_rendering", ("core",)),
    PackageDependencyRule("discovery", ("core",)),
    PackageDependencyRule(
        "discovery_registry", ("core", "discovery")
    ),
    PackageDependencyRule(
        "discovery_server", ("contracts", "discovery", "discovery_registry", "servers")
    ),
    PackageDependencyRule(
        "docker_runtime",
        (
            "core",
            "effects",
            "execution",
            "runtimes",
        ),
    ),
    PackageDependencyRule(
        "effects",
        (
            "core",
            "execution",
        ),
    ),
    PackageDependencyRule("execution", ()),
    PackageDependencyRule(
        "implementations",
        (
            "core",
        ),
    ),
    PackageDependencyRule("idempotency", ()),
    PackageDependencyRule(
        "idempotency_gateway", ("adapters", "contracts", "idempotency", "servers")
    ),
    PackageDependencyRule("load_generation", ()),
    PackageDependencyRule(
        "load_generator_server", ("adapters", "contracts", "load_generation", "servers")
    ),
    PackageDependencyRule("mcp_read", ("read_services",)),
    PackageDependencyRule("operations", ("core", "policies")),
    PackageDependencyRule("policies", ("core",)),
    PackageDependencyRule(
        "projections",
        ("core", "execution", "saga", "scheduling"),
    ),
    PackageDependencyRule(
        "read_services",
        ("core", "execution", "operations", "projections", "stores"),
    ),
    PackageDependencyRule("runtimes", ("core",)),
    PackageDependencyRule("saga", ()),
    PackageDependencyRule("scheduling", ("core", "saga")),
    PackageDependencyRule(
        "servers",
        (
            "_optional",
            "adapters",
            "core",
            "contracts",
            "configuration_rendering",
            "discovery",
            "idempotency",
            "implementations",
            "load_generation",
            "read_services",
            "webhook",
        ),
    ),
    PackageDependencyRule("stores", ("core", "execution")),
    PackageDependencyRule("webhook", ("_optional", "core")),
    PackageDependencyRule(
        "webhook_server",
        ("contracts", "core", "servers", "webhook"),
    ),
    PackageDependencyRule(
        "workflows",
        (
            "core",
            "effects",
            "execution",
            "policies",
            "projections",
            "saga",
            "scheduling",
            "stores",
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
                "control_plane_kit.webhook.http",
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
                "control_plane_kit.webhook.http",
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
STATIC_ENVIRONMENT_POLICY = CallKeywordShapePolicy(
    rule_id="closed-static-environment",
    call_names=("DockerImageImplementation",),
    keyword_name="environment",
    forbidden_shapes=(ExpressionShape.DICTIONARY, ExpressionShape.LIST),
)
ENVIRONMENT_METADATA_POLICY = CallKeywordMappingKeyPolicy(
    rule_id="no-environment-metadata",
    call_names=("Node", "MaterializedNode"),
    keyword_name="metadata",
    forbidden_keys=("environment",),
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
                (
                    DEPENDENCY_POLICY,
                    TRANSPORT_POLICY,
                    TEMPLATE_ENGINE_POLICY,
                    STATIC_ENVIRONMENT_POLICY,
                    ENVIRONMENT_METADATA_POLICY,
                ),
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

    def test_raw_static_environment_mapping_is_rejected_at_docker_authoring(self) -> None:
        facts = analyze_source(
            "from control_plane_kit import DockerImageImplementation\n"
            "DockerImageImplementation('service:latest', environment={'MODE': 'safe'})\n",
            path="control_plane_kit/servers/product.py",
            module="control_plane_kit.servers.product",
        )

        findings = STATIC_ENVIRONMENT_POLICY.evaluate(facts)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "closed-static-environment")

    def test_environment_metadata_escape_hatch_is_rejected(self) -> None:
        facts = analyze_source(
            "Node(metadata={'environment': {'MODE': 'unsafe'}})\n",
            path="control_plane_kit/product.py",
            module="control_plane_kit.product",
        )

        findings = ENVIRONMENT_METADATA_POLICY.evaluate(facts)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "no-environment-metadata")


if __name__ == "__main__":
    unittest.main()
