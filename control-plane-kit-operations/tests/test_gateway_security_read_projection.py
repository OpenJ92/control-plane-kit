from __future__ import annotations

import ast
import importlib
from inspect import signature
import json
from pathlib import Path
import unittest

import control_plane_kit_operations as operations
import control_plane_kit_operations.read_services as read_services
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.gateway_delegation import (
    GatewayProbeAccessPath,
    GatewayProbeCommandKind,
)
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyNotFound,
    RegisteredDelegationSigningKey,
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.gateway_probes import (
    GatewayProbeAttempt,
    GatewayProbeAttemptStatus,
    GatewayProbeError,
)
from control_plane_kit_operations.read_pages import (
    DelegationKeyReadCursor,
    EpochReadCursor,
    ReadCollection,
    ReadPage,
    ReadPageCandidate,
    ReadPageRequest,
    WorkspaceReadScope,
)
from control_plane_kit_operations.records import BoundedEvidence, WorkspaceRecord

from test_read_services_package import _local_module_imports


_OWNER_MODULE = "control_plane_kit_operations.read_services.gateway_security"


def _request(collection: ReadCollection) -> ReadPageRequest:
    return ReadPageRequest(collection, WorkspaceReadScope("workspace-a"), 1)


def _probe(probe_id: str = "probe-a", *, workspace_id: str = "workspace-a"):
    return GatewayProbeAttempt(
        probe_id=probe_id,
        workspace_id=workspace_id,
        request_id=f"request-{probe_id}",
        actor_id="operator-a",
        current_graph_id="graph-a",
        gateway_node_id="gateway-a",
        gateway_runtime_id="runtime-a",
        access_path=GatewayProbeAccessPath.RUNTIME_PRIVATE,
        probe_kind=GatewayProbeCommandKind.HTTP_STATUS,
        target_id="hello.http",
        request_digest="1" * 64,
        issuer="cpk-server-a",
        key_id="key-a",
        audience="gateway:workspace-a:gateway-a",
        grant_jti=f"grant-{probe_id}",
        issued_at=100,
        expires_at=160,
        status=GatewayProbeAttemptStatus.SUCCEEDED,
        requested_at="2026-08-13T12:00:00Z",
        intent_fingerprint="2" * 64,
        evidence=BoundedEvidence(),
    )


def _public_key(key_id: str) -> DelegationPublicKey:
    return DelegationPublicKey(
        key_id,
        DelegationKeyAlgorithm.ED25519,
        "-----BEGIN PUBLIC KEY-----\n"
        f"{key_id}-public-verification-material\n"
        "-----END PUBLIC KEY-----\n",
    )


def _key(
    key_id: str,
    status: RegisteredDelegationSigningKeyStatus,
) -> RegisteredDelegationSigningKey:
    active = status is RegisteredDelegationSigningKeyStatus.ACTIVE
    return RegisteredDelegationSigningKey(
        registration_id=f"registration-{key_id}",
        workspace_id="workspace-a",
        purpose=DelegationKeyPurpose.GATEWAY_PROBE,
        issuer="cpk-server-a",
        public_key=_public_key(key_id),
        private_key_reference=SecretReference(
            f"secret://delegation-private/workspace-a/{key_id}"
        ),
        admitted_by="operator-a",
        admitted_at="2026-08-13T12:00:00Z",
        status=status,
        activated_by="operator-a" if active else None,
        activated_at="2026-08-13T12:01:00Z" if active else None,
    )


class _WorkspaceCapability:
    def __init__(
        self,
        trace: list[object],
        *,
        failure: BaseException | None = None,
    ) -> None:
        self._trace = trace
        self._failure = failure

    def __call__(self, workspace_id: str) -> WorkspaceRecord:
        self._trace.append(("workspace", workspace_id))
        if self._failure is not None:
            raise self._failure
        return WorkspaceRecord(workspace_id, "Workspace A")


