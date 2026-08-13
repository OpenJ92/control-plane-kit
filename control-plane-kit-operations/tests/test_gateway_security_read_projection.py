from __future__ import annotations

import ast
import importlib
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
    EpochReadCursor,
    IdentityReadCursor,
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


class _KeyStore:
    def __init__(
        self,
        trace: list[object],
        keys: tuple[RegisteredDelegationSigningKey, ...] | None = None,
        *,
        failure: BaseException | None = None,
    ) -> None:
        self._trace = trace
        self._keys = keys or (
            _key("key-a", RegisteredDelegationSigningKeyStatus.VERIFY_ONLY),
            _key("key-b", RegisteredDelegationSigningKeyStatus.ACTIVE),
        )
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
                    IdentityReadCursor(
                        request.collection,
                        request.scope,
                        value.registration_id,
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
        return self._result()


class _ForbiddenStore:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"unrelated store capability was acquired: {name}")


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
            _key("key-b", RegisteredDelegationSigningKeyStatus.ACTIVE),
            _key("key-a", RegisteredDelegationSigningKeyStatus.VERIFY_ONLY),
        )
        projection = self._projection(trace, key_store=_KeyStore(trace, keys))
        request = _request(ReadCollection.DELEGATION_SIGNING_KEYS)

        page = projection.delegation_signing_keys(request)
        detail = projection.gateway_verifier_configuration(
            "workspace-a",
            "gateway-a",
        )

        inventory = page.items[0]
        self.assertEqual(inventory["key_id"], "key-b")
        self.assertNotIn("private_key_reference", inventory)
        self.assertNotIn("public_key_pem", inventory)
        self.assertNotIn("BEGIN PUBLIC KEY", repr(inventory))

        configuration = detail.payload["gateway_verifier_configuration"]
        self.assertEqual(configuration["issuer"], "cpk-server-a")
        self.assertEqual(configuration["audience"], "gateway:workspace-a:gateway-a")
        self.assertEqual(configuration["gateway_node_id"], "gateway-a")
        self.assertEqual(
            [value["key_id"] for value in configuration["public_keys"]],
            ["key-a", "key-b"],
        )
        self.assertTrue(
            all("BEGIN PUBLIC KEY" in value["public_key_pem"] for value in configuration["public_keys"])
        )
        self.assertEqual(
            [binding["name"] for binding in configuration["public_environment"]],
            sorted(binding["name"] for binding in configuration["public_environment"]),
        )
        self.assertNotIn("private_key_reference", repr(configuration))
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


class GatewaySecurityReadProjectionStructureTests(unittest.TestCase):
    def _module(self) -> object:
        try:
            return importlib.import_module(_OWNER_MODULE)
        except ModuleNotFoundError as error:
            self.fail(f"gateway security read projection is absent: {error.name}")

    def test_owner_and_facade_have_exact_method_partition(self) -> None:
        module = self._module()
        owner = module._GatewaySecurityReadProjection
        methods = (
            "gateway_probe_timeline",
            "gateway_probe_detail",
            "delegation_signing_keys",
            "gateway_verifier_configuration",
        )
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
        for method_name in methods:
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
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
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
