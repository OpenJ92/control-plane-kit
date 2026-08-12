from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import re
from dataclasses import replace
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
from control_plane_kit_operations.postgres import SchemaInstallationError, install_schema
from control_plane_kit_operations.postgres import current_schema_contract


_ATTEMPT_COLUMNS = (
    ("attempt_id", "text", True, None),
    ("workspace_id", "text", True, None),
    ("request_id", "text", True, None),
    ("actor_subject", "text", True, None),
    ("current_graph_id", "text", True, None),
    ("current_realized_projection_id", "text", True, None),
    ("gateway_runtime_id", "text", True, None),
    ("transit_key_registration_id", "text", True, None),
    ("workload_key_registration_id", "text", True, None),
    ("transit_authorization_id", "text", True, None),
    ("workload_authorization_id", "text", True, None),
    ("transit_correlation_id", "text", True, None),
    ("workload_correlation_id", "text", True, None),
    ("request_bytes", "bytea", True, None),
    ("request_digest", "text", True, None),
    ("transit_grant_bytes", "bytea", True, None),
    ("transit_grant_digest", "text", True, None),
    ("workload_grant_bytes", "bytea", True, None),
    ("workload_grant_digest", "text", True, None),
    ("transit_issuer", "text", True, None),
    ("transit_key_id", "text", True, None),
    ("transit_jti", "text", True, None),
    ("workload_issuer", "text", True, None),
    ("workload_key_id", "text", True, None),
    ("workload_jti", "text", True, None),
    ("intended_at", "timestamp(6) with time zone", True, None),
    ("intent_fingerprint", "text", True, None),
)

_ATTEMPT_KEY_CONSTRAINTS = {
    "cpk_node_control_attempts_pkey": ("p", ("attempt_id",)),
    "cpk_node_control_attempts_workspace_request_key": (
        "u", ("workspace_id", "request_id"),
    ),
    "cpk_node_control_attempts_transit_jti_key": (
        "u", ("transit_issuer", "transit_jti"),
    ),
    "cpk_node_control_attempts_workload_jti_key": (
        "u", ("workload_issuer", "workload_jti"),
    ),
}

_ATTEMPT_SUPPORTING_INDEXES = {
    "cpk_node_control_attempts_projection_source_idx": (
        "current_realized_projection_id", "current_graph_id",
    ),
    "cpk_node_control_attempts_projection_workspace_idx": (
        "current_realized_projection_id", "workspace_id",
    ),
    "cpk_node_control_attempts_transit_key_workspace_idx": (
        "transit_key_registration_id", "workspace_id",
    ),
    "cpk_node_control_attempts_workload_key_workspace_idx": (
        "workload_key_registration_id", "workspace_id",
    ),
    "cpk_node_control_attempts_transit_authorization_workspace_idx": (
        "transit_authorization_id", "workspace_id",
    ),
    "cpk_node_control_attempts_workload_authorization_workspace_idx": (
        "workload_authorization_id", "workspace_id",
    ),
}

_ATTEMPT_CHECK_CONSTRAINTS = {
    f"cpk_node_control_attempts_{name}_check"
    for name in (
        "attempt_id", "workspace_id", "request_id", "actor_subject",
        "current_graph_id", "current_realized_projection_id",
        "gateway_runtime_id", "transit_key_registration_id",
        "workload_key_registration_id", "transit_authorization_id",
        "workload_authorization_id", "transit_correlation_id",
        "workload_correlation_id", "request_bytes", "request_digest",
        "transit_grant_bytes", "transit_grant_digest", "workload_grant_bytes",
        "workload_grant_digest", "transit_issuer", "transit_key_id",
        "transit_jti", "workload_issuer", "workload_key_id", "workload_jti",
        "intent_fingerprint",
    )
}

_ATTEMPT_FOREIGN_KEYS = {
    "cpk_node_control_attempts_workspace_id_fkey": (
        ("workspace_id",), "cpk_workspaces", ("workspace_id",),
    ),
    "cpk_node_control_attempts_projection_source_fk": (
        ("current_realized_projection_id", "current_graph_id"),
        "cpk_realized_graph_projections",
        ("projection_id", "source_authored_graph_id"),
    ),
    "cpk_node_control_attempts_projection_workspace_fk": (
        ("current_realized_projection_id", "workspace_id"),
        "cpk_realized_graph_projections",
        ("projection_id", "workspace_id"),
    ),
    "cpk_node_control_attempts_transit_key_workspace_fk": (
        ("transit_key_registration_id", "workspace_id"),
        "cpk_delegation_signing_keys",
        ("registration_id", "workspace_id"),
    ),
    "cpk_node_control_attempts_workload_key_workspace_fk": (
        ("workload_key_registration_id", "workspace_id"),
        "cpk_delegation_signing_keys",
        ("registration_id", "workspace_id"),
    ),
    "cpk_node_control_attempts_transit_authorization_workspace_fk": (
        ("transit_authorization_id", "workspace_id"),
        "cpk_secret_use_authorizations",
        ("authorization_id", "workspace_id"),
    ),
    "cpk_node_control_attempts_workload_authorization_workspace_fk": (
        ("workload_authorization_id", "workspace_id"),
        "cpk_secret_use_authorizations",
        ("authorization_id", "workspace_id"),
    ),
}