class _ProbeStore:
    def __init__(
        self,
        trace: list[object],
        attempt: GatewayProbeAttempt | None = None,
        *,
        failure: BaseException | None = None,
    ) -> None:
        self._trace = trace
        self._attempt = attempt or _probe()
        self._failure = failure

    def page(self, request: ReadPageRequest) -> ReadPage[GatewayProbeAttempt]:
        self._trace.append(("probe-page", request))
        if self._failure is not None:
            raise self._failure
        return ReadPage.from_candidates(
            request,
            (
                ReadPageCandidate(
                    self._attempt,
                    EpochReadCursor(
                        request.collection,
                        request.scope,
                        self._attempt.issued_at,
                        self._attempt.probe_id,
                    ),
                ),
            ),
        )

    def get(self, probe_id: str) -> GatewayProbeAttempt:
        self._trace.append(("probe-get", probe_id))
        if self._failure is not None:
            raise self._failure
        return self._attempt


class _MalformedProbe:
    def __init__(self, failure: BaseException) -> None:
        self._failure = failure
        self.workspace_id = "workspace-a"

    def descriptor(self) -> object:
        raise self._failure


class _MalformedKeyRecord:
    registration_id = "registration-malformed"
    purpose = DelegationKeyPurpose.GATEWAY_PROBE
    issuer = "cpk-server-a"
    key_id = "malformed"


class _KeyStore:
    def __init__(
        self,
        trace: list[object],
        keys: tuple[RegisteredDelegationSigningKey, ...] | None = None,
        *,
        active: RegisteredDelegationSigningKey | None = None,
        verification_keys: tuple[RegisteredDelegationSigningKey, ...] | None = None,
        failure: BaseException | None = None,
    ) -> None:
        self._trace = trace
        self._keys = keys or (
            _key("key-a", RegisteredDelegationSigningKeyStatus.VERIFY_ONLY),
            _key("key-b", RegisteredDelegationSigningKeyStatus.ACTIVE),
        )
        self._active = active
        self._verification_keys = verification_keys
        self._failure = failure

    def _result(self) -> tuple[RegisteredDelegationSigningKey, ...]:
        if self._failure is not None:
            raise self._failure
        return self._keys

    def workspace_page(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[RegisteredDelegationSigningKey]:
        self._trace.append(("key-page", request))
        values = self._result()
        return ReadPage.from_candidates(
            request,
            tuple(
                ReadPageCandidate(
                    value,
                    DelegationKeyReadCursor(
                        request.collection,
                        request.scope,
                        value.purpose,
                        value.issuer,
                        value.key_id,
                    ),
                )
                for value in values[:1]
            ),
        )

    def require_unambiguous_active(
        self,
        workspace_id: str,
        purpose: DelegationKeyPurpose,
    ) -> RegisteredDelegationSigningKey:
        self._trace.append(("key-active", workspace_id, purpose))
        values = self._result()
        if self._active is not None:
            return self._active
        return next(
            value
            for value in values
            if value.status is RegisteredDelegationSigningKeyStatus.ACTIVE
        )

    def list_for_verification(
        self,
        workspace_id: str,
        purpose: DelegationKeyPurpose,
        issuer: str,
    ) -> tuple[RegisteredDelegationSigningKey, ...]:
        self._trace.append(("key-verification", workspace_id, purpose, issuer))
        values = self._result()
        return self._verification_keys if self._verification_keys is not None else values


class _ForbiddenStore:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"unrelated store capability was acquired: {name}")


def _resolved_module_imports(tree: ast.AST) -> set[str]:
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            package = ("control_plane_kit_operations", "read_services")
            retained = len(package) - node.level + 1
            base = ".".join(package[:retained])
            resolved = f"{base}.{node.module}" if node.module else base
            imported.add(resolved)
            if not node.module:
                imported.update(f"{base}.{alias.name}" for alias in node.names)
            continue
        if not node.module:
            continue
        imported.add(node.module)
        if node.module == "control_plane_kit_operations":
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


