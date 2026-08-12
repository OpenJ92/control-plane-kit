from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import unittest

import psycopg
import rfc8785

import control_plane_kit_operations as operations
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
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
from control_plane_kit_operations.postgres import install_schema


class NodeControlAttemptTests(unittest.TestCase):
    def contract(self, name: str):
        value = getattr(operations, name, None)
        self.assertIsNotNone(value, f"{name} is not implemented")
        return value

    def reference(
        self,
        role: NodeControlGraphReferenceRole,
        value: str,
    ) -> NodeControlGraphReference:
        return NodeControlGraphReference(role, value)

    def request(self, value: int | float = 1) -> NodeControlCommandRequest:
        return NodeControlCommandRequest(
            target=NodeControlTarget(
                workspace_id=self.reference(
                    NodeControlGraphReferenceRole.WORKSPACE,
                    "workspace-a",
                ),
                graph_revision=self.reference(
                    NodeControlGraphReferenceRole.GRAPH_REVISION,
                    "graph-current",
                ),
                node_id=self.reference(
                    NodeControlGraphReferenceRole.NODE,
                    "router",
                ),
                provider_socket_name=self.reference(
                    NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                    "control",
                ),
            ),
            variable_name=self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                "limit",
            ),
            operation=NodeControlOperation.APPLY_COMMAND,
            request_id="request-a",
            idempotency_key="idempotency-a",
            command_codec=ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
            precondition=ControlPlaneTransitionPrecondition(4),
            payload=NodeControlPayload(
                ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                ScalarControlState(value),
            ),
        )

    def grants(self, request: NodeControlCommandRequest):
        transit = DelegatedGatewayNodeControlTransitGrant(
            profile=DelegatedGatewayNodeControlTransitGrantProfile.V1,
            canonicalization=NodeControlCanonicalization.JCS_RFC8785_V1,
            purpose=DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
            issuer="cpk-server",
            key_id="transit-key",
            attempt_id="attempt-a",
            workspace_id=request.target.workspace_id,
            graph_revision=request.target.graph_revision,
            gateway_node_id=self.reference(
                NodeControlGraphReferenceRole.NODE,
                "gateway",
            ),
            target=request.target,
            variable_name=request.variable_name,
            operation=request.operation,
            command_codec=request.command_codec,
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            request_digest=request.canonical_digest(),
            issued_at=100,
            not_before=100,
            expires_at=200,
            jti="transit-jti",
        )
        workload = DelegatedWorkloadNodeControlGrant(
            issuer="cpk-server",
            key_id="workload-key",
            audience="workload:router:control",
            target=request.target,
            variable_name=request.variable_name,
            operation=request.operation,
            command_codec=request.command_codec,
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            request_digest=request.canonical_digest(),
            issued_at=100,
            not_before=100,
            expires_at=200,
            jti="workload-jti",
        )
        return transit, workload

    def attempt(self, value: int | float = 1):
        request = self.request(value)
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
            transit_grant=transit,
            workload_grant=workload,
        )

    def test_public_record_derives_exact_wire_and_fingerprint(self) -> None:
        attempt = self.attempt(1e20)
        expected = rfc8785.dumps(
            {
                "actor_subject": "operator-a",
                "gateway_node_id": "gateway",
                "profile": "node-control-intent.v1",
                "request_digest": attempt.request.canonical_digest().value,
            }
        )
        self.assertEqual(
            attempt.intent_fingerprint,
            hashlib.sha256(expected).hexdigest(),
        )
        self.assertEqual(attempt.workspace_id, "workspace-a")
        self.assertEqual(attempt.request_id, "request-a")
        self.assertEqual(attempt.request_bytes, attempt.request.canonical_bytes())
        self.assertEqual(
            attempt.transit_grant_bytes,
            attempt.transit_grant.canonical_bytes(),
        )
        self.assertEqual(
            attempt.workload_grant_bytes,
            attempt.workload_grant.canonical_bytes(),
        )
        self.assertNotIn("private", repr(attempt).lower())
        self.assertNotIn("signature", repr(attempt).lower())

    def test_record_rejects_cross_value_and_caller_fingerprint_drift(self) -> None:
        record_type = self.contract("NodeControlIntendedAttempt")
        attempt = self.attempt()
        changed = self.request(2)
        with self.assertRaises(self.contract("NodeControlAttemptError")):
            record_type(**{**attempt.__dict__, "request": changed})
        self.assertNotIn("intent_fingerprint", attempt.__dict__)

    def test_store_surface_and_schema_are_current_only(self) -> None:
        self.contract("NodeControlAttemptStore")
        self.assertTrue(hasattr(operations, "NodeControlAttemptCorrupt"))
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            self.fail("CPK_OPERATIONS_TEST_DATABASE_URL is required")
        with psycopg.connect(database_url, autocommit=True) as connection:
            install_schema(connection)
            columns = connection.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'cpk_node_control_attempts'
                ORDER BY ordinal_position
                """
            ).fetchall()
        names = {name for name, _ in columns}
        self.assertTrue({"request_bytes", "transit_grant_bytes", "workload_grant_bytes"} <= names)
        self.assertEqual(
            names
            & {
                "status", "result", "completed_at", "signature", "compact_token",
                "endpoint", "private_key_reference", "metadata",
            },
            set(),
        )
        self.assertEqual(
            dict(columns)["request_bytes"],
            "bytea",
        )

    def test_atlas_and_package_boundaries_explain_one_insert_only_truth(self) -> None:
        root = Path(__file__).resolve().parents[1]
        atlas = (root / "OPERATIONS_TABLE_ATLAS.md").read_text(encoding="utf-8")
        self.assertIn("### `cpk_node_control_attempts`", atlas)
        self.assertIn("Row membership means only INTENDED", atlas)
        self.assertIn("#1556", atlas)
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (root / "src" / "control_plane_kit_operations").rglob("*.py")
            if "node_control_attempt" in path.name
        ).lower()
        for forbidden in (
            "fastapi", "httpx", "docker", "jwt", "signature", "compact_token",
            "schema migration", "backfill", "upgrade",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
