from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
from dataclasses import fields, replace
import threading
import time
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
        attempt = self.attempt()
        changed = self.request(2)
        with self.assertRaises(self.contract("NodeControlAttemptError")):
            replace(attempt, request=changed)
        self.assertNotIn("intent_fingerprint", {field.name for field in fields(attempt)})
        self.assertEqual(
            replace(
                attempt,
                intended_at="2027-01-15T09:00:00Z",
                transit_grant=replace(attempt.transit_grant, expires_at=201),
            ).intent_fingerprint,
            attempt.intent_fingerprint,
        )
        self.assertNotEqual(
            replace(attempt, actor_subject="operator-b").intent_fingerprint,
            attempt.intent_fingerprint,
        )

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
        self.assertEqual(names, {
            "attempt_id", "workspace_id", "request_id", "actor_subject",
            "current_graph_id", "current_realized_projection_id",
            "gateway_runtime_id", "transit_key_registration_id",
            "workload_key_registration_id", "transit_authorization_id",
            "workload_authorization_id", "transit_correlation_id",
            "workload_correlation_id", "request_bytes", "request_digest",
            "transit_grant_bytes", "transit_grant_digest", "workload_grant_bytes",
            "workload_grant_digest", "transit_issuer", "transit_key_id",
            "transit_jti", "workload_issuer", "workload_key_id", "workload_jti",
            "intended_at", "intent_fingerprint",
        })
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


