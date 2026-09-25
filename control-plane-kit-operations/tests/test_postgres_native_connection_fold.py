"""Native acceptance laws at the existing transaction and persistence owners.

Predecessor events are fixture setup, not evidence of deployment creation.
Raw observations are recording-port values, not live provider evidence.
"""
from dataclasses import replace
import unittest
from unittest import mock

import rfc8785

from control_plane_kit_core import RuntimeEffectResult
from control_plane_kit_core.operations import ActivityEventKind, ActivityRunStatus
from control_plane_kit_core.planning import (
    ManagementBootstrapStage, ObserveManagementBootstrap, compile_graph_activity_plan,
    derive_schedule, project_activity_journal,
)
from control_plane_kit_core.planning.saga import SagaStepId
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_operations import effect_attempt_fold as commands
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict, EffectAttemptFoldDenied, ExistingFold, FoldEffectAttempt, NewlyFolded,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_outcome_evidence import (
    ExecutionEffectOutcome, NativeConnectionObservation, NativeConnectionOutcome,
    effect_outcome_failure, effect_outcome_transition,
)
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.effect_outcome_store import EffectAttemptOutcomeStore
from control_plane_kit_operations.postgres.runtime_authority_store import RuntimeAuthorityStore
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.execution_lease_recovery_fixture import Sequence
from tests.health_effect_start_fixture import trusted_health_context
from tests.postgres_health_effect_start_fixture import PostgresHealthEffectStartFixture


