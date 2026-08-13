from __future__ import annotations

import ast
import importlib
from pathlib import Path
import unittest

import control_plane_kit_operations.read_services as read_services
from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_operations import ReadModelError
from control_plane_kit_operations.read_pages import (
    IdentityReadCursor,
    ReadCollection,
    ReadPage,
    ReadPageCandidate,
    ReadPageRequest,
    WorkspaceReadScope,
)
from control_plane_kit_operations.records import WorkspaceRecord
from control_plane_kit_operations.secret_providers import (
    RegisteredSecretProvider,
    RegisteredSecretReference,
    SecretProviderKind,
)

from test_read_services_package import _local_module_imports


class _DescriptorRecord:
    def __init__(self, identity: str) -> None:
        self.identity = identity

    def descriptor(self) -> dict[str, object]:
        return {
            "identity": self.identity,
            "public_label": "visible",
            "address": "10.0.0.8",
            "credential_reference": "secret://private/candidate",
        }


def _provider() -> RegisteredSecretProvider:
    return RegisteredSecretProvider(
        registration_id="provider-registration-a",
        workspace_id="workspace-a",
        provider_id=SecretProviderId("workspace-secrets"),
        provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
        display_name="Workspace secrets",
        endpoint_reference=SecretProviderEndpointReference("workspace-secrets"),
        credential_reference=SecretReference(
            "secret://bootstrap/workspace-secrets/client-token"
        ),
        allowed_reference_prefixes=(
            SecretReference("secret://workspace-secrets/workspace-a"),
        ),
        allowed_intents=(SecretUseIntent.POSTGRES_PASSWORD,),
        admitted_by="operator-a",
        admitted_at="2026-08-13T12:00:00Z",
    )


def _reference() -> RegisteredSecretReference:
    return RegisteredSecretReference(
        registration_id="reference-registration-a",
        workspace_id="workspace-a",
        reference=SecretReference(
            "secret://workspace-secrets/workspace-a/postgres/password"
        ),
        provider_registration_id="provider-registration-a",
        allowed_intents=(SecretUseIntent.POSTGRES_PASSWORD,),
        admitted_by="operator-a",
        admitted_at="2026-08-13T12:01:00Z",
    )


def _request(collection: ReadCollection) -> ReadPageRequest:
    return ReadPageRequest(
        collection,
        WorkspaceReadScope("workspace-a"),
        1,
    )


def _page(request: ReadPageRequest, value: object) -> ReadPage[object]:
    return ReadPage.from_candidates(
        request,
        (
            ReadPageCandidate(
                value,
                IdentityReadCursor(
                    request.collection,
                    request.scope,
                    "item-a",
                ),
            ),
        ),
    )


class _WorkspaceCapability:
    def __init__(self, trace: list[object]) -> None:
        self._trace = trace

    def __call__(self, workspace_id: str) -> WorkspaceRecord:
        self._trace.append(("workspace", workspace_id))
        return WorkspaceRecord(workspace_id, "Workspace A")


class _FamilyStore:
    def __init__(self, trace: list[object], value: object) -> None:
        self._trace = trace
        self._value = value

    def active_page(self, request: ReadPageRequest) -> ReadPage[object]:
        self._trace.append(("active_page", request))
        return _page(request, self._value)

    def get(self, workspace_id: str, reference: object) -> object:
        self._trace.append(("get", workspace_id, reference))
        return self._value

    def get_active(self, workspace_id: str, provider_id: object) -> object:
        self._trace.append(("get_active", workspace_id, provider_id))
        return self._value

    def get_by_registration(
        self,
        workspace_id: str,
        registration_id: str,
    ) -> object:
        self._trace.append(
            ("get_by_registration", workspace_id, registration_id)
        )
        return self._value


_STORE_PARAMETERS = (
    "runtime_authority_store",
    "runtime_authority_delivery_store",
    "ingress_authority_store",
    "secret_provider_store",
    "secret_reference_store",
)


class AuthoritySecretReadProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.module = importlib.import_module(
                "control_plane_kit_operations.read_services.authority_secrets"
            )
        except ModuleNotFoundError as error:
            self.fail(f"authority/secret read projection is absent: {error.name}")

    def _projection(
        self,
        trace: list[object],
        **stores: object,
    ) -> object:
        arguments = {name: None for name in _STORE_PARAMETERS}
        arguments.update(stores)
        return self.module._AuthoritySecretReadProjection(
            _WorkspaceCapability(trace),
            **arguments,
        )

    def test_each_configured_page_capability_works_with_all_siblings_absent(
        self,
    ) -> None:
        cases = (
            (
                "runtime_authority_store",
                "runtime_authorities",
                ReadCollection.RUNTIME_AUTHORITIES,
                _DescriptorRecord("runtime-a"),
                "runtime-a",
            ),
            (
                "runtime_authority_delivery_store",
                "runtime_authority_deliveries",
                ReadCollection.RUNTIME_AUTHORITY_DELIVERIES,
                _DescriptorRecord("delivery-a"),
                "delivery-a",
            ),
            (
                "ingress_authority_store",
                "ingress_authorities",
                ReadCollection.INGRESS_AUTHORITIES,
                _DescriptorRecord("ingress-a"),
                "ingress-a",
            ),
            (
                "secret_provider_store",
                "secret_providers",
                ReadCollection.SECRET_PROVIDERS,
                _provider(),
                "provider-registration-a",
            ),
            (
                "secret_reference_store",
                "secret_references",
                ReadCollection.SECRET_REFERENCES,
                _reference(),
                "reference-registration-a",
            ),
        )
        for store_name, method_name, collection, value, identity in cases:
            with self.subTest(method=method_name):
                trace: list[object] = []
                store = _FamilyStore(trace, value)
                projection = self._projection(trace, **{store_name: store})
                request = _request(collection)

                page = getattr(projection, method_name)(request)

                self.assertIs(page.request, request)
                self.assertEqual(
                    trace,
                    [("workspace", "workspace-a"), ("active_page", request)],
                )
                rendered = page.items[0]
                if isinstance(value, _DescriptorRecord):
                    self.assertEqual(rendered["identity"], identity)
                    self.assertEqual(rendered["public_label"], "visible")
                    self.assertEqual(rendered["address"], "<redacted>")
                    self.assertEqual(
                        rendered["credential_reference"],
                        "<redacted>",
                    )
                else:
                    self.assertEqual(rendered["registration_id"], identity)

    def test_each_absent_store_keeps_its_exact_independent_error(self) -> None:
        cases = (
            (
                "runtime_authorities",
                (_request(ReadCollection.RUNTIME_AUTHORITIES),),
                "runtime authority store is not configured",
            ),
            (
                "runtime_authority_detail",
                ("workspace-a", RuntimeAuthorityReference("runtime-a")),
                "runtime authority store is not configured",
            ),
            (
                "runtime_authority_deliveries",
                (_request(ReadCollection.RUNTIME_AUTHORITY_DELIVERIES),),
                "runtime authority delivery store is not configured",
            ),
            (
                "runtime_authority_delivery_detail",
                ("workspace-a", RuntimeAuthorityReference("runtime-a")),
                "runtime authority delivery store is not configured",
            ),
            (
                "ingress_authorities",
                (_request(ReadCollection.INGRESS_AUTHORITIES),),
                "ingress authority store is not configured",
            ),
            (
                "ingress_authority_detail",
                ("workspace-a", IngressAuthorityReference("ingress-a")),
                "ingress authority store is not configured",
            ),
            (
                "secret_providers",
                (_request(ReadCollection.SECRET_PROVIDERS),),
                "secret provider store is not configured",
            ),
            (
                "secret_provider_detail",
                ("workspace-a", SecretProviderId("workspace-secrets")),
                "secret provider store is not configured",
            ),
            (
                "secret_references",
                (_request(ReadCollection.SECRET_REFERENCES),),
                "secret reference store is not configured",
            ),
            (
                "secret_reference_detail",
                ("workspace-a", "reference-registration-a"),
                "secret reference store is not configured",
            ),
        )
        for method_name, arguments, message in cases:
            with self.subTest(method=method_name):
                trace: list[object] = []
                projection = self._projection(trace)
                with self.assertRaises(ReadModelError) as caught:
                    getattr(projection, method_name)(*arguments)
                self.assertEqual(str(caught.exception), message)
                self.assertEqual(
                    repr(caught.exception),
                    f"ReadModelError({message!r})",
                )
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertEqual(trace, [("workspace", "workspace-a")])