class _NodeControlAttemptFixture:
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

    def request(
        self,
        value: int | float = 1,
        *,
        operation: NodeControlOperation = NodeControlOperation.APPLY_COMMAND,
    ) -> NodeControlCommandRequest:
        is_apply = operation is NodeControlOperation.APPLY_COMMAND
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
            operation=operation,
            request_id="request-a",
            idempotency_key="idempotency-a",
            command_codec=(
                ControlPlaneCommandCodec.REPLACE_SCALAR_V1 if is_apply else None
            ),
            precondition=(ControlPlaneTransitionPrecondition(4) if is_apply else None),
            payload=(
                NodeControlPayload(
                    ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                    ScalarControlState(value),
                )
                if is_apply
                else None
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

    def assert_attempt_error(self, factory, *, forbidden: str | None = None) -> None:
        error_type = self.contract("NodeControlAttemptError")
        with self.assertRaises(error_type) as caught:
            factory()
        self.assertLessEqual(len(str(caught.exception)), 128)
        self.assertLessEqual(len(repr(caught.exception)), 160)
        if forbidden is not None:
            self.assertNotIn(forbidden, str(caught.exception))
            self.assertNotIn(forbidden, repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)


class NodeControlAttemptTests(_NodeControlAttemptFixture, unittest.TestCase):
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
        self.assert_attempt_error(lambda: replace(attempt, request=changed))
        self.assertNotIn(
            "intent_fingerprint",
            inspect.signature(type(attempt)).parameters,
        )
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

    def test_fingerprint_depends_only_on_actor_gateway_and_request(self) -> None:
        attempt = self.attempt()
        changed_identity = replace(
            attempt,
            attempt_id="attempt-b",
            gateway_runtime_id="docker-b",
            transit_key_registration_id="dkey_" + "e" * 64,
            workload_key_registration_id="dkey_" + "f" * 64,
            transit_authorization_id="suse_" + "1" * 64,
            workload_authorization_id="suse_" + "2" * 64,
            transit_correlation_id="transit-correlation-b",
            workload_correlation_id="workload-correlation-b",
            intended_at="2027-01-15T09:00:00Z",
            transit_grant=replace(
                attempt.transit_grant,
                attempt_id="attempt-b",
                key_id="transit-key-b",
                issued_at=101,
                not_before=101,
                expires_at=201,
                jti="transit-jti-b",
            ),
            workload_grant=replace(
                attempt.workload_grant,
                key_id="workload-key-b",
                issued_at=101,
                not_before=101,
                expires_at=201,
                jti="workload-jti-b",
            ),
        )
        self.assertEqual(changed_identity.intent_fingerprint, attempt.intent_fingerprint)

        changed_gateway = replace(
            attempt,
            transit_grant=replace(
                attempt.transit_grant,
                gateway_node_id=self.reference(
                    NodeControlGraphReferenceRole.NODE,
                    "gateway-b",
                ),
            ),
        )
        self.assertNotEqual(
            changed_gateway.intent_fingerprint,
            attempt.intent_fingerprint,
        )

        changed_request = self.request(2)
        transit, workload = self.grants(changed_request)
        changed_command = replace(
            attempt,
            request=changed_request,
            transit_grant=transit,
            workload_grant=workload,
        )
        self.assertNotEqual(
            changed_command.intent_fingerprint,
            attempt.intent_fingerprint,
        )

    def test_record_accepts_read_and_apply_and_rejects_each_claim_mismatch(self) -> None:
        self.attempt()
        read_request = self.request(operation=NodeControlOperation.READ_STATE)
        transit, workload = self.grants(read_request)
        replace(
            self.attempt(),
            request=read_request,
            transit_grant=transit,
            workload_grant=workload,
        )

        attempt = self.attempt()
        other_target = replace(
            attempt.request.target,
            node_id=self.reference(NodeControlGraphReferenceRole.NODE, "router-b"),
        )
        other_variable = self.reference(
            NodeControlGraphReferenceRole.VARIABLE,
            "other-variable",
        )
        other_workspace_target = replace(
            attempt.request.target,
            workspace_id=self.reference(
                NodeControlGraphReferenceRole.WORKSPACE,
                "workspace-b",
            ),
        )
        other_revision_target = replace(
            attempt.request.target,
            graph_revision=self.reference(
                NodeControlGraphReferenceRole.GRAPH_REVISION,
                "graph-other",
            ),
        )
        other_workspace_transit = self.grants(
            replace(attempt.request, target=other_workspace_target)
        )[0]
        other_revision_transit = self.grants(
            replace(attempt.request, target=other_revision_target)
        )[0]
        mismatches = (
            lambda: replace(
                attempt,
                transit_grant=replace(attempt.transit_grant, attempt_id="attempt-b"),
            ),
            lambda: replace(attempt, current_graph_id="graph-other"),
            lambda: replace(
                attempt,
                transit_grant=replace(
                    attempt.transit_grant,
                    target=other_target,
                    workspace_id=other_target.workspace_id,
                    graph_revision=other_target.graph_revision,
                ),
            ),
            lambda: replace(
                attempt,
                workload_grant=replace(attempt.workload_grant, target=other_target),
            ),
            lambda: replace(
                attempt,
                transit_grant=replace(
                    attempt.transit_grant,
                    variable_name=other_variable,
                ),
            ),
            lambda: replace(
                attempt,
                workload_grant=replace(
                    attempt.workload_grant,
                    variable_name=other_variable,
                ),
            ),
            lambda: replace(
                attempt,
                transit_grant=replace(attempt.transit_grant, request_id="request-b"),
            ),
            lambda: replace(
                attempt,
                workload_grant=replace(
                    attempt.workload_grant,
                    request_id="request-b",
                ),
            ),
            lambda: replace(
                attempt,
                transit_grant=replace(
                    attempt.transit_grant,
                    idempotency_key="idempotency-b",
                ),
            ),
            lambda: replace(
                attempt,
                workload_grant=replace(
                    attempt.workload_grant,
                    idempotency_key="idempotency-b",
                ),
            ),
            lambda: replace(
                attempt,
                transit_grant=replace(
                    attempt.transit_grant,
                    request_digest=self.request(2).canonical_digest(),
                ),
            ),
            lambda: replace(
                attempt,
                workload_grant=replace(
                    attempt.workload_grant,
                    request_digest=self.request(2).canonical_digest(),
                ),
            ),
            lambda: replace(
                attempt,
                transit_grant=replace(
                    attempt.transit_grant,
                    operation=NodeControlOperation.READ_STATE,
                    command_codec=None,
                ),
            ),
            lambda: replace(
                attempt,
                workload_grant=replace(
                    attempt.workload_grant,
                    operation=NodeControlOperation.READ_STATE,
                    command_codec=None,
                ),
            ),
            lambda: replace(
                attempt,
                transit_grant=replace(
                    attempt.transit_grant,
                    command_codec=ControlPlaneCommandCodec.REPLACE_MAP_V1,
                ),
            ),
            lambda: replace(
                attempt,
                workload_grant=replace(
                    attempt.workload_grant,
                    command_codec=ControlPlaneCommandCodec.REPLACE_MAP_V1,
                ),
            ),
            lambda: replace(attempt, transit_grant=other_workspace_transit),
            lambda: replace(attempt, transit_grant=other_revision_transit),
        )
        for index, mismatch in enumerate(mismatches):
            with self.subTest(mismatch=index):
                self.assert_attempt_error(mismatch)

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
                SELECT column_name,
                       CASE WHEN data_type = 'timestamp with time zone'
                            THEN 'timestamp(' || datetime_precision || ') with time zone'
                            ELSE data_type END,
                       is_nullable = 'NO', column_default
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'cpk_node_control_attempts'
                ORDER BY ordinal_position
                """
            ).fetchall()
        self.assertEqual(tuple(columns), _ATTEMPT_COLUMNS)

        contract = current_schema_contract.CURRENT_POSTGRES_SCHEMA_CONTRACT
        relation = next(
            value
            for value in contract.relations
            if value.name == "cpk_node_control_attempts"
        )
        self.assertEqual(
            (
                relation.kind,
                relation.persistence,
                relation.access_method,
                relation.is_partition,
                relation.row_security,
                relation.force_row_security,
            ),
            ("r", "p", "heap", False, False, False),
        )
        contract_columns = tuple(
            (
                value.name,
                value.formatted_type,
                value.not_null,
                value.default_expression,
            )
            for value in contract.columns
            if value.relation == "cpk_node_control_attempts"
        )
        self.assertEqual(contract_columns, tuple(sorted(_ATTEMPT_COLUMNS)))

        constraints = {
            value.name: value
            for value in contract.constraints
            if value.relation == "cpk_node_control_attempts"
        }
        for name, (kind, expected_columns) in _ATTEMPT_KEY_CONSTRAINTS.items():
            with self.subTest(constraint=name):
                self.assertEqual(constraints[name].kind, kind)
                self.assertEqual(constraints[name].local_columns, expected_columns)
        for name, (local, referenced, remote) in _ATTEMPT_FOREIGN_KEYS.items():
            with self.subTest(foreign_key=name):
                value = constraints[name]
                self.assertEqual(value.kind, "f")
                self.assertEqual(value.local_columns, local)
                self.assertEqual(value.referenced_relation, referenced)
                self.assertEqual(value.referenced_columns, remote)
                self.assertFalse(value.deferrable)
                self.assertEqual(value.update_action, "a")
                self.assertEqual(value.delete_action, "a")
        foreign_keys = tuple(
            value for value in constraints.values() if value.kind == "f"
        )
        self.assertEqual(len(foreign_keys), len(_ATTEMPT_FOREIGN_KEYS))
        self.assertFalse(
            any(
                value.referenced_relation == "cpk_workspaces"
                and value.referenced_columns != ("workspace_id",)
                for value in foreign_keys
            )
        )
        checks = tuple(value for value in constraints.values() if value.kind == "c")
        self.assertEqual({value.name for value in checks}, _ATTEMPT_CHECK_CONSTRAINTS)
        self.assertTrue(all(value.validated for value in checks))
        self.assertEqual(
            set(constraints),
            _ATTEMPT_CHECK_CONSTRAINTS
            | set(_ATTEMPT_KEY_CONSTRAINTS)
            | set(_ATTEMPT_FOREIGN_KEYS),
        )
        check_text = "\n".join(value.check_expression or "" for value in checks)
        for required in (
            "octet_length(request_bytes)", "16384", "request_digest",
            "octet_length(transit_grant_bytes)", "2834", "transit_grant_digest",
            "octet_length(workload_grant_bytes)", "2111", "workload_grant_digest",
            "intent_fingerprint", "transit_key_registration_id",
            "workload_key_registration_id", "transit_authorization_id",
            "workload_authorization_id",
        ):
            with self.subTest(check=required):
                self.assertIn(required, check_text)

        indexes = {
            value.name: value
            for value in contract.indexes
            if value.relation == "cpk_node_control_attempts"
        }
        self.assertEqual(
            set(indexes),
            set(_ATTEMPT_KEY_CONSTRAINTS) | set(_ATTEMPT_SUPPORTING_INDEXES),
        )
        for name in _ATTEMPT_KEY_CONSTRAINTS:
            self.assertEqual(indexes[name].owning_constraint, name)
        for name, entries in _ATTEMPT_SUPPORTING_INDEXES.items():
            with self.subTest(supporting_index=name):
                self.assertIsNone(indexes[name].owning_constraint)
                self.assertEqual(indexes[name].key_entries, entries)
                self.assertFalse(indexes[name].unique)
                self.assertIsNone(indexes[name].predicate)
        self.assertFalse(any("time" in value.name for value in indexes.values()))
        self.assertFalse(any("digest" in value.name for value in indexes.values()))

    def test_public_scalars_and_store_selectors_fail_before_sql(self) -> None:
        attempt = self.attempt()
        candidates = (
            ("Operator/A", lambda value: replace(attempt, actor_subject=value)),
            (
                "projection/current",
                lambda value: replace(
                    attempt,
                    current_realized_projection_id=value,
                ),
            ),
            (
                "docker current",
                lambda value: replace(attempt, gateway_runtime_id=value),
            ),
            (
                "not-a-key-registration",
                lambda value: replace(
                    attempt,
                    transit_key_registration_id=value,
                ),
            ),
            (
                "not-an-authorization",
                lambda value: replace(
                    attempt,
                    workload_authorization_id=value,
                ),
            ),
            (
                "correlation with spaces",
                lambda value: replace(
                    attempt,
                    transit_correlation_id=value,
                ),
            ),
            (
                "CPK/SERVER",
                lambda value: replace(
                    attempt,
                    transit_grant=replace(attempt.transit_grant, issuer=value),
                ),
            ),
            (
                "WORKLOAD/SERVER",
                lambda value: replace(
                    attempt,
                    workload_grant=replace(attempt.workload_grant, issuer=value),
                ),
            ),
        )
        for candidate, factory in candidates:
            with self.subTest(candidate=candidate):
                self.assert_attempt_error(
                    lambda factory=factory, candidate=candidate: factory(candidate),
                    forbidden=candidate,
                )

        class UnexpectedSqlConnection:
            def execute(self, *_args, **_kwargs):
                raise AssertionError("invalid selector reached SQL")

        store = self.contract("NodeControlAttemptStore")(UnexpectedSqlConnection())
        invalid = "selector/" + "x" * 256
        selectors = (
            lambda: store.lock_request_id(invalid, "request-a"),
            lambda: store.lock_request_id("workspace-a", invalid),
            lambda: store.get(invalid),
            lambda: store.get_by_request_id(invalid, "request-a"),
            lambda: store.get_by_request_id("workspace-a", invalid),
        )
        for index, call in enumerate(selectors):
            with self.subTest(selector=index):
                self.assert_attempt_error(call, forbidden=invalid)

    def test_atlas_and_package_boundaries_explain_one_insert_only_truth(self) -> None:
        root = Path(__file__).resolve().parents[1]
        atlas = (root / "OPERATIONS_TABLE_ATLAS.md").read_text(encoding="utf-8")
        self.assertIn("### `cpk_node_control_attempts`", atlas)
        self.assertIn("Row membership means only INTENDED", atlas)
        self.assertIn("#1556", atlas)
        attempt_section = atlas.split("### `cpk_node_control_attempts`", 1)[1].split(
            "### `cpk_observations`",
            1,
        )[0]
        self.assertIn("unsigned", attempt_section.lower())
        self.assertIsNone(
            re.search(r"\bsigned grants\b", attempt_section.lower())
        )
        self.assertIn(
            current_schema_contract.CURRENT_POSTGRES_SCHEMA_CONTRACT_SHA256,
            atlas.splitlines()[2],
        )
        for name in _ATTEMPT_FOREIGN_KEYS:
            with self.subTest(atlas_foreign_key=name):
                self.assertIn(name, atlas)
        self.assertLess(
            atlas.index("### `cpk_node_control_attempts`"),
            atlas.index("### `cpk_realized_graph_projections`"),
        )

        package = root / "src" / "control_plane_kit_operations"
        modules = (
            package / "node_control_attempts.py",
            package / "postgres" / "node_control_attempt_store.py",
        )
        self.assertTrue(all(path.is_file() for path in modules))
        imports: set[str] = set()
        calls: set[str] = set()
        for path in modules:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
                elif isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
        self.assertFalse(
            imports
            & {
                "fastapi",
                "httpx",
                "docker",
                "jwt",
                "control_plane_kit_interpreters",
            }
        )
        self.assertEqual(
            calls & {"commit", "rollback", "transaction", "connect"},
            set(),
        )


class NodeControlAttemptPostgresTests(
    _NodeControlAttemptFixture,
    unittest.TestCase,
):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            self.fail("CPK_OPERATIONS_TEST_DATABASE_URL is required")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        schema_installed = False
        try:
            install_schema(self.connection)
            schema_installed = True
            self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
            self._seed_truth(self.connection)
            self.store_type = self.contract("NodeControlAttemptStore")
        except BaseException:
            if schema_installed:
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
            self.connection.close()
            raise

    def tearDown(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
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

    def _assert_corrupt(
        self,
        store,
        *,
        attempt_id: str = "attempt-a",
        forbidden: str | None = None,
    ) -> None:
        with self.assertRaises(self.contract("NodeControlAttemptCorrupt")) as caught:
            store.get(attempt_id)
        rendered = str(caught.exception)
        self.assertLessEqual(len(rendered), 128)
        rendered_repr = repr(caught.exception)
        self.assertLessEqual(len(rendered_repr), 160)
        if forbidden is not None:
            self.assertNotIn(forbidden, rendered)
            self.assertNotIn(forbidden, rendered_repr)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def _attempt_catalog_snapshot(self, connection) -> tuple[tuple[object, ...], ...]:
        return tuple(
            connection.execute(
                """
                SELECT 'relation', relation.relname, relation.relkind::text,
                       relation.relpersistence::text
                FROM pg_class AS relation
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = 'cpk_node_control_attempts'
                UNION ALL
                SELECT 'column', attribute.attname,
                       format_type(attribute.atttypid, attribute.atttypmod),
                       attribute.attnotnull::text
                FROM pg_attribute AS attribute
                JOIN pg_class AS relation ON relation.oid = attribute.attrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = 'cpk_node_control_attempts'
                  AND attribute.attnum > 0 AND NOT attribute.attisdropped
                UNION ALL
                SELECT 'constraint', owned.conname,
                       owned.contype::text, pg_get_constraintdef(owned.oid, false)
                FROM pg_constraint AS owned
                JOIN pg_class AS relation ON relation.oid = owned.conrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = 'cpk_node_control_attempts'
                UNION ALL
                SELECT 'index', indexed.relname, '', pg_get_indexdef(indexed.oid)
                FROM pg_index AS owned
                JOIN pg_class AS relation ON relation.oid = owned.indrelid
                JOIN pg_class AS indexed ON indexed.oid = owned.indexrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = 'cpk_node_control_attempts'
                ORDER BY 1, 2, 3, 4
                """
            ).fetchall()
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
        corruptions = (
            ("request_bytes", b" "),
            ("transit_grant_bytes", b" "),
            ("workload_grant_bytes", b" "),
        )
        for column, suffix in corruptions:
            with self.subTest(bytes=column):
                self.connection.execute(
                    f"UPDATE cpk_node_control_attempts SET {column} = {column} || %s",
                    (suffix,),
                )
                self._assert_corrupt(store)
                self.connection.execute("TRUNCATE cpk_node_control_attempts")
                store.add(attempt)

        strict_candidates = (
            (
                "request_bytes", "request_digest",
                attempt.request_bytes + b" ",
                self.request(2).canonical_bytes(),
            ),
            (
                "transit_grant_bytes", "transit_grant_digest",
                attempt.transit_grant_bytes + b" ",
                self.grants(self.request(2))[0].canonical_bytes(),
            ),
            (
                "workload_grant_bytes", "workload_grant_digest",
                attempt.workload_grant_bytes + b" ",
                self.grants(self.request(2))[1].canonical_bytes(),
            ),
        )
        for bytes_column, digest_column, noncanonical, different in strict_candidates:
            for candidate_kind, candidate in (
                ("noncanonical", noncanonical),
                ("different-canonical", different),
            ):
                with self.subTest(wire=bytes_column, candidate=candidate_kind):
                    digest = hashlib.sha256(candidate).hexdigest()
                    self.connection.execute(
                        f"""
                        UPDATE cpk_node_control_attempts
                        SET {bytes_column}=%s, {digest_column}=%s
                        """,
                        (candidate, digest),
                    )
                    self._assert_corrupt(store, forbidden=digest)
                    self.connection.execute("TRUNCATE cpk_node_control_attempts")
                    store.add(attempt)

        for column in (
            "request_digest",
            "transit_grant_digest",
            "workload_grant_digest",
        ):
            with self.subTest(digest=column):
                self.connection.execute(
                    f"UPDATE cpk_node_control_attempts SET {column}=%s",
                    ("0" * 64,),
                )
                self._assert_corrupt(store, forbidden="0" * 64)
                self.connection.execute("TRUNCATE cpk_node_control_attempts")
                store.add(attempt)

        for column, candidate in (
            ("transit_issuer", "other-server"),
            ("transit_key_id", "other-transit-key"),
            ("transit_jti", "other-transit-jti"),
            ("workload_issuer", "other-server"),
            ("workload_key_id", "other-workload-key"),
            ("workload_jti", "other-workload-jti"),
        ):
            with self.subTest(duplicate_scalar=column):
                self.connection.execute(
                    f"UPDATE cpk_node_control_attempts SET {column}=%s",
                    (candidate,),
                )
                self._assert_corrupt(store, forbidden=candidate)
                self.connection.execute("TRUNCATE cpk_node_control_attempts")
                store.add(attempt)

        self.connection.execute(
            "UPDATE cpk_node_control_attempts SET request_id='request-b'"
        )
        self._assert_corrupt(store, forbidden="request-b")
        self.connection.execute("TRUNCATE cpk_node_control_attempts")
        store.add(attempt)

        self.connection.execute(
            "UPDATE cpk_node_control_attempts SET attempt_id='attempt-b'"
        )
        self._assert_corrupt(store, attempt_id="attempt-b", forbidden="attempt-b")
        self.connection.execute("TRUNCATE cpk_node_control_attempts")
        store.add(attempt)

        self.connection.execute(
            """
            INSERT INTO cpk_graph_versions
              (graph_id, workspace_id, version, graph_descriptor, created_by,
               created_at, metadata)
            VALUES ('graph-other', 'workspace-a', 2, '{}', 'operator-a',
                    '2027-01-15T09:00:00Z', '{}')
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_realized_graph_projections
              (projection_id, workspace_id, source_authored_graph_id,
               projection_kind, projection_key, projection_digest,
               graph_descriptor, created_by, created_at)
            VALUES ('projection-other', 'workspace-a', 'graph-other', 'identity',
                    'other', %s, '{}', 'operator-a', '2027-01-15T09:00:00Z')
            """,
            ("3" * 64,),
        )
        self.connection.execute(
            """
            UPDATE cpk_node_control_attempts
            SET current_graph_id='graph-other',
                current_realized_projection_id='projection-other'
            """
        )
        self._assert_corrupt(store, forbidden="graph-other")
        self.connection.execute("TRUNCATE cpk_node_control_attempts")

    def test_prior_attempt_shape_requires_reset_without_repair(self) -> None:
        drift = psycopg.connect(self.database_url)
        try:
            drift.execute(
                "ALTER TABLE cpk_node_control_attempts ADD COLUMN forbidden text"
            )
            before = self._attempt_catalog_snapshot(drift)
            with self.assertRaisesRegex(
                SchemaInstallationError,
                "operations schema reset is required",
            ):
                install_schema(drift)
            self.assertEqual(self._attempt_catalog_snapshot(drift), before)
            observed = drift.execute(
                """
                SELECT count(*)
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'cpk_node_control_attempts'
                  AND column_name = 'forbidden'
                """
            ).fetchone()
            self.assertEqual(observed, (1,))
            drift.rollback()
        finally:
            drift.close()

    def test_exact_current_reentry_preserves_catalog_and_rows(self) -> None:
        attempt = self.attempt(1e20)
        self.store_type(self.connection).add(attempt)
        before_catalog = self._attempt_catalog_snapshot(self.connection)
        before_rows = self.connection.execute(
            "SELECT * FROM cpk_node_control_attempts WHERE attempt_id='attempt-a'"
        ).fetchall()
        self.connection.execute(
            """
            UPDATE cpk_workspaces
            SET current_graph_id=NULL, current_realized_projection_id=NULL
            WHERE workspace_id='workspace-a'
            """
        )
        install_schema(self.connection)
        self.assertEqual(
            self._attempt_catalog_snapshot(self.connection),
            before_catalog,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT * FROM cpk_node_control_attempts WHERE attempt_id='attempt-a'"
            ).fetchall(),
            before_rows,
        )
        self.assertEqual(self.store_type(self.connection).get("attempt-a"), attempt)

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

    def test_each_key_and_authorization_witness_is_checked_independently(self) -> None:
        store = self.store_type(self.connection)
        store.add(self.attempt())
        mutations = (
            (
                "cpk_delegation_signing_keys", "dkey_" + "a" * 64,
                "purpose", "gateway-probe", "gateway-node-control-transit",
            ),
            (
                "cpk_delegation_signing_keys", "dkey_" + "a" * 64,
                "issuer", "mismatch-server", "cpk-server",
            ),
            (
                "cpk_delegation_signing_keys", "dkey_" + "a" * 64,
                "key_id", "mismatch-key", "transit-key",
            ),
            (
                "cpk_delegation_signing_keys", "dkey_" + "a" * 64,
                "private_key_reference", "secret://keys/other",
                "secret://keys/transit",
            ),
            (
                "cpk_delegation_signing_keys", "dkey_" + "b" * 64,
                "purpose", "gateway-probe", "workload-node-control",
            ),
            (
                "cpk_delegation_signing_keys", "dkey_" + "b" * 64,
                "issuer", "mismatch-server", "cpk-server",
            ),
            (
                "cpk_delegation_signing_keys", "dkey_" + "b" * 64,
                "key_id", "mismatch-key", "workload-key",
            ),
            (
                "cpk_delegation_signing_keys", "dkey_" + "b" * 64,
                "private_key_reference", "secret://keys/other",
                "secret://keys/workload",
            ),
            (
                "cpk_secret_use_authorizations", "suse_" + "c" * 64,
                "use_intent", "workload.node-control-signing-key",
                "gateway.node-control-transit-signing-key",
            ),
            (
                "cpk_secret_use_authorizations", "suse_" + "c" * 64,
                "actor_subject", "operator-b", "operator-a",
            ),
            (
                "cpk_secret_use_authorizations", "suse_" + "c" * 64,
                "correlation_id", "transit-correlation-b",
                "transit-correlation-a",
            ),
            (
                "cpk_secret_use_authorizations", "suse_" + "c" * 64,
                "secret_reference", "secret://keys/other",
                "secret://keys/transit",
            ),
            (
                "cpk_secret_use_authorizations", "suse_" + "d" * 64,
                "use_intent", "gateway.node-control-transit-signing-key",
                "workload.node-control-signing-key",
            ),
            (
                "cpk_secret_use_authorizations", "suse_" + "d" * 64,
                "actor_subject", "operator-b", "operator-a",
            ),
            (
                "cpk_secret_use_authorizations", "suse_" + "d" * 64,
                "correlation_id", "workload-correlation-b",
                "workload-correlation-a",
            ),
            (
                "cpk_secret_use_authorizations", "suse_" + "d" * 64,
                "secret_reference", "secret://keys/other",
                "secret://keys/workload",
            ),
        )
        for table, identity, column, wrong, original in mutations:
            with self.subTest(table=table, identity=identity, column=column):
                identity_column = (
                    "registration_id"
                    if table == "cpk_delegation_signing_keys"
                    else "authorization_id"
                )
                self.connection.execute(
                    f"UPDATE {table} SET {column}=%s WHERE {identity_column}=%s",
                    (wrong, identity),
                )
                self._assert_corrupt(store, forbidden=str(wrong))
                self.connection.execute(
                    f"UPDATE {table} SET {column}=%s WHERE {identity_column}=%s",
                    (original, identity),
                )

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
        retry = psycopg.connect(self.database_url)
        try:
            retry_store = self.store_type(retry)
            retry_store.lock_request_id("workspace-a", "request-a")
            self.assertEqual(
                retry_store.get_by_request_id("workspace-a", "request-a"),
                self.attempt(),
            )
            retry.rollback()
        finally:
            retry.close()
        conflicting = self.attempt()
        with self.assertRaises(self.contract("NodeControlAttemptConflict")) as caught:
            store.add(
                replace(
                    conflicting,
                    attempt_id="attempt-b",
                    transit_grant=replace(
                        conflicting.transit_grant,
                        attempt_id="attempt-b",
                    ),
                )
            )
        self.assertLessEqual(len(str(caught.exception)), 128)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
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

    def test_invalid_relational_witness_is_bounded_and_candidate_free(self) -> None:
        candidate = "dkey_" + "9" * 64
        invalid = replace(
            self.attempt(),
            transit_key_registration_id=candidate,
        )
        with self.assertRaises(self.contract("NodeControlAttemptError")) as caught:
            self.store_type(self.connection).add(invalid)
        for rendered in (str(caught.exception), repr(caught.exception)):
            self.assertLessEqual(len(rendered), 160)
            self.assertNotIn(candidate, rendered)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_advisory_request_lock_serializes_two_connections(self) -> None:
        first = psycopg.connect(self.database_url)
        second = psycopg.connect(self.database_url)
        ready = threading.Event()
        acquired = threading.Event()
        errors: list[BaseException] = []
        thread = None
        try:
            self.store_type(first).lock_request_id("workspace-a", "request-a")

            def acquire() -> None:
                ready.set()
                try:
                    self.store_type(second).lock_request_id(
                        "workspace-a",
                        "request-a",
                    )
                    acquired.set()
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=acquire)
            thread.start()
            self.assertTrue(ready.wait(timeout=2))
            deadline = time.monotonic() + 2
            observed_wait = False
            while time.monotonic() < deadline:
                row = self.connection.execute(
                    """
                    SELECT state, wait_event_type, wait_event
                    FROM pg_stat_activity
                    WHERE pid=%s
                    """,
                    (second.info.backend_pid,),
                ).fetchone()
                if row is not None and row[0] == "active" and row[1] == "Lock":
                    observed_wait = True
                    break
                time.sleep(0.01)
            self.assertTrue(observed_wait)
            self.assertFalse(acquired.is_set())
            first.commit()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(acquired.is_set())
            second.rollback()
        finally:
            first.commit()
            if thread is not None and thread.is_alive():
                thread.join(timeout=5)
            if thread is not None and thread.is_alive():
                second.cancel()
                thread.join(timeout=5)
            first.close()
            if thread is not None and thread.is_alive():
                second.close()
                thread.join(timeout=5)
            elif not second.closed:
                second.rollback()
                second.close()


if __name__ == "__main__":
    unittest.main()