class _GatewaySecuritySpy:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.results = {
            "gateway_probe_timeline": object(),
            "gateway_probe_detail": object(),
            "delegation_signing_keys": object(),
            "gateway_verifier_configuration": object(),
        }

    def gateway_probe_timeline(self, request: object) -> object:
        self.calls.append(("gateway_probe_timeline", request))
        return self.results["gateway_probe_timeline"]

    def gateway_probe_detail(self, workspace_id: str, probe_id: str) -> object:
        self.calls.append(("gateway_probe_detail", workspace_id, probe_id))
        return self.results["gateway_probe_detail"]

    def delegation_signing_keys(self, request: object) -> object:
        self.calls.append(("delegation_signing_keys", request))
        return self.results["delegation_signing_keys"]

    def gateway_verifier_configuration(
        self,
        workspace_id: str,
        gateway_node_id: str,
    ) -> object:
        self.calls.append(
            (
                "gateway_verifier_configuration",
                workspace_id,
                gateway_node_id,
            )
        )
        return self.results["gateway_verifier_configuration"]


class GatewaySecurityReadProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.module = importlib.import_module(_OWNER_MODULE)
        except ModuleNotFoundError as error:
            self.fail(f"gateway security read projection is absent: {error.name}")

    def _projection(
        self,
        trace: list[object],
        *,
        workspace_failure: BaseException | None = None,
        probe_store: object | None = None,
        key_store: object | None = None,
    ) -> object:
        return self.module._GatewaySecurityReadProjection(
            _WorkspaceCapability(trace, failure=workspace_failure),
            gateway_probe_store=probe_store,
            delegation_signing_key_store=key_store,
        )

    def test_probe_page_and_detail_work_without_delegation_store(self) -> None:
        trace: list[object] = []
        store = _ProbeStore(trace)
        projection = self._projection(trace, probe_store=store)
        request = _request(ReadCollection.GATEWAY_PROBES)

        page = projection.gateway_probe_timeline(request)
        detail = projection.gateway_probe_detail("workspace-a", "probe-a")

        self.assertEqual(page.items, (_probe().descriptor(),))
        self.assertEqual(detail.payload, {"gateway_probe": _probe().descriptor()})
        self.assertEqual(
            trace,
            [
                ("workspace", "workspace-a"),
                ("probe-page", request),
                ("workspace", "workspace-a"),
                ("probe-get", "probe-a"),
            ],
        )

    def test_key_inventory_and_verifier_work_without_probe_store(self) -> None:
        trace: list[object] = []
        keys = (
            _key("key-a", RegisteredDelegationSigningKeyStatus.VERIFY_ONLY),
            _key("key-b", RegisteredDelegationSigningKeyStatus.ACTIVE),
        )
        projection = self._projection(trace, key_store=_KeyStore(trace, keys))
        request = _request(ReadCollection.DELEGATION_SIGNING_KEYS)

        page = projection.delegation_signing_keys(request)
        detail = projection.gateway_verifier_configuration(
            "workspace-a",
            "gateway-a",
        )

        inventory = page.items[0]
        self.assertEqual(
            inventory,
            {
                "registration_id": "registration-key-a",
                "workspace_id": "workspace-a",
                "purpose": "gateway-probe",
                "issuer": "cpk-server-a",
                "key_id": "key-a",
                "algorithm": "ed25519",
                "fingerprint_sha256": keys[0].public_key.fingerprint_sha256,
                "admitted_by": "operator-a",
                "admitted_at": "2026-08-13T12:00:00Z",
                "status": "verify-only",
                "activated_by": None,
                "activated_at": None,
                "retired_by": None,
                "retired_at": None,
                "revoked_by": None,
                "revoked_at": None,
            },
        )

        configuration = detail.payload["gateway_verifier_configuration"]
        self.assertEqual(
            configuration,
            {
                "issuer": "cpk-server-a",
                "audience": "gateway:workspace-a:gateway-a",
                "gateway_node_id": "gateway-a",
                "public_keys": [
                    {
                        **value.public_key.descriptor(),
                        "public_key_pem": value.public_key.public_key_pem,
                    }
                    for value in keys
                ],
                "public_environment": [
                    {
                        "kind": "public-static",
                        "name": "CPK_GATEWAY_PROBE_AUDIENCE",
                        "value": "gateway:workspace-a:gateway-a",
                    },
                    {
                        "kind": "public-static",
                        "name": "CPK_GATEWAY_PROBE_ISSUER",
                        "value": "cpk-server-a",
                    },
                    {
                        "kind": "public-static",
                        "name": "CPK_GATEWAY_PROBE_NODE_ID",
                        "value": "gateway-a",
                    },
                    {
                        "kind": "public-static",
                        "name": "CPK_GATEWAY_PROBE_VERIFICATION_KEYS_JSON",
                        "value": json.dumps(
                            {
                                value.key_id: value.public_key.public_key_pem
                                for value in keys
                            },
                            ensure_ascii=True,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    },
                    {
                        "kind": "public-static",
                        "name": "CPK_GATEWAY_PROBE_VERIFIER",
                        "value": "ed25519",
                    },
                ],
            },
        )
        for private_reference in (
            "secret://delegation-private/workspace-a/key-a",
            "secret://delegation-private/workspace-a/key-b",
        ):
            for rendered in (
                str(inventory),
                repr(inventory),
                str(configuration),
                repr(configuration),
            ):
                self.assertNotIn(private_reference, rendered)
        self.assertEqual(
            trace,
            [
                ("workspace", "workspace-a"),
                ("key-page", request),
                ("workspace", "workspace-a"),
                (
                    "key-active",
                    "workspace-a",
                    DelegationKeyPurpose.GATEWAY_PROBE,
                ),
                (
                    "key-verification",
                    "workspace-a",
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    "cpk-server-a",
                ),
            ],
        )

    def test_each_missing_capability_fails_only_after_workspace_admission(self) -> None:
        cases = (
            (
                "gateway_probe_timeline",
                (_request(ReadCollection.GATEWAY_PROBES),),
                "gateway probe store is not configured",
            ),
            (
                "gateway_probe_detail",
                ("workspace-a", "probe-a"),
                "gateway probe store is not configured",
            ),
            (
                "delegation_signing_keys",
                (_request(ReadCollection.DELEGATION_SIGNING_KEYS),),
                "delegation signing key store is not configured",
            ),
            (
                "gateway_verifier_configuration",
                ("workspace-a", "gateway-a"),
                "delegation signing key store is not configured",
            ),
        )
        for method_name, arguments, message in cases:
            with self.subTest(method=method_name):
                trace: list[object] = []
                projection = self._projection(trace)
                with self.assertRaises(operations.ReadModelError) as caught:
                    getattr(projection, method_name)(*arguments)
                self.assertEqual(str(caught.exception), message)
                self.assertEqual(trace, [("workspace", "workspace-a")])

    def test_missing_workspace_precedes_store_configuration_and_access(self) -> None:
        missing = operations.ReadModelError("workspace is unavailable")
        cases = (
            ("gateway_probe_timeline", (_request(ReadCollection.GATEWAY_PROBES),)),
            ("gateway_probe_detail", ("workspace-a", "probe-a")),
            (
                "delegation_signing_keys",
                (_request(ReadCollection.DELEGATION_SIGNING_KEYS),),
            ),
            ("gateway_verifier_configuration", ("workspace-a", "gateway-a")),
        )
        for method_name, arguments in cases:
            with self.subTest(method=method_name):
                trace: list[object] = []
                projection = self._projection(
                    trace,
                    workspace_failure=missing,
                    probe_store=_ForbiddenStore(),
                    key_store=_ForbiddenStore(),
                )
                with self.assertRaises(operations.ReadModelError) as caught:
                    getattr(projection, method_name)(*arguments)
                self.assertIs(caught.exception, missing)
                self.assertEqual(trace, [("workspace", "workspace-a")])

    def test_missing_and_foreign_probe_details_are_cause_free_equivalents(self) -> None:
        failures: list[operations.ReadModelError] = []
        for store in (
            _ProbeStore([], failure=KeyError("candidate-private-provider-detail")),
            _ProbeStore([], _probe(workspace_id="workspace-b")),
        ):
            projection = self._projection([], probe_store=store)
            with self.assertRaises(operations.ReadModelError) as caught:
                projection.gateway_probe_detail("workspace-a", "probe-a")
            failures.append(caught.exception)

        self.assertEqual(
            [str(error) for error in failures],
            ["missing gateway probe 'probe-a'"] * 2,
        )
        for error in failures:
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            self.assertNotIn("candidate-private-provider-detail", repr(error))

    def test_verifier_failures_keep_one_bounded_public_category(self) -> None:
        cases = (
            DelegationSigningKeyNotFound("candidate-private-key-identity"),
            GatewayProbeError("candidate-private-verifier-detail"),
        )
        for failure in cases:
            with self.subTest(failure=type(failure).__name__):
                projection = self._projection(
                    [],
                    key_store=_KeyStore([], failure=failure),
                )
                with self.assertRaises(operations.ReadModelError) as caught:
                    projection.gateway_verifier_configuration(
                        "workspace-a",
                        "gateway-a",
                    )
                self.assertEqual(
                    str(caught.exception),
                    "gateway verifier configuration is unavailable",
                )
                self.assertNotIn("candidate-private", str(caught.exception))

    def test_verifier_rejects_a_verification_set_without_an_active_member(
        self,
    ) -> None:
        active = _key("key-b", RegisteredDelegationSigningKeyStatus.ACTIVE)
        verification_keys = (
            _key("key-a", RegisteredDelegationSigningKeyStatus.VERIFY_ONLY),
            _key("key-c", RegisteredDelegationSigningKeyStatus.VERIFY_ONLY),
        )
        projection = self._projection(
            [],
            key_store=_KeyStore(
                [],
                (active,),
                active=active,
                verification_keys=verification_keys,
            ),
        )

        with self.assertRaises(operations.ReadModelError) as caught:
            projection.gateway_verifier_configuration(
                "workspace-a",
                "gateway-a",
            )

        self.assertEqual(
            str(caught.exception),
            "gateway verifier configuration is unavailable",
        )
        self.assertIs(type(caught.exception.__cause__), GatewayProbeError)
        self.assertEqual(
            str(caught.exception.__cause__),
            "gateway verifier set has no active key",
        )
        self.assertIs(caught.exception.__context__, caught.exception.__cause__)

    def test_malformed_inventory_is_bounded_and_unexpected_probe_failure_is_raw(
        self,
    ) -> None:
        malformed_key_store = _KeyStore([], (_MalformedKeyRecord(),))
        projection = self._projection([], key_store=malformed_key_store)
        with self.assertRaises(operations.ReadModelError) as caught:
            projection.delegation_signing_keys(
                _request(ReadCollection.DELEGATION_SIGNING_KEYS)
            )
        self.assertEqual(
            str(caught.exception),
            "delegation signing key record cannot be projected",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn("registration-malformed", repr(caught.exception))

        failure = RuntimeError("unexpected-probe-descriptor-failure")
        projection = self._projection(
            [],
            probe_store=_ProbeStore([], _MalformedProbe(failure)),
        )
        with self.assertRaises(RuntimeError) as raw:
            projection.gateway_probe_detail("workspace-a", "probe-a")
        self.assertIs(raw.exception, failure)


class GatewaySecurityReadProjectionStructureTests(unittest.TestCase):
    def _module(self) -> object:
        try:
            return importlib.import_module(_OWNER_MODULE)
        except ModuleNotFoundError as error:
            self.fail(f"gateway security read projection is absent: {error.name}")

    def test_import_parser_resolves_absolute_and_relative_forms(self) -> None:
        hostile_imports = (
            (
                "import control_plane_kit_operations.postgres.stores as stores\n",
                "control_plane_kit_operations.postgres.stores",
            ),
            (
                "from control_plane_kit_operations import cpk_server as server\n",
                "control_plane_kit_operations.cpk_server",
            ),
            (
                "from control_plane_kit_operations.postgres import stores\n",
                "control_plane_kit_operations.postgres",
            ),
            (
                "from .. import postgres as stores\n",
                "control_plane_kit_operations.postgres",
            ),
            (
                "from .. import cpk_server as server\n",
                "control_plane_kit_operations.cpk_server",
            ),
            (
                "from ..postgres import stores\n",
                "control_plane_kit_operations.postgres",
            ),
            (
                "from . import instance as facade\n",
                "control_plane_kit_operations.read_services.instance",
            ),
        )
        for source, expected in hostile_imports:
            with self.subTest(source=source):
                self.assertIn(expected, _resolved_module_imports(ast.parse(source)))

    def test_owner_and_facade_have_exact_method_partition(self) -> None:
        module = self._module()
        owner = module._GatewaySecurityReadProjection
        methods = {
            "gateway_probe_timeline": ("self", "request"),
            "gateway_probe_detail": ("self", "workspace_id", "probe_id"),
            "delegation_signing_keys": ("self", "request"),
            "gateway_verifier_configuration": (
                "self",
                "workspace_id",
                "gateway_node_id",
            ),
        }
        self.assertEqual(
            {name for name in vars(owner) if not name.startswith("_")},
            set(methods),
        )
        facade_path = Path(importlib.import_module(
            "control_plane_kit_operations.read_services.instance"
        ).__file__)
        tree = ast.parse(facade_path.read_text())
        facade = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "InstanceReadService"
        )
        for method_name, parameters in methods.items():
            self.assertEqual(
                tuple(signature(getattr(owner, method_name)).parameters),
                parameters,
            )
            self.assertEqual(
                tuple(
                    signature(
                        getattr(operations.InstanceReadService, method_name)
                    ).parameters
                ),
                parameters,
            )
            method = next(
                node
                for node in facade.body
                if isinstance(node, ast.FunctionDef) and node.name == method_name
            )
            calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
            self.assertEqual(len(calls), 1)
            call = calls[0]
            self.assertIsInstance(call.func, ast.Attribute)
            self.assertEqual(call.func.attr, method_name)
            self.assertIsInstance(call.func.value, ast.Attribute)
            self.assertEqual(call.func.value.attr, "_gateway_security")

    def test_facade_preserves_exact_argument_and_return_identity(self) -> None:
        facade = object.__new__(operations.InstanceReadService)
        spy = _GatewaySecuritySpy()
        facade._gateway_security = spy
        request = object()
        cases = (
            ("gateway_probe_timeline", (request,)),
            ("gateway_probe_detail", ("workspace-a", "probe-a")),
            ("delegation_signing_keys", (request,)),
            (
                "gateway_verifier_configuration",
                ("workspace-a", "gateway-a"),
            ),
        )
        for method_name, arguments in cases:
            with self.subTest(method=method_name):
                result = getattr(facade, method_name)(*arguments)
                self.assertIs(result, spy.results[method_name])
                self.assertEqual(spy.calls[-1], (method_name, *arguments))

    def test_facade_retains_owner_not_raw_security_stores(self) -> None:
        instance = operations.InstanceReadService(
            workspace_store=object(),
            graph_topology_store=object(),
            gateway_probe_store=object(),
            delegation_signing_key_store=object(),
        )
        self.assertIn("_gateway_security", vars(instance))
        self.assertNotIn("_gateway_probe_store", vars(instance))
        self.assertNotIn("_delegation_signing_key_store", vars(instance))

    def test_owner_is_private_and_has_no_sibling_projection_or_outer_edges(self) -> None:
        module = self._module()
        self.assertFalse(hasattr(read_services, "_GatewaySecurityReadProjection"))
        self.assertNotIn("_GatewaySecurityReadProjection", operations.__all__)
        path = Path(module.__file__)
        tree = ast.parse(path.read_text())
        module_names = {
            candidate.stem
            for candidate in path.parent.glob("*.py")
            if candidate.is_file()
        }
        self.assertEqual(
            _local_module_imports(tree, module_names),
            {"errors", "models", "protocols"},
        )
        imported = _resolved_module_imports(tree)
        for forbidden in (
            "control_plane_kit_operations.cpk_server",
            "control_plane_kit_operations.postgres",
            "control_plane_kit_operations.read_services.instance",
            "control_plane_kit_operations.read_services.authority_secrets",
            "control_plane_kit_operations.read_services.observations",
            "control_plane_kit_operations.read_services.operations_history",
            "control_plane_kit_operations.read_services.workspace_graph",
        ):
            self.assertFalse(any(
                name == forbidden or name.startswith(f"{forbidden}.")
                for name in imported
            ))


if __name__ == "__main__":
    unittest.main()