class AuthoritySecretReadProjectionStructureTests(unittest.TestCase):
    def _trees(self) -> tuple[ast.Module, ast.Module]:
        paths = tuple(getattr(read_services, "__path__", ()))
        self.assertEqual(len(paths), 1)
        package_path = Path(paths[0])
        owner_path = package_path / "authority_secrets.py"
        self.assertTrue(owner_path.is_file(), "authority/secret owner is absent")
        return (
            ast.parse(owner_path.read_text(encoding="utf-8")),
            ast.parse((package_path / "instance.py").read_text(encoding="utf-8")),
        )

    def test_owner_has_exact_family_and_facade_has_only_delegates(self) -> None:
        owner, instance = self._trees()
        owner_classes = {
            node.name for node in owner.body if isinstance(node, ast.ClassDef)
        }
        self.assertEqual(owner_classes, {"_AuthoritySecretReadProjection"})
        projection = next(
            node
            for node in owner.body
            if isinstance(node, ast.ClassDef)
            and node.name == "_AuthoritySecretReadProjection"
        )
        methods = {
            node.name
            for node in projection.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        moved_methods = {
            "runtime_authorities",
            "runtime_authority_detail",
            "runtime_authority_deliveries",
            "runtime_authority_delivery_detail",
            "ingress_authorities",
            "ingress_authority_detail",
            "secret_providers",
            "secret_provider_detail",
            "secret_references",
            "secret_reference_detail",
        }
        self.assertEqual(methods, {"__init__"} | moved_methods)

        service = next(
            node
            for node in instance.body
            if isinstance(node, ast.ClassDef) and node.name == "InstanceReadService"
        )
        facade_methods = {
            node.name: node
            for node in service.body
            if isinstance(node, ast.FunctionDef)
        }
        expected_arguments = {
            name: ("request",) for name in moved_methods if not name.endswith("detail")
        }
        expected_arguments.update(
            {
                "runtime_authority_detail": ("workspace_id", "authority_ref"),
                "runtime_authority_delivery_detail": (
                    "workspace_id",
                    "authority_ref",
                ),
                "ingress_authority_detail": ("workspace_id", "authority_ref"),
                "secret_provider_detail": ("workspace_id", "provider_id"),
                "secret_reference_detail": ("workspace_id", "registration_id"),
            }
        )
        for method_name, argument_names in expected_arguments.items():
            with self.subTest(method=method_name):
                method = facade_methods[method_name]
                self.assertEqual(len(method.body), 1)
                returned = method.body[0]
                self.assertIsInstance(returned, ast.Return)
                self.assertIsInstance(returned.value, ast.Call)
                call = returned.value
                self.assertIsInstance(call.func, ast.Attribute)
                self.assertEqual(call.func.attr, method_name)
                self.assertIsInstance(call.func.value, ast.Attribute)
                self.assertEqual(call.func.value.attr, "_authority_secrets")
                self.assertIsInstance(call.func.value.value, ast.Name)
                self.assertEqual(call.func.value.value.id, "self")
                self.assertEqual(
                    tuple(
                        argument.id
                        for argument in call.args
                        if isinstance(argument, ast.Name)
                    ),
                    argument_names,
                )
                self.assertEqual(len(call.args), len(argument_names))
                self.assertEqual(call.keywords, [])

    def test_facade_retains_projection_not_raw_family_stores(self) -> None:
        _owner, instance = self._trees()
        service = next(
            node
            for node in instance.body
            if isinstance(node, ast.ClassDef) and node.name == "InstanceReadService"
        )
        initializer = next(
            node
            for node in service.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        assigned = {
            target.attr
            for node in ast.walk(initializer)
            for target in (
                node.targets
                if isinstance(node, ast.Assign)
                else (node.target,)
                if isinstance(node, ast.AnnAssign)
                else ()
            )
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        }
        self.assertIn("_authority_secrets", assigned)
        self.assertTrue(
            {
                "_runtime_authority_store",
                "_runtime_authority_delivery_store",
                "_ingress_authority_store",
                "_secret_provider_store",
                "_secret_reference_store",
            }.isdisjoint(assigned)
        )

    def test_owner_has_no_facade_or_other_projection_family_edge(self) -> None:
        owner, _instance = self._trees()
        forbidden = {
            "instance",
            "workspace_graph",
            "operations_history",
            "observations",
            "gateway_security",
        }
        self.assertEqual(_local_module_imports(owner, forbidden), set())


if __name__ == "__main__":
    unittest.main()
