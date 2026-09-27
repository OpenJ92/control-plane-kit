from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
import json
import os
import threading
import time
import unittest

import psycopg

from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey,
)
from control_plane_kit_core.identity import (
    AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind, WorkspaceGrant,
)
from control_plane_kit_core.operations.services import ControlPlaneServiceRole
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.cpk_server import (
    CpkServerApplicationError, CpkServerReadService,
)
from control_plane_kit_operations.delegation_signing_keys import RegisteredDelegationSigningKey
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import WorkspaceRecord


SURFACE = DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ
HEALTH = DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ
CONTROL = DelegationKeyPurpose.WORKLOAD_NODE_CONTROL
ROUTE = "read.workload-verifier-configuration"
NOW = "2026-09-27T10:00:00Z"


@dataclass(frozen=True)
class RouteRequest:
    surface: str
    route_id: str
    service_role: ControlPlaneServiceRole
    path_parameters: dict[str, object]
    payload: dict[str, object]
    principal: AuthenticatedPrincipal


def principal(workspace="workspace-a", scopes=(PolicyScope.DELEGATION_KEY_READ,)):
    return AuthenticatedPrincipal(
        PrincipalIdentity("test-issuer", "operator-a", PrincipalKind.OPERATOR),
        (WorkspaceGrant(workspace, scopes),),
    )


class WorkloadVerifierConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not self.database_url:
            raise RuntimeError("Run ./control-plane-kit-operations/test.sh for PostgreSQL")
        self.connection = psycopg.connect(self.database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        with self.uow() as uow:
            for workspace in ("workspace-a", "workspace-b"):
                uow.stores.workspaces.create(WorkspaceRecord(workspace, workspace))
            uow.commit()

    def tearDown(self):
        self.connection.close()

    def uow(self):
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def seed(self, purpose=SURFACE, *, count=2, workspace="workspace-a",
             issuer="issuer-a", active=True, padding=0):
        records = []
        with self.uow() as uow:
            for index in range(count):
                key_id = f"key-{index}"
                record = RegisteredDelegationSigningKey(
                    registration_id=f"{workspace}-{purpose.value}-{issuer}-{index}",
                    workspace_id=workspace, purpose=purpose, issuer=issuer,
                    public_key=DelegationPublicKey(key_id, DelegationKeyAlgorithm.ED25519,
                        "-----BEGIN PUBLIC KEY-----\n" + f"{purpose.value}-{issuer}-{index}-"
                        + "A" * padding + "public-material\n-----END PUBLIC KEY-----\n"),
                    private_key_reference=SecretReference("secret://private-canary/key"),
                    admitted_by="operator-a", admitted_at=NOW,
                )
                records.append(uow.stores.delegation_signing_keys.register(record))
            if active:
                uow.stores.delegation_signing_keys.activate(
                    workspace, purpose, issuer, records[0].key_id,
                    activated_by="operator-a", activated_at=NOW)
            uow.commit()
        return records

    def request(self, purposes=(SURFACE,), *, surface="http", identity=None):
        selector = ",".join(purpose.value for purpose in purposes)
        return self.raw_request(selector, surface=surface, identity=identity)

    def raw_request(self, selector, *, surface="http", identity=None):
        arguments = {"workspace_id": "workspace-a", "purposes": selector}
        return RouteRequest(
            surface=surface, route_id=ROUTE, service_role=ControlPlaneServiceRole.READS,
            path_parameters=arguments if surface == "http" else {},
            payload={} if surface == "http" else arguments,
            principal=identity or principal(),
        )

    def query(self, purposes=(SURFACE,), **kwargs):
        return CpkServerReadService(self.uow).handle(self.request(purposes, **kwargs))

    def assert_unavailable(self, callback):
        with self.assertRaises(CpkServerApplicationError) as caught:
            callback()
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(str(caught.exception), "workload verifier configuration is unavailable")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn("private-canary", repr(caught.exception))

    def test_public_http_mcp_snapshot_has_exact_families_and_issuer_membership(self):
        expected = {}
        for purpose in (SURFACE, HEALTH, CONTROL):
            expected[purpose.value] = self.seed(purpose)
        self.seed(SURFACE, workspace="workspace-b")
        self.seed(SURFACE, issuer="unselected-issuer", active=False)
        self.seed(DelegationKeyPurpose.GATEWAY_PROBE)
        first = self.query((HEALTH, CONTROL, SURFACE))
        self.assertEqual(first, self.query((SURFACE, HEALTH, CONTROL), surface="mcp"))
        families = first["workload_verifier_configuration"]["verifiers"]
        self.assertEqual([family["purpose"] for family in families], sorted(expected))
        for family in families:
            self.assertEqual(set(family), {"purpose", "issuer", "public_keys"})
            self.assertEqual(family["issuer"], "issuer-a")
            self.assertEqual(family["public_keys"], [
                {"key_id": record.key_id, "algorithm": "ed25519",
                 "public_key_pem": record.public_key.public_key_pem}
                for record in expected[family["purpose"]]
            ])
        rendered = json.dumps(first)
        for forbidden in ("private-canary", "private_key_reference", "registration_id",
                          "unselected-issuer", "gateway-probe", "audience", "status"):
            self.assertNotIn(forbidden, rendered)

    def test_missing_or_ambiguous_current_authority_is_bounded(self):
        self.seed(SURFACE)
        self.assert_unavailable(lambda: self.query((SURFACE, HEALTH)))
        self.seed(SURFACE, issuer="another-active-issuer")
        self.assert_unavailable(self.query)

    def test_closed_family_selector_rejects_malformed_or_excess_input(self):
        self.seed(SURFACE)
        # Establish the public entrance before checking malformed selections.
        self.query()
        for value in ("", SURFACE.value + ",", SURFACE.value + "," + SURFACE.value,
                      HEALTH.value, "gateway-probe", SURFACE.value + ",unknown",
                      ",".join([SURFACE.value] * 4), "x" * 193, True):
            for surface in ("http", "mcp"):
                with self.subTest(value=value, surface=surface):
                    with self.assertRaises(CpkServerApplicationError) as caught:
                        CpkServerReadService(self.uow).handle(self.raw_request(value, surface=surface))
                    self.assertEqual(caught.exception.status, 400)
                    self.assertNotIn("private-canary", str(caught.exception))

    def test_verification_read_bounds_materialization_and_refuses_overflow(self):
        self.seed(SURFACE, count=18)
        with self.uow() as uow:
            store = uow.stores.delegation_signing_keys
            bounded = store.list_for_verification("workspace-a", SURFACE, "issuer-a", limit=17)
            self.assertEqual(len(bounded), 17)
            self.assertEqual(len(store.list_for_verification("workspace-a", SURFACE, "issuer-a")), 18)
        self.assert_unavailable(self.query)

    def test_full_envelope_size_is_bounded_even_with_valid_family_counts(self):
        self.seed(SURFACE, count=16, padding=2500)
        self.seed(HEALTH, count=16, padding=2500)
        self.assert_unavailable(lambda: self.query((SURFACE, HEALTH)))

    def test_workspace_read_authority_precedes_any_store_acquisition(self):
        def forbidden_uow():
            self.fail("unauthorized read acquired stores")
        service = CpkServerReadService(forbidden_uow)
        for identity in (principal("workspace-b"), principal(scopes=(PolicyScope.DELEGATION_KEY_REGISTER,))):
            for surface in ("http", "mcp"):
                with self.subTest(surface=surface, identity=identity):
                    with self.assertRaises(CpkServerApplicationError) as caught:
                        service.handle(self.request(surface=surface, identity=identity))
                    self.assertEqual(caught.exception.status, 403)

    def test_cross_family_read_holds_real_purpose_locks_until_owning_uow_exits(self):
        self.seed(SURFACE)
        self.seed(HEALTH)
        self.query((HEALTH, SURFACE))
        first_lock = threading.Event()
        continue_read = threading.Event()
        writer_ready = threading.Event()
        writer_done = threading.Event()
        writer_pid = []
        purpose_locks = []

        class ObservedConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, query, params=()):
                result = self.connection.execute(query, params)
                if "pg_advisory_xact_lock_shared" in str(query):
                    purpose_locks.append(params[0])
                    if len(purpose_locks) == 1:
                        first_lock.set()
                        if not continue_read.wait(timeout=10):
                            raise AssertionError("read observation was not released")
                return result

        def read_uow():
            return PostgresUnitOfWork(lambda: ObservedConnection(psycopg.connect(self.database_url)))

        def revoke():
            connection = psycopg.connect(self.database_url)
            writer_pid.append(connection.info.backend_pid)
            writer_ready.set()
            with PostgresUnitOfWork(lambda: connection) as uow:
                uow.stores.delegation_signing_keys.revoke(
                    "workspace-a", SURFACE, "issuer-a", "key-1",
                    revoked_by="operator-a", revoked_at=NOW)
                uow.commit()
            writer_done.set()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            reader = pool.submit(CpkServerReadService(read_uow).handle, self.request((HEALTH, SURFACE)))
            try:
                self.assertTrue(first_lock.wait(timeout=5))
                writer = pool.submit(revoke)
                self.assertTrue(writer_ready.wait(timeout=5))
                deadline = time.monotonic() + 5
                observed_wait = False
                while time.monotonic() < deadline:
                    row = self.connection.execute(
                        "SELECT state, wait_event_type FROM pg_stat_activity WHERE pid=%s",
                        (writer_pid[0],)).fetchone()
                    if row == ("active", "Lock"):
                        observed_wait = True
                        break
                    time.sleep(0.01)
                self.assertTrue(observed_wait)
                self.assertFalse(writer_done.is_set())
            finally:
                continue_read.set()
            snapshot = reader.result(timeout=10)
            writer.result(timeout=10)
        self.assertTrue(writer_done.is_set())
        self.assertEqual(purpose_locks, [
            f"delegation-key-purpose:workspace-a:{purpose.value}"
            for purpose in sorted((SURFACE, HEALTH), key=lambda value: value.value)
        ])
        old = {family["purpose"]: family for family in snapshot["workload_verifier_configuration"]["verifiers"]}
        new = self.query()["workload_verifier_configuration"]["verifiers"][0]
        self.assertEqual([key["key_id"] for key in old[SURFACE.value]["public_keys"]], ["key-0", "key-1"])
        self.assertEqual([key["key_id"] for key in new["public_keys"]], ["key-0"])


if __name__ == "__main__":
    unittest.main()