class PostgresNativeConnectionFoldTests(PostgresHealthEffectStartFixture, unittest.TestCase):
    def health_context(self, **options):
        _, _, current, desired, _ = super().health_context(
            stage=ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)
        graph = desired.graph
        desired = validate_graph(replace(graph, runtimes={**graph.runtimes,
            "docker": replace(graph.runtimes["docker"],
                authority_ref=RuntimeAuthorityReference("native-docker"))}))
        desired.require_valid()
        plan = compile_graph_activity_plan(current, desired)
        selected, = (item for item in plan.activities
            if type(item.operation) is ObserveManagementBootstrap
            and item.operation.stage is ManagementBootstrapStage.CONNECTOR_CONNECTED)
        return plan, selected, current, desired, None

    def health_start_value(self, **options):
        command = super().health_start_value(**options)
        graph = DEFAULT_GRAPH_CODEC.decode(self.projections["health-desired"].graph_descriptor)
        intent = replace(command.intent, authority_ref=graph.runtimes["docker"].authority_ref)
        return replace(command, intent=intent,
            transition=replace(command.transition, request_fingerprint=runtime_effect_intent_fingerprint(intent)))

    def setUp(self):
        super().setUp()
        with self.unit_of_work() as uow:
            self.runtime_authority = uow.stores.runtime_authorities.register(
                workspace_id="workspace-a", authority_ref=self.start_value.intent.authority_ref,
                runtime_kind=self.start_value.intent.runtime_kind, authority=LocalDockerSocketAuthority(),
                admitted_by="operator-a", admitted_at="2026-08-01T11:00:00Z")
            uow.commit()
        with self.observed_time("2030-01-01T00:00:00Z"):
            self.started = EffectAttemptStartService(self.unit_of_work,
                id_factory=Sequence("native-original")).execute(self.start_value).attempt
        with self.unit_of_work() as uow:
            self.intent_record = uow.stores.effect_attempt_intents.get(self.started.state.identity)

    def raw(self, *, kind=NativeConnectionOutcome.CONNECTED, end="2030-01-01T00:00:01Z"):
        if kind is NativeConnectionOutcome.UNKNOWN:
            return NativeConnectionObservation(kind, "native-original", self.health_activity.activity_id.value)
        return NativeConnectionObservation(kind, "native-original", self.health_activity.activity_id.value,
            "c" * 64, "2030-01-01T00:00:00Z", end,
            2**64 - 1 if kind is NativeConnectionOutcome.CONNECTED else 0,
            "11111111-1111-4111-8111-111111111111")

    def command(self, raw=None, **changes):
        command_type = getattr(commands, "FoldNativeConnectionObservation", None)
        self.assertIsNotNone(command_type, "raw native observation acceptance command is missing")
        return command_type(**(dict(request_id="request-a", identity=self.started.state.identity,
            observation=self.raw() if raw is None else raw, context=trusted_health_context(),
            authority=self.start_value.authority, fence=self.start_value.fence,
            intent_record=self.intent_record, runtime_authority=self.runtime_authority) | changes))

    def service(self, ids=None):
        service = EffectAttemptFoldService(self.unit_of_work,
            id_factory=Sequence("native-completed") if ids is None else ids)
        self.assertTrue(callable(getattr(service, "execute_native", None)),
            "database-time native fold entrance is missing")
        return service

    def native_snapshot(self):
        return (self.health_snapshot(), tuple(self.connection.execute(
            "SELECT * FROM cpk_effect_attempt_outcomes ORDER BY 1, 2, 3").fetchall()))

    def test_database_acceptance_preserves_sample_and_replay_never_reclassifies(self):
        command = self.command(self.raw(end="2030-01-01T00:00:00.999999999Z"))
        service = self.service()
        with self.observed_time("2030-01-01T00:00:11Z"):
            result = service.execute_native(command)
        self.assertIs(type(result), NewlyFolded)
        self.assertEqual(result.attempt.state.status.value, "not_ready")
        outcome = result.outcome_record.outcome
        self.assertEqual(outcome.acceptance_reason, "stale-at-acceptance")
        self.assertEqual(outcome.observation, command.observation)
        self.assertEqual(outcome.accepted_at, result.attempt.latest_transition_event.occurred_at)
        self.assertEqual(outcome.accepted_at, "2030-01-01T00:00:11Z")
        self.assertIsNone(result.attempt.latest_transition_event.failure)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.effect_outcomes.get(self.started.state.identity,
                result.attempt.latest_transition_event.event_id), result.outcome_record)
        before, ids = self.native_snapshot(), Sequence("must-not-allocate")
        with self.forbid_fresh_health(), mock.patch.object(RuntimeAuthorityStore,
                "get_active_for_update", side_effect=AssertionError("replay reloaded current runtime authority")):
            replay = self.service(ids).execute_native(command)
            self.assertEqual(replay, ExistingFold(result.attempt, result.outcome_record))
            with self.assertRaises(EffectAttemptFoldConflict):
                self.service(ids).execute_native(replace(command, observation=self.raw()))
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.native_snapshot(), before)

    def test_acceptance_clock_is_sampled_after_current_runtime_authority_lock(self):
        command, service = self.command(), self.service()
        calls = []
        original = RuntimeAuthorityStore.get_active_for_update
        def authority(store, *args, **kwargs):
            value = original(store, *args, **kwargs)
            calls.append("authority")
            return value
        with self.observed_time("2030-01-01T00:00:11Z"):
            observe = PostgresExecutionStore.observe_request_lease_for_update
            def clock(store, request_id):
                self.assertIn("authority", calls,
                    "native freshness was sampled before current authority serialization")
                calls.append("clock")
                return observe(store, request_id)
            with mock.patch.object(RuntimeAuthorityStore, "get_active_for_update", authority), \
                    mock.patch.object(PostgresExecutionStore, "observe_request_lease_for_update", clock):
                result = service.execute_native(command)
        self.assertEqual(result.attempt.state.status.value, "succeeded")
        self.assertEqual(calls, ["authority", "clock"])
        self.assertEqual(result.outcome_record.outcome.observation.ready_connections, 2**64 - 1)

    def test_completed_unknown_waits_without_failure_compensation_or_automatic_next_read(self):
        command, service = self.command(self.raw(kind=NativeConnectionOutcome.UNKNOWN)), self.service()
        with self.unit_of_work() as uow:
            before = project_activity_journal(self.health_plan, activity_journal_events(
                uow.stores.execution.events_for_run("run-a")))
        with self.observed_time("2030-01-01T00:00:02Z"):
            result = service.execute_native(command)
        with self.unit_of_work() as uow:
            run = uow.stores.execution.get_run("run-a")
            events = uow.stores.execution.events_for_run("run-a")
        self.assertIs(run.status, ActivityRunStatus.RUNNING)
        self.assertIs(events[-1].kind, ActivityEventKind.STEP_OBSERVATION_NOT_READY)
        self.assertEqual(result.attempt.state.status.value, "not_ready")
        journal = project_activity_journal(self.health_plan, activity_journal_events(events))
        schedule = derive_schedule(self.health_plan, journal.state)
        native_id = self.health_activity.activity_id
        self.assertEqual(journal.state.step(SagaStepId(native_id.value)).status.value, "waiting")
        self.assertEqual(journal.state.completion_order, before.state.completion_order)
        self.assertFalse(schedule.successful)
        self.assertNotIn(native_id, tuple(item.activity_id for item in (*schedule.ready, *schedule.running)))
        # PATH has the same creation dependencies as CONNECTED; it is lawful
        # independent progress. Gateway ingress readiness waits on PATH, while
        # workload obligations also retain their native connection dependency.
        path, = (item for item in self.health_plan.activities
            if type(item.operation) is ObserveManagementBootstrap
            and item.operation.stage is ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)
        ingress, = (item for item in self.health_plan.activities
            if type(item.operation) is ObserveManagementBootstrap
            and item.operation.stage is ManagementBootstrapStage.GATEWAY_INGRESS_READY)
        self.assertIn(path.activity_id, tuple(item.activity_id for item in schedule.ready))
        self.assertNotIn(ingress.activity_id, tuple(item.activity_id for item in schedule.ready))
        native_dependents = tuple(item for item in self.health_plan.activities
            if any(dependency.predecessor == native_id for dependency in item.dependencies))
        self.assertTrue(native_dependents, "fixture must retain actual native-dependent obligations")
        for dependent in native_dependents:
            self.assertNotIn(dependent.activity_id, tuple(item.activity_id for item in schedule.ready))
        self.assertEqual(self.health_counts(), (1, 1, 0, 0))
        self.assertFalse(any(event.kind in (ActivityEventKind.RUN_FAILED,
            ActivityEventKind.RUN_COMPENSATION_STARTED) for event in events))

    def test_invalid_correlation_context_and_expired_acceptance_leave_no_outcome(self):
        command, service = self.command(), self.service()
        with self.observed_time("2030-01-01T00:00:02Z"):
            before = self.native_snapshot()
            for changes in (
                {"observation": replace(command.observation, effect_id="foreign-start")},
                {"context": trusted_health_context(workspace="foreign-workspace")},
                {"context": trusted_health_context(scopes=())},
                {"fence": replace(command.fence, generation=command.fence.generation + 1)},
                {"runtime_authority": replace(command.runtime_authority, registration_id="foreign")},
            ):
                with self.subTest(changes=changes), self.assertRaises((InvalidOperationCommand,
                        EffectAttemptFoldDenied, EffectAttemptFoldConflict)):
                    service.execute_native(replace(command, **changes))
                self.assertEqual(self.native_snapshot(), before)
        with self.observed_time("2030-01-01T00:10:01Z"):
            before = self.native_snapshot()
            with self.assertRaises(EffectAttemptFoldDenied):
                service.execute_native(command)
            self.assertEqual(self.native_snapshot(), before)

    def test_generic_mutation_success_cannot_complete_the_native_connection_operation(self):
        outcome = ExecutionEffectOutcome(self.started.state.identity,
            self.started.state.request_fingerprint, RuntimeEffectResult.succeeded("native-original"))
        command = FoldEffectAttempt("request-a", effect_outcome_transition(outcome),
            self.start_value.authority, self.start_value.fence, effect_outcome_failure(outcome), outcome)
        with self.observed_time("2030-01-01T00:00:02Z"):
            before = self.native_snapshot()
            with self.assertRaises((EffectAttemptFoldDenied, EffectAttemptFoldConflict)):
                EffectAttemptFoldService(self.unit_of_work,
                    id_factory=Sequence("must-not-persist")).execute(command)
            self.assertEqual(self.native_snapshot(), before)

    def test_revoked_current_runtime_denies_first_acceptance_without_writing(self):
        command, service = self.command(), self.service()
        with self.unit_of_work() as uow:
            uow.stores.runtime_authorities.revoke("workspace-a", self.runtime_authority.authority_ref)
            uow.commit()
        with self.observed_time("2030-01-01T00:00:02Z"):
            before = self.native_snapshot()
            with self.assertRaises(EffectAttemptFoldDenied):
                service.execute_native(command)
            self.assertEqual(self.native_snapshot(), before)

    def test_native_fold_cannot_borrow_a_real_signed_stage_intent(self):
        command, service = self.command(), self.service()
        path, = (item for item in self.health_plan.activities
            if type(item.operation) is ObserveManagementBootstrap
            and item.operation.stage is ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)
        start = self.health_start_value(activity=path)
        with self.observed_time("2030-01-01T00:00:02Z"):
            # Independent PATH is legitimately ready; create its real signed
            # owner record as the negative, rather than forging an intent tree.
            signed = EffectAttemptStartService(self.unit_of_work,
                id_factory=Sequence("path-original", "path-request", "path-transit", "path-workload"),
                health_receiver_decoders=self.health_receiver_decoders()).execute_health(
                    self.health_command(start=start))
            with self.unit_of_work() as uow:
                foreign = uow.stores.effect_attempt_intents.get(signed.start.attempt.state.identity)
            before = self.native_snapshot()
            with self.assertRaises((InvalidOperationCommand, EffectAttemptFoldConflict, EffectAttemptFoldDenied)):
                service.execute_native(replace(command, intent_record=foreign))
            with self.assertRaises((InvalidOperationCommand, EffectAttemptFoldConflict, EffectAttemptFoldDenied)):
                service.execute_native(replace(command, identity=foreign.identity, intent_record=foreign,
                    observation=replace(command.observation, effect_id=foreign.original_start_event.event_id,
                        activity_id=foreign.identity.activity_id)))
            self.assertEqual(self.native_snapshot(), before)

    def test_outcome_write_failure_rolls_back_event_attempt_and_acceptance(self):
        command, service = self.command(), self.service()
        with self.observed_time("2030-01-01T00:00:02Z"):
            before = self.native_snapshot()
            with mock.patch.object(EffectAttemptOutcomeStore, "insert", side_effect=RuntimeError("injected write failure")):
                with self.assertRaisesRegex(RuntimeError, "injected write failure"):
                    service.execute_native(command)
            self.assertEqual(self.native_snapshot(), before)

    def test_retained_acceptance_codec_rejects_tampered_reason_time_and_decimal_count(self):
        command, service = self.command(), self.service()
        with self.observed_time("2030-01-01T00:00:02Z"):
            result = service.execute_native(command)
        retained = result.outcome_record.outcome.acceptance_descriptor()
        identity = self.started.state.identity
        coordinates = (identity.run_id.value, identity.activity_id, identity.attempt)
        for changed in (
            retained | {"acceptance_reason": "stale-at-acceptance"},
            retained | {"accepted_at": "2030-01-01T00:00:03Z"},
            retained | {"observation": retained["observation"] | {
                "ready_connections": "0" + retained["observation"]["ready_connections"]}},
            retained | {"unexpected": "field"},
        ):
            with self.subTest(changed=changed):
                self.connection.execute("UPDATE cpk_effect_attempt_outcomes SET preimage=%s "
                    "WHERE run_id=%s AND activity_id=%s AND attempt=%s",
                    (rfc8785.dumps(changed), *coordinates))
                with self.unit_of_work() as uow, self.assertRaises(OperationsRecordError):
                    uow.stores.effect_outcomes.get(identity, result.attempt.latest_transition_event.event_id)
        self.connection.execute("UPDATE cpk_effect_attempt_outcomes SET preimage=%s "
            "WHERE run_id=%s AND activity_id=%s AND attempt=%s", (rfc8785.dumps(retained), *coordinates))
        self.connection.execute("UPDATE cpk_activity_events SET occurred_at='2030-01-01T00:00:03Z' "
            "WHERE event_id=%s", (result.attempt.latest_transition_event.event_id,))
        with self.unit_of_work() as uow, self.assertRaises(OperationsRecordError):
            uow.stores.effect_outcomes.get(identity, result.attempt.latest_transition_event.event_id)
