"""Actual observed managed replacement; no D1/publication or provider claim."""
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import replace
import os
import unittest
from unittest import mock

import psycopg
from psycopg.types.json import Jsonb
import rfc8785

from control_plane_kit_core.configuration_installation import (
    ConfigurationInstallationDisposition, ConfigurationInstallationReceipt,
)
from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCompletion, configuration_invocation_correlation_for_request,
    configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.operations import (
    ControlPlaneServiceRole, EffectAttemptIdentity, EffectAttemptStatus,
    EffectAttemptTransition, EffectAttemptTransitionKind,
)
from control_plane_kit_core.planning import ManagementBootstrapStage, ObserveManagementBootstrap, ReconcileNode
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservedSucceeded, RuntimeEffectObservedIndeterminate,
    RuntimeEffectObservationEvidence, RuntimeEffectObservationFailure,
    runtime_effect_intent_fingerprint, runtime_effect_request_for_intent,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint as F
from control_plane_kit_operations.coordinator import ExecuteActivityRun
from control_plane_kit_operations.effect_attempt_start import StartEffectAttempt, NewlyStarted
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict, ExistingFold, FoldEffectAttempt, GuardedObservedEffectFold,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_reconciliation import (
    ReconcileEffectAttempt, EffectAttemptReconciliationConflict,
)
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptEventEvidence, effect_attempt_state_fingerprint,
)
from control_plane_kit_operations.effect_outcome_evidence import (
    ObservedEffectOutcome, effect_outcome_failure, effect_outcome_transition,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_evidence import _composed_read
from control_plane_kit_operations.postgres.receiver_execution_scopes import _ExecutionScopeStorage
from control_plane_kit_operations.records import BoundedEvidence, OperationsRecordError
from control_plane_kit_operations.runtime_adapter_registration import RuntimeAdapterRegistration
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_reconciliation_fixture import RecordingObserver, FailIfObserver
from tests.test_postgres_secret_installation_admission import ProofReadObserver
from tests import test_managed_application_chain as chain
from tests import test_managed_configuration_update_execution as content
from tests.test_postgres_configuration_transfer_producer_receiver import ProfiledRecordingRuntime


KEY = "configuration_installation_receipt"


class ManagedObservedFixture(chain.ManagedApplicationFixture):
    """Reuse the admitted content path without collecting its TestCase methods."""
    application_artifacts = content.ManagedConfigurationUpdateExecutionTests.application_artifacts
    complete_initial = content.ManagedConfigurationUpdateExecutionTests.complete_initial
    desired_update = content.ManagedConfigurationUpdateExecutionTests.desired_update
    prepare_content = content.ManagedConfigurationUpdateExecutionTests.prepare_content

    def setUp(self):
        super().setUp()
        self.runtime = ProfiledRecordingRuntime(self)

    def application(self):
        return super().application(runtime_registration=RuntimeAdapterRegistration(
            self.runtime, exact_version_execute_with_authority=True))

    async def started(self):
        await self.prepare_content()
        coordinator = self.app.services[ControlPlaneServiceRole.EXECUTION]._service
        with self.unit_of_work() as uow:
            run = uow.stores.execution.get_run(self.update_run)
            request = uow.stores.execution.get_request(run.admission.request_id)
        self.authority = ExecutionWorkerAuthority(request.claim.worker_id, tuple(PolicyScope))
        self.fence = ExecutionLeaseFence(request.claim.worker_id, request.claim.generation)
        command = ExecuteActivityRun(self.update_run, self.authority, self.fence, IdempotencyKey("observe-start"))
        with _configuration_accounting(self.update_run, active=False):
            coordinator._configure_run(self.update_run)
            context = coordinator._load_context(command)
            selected = context.schedule.running or context.schedule.ready
            self.assertTrue(selected, "managed update has no selected activity")
            prerequisite = selected[0]
            self.assertIs(type(prerequisite.operation), ObserveManagementBootstrap)
            self.assertIs(prerequisite.operation.stage, ManagementBootstrapStage.GATEWAY_LOCAL_READY)
        effects = self.effects()
        progressed = await self.invoke("command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
            path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
            payload={"claim_generation": self.update_generation,
                "idempotency_key": "observe-prerequisite", "max_effects": 1})
        self.assertEqual((progressed["coordinator_status"], progressed["run_status"],
            progressed["effects_attempted"]), ("progressed", "running", 1))
        self.assertEqual(self.effects()[:4], effects[:4])
        reads = self.effects()[4]
        self.assertEqual(reads[:-1], effects[4])
        self.assertEqual(len(reads), len(effects[4]) + 1)
        observed_request, _, _ = reads[-1]
        self.assertEqual(observed_request.operation, prerequisite.operation)
        self.assertEqual(observed_request.source.run_id.value, self.update_run)
        before = self.effects()
        with _configuration_accounting(self.update_run, active=False):
            coordinator._configure_run(self.update_run)
            context = coordinator._load_context(command)
            selected = context.schedule.running or context.schedule.ready
            self.assertTrue(selected, "managed update has no selected replacement")
            self.assertIs(type(selected[0].operation), ReconcileNode)
            self.assertEqual(progressed["activity_id"], selected[0].activity_id.value)
            intent = context.configuration_intent
            self.assertIsNotNone(intent, "actual next managed activity was not proposed")
            self.assertIs(type(intent.operation), ReconcileNode)
            self.assertIs(intent.kind, RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1)
            self.assertEqual(intent.products[0].secret_selections, ())
            identity = EffectAttemptIdentity(intent.source.run_id, intent.activity_id.value, 1)
            transition = EffectAttemptTransition(EffectAttemptTransitionKind.STARTED, identity,
                request_fingerprint=runtime_effect_intent_fingerprint(intent))
            started = coordinator._start_service.execute(StartEffectAttempt(
                request.identity.request_id, transition, intent, self.authority, self.fence))
        self.assertIs(type(started), NewlyStarted)
        self.assertEqual(self.effects(), before, "setup dispatched the retained attempt")
        with self.unit_of_work() as uow:
            self.original = uow.stores.effect_attempt_intents.get(identity)
            self.attempt = uow.stores.effect_attempts.get(identity)
            self.runtime_authority = uow.stores.runtime_authorities.get_active_for_update(
                self.original.workspace_id, intent.authority_ref)
        self.assertEqual(self.original.intent, intent)
        self.assertEqual(self.attempt, started.attempt)
        self.request = runtime_effect_request_for_intent(intent, effect_id=self.original.original_start_event.event_id)
        self.reconcile = ReconcileEffectAttempt(request.identity.request_id, identity, self.authority, self.fence)

    def observation(self, case="replaced"):
        correlation = configuration_invocation_correlation_for_request(self.request)
        receipt = ConfigurationInstallationReceipt(correlation.request_fingerprint,
            configuration_invocation_selection_fingerprint(correlation.selection),
            ConfigurationInstallationDisposition.CREATED if case == "created" else
                ConfigurationInstallationDisposition.REUSED if case == "reused" else
                ConfigurationInstallationDisposition.REPLACED,
            None if case == "created" else "a" * 64,
            ("a" if case == "reused" else "b") * 64).descriptor()
        if case == "foreign":
            receipt["selection_fingerprint"] = "f" * 64
        if case == "foreign-request":
            receipt["request_fingerprint"] = "e" * 64
        if case == "malformed":
            receipt["profile"] = "wrong"
        evidence = {"provider_status": "reported-success"} if case == "missing" else {KEY: receipt}
        if case == "completion-only":
            evidence = {"configuration_invocation_completion": ConfigurationInvocationCompletion(
                correlation.request_fingerprint,
                configuration_invocation_selection_fingerprint(correlation.selection)).descriptor()}
        if case == "indeterminate":
            return RuntimeEffectObservedIndeterminate(self.request.effect_id, correlation.request_fingerprint,
                RuntimeEffectObservationEvidence({"provider_status": "unverified"}),
                RuntimeEffectObservationFailure("fixture.unverified", "Replacement is unverified."))
        return RuntimeEffectObservedSucceeded(self.request.effect_id, correlation.request_fingerprint,
            RuntimeEffectObservationEvidence(evidence))

    def guarded(self, observation):
        outcome = ObservedEffectOutcome(self.original.identity, observation)
        return GuardedObservedEffectFold(FoldEffectAttempt(self.original.request_id,
            effect_outcome_transition(outcome), self.authority, self.fence,
            effect_outcome_failure(outcome), outcome), self.original, self.runtime_authority)

    def publication_snapshot(self):
        return tuple((table, self.rows(table)) for table in (
            "cpk_workspaces", "cpk_configuration_invocation_completions", "cpk_configuration_acceptances",
            "cpk_configuration_accepted_slots", "cpk_configuration_claim_transfers"))

    @contextmanager
    def corrupt_observed_receipt(self, folded, observation):
        # Existing receipt-corruption recipe, retaining the actual observed
        # envelope. Restore every coherently changed row unconditionally.
        record = folded.outcome_record
        outcome = replace(record.outcome, observation=observation)
        state = replace(record.attempt.state, outcome_fingerprint=outcome.outcome_fingerprint)
        evidence = BoundedEvidence.from_mapping({"effect_attempt": EffectAttemptEventEvidence(
            state.identity.attempt, effect_attempt_state_fingerprint(state)).descriptor()})
        key = (state.identity.run_id.value, state.identity.activity_id, state.identity.attempt)
        event_id = record.attempt.latest_transition_event.event_id
        stored = self.connection.execute("SELECT preimage,outcome_fingerprint FROM cpk_effect_attempt_outcomes "
            "WHERE run_id=%s AND activity_id=%s AND attempt=%s", key).fetchone()
        payload = self.connection.execute("SELECT payload FROM cpk_activity_events WHERE event_id=%s", (event_id,)).fetchone()[0]
        try:
            with self.connection.transaction():
                self.connection.execute("UPDATE cpk_effect_attempt_outcomes SET preimage=%s,outcome_fingerprint=%s "
                    "WHERE run_id=%s AND activity_id=%s AND attempt=%s",
                    (rfc8785.dumps(observation.descriptor()), outcome.outcome_fingerprint, *key))
                self.connection.execute("UPDATE cpk_effect_attempts SET outcome_fingerprint=%s "
                    "WHERE run_id=%s AND activity_id=%s AND attempt=%s", (outcome.outcome_fingerprint, *key))
                self.connection.execute("UPDATE cpk_activity_events SET payload=%s WHERE event_id=%s",
                    (Jsonb({**payload, "evidence": evidence.descriptor()}), event_id))
            yield
        finally:
            with self.connection.transaction():
                self.connection.execute("UPDATE cpk_effect_attempt_outcomes SET preimage=%s,outcome_fingerprint=%s "
                    "WHERE run_id=%s AND activity_id=%s AND attempt=%s", (*stored, *key))
                self.connection.execute("UPDATE cpk_effect_attempts SET outcome_fingerprint=%s "
                    "WHERE run_id=%s AND activity_id=%s AND attempt=%s", (stored[1], *key))
                self.connection.execute("UPDATE cpk_activity_events SET payload=%s WHERE event_id=%s", (Jsonb(payload), event_id))


class ManagedObservedInstallationTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def started(self):
        fixture = ManagedObservedFixture()
        with ExitStack() as cleanups:
            fixture.addCleanup = cleanups.callback
            fixture.setUp()
            await fixture.started()
            yield fixture

    async def test_secret_free_managed_reconciliation_requires_replaced_observation(self):
        for case in ("replaced", "missing", "created", "reused", "foreign"):
            with self.subTest(case=case):
                async with self.started() as fixture:
                    observation = fixture.observation(case)
                    observer = RecordingObserver(observation, ledger=fixture.tracker)
                    ids = Sequence("managed-observed-fold")
                    service = EffectAttemptReconciliationService(fixture.unit_of_work, observer,
                        EffectAttemptFoldService(fixture.unit_of_work, id_factory=ids))
                    before, effects = fixture.publication_snapshot(), fixture.effects()
                    with mock.patch.object(service._secret_use_authorizer, "authorize_resolution",
                            side_effect=AssertionError("secret-free observer requested operational authorization")), \
                            mock.patch.object(service._secret_use_authorizer, "authorize_version_resolution",
                                side_effect=AssertionError("secret-free observer requested consumer authorization")):
                        folded = service.execute(fixture.reconcile)
                    actual = folded.outcome_record.outcome.observation
                    if case == "replaced":
                        self.assertEqual(actual, observation)
                        self.assertIs(folded.attempt.state.status, EffectAttemptStatus.SUCCEEDED)
                    else:
                        self.assertIs(type(actual), RuntimeEffectObservedIndeterminate)
                        self.assertIs(folded.attempt.state.status, EffectAttemptStatus.UNCERTAIN)
                        self.assertEqual(actual.effect_id, observation.effect_id)
                        self.assertEqual(actual.request_fingerprint, observation.request_fingerprint)
                        self.assertNotIn(KEY, actual.evidence.descriptor())
                        self.assertEqual(actual.observations, ())
                    self.assertEqual(len(observer.calls), 1)
                    self.assertEqual(fixture.tracker.active, 0)
                    self.assertEqual(fixture.publication_snapshot(), before)
                    self.assertEqual(fixture.effects(), effects)

    async def test_independent_guarded_fold_refuses_unproved_managed_success_before_ids(self):
        async with self.started() as fixture:
            before, publication = fixture.durable_snapshot(), fixture.publication_snapshot()
            ids = Sequence("must-not-be-used")
            service = EffectAttemptFoldService(fixture.unit_of_work, id_factory=ids)
            for case in ("missing", "completion-only", "created", "reused", "foreign", "foreign-request", "malformed"):
                with self.subTest(case=case), self.assertRaises(EffectAttemptFoldConflict):
                    service.execute_observed(fixture.guarded(fixture.observation(case)))
                self.assertEqual(ids.calls, [])
                self.assertEqual(fixture.durable_snapshot(), before)
                self.assertEqual(fixture.publication_snapshot(), publication)
            contradictory = replace(fixture.observation("indeterminate"), evidence=fixture.observation().evidence)
            with self.assertRaises(EffectAttemptFoldConflict):
                service.execute_observed(fixture.guarded(contradictory))
            self.assertEqual(ids.calls, [])
            self.assertEqual(fixture.durable_snapshot(), before)
            folded = EffectAttemptFoldService(fixture.unit_of_work,
                id_factory=Sequence("managed-observed-uncertain")).execute_observed(
                    fixture.guarded(fixture.observation("indeterminate")))
            self.assertIs(folded.attempt.state.status, EffectAttemptStatus.UNCERTAIN)
            self.assertEqual(fixture.publication_snapshot(), publication)

    async def test_retained_observation_revalidates_original_plan_with_bounded_reads_and_replay(self):
        async with self.started() as fixture:
            publication = fixture.publication_snapshot()
            folded = EffectAttemptFoldService(fixture.unit_of_work,
                id_factory=Sequence("managed-observed-success")).execute_observed(fixture.guarded(fixture.observation()))
            identity, event = fixture.original.identity, folded.attempt.latest_transition_event.event_id
            before = fixture.durable_snapshot(), fixture.effects()
            replay_ids = Sequence("must-not-be-used")
            replay_service = EffectAttemptReconciliationService(fixture.unit_of_work, FailIfObserver(),
                EffectAttemptFoldService(fixture.unit_of_work, id_factory=replay_ids))
            with mock.patch.object(replay_service._secret_use_authorizer, "authorize_resolution",
                    side_effect=AssertionError("terminal replay requested operational authorization")), \
                    mock.patch.object(replay_service._secret_use_authorizer, "authorize_version_resolution",
                        side_effect=AssertionError("terminal replay requested consumer authorization")):
                replay = replay_service.execute(fixture.reconcile)
            self.assertIs(type(replay), ExistingFold)
            self.assertEqual(replay.outcome_record, folded.outcome_record)
            self.assertEqual(replay_ids.calls, [])
            self.assertEqual((fixture.durable_snapshot(), fixture.effects()), before)

            # No caller-provided accounting scope: standalone get must establish
            # bounded plan transport itself, not use get_plan's legacy branch.
            observer = ProofReadObserver(psycopg.connect(os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]))
            standalone_queries, execute = [], observer.execute
            def record_query(query, params=()):
                standalone_queries.append(str(query))
                return execute(query, params)
            # Keep typed wire observation disabled for legacy non-plan reads.
            # This local spy delegates unchanged and records query text only.
            with PostgresUnitOfWork(lambda: observer) as uow, mock.patch.object(observer, "execute", record_query):
                self.assertEqual(uow.stores.effect_outcomes.get(identity, event), folded.outcome_record)
                plans = [sql for sql in standalone_queries if "FROM cpk_activity_plans" in sql]
                self.assertEqual(len(plans), 2)
                self.assertTrue(plans[0].startswith("SELECT octet_length("))
                self.assertTrue(plans[1].startswith("SELECT CASE WHEN"))

            # Existing passive observer captures wire queries/widths. A cold
            # standalone candidate must establish the bounded original plan.
            observer = ProofReadObserver(psycopg.connect(os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]))
            with PostgresUnitOfWork(lambda: observer) as uow, _composed_read(uow.stores.connection) as read:
                probe_prefix = []
                def record_prefix(_observer, query, row):
                    if "FROM cpk_activity_plans" in str(query) and str(query).startswith("SELECT octet_length("):
                        probe_prefix.append(read.used.records)
                observer.after_row, observer.enabled = record_prefix, True
                self.assertEqual(uow.stores.effect_outcomes.get(identity, event), folded.outcome_record)
                plans = [sql for sql in observer.queries if "FROM cpk_activity_plans" in sql]
                self.assertEqual(len(plans), 2, "candidate plan needs one bounded probe/fetch")
                self.assertTrue(plans[0].startswith("SELECT octet_length("))
                self.assertTrue(plans[1].startswith("SELECT CASE WHEN"))
                self.assertEqual(len(probe_prefix), 1)
                self.assertLessEqual(observer.largest_cell, 1024 * 1024)

            # Spend only the initial ledger allowance: enough for the same cold
            # prefix and plan probe, insufficient for its guarded plan fetch.
            observer = ProofReadObserver(psycopg.connect(os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]))
            with PostgresUnitOfWork(lambda: observer) as uow, _composed_read(uow.stores.connection) as read:
                self.assertEqual(read.used, F(0, 0, 0, 0))
                read.used = read.used.plus(F(8192 - probe_prefix[0], 0, 0, 0))
                observer.enabled = True
                with self.assertRaises(OperationsRecordError):
                    uow.stores.effect_outcomes.get(identity, event)
                plans = [sql for sql in observer.queries if "FROM cpk_activity_plans" in sql]
                self.assertEqual(len(plans), 1)
                self.assertTrue(plans[0].startswith("SELECT octet_length("))
                self.assertGreaterEqual(read.used.records, 8192)

            # The receiver history already owns its original plan. Its outcome
            # checks must not fetch a second copy or reset the shared ledger.
            observer = ProofReadObserver(psycopg.connect(os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]))
            with PostgresUnitOfWork(lambda: observer) as uow, _composed_read(uow.stores.connection) as read:
                history = _ExecutionScopeStorage(uow.stores.connection, read)
                request = history.request(fixture.original.workspace_id, fixture.original.request_id)
                original, _ = history.verify(request.identity)
                run = uow.stores.execution.get_run(identity.run_id.value)
                events = history.events(run.run_id)
                observer.enabled = True
                _, _, outcomes = history.effects(request, original, run, events)
                self.assertIn(folded.outcome_record, outcomes)
                self.assertFalse(any("FROM cpk_activity_plans" in sql for sql in observer.queries))
                for changes in ({"plan_id": "foreign-plan"}, {"base_graph_id": "foreign-base"},
                        {"desired_graph_id": "foreign-desired"}):
                    with self.subTest(supplied_plan=tuple(changes)), self.assertRaises(ValueError):
                        history.effects(request, (replace(original[0], **changes), *original[1:]), run, events)
                self.assertFalse(any("FROM cpk_activity_plans" in sql for sql in observer.queries))

            for case in ("missing", "created", "reused", "foreign"):
                with self.subTest(retained=case), fixture.corrupt_observed_receipt(folded, fixture.observation(case)):
                    corrupted = fixture.durable_snapshot(), fixture.effects()
                    with fixture.unit_of_work() as uow, self.assertRaises(OperationsRecordError):
                        uow.stores.effect_outcomes.get(identity, event)
                    with mock.patch.object(replay_service._secret_use_authorizer, "authorize_resolution",
                            side_effect=AssertionError("corrupt replay requested operational authorization")), \
                            mock.patch.object(replay_service._secret_use_authorizer, "authorize_version_resolution",
                                side_effect=AssertionError("corrupt replay requested consumer authorization")), \
                            self.assertRaises(EffectAttemptReconciliationConflict):
                        replay_service.execute(fixture.reconcile)
                    self.assertEqual(replay_ids.calls, [])
                    self.assertEqual((fixture.durable_snapshot(), fixture.effects()), corrupted)
            self.assertEqual((fixture.durable_snapshot(), fixture.effects()), before)
            self.assertEqual(fixture.publication_snapshot(), publication)
