from __future__ import annotations

import ast
from dataclasses import fields, replace
import inspect
from pathlib import Path
import unittest

import control_plane_kit_operations as operations
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    ControlPlaneTransitionPrecondition,
    DelegatedWorkloadNodeControlGrant,
    NodeControlCanonicalization,
    NodeControlCommandRequest,
    NodeControlGraphReference,
    NodeControlGraphReferenceRole,
    NodeControlOperation,
    NodeControlPayload,
    NodeControlTarget,
    ScalarControlState,
)
from control_plane_kit_core.node_control_transit import (
    DelegatedGatewayNodeControlTransitGrant,
    DelegatedGatewayNodeControlTransitGrantProfile,
)
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)


PUBLIC_KEY_A = """-----BEGIN PUBLIC KEY-----
AAAA
-----END PUBLIC KEY-----
"""
PUBLIC_KEY_B = """-----BEGIN PUBLIC KEY-----
BBBB
-----END PUBLIC KEY-----
"""
PUBLIC_KEY_C = """-----BEGIN PUBLIC KEY-----
CCCC
-----END PUBLIC KEY-----
"""


class _SigningAuthorityFixture:
    def contract(self, name: str):
        value = getattr(operations, name, None)
        self.assertIsNotNone(value, f"{name} is not implemented")
        return value

    @staticmethod
    def reference(
        role: NodeControlGraphReferenceRole,
        value: str,
    ) -> NodeControlGraphReference:
        return NodeControlGraphReference(role, value)

    def request(
        self,
        *,
        request_id: str = "request-a",
        node_id: str = "router",
        graph_revision: str = "graph-current",
    ) -> NodeControlCommandRequest:
        return NodeControlCommandRequest(
            target=NodeControlTarget(
                workspace_id=self.reference(
                    NodeControlGraphReferenceRole.WORKSPACE,
                    "workspace-a",
                ),
                graph_revision=self.reference(
                    NodeControlGraphReferenceRole.GRAPH_REVISION,
                    graph_revision,
                ),
                node_id=self.reference(NodeControlGraphReferenceRole.NODE, node_id),
                provider_socket_name=self.reference(
                    NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                    "control",
                ),
            ),
            variable_name=self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                "routing",
            ),
            operation=NodeControlOperation.APPLY_COMMAND,
            request_id=request_id,
            idempotency_key=f"idempotency-{request_id.removeprefix('request-')}",
            command_codec=ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
            precondition=ControlPlaneTransitionPrecondition(4),
            payload=NodeControlPayload(
                ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                ScalarControlState("blue"),
            ),
        )

    def grants(
        self,
        request: NodeControlCommandRequest,
        *,
        attempt_id: str = "attempt-a",
        issued_at: int = 100,
        not_before: int = 100,
        expires_at: int = 200,
    ) -> tuple[
        DelegatedGatewayNodeControlTransitGrant,
        DelegatedWorkloadNodeControlGrant,
    ]:
        common = dict(
            target=request.target,
            variable_name=request.variable_name,
            operation=request.operation,
            command_codec=request.command_codec,
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            request_digest=request.canonical_digest(),
            issued_at=issued_at,
            not_before=not_before,
            expires_at=expires_at,
        )
        return (
            DelegatedGatewayNodeControlTransitGrant(
                profile=DelegatedGatewayNodeControlTransitGrantProfile.V1,
                canonicalization=NodeControlCanonicalization.JCS_RFC8785_V1,
                purpose=DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
                issuer="cpk-server",
                key_id="transit-key",
                attempt_id=attempt_id,
                workspace_id=request.target.workspace_id,
                graph_revision=request.target.graph_revision,
                gateway_node_id=self.reference(
                    NodeControlGraphReferenceRole.NODE,
                    "gateway",
                ),
                jti=f"transit-jti-{attempt_id}",
                **common,
            ),
            DelegatedWorkloadNodeControlGrant(
                issuer="cpk-server",
                key_id="workload-key",
                audience="workload:router:control",
                jti=f"workload-jti-{attempt_id}",
                **common,
            ),
        )

    @property
    def transit_public_key(self) -> DelegationPublicKey:
        return DelegationPublicKey(
            "transit-key",
            DelegationKeyAlgorithm.ED25519,
            PUBLIC_KEY_A,
        )

    @property
    def workload_public_key(self) -> DelegationPublicKey:
        return DelegationPublicKey(
            "workload-key",
            DelegationKeyAlgorithm.ED25519,
            PUBLIC_KEY_B,
        )

    def family_requests(
        self,
        *,
        request: NodeControlCommandRequest | None = None,
        attempt_id: str = "attempt-a",
    ):
        request = self.request() if request is None else request
        transit_grant, workload_grant = self.grants(
            request,
            attempt_id=attempt_id,
        )
        transit_type = self.contract(
            "DeferredGatewayNodeControlTransitSigningRequest"
        )
        workload_type = self.contract("DeferredWorkloadNodeControlSigningRequest")
        return (
            transit_type(
                "dkey_" + "a" * 64,
                "suse_" + "c" * 64,
                transit_grant,
            ),
            workload_type(
                "dkey_" + "b" * 64,
                "suse_" + "d" * 64,
                workload_grant,
            ),
        )

    def attempt(
        self,
        *,
        transit_grant: DelegatedGatewayNodeControlTransitGrant | None = None,
        workload_grant: DelegatedWorkloadNodeControlGrant | None = None,
    ):
        request = self.request()
        transit, workload = self.grants(request)
        return self.contract("NodeControlIntendedAttempt")(
            attempt_id="attempt-a",
            actor_subject="operator-a",
            current_graph_id="graph-current",
            current_realized_projection_id="projection-current",
            gateway_runtime_id="docker-a",
            transit_key_registration_id="dkey_" + "a" * 64,
            workload_key_registration_id="dkey_" + "b" * 64,
            transit_authorization_id="suse_" + "c" * 64,
            workload_authorization_id="suse_" + "d" * 64,
            transit_correlation_id="transit-correlation-a",
            workload_correlation_id="workload-correlation-a",
            intended_at="2027-01-15T08:00:00Z",
            request=request,
            transit_grant=transit if transit_grant is None else transit_grant,
            workload_grant=workload if workload_grant is None else workload_grant,
        )

    def deferred(self, **changes):
        transit, workload = self.family_requests()
        values = {
            "attempt_id": "attempt-a",
            "actor_subject": "operator-a",
            "current_graph_id": "graph-current",
            "current_realized_projection_id": "projection-current",
            "transit": transit,
            "transit_correlation_id": "transit-correlation-a",
            "transit_public_key_fingerprint_sha256": (
                self.transit_public_key.fingerprint_sha256
            ),
            "workload": workload,
            "workload_correlation_id": "workload-correlation-a",
            "workload_public_key_fingerprint_sha256": (
                self.workload_public_key.fingerprint_sha256
            ),
        }
        values.update(changes)
        return self.contract("DeferredNodeControlSigningRequest")(**values)

    def resolution_grant(self, family: str, **changes) -> SecretResolutionGrant:
        transit = family == "transit"
        values = {
            "authorization_id": "suse_" + ("c" if transit else "d") * 64,
            "workspace_id": "workspace-a",
            "reference_registration_id": "sref_" + ("a" if transit else "b") * 64,
            "provider_registration_id": "sprov_" + "e" * 64,
            "endpoint_reference": SecretProviderEndpointReference("provider-a"),
            "credential_reference": SecretReference(
                "secret://workspace-secrets/provider-token"
            ),
            "reference": SecretReference(
                "secret://workspace-secrets/keys/"
                + ("transit" if transit else "workload")
            ),
            "intent": (
                SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY
                if transit
                else SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY
            ),
            "actor_subject": "operator-a",
            "correlation_id": (
                "transit-correlation-a" if transit else "workload-correlation-a"
            ),
            "intent_fingerprint": ("c" if transit else "d") * 64,
            "operation_id": "attempt-a",
        }
        values.update(changes)
        return SecretResolutionGrant(**values)

    def authority(self, family: str, **changes):
        transit = family == "transit"
        values = {
            "public_key": (
                self.transit_public_key if transit else self.workload_public_key
            ),
            "resolution_grant": self.resolution_grant(family),
        }
        values.update(changes)
        name = (
            "GatewayNodeControlTransitSigningAuthority"
            if transit
            else "WorkloadNodeControlSigningAuthority"
        )
        return self.contract(name)(**values)

    def pair(self, **changes):
        values = {
            "deferred_request": self.deferred(),
            "transit": self.authority("transit"),
            "workload": self.authority("workload"),
        }
        values.update(changes)
        return self.contract("NodeControlSigningAuthorityPair")(**values)

    def assert_contract_error(self, factory, *, forbidden: str | None = None) -> None:
        error_type = self.contract("NodeControlSigningAuthorityError")
        with self.assertRaises(error_type) as caught:
            factory()
        self.assertLessEqual(len(str(caught.exception)), 128)
        self.assertLessEqual(len(repr(caught.exception)), 180)
        if forbidden is not None:
            self.assertNotIn(forbidden, str(caught.exception))
            self.assertNotIn(forbidden, repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)


