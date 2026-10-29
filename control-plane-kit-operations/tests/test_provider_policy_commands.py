"""#2060 durable command, authority, transaction and readback laws."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib.resources import files
from hashlib import sha256
import os
import threading
import unittest
from uuid import uuid4

import psycopg

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    PrincipalIdentity,
    PrincipalKind,
)
from control_plane_kit_core.provider_policy import (
    ProviderPolicyCommandResult,
    ProviderPolicyCoordinate,
    ProviderPolicyErrorCode,
    ProviderPolicyFailure,
    ProviderPolicyOperation,
    ProviderPolicyOutcome,
)
from control_plane_kit_operations.postgres import (
    PostgresUnitOfWork,
    ProviderPolicyCommandUpgradeError,
    install_schema,
    upgrade_provider_policy_commands,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.lifecycle_lock_fixture import LifecycleLockFixture


NOW = "2026-10-28T18:00:00Z"


class ProviderPolicyCommandTests(LifecycleLockFixture, unittest.TestCase):
    def setUp(self):
        self.database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not self.database_url:
            raise RuntimeError("Run ./control-plane-kit-operations/test.sh for Docker PostgreSQL")
        self.schema = "provider_policy_" + uuid4().hex
        self.connection = psycopg.connect(self.database_url, autocommit=True)
        self.addCleanup(self.connection.close)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.addCleanup(self.connection.execute, f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.execute(f'SET search_path TO "{self.schema}"')
        install_schema(self.connection)
        self.identity = PrincipalIdentity("issuer-a", "operator-a", PrincipalKind.OPERATOR)
        self.other = PrincipalIdentity("issuer-a", "operator-b", PrincipalKind.OPERATOR)
        self.local = threading.local()
        self.mutation_calls = []
        self.readback_calls = []
        self.acquisitions = []
        self.behavior = self.success
        self.readback_behavior = lambda request: self.success(self.mutation_calls[0])

    def public(self, name):
        value = getattr(operations, name, None)
        self.assertIsNotNone(value, "provider-policy Operations owner is unavailable: " + name)
        return value

    def connect(self):
        return psycopg.connect(self.database_url, options=f"-c search_path={self.schema}")

    @contextmanager
    def unit_of_work(self):
        self.local.active = getattr(self.local, "active", 0) + 1
        try:
            with PostgresUnitOfWork(self.connect) as uow:
                yield uow
        finally:
            self.local.active -= 1

    def binding(self, binding_id="binding-a", provider_id="Secrets Provider East"):
        return self.public("ProviderPolicyBinding")(
            binding_id=binding_id,
            logical_provider_id="provider-a",
            provider_id=provider_id,
        )

    def policy(self, binding=None, identity=None):
        admission = self.public("ProviderPolicyAdmission")(
            identity=identity or self.identity,
            binding=binding or self.binding(),
        )
        return self.public("ProviderPolicyAdministrationPolicy")(
            installation_id="installation-a",
            admissions=(admission,),
        )

    def context(self, policy=None, principal=None):
        return (policy or self.policy()).authorize(
            AuthenticatedPrincipal(principal or self.identity), "provider-a"
        )

    def coordinate(self):
        return ProviderPolicyCoordinate(
            "workload-a", "workspace-a", "secret.metadata", None
        )

    def success(self, command):
        return ProviderPolicyCommandResult(
            provider_id=command.provider_id,
            correlation_id=command.provider_correlation,
            operation=command.operation,
            coordinate=command.coordinate,
            expected_revision=command.expected_revision,
            outcome=(ProviderPolicyOutcome.GRANTED if command.operation is ProviderPolicyOperation.GRANT
                     else ProviderPolicyOutcome.REVOKED),
            code=("provider-policy-granted" if command.operation is ProviderPolicyOperation.GRANT
                  else "provider-policy-revoked"),
            grant_id=("provider-grant-a" if command.operation is ProviderPolicyOperation.GRANT
                      else command.grant_id),
            resulting_revision=command.expected_revision + 1,
            provider_actor="policy-admin",
            occurred_at=NOW,
        )

    def port_factory(self, binding):
        self.assertEqual(getattr(self.local, "active", 0), 0, "provider acquired inside UoW")
        self.acquisitions.append(binding)
        fixture = self

        class Port:
            def grant(self, command):
                fixture.assertEqual(getattr(fixture.local, "active", 0), 0)
                fixture.assertEqual(
                    fixture.read(key=command.caller_idempotency_key).state.value,
                    "dispatch-claimed",
                )
                fixture.mutation_calls.append(command)
                return fixture.behavior(command)

            def revoke(self, command):
                return self.grant(command)

            def command_result(self, request):
                fixture.assertEqual(getattr(fixture.local, "active", 0), 0)
                fixture.readback_calls.append(request)
                return fixture.readback_behavior(request)

        return Port()

    def service(self, policy=None):
        return self.public("ProviderPolicyCommandService")(
            self.unit_of_work,
            administration_policy=policy or self.policy(),
            port_factory=self.port_factory,
            clock=lambda: NOW,
        )

    def grant(self, context=None, key="grant-a", coordinate=None):
        return self.public("GrantProviderPolicy")(
            context=context or self.context(),
            coordinate=coordinate or self.coordinate(),
            expected_revision=3,
            idempotency_key=IdempotencyKey(key),
        )

    def read(self, command_id=None, key="grant-a"):
        with self.unit_of_work() as uow:
            store = uow.stores.provider_policy_commands
            if command_id is not None:
                return store.get(command_id)
            return store.get_for_key(
                "installation-a", self.identity, "provider-a", key
            )

    def test_current_dedicated_denial_precedes_provider_acquisition_and_history(self):
        policy = self.policy(identity=self.other)
        with self.assertRaises(self.public("ProviderPolicyAdministrationDenied")):
            policy.authorize(AuthenticatedPrincipal(self.identity), "provider-a")
        self.assertEqual(self.acquisitions, [])
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_provider_policy_commands"
        ).fetchone()[0], 0)

    def test_success_commits_claim_before_one_provider_mutation_and_records_result(self):
        result = self.service().grant(self.grant())
        self.assertEqual(result.state.value, "resolved")
        self.assertEqual(len(self.mutation_calls), 1)
        stored = self.read(result.command_id)
        self.assertEqual(stored.state.value, "resolved")
        self.assertEqual(stored.provider_correlation, self.mutation_calls[0].provider_correlation)
        self.assertEqual(stored.observations[0].canonical_result, self.success(self.mutation_calls[0]))

    def test_changed_reuse_conflicts_and_identical_replay_never_redispatches(self):
        original = self.service().grant(self.grant())
        replay = self.service().grant(self.grant())
        self.assertEqual(replay, original)
        with self.assertRaises(self.public("ProviderPolicyCommandConflict")):
            self.service().grant(self.grant(coordinate=replace(self.coordinate(), subject="other")))
        self.assertEqual(len(self.mutation_calls), 1)

    def test_lost_response_reconciles_by_read_only_with_mutation_count_one(self):
        self.behavior = lambda command: (_ for _ in ()).throw(ProviderPolicyFailure(
            ProviderPolicyErrorCode.TRANSPORT,
            "provider response unavailable",
            effect_may_have_occurred=True,
        ))
        unresolved = self.service().grant(self.grant())
        self.assertEqual(unresolved.state.value, "dispatched-unresolved")
        self.assertEqual(len(self.mutation_calls), 1)
        reconciled = self.service().reconcile(self.public("ReconcileProviderPolicy")(
            context=self.context(), command_id=unresolved.command_id,
        ))
        self.assertEqual(reconciled.state.value, "resolved")
        self.assertEqual(len(self.mutation_calls), 1)
        self.assertEqual(len(self.readback_calls), 1)

    def test_binding_replacement_cannot_redirect_historical_readback(self):
        self.behavior = lambda command: (_ for _ in ()).throw(ProviderPolicyFailure(
            ProviderPolicyErrorCode.TRANSPORT,
            "provider response unavailable",
            effect_may_have_occurred=True,
        ))
        unresolved = self.service().grant(self.grant())
        replacement = self.policy(self.binding("binding-b", "Secrets Provider West"))
        with self.assertRaises(self.public("ProviderPolicyAdministrationDenied")):
            self.service(replacement).reconcile(self.public("ReconcileProviderPolicy")(
                context=self.context(replacement), command_id=unresolved.command_id,
            ))
        self.assertEqual(self.readback_calls, [])
        self.assertEqual(len(self.mutation_calls), 1)

    def test_concurrent_duplicate_has_one_irreversible_dispatch_claim(self):
        entered, release = threading.Event(), threading.Event()

        def paused(command):
            entered.set()
            if not release.wait(timeout=15):
                raise AssertionError("provider was not released")
            return self.success(command)

        self.behavior = paused
        with ThreadPoolExecutor(max_workers=2) as pool:
            initial = pool.submit(self.service().grant, self.grant())
            try:
                self.assertTrue(entered.wait(timeout=10))
                duplicate = pool.submit(self.service().grant, self.grant()).result(timeout=10)
                self.assertEqual(duplicate.state.value, "dispatch-claimed")
                self.assertEqual(len(self.mutation_calls), 1)
            finally:
                release.set()
            self.assertEqual(initial.result(timeout=15).state.value, "resolved")
        self.assertEqual(len(self.mutation_calls), 1)

    def test_missing_or_mismatched_readback_preserves_uncertainty(self):
        self.behavior = lambda command: (_ for _ in ()).throw(ProviderPolicyFailure(
            ProviderPolicyErrorCode.TRANSPORT,
            "provider response unavailable",
            effect_may_have_occurred=True,
        ))
        unresolved = self.service().grant(self.grant())
        self.readback_behavior = lambda request: (_ for _ in ()).throw(
            ProviderPolicyFailure(ProviderPolicyErrorCode.MISSING, "command missing")
        )
        missing = self.service().reconcile(self.public("ReconcileProviderPolicy")(
            self.context(), unresolved.command_id
        ))
        self.assertEqual(missing.state.value, "dispatched-unresolved")
        self.readback_behavior = lambda request: replace(
            self.success(self.mutation_calls[0]),
            coordinate=replace(self.coordinate(), subject="different"),
        )
        mismatched = self.service().reconcile(self.public("ReconcileProviderPolicy")(
            self.context(), unresolved.command_id
        ))
        self.assertEqual(mismatched.state.value, "evidence-mismatch-unresolved")
        self.assertEqual(len(self.mutation_calls), 1)

    def test_observation_commit_failure_leaves_claim_discoverable_without_retry(self):
        calls = 0

        @contextmanager
        def failing_factory():
            nonlocal calls
            calls += 1
            with self.unit_of_work() as uow:
                if calls == 3:
                    class Failing:
                        stores = uow.stores

                        @staticmethod
                        def commit():
                            raise RuntimeError("observation commit unavailable")

                    yield Failing()
                else:
                    yield uow

        service = self.public("ProviderPolicyCommandService")(
            failing_factory,
            administration_policy=self.policy(),
            port_factory=self.port_factory,
            clock=lambda: NOW,
        )
        with self.assertRaises(self.public("ProviderPolicyCommandUnavailable")):
            service.grant(self.grant())
        stored = self.read()
        self.assertEqual(stored.state.value, "dispatch-claimed")
        self.assertEqual(stored.observations, ())
        self.assertEqual(len(self.mutation_calls), 1)
        recovered = self.service().reconcile(self.public("ReconcileProviderPolicy")(
            self.context(), stored.command_id
        ))
        self.assertEqual(recovered.state.value, "resolved")
        self.assertEqual(len(self.mutation_calls), 1)

    def test_definite_refusal_is_terminal_and_never_reconciled(self):
        self.behavior = lambda command: (_ for _ in ()).throw(
            ProviderPolicyFailure(ProviderPolicyErrorCode.DENIED, "provider denied command")
        )
        refused = self.service().grant(self.grant())
        self.assertEqual(refused.state.value, "refused")
        replay = self.service().reconcile(self.public("ReconcileProviderPolicy")(
            self.context(), refused.command_id
        ))
        self.assertEqual(replay.state.value, "refused")
        self.assertEqual(self.readback_calls, [])
        self.assertEqual(len(self.mutation_calls), 1)

    def test_revoke_preserves_exact_grant_identity_and_current_read_is_authorized(self):
        request = self.public("RevokeProviderPolicy")(
            context=self.context(),
            coordinate=self.coordinate(),
            expected_revision=4,
            idempotency_key=IdempotencyKey("revoke-a"),
            grant_id="provider-grant-a",
        )
        result = self.service().revoke(request)
        self.assertEqual(result.state.value, "resolved")
        self.assertEqual(self.mutation_calls[0].grant_id, "provider-grant-a")
        replacement = self.policy(self.binding("binding-b", "Secrets Provider West"))
        with self.assertRaises(self.public("ProviderPolicyAdministrationDenied")):
            self.service(replacement).read(
                self.context(replacement), result.command_id
            )

    def test_explicit_additive_upgrade_accepts_only_exact_prior_and_replays_current(self):
        prior_schema = "provider_policy_prior_" + uuid4().hex
        self.connection.execute(f'CREATE SCHEMA "{prior_schema}"')
        self.addCleanup(self.connection.execute, f'DROP SCHEMA "{prior_schema}" CASCADE')
        self.connection.execute(f'SET search_path TO "{prior_schema}"')
        package = files("control_plane_kit_operations.postgres")
        current_sql = package.joinpath("current_schema.sql").read_text(encoding="utf-8")
        additive_sql = package.joinpath(
            "provider_policy_command_schema.sql"
        ).read_text(encoding="utf-8")
        self.assertTrue(current_sql.endswith(additive_sql))
        self.connection.execute(current_sql[: -len(additive_sql)])
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id,name,lifecycle) "
            "VALUES ('retained-workspace','Retained','created')"
        )
        policy = self.policy()
        context = self.context(policy)
        upgraded = upgrade_provider_policy_commands(
            self.connection, context, administration_policy=policy
        )
        self.assertFalse(upgraded.replayed)
        self.assertFalse(upgraded.provisional)
        replay = upgrade_provider_policy_commands(
            self.connection, context, administration_policy=policy
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_provider_policy_commands"
        ).fetchone()[0], 0)
        self.assertEqual(self.connection.execute(
            "SELECT name FROM cpk_workspaces WHERE workspace_id='retained-workspace'"
        ).fetchone(), ("Retained",))

    def observation(self, record, evidence, source):
        payload = (
            repr(evidence.descriptor())
            if type(evidence) is ProviderPolicyFailure
            else repr(evidence)
        )
        fingerprint = sha256(payload.encode("utf-8")).hexdigest()
        observation_id = sha256((
            record.command_id + "\x1f" + source.value + "\x1f" + fingerprint
        ).encode()).hexdigest()
        return self.public("ProviderPolicyOutcomeObservation")(
            observation_id=observation_id,
            source=source,
            observed_at=NOW,
            evidence_fingerprint=fingerprint,
            canonical_result=None if type(evidence) is ProviderPolicyFailure else evidence,
            failure=evidence if type(evidence) is ProviderPolicyFailure else None,
            matches_intent=(
                False if type(evidence) is ProviderPolicyFailure
                else evidence.matches(record.command)
            ),
        )

    def append(self, record, observation):
        with self.unit_of_work() as uow:
            result = uow.stores.provider_policy_commands.append_observation(
                record.command_id, observation
            )
            uow.commit()
            return result

    def test_concurrent_contradictory_canonical_results_are_unresolved(self):
        self.behavior = lambda command: (_ for _ in ()).throw(ProviderPolicyFailure(
            ProviderPolicyErrorCode.TRANSPORT, "response unavailable",
            effect_may_have_occurred=True,
        ))
        unresolved = self.service().grant(self.grant())
        barrier = threading.Barrier(2)
        counter = iter(("provider-grant-a", "provider-grant-b"))
        lock = threading.Lock()

        def contradictory(request):
            barrier.wait(timeout=10)
            with lock:
                grant_id = next(counter)
            return replace(self.success(self.mutation_calls[0]), grant_id=grant_id)

        self.readback_behavior = contradictory
        request = self.public("ReconcileProviderPolicy")(self.context(), unresolved.command_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(self.service().reconcile, (request, request)))
        self.assertEqual(len(self.mutation_calls), 1)
        self.assertEqual(max(value.observation_count for value in results), 3)
        self.assertEqual(self.read(unresolved.command_id).state.value,
                         "evidence-mismatch-unresolved")

    def test_late_initial_response_conflicting_with_readback_is_unresolved(self):
        entered, release = threading.Event(), threading.Event()

        def paused(command):
            entered.set()
            if not release.wait(timeout=15):
                raise AssertionError("provider was not released")
            return self.success(command)

        self.behavior = paused
        with ThreadPoolExecutor(max_workers=1) as pool:
            initial = pool.submit(self.service().grant, self.grant())
            try:
                self.assertTrue(entered.wait(timeout=10))
                claimed = self.read()
                self.readback_behavior = lambda request: replace(
                    self.success(claimed.command), grant_id="readback-grant"
                )
                readback = self.service().reconcile(
                    self.public("ReconcileProviderPolicy")(
                        self.context(), claimed.command_id
                    )
                )
                self.assertEqual(readback.state.value, "resolved")
            finally:
                release.set()
            initial.result(timeout=15)
        self.assertEqual(len(self.mutation_calls), 1)
        self.assertEqual(
            self.read(claimed.command_id).state.value,
            "evidence-mismatch-unresolved",
        )

    def test_retained_match_and_fingerprint_claims_are_recomputed(self):
        resolved = self.service().grant(self.grant())
        table = "cpk_provider_policy_outcome_observations"
        self.connection.execute(
            f"UPDATE {table} SET descriptor=jsonb_set(descriptor,'{{matches_intent}}','false')"
        )
        with self.assertRaises(self.public("ProviderPolicyCommandError")):
            self.read(resolved.command_id)
        self.connection.execute(
            f"UPDATE {table} SET descriptor=jsonb_set(descriptor,'{{matches_intent}}','true'), "
            "evidence_fingerprint=%s", ("f" * 64,)
        )
        with self.assertRaises(self.public("ProviderPolicyCommandError")):
            self.read(resolved.command_id)

    def test_wire_conflict_remains_unresolved_until_exact_readback(self):
        self.behavior = lambda command: (_ for _ in ()).throw(
            ProviderPolicyFailure(ProviderPolicyErrorCode.CONFLICT, "wire conflict")
        )
        unresolved = self.service().grant(self.grant())
        self.assertEqual(unresolved.state.value, "dispatched-unresolved")
        recovered = self.service().reconcile(self.public("ReconcileProviderPolicy")(
            self.context(), unresolved.command_id
        ))
        self.assertEqual(recovered.state.value, "resolved")
        self.assertEqual(len(self.mutation_calls), 1)

    def test_full_principal_identity_has_unambiguous_command_identity(self):
        first = PrincipalIdentity("a\x1fb", "c", PrincipalKind.OPERATOR)
        second = PrincipalIdentity("a", "b\x1fc", PrincipalKind.OPERATOR)
        admissions = tuple(
            self.public("ProviderPolicyAdmission")(identity, self.binding())
            for identity in (first, second)
        )
        policy = self.public("ProviderPolicyAdministrationPolicy")(
            "installation-a", admissions
        )
        calls = []

        class Port:
            def grant(inner, command):
                calls.append(command)
                return self.success(command)
            revoke = grant

        service = self.public("ProviderPolicyCommandService")(
            self.unit_of_work, administration_policy=policy,
            port_factory=lambda binding: Port(), clock=lambda: NOW,
        )
        results = []
        for identity in (first, second):
            context = policy.authorize(AuthenticatedPrincipal(identity), "provider-a")
            results.append(service.grant(self.grant(context=context, key="same-key")))
        self.assertNotEqual(results[0].command_id, results[1].command_id)
        self.assertEqual(len(calls), 2)

    def test_port_acquisition_failure_is_bounded_redacted_and_not_redispatched(self):
        def unavailable(binding):
            raise RuntimeError("credential-canary")

        service = self.public("ProviderPolicyCommandService")(
            self.unit_of_work, administration_policy=self.policy(),
            port_factory=unavailable, clock=lambda: NOW,
        )
        result = service.grant(self.grant())
        self.assertEqual(result.state.value, "dispatched-unresolved")
        self.assertNotIn("credential-canary", repr(result.observations))
        replay = service.grant(self.grant())
        self.assertEqual(replay.command_id, result.command_id)
        self.assertEqual(replay.observation_count, 1)
        stored = self.read(result.command_id)
        self.readback_behavior = lambda request: self.success(stored.command)
        recovered = self.service().reconcile(
            self.public("ReconcileProviderPolicy")(
                self.context(), result.command_id
            )
        )
        self.assertEqual(recovered.state.value, "resolved")
        self.assertEqual(self.mutation_calls, [])

    def test_observation_projection_is_bounded_without_hiding_conflict(self):
        self.behavior = lambda command: (_ for _ in ()).throw(ProviderPolicyFailure(
            ProviderPolicyErrorCode.TRANSPORT, "initial unavailable",
            effect_may_have_occurred=True,
        ))
        record = self.service().grant(self.grant())
        source = self.public("ProviderPolicyObservationSource").COMMAND_RESULT_READ
        for index in range(105):
            failure = ProviderPolicyFailure(
                ProviderPolicyErrorCode.UNAVAILABLE, f"read unavailable {index}"
            )
            record = self.append(record, self.observation(record, failure, source))
        contradictory = replace(
            self.success(self.mutation_calls[0]),
            coordinate=ProviderPolicyCoordinate(
                "other-subject", "workspace-a", "read", "operator"
            ),
        )
        record = self.append(record, self.observation(record, contradictory, source))
        self.assertEqual(record.observation_count, 107)
        self.assertEqual(len(record.observations), 100)
        self.assertEqual(record.state.value, "evidence-mismatch-unresolved")

    def prior_sql(self):
        package = files("control_plane_kit_operations.postgres")
        current = package.joinpath("current_schema.sql").read_text(encoding="utf-8")
        additive = package.joinpath(
            "provider_policy_command_schema.sql"
        ).read_text(encoding="utf-8")
        self.assertTrue(current.endswith(additive))
        return current[: -len(additive)]

    def test_upgrade_refuses_drift_without_partial_schema(self):
        schema = "provider_policy_drift_" + uuid4().hex
        self.connection.execute(f'CREATE SCHEMA "{schema}"')
        self.addCleanup(self.connection.execute, f'DROP SCHEMA "{schema}" CASCADE')
        self.connection.execute(f'SET search_path TO "{schema}"')
        self.connection.execute(self.prior_sql())
        self.connection.execute("ALTER TABLE cpk_workspaces ADD COLUMN stray text")
        with self.assertRaises(ProviderPolicyCommandUpgradeError):
            upgrade_provider_policy_commands(
                self.connection, self.context(), administration_policy=self.policy()
            )
        self.assertIsNone(self.connection.execute(
            "SELECT to_regclass('cpk_provider_policy_commands')"
        ).fetchone()[0])

    def test_upgrade_denial_precedes_schema_inspection_or_mutation(self):
        policy = self.policy(identity=self.other)
        before = self.connection.execute(
            "SELECT count(*) FROM pg_class WHERE relnamespace=current_schema()::regnamespace"
        ).fetchone()[0]
        with self.assertRaises(ProviderPolicyCommandUpgradeError):
            upgrade_provider_policy_commands(
                self.connection,
                self.context(),
                administration_policy=policy,
            )
        after = self.connection.execute(
            "SELECT count(*) FROM pg_class WHERE relnamespace=current_schema()::regnamespace"
        ).fetchone()[0]
        self.assertEqual(after, before)

    def test_upgrade_is_provisional_inside_caller_transaction_and_rolls_back(self):
        schema = "provider_policy_outer_" + uuid4().hex
        self.connection.execute(f'CREATE SCHEMA "{schema}"')
        self.addCleanup(self.connection.execute, f'DROP SCHEMA "{schema}" CASCADE')
        self.connection.execute(f'SET search_path TO "{schema}"')
        self.connection.execute(self.prior_sql())
        self.connection.autocommit = False
        try:
            self.connection.execute("SELECT 1")
            result = upgrade_provider_policy_commands(
                self.connection, self.context(), administration_policy=self.policy()
            )
            self.assertTrue(result.provisional)
            self.connection.rollback()
        finally:
            if self.connection.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                self.connection.rollback()
            self.connection.autocommit = True
        self.assertIsNone(self.connection.execute(
            "SELECT to_regclass('cpk_provider_policy_commands')"
        ).fetchone()[0])

    def test_concurrent_upgrade_has_one_transition_and_one_exact_replay(self):
        schema = "provider_policy_concurrent_" + uuid4().hex
        self.connection.execute(f'CREATE SCHEMA "{schema}"')
        self.addCleanup(self.connection.execute, f'DROP SCHEMA "{schema}" CASCADE')
        self.connection.execute(f'SET search_path TO "{schema}"')
        self.connection.execute(self.prior_sql())
        policy = self.policy()
        context = self.context(policy)
        barrier = threading.Barrier(2)

        def run():
            connection = psycopg.connect(self.database_url, autocommit=True)
            try:
                connection.execute(f'SET search_path TO "{schema}"')
                barrier.wait(timeout=10)
                return upgrade_provider_policy_commands(
                    connection, context, administration_policy=policy
                )
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(lambda _: run(), range(2)))
        self.assertEqual(sorted(value.replayed for value in results), [False, True])


if __name__ == "__main__":
    unittest.main()