class NodeControlAttemptPostgresTests(NodeControlAttemptTests):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            self.fail("CPK_OPERATIONS_TEST_DATABASE_URL is required")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        try:
            install_schema(self.connection)
            self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
            self._seed_truth(self.connection)
            self.store_type = self.contract("NodeControlAttemptStore")
        except BaseException:
            self.connection.close()
            raise

    def tearDown(self) -> None:
        self.connection.close()

    def _seed_truth(self, connection) -> None:
        connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'running')"
        )
        connection.execute(
            """
            INSERT INTO cpk_graph_versions
              (graph_id, workspace_id, version, graph_descriptor, created_by,
               created_at, metadata)
            VALUES ('graph-current', 'workspace-a', 1, '{}'::jsonb,
                    'operator-a', '2027-01-15T07:00:00Z', '{}'::jsonb)
            """
        )
        connection.execute(
            """
            INSERT INTO cpk_realized_graph_projections
              (projection_id, workspace_id, source_authored_graph_id,
               projection_kind, projection_key, projection_digest,
               graph_descriptor, created_by, created_at)
            VALUES ('projection-current', 'workspace-a', 'graph-current',
                    'identity', 'current', %s, '{}'::jsonb, 'operator-a',
                    '2027-01-15T07:00:00Z')
            """,
            ("1" * 64,),
        )
        connection.execute(
            """
            UPDATE cpk_workspaces
            SET current_graph_id='graph-current',
                current_realized_projection_id='projection-current'
            WHERE workspace_id='workspace-a'
            """
        )
        connection.execute(
            """
            INSERT INTO cpk_secret_providers
              (registration_id, workspace_id, provider_id, provider_kind,
               display_name, endpoint_reference, credential_reference,
               allowed_reference_prefixes, allowed_intents, admitted_by,
               admitted_at, status, metadata)
            VALUES ('sprov_%s', 'workspace-a', 'secrets-a',
                    'control-plane-kit-secrets', 'Secrets A', 'provider-a',
                    'secret://bootstrap/provider-token', '["secret://keys/"]',
                    '["gateway.node-control-transit-signing-key",
                      "workload.node-control-signing-key"]',
                    'operator-a', '2027-01-15T07:00:00Z', 'active', '{}')
            """ % ("e" * 64)
        )
        for suffix, reference, intent in (
            ("a", "secret://keys/transit", "gateway.node-control-transit-signing-key"),
            ("b", "secret://keys/workload", "workload.node-control-signing-key"),
            ("f", "secret://keys/other", "gateway.node-control-transit-signing-key"),
        ):
            connection.execute(
                """
                INSERT INTO cpk_secret_references
                  (registration_id, workspace_id, secret_reference,
                   provider_registration_id, allowed_intents, admitted_by,
                   admitted_at, status, metadata)
                VALUES (%s, 'workspace-a', %s, %s, %s, 'operator-a',
                        '2027-01-15T07:00:00Z', 'active', '{}')
                """,
                (
                    "sref_" + suffix * 64,
                    reference,
                    "sprov_" + "e" * 64,
                    json.dumps([intent]),
                ),
            )
        for suffix, purpose, issuer, key_id, reference in (
            (
                "a", "gateway-node-control-transit", "cpk-server",
                "transit-key", "secret://keys/transit",
            ),
            (
                "b", "workload-node-control", "cpk-server",
                "workload-key", "secret://keys/workload",
            ),
            (
                "f", "gateway-node-control-transit", "other-server",
                "other-key", "secret://keys/other",
            ),
        ):
            connection.execute(
                """
                INSERT INTO cpk_delegation_signing_keys
                  (registration_id, workspace_id, purpose, issuer, key_id,
                   algorithm, public_key_pem, public_fingerprint_sha256,
                   private_key_reference, admitted_by, admitted_at, status,
                   activated_by, activated_at)
                VALUES (%s, 'workspace-a', %s, %s, %s, 'ed25519',
                        '-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n',
                        %s, %s, 'operator-a', '2027-01-15T07:00:00Z',
                        'active', 'operator-a', '2027-01-15T07:00:00Z')
                """,
                (
                    "dkey_" + suffix * 64,
                    purpose,
                    issuer,
                    key_id,
                    suffix * 64,
                    reference,
                ),
            )
        for suffix, reference_suffix, reference, intent, correlation in (
            ("c", "a", "secret://keys/transit", "gateway.node-control-transit-signing-key", "transit-correlation-a"),
            ("d", "b", "secret://keys/workload", "workload.node-control-signing-key", "workload-correlation-a"),
            ("f", "f", "secret://keys/other", "gateway.node-control-transit-signing-key", "other-correlation"),
        ):
            connection.execute(
                """
                INSERT INTO cpk_secret_use_authorizations
                  (authorization_id, workspace_id, reference_registration_id,
                   provider_registration_id, secret_reference, use_intent,
                   actor_subject, correlation_id, requested_at,
                   intent_fingerprint)
                VALUES (%s, 'workspace-a', %s, %s, %s, %s, 'operator-a',
                        %s, '2027-01-15T08:00:00Z', %s)
                """,
                (
                    "suse_" + suffix * 64,
                    "sref_" + reference_suffix * 64,
                    "sprov_" + "e" * 64,
                    reference,
                    intent,
                    correlation,
                    suffix * 64,
                ),
            )

    def test_restart_round_trip_and_byte_corruption_are_exact(self) -> None:
        attempt = self.attempt(1e20)
        store = self.store_type(self.connection)
        self.assertEqual(store.add(attempt), attempt)
        restarted = psycopg.connect(self.database_url, autocommit=True)
        try:
            restarted_store = self.store_type(restarted)
            self.assertEqual(restarted_store.get("attempt-a"), attempt)
            self.assertEqual(
                restarted_store.get_by_request_id("workspace-a", "request-a"),
                attempt,
            )
        finally:
            restarted.close()
        self.connection.execute(
            "UPDATE cpk_node_control_attempts SET request_bytes = request_bytes || %s",
            (b" ",),
        )
        with self.assertRaises(self.contract("NodeControlAttemptCorrupt")) as caught:
            store.get("attempt-a")
        self.assertLessEqual(len(str(caught.exception)), 128)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.connection.execute("TRUNCATE cpk_node_control_attempts")

    def test_wrong_same_workspace_key_and_authorization_fail_closed(self) -> None:
        store = self.store_type(self.connection)
        store.add(self.attempt())
        for column, value in (
            ("transit_key_registration_id", "dkey_" + "f" * 64),
            ("transit_authorization_id", "suse_" + "f" * 64),
            ("workload_key_registration_id", "dkey_" + "f" * 64),
            ("workload_authorization_id", "suse_" + "f" * 64),
        ):
            with self.subTest(column=column):
                self.connection.execute(
                    f"UPDATE cpk_node_control_attempts SET {column}=%s",
                    (value,),
                )
                with self.assertRaises(self.contract("NodeControlAttemptCorrupt")):
                    store.get("attempt-a")
                self.connection.execute("TRUNCATE cpk_node_control_attempts")
                store.add(self.attempt())

    def test_rollback_idempotency_and_graph_advancement(self) -> None:
        connection = psycopg.connect(self.database_url)
        try:
            store = self.store_type(connection)
            store.add(self.attempt())
            connection.rollback()
        finally:
            connection.close()
        self.assertIsNone(
            self.store_type(self.connection).get_by_request_id(
                "workspace-a", "request-a"
            )
        )
        store = self.store_type(self.connection)
        store.add(self.attempt())
        with self.assertRaises(self.contract("NodeControlAttemptConflict")):
            store.add(replace(self.attempt(), attempt_id="attempt-b"))
        self.connection.execute(
            """
            INSERT INTO cpk_graph_versions
              (graph_id, workspace_id, version, graph_descriptor, created_by,
               created_at, metadata)
            VALUES ('graph-next', 'workspace-a', 2, '{}', 'operator-a',
                    '2027-01-15T09:00:00Z', '{}')
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_realized_graph_projections
              (projection_id, workspace_id, source_authored_graph_id,
               projection_kind, projection_key, projection_digest,
               graph_descriptor, created_by, created_at)
            VALUES ('projection-next', 'workspace-a', 'graph-next', 'identity',
                    'next', %s, '{}', 'operator-a', '2027-01-15T09:00:00Z')
            """,
            ("2" * 64,),
        )
        self.connection.execute(
            """
            UPDATE cpk_workspaces SET current_graph_id='graph-next',
              current_realized_projection_id='projection-next'
            WHERE workspace_id='workspace-a'
            """
        )
        self.assertEqual(store.get("attempt-a").current_graph_id, "graph-current")

    def test_advisory_request_lock_serializes_two_connections(self) -> None:
        first = psycopg.connect(self.database_url)
        second = psycopg.connect(self.database_url)
        acquired = threading.Event()
        thread = None
        try:
            self.store_type(first).lock_request_id("workspace-a", "request-a")

            def acquire() -> None:
                self.store_type(second).lock_request_id("workspace-a", "request-a")
                acquired.set()

            thread = threading.Thread(target=acquire)
            thread.start()
            time.sleep(0.2)
            self.assertFalse(acquired.is_set())
            first.commit()
            thread.join(timeout=2)
            self.assertTrue(acquired.is_set())
        finally:
            first.rollback()
            second.rollback()
            if thread is not None:
                thread.join(timeout=2)
            first.close()
            second.close()


if __name__ == "__main__":
    unittest.main()
