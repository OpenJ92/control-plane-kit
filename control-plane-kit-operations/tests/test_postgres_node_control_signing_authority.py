from __future__ import annotations

import ast
from dataclasses import replace
import os
from pathlib import Path
import threading
import unittest

import psycopg
from psycopg.types.json import Jsonb

import control_plane_kit_operations as operations
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.postgres.delegation_signing_key_store import (
    DelegationSigningKeyStore,
)

import test_node_control_signing_authority as contract_tests


class NodeControlSigningAuthorityPostgresTests(
    contract_tests._SigningAuthorityFixture,
    unittest.TestCase,
):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            self.fail("CPK_OPERATIONS_TEST_DATABASE_URL is required")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        installed = False
        try:
            install_schema(self.connection)
            installed = True
            self._reset_fixture()
        except BaseException:
            if installed:
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
            self.connection.close()
            raise

    def tearDown(self) -> None:
        try:
            self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        finally:
            self.connection.close()

    def _reset_fixture(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self._seed_truth()
        self.contract("NodeControlAttemptStore")(self.connection).add(self.attempt())

    def _seed_truth(self) -> None:
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id,name,lifecycle) "
            "VALUES ('workspace-a','Workspace A','running')"
        )
        self.connection.execute(
            """
            INSERT INTO cpk_graph_versions
              (graph_id,workspace_id,version,graph_descriptor,created_by,created_at,metadata)
            VALUES ('graph-current','workspace-a',1,'{}'::jsonb,'operator-a',
                    '2027-01-15T07:00:00Z','{}'::jsonb)
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_realized_graph_projections
              (projection_id,workspace_id,source_authored_graph_id,projection_kind,
               projection_key,projection_digest,graph_descriptor,created_by,created_at)
            VALUES ('projection-current','workspace-a','graph-current','identity',
                    'current',%s,'{}'::jsonb,'operator-a','2027-01-15T07:00:00Z')
            """,
            ("1" * 64,),
        )
        self.connection.execute(
            """
            UPDATE cpk_workspaces
            SET current_graph_id='graph-current',
                current_realized_projection_id='projection-current'
            WHERE workspace_id='workspace-a'
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_secret_providers
              (registration_id,workspace_id,provider_id,provider_kind,display_name,
               endpoint_reference,credential_reference,allowed_reference_prefixes,
               allowed_intents,admitted_by,admitted_at,status,metadata)
            VALUES (%s,'workspace-a','workspace-secrets','control-plane-kit-secrets',
                    'Workspace secrets','provider-a',
                    'secret://workspace-secrets/provider-token',%s,%s,'operator-a',
                    '2027-01-15T07:00:00Z','active','{}'::jsonb)
            """,
            (
                "sprov_" + "e" * 64,
                Jsonb(["secret://workspace-secrets/keys"]),
                Jsonb(
                    [
                        "gateway.node-control-transit-signing-key",
                        "workload.node-control-signing-key",
                    ]
                ),
            ),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_secret_providers
              (registration_id,workspace_id,provider_id,provider_kind,display_name,
               endpoint_reference,credential_reference,allowed_reference_prefixes,
               allowed_intents,admitted_by,admitted_at,status,metadata)
            VALUES (%s,'workspace-a','other-secrets','control-plane-kit-secrets',
                    'Other secrets','provider-other',
                    'secret://other-secrets/provider-token',%s,%s,'operator-a',
                    '2027-01-15T07:00:00Z','active','{}'::jsonb)
            """,
            (
                "sprov_" + "f" * 64,
                Jsonb(["secret://workspace-secrets/keys"]),
                Jsonb(
                    [
                        "gateway.node-control-transit-signing-key",
                        "workload.node-control-signing-key",
                    ]
                ),
            ),
        )
        for suffix, reference, intent in (
            (
                "a",
                "secret://workspace-secrets/keys/transit",
                "gateway.node-control-transit-signing-key",
            ),
            (
                "b",
                "secret://workspace-secrets/keys/workload",
                "workload.node-control-signing-key",
            ),
        ):
            self.connection.execute(
                """
                INSERT INTO cpk_secret_references
                  (registration_id,workspace_id,secret_reference,
                   provider_registration_id,allowed_intents,admitted_by,
                   admitted_at,status,metadata)
                VALUES (%s,'workspace-a',%s,%s,%s,'operator-a',
                        '2027-01-15T07:00:00Z','active','{}'::jsonb)
                """,
                (
                    "sref_" + suffix * 64,
                    reference,
                    "sprov_" + "e" * 64,
                    Jsonb([intent]),
                ),
            )
        keys = (
            (
                "a",
                DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
                self.transit_public_key,
                "secret://workspace-secrets/keys/transit",
            ),
            (
                "b",
                DelegationKeyPurpose.WORKLOAD_NODE_CONTROL,
                self.workload_public_key,
                "secret://workspace-secrets/keys/workload",
            ),
        )
        for suffix, purpose, public_key, reference in keys:
            self.connection.execute(
                """
                INSERT INTO cpk_delegation_signing_keys
                  (registration_id,workspace_id,purpose,issuer,key_id,algorithm,
                   public_key_pem,public_fingerprint_sha256,private_key_reference,
                   admitted_by,admitted_at,status,activated_by,activated_at)
                VALUES (%s,'workspace-a',%s,'cpk-server',%s,%s,%s,%s,%s,
                        'operator-a','2027-01-15T07:00:00Z','active','operator-a',
                        '2027-01-15T07:00:00Z')
                """,
                (
                    "dkey_" + suffix * 64,
                    purpose.value,
                    public_key.key_id,
                    public_key.algorithm.value,
                    public_key.public_key_pem,
                    public_key.fingerprint_sha256,
                    reference,
                ),
            )
        for suffix, reference_suffix, reference, intent, correlation in (
            (
                "c",
                "a",
                "secret://workspace-secrets/keys/transit",
                "gateway.node-control-transit-signing-key",
                "transit-correlation-a",
            ),
            (
                "d",
                "b",
                "secret://workspace-secrets/keys/workload",
                "workload.node-control-signing-key",
                "workload-correlation-a",
            ),
        ):
            self.connection.execute(
                """
                INSERT INTO cpk_secret_use_authorizations
                  (authorization_id,workspace_id,reference_registration_id,
                   provider_registration_id,secret_reference,use_intent,
                   actor_subject,correlation_id,requested_at,intent_fingerprint,
                   operation_id)
                VALUES (%s,'workspace-a',%s,%s,%s,%s,'operator-a',%s,
                        '2027-01-15T08:00:00Z',%s,'attempt-a')
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

    def service(self, *, now: object = 100, factory=None):
        service_type = self.contract("NodeControlSigningAuthorityReloadService")
        return service_type(
            factory
            or (lambda: PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))),
            epoch_clock=lambda: now,
        )

    def command(self, attempt_id: object = "attempt-a"):
        return self.contract("ReloadNodeControlSigningAuthority")(attempt_id)

    def assert_unavailable(
        self,
        factory,
        *,
        forbidden: str | None = None,
    ) -> None:
        error_type = self.contract("NodeControlSigningAuthorityUnavailable")
        with self.assertRaises(error_type) as caught:
            factory()
        self.assertLessEqual(len(str(caught.exception)), 128)
        self.assertLessEqual(len(repr(caught.exception)), 180)
        if forbidden is not None:
            self.assertNotIn(forbidden, str(caught.exception))
            self.assertNotIn(forbidden, repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_reload_returns_exact_complete_pair_after_transaction(self) -> None:
        observations: list[str] = []

        def clock() -> int:
            observations.append("now")
            return 100

        service_type = self.contract("NodeControlSigningAuthorityReloadService")
        pair = service_type(
            lambda: PostgresUnitOfWork(lambda: psycopg.connect(self.database_url)),
            epoch_clock=clock,
        ).execute(self.command())
        self.assertEqual(observations, ["now"])
        self.assertEqual(pair.deferred_request.attempt_id, "attempt-a")
        self.assertEqual(pair.deferred_request.actor_subject, "operator-a")
        self.assertEqual(pair.transit.public_key, self.transit_public_key)
        self.assertEqual(pair.workload.public_key, self.workload_public_key)
        self.assertEqual(
            pair.transit.resolution_grant.reference.reference_id,
            "secret://workspace-secrets/keys/transit",
        )
        self.assertEqual(
            pair.workload.resolution_grant.reference.reference_id,
            "secret://workspace-secrets/keys/workload",
        )
        self.assertEqual(pair.transit.resolution_grant.operation_id, "attempt-a")
        self.assertEqual(pair.workload.resolution_grant.operation_id, "attempt-a")
        for value in (pair, pair.transit, pair.workload):
            rendered = repr(value)
            self.assertNotIn("BEGIN PUBLIC KEY", rendered)
            self.assertNotIn("provider-token", rendered)

    def test_selector_and_clock_fail_boundedly_without_provider_effect(self) -> None:
        class UnexpectedUnitOfWork:
            def __call__(self):
                raise AssertionError("invalid selector entered a unit of work")

        invalid = "attempt/" + "x" * 256
        self.assert_contract_error(
            lambda: self.command(invalid),
            forbidden=invalid,
        )
        self.assert_contract_error(
            lambda: self.contract("NodeControlSigningAuthorityReloadService")(
                UnexpectedUnitOfWork(),
                epoch_clock=lambda: 100,
            ).execute(object())
        )
        for now in (True, 1.5, -1, 2**53):
            with self.subTest(now=now):
                self.assert_unavailable(
                    lambda now=now: self.service(now=now).execute(self.command())
                )

        operational = psycopg.OperationalError("database unavailable")

        class FailedUnitOfWork:
            def __enter__(self):
                raise operational

            def __exit__(self, *_args):
                raise AssertionError("failed UoW should not exit")

        service_type = self.contract("NodeControlSigningAuthorityReloadService")
        with self.assertRaises(psycopg.OperationalError) as caught:
            service_type(
                lambda: FailedUnitOfWork(),
                epoch_clock=lambda: 100,
            ).execute(self.command())
        self.assertIs(caught.exception, operational)

    def test_current_lineage_is_rechecked_without_rewriting_attempt(self) -> None:
        before = self.connection.execute(
            "SELECT * FROM cpk_node_control_attempts WHERE attempt_id='attempt-a'"
        ).fetchone()
        self.connection.execute(
            """
            UPDATE cpk_workspaces
            SET current_graph_id=NULL,current_realized_projection_id=NULL
            WHERE workspace_id='workspace-a'
            """
        )
        self.assert_unavailable(lambda: self.service().execute(self.command()))
        self.assertEqual(
            self.connection.execute(
                "SELECT * FROM cpk_node_control_attempts WHERE attempt_id='attempt-a'"
            ).fetchone(),
            before,
        )

        self._reset_fixture()
        self.connection.execute(
            """
            INSERT INTO cpk_graph_versions
              (graph_id,workspace_id,version,graph_descriptor,created_by,created_at,metadata)
            VALUES ('graph-next','workspace-a',2,'{}'::jsonb,'operator-a',
                    '2027-01-15T09:00:00Z','{}'::jsonb)
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_realized_graph_projections
              (projection_id,workspace_id,source_authored_graph_id,projection_kind,
               projection_key,projection_digest,graph_descriptor,created_by,created_at)
            VALUES ('projection-next','workspace-a','graph-next','identity','next',
                    %s,'{}'::jsonb,'operator-a','2027-01-15T09:00:00Z')
            """,
            ("2" * 64,),
        )
        self.connection.execute(
            """
            UPDATE cpk_workspaces
            SET current_graph_id='graph-next',
                current_realized_projection_id='projection-next'
            WHERE workspace_id='workspace-a'
            """
        )
        self.assert_unavailable(lambda: self.service().execute(self.command()))

    def test_each_grant_uses_exact_not_before_and_expires_edges(self) -> None:
        self.service(now=100).execute(self.command())
        attempt = self.attempt()
        for family in ("transit", "workload"):
            for edge, replacement in (
                ("not-before", {"not_before": 101}),
                ("expires", {"expires_at": 100}),
            ):
                with self.subTest(family=family, edge=edge):
                    self.connection.execute("TRUNCATE cpk_node_control_attempts")
                    grant = replace(getattr(attempt, f"{family}_grant"), **replacement)
                    changed = replace(attempt, **{f"{family}_grant": grant})
                    self.contract("NodeControlAttemptStore")(self.connection).add(changed)
                    self.assert_unavailable(
                        lambda: self.service(now=100).execute(self.command())
                    )

    def test_active_key_selection_and_retained_public_identity_are_exact(self) -> None:
        mutations = (
            (
                "revoked",
                "UPDATE cpk_delegation_signing_keys SET status='revoked', "
                "revoked_by='operator-a',revoked_at='2027-01-15T09:00:00Z' "
                "WHERE registration_id=%s",
                ("dkey_" + "a" * 64,),
            ),
            (
                "changed-public-material",
                "UPDATE cpk_delegation_signing_keys SET public_key_pem=%s, "
                "public_fingerprint_sha256=%s WHERE registration_id=%s",
                (
                    contract_tests.PUBLIC_KEY_C,
                    DelegationPublicKey(
                        "transit-key",
                        DelegationKeyAlgorithm.ED25519,
                        contract_tests.PUBLIC_KEY_C,
                    ).fingerprint_sha256,
                    "dkey_" + "a" * 64,
                ),
            ),
            (
                "wrong-purpose",
                "UPDATE cpk_delegation_signing_keys SET purpose='gateway-probe' "
                "WHERE registration_id=%s",
                ("dkey_" + "a" * 64,),
            ),
            (
                "workload-revoked",
                "UPDATE cpk_delegation_signing_keys SET status='revoked', "
                "revoked_by='operator-a',revoked_at='2027-01-15T09:00:00Z' "
                "WHERE registration_id=%s",
                ("dkey_" + "b" * 64,),
            ),
            (
                "workload-changed-public-material",
                "UPDATE cpk_delegation_signing_keys SET public_key_pem=%s, "
                "public_fingerprint_sha256=%s WHERE registration_id=%s",
                (
                    contract_tests.PUBLIC_KEY_C,
                    DelegationPublicKey(
                        "workload-key",
                        DelegationKeyAlgorithm.ED25519,
                        contract_tests.PUBLIC_KEY_C,
                    ).fingerprint_sha256,
                    "dkey_" + "b" * 64,
                ),
            ),
        )
        for name, statement, parameters in mutations:
            with self.subTest(name=name):
                self.connection.execute(statement, parameters)
                self.assert_unavailable(lambda: self.service().execute(self.command()))
                self._reset_fixture()

        extra_key = DelegationPublicKey(
            "other-transit-key",
            DelegationKeyAlgorithm.ED25519,
            contract_tests.PUBLIC_KEY_C,
        )
        self.connection.execute(
            """
            INSERT INTO cpk_delegation_signing_keys
              (registration_id,workspace_id,purpose,issuer,key_id,algorithm,
               public_key_pem,public_fingerprint_sha256,private_key_reference,
               admitted_by,admitted_at,status,activated_by,activated_at)
            VALUES (%s,'workspace-a','gateway-node-control-transit','other-server',
                    %s,'ed25519',%s,%s,'secret://workspace-secrets/keys/transit',
                    'operator-a','2027-01-15T07:00:00Z','active','operator-a',
                    '2027-01-15T07:00:00Z')
            """,
            (
                "dkey_" + "f" * 64,
                extra_key.key_id,
                extra_key.public_key_pem,
                extra_key.fingerprint_sha256,
            ),
        )
        self.assert_unavailable(lambda: self.service().execute(self.command()))

        self._reset_fixture()
        extra_workload = DelegationPublicKey(
            "other-workload-key",
            DelegationKeyAlgorithm.ED25519,
            contract_tests.PUBLIC_KEY_C,
        )
        self.connection.execute(
            """
            INSERT INTO cpk_delegation_signing_keys
              (registration_id,workspace_id,purpose,issuer,key_id,algorithm,
               public_key_pem,public_fingerprint_sha256,private_key_reference,
               admitted_by,admitted_at,status,activated_by,activated_at)
            VALUES (%s,'workspace-a','workload-node-control','other-server',
                    %s,'ed25519',%s,%s,'secret://workspace-secrets/keys/workload',
                    'operator-a','2027-01-15T07:00:00Z','active','operator-a',
                    '2027-01-15T07:00:00Z')
            """,
            (
                "dkey_" + "9" * 64,
                extra_workload.key_id,
                extra_workload.public_key_pem,
                extra_workload.fingerprint_sha256,
            ),
        )
        self.assert_unavailable(lambda: self.service().execute(self.command()))

    def test_authorization_reference_and_provider_truth_is_independent(self) -> None:
        mutations = (
            (
                "wrong-operation",
                "UPDATE cpk_secret_use_authorizations SET operation_id='attempt-other' "
                "WHERE authorization_id=%s",
                ("suse_" + "c" * 64,),
            ),
            (
                "optional-provenance",
                "UPDATE cpk_secret_use_authorizations SET session_id='session-other' "
                "WHERE authorization_id=%s",
                ("suse_" + "c" * 64,),
            ),
            (
                "wrong-intent",
                "UPDATE cpk_secret_use_authorizations SET use_intent="
                "'gateway.probe-signing-key' WHERE authorization_id=%s",
                ("suse_" + "c" * 64,),
            ),
            (
                "inactive-reference",
                "UPDATE cpk_secret_references SET status='revoked',"
                "revoked_by='operator-a',revoked_at='2027-01-15T09:00:00Z' "
                "WHERE registration_id=%s",
                ("sref_" + "a" * 64,),
            ),
            (
                "inactive-provider",
                "UPDATE cpk_secret_providers SET status='revoked',"
                "revoked_by='operator-a',revoked_at='2027-01-15T09:00:00Z' "
                "WHERE registration_id=%s",
                ("sprov_" + "e" * 64,),
            ),
            (
                "reference-provider-substitution",
                "UPDATE cpk_secret_references SET provider_registration_id=%s "
                "WHERE registration_id=%s",
                ("sprov_" + "f" * 64, "sref_" + "a" * 64),
            ),
            (
                "authorization-provider-substitution",
                "UPDATE cpk_secret_use_authorizations SET provider_registration_id=%s "
                "WHERE authorization_id=%s",
                ("sprov_" + "f" * 64, "suse_" + "c" * 64),
            ),
        )
        for name, statement, parameters in mutations:
            with self.subTest(name=name):
                self.connection.execute(statement, parameters)
                self.assert_unavailable(lambda: self.service().execute(self.command()))
                self._reset_fixture()

    def test_store_query_is_bounded_read_only_and_schema_neutral(self) -> None:
        module_path = (
            Path(operations.__file__).parent
            / "postgres"
            / "node_control_signing_authority_store.py"
        )
        self.assertTrue(module_path.is_file(), "authority reload store is absent")
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        self.assertIn("fetchone", attributes)
        self.assertNotIn("fetchall", attributes)
        self.assertEqual(source.count(".execute("), 1)
        for relation in (
            "cpk_secret_use_authorizations",
            "cpk_secret_references",
            "cpk_secret_providers",
        ):
            self.assertIn(relation, source)
        self.assertNotIn("commit", attributes)
        self.assertNotIn("rollback", attributes)
        for token in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "CREATE "):
            self.assertNotIn(token, source.upper())
        self.assertIn("FOR SHARE", source.upper())
        bundle_fields = operations.PostgresStoreBundle.__dataclass_fields__
        self.assertIn("node_control_signing_authority", bundle_fields)
        before = self.connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema=current_schema()"
        ).fetchone()
        install_schema(self.connection)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema=current_schema()"
            ).fetchone(),
            before,
        )

    def test_reload_holds_workspace_purpose_and_secret_rows_through_commit(self) -> None:
        observed = threading.Event()
        release = threading.Event()
        result: list[object] = []
        failures: list[BaseException] = []

        def clock() -> int:
            observed.set()
            if not release.wait(5):
                raise AssertionError("reload clock was not released")
            return 100

        service_type = self.contract("NodeControlSigningAuthorityReloadService")
        service = service_type(
            lambda: PostgresUnitOfWork(lambda: psycopg.connect(self.database_url)),
            epoch_clock=clock,
        )

        def run_service() -> None:
            try:
                result.append(service.execute(self.command()))
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=run_service)
        thread.start()
        try:
            self.assertTrue(observed.wait(5), "reload did not reach locked clock")
            contenders = (
                (
                    "workspace",
                    "UPDATE cpk_workspaces "
                    "SET metadata=metadata || '{\"lock_probe\":true}'::jsonb "
                    "WHERE workspace_id='workspace-a'",
                ),
                (
                    "transit-authorization",
                    "UPDATE cpk_secret_use_authorizations "
                    "SET session_id='lock-proof-transit' "
                    "WHERE authorization_id='suse_" + "c" * 64 + "'",
                ),
                (
                    "workload-authorization",
                    "UPDATE cpk_secret_use_authorizations "
                    "SET session_id='lock-proof-workload' "
                    "WHERE authorization_id='suse_" + "d" * 64 + "'",
                ),
                (
                    "transit-reference",
                    "UPDATE cpk_secret_references "
                    "SET metadata=metadata || '{\"lock_probe\":true}'::jsonb "
                    "WHERE registration_id='sref_" + "a" * 64 + "'",
                ),
                (
                    "workload-reference",
                    "UPDATE cpk_secret_references "
                    "SET metadata=metadata || '{\"lock_probe\":true}'::jsonb "
                    "WHERE registration_id='sref_" + "b" * 64 + "'",
                ),
                (
                    "provider",
                    "UPDATE cpk_secret_providers "
                    "SET metadata=metadata || '{\"lock_probe\":true}'::jsonb "
                    "WHERE registration_id='sprov_" + "e" * 64 + "'",
                ),
            )
            for name, statement in contenders:
                with self.subTest(lock=name):
                    contender = psycopg.connect(self.database_url)
                    try:
                        contender.execute("SET LOCAL lock_timeout='250ms'")
                        with self.assertRaises(psycopg.errors.LockNotAvailable):
                            contender.execute(statement)
                    finally:
                        contender.rollback()
                        contender.close()

            for name, purpose, key_id in (
                (
                    "transit-purpose-key",
                    DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
                    "transit-key",
                ),
                (
                    "workload-purpose-key",
                    DelegationKeyPurpose.WORKLOAD_NODE_CONTROL,
                    "workload-key",
                ),
            ):
                with self.subTest(lock=name):
                    contender = psycopg.connect(self.database_url)
                    try:
                        contender.execute("SET LOCAL lock_timeout='250ms'")
                        with self.assertRaises(psycopg.errors.LockNotAvailable):
                            DelegationSigningKeyStore(contender).revoke(
                                "workspace-a",
                                purpose,
                                "cpk-server",
                                key_id,
                                revoked_by="operator-a",
                                revoked_at="2027-01-15T09:00:00Z",
                            )
                    finally:
                        contender.rollback()
                        contender.close()
        finally:
            release.set()
            thread.join(5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(result), 1)
        retry = psycopg.connect(self.database_url)
        try:
            for name, statement in contenders:
                with self.subTest(released=name):
                    self.assertEqual(retry.execute(statement).rowcount, 1)
            revoked = tuple(
                DelegationSigningKeyStore(retry).revoke(
                    "workspace-a",
                    purpose,
                    "cpk-server",
                    key_id,
                    revoked_by="operator-a",
                    revoked_at="2027-01-15T09:00:00Z",
                )
                for purpose, key_id in (
                    (
                        DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
                        "transit-key",
                    ),
                    (
                        DelegationKeyPurpose.WORKLOAD_NODE_CONTROL,
                        "workload-key",
                    ),
                )
            )
            retry.commit()
        finally:
            retry.close()
        self.assertEqual(tuple(key.status.value for key in revoked), ("revoked",) * 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT session_id FROM cpk_secret_use_authorizations "
                "ORDER BY authorization_id"
            ).fetchall(),
            [("lock-proof-transit",), ("lock-proof-workload",)],
        )
        for relation, expected_count in (
            ("cpk_workspaces", 1),
            ("cpk_secret_references", 2),
            ("cpk_secret_providers", 1),
        ):
            with self.subTest(released_state=relation):
                metadata = self.connection.execute(
                    f"SELECT metadata FROM {relation} "
                    "WHERE metadata ? 'lock_probe'"
                ).fetchall()
                self.assertEqual(len(metadata), expected_count)
                self.assertTrue(
                    all(row[0].get("lock_probe") is True for row in metadata)
                )


if __name__ == "__main__":
    unittest.main()
