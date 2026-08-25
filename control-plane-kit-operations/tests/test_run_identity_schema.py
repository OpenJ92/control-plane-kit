from __future__ import annotations

import os
import unittest
import uuid

import psycopg

import control_plane_kit_operations.postgres as postgres
from control_plane_kit_operations.postgres import current_schema_contract


_RUN_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"


def _direct_expression(column: str) -> str:
    return f'(({column} COLLATE "C") ~ \'{_RUN_PATTERN}\'::text)'


def _nullable_expression(column: str) -> str:
    return f'(({column} IS NULL) OR {_direct_expression(column)})'


_DIRECT_CHECKS = {
    "cpk_activity_runs_run_id_check": (
        "cpk_activity_runs",
        "run_id",
        _direct_expression("run_id"),
    ),
    "cpk_cloudflare_ingress_resources_removed_by_run_id_check": (
        "cpk_cloudflare_ingress_resources",
        "removed_by_run_id",
        _nullable_expression("removed_by_run_id"),
    ),
    "cpk_cloudflare_ingress_resources_source_run_id_check": (
        "cpk_cloudflare_ingress_resources",
        "source_run_id",
        _direct_expression("source_run_id"),
    ),
    "cpk_gateway_key_rotation_deployments_run_id_check": (
        "cpk_gateway_key_rotation_deployments",
        "run_id",
        _direct_expression("run_id"),
    ),
    "cpk_generated_ingress_secret_references_source_run_id_check": (
        "cpk_generated_ingress_secret_references",
        "source_run_id",
        _direct_expression("source_run_id"),
    ),
    "cpk_secret_use_authorizations_run_check": (
        "cpk_secret_use_authorizations",
        "run_id",
        _nullable_expression("run_id"),
    ),
}

_DERIVED_FKS = {
    "cpk_activity_events_run_id_fkey": (
        "cpk_activity_events",
        ("run_id",),
        "cpk_activity_runs",
        ("run_id",),
    ),
    "cpk_activity_runs_prior_run_id_fkey": (
        "cpk_activity_runs",
        ("prior_run_id",),
        "cpk_activity_runs",
        ("run_id",),
    ),
}
_DERIVED_COLUMNS = frozenset(
    (relation, local)
    for relation, local, _, _ in _DERIVED_FKS.values()
)

_INVALID_RUN_IDS = (
    "",
    " ",
    "-leading",
    ".leading",
    "_leading",
    ":leading",
    "slash/value",
    "space value",
    "line\nbreak",
    "trailing\n",
    "trailing\r",
    "nonascii-\u00e9",
    "a" * 201,
)

_ROW_SELECTORS = {
    "cpk_activity_runs_run_id_check": "run_id = 'run-root'",
    "cpk_cloudflare_ingress_resources_removed_by_run_id_check": (
        "workspace_id = 'workspace-a' AND ingress_id = 'ingress-removed'"
    ),
    "cpk_cloudflare_ingress_resources_source_run_id_check": (
        "workspace_id = 'workspace-a' AND ingress_id = 'ingress-removed'"
    ),
    "cpk_gateway_key_rotation_deployments_run_id_check": (
        "rotation_id = 'rotation-a' AND phase = 'overlap'"
    ),
    "cpk_generated_ingress_secret_references_source_run_id_check": (
        "workspace_id = 'workspace-a' AND purpose = 'cloudflared-tunnel-token'"
    ),
    "cpk_secret_use_authorizations_run_check": (
        "authorization_id = 'suse_" + "1" * 64 + "'"
    ),
}


class _RecordingConnection:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls: list[str] = []

    @property
    def autocommit(self):
        return self.delegate.autocommit

    def transaction(self):
        return self.delegate.transaction()

    def execute(self, query, params=()):
        self.calls.append(query if isinstance(query, str) else str(query))
        if params == ():
            return self.delegate.execute(query)
        return self.delegate.execute(query, params)


def _capture_install_error(connection) -> BaseException:
    try:
        postgres.install_schema(connection)
    except BaseException as error:
        return error
    raise AssertionError("schema installation unexpectedly succeeded")