class NodeControlSigningAuthorityContractTests(
    _SigningAuthorityFixture,
    unittest.TestCase,
):
    def test_public_algebra_has_one_shared_deferred_request(self) -> None:
        expected = {
            "ReloadNodeControlSigningAuthority": ("attempt_id",),
            "DeferredNodeControlSigningRequest": (
                "attempt_id",
                "actor_subject",
                "current_graph_id",
                "current_realized_projection_id",
                "transit",
                "transit_correlation_id",
                "transit_public_key_fingerprint_sha256",
                "workload",
                "workload_correlation_id",
                "workload_public_key_fingerprint_sha256",
            ),
            "GatewayNodeControlTransitSigningAuthority": (
                "public_key",
                "resolution_grant",
            ),
            "WorkloadNodeControlSigningAuthority": (
                "public_key",
                "resolution_grant",
            ),
            "NodeControlSigningAuthorityPair": (
                "deferred_request",
                "transit",
                "workload",
            ),
        }
        for name, field_names in expected.items():
            with self.subTest(name=name):
                contract = self.contract(name)
                self.assertEqual(tuple(field.name for field in fields(contract)), field_names)
                self.assertIs(getattr(operations, name), contract)
                self.assertTrue(hasattr(contract, "__slots__"))

        service_parameters = tuple(
            inspect.signature(
                self.contract("NodeControlSigningAuthorityReloadService")
            ).parameters.values()
        )
        self.assertEqual(
            tuple((value.name, value.kind) for value in service_parameters),
            (
                ("unit_of_work_factory", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                ("epoch_clock", inspect.Parameter.KEYWORD_ONLY),
            ),
        )

        deferred_fields = {
            field.name: field for field in fields(self.contract("DeferredNodeControlSigningRequest"))
        }
        for name in (
            "actor_subject",
            "transit_correlation_id",
            "transit_public_key_fingerprint_sha256",
            "workload_correlation_id",
            "workload_public_key_fingerprint_sha256",
        ):
            self.assertFalse(deferred_fields[name].repr)

    def test_shared_deferred_rejects_cross_request_and_lineage_families(self) -> None:
        self.deferred()
        transit, workload = self.family_requests()
        other_request = self.request(request_id="request-b")
        other_transit, other_workload = self.family_requests(
            request=other_request,
            attempt_id="attempt-b",
        )
        candidates = (
            ("attempt-b", {"attempt_id": "attempt-b"}),
            ("graph-other", {"current_graph_id": "graph-other"}),
            (
                "projection-other",
                {"current_realized_projection_id": "projection-other"},
            ),
            ("request-b", {"workload": other_workload}),
            ("request-b", {"transit": other_transit}),
            ("family-swap", {"transit": workload}),
            ("family-swap", {"workload": transit}),
        )
        for forbidden, changes in candidates:
            with self.subTest(changes=tuple(changes)):
                self.assert_contract_error(
                    lambda changes=changes: self.deferred(**changes),
                    forbidden=forbidden,
                )

    def test_shared_deferred_anchor_bounds_and_types_are_closed(self) -> None:
        candidates = (
            ("actor_subject", object()),
            ("actor_subject", "Actor/" + "x" * 200),
            ("transit_correlation_id", object()),
            ("transit_correlation_id", "transit/" + "x" * 256),
            ("workload_correlation_id", object()),
            ("workload_correlation_id", "workload/" + "x" * 256),
            ("transit_public_key_fingerprint_sha256", object()),
            ("transit_public_key_fingerprint_sha256", "A" * 64),
            ("workload_public_key_fingerprint_sha256", object()),
            ("workload_public_key_fingerprint_sha256", "0" * 63),
        )
        for name, candidate in candidates:
            with self.subTest(name=name, kind=type(candidate).__name__):
                self.assert_contract_error(
                    lambda name=name, candidate=candidate: self.deferred(
                        **{name: candidate}
                    ),
                    forbidden=candidate if isinstance(candidate, str) else None,
                )

    def test_pair_is_reference_only_and_rejects_family_substitution(self) -> None:
        pair = self.pair()
        self.assertEqual(pair.transit.public_key, self.transit_public_key)
        self.assertEqual(pair.workload.public_key, self.workload_public_key)
        rendered = repr(pair)
        for forbidden in (
            "BEGIN PUBLIC KEY",
            "secret://",
            "provider-token",
            "provider-a",
        ):
            self.assertNotIn(forbidden, rendered)

        candidates = (
            ("transit", self.authority("workload")),
            ("workload", self.authority("transit")),
        )
        for field_name, candidate in candidates:
            with self.subTest(field_name=field_name):
                self.assert_contract_error(
                    lambda field_name=field_name, candidate=candidate: self.pair(
                        **{field_name: candidate}
                    )
                )

        class HostileTransitRequest(
            self.contract("DeferredGatewayNodeControlTransitSigningRequest")
        ):
            pass

        class HostileDeferred(self.contract("DeferredNodeControlSigningRequest")):
            pass

        transit, workload = self.family_requests()
        hostile_transit = HostileTransitRequest(
            transit.key_registration_id,
            transit.authorization_id,
            transit.grant,
        )
        self.assert_contract_error(
            lambda: self.deferred(transit=hostile_transit)
        )
        deferred = self.deferred()
        hostile_deferred = HostileDeferred(
            **{field.name: getattr(deferred, field.name) for field in fields(deferred)}
        )
        self.assert_contract_error(
            lambda: self.contract("NodeControlSigningAuthorityPair")(
                deferred_request=hostile_deferred,
                transit=self.authority("transit"),
                workload=self.authority("workload"),
            )
        )

    def test_pair_checks_every_actor_correlation_and_authorization_anchor(self) -> None:
        deferred = self.deferred()
        vectors = (
            (
                "transit",
                "operator-other",
                self.authority(
                    "transit",
                    resolution_grant=self.resolution_grant(
                        "transit", actor_subject="operator-other"
                    ),
                ),
            ),
            (
                "workload",
                "operator-other",
                self.authority(
                    "workload",
                    resolution_grant=self.resolution_grant(
                        "workload", actor_subject="operator-other"
                    ),
                ),
            ),
            (
                "transit",
                "correlation-other",
                self.authority(
                    "transit",
                    resolution_grant=self.resolution_grant(
                        "transit", correlation_id="correlation-other"
                    ),
                ),
            ),
            (
                "workload",
                "correlation-other",
                self.authority(
                    "workload",
                    resolution_grant=self.resolution_grant(
                        "workload", correlation_id="correlation-other"
                    ),
                ),
            ),
            (
                "transit",
                "suse_" + "e" * 64,
                self.authority(
                    "transit",
                    resolution_grant=self.resolution_grant(
                        "transit", authorization_id="suse_" + "e" * 64
                    ),
                ),
            ),
            (
                "workload",
                "suse_" + "e" * 64,
                self.authority(
                    "workload",
                    resolution_grant=self.resolution_grant(
                        "workload", authorization_id="suse_" + "e" * 64
                    ),
                ),
            ),
        )
        for family, forbidden, authority in vectors:
            with self.subTest(family=family, forbidden=forbidden):
                self.assert_contract_error(
                    lambda family=family, authority=authority: self.contract(
                        "NodeControlSigningAuthorityPair"
                    )(
                        deferred_request=deferred,
                        transit=(
                            authority if family == "transit" else self.authority("transit")
                        ),
                        workload=(
                            authority if family == "workload" else self.authority("workload")
                        ),
                    ),
                    forbidden=forbidden,
                )

        swapped = self.contract("NodeControlSigningAuthorityPair")
        self.assert_contract_error(
            lambda: swapped(
                deferred_request=replace(
                    deferred,
                    transit_correlation_id="workload-correlation-a",
                    workload_correlation_id="transit-correlation-a",
                ),
                transit=self.authority("transit"),
                workload=self.authority("workload"),
            )
        )

    def test_pair_checks_public_material_intent_operation_and_provenance(self) -> None:
        changed_transit_key = DelegationPublicKey(
            "transit-key",
            DelegationKeyAlgorithm.ED25519,
            PUBLIC_KEY_C,
        )
        changed_workload_key = DelegationPublicKey(
            "workload-key",
            DelegationKeyAlgorithm.ED25519,
            PUBLIC_KEY_C,
        )

        class HostilePublicKey(DelegationPublicKey):
            pass

        class HostileResolutionGrant(SecretResolutionGrant):
            pass

        hostile_public_key = HostilePublicKey(
            "transit-key",
            DelegationKeyAlgorithm.ED25519,
            PUBLIC_KEY_A,
        )
        transit_grant = self.resolution_grant("transit")
        hostile_resolution_grant = HostileResolutionGrant(
            **{
                field.name: getattr(transit_grant, field.name)
                for field in fields(transit_grant)
            }
        )
        vectors = (
            (
                "transit",
                changed_transit_key.fingerprint_sha256,
                self.authority("transit", public_key=changed_transit_key),
            ),
            (
                "workload",
                changed_workload_key.fingerprint_sha256,
                self.authority("workload", public_key=changed_workload_key),
            ),
            (
                "transit",
                None,
                self.authority("transit", public_key=hostile_public_key),
            ),
            (
                "transit",
                None,
                self.authority(
                    "transit",
                    resolution_grant=hostile_resolution_grant,
                ),
            ),
            (
                "transit",
                "attempt-other",
                self.authority(
                    "transit",
                    resolution_grant=self.resolution_grant(
                        "transit", operation_id="attempt-other"
                    ),
                ),
            ),
            (
                "workload",
                "attempt-other",
                self.authority(
                    "workload",
                    resolution_grant=self.resolution_grant(
                        "workload", operation_id="attempt-other"
                    ),
                ),
            ),
            (
                "transit",
                SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY.value,
                self.authority(
                    "transit",
                    resolution_grant=self.resolution_grant(
                        "transit",
                        intent=SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY,
                    ),
                ),
            ),
            (
                "workload",
                SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY.value,
                self.authority(
                    "workload",
                    resolution_grant=self.resolution_grant(
                        "workload",
                        intent=SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY,
                    ),
                ),
            ),
            (
                "transit",
                "session-other",
                self.authority(
                    "transit",
                    resolution_grant=self.resolution_grant(
                        "transit", session_id="session-other"
                    ),
                ),
            ),
            (
                "workload",
                "probe-other",
                self.authority(
                    "workload",
                    resolution_grant=self.resolution_grant(
                        "workload", probe_id="probe-other"
                    ),
                ),
            ),
        )
        for family, forbidden, authority in vectors:
            with self.subTest(family=family, forbidden=forbidden):
                self.assert_contract_error(
                    lambda family=family, authority=authority: self.pair(
                        **{family: authority}
                    ),
                    forbidden=forbidden,
                )

    def test_module_is_effect_free_and_private_store_is_not_root_exported(self) -> None:
        module_path = (
            Path(operations.__file__).parent / "node_control_signing_authority.py"
        )
        self.assertTrue(module_path.is_file(), "signing authority module is absent")
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        forbidden_roots = {
            "fastapi",
            "httpx",
            "requests",
            "socket",
            "docker",
            "jwt",
            "control_plane_kit_interpreters",
        }
        self.assertFalse(
            {
                name
                for name in imports
                if any(name == root or name.startswith(root + ".") for root in forbidden_roots)
            }
        )
        source = module_path.read_text(encoding="utf-8")
        for forbidden in ("sign(", "resolve(", "fetchall("):
            self.assertNotIn(forbidden, source)
        self.assertFalse(hasattr(operations, "NodeControlSigningAuthorityStore"))


if __name__ == "__main__":
    unittest.main()
