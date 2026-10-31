"""Original selection and fresh observation through actual Operations owners."""

import asyncio
from dataclasses import replace
import unittest
from unittest import mock
from uuid import uuid4

import control_plane_kit_operations as operations
from control_plane_kit_operations.effect_attempt_start import ExistingAttempt
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_reconciliation import ReconcileEffectAttempt
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.runtime_adapter_registration import RuntimeAdapterRegistration
from control_plane_kit_operations.runtime_effect_origins import RuntimeEffectOriginRecord, runtime_effect_origin_fingerprint
from tests.postgres_effect_attempt_reconciliation_fixture import UnitOfWorkLedger
from tests.test_managed_application_chain import now
from tests.test_postgres_runtime_reconciliation_invocations import _ControlledReconciliationObserver
from tests.test_postgres_workload_invocations import _WorkloadEligibilityFixture


class _OriginalRuntimeObservationFixture(_WorkloadEligibilityFixture):
    workload_preparation = False

    def setUp(self):
        super().setUp()
        self.ledger = UnitOfWorkLedger(self.unit_of_work)
        self.service = operations.ExecutionInvocationService(self.ledger, permission_policy=self.policy(), clock=now)
        self.observer = _ControlledReconciliationObserver(self)

    def start_current(self):
        from tests.test_postgres_workload_invocations import PostgresWorkloadVersionInvocationTests
        self.bound, self.start, self.started = PostgresWorkloadVersionInvocationTests.original_start(self)

    def historical_origin(self):
        attempt = self.started.attempt
        return RuntimeEffectOriginRecord(command=self.bound.receipt, workspace_id="workspace-a",
            identity=attempt.state.identity, original_event_id=attempt.original_start_event.event_id,
            request_fingerprint=attempt.state.request_fingerprint,
            runtime_registration_id=self.runtime.registration_id,
            recorded_at=attempt.original_start_event.occurred_at)

    def seed(self):
        # Explicit historical arrangement in this isolated test schema. Current
        # starts use invocation markers; this models an older origin-only attempt.
        self.start_current()
        origin = self.historical_origin()
        with self.unit_of_work() as uow:
            marker = uow.stores.execution.command_invocation_for_slot(self.bound.receipt,
                activity_id=self.activity_id, kind=operations.ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
            self.assertEqual(marker.disposition.value, "planned")
            self.assertIsNone(marker.decision)
            self.assertIsNone(marker.outcome)
            self.assertEqual(self.observer.requests, [])
            self.assertEqual(uow.stores.effect_attempts.get(origin.identity), self.started.attempt)
            self.assertEqual(uow.stores.connection.execute(
                "DELETE FROM cpk_execution_command_invocations WHERE invocation_id=%s RETURNING invocation_id",
                (marker.invocation_id,)).fetchall(), [(marker.invocation_id,)])
            uow.stores.runtime_effect_origins.insert_absent(origin)
            uow.commit()
        with self.unit_of_work() as uow:
            self.origin = uow.stores.runtime_effect_origins.get(origin.identity)
            self.assertEqual(self.origin, origin)
            self.assertEqual(uow.stores.execution.command_invocations(self.bound.receipt, limit=10).items, ())
            self.intent_record = uow.stores.effect_attempt_intents.get(self.started.attempt.state.identity)
        return self.origin

    def direct(self, *, qualify=True, service=None, observer=None, key="observe-original"):
        bound = self.binding(key=key, generation=self.runtime_fence.generation, scopes=self.original.granted_scopes)
        command = ReconcileEffectAttempt("request-a", self.started.attempt.state.identity,
            self.authority(scopes=self.original.granted_scopes), self.runtime_fence)
        owner = EffectAttemptReconciliationService(self.ledger,
            RuntimeAdapterRegistration(observer or self.observer, invocation_profile="cpk.semantic-invocations.v1",
                original_runtime_observation_invocations=qualify),
            EffectAttemptFoldService(self.ledger, id_factory=lambda: uuid4().hex),
            permission_policy=self.policy(), invocation_service=service or self.service)
        return owner, command, bound


class PostgresOriginalRuntimeObservationTests(_OriginalRuntimeObservationFixture, unittest.TestCase):
    def test_local_observation_has_fresh_committed_permission_and_guarded_uncertain_fold(self):
        origin = self.seed()
        owner, command, bound = self.direct()
        result = owner.execute(command, command_context=bound)
        self.assertEqual(result.attempt.state.status.value, "uncertain")
        self.assertEqual([row.kind.value for row in self.observer.committed], ["reconciliation-observation"])
        row, = self.observer.committed
        self.assertEqual(row.command, bound.receipt)
        self.assertNotEqual(row.command, origin.command)
        self.assertEqual(self.observer.requests[0][1], self.runtime)
        self.assertEqual(self.observer.requests[0][0].runtime_request.secret_resolution_grants, ())
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.runtime_effect_origins.get(origin.identity), origin)
        self.assertEqual(self.ledger.active, 0)

    def test_missing_new_observer_qualification_refuses_before_observer(self):
        self.seed()
        owner, command, bound = self.direct(qualify=False)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            owner.execute(command, command_context=bound)
        self.assertEqual(self.observer.requests, [])

    def test_revocation_before_observation_admission_stops_external_unit(self):
        self.seed()
        owner, command, bound = self.direct()
        self.observer.before_observation = lambda _: self.revoke()
        with self.assertRaises(operations.ExecutionInvocationDenied):
            owner.execute(command, command_context=bound)
        self.assertEqual(self.observer.committed, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes").fetchone()[0], 0)

    def test_missing_historical_origin_is_not_backfilled_or_observed(self):
        from control_plane_kit_operations.postgres import install_schema
        self.seed()
        self.connection.execute("DELETE FROM cpk_runtime_effect_origins")
        install_schema(self.connection)
        owner, command, bound = self.direct()
        with self.assertRaises(operations.ExecutionInvocationConflict):
            owner.execute(command, command_context=bound)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_runtime_effect_origins").fetchone()[0], 0)
        self.assertEqual(self.observer.requests, [])

    def test_origin_store_rejects_mismatched_witness_and_current_schema_refuses_it(self):
        from psycopg.types.json import Jsonb
        from control_plane_kit_operations.postgres import install_schema, SchemaInstallationError
        self.seed()
        descriptor = replace(self.origin, original_event_id="other-event").descriptor()
        self.connection.execute("UPDATE cpk_runtime_effect_origins SET descriptor=%s", (Jsonb(descriptor),))
        with self.unit_of_work() as uow, self.assertRaises(ValueError):
            uow.stores.runtime_effect_origins.get(self.origin.identity)
        with self.assertRaises(SchemaInstallationError):
            install_schema(self.connection)
        owner, command, bound = self.direct()
        with self.assertRaises(operations.ExecutionInvocationConflict):
            owner.execute(command, command_context=bound)
        self.assertEqual(self.observer.requests, [])
        self.assertEqual(self.connection.execute("SELECT descriptor FROM cpk_runtime_effect_origins").fetchone()[0], descriptor)

    def test_origin_store_rejects_mixed_invocation_and_origin_sources(self):
        self.start_current()
        with self.unit_of_work() as uow, self.assertRaisesRegex(ValueError, "mixed sources"):
            uow.stores.runtime_effect_origins.insert_absent(self.historical_origin())

    def test_local_marker_write_failure_rolls_back_real_start(self):
        from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
        original = PostgresExecutionStore.add_command_invocation
        before = self.attempt_snapshot()
        def write_then_fail(store, record):
            original(store, record)
            raise RuntimeError("marker write interrupted")
        with mock.patch.object(PostgresExecutionStore, "add_command_invocation", write_then_fail):
            with self.assertRaisesRegex(RuntimeError, "marker write interrupted"):
                self.start_current()
        self.assertEqual(self.attempt_snapshot(), before)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_runtime_effect_origins").fetchone()[0], 0)

    def test_lost_local_commit_ack_retains_marker_without_new_dispatch(self):
        from control_plane_kit_operations.postgres import PostgresUnitOfWork
        from tests.test_postgres_health_effect_start_rollback import _CommitThenRaiseConnection
        real_factory = self.unit_of_work
        connections = []
        def factory():
            def connect():
                connection = _CommitThenRaiseConnection(self.connect(), RuntimeError("marker ack lost"))
                connections.append(connection)
                return connection
            return PostgresUnitOfWork(connect)
        original_start = self.fresh_start
        with mock.patch.object(self, "fresh_start", lambda: original_start(factory)):
            with self.assertRaisesRegex(RuntimeError, "marker ack lost"):
                self.start_current()
        self.assertEqual([connection.commits for connection in connections], [1])
        with real_factory() as uow:
            identity = self.identity(activity_id=self.activity_id)
            self.assertIsNone(uow.stores.runtime_effect_origins.get(identity))
            retained = uow.stores.effect_attempt_intents.get(identity)
            marker = uow.stores.execution.original_final_invocation(identity,
                original_event_id=retained.original_start_event.event_id,
                request_fingerprint=retained.request_fingerprint,
                kind=operations.ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
            self.assertEqual(marker.disposition.value, "planned")
            self.assertIsNone(marker.decision)
            self.assertIsNone(marker.outcome)
        transition = self.transition(identity=identity, intent=retained.intent)
        start = self.start_command(intent=retained.intent, transition=transition, fence=self.runtime_fence)
        context = operations.EffectCommandContext(self.original, marker.command)
        replay = self.fresh_start().execute(start, command_context=context)
        self.assertIs(type(replay), ExistingAttempt)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_runtime_effect_origins").fetchone()[0], 0)

    def test_failed_commit_request_rolls_back_local_marker_and_start(self):
        before = self.attempt_snapshot()
        def factory():
            uow = self.unit_of_work()
            uow.commit = mock.Mock(side_effect=RuntimeError("commit request refused"))
            return uow
        original_start = self.fresh_start
        with mock.patch.object(self, "fresh_start", lambda: original_start(factory)):
            with self.assertRaisesRegex(RuntimeError, "commit request refused"):
                self.start_current()
        self.assertEqual(self.attempt_snapshot(), before)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_runtime_effect_origins").fetchone()[0], 0)

    def test_dynamic_origin_cannot_fall_through_legacy_observation(self):
        self.seed()
        _, command, _ = self.direct()
        owner = EffectAttemptReconciliationService(self.ledger, self.observer,
            EffectAttemptFoldService(self.ledger, id_factory=lambda: uuid4().hex))
        with self.assertRaises(operations.ExecutionInvocationConflict):
            owner.execute(command)
        self.assertEqual(self.observer.requests, [])

    def test_original_fingerprint_commits_command_attempt_and_event(self):
        origin = self.seed()
        before = runtime_effect_origin_fingerprint(origin, self.start.intent, self.runtime)
        for changed in (replace(origin, original_event_id="another-start"),
                replace(origin, identity=replace(origin.identity, attempt=2)),
                replace(origin, command=replace(origin.command, idempotency_key="another-command"))):
            with self.subTest(changed=changed):
                self.assertNotEqual(runtime_effect_origin_fingerprint(changed, self.start.intent, self.runtime), before)

    def test_terminal_replay_needs_no_new_observer_qualification_or_call(self):
        self.seed()
        owner, command, bound = self.direct()
        completed = owner.execute(command, command_context=bound)
        unqualified, command, later = self.direct(qualify=False, key="terminal-observation")
        replay = unqualified.execute(command, command_context=later)
        self.assertEqual(replay.attempt, completed.attempt)
        self.assertEqual(len(self.observer.requests), 1)

    def test_capture_dispatch_replacement_mismatch_refuses_before_adapter(self):
        from control_plane_kit_core.secrets import SecretReference
        from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
        from control_plane_kit_operations.runtime_authorities import RemoteDockerTlsAuthority
        from tests.test_postgres_runtime_invocation_carriage import _PreparedRuntimeCarriageFixture
        owner, command = self.composition()
        actual = EffectAttemptStartService.execute
        def replace_before_start(service, start, **options):
            _PreparedRuntimeCarriageFixture.change_runtime(self, RemoteDockerTlsAuthority(
                "tcp://replacement.example.test:2376", SecretReference("secret://runtime/ca"),
                SecretReference("secret://runtime/cert"), SecretReference("secret://runtime/key")))
            return actual(service, start, **options)
        with mock.patch.object(EffectAttemptStartService, "execute", replace_before_start):
            with self.assertRaises(operations.ExecutionInvocationConflict):
                asyncio.run(owner.execute_managed(command))
        self.assertEqual(self.adapter.requests, [])

    def test_original_registration_replacement_never_retargets_observation(self):
        from control_plane_kit_core.secrets import SecretReference
        from control_plane_kit_operations.runtime_authorities import RemoteDockerTlsAuthority
        from tests.test_postgres_runtime_invocation_carriage import _PreparedRuntimeCarriageFixture
        original = self.seed()
        _PreparedRuntimeCarriageFixture.change_runtime(self, RemoteDockerTlsAuthority(
            "tcp://replacement.example.test:2376", SecretReference("secret://runtime/ca"),
            SecretReference("secret://runtime/cert"), SecretReference("secret://runtime/key")))
        owner, command, bound = self.direct()
        with self.assertRaises(operations.ExecutionInvocationConflict):
            owner.execute(command, command_context=bound)
        self.assertEqual(self.observer.committed, [])
        self.assertTrue(all(authority.registration_id == original.runtime_registration_id
            for _, authority in self.observer.requests))

    def test_fresh_local_dispatch_requires_matching_loaded_runtime_context(self):
        owner, command = self.composition()
        load = owner._load_context
        def missing_authority(execution):
            return replace(load(execution), runtime_authorities=())
        with mock.patch.object(owner, "_load_context", missing_authority):
            with self.assertRaises(operations.ExecutionInvocationConflict):
                asyncio.run(owner.execute_managed(command))
        with self.unit_of_work() as uow:
            identity = self.identity(activity_id=self.activity_id)
            self.assertIsNone(uow.stores.runtime_effect_origins.get(identity))
            attempt = uow.stores.effect_attempts.get(identity)
            marker = uow.stores.execution.original_final_invocation(identity,
                original_event_id=attempt.original_start_event.event_id,
                request_fingerprint=attempt.state.request_fingerprint,
                kind=operations.ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        self.assertIsNone(marker.decision)
        self.assertIsNone(marker.outcome)
        self.assertEqual(self.adapter.requests, [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes").fetchone()[0], 0)

    def test_coordinator_routes_historical_local_origin_to_fresh_observation(self):
        from tests.test_postgres_runtime_reconciliation_invocations import _PreparedRuntimeReconciliationFixture
        self.seed()
        self.adapter = mock.Mock()
        owner, command = _PreparedRuntimeReconciliationFixture.composition_for_reconciliation(
            self, self.observer, self.service)
        owner._reconciliation_service._observer = replace(owner._reconciliation_service._observer,
            original_runtime_observation_invocations=True)
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual(result.status.value, "uncertain")
        self.assertEqual(len(self.observer.committed), 1)
        self.adapter.execute_with_authority.assert_not_called()

    def test_lost_observation_admission_ack_returns_no_permit(self):
        from control_plane_kit_operations.postgres import PostgresUnitOfWork
        from tests.test_postgres_health_effect_start_rollback import _CommitThenRaiseConnection
        self.seed()
        connections = []
        def connect():
            connection = _CommitThenRaiseConnection(self.connect(), RuntimeError("observation ack lost"))
            connections.append(connection)
            return connection
        service = operations.ExecutionInvocationService(lambda: PostgresUnitOfWork(connect),
            permission_policy=self.policy(), clock=now)
        _, command, bound = self.direct()
        port = service.bind_reconciliation(command_context=bound, command=command, attempt=self.started.attempt,
            intent_record=self.intent_record, runtime_authority=self.runtime)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            port.admit("reconciliation-observation")
        self.assertEqual(sum(connection.commits for connection in connections), 1)
        with self.unit_of_work() as uow:
            row = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=self.activity_id, kind=operations.ExecutionInvocationKind.RECONCILIATION_OBSERVATION)
        self.assertEqual((row.disposition.value, row.outcome.value), ("admitted", "pending"))
        self.assertEqual(self.observer.committed, [])

    def test_pending_observation_cannot_produce_a_fold(self):
        self.seed()
        self.observer.classify = False
        owner, command, bound = self.direct()
        with self.assertRaises(operations.ExecutionInvocationConflict):
            owner.execute(command, command_context=bound)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempt_outcomes").fetchone()[0], 0)


class PostgresOriginalObservationRoleTests(_OriginalRuntimeObservationFixture, unittest.TestCase):
    workload_checks = True

    def test_local_passive_observation_does_not_authorize_declared_probe_password(self):
        self.seed()
        check, = self.start.intent.products[0].product.runtime_contract.verification.checks
        self.assertIsNotNone(check.authentication)
        owner, command, bound = self.direct()
        result = owner.execute(command, command_context=bound)
        self.assertEqual(result.attempt.state.status.value, "uncertain")
        self.assertEqual(self.observer.requests[0][0].runtime_request.secret_resolution_grants, ())
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)

    def test_selected_observer_refuses_reconcile_checks_and_secret_installation(self):
        from control_plane_kit_core.planning import ReconcileNode
        from control_plane_kit_operations.runtime_effect_origins import original_runtime_observation_supported
        from tests.effect_attempt_intent_fixture import product_material
        self.seed()
        intent = self.start.intent
        self.assertFalse(original_runtime_observation_supported(
            replace(intent, operation=ReconcileNode(intent.operation.target)), self.runtime))
        self.assertFalse(original_runtime_observation_supported(replace(intent, products=(product_material(),)), self.runtime))


from tests.test_postgres_workload_invocations import _WorkloadInvocationFixture
from tests.test_postgres_runtime_invocation_carriage import _PreparedRuntimeCarriageFixture


class _RemoteOriginalWorkloadFixture(_WorkloadInvocationFixture, _PreparedRuntimeCarriageFixture):
    from control_plane_kit_core.secrets import SecretReference
    from control_plane_kit_operations.runtime_authorities import RemoteDockerTlsAuthority
    workload_preparation = False
    workload_checks = True
    workload_runtime_authority = RemoteDockerTlsAuthority("tcp://runtime.example.test:2376",
        SecretReference("secret://runtime/ca"), SecretReference("secret://runtime/cert"),
        SecretReference("secret://runtime/key"))
    direct = _OriginalRuntimeObservationFixture.direct

    def seed(self):
        from tests.test_postgres_workload_invocations import PostgresWorkloadVersionInvocationTests
        self.bound, self.start, self.started = PostgresWorkloadVersionInvocationTests.original_start(self)
        self.service = operations.ExecutionInvocationService(self.ledger,
            permission_policy=self.policy(), clock=now)
        self.observer = _ControlledReconciliationObserver(self)
        original = self.service.bind_runtime(command_context=self.bound, start=self.start, started=self.started)
        permit = original.admit("prepared-runtime-dispatch")
        original.record_outcome(permit, "unknown")
        with self.unit_of_work() as uow:
            self.intent_record = uow.stores.effect_attempt_intents.get(self.started.attempt.state.identity)
            self.marker = uow.stores.execution.original_final_invocation(self.started.attempt.state.identity,
                original_event_id=self.started.attempt.original_start_event.event_id,
                request_fingerprint=self.started.attempt.state.request_fingerprint,
                kind=operations.ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
            self.assertIsNone(uow.stores.runtime_effect_origins.get(self.started.attempt.state.identity))


class PostgresPreparedWorkloadObservationTests(_RemoteOriginalWorkloadFixture, unittest.TestCase):
    def test_remote_passive_observation_authorizes_only_actual_tls_not_probe_roles(self):
        self.seed()
        owner, command, bound = self.direct()
        result = owner.execute(command, command_context=bound)
        self.assertEqual(result.attempt.state.status.value, "uncertain")
        grants = self.observer.requests[0][0].runtime_request.secret_resolution_grants
        self.assertEqual({grant.intent.value for grant in grants}, {
            "docker.remote-tls.ca-certificate", "docker.remote-tls.client-certificate", "docker.remote-tls.client-key"})
        self.assertEqual([row.kind.value for row in self.observer.committed],
            ["secret-resolution"] * 3 + ["reconciliation-observation"])
        self.assertTrue(all(row.command == bound.receipt for row in self.observer.committed))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 3)

    def test_new_prepared_observation_target_commits_original_invocation_identity(self):
        from control_plane_kit_operations.runtime_observation_invocations import runtime_observation_profile
        self.seed()
        first = runtime_observation_profile(self.marker, self.start.intent, self.runtime)
        second = runtime_observation_profile(replace(self.marker, attempt=2), self.start.intent, self.runtime)
        self.assertTrue(first.requires_original_qualification)
        self.assertNotEqual(first.target_fingerprint, second.target_fingerprint)
        self.assertEqual(first.required_uses, second.required_uses)

    def test_observation_projection_excludes_pull_credentials_without_rewriting_original_profile(self):
        from control_plane_kit_operations.runtime_observation_invocations import runtime_observation_profile
        from control_plane_kit_operations.prepared_runtime_invocations import prepared_runtime_invocation_profile
        from tests.effect_attempt_intent_fixture import product_material
        self.seed()
        material = replace(self.start.intent.products[0], pull_authority=product_material().pull_authority)
        self.assertIsNotNone(material.pull_authority)
        intent = replace(self.start.intent, products=(material,))
        original = prepared_runtime_invocation_profile(intent,
            original_event_id=self.marker.original_event_id, runtime_authority=self.runtime)
        from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
        marker = replace(self.marker, request_fingerprint=runtime_effect_intent_fingerprint(intent),
            target_fingerprint=original.target_fingerprint)
        observation = runtime_observation_profile(marker, intent, self.runtime)
        self.assertEqual(len(original.required_uses), 4)
        self.assertEqual(len(observation.required_uses), 3)
        self.assertTrue(all(use.intent.value.startswith("docker.remote-tls.") for use in observation.required_uses))
