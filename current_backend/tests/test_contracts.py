from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from current_backend.contracts import (
    BackendContractError,
    load_backend_contracts,
    validate_backend,
)
from current_backend.source_lock import MaterializedBackend


CORE_COMMIT = "1" * 40
INTERPRETERS_COMMIT = "2" * 40
SECRETS_COMMIT = "3" * 40


class BackendContractTests(unittest.TestCase):
    def test_closed_contract_fixture_is_coherent(self) -> None:
        with _backend_fixture() as fixture:
            report = validate_backend(fixture.backend, fixture.contracts)

        self.assertEqual(report.source_files, 13)
        self.assertEqual(
            report.import_edges,
            (
                ("interpreters", "core"),
                ("operations", "core"),
                ("server-products", "core"),
                ("server-products", "interpreters"),
                ("server-products", "operations"),
            ),
        )
        self.assertEqual(report.products, ("hello-server",))
        self.assertEqual(len(report.protocols), 6)
        self.assertEqual(report.acceptance, ("cpk-server-http-mcp-source-live",))

    def test_direct_reverse_and_cyclic_dependencies_fail_closed(self) -> None:
        with _backend_fixture() as fixture:
            operations = fixture.path("control-plane-kit") / (
                "control-plane-kit-operations/src/control_plane_kit_operations/service.py"
            )
            operations.write_text(
                "from control_plane_kit_interpreters import DockerRuntimeInterpreter\n",
                encoding="utf-8",
            )
            core_project = fixture.path("control-plane-kit") / "control-plane-kit-core/pyproject.toml"
            core_project.write_text(
                _project("control-plane-kit-core", ("control-plane-kit-operations>=0.1.0",)),
                encoding="utf-8",
            )
            core_source = fixture.path("control-plane-kit") / (
                "control-plane-kit-core/src/control_plane_kit_core/model.py"
            )
            core_source.write_text(
                "from control_plane_kit_operations import Operation\n",
                encoding="utf-8",
            )

            with self.assertRaises(BackendContractError) as raised:
                validate_backend(fixture.backend, fixture.contracts)

        message = str(raised.exception)
        self.assertIn("forbidden dependency edge: operations -> interpreters", message)
        self.assertIn("forbidden dependency edge: core -> operations", message)
        self.assertIn("dependency cycle", message)

    def test_transitive_forbidden_path_is_rejected_at_its_illegal_edge(self) -> None:
        with _backend_fixture() as fixture:
            path = fixture.path("control-plane-kit") / (
                "control-plane-kit-operations/src/control_plane_kit_operations/service.py"
            )
            path.write_text(
                "from control_plane_kit_interpreters import runtime\n",
                encoding="utf-8",
            )
            project = fixture.path("control-plane-kit") / "control-plane-kit-operations/pyproject.toml"
            project.write_text(
                _project(
                    "control-plane-kit-operations",
                    (
                        "control-plane-kit-core>=0.1.0",
                        "control-plane-kit-interpreters>=0.1.0",
                    ),
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                BackendContractError,
                "forbidden dependency edge: operations -> interpreters",
            ):
                validate_backend(fixture.backend, fixture.contracts)

    def test_unowned_and_duplicate_source_inventory_fail_closed(self) -> None:
        with _backend_fixture() as fixture:
            unowned = fixture.path("control-plane-kit") / "control-plane-kit-extra/src/extra/value.py"
            _write(unowned, "VALUE = 1\n")
            core = fixture.contracts.distributions[0]
            duplicate = replace(
                core,
                source_globs=(*core.source_globs, "control-plane-kit-core/src/**/*.py"),
            )
            contracts = replace(
                fixture.contracts,
                distributions=(duplicate, *fixture.contracts.distributions[1:]),
            )

            with self.assertRaises(BackendContractError) as raised:
                validate_backend(fixture.backend, contracts)

        self.assertIn("current source has no owner", str(raised.exception))
        self.assertIn("source has duplicate owners", str(raised.exception))

    def test_stale_and_unowned_dependency_pins_fail_closed(self) -> None:
        with _backend_fixture() as fixture:
            project = fixture.path("control-plane-kit-interpreters") / "pyproject.toml"
            text = project.read_text(encoding="utf-8")
            text = text.replace(CORE_COMMIT, "f" * 40)
            text += (
                '\n# https://github.com/OpenJ92/not-owned/archive/'
                + "e" * 40
                + ".zip\n"
            )
            project.write_text(text, encoding="utf-8")

            with self.assertRaises(BackendContractError) as raised:
                validate_backend(fixture.backend, fixture.contracts)

        self.assertIn("pin mismatch", str(raised.exception))
        self.assertIn("unowned pin coordinate", str(raised.exception))

    def test_descriptor_catalogue_and_packaged_checksum_drift_fail_closed(self) -> None:
        with _backend_fixture() as fixture:
            server = fixture.path("control-plane-kit-servers")
            (server / "products/hello_server/product.cpk.json").write_text(
                '{"changed":true}\n', encoding="utf-8"
            )
            (server / "src/control_plane_kit_servers/catalogue.json.sha256").write_text(
                f"{'0' * 64}  catalogue.json\n", encoding="ascii"
            )

            with self.assertRaises(BackendContractError) as raised:
                validate_backend(fixture.backend, fixture.contracts)

        self.assertIn("descriptor checksum mismatch", str(raised.exception))
        self.assertIn("packaged catalogue checksum mismatch", str(raised.exception))

    def test_missing_protocol_method_fails_closed(self) -> None:
        with _backend_fixture() as fixture:
            path = fixture.path("control-plane-kit-interpreters") / (
                "src/control_plane_kit_interpreters/docker/runtime.py"
            )
            path.write_text("class DockerRuntimeInterpreter:\n    pass\n", encoding="utf-8")

            with self.assertRaisesRegex(
                BackendContractError,
                "protocol implementation is missing: runtime-effect.execute",
            ):
                validate_backend(fixture.backend, fixture.contracts)

    def test_concrete_interpreter_and_transport_imports_in_operations_fail_closed(self) -> None:
        with _backend_fixture() as fixture:
            path = fixture.path("control-plane-kit") / (
                "control-plane-kit-operations/src/control_plane_kit_operations/service.py"
            )
            path.write_text(
                "import httpx\nfrom control_plane_kit_interpreters.docker import runtime\n",
                encoding="utf-8",
            )

            with self.assertRaises(BackendContractError) as raised:
                validate_backend(fixture.backend, fixture.contracts)

        self.assertIn("imports httpx", str(raised.exception))
        self.assertIn("imports control_plane_kit_interpreters.docker", str(raised.exception))

    def test_source_live_bypass_and_application_mock_fail_closed(self) -> None:
        with _backend_fixture() as fixture:
            acceptance = replace(
                fixture.contracts.acceptance[0],
                authoritative_caller="host-script",
                uses_application_mocks=True,
            )
            contracts = replace(fixture.contracts, acceptance=(acceptance,))

            with self.assertRaises(BackendContractError) as raised:
                validate_backend(fixture.backend, contracts)

        self.assertIn("bypasses cpk-server", str(raised.exception))
        self.assertIn("uses application mocks", str(raised.exception))

    def test_manifest_rejects_unknown_fields(self) -> None:
        root = Path(__file__).resolve().parents[2]
        raw = json.loads((root / "current-backend.contracts.json").read_text())
        raw["unexpected"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contracts.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(BackendContractError, "fields are inconsistent"):
                load_backend_contracts(path)

    def test_manifest_rejects_traversing_source_glob_and_duplicate_prefix(self) -> None:
        root = Path(__file__).resolve().parents[2]
        original = json.loads((root / "current-backend.contracts.json").read_text())
        cases = []
        traversing = json.loads(json.dumps(original))
        traversing["distributions"][0]["source_globs"] = ["../outside/**/*.py"]
        cases.append((traversing, "source glob is unsafe"))
        duplicate = json.loads(json.dumps(original))
        duplicate["distributions"][1]["module_prefixes"] = [
            "control_plane_kit_core"
        ]
        cases.append((duplicate, "module prefix inventory contains duplicates"))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contracts.json"
            for document, message in cases:
                with self.subTest(message=message):
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaisesRegex(BackendContractError, message):
                        load_backend_contracts(path)


class _Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.paths = {
            name: root / name
            for name in (
                "control-plane-kit",
                "control-plane-kit-interpreters",
                "control-plane-kit-servers",
                "control-plane-kit-secrets",
            )
        }
        self.backend = MaterializedBackend(root=root, repositories=self.paths)
        self.contracts = load_backend_contracts(
            Path(__file__).resolve().parents[2] / "current-backend.contracts.json"
        )

    def path(self, repository: str) -> Path:
        return self.paths[repository]


@contextmanager
def _backend_fixture():
    with tempfile.TemporaryDirectory() as directory:
        fixture = _Fixture(Path(directory))
        _populate_fixture(fixture)
        yield fixture


def _populate_fixture(fixture: _Fixture) -> None:
    coordination = fixture.path("control-plane-kit")
    interpreters = fixture.path("control-plane-kit-interpreters")
    secrets = fixture.path("control-plane-kit-secrets")
    servers = fixture.path("control-plane-kit-servers")

    _write(
        coordination / "control-plane-kit-core/pyproject.toml",
        _project("control-plane-kit-core", ()),
    )
    _write(
        coordination / "control-plane-kit-core/src/control_plane_kit_core/model.py",
        "class RuntimeEffectRequest:\n    pass\n",
    )
    _write(
        coordination / "control-plane-kit-core/src/control_plane_kit_core/secrets.py",
        "class AuthorizedSecretResolver:\n    def resolve(self): ...\n"
        "class SecretCustodian:\n    def store(self): ...\n    def revoke(self): ...\n",
    )
    _write(
        coordination / "control-plane-kit-operations/pyproject.toml",
        _project("control-plane-kit-operations", ("control-plane-kit-core>=0.1.0",)),
    )
    _write(
        coordination / "control-plane-kit-operations/src/control_plane_kit_operations/service.py",
        "from control_plane_kit_core.model import RuntimeEffectRequest\n",
    )
    _write(
        coordination / "control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py",
        "class RuntimeEffectInterpreter:\n    def execute(self): ...\n"
        "class RuntimeAuthorityAwareInterpreter:\n    def execute_with_authority(self): ...\n",
    )
    _write(
        coordination / "control-plane-kit-operations/src/control_plane_kit_operations/ingress_realization.py",
        "class IngressProviderInterpreter:\n    def create(self): ...\n    def teardown(self): ...\n",
    )
    _write(
        coordination / "control-plane-kit-operations/src/control_plane_kit_operations/gateway_probes.py",
        "class GatewayProbeDispatcher:\n    def dispatch(self): ...\n",
    )

    _write(
        interpreters / "pyproject.toml",
        _project(
            "control-plane-kit-interpreters",
            (
                _archive_dependency("control-plane-kit-core", "control-plane-kit", CORE_COMMIT),
            ),
            optional=(
                _archive_dependency(
                    "control-plane-kit-secrets", "control-plane-kit-secrets", SECRETS_COMMIT
                ),
            ),
        ),
    )
    _write(
        interpreters / "src/control_plane_kit_interpreters/docker/runtime.py",
        "from control_plane_kit_core.model import RuntimeEffectRequest\n"
        "class DockerRuntimeInterpreter:\n"
        "    def execute(self): ...\n"
        "    def execute_with_authority(self): ...\n",
    )
    _write(
        interpreters / "src/control_plane_kit_interpreters/cloudflare/client.py",
        "class CloudflareNamedIngressInterpreter:\n"
        "    def create(self): ...\n"
        "    def teardown(self): ...\n",
    )
    _write(
        interpreters / "src/control_plane_kit_interpreters/secret_provider/resolver.py",
        "class ControlPlaneKitSecretsResolver:\n    def resolve(self): ...\n",
    )
    _write(
        interpreters / "src/control_plane_kit_interpreters/secret_provider/custody.py",
        "class ControlPlaneKitSecretsCustodian:\n"
        "    def store(self): ...\n"
        "    def revoke(self): ...\n",
    )

    _write(secrets / "pyproject.toml", _project("control-plane-kit-secrets", ("cryptography>=42",)))
    _write(secrets / "src/control_plane_kit_secrets/store.py", "class EncryptedStore:\n    pass\n")

    _write(
        servers / "pyproject.toml",
        _project(
            "control-plane-kit-servers",
            (
                _archive_dependency("control-plane-kit-core", "control-plane-kit", CORE_COMMIT),
                _archive_dependency("control-plane-kit-operations", "control-plane-kit", CORE_COMMIT),
                _archive_dependency(
                    "control-plane-kit-interpreters",
                    "control-plane-kit-interpreters",
                    INTERPRETERS_COMMIT,
                ),
            ),
        ),
    )
    _write(
        servers / "src/control_plane_kit_servers/catalogue_support.py",
        "from control_plane_kit_core.model import RuntimeEffectRequest\n",
    )
    _write(
        servers / "products/cpk_server/src/control_plane_kit_servers_cpk_server/server.py",
        "from control_plane_kit_core.model import RuntimeEffectRequest\n"
        "from control_plane_kit_operations.service import RuntimeEffectRequest as Request\n"
        "from control_plane_kit_interpreters.docker.runtime import DockerRuntimeInterpreter\n"
        "class _SignedGatewayProbeDispatcher:\n    def dispatch(self): ...\n",
    )
    _write(
        servers / "products/cpk_local_gateway/Dockerfile",
        _archive_dependency("control-plane-kit-core", "control-plane-kit", CORE_COMMIT),
    )
    _write(
        servers / "products/cpk_server/Dockerfile",
        _archive_dependency("control-plane-kit-core", "control-plane-kit", CORE_COMMIT)
        + "\n"
        + _archive_dependency("control-plane-kit-operations", "control-plane-kit", CORE_COMMIT)
        + "\n"
        + _archive_dependency(
            "control-plane-kit-interpreters", "control-plane-kit-interpreters", INTERPRETERS_COMMIT
        ),
    )
    _write(
        servers / "products/secrets_server/Dockerfile",
        _archive_dependency("control-plane-kit-secrets", "control-plane-kit-secrets", SECRETS_COMMIT),
    )
    _write(
        servers / "scripts/cpk_server_image_smoke.sh",
        "#!/bin/sh\n# products/cpk_server/Dockerfile\n"
        "# Authorization: Bearer\n# Mcp-Method: tools/call\n# /workspaces\n",
    )
    _write(servers / "scripts/docker_residue_audit.sh", "#!/bin/sh\nexit 0\n")

    descriptor_path = servers / "products/hello_server/product.cpk.json"
    _write(descriptor_path, '{"schema":"cpk.product.v1","name":"hello-server"}\n')
    descriptor_sha = hashlib.sha256(descriptor_path.read_bytes()).hexdigest()
    coordinate_product = {
        "product_id": "hello-server",
        "owner_directory": "products/hello_server",
        "descriptor_path": "products/hello_server/product.cpk.json",
        "source_commit": "4" * 40,
        "image": {
            "registry": "ghcr.io",
            "repository": "openj92/hello-server",
            "tag": "test",
            "digest": "sha256:" + "5" * 64,
        },
    }
    coordinates = {
        "schema": "cpk-servers.coordinates",
        "upstreams": {
            "control_plane_kit_commit": CORE_COMMIT,
            "control_plane_kit_interpreters_commit": INTERPRETERS_COMMIT,
            "control_plane_kit_secrets_commit": SECRETS_COMMIT,
        },
        "products": [coordinate_product],
    }
    catalogue_product = {
        "product_id": "hello-server",
        "owner_directory": "products/hello_server",
        "descriptor_path": "products/hello_server/product.cpk.json",
        "descriptor_sha256": descriptor_sha,
        "source_commit": "4" * 40,
        "image_ref": "ghcr.io/openj92/hello-server:test",
        "image_digest": "sha256:" + "5" * 64,
        "status": "completed",
    }
    catalogue = {"schema": "cpk-servers.descriptor-catalogue", "products": [catalogue_product]}
    packaged = {"products": [catalogue_product], "schema": "cpk-servers.descriptor-catalogue"}
    _write(servers / "coordinates/server-products.json", json.dumps(coordinates) + "\n")
    _write(servers / "catalogue/products.json", json.dumps(catalogue) + "\n")
    packaged_path = servers / "src/control_plane_kit_servers/catalogue.json"
    _write(packaged_path, json.dumps(packaged, sort_keys=True) + "\n")
    checksum = hashlib.sha256(packaged_path.read_bytes()).hexdigest()
    _write(
        servers / "src/control_plane_kit_servers/catalogue.json.sha256",
        f"{checksum}  catalogue.json\n",
    )


def _project(
    name: str,
    dependencies: tuple[str, ...],
    *,
    optional: tuple[str, ...] = (),
) -> str:
    lines = [
        "[project]",
        f'name = "{name}"',
        'version = "0.1.0"',
        "dependencies = [",
        *(f'  "{value}",' for value in dependencies),
        "]",
    ]
    if optional:
        lines.extend(
            [
                "[project.optional-dependencies]",
                "test = [",
                *(f'  "{value}",' for value in optional),
                "]",
            ]
        )
    return "\n".join(lines) + "\n"


def _archive_dependency(name: str, repository: str, commit: str) -> str:
    return f"{name} @ https://github.com/OpenJ92/{repository}/archive/{commit}.zip"


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
