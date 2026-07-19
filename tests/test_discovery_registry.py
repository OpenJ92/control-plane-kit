from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import threading
import unittest

import psycopg

from control_plane_kit import (
    DeregisterDiscoveryInstance,
    DiscoveryAuthority,
    DiscoveryConflict,
    DiscoveryDenied,
    DiscoveryIdentity,
    DiscoveryLease,
    DiscoveryMissing,
    DiscoveryOutcome,
    DiscoveryRegistration,
    DiscoveryRegistrationMode,
    DiscoveryRegistrationStatus,
    DiscoveryRegistryService,
    DiscoveryScope,
    Endpoint,
    EndpointScope,
    ExpireDiscoveryLeases,
    HeartbeatDiscoveryInstance,
    LiteralAddress,
    PostgresDiscoveryUnitOfWork,
    Protocol,
    RegisterDiscoveryInstance,
    ResolveDiscoveryService,
    install_discovery_schema,
)
from tests.postgres_case import PostgresStoreTestCase


NOW = datetime(2026, 7, 19, 12, tzinfo=timezone.utc)


class DiscoveryRegistryTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.database_url = os.environ["CPK_TEST_DATABASE_URL"]
        install_discovery_schema(lambda: psycopg.connect(self.database_url))
        self.connection.execute("DELETE FROM cpk_discovery_commands")
        self.connection.execute("DELETE FROM cpk_discovery_registrations")

    def factory(self) -> PostgresDiscoveryUnitOfWork:
        return PostgresDiscoveryUnitOfWork(
            lambda: psycopg.connect(self.database_url)
        )

    def service(self, *, clock=lambda: NOW, factory=None) -> DiscoveryRegistryService:
        return DiscoveryRegistryService(
            self.factory if factory is None else factory,
            clock=clock,
        )

    def test_register_resolve_and_exact_replay_share_one_command_ledger(self) -> None:
        service = self.service()
        command = RegisterDiscoveryInstance("register-a", _registration())

        first = service.execute(command, _manager())
        replay = service.execute(command, _manager())
        resolved = service.execute(
            ResolveDiscoveryService(
                "resolve-a", "workspace-a", "orders", NOW, 10
            ),
            _reader(),
        )

        self.assertIs(first.outcome, DiscoveryOutcome.REGISTERED)
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.registrations, first.registrations)
        self.assertEqual(resolved.registrations, first.registrations)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_discovery_commands"
            ).fetchone()[0],
            2,
        )

    def test_changed_command_intent_conflicts_without_mutation(self) -> None:
        service = self.service()
        service.execute(
            RegisterDiscoveryInstance("register-a", _registration()),
            _manager(),
        )
        changed = RegisterDiscoveryInstance(
            "register-a",
            _registration(instance_id="orders-b"),
        )

        with self.assertRaisesRegex(DiscoveryConflict, "different intent"):
            service.execute(changed, _manager())

        self.assertEqual(
            self.connection.execute(
                "SELECT instance_id FROM cpk_discovery_registrations"
            ).fetchall(),
            [("orders-a",)],
        )

    def test_heartbeat_requires_current_expiry_and_extends_revision(self) -> None:
        service = self.service()
        registered = service.execute(
            RegisterDiscoveryInstance("register-a", _registration()),
            _manager(),
        ).registrations[0]
        replacement = DiscoveryLease(NOW + timedelta(seconds=10), NOW + timedelta(seconds=60))
        heartbeat = HeartbeatDiscoveryInstance(
            "heartbeat-a",
            registered.registration.identity,
            registered.registration.lease.expires_at,
            replacement,
        )

        renewed = service.execute(heartbeat, _manager()).registrations[0]

        self.assertEqual(renewed.revision, 2)
        self.assertEqual(renewed.registration.lease, replacement)
        stale = HeartbeatDiscoveryInstance(
            "heartbeat-stale",
            renewed.registration.identity,
            registered.registration.lease.expires_at,
            DiscoveryLease(NOW + timedelta(seconds=20), NOW + timedelta(seconds=90)),
        )
        with self.assertRaisesRegex(DiscoveryConflict, "stale"):
            service.execute(stale, _manager())

    def test_expiry_boundary_removes_resolution_without_deleting_truth(self) -> None:
        service = self.service()
        registered = service.execute(
            RegisterDiscoveryInstance("register-a", _registration()),
            _manager(),
        ).registrations[0]
        at_boundary = registered.registration.lease.expires_at

        expired = service.execute(
            ExpireDiscoveryLeases("expire-a", "workspace-a", at_boundary, 10),
            _manager(),
        )
        resolved = service.execute(
            ResolveDiscoveryService(
                "resolve-a", "workspace-a", "orders", at_boundary, 10
            ),
            _reader(),
        )

        self.assertEqual(expired.affected_count, 1)
        self.assertIs(
            expired.registrations[0].status,
            DiscoveryRegistrationStatus.EXPIRED,
        )
        self.assertEqual(resolved.registrations, ())
        self.assertEqual(
            self.connection.execute(
                "SELECT status FROM cpk_discovery_registrations"
            ).fetchone()[0],
            "expired",
        )

    def test_expired_identity_can_register_again_but_cannot_heartbeat(self) -> None:
        service = self.service()
        original = service.execute(
            RegisterDiscoveryInstance("register-a", _registration()),
            _manager(),
        ).registrations[0]
        boundary = original.registration.lease.expires_at
        expired_service = self.service(clock=lambda: boundary)

        with self.assertRaisesRegex(DiscoveryConflict, "cannot be renewed"):
            expired_service.execute(
                HeartbeatDiscoveryInstance(
                    "heartbeat-expired",
                    original.registration.identity,
                    boundary,
                    DiscoveryLease(boundary, boundary + timedelta(seconds=30)),
                ),
                _manager(),
            )

        replacement = DiscoveryRegistration(
            original.registration.identity,
            original.registration.endpoint,
            original.registration.mode,
            DiscoveryLease(boundary, boundary + timedelta(seconds=30)),
        )
        registered = expired_service.execute(
            RegisterDiscoveryInstance("register-again", replacement),
            _manager(),
        ).registrations[0]
        self.assertEqual(registered.revision, 2)
        self.assertIs(registered.status, DiscoveryRegistrationStatus.ACTIVE)

    def test_deregister_and_self_registration_enforce_authority(self) -> None:
        service = self.service()
        self_registration = _registration(mode=DiscoveryRegistrationMode.SELF)
        with self.assertRaises(DiscoveryDenied):
            service.execute(
                RegisterDiscoveryInstance("register-denied", self_registration),
                _self_authority("another-instance"),
            )
        saved = service.execute(
            RegisterDiscoveryInstance("register-a", self_registration),
            _self_authority("orders-a"),
        ).registrations[0]
        retired = service.execute(
            DeregisterDiscoveryInstance(
                "deregister-a",
                saved.registration.identity,
                saved.registration.lease.expires_at,
            ),
            _self_authority("orders-a"),
        ).registrations[0]
        self.assertIs(retired.status, DiscoveryRegistrationStatus.DEREGISTERED)

    def test_workspace_authority_fails_before_writes(self) -> None:
        foreign = DiscoveryAuthority(
            "manager",
            "workspace-b",
            frozenset((DiscoveryScope.MANAGE,)),
        )
        with self.assertRaisesRegex(DiscoveryDenied, "workspace"):
            self.service().execute(
                RegisterDiscoveryInstance("register-a", _registration()),
                foreign,
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_discovery_commands"
            ).fetchone()[0],
            0,
        )

    def test_concurrent_registrations_have_one_winner(self) -> None:
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def attempt(command_id: str) -> None:
            barrier.wait(timeout=5)
            try:
                self.service().execute(
                    RegisterDiscoveryInstance(command_id, _registration()),
                    _manager(),
                )
                outcome = "registered"
            except DiscoveryConflict:
                outcome = "conflict"
            with lock:
                outcomes.append(outcome)

        threads = [
            threading.Thread(target=attempt, args=("register-a",)),
            threading.Thread(target=attempt, args=("register-b",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(sorted(outcomes), ["conflict", "registered"])
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_discovery_registrations WHERE status = 'active'"
            ).fetchone()[0],
            1,
        )

    def test_concurrent_heartbeats_have_one_winner(self) -> None:
        registered = self.service().execute(
            RegisterDiscoveryInstance("register-a", _registration()),
            _manager(),
        ).registrations[0]
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def attempt(command_id: str, seconds: int) -> None:
            barrier.wait(timeout=5)
            try:
                self.service(clock=lambda: NOW + timedelta(seconds=5)).execute(
                    HeartbeatDiscoveryInstance(
                        command_id,
                        registered.registration.identity,
                        registered.registration.lease.expires_at,
                        DiscoveryLease(
                            NOW + timedelta(seconds=10),
                            NOW + timedelta(seconds=seconds),
                        ),
                    ),
                    _manager(),
                )
                outcome = "heartbeat"
            except DiscoveryConflict:
                outcome = "conflict"
            with lock:
                outcomes.append(outcome)

        threads = [
            threading.Thread(target=attempt, args=("heartbeat-a", 60)),
            threading.Thread(target=attempt, args=("heartbeat-b", 90)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(outcomes), ["conflict", "heartbeat"])
        row = self.connection.execute(
            "SELECT revision, expires_at FROM cpk_discovery_registrations"
        ).fetchone()
        self.assertEqual(row[0], 2)
        self.assertIn(
            row[1],
            (NOW + timedelta(seconds=60), NOW + timedelta(seconds=90)),
        )

    def test_deregister_and_heartbeat_race_has_one_state_winner(self) -> None:
        registered = self.service().execute(
            RegisterDiscoveryInstance("register-a", _registration()),
            _manager(),
        ).registrations[0]
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def heartbeat() -> None:
            barrier.wait(timeout=5)
            try:
                self.service(clock=lambda: NOW + timedelta(seconds=5)).execute(
                    HeartbeatDiscoveryInstance(
                        "heartbeat-a",
                        registered.registration.identity,
                        registered.registration.lease.expires_at,
                        DiscoveryLease(
                            NOW + timedelta(seconds=10),
                            NOW + timedelta(seconds=60),
                        ),
                    ),
                    _manager(),
                )
                outcome = "heartbeat"
            except (DiscoveryConflict, DiscoveryMissing):
                outcome = "rejected"
            with lock:
                outcomes.append(outcome)

        def deregister() -> None:
            barrier.wait(timeout=5)
            try:
                self.service(clock=lambda: NOW + timedelta(seconds=5)).execute(
                    DeregisterDiscoveryInstance(
                        "deregister-a",
                        registered.registration.identity,
                        registered.registration.lease.expires_at,
                    ),
                    _manager(),
                )
                outcome = "deregistered"
            except (DiscoveryConflict, DiscoveryMissing):
                outcome = "rejected"
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=heartbeat), threading.Thread(target=deregister)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(outcomes.count("rejected"), 1)
        self.assertEqual(len(outcomes), 2)
        row = self.connection.execute(
            "SELECT status, revision, expires_at FROM cpk_discovery_registrations"
        ).fetchone()
        self.assertEqual(row[1], 2)
        if "heartbeat" in outcomes:
            self.assertEqual(row[0], "active")
            self.assertEqual(row[2], NOW + timedelta(seconds=60))
        else:
            self.assertEqual(row[0], "deregistered")
            self.assertEqual(row[2], registered.registration.lease.expires_at)

    def test_late_heartbeat_after_deregistration_fails_closed(self) -> None:
        registered = self.service().execute(
            RegisterDiscoveryInstance("register-a", _registration()),
            _manager(),
        ).registrations[0]
        self.service(clock=lambda: NOW + timedelta(seconds=5)).execute(
            DeregisterDiscoveryInstance(
                "deregister-a",
                registered.registration.identity,
                registered.registration.lease.expires_at,
            ),
            _manager(),
        )

        with self.assertRaises(DiscoveryMissing):
            self.service(clock=lambda: NOW + timedelta(seconds=6)).execute(
                HeartbeatDiscoveryInstance(
                    "heartbeat-late",
                    registered.registration.identity,
                    registered.registration.lease.expires_at,
                    DiscoveryLease(
                        NOW + timedelta(seconds=10),
                        NOW + timedelta(seconds=60),
                    ),
                ),
                _manager(),
            )

        row = self.connection.execute(
            "SELECT status, revision FROM cpk_discovery_registrations"
        ).fetchone()
        self.assertEqual(row, ("deregistered", 2))

    def test_expiry_and_heartbeat_race_cannot_both_mutate_the_lease(self) -> None:
        registered = self.service().execute(
            RegisterDiscoveryInstance("register-a", _registration()),
            _manager(),
        ).registrations[0]
        boundary = registered.registration.lease.expires_at
        barrier = threading.Barrier(2)
        outcomes: dict[str, object] = {}
        lock = threading.Lock()

        def heartbeat() -> None:
            barrier.wait(timeout=5)
            try:
                self.service(clock=lambda: NOW + timedelta(seconds=5)).execute(
                    HeartbeatDiscoveryInstance(
                        "heartbeat-race",
                        registered.registration.identity,
                        boundary,
                        DiscoveryLease(
                            NOW + timedelta(seconds=10),
                            NOW + timedelta(seconds=60),
                        ),
                    ),
                    _manager(),
                )
                outcome: object = "renewed"
            except (DiscoveryConflict, DiscoveryMissing):
                outcome = "expired-first"
            with lock:
                outcomes["heartbeat"] = outcome

        def expire() -> None:
            barrier.wait(timeout=5)
            result = self.service().execute(
                ExpireDiscoveryLeases(
                    "expire-race", "workspace-a", boundary, 10
                ),
                _manager(),
            )
            with lock:
                outcomes["expiry_count"] = result.affected_count

        threads = [threading.Thread(target=heartbeat), threading.Thread(target=expire)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        row = self.connection.execute(
            "SELECT status, revision, expires_at FROM cpk_discovery_registrations"
        ).fetchone()
        if outcomes["heartbeat"] == "renewed":
            self.assertEqual(outcomes["expiry_count"], 0)
            self.assertEqual(row, ("active", 2, NOW + timedelta(seconds=60)))
        else:
            self.assertEqual(outcomes["expiry_count"], 1)
            self.assertEqual(row, ("expired", 2, boundary))

    def test_resolution_limit_is_deterministic_and_workspace_scoped(self) -> None:
        service = self.service()
        for instance_id in ("orders-c", "orders-a", "orders-b"):
            service.execute(
                RegisterDiscoveryInstance(
                    f"register-{instance_id}",
                    _registration(instance_id=instance_id),
                ),
                _manager(),
            )

        result = service.execute(
            ResolveDiscoveryService(
                "resolve-two", "workspace-a", "orders", NOW, 2
            ),
            _reader(),
        )

        self.assertEqual(
            tuple(
                record.registration.identity.instance_id
                for record in result.registrations
            ),
            ("orders-a", "orders-b"),
        )

    def test_schema_reinstall_preserves_rows_and_named_constraints(self) -> None:
        self.service().execute(
            RegisterDiscoveryInstance("register-a", _registration()),
            _manager(),
        )
        install_discovery_schema(lambda: psycopg.connect(self.database_url))

        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_discovery_registrations"
            ).fetchone()[0],
            1,
        )
        constraints = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT conname FROM pg_constraint
                WHERE conrelid IN (
                  'cpk_discovery_registrations'::regclass,
                  'cpk_discovery_commands'::regclass
                )
                """
            ).fetchall()
        }
        self.assertTrue(
            {
                "cpk_discovery_status_check",
                "cpk_discovery_mode_check",
                "cpk_discovery_scope_check",
                "cpk_discovery_protocol_check",
                "cpk_discovery_lease_check",
                "cpk_discovery_revision_check",
                "cpk_discovery_command_variant_check",
            }.issubset(constraints)
        )

    def test_late_ledger_failure_rolls_back_projection_mutation(self) -> None:
        def factory():
            return _FailingLedgerUnitOfWork(self.factory())

        with self.assertRaisesRegex(RuntimeError, "ledger unavailable"):
            self.service(factory=factory).execute(
                RegisterDiscoveryInstance("register-a", _registration()),
                _manager(),
            )

        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_discovery_registrations"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_discovery_commands"
            ).fetchone()[0],
            0,
        )


def _registration(
    *,
    instance_id: str = "orders-a",
    mode: DiscoveryRegistrationMode = DiscoveryRegistrationMode.CONTROL_PLANE,
) -> DiscoveryRegistration:
    return DiscoveryRegistration(
        DiscoveryIdentity("workspace-a", "orders", instance_id),
        Endpoint(
            LiteralAddress(f"http://{instance_id}:8080"),
            Protocol.HTTP,
            EndpointScope.PRIVATE,
        ),
        mode,
        DiscoveryLease(NOW, NOW + timedelta(seconds=30)),
    )


def _manager() -> DiscoveryAuthority:
    return DiscoveryAuthority(
        "manager",
        "workspace-a",
        frozenset((DiscoveryScope.MANAGE, DiscoveryScope.RESOLVE)),
    )


def _reader() -> DiscoveryAuthority:
    return DiscoveryAuthority(
        "reader",
        "workspace-a",
        frozenset((DiscoveryScope.RESOLVE,)),
    )


def _self_authority(instance_id: str) -> DiscoveryAuthority:
    return DiscoveryAuthority(
        instance_id,
        "workspace-a",
        frozenset((DiscoveryScope.REGISTER_SELF,)),
        subject_service_id="orders",
        subject_instance_id=instance_id,
    )


class _FailingLedgerStore:
    def __init__(self, delegate) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def add_command(self, *args, **kwargs) -> None:
        raise RuntimeError("ledger unavailable after projection write")


class _FailingLedgerUnitOfWork:
    def __init__(self, delegate: PostgresDiscoveryUnitOfWork) -> None:
        self._delegate = delegate
        self._store = None

    @property
    def store(self):
        if self._store is None:
            raise RuntimeError("failing discovery unit of work is not active")
        return self._store

    def __enter__(self):
        self._delegate.__enter__()
        self._store = _FailingLedgerStore(self._delegate.store)
        return self

    def commit(self) -> None:
        self._delegate.commit()

    def __exit__(self, *args) -> None:
        self._store = None
        self._delegate.__exit__(*args)


if __name__ == "__main__":
    unittest.main()