class RunIdentitySchemaStaticTests(unittest.TestCase):
    def test_contract_has_exact_owned_object_counts(self) -> None:
        contract = current_schema_contract.CURRENT_POSTGRES_SCHEMA_CONTRACT
        self.assertEqual(len(contract.relations), 35)
        self.assertEqual(len(contract.columns), 469)
        self.assertEqual(len(contract.constraints), 349)
        self.assertEqual(len(contract.indexes), 116)

    def test_contract_has_six_exact_direct_checks(self) -> None:
        constraints = {
            value.name: value
            for value in current_schema_contract.CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints
        }
        for name, (relation, column, expression) in _DIRECT_CHECKS.items():
            with self.subTest(direct=name):
                value = constraints[name]
                self.assertEqual(value.relation, relation)
                self.assertEqual(value.kind, "c")
                self.assertTrue(value.validated)
                self.assertFalse(value.deferrable)
                self.assertFalse(value.deferred)
                self.assertEqual(value.local_columns, (column,))
                self.assertEqual(value.check_expression, expression)

    def test_contract_has_two_derived_fks_without_duplicate_checks(self) -> None:
        constraints = {
            value.name: value
            for value in current_schema_contract.CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints
        }
        for name, expected in _DERIVED_FKS.items():
            with self.subTest(derived=name):
                value = constraints[name]
                relation, local, referenced, remote = expected
                self.assertEqual(value.relation, relation)
                self.assertEqual(value.kind, "f")
                self.assertTrue(value.validated)
                self.assertFalse(value.deferrable)
                self.assertFalse(value.deferred)
                self.assertEqual(value.local_columns, local)
                self.assertEqual(value.referenced_relation, referenced)
                self.assertEqual(value.referenced_columns, remote)

        check_columns = {
            (value.relation, value.local_columns)
            for value in constraints.values()
            if value.kind == "c"
        }
        self.assertTrue(_DERIVED_COLUMNS.isdisjoint(check_columns))


class RunIdentitySchemaPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required through the "
                "disposable operations Docker test environment"
            )
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.schema = f"run_identity_{uuid.uuid4().hex}"
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')
        postgres.install_schema(self.connection)
        self._seed_direct_rows()

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.close()

    def test_direct_checks_are_exact_and_boundary_values_round_trip(self) -> None:
        observed = {
            row[1]: (row[0], row[2], row[3], row[4])
            for row in self.connection.execute(
                "SELECT relation.relname, owned.conname, "
                "owned.convalidated, owned.condeferrable, "
                "pg_get_expr(owned.conbin, owned.conrelid) "
                "FROM pg_constraint AS owned "
                "JOIN pg_class AS relation ON relation.oid=owned.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() "
                "AND owned.conname = ANY(%s) ORDER BY owned.conname",
                (list(_DIRECT_CHECKS),),
            ).fetchall()
        }
        self.assertEqual(
            observed,
            {
                name: (relation, True, False, expression)
                for name, (relation, _, expression) in _DIRECT_CHECKS.items()
            },
        )

        for boundary in ("a", "a" * 200):
            for name, (table, column, _) in _DIRECT_CHECKS.items():
                with self.subTest(boundary=len(boundary), constraint=name):
                    self.connection.execute(
                        f"UPDATE {table} SET {column}=%s WHERE {_ROW_SELECTORS[name]}",
                        (boundary,),
                    )
                    self.assertEqual(
                        self.connection.execute(
                            f"SELECT {column} FROM {table} "
                            f"WHERE {column}=%s LIMIT 1",
                            (boundary,),
                        ).fetchone(),
                        (boundary,),
                    )
                    self._restore_direct_value(name)

        self.assertEqual(
            self.connection.execute(
                "SELECT removed_by_run_id FROM cpk_cloudflare_ingress_resources "
                "WHERE ingress_id='ingress-active'"
            ).fetchone(),
            (None,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT run_id FROM cpk_secret_use_authorizations "
                "WHERE authorization_id=%s",
                ("suse_" + "2" * 64,),
            ).fetchone(),
            (None,),
        )

    def test_each_direct_check_rejects_the_same_invalid_text_language(self) -> None:
        for name, (table, column, _) in _DIRECT_CHECKS.items():
            for candidate in _INVALID_RUN_IDS:
                with self.subTest(constraint=name, candidate=repr(candidate)[:32]):
                    with self.assertRaises(psycopg.errors.CheckViolation) as raised:
                        self.connection.execute(
                            f"UPDATE {table} SET {column}=%s "
                            f"WHERE {_ROW_SELECTORS[name]}",
                            (candidate,),
                        )
                    self.assertEqual(raised.exception.diag.constraint_name, name)

    def test_event_and_prior_run_identity_are_fk_derived_not_duplicated(self) -> None:
        catalog_rows = self.connection.execute(
            "SELECT relation.relname, owned.conname, owned.contype::text, "
            "owned.convalidated, owned.condeferrable, owned.condeferred, "
            "ARRAY(SELECT attribute.attname "
            "      FROM unnest(owned.conkey) WITH ORDINALITY "
            "           AS key(attnum, ordinal) "
            "      JOIN pg_attribute AS attribute "
            "        ON attribute.attrelid=owned.conrelid "
            "       AND attribute.attnum=key.attnum "
            "      ORDER BY key.ordinal) "
            "FROM pg_constraint AS owned "
            "JOIN pg_class AS relation ON relation.oid=owned.conrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=current_schema()"
        ).fetchall()
        constraints = {
            row[1]: row[2:6]
            for row in catalog_rows
        }
        check_columns = {
            (row[0], tuple(row[6]))
            for row in catalog_rows
            if row[2] == "c"
        }
        self.assertTrue(_DERIVED_COLUMNS.isdisjoint(check_columns))
        for name in _DERIVED_FKS:
            self.assertEqual(constraints[name], ("f", True, False, False))

        self.connection.execute(
            "INSERT INTO cpk_activity_events "
            "(event_id, run_id, ordinal, event_type, occurred_at, payload) "
            "VALUES ('event-valid', 'run-root', 1, 'run_opened', "
            "'2026-08-14T00:07:30Z', '{}')"
        )
        with self.assertRaises(psycopg.errors.ForeignKeyViolation) as event_error:
            self.connection.execute(
                "INSERT INTO cpk_activity_events "
                "(event_id, run_id, ordinal, event_type, occurred_at, payload) "
                "VALUES ('event-invalid', 'run/bad', 1, 'run_opened', "
                "'2026-08-14T00:07:31Z', '{}')"
            )
        self.assertEqual(
            event_error.exception.diag.constraint_name,
            "cpk_activity_events_run_id_fkey",
        )

        with self.assertRaises(psycopg.errors.ForeignKeyViolation) as prior_error:
            self._insert_retry("run-invalid-prior", "run/bad")
        self.assertEqual(
            prior_error.exception.diag.constraint_name,
            "cpk_activity_runs_prior_run_id_fkey",
        )
        self._insert_retry("run-retry", "run-root")

        with self.assertRaises(psycopg.errors.CheckViolation) as parent_error:
            self.connection.execute(
                "INSERT INTO cpk_activity_runs "
                "(run_id, plan_id, request_id, attempt, prior_run_id, status, "
                "created_at) "
                "VALUES ('run/bad', 'plan-a', 'request-a', 3, 'run-root', "
                "'claimed', "
                "'2026-08-14T00:22:00Z')"
            )
        self.assertEqual(
            parent_error.exception.diag.constraint_name,
            "cpk_activity_runs_run_id_check",
        )

    def test_exact_reentry_preserves_rows_and_object_identity(self) -> None:
        self.assertEqual(
            self.connection.execute(
                "SELECT request.status, request.claim_worker_id, "
                "run.status, request.claimed_at <= run.created_at, "
                "run.created_at <= run.started_at, run.started_at <= run.settled_at "
                "FROM cpk_execution_requests AS request "
                "JOIN cpk_activity_runs AS run ON run.request_id=request.request_id "
                "WHERE request.request_id='request-a'"
            ).fetchone(),
            ("claimed", "worker-a", "succeeded", True, True, True),
        )
        before_objects = self._object_identities()
        before_rows = self._run_rows()
        recorder = _RecordingConnection(self.connection)

        postgres.install_schema(recorder)

        self.assertEqual(self._object_identities(), before_objects)
        self.assertEqual(self._run_rows(), before_rows)
        for call in recorder.calls:
            self.assertNotRegex(
                " ".join(call.lower().split()),
                r"\b(create|alter|drop|truncate|insert|update|delete)\b",
            )

    def test_constraint_drift_is_reset_required_without_repair(self) -> None:
        name = "cpk_activity_runs_run_id_check"
        exact = _DIRECT_CHECKS[name][2]
        variants = {
            "missing": (),
            "weakened": (
                f"ALTER TABLE cpk_activity_runs ADD CONSTRAINT {name} "
                "CHECK (run_id <> '')",
            ),
            "renamed": (
                "ALTER TABLE cpk_activity_runs ADD CONSTRAINT "
                f"{name}_renamed CHECK ({exact})",
            ),
            "wrong-expression": (
                f"ALTER TABLE cpk_activity_runs ADD CONSTRAINT {name} "
                "CHECK (octet_length(run_id) <= 200)",
            ),
            "not-valid": (
                f"ALTER TABLE cpk_activity_runs ADD CONSTRAINT {name} "
                f"CHECK ({exact}) NOT VALID",
            ),
            "extra": (
                "ALTER TABLE cpk_activity_runs ADD CONSTRAINT "
                "cpk_activity_runs_run_id_extra CHECK (run_id <> '')",
            ),
        }
        for variant, statements in variants.items():
            with self.subTest(variant=variant):
                self._reset_schema()
                postgres.install_schema(self.connection)
                self.connection.execute(
                    "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
                    "VALUES ('sentinel', 'Sentinel', 'created')"
                )
                if variant != "extra":
                    self.connection.execute(
                        f"ALTER TABLE cpk_activity_runs DROP CONSTRAINT {name}"
                    )
                for statement in statements:
                    self.connection.execute(statement)
                before_constraints = self._constraint_snapshot()
                before_objects = self._object_identities()
                recorder = _RecordingConnection(self.connection)

                error = _capture_install_error(recorder)

                self.assertIs(type(error), postgres.SchemaInstallationError)
                self.assertEqual(str(error), "operations schema reset is required")
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)
                self.assertEqual(self._constraint_snapshot(), before_constraints)
                self.assertEqual(self._object_identities(), before_objects)
                self.assertEqual(
                    self.connection.execute(
                        "SELECT workspace_id, name, lifecycle FROM cpk_workspaces"
                    ).fetchall(),
                    [("sentinel", "Sentinel", "created")],
                )
                for call in recorder.calls:
                    self.assertNotRegex(
                        " ".join(call.lower().split()),
                        r"\b(create|alter|drop|truncate|insert|update|delete)\b",
                    )

    def _seed_direct_rows(self) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created');

            INSERT INTO cpk_graph_versions
              (graph_id, workspace_id, version, graph_descriptor, created_by,
               created_at)
            VALUES ('graph-a', 'workspace-a', 1,
                    '{"name":"graph-a","runtimes":{},"nodes":{},"edges":{},"public_ingresses":[]}',
                    'operator-a',
                    '2026-08-14T00:00:00Z');

            INSERT INTO cpk_realized_graph_projections
              (projection_id, workspace_id, source_authored_graph_id,
               projection_kind, projection_key, projection_digest,
               graph_descriptor, created_by, created_at)
            VALUES
              ('projection-8c93244f9a9f0eeeb374541de8926a2d7a4c79e01dee2039534bf38a6838c0e1',
               'workspace-a', 'graph-a', 'identity', 'identity',
               '8c93244f9a9f0eeeb374541de8926a2d7a4c79e01dee2039534bf38a6838c0e1',
               '{"name":"graph-a","runtimes":{},"nodes":{},"edges":{},"public_ingresses":[]}',
               'operator-a', '2026-08-14T00:00:00Z');

            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-a', 'workspace-a', 'operator-a', 'Deploy', 'open',
                    '2026-08-14T00:02:00Z');

            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id,
               base_realized_projection_id, desired_realized_projection_id,
               desired_graph_revision, status, created_at, payload)
            VALUES
              ('plan-a', 'session-a', 'graph-a', 'graph-a',
               'projection-8c93244f9a9f0eeeb374541de8926a2d7a4c79e01dee2039534bf38a6838c0e1',
               'projection-8c93244f9a9f0eeeb374541de8926a2d7a4c79e01dee2039534bf38a6838c0e1',
               1, 'planned', '2026-08-14T00:03:00Z', '{}');

            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, subject_kind, subject_payload,
               review_digest, requested_by, requested_at, required_scope,
               max_risk, destructive)
            VALUES ('approval-a', 'session-a', 'plan-a', 'activity-plan',
                    jsonb_build_object('kind', 'activity-plan',
                                       'plan_id', 'plan-a'),
                    encode(sha256(convert_to('activity-plan:plan-a', 'UTF8')),
                           'hex'),
                    'operator-a', '2026-08-14T00:04:00Z',
                    'plan:approve', 'low', false);

            INSERT INTO cpk_approval_decisions
              (decision_id, request_id, actor_id, decision, scope, decided_at)
            VALUES ('decision-a', 'approval-a', 'reviewer-a', 'approved',
                    'plan:approve', '2026-08-14T00:05:00Z');

            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint,
               claim_worker_id, claim_generation, claimed_at, lease_expires_at)
            VALUES ('request-a', 'workspace-a', 'session-a', 'plan-a', 'claimed',
                    'operator-a', '2026-08-14T00:06:00Z', 'approval-a',
                    'decision-a', 'execute-a', 'fingerprint-a', 'worker-a', 1,
                    '2026-08-14T00:06:30Z', '2026-08-14T01:00:00Z');

            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, status, created_at,
               started_at, settled_at)
            VALUES ('run-root', 'plan-a', 'request-a', 1, 'succeeded',
                    '2026-08-14T00:07:00Z', '2026-08-14T00:08:00Z',
                    '2026-08-14T00:09:00Z');

            INSERT INTO cpk_cloudflare_ingress_resources
              (workspace_id, runtime_id, ingress_id, epoch, status,
               authority_ref, provider_kind, tunnel_name, tunnel_id,
               dns_record_id, hostname, zone_id, lifecycle, created_at,
               observed_at, source_run_id, source_activity_id, source_event_id,
               removed_at, removed_by_run_id)
            VALUES
              ('workspace-a', 'runtime-a', 'ingress-removed', 1, 'removed',
               'authority-a', 'cloudflare', 'tunnel-a', 'tunnel-id-a',
               'dns-a', 'app.example.test', 'zone-a', 'ephemeral',
               '2026-08-14T00:10:00Z', '2026-08-14T00:11:00Z', 'run-source',
               'activity-a', 'event-a', '2026-08-14T00:12:00Z', 'run-remove'),
              ('workspace-a', 'runtime-a', 'ingress-active', 1, 'active',
               'authority-a', 'cloudflare', 'tunnel-b', 'tunnel-id-b',
               'dns-b', 'active.example.test', 'zone-a', 'ephemeral',
               '2026-08-14T00:10:00Z', '2026-08-14T00:11:00Z', 'run-source',
               'activity-a', 'event-a', NULL, NULL);

            INSERT INTO cpk_gateway_key_rotations
              (rotation_id, workspace_id, gateway_node_id, purpose, issuer,
               old_key_id, new_secret_reference, key_generation_correlation,
               maximum_grant_lifetime_seconds, clock_skew_seconds,
               correlation_id, requested_by, requested_at, intent_fingerprint,
               status, version)
            VALUES ('rotation-a', 'workspace-a', 'gateway-a', 'gateway-probe',
                    'issuer-a', 'key-a', 'secret://provider-a/key-b',
                    'generate-key-b', 120, 10, 'rotation-correlation-a',
                    'operator-a', '2026-08-14T00:13:00Z', repeat('c', 64),
                    'requested', 1);

            INSERT INTO cpk_gateway_key_rotation_deployments
              (rotation_id, phase, status, session_id, plan_id,
               approval_request_id, approval_decision_id, execution_request_id,
               run_id, base_authored_graph_id, base_realized_projection_id,
               desired_authored_graph_id, desired_realized_projection_id,
               desired_revision, prepared_at)
            VALUES ('rotation-a', 'overlap', 'prepared', 'session-a', 'plan-a',
                    'approval-a', 'decision-a', 'request-a', 'run-deployment',
                    'graph-a',
                    'projection-8c93244f9a9f0eeeb374541de8926a2d7a4c79e01dee2039534bf38a6838c0e1',
                    'graph-a',
                    'projection-8c93244f9a9f0eeeb374541de8926a2d7a4c79e01dee2039534bf38a6838c0e1',
                    1,
                    '2026-08-14T00:14:00Z');

            INSERT INTO cpk_generated_ingress_secret_references
              (workspace_id, purpose, secret_ref, recorded_at, source_run_id,
               source_activity_id, source_event_id)
            VALUES ('workspace-a', 'cloudflared-tunnel-token',
                    'secret://provider-a/tunnel/token',
                    '2026-08-14T00:15:00Z', 'run-generated', 'activity-a',
                    'event-a');

            INSERT INTO cpk_secret_providers
              (registration_id, workspace_id, provider_id, provider_kind,
               display_name, endpoint_reference, credential_reference,
               allowed_reference_prefixes, allowed_intents, admitted_by,
               admitted_at, status)
            VALUES ('provider-registration-a', 'workspace-a', 'provider-a',
                    'control-plane-kit-secrets', 'Provider A', 'provider-a',
                    'secret://bootstrap/provider-token',
                    '["secret://provider-a/"]', '["postgres.password"]',
                    'operator-a', '2026-08-14T00:16:00Z', 'active');

            INSERT INTO cpk_secret_references
              (registration_id, workspace_id, secret_reference,
               provider_registration_id, allowed_intents, admitted_by,
               admitted_at, status)
            VALUES ('reference-registration-a', 'workspace-a',
                    'secret://provider-a/database/password',
                    'provider-registration-a', '["postgres.password"]',
                    'operator-a', '2026-08-14T00:17:00Z', 'active');

            INSERT INTO cpk_secret_use_authorizations
              (authorization_id, workspace_id, reference_registration_id,
               provider_registration_id, secret_reference, use_intent,
               actor_subject, correlation_id, requested_at, intent_fingerprint,
               run_id)
            VALUES
              ('suse_1111111111111111111111111111111111111111111111111111111111111111',
               'workspace-a', 'reference-registration-a',
               'provider-registration-a',
               'secret://provider-a/database/password', 'postgres.password',
               'operator-a', 'authorization-a', '2026-08-14T00:18:00Z',
               repeat('d', 64), 'run-secret'),
              ('suse_2222222222222222222222222222222222222222222222222222222222222222',
               'workspace-a', 'reference-registration-a',
               'provider-registration-a',
               'secret://provider-a/database/password', 'postgres.password',
               'operator-a', 'authorization-b', '2026-08-14T00:19:00Z',
               repeat('e', 64), NULL);
            """
        )

    def _insert_retry(self, run_id: str, prior_run_id: str) -> None:
        self.connection.execute(
            "INSERT INTO cpk_activity_runs "
            "(run_id, plan_id, request_id, attempt, prior_run_id, status, created_at) "
            "VALUES (%s, 'plan-a', 'request-a', 2, %s, 'claimed', "
            "'2026-08-14T00:22:00Z')",
            (run_id, prior_run_id),
        )

    def _restore_direct_value(self, name: str) -> None:
        values = {
            "cpk_activity_runs_run_id_check": "run-root",
            "cpk_cloudflare_ingress_resources_removed_by_run_id_check": "run-remove",
            "cpk_cloudflare_ingress_resources_source_run_id_check": "run-source",
            "cpk_gateway_key_rotation_deployments_run_id_check": "run-deployment",
            "cpk_generated_ingress_secret_references_source_run_id_check": "run-generated",
            "cpk_secret_use_authorizations_run_check": "run-secret",
        }
        table, column, _ = _DIRECT_CHECKS[name]
        current = self.connection.execute(
            f"SELECT {column} FROM {table} WHERE {column} IN ('a', %s) LIMIT 1",
            ("a" * 200,),
        ).fetchone()
        if current is not None:
            self.connection.execute(
                f"UPDATE {table} SET {column}=%s WHERE {column}=%s",
                (values[name], current[0]),
            )

    def _reset_schema(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def _constraint_snapshot(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            self.connection.execute(
                "SELECT relation.relname, owned.conname, owned.oid::bigint, "
                "owned.convalidated, pg_get_expr(owned.conbin, owned.conrelid) "
                "FROM pg_constraint AS owned "
                "JOIN pg_class AS relation ON relation.oid=owned.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() ORDER BY 1, 2, 3"
            ).fetchall()
        )

    def _object_identities(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            self.connection.execute(
                "SELECT 'relation', relation.relname, relation.oid::bigint "
                "FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() "
                "UNION ALL "
                "SELECT 'constraint', owned.conname, owned.oid::bigint "
                "FROM pg_constraint AS owned "
                "JOIN pg_class AS relation ON relation.oid=owned.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() ORDER BY 1, 2, 3"
            ).fetchall()
        )

    def _run_rows(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            self.connection.execute(
                "SELECT run_id, prior_run_id, attempt, status "
                "FROM cpk_activity_runs ORDER BY attempt, run_id"
            ).fetchall()
        )


if __name__ == "__main__":
    unittest.main()
