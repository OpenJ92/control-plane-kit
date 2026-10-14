"""Original selected material at durable outcome boundaries, using real starts."""
from contextlib import contextmanager
from dataclasses import replace
import unittest

import psycopg
from psycopg.types.json import Jsonb
import rfc8785

from control_plane_kit_core.operations import ActivityEventKind, EffectAttemptStatus, fold_effect_attempt
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationEvidence, RuntimeEffectObservationFailure,
    RuntimeEffectObservedFailed, RuntimeEffectObservedIndeterminate,
    RuntimeEffectObservedSucceeded, runtime_effect_intent_fingerprint,
)
from control_plane_kit_core.runtime_effects import (
    EffectResultKind, RuntimeEffectFailure, RuntimeEffectResult,
)
from control_plane_kit_core.secret_installation import (
    secret_installation_receipt_for_observation, secret_installation_receipt_for_result,
)
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict, ExistingFold, FoldEffectAttempt,
    GuardedObservedEffectFold, NewlyFolded,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_reconciliation import EffectAttemptReconciliationConflict
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptEventEvidence, EffectAttemptRecord, effect_attempt_state_fingerprint,
)
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord, ExecutionEffectOutcome, ObservedEffectOutcome,
    effect_outcome_failure, effect_outcome_transition,
)
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_evidence import _composed_read
from control_plane_kit_operations.records import ActivityEventRecord, BoundedEvidence, OperationsRecordError
from control_plane_kit_operations.receiver_execution_scopes import (
    ExecutionReceiverScope, classify_receiver_scope_evidence,
)
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_reconciliation_fixture import (
    FailIfObserver, PostgresEffectAttemptReconciliationFixture, RecordingObserver, UnitOfWorkLedger,
)
from tests import test_postgres_effect_attempt_reconciliation_authority_grants as selected_start_owner
from tests.test_secret_installation_admission import receipt_result
from tests.test_secret_version_dispatch import registration, selected_context
from tests.test_receiver_execution_scope_transport import ValueObserver
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds


class ProofReadObserver(ValueObserver):
    """Observe actual SQL and rows without changing their contents."""
    def __init__(self, connection, after_row=None):
        super().__init__(connection, after_row)
        self.queries = []

    def execute(self, query, params=()):
        if self.enabled:
            self.queries.append(str(query))
        return super().execute(query, params)


class PostgresSecretInstallationAdmissionTests(PostgresEffectAttemptReconciliationFixture, unittest.TestCase):
    # Reuse the accepted actual node admission/lifecycle setup without inheriting
    # its TestCase or collecting its tests a second time.
    _seed_version_node_reconciliation = (
        selected_start_owner.PostgresEffectAttemptReconciliationAuthorityGrantTests._seed_version_node_reconciliation)
    _admit_version_reconciliation_material = (
        selected_start_owner.PostgresEffectAttemptReconciliationAuthorityGrantTests._admit_version_reconciliation_material)

    def selected_start(self):
        current, intent, fence = self._seed_version_node_reconciliation()
        request = self.runtime_request(current, intent)
        control = receipt_result(request)
        self.assertIsNotNone(secret_installation_receipt_for_result(request, control))
        return current, intent, fence, request, control

    def selected_direct_outcome(self, current, intent, result):
        return ExecutionEffectOutcome(current.state.identity, runtime_effect_intent_fingerprint(intent), result)

    def observed(self, current, intent, evidence):
        return RuntimeEffectObservedSucceeded(current.original_start_event.event_id,
            runtime_effect_intent_fingerprint(intent), RuntimeEffectObservationEvidence(evidence))

    def fold_command_for(self, current, intent, fence, outcome):
        command = FoldEffectAttempt(intent.source.request_id, effect_outcome_transition(outcome),
            self.authority(scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE)),
            fence, effect_outcome_failure(outcome), outcome)
        if type(outcome) is ObservedEffectOutcome:
            return GuardedObservedEffectFold(command, self.intent_record(current, intent=intent), None)
        return command

    def fold_selected(self, command, *ids):
        sequence = Sequence(*ids)
        service = EffectAttemptFoldService(self.unit_of_work, id_factory=sequence)
        result = service.execute_observed(command) if type(command) is GuardedObservedEffectFold else service.execute(command)
        return result, sequence

    def assert_fold_refused_without_changes(self, command):
        before = self.complete_reconciliation_snapshot()
        sequence = Sequence("must-not-be-used")
        service = EffectAttemptFoldService(self.unit_of_work, id_factory=sequence)
        with self.assertRaises(EffectAttemptFoldConflict) as caught:
            if type(command) is GuardedObservedEffectFold:
                service.execute_observed(command)
            else:
                service.execute(command)
        self.assertEqual(str(caught.exception), "effect attempt fold truth is invalid")
        self.assertEqual(sequence.calls, [])
        self.assertEqual(self.complete_reconciliation_snapshot(), before)

    def test_direct_fold_requires_receipt_before_durable_changes(self):
        for case in ("complete", "missing", "foreign"):
            with self.subTest(case=case):
                current, intent, fence, request, control = self.selected_start()
                result = control
                if case == "missing":
                    result = RuntimeEffectResult.succeeded(request.effect_id)
                elif case == "foreign":
                    result = replace(control, evidence={**control.evidence,
                        "secret_installation_receipt": {**control.evidence["secret_installation_receipt"],
                            "selection_fingerprint": "f" * 64}})
                command = self.fold_command_for(current, intent, fence, self.selected_direct_outcome(current, intent, result))
                if case != "complete":
                    self.assert_fold_refused_without_changes(command)
                    continue
                folded, _ = self.fold_selected(command, "receipt-direct-terminal")
                self.assertIs(type(folded), NewlyFolded)
                self.assertIs(folded.attempt.state.status, EffectAttemptStatus.SUCCEEDED)
                self.assertEqual(folded.outcome_record.outcome.result, control)

    def test_guarded_observed_fold_enforces_original_receipt_independently(self):
        for case in ("complete", "missing", "foreign"):
            with self.subTest(case=case):
                current, intent, fence, request, control = self.selected_start()
                evidence = control.evidence
                if case == "missing":
                    evidence = {"provider_status": "reported-success"}
                elif case == "foreign":
                    evidence = {**evidence, "secret_resolution_receipt": {
                        **evidence["secret_resolution_receipt"], "request_fingerprint": "f" * 64}}
                observation = self.observed(current, intent, evidence)
                admitted = self.observed(current, intent, control.evidence)
                self.assertIsNotNone(secret_installation_receipt_for_observation(request, admitted))
                outcome = ObservedEffectOutcome(current.state.identity, observation)
                command = self.fold_command_for(current, intent, fence, outcome)
                if case != "complete":
                    self.assert_fold_refused_without_changes(command)
                    continue
                folded, _ = self.fold_selected(command, "receipt-observed-terminal")
                self.assertIs(type(folded), NewlyFolded)
                self.assertEqual(folded.outcome_record.outcome, outcome)
                self.assertIs(type(folded.outcome_record.outcome.observation), RuntimeEffectObservedSucceeded)

    def test_durable_fold_rejects_contradictory_nonsuccess_profiles(self):
        for observed in (False, True):
            for uncertain in (False, True):
                for claimed in (False, True):
                    with self.subTest(observed=observed, uncertain=uncertain, claimed=claimed):
                        current, intent, fence, request, control = self.selected_start()
                        evidence = control.evidence if claimed else {"provider_status": "not-proved"}
                        if observed:
                            variant = RuntimeEffectObservedIndeterminate if uncertain else RuntimeEffectObservedFailed
                            value = variant(request.effect_id, runtime_effect_intent_fingerprint(intent),
                                RuntimeEffectObservationEvidence(evidence),
                                RuntimeEffectObservationFailure("fixture.unverified", "Installation is unverified."))
                            outcome = ObservedEffectOutcome(current.state.identity, value)
                            if not claimed:
                                self.assertIsNone(secret_installation_receipt_for_observation(request, value))
                        else:
                            value = RuntimeEffectResult(request.effect_id,
                                EffectResultKind.UNCERTAIN if uncertain else EffectResultKind.FAILED,
                                evidence=evidence,
                                failure=RuntimeEffectFailure("fixture.unverified", "Installation is unverified."))
                            outcome = self.selected_direct_outcome(current, intent, value)
                            if not claimed:
                                self.assertIsNone(secret_installation_receipt_for_result(request, value))
                        command = self.fold_command_for(current, intent, fence, outcome)
                        if claimed:
                            self.assert_fold_refused_without_changes(command)
                        else:
                            folded, _ = self.fold_selected(command, "unproved-terminal")
                            self.assertEqual(folded.outcome_record.outcome, outcome)
                            self.assertIsNot(folded.attempt.state.status, EffectAttemptStatus.SUCCEEDED)

    def test_reconciliation_missing_receipt_records_correlated_indeterminate_once(self):
        for contradictory_failure in (False, True):
            with self.subTest(contradictory_failure=contradictory_failure):
                current, intent, fence, request, control = self.selected_start()
                # Establish a lawful local-admission envelope before asserting
                # missing behavior. Empty evidence is not admitted by Core.
                expected = RuntimeEffectObservedIndeterminate(request.effect_id,
                    runtime_effect_intent_fingerprint(intent),
                    RuntimeEffectObservationEvidence({"admission_boundary": "secret-installation"}),
                    RuntimeEffectObservationFailure("runtime.secret-installation-proof-unavailable",
                        "Secret installation proof is unavailable."))
                self.assertIsNone(secret_installation_receipt_for_observation(request, expected))
                value = self.observed(current, intent, {"provider_status": "reported-success"})
                if contradictory_failure:
                    value = RuntimeEffectObservedFailed(request.effect_id,
                        runtime_effect_intent_fingerprint(intent), RuntimeEffectObservationEvidence(control.evidence),
                        RuntimeEffectObservationFailure("fixture.failed", "Provider reported failure."))
                ledger = UnitOfWorkLedger(self.unit_of_work)
                observer = RecordingObserver(value, ledger=ledger)
                command = self.reconciliation_command(current, generation=fence.generation,
                    scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE))
                folded = self.reconciliation_service(registration(observer, exact_version_observe=True),
                    ledger=ledger, ids=("admission-indeterminate",)).execute(command)
                self.assertEqual(len(observer.calls), 1)
                admitted = folded.outcome_record.outcome.observation
                self.assertIs(type(admitted), RuntimeEffectObservedIndeterminate)
                self.assertEqual((admitted.effect_id, admitted.request_fingerprint),
                    (expected.effect_id, expected.request_fingerprint))
                self.assertEqual(admitted.failure.code, expected.failure.code)
                self.assertEqual(admitted.evidence.descriptor(), expected.evidence.descriptor())
                self.assertEqual(admitted.observations, ())
                self.assertIs(folded.attempt.state.status, EffectAttemptStatus.UNCERTAIN)
                self.assertEqual(ledger.active, 0)

    def test_reconciliation_complete_receipt_and_restart_replay_preserve_original(self):
        current, intent, fence, request, control = self.selected_start()
        value = self.observed(current, intent, control.evidence)
        self.assertIsNotNone(secret_installation_receipt_for_observation(request, value))
        ledger = UnitOfWorkLedger(self.unit_of_work)
        observer = RecordingObserver(value, ledger=ledger)
        command = self.reconciliation_command(current, generation=fence.generation,
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE))
        folded = self.reconciliation_service(registration(observer, exact_version_observe=True),
            ledger=ledger, ids=("installation-observed",)).execute(command)
        self.assertIs(type(folded), NewlyFolded)
        self.assertEqual(folded.outcome_record.outcome.observation, value)
        self.assertEqual(len(observer.calls), 1)
        before = self.complete_reconciliation_snapshot()
        sequence = Sequence("must-not-be-used")
        replay = self.reconciliation_service(FailIfObserver("terminal replay called observer"),
            fold_service=EffectAttemptFoldService(self.unit_of_work, id_factory=sequence)).execute(command)
        self.assertIs(type(replay), ExistingFold)
        self.assertEqual(replay.outcome_record, folded.outcome_record)
        self.assertEqual(sequence.calls, [])
        self.assertEqual(self.complete_reconciliation_snapshot(), before)

    def successful_direct_fold(self):
        current, intent, fence, request, control = self.selected_start()
        command = self.fold_command_for(current, intent, fence, self.selected_direct_outcome(current, intent, control))
        folded, _ = self.fold_selected(command, "original-installation-terminal")
        self.assertIs(type(folded), NewlyFolded)
        self.assertEqual(folded.outcome_record.outcome.result, control)
        return current, intent, fence, command, folded

    @contextmanager
    def corrupt_retained_receipt(self, folded):
        """Recommit the synthetic test outcome consistently, then restore it.

        The isolated test schema is changed atomically so independent service
        transactions see the same corruption. Restoration is unconditional.
        No original intent, graph, authority or provider resource is changed.
        """
        original = folded.outcome_record
        outcome = replace(original.outcome,
            result=replace(original.outcome.result, evidence={"provider_status": "unproved-success"}))
        state = replace(original.attempt.state, outcome_fingerprint=outcome.outcome_fingerprint)
        evidence = BoundedEvidence.from_mapping({"effect_attempt": EffectAttemptEventEvidence(
            state.identity.attempt, effect_attempt_state_fingerprint(state)).descriptor()})
        event = replace(original.attempt.latest_transition_event, evidence=evidence)
        bad = EffectAttemptOutcomeRecord(original.workspace_id, outcome,
            EffectAttemptRecord(state, original.attempt.original_start_event, event), ())
        identity = state.identity
        key = (identity.run_id.value, identity.activity_id, identity.attempt)
        stored = self.connection.execute(
            "SELECT preimage,outcome_fingerprint FROM cpk_effect_attempt_outcomes "
            "WHERE run_id=%s AND activity_id=%s AND attempt=%s", key).fetchone()
        payload = self.connection.execute("SELECT payload FROM cpk_activity_events WHERE event_id=%s",
            (event.event_id,)).fetchone()[0]
        changed = {**payload, "evidence": evidence.descriptor()}
        with self.connection.transaction():
            self.connection.execute("UPDATE cpk_effect_attempt_outcomes SET preimage=%s,outcome_fingerprint=%s "
                "WHERE run_id=%s AND activity_id=%s AND attempt=%s",
                (rfc8785.dumps(outcome.result.descriptor()), outcome.outcome_fingerprint, *key))
            self.connection.execute("UPDATE cpk_effect_attempts SET outcome_fingerprint=%s "
                "WHERE run_id=%s AND activity_id=%s AND attempt=%s", (outcome.outcome_fingerprint, *key))
            self.connection.execute("UPDATE cpk_activity_events SET payload=%s WHERE event_id=%s",
                (Jsonb(changed), event.event_id))
        try:
            yield bad
        finally:
            with self.connection.transaction():
                self.connection.execute("UPDATE cpk_effect_attempt_outcomes SET preimage=%s,outcome_fingerprint=%s "
                    "WHERE run_id=%s AND activity_id=%s AND attempt=%s", (*stored, *key))
                self.connection.execute("UPDATE cpk_effect_attempts SET outcome_fingerprint=%s "
                    "WHERE run_id=%s AND activity_id=%s AND attempt=%s", (stored[1], *key))
                self.connection.execute("UPDATE cpk_activity_events SET payload=%s WHERE event_id=%s",
                    (Jsonb(payload), event.event_id))

    def test_outcome_store_insert_requires_original_receipt_without_endpoint_membership(self):
        for complete in (True, False):
            with self.subTest(complete=complete):
                current, intent, _, request, control = self.selected_start()
                outcome = self.selected_direct_outcome(current, intent,
                    control if complete else RuntimeEffectResult.succeeded(request.effect_id))
                state = fold_effect_attempt(current.state, effect_outcome_transition(outcome), fence=current.state.fence)
                before = self.complete_reconciliation_snapshot()
                with self.unit_of_work() as uow:
                    observed = uow.stores.execution.observe_request_lease_for_update(intent.source.request_id)
                    event = ActivityEventRecord("direct-store-terminal", state.identity.run_id.value,
                        uow.stores.execution.next_event_ordinal(state.identity.run_id.value),
                        ActivityEventKind.STEP_SUCCEEDED, observed.observed_at,
                        activity_id=state.identity.activity_id,
                        evidence=BoundedEvidence.from_mapping({"effect_attempt": EffectAttemptEventEvidence(
                            state.identity.attempt, effect_attempt_state_fingerprint(state)).descriptor()}))
                    record = EffectAttemptOutcomeRecord("workspace-a", outcome,
                        EffectAttemptRecord(state, current.original_start_event, event), ())
                    self.assertEqual(record.endpoint_observations, ())
                    uow.stores.execution.add_event(event)
                    if complete:
                        self.assertEqual(uow.stores.effect_outcomes.insert(record), record)
                    else:
                        with self.assertRaises(OperationsRecordError) as caught:
                            uow.stores.effect_outcomes.insert(record)
                        self.assertEqual(str(caught.exception), "effect attempt outcome store input is invalid")
                    # Deliberately roll back both paths: the test owns admission,
                    # not a new direct writer for the attempt's terminal state.
                self.assertEqual(self.complete_reconciliation_snapshot(), before)

    def test_outcome_store_reload_refuses_self_consistent_receipt_corruption(self):
        _, _, _, _, folded = self.successful_direct_fold()
        identity, event = folded.attempt.state.identity, folded.attempt.latest_transition_event.event_id
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.effect_outcomes.get(identity, event), folded.outcome_record)
        with self.corrupt_retained_receipt(folded) as bad:
            self.assertEqual(bad.attempt.state.outcome_fingerprint, bad.outcome.outcome_fingerprint)
            with self.unit_of_work() as uow:
                self.assertEqual(uow.stores.effect_attempts.get(identity), bad.attempt)
                with self.assertRaises(OperationsRecordError) as caught:
                    uow.stores.effect_outcomes.get(identity, event)
                self.assertEqual(str(caught.exception), "effect attempt outcome row is invalid")
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.effect_outcomes.get(identity, event), folded.outcome_record)

    def test_original_replay_refuses_corrupt_receipt_without_effects_or_repair(self):
        current, intent, fence, command, folded = self.successful_direct_fold()
        with self.corrupt_retained_receipt(folded) as bad:
            before = self.complete_reconciliation_snapshot()
            corrupt_command = self.fold_command_for(current, intent, fence, bad.outcome)
            self.assert_fold_refused_without_changes(corrupt_command)
            sequence = Sequence("must-not-be-used")
            replay = self.reconciliation_service(FailIfObserver("corrupt history invoked observer"),
                fold_service=EffectAttemptFoldService(self.unit_of_work, id_factory=sequence))
            with self.assertRaises(EffectAttemptReconciliationConflict):
                replay.execute(self.reconciliation_command(current, generation=fence.generation,
                    scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE)))
            self.assertEqual(sequence.calls, [])
            self.assertEqual(self.complete_reconciliation_snapshot(), before)
        restored, _ = self.fold_selected(command)
        self.assertIs(type(restored), ExistingFold)
        self.assertEqual(restored.outcome_record, folded.outcome_record)

    def test_new_original_reads_are_charged_before_transport(self):
        _, _, _, _, folded = self.successful_direct_fold()
        identity, event = folded.attempt.state.identity, folded.attempt.latest_transition_event.event_id
        for exhaust in (False, True):
            with self.subTest(exhaust=exhaust):
                rows, holder, injected = [], {}, []

                def observe(observer, query, row):
                    rows.append((str(query), row))
                    is_original_probe = ("FROM cpk_effect_attempt_intents " in str(query)
                        and row and all(value is None or type(value) is int for value in row))
                    if exhaust and is_original_probe and not injected:
                        # Explicit near-capacity state after the real scalar
                        # probe; never modify returned rows or decoder results.
                        read = holder["read"]
                        remaining = 16 * 1024 * 1024 - 1024 - read.used.accounted_bytes
                        self.assertGreater(remaining, 0)
                        read.used = read.used.plus(ConfigurationEvidenceFootprint(0, remaining, 0, 0))
                        injected.append(True)

                observer = ProofReadObserver(psycopg.connect(self.database_url), observe)
                with PostgresUnitOfWork(lambda: observer) as uow, _composed_read(uow.stores.connection) as read:
                    holder["read"] = read
                    observer.enabled = True
                    if exhaust:
                        with self.assertRaises(OperationsRecordError):
                            uow.stores.effect_outcomes.get(identity, event)
                    else:
                        self.assertEqual(uow.stores.effect_outcomes.get(identity, event), folded.outcome_record)
                    observer.enabled = False
                    original_rows = [row for query, row in rows if "FROM cpk_effect_attempt_intents " in query]
                    self.assertEqual(len(original_rows), 1 if exhaust else 2)
                    if exhaust:
                        self.assertEqual(injected, [True])
                        self.assertTrue(all(type(value) is int for value in original_rows[0]))
                    else:
                        self.assertEqual(read.used.value_octets, observer.value_bytes)
                        self.assertEqual(read.used.scalar_markers, sum(len(row) for _, row in rows))
                        self.assertIs(original_rows[1][-1], True)
                    self.assertLessEqual(read.used.accounted_bytes, 16 * 1024 * 1024)

    def test_coordinator_direct_adapter_cannot_bypass_receipt_admission(self):
        from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
        from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
        from tests.graph_lineage_fixture import execution_graph
        from tests.test_execution_coordinator import (
            ExecutionCoordinatorTests, RecordingAdapter, TrackingUnitOfWorkFactory,
        )

        for complete in (True, False):
            with self.subTest(complete=complete):
                template = selected_context()
                engine = ExecutionCoordinatorTests()
                engine.database_url, engine.connection = self.database_url, self.connection
                engine.tracker = TrackingUnitOfWorkFactory(self.database_url)
                engine.reset_execution_request(plan=template.plan_record.plan,
                    base_graph=execution_graph("current", runtime_id="runtime-a"),
                    desired_graph=DEFAULT_GRAPH_CODEC.decode(template.desired_graph.graph_descriptor),
                    product_document=template.registered_products[0].descriptor_document)
                engine.claim_and_start()
                with self.unit_of_work() as uow:
                    stores = uow.stores
                    plan = stores.activity_history.get_plan("plan-a")
                    activity, = plan.plan.activities
                    context = replace(template, activity=activity, plan_record=plan,
                        request=stores.execution.get_request("request-a"), run=stores.execution.get_run("run-a"),
                        base_graph=stores.realized_graphs.get(plan.base_realized_projection_id),
                        desired_graph=stores.realized_graphs.get(plan.desired_realized_projection_id),
                        registered_products=stores.registered_products.list_active("workspace-a"))
                intent = _runtime_effect_intent_for_context(context, activity)
                self.assertTrue(intent.products[0].secret_selections)
                self._admit_version_reconciliation_material(intent)
                engine.ids = GeneratedIds("coordinator-receipt")

                def result_for(_context, request):
                    control = receipt_result(request)
                    self.assertIsNotNone(secret_installation_receipt_for_result(request, control))
                    return control if complete else RuntimeEffectResult.succeeded(request.effect_id)

                adapter = RecordingAdapter(engine.tracker, result_for)
                result = engine.coordinator(adapter).execute(engine.command(
                    scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE)))
                self.assertEqual(adapter.calls, [activity.activity_id.value])
                self.assertEqual(adapter.active_during_calls, [0])
                self.assertIs(result.status, CoordinatorStatus.COMPLETED if complete else CoordinatorStatus.UNCERTAIN)
                with self.unit_of_work() as uow:
                    events = uow.stores.execution.events_for_run("run-a")
                    attempts = self.connection.execute("SELECT status FROM cpk_effect_attempts").fetchall()
                self.assertEqual(attempts, [("succeeded" if complete else "uncertain",)])
                if not complete:
                    self.assertFalse(any(event.kind is ActivityEventKind.STEP_SUCCEEDED for event in events))

    def receiver_read(self):
        observer = ProofReadObserver(psycopg.connect(self.database_url))
        with PostgresUnitOfWork(lambda: observer) as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            observer.enabled = True
            evidence = uow.stores.execution.receiver_scope_evidence("workspace-a",
                (ExecutionReceiverScope("runtime-a", "api"),), guard)
            observer.enabled = False
            result = classify_receiver_scope_evidence(evidence)
        self.assertLessEqual(observer.value_bytes, 16 * 1024 * 1024)
        self.assertLessEqual(observer.largest_cell, 1024 * 1024)
        original_queries = [query for query in observer.queries if "cpk_effect_attempt_intents" in query]
        self.assertTrue(original_queries)
        # Receiver history already obtains the complete run's originals in its
        # bounded batch. Outcome admission must not add individual point gets.
        self.assertFalse(any("activity_id=%s AND attempt=%s" in query for query in original_queries))
        return result, len(original_queries)

    def test_receiver_history_reuses_loaded_original_and_refuses_missing_receipt(self):
        _, _, _, _, folded = self.successful_direct_fold()
        control, original_queries = self.receiver_read()
        self.assertEqual(control.disposition, "conflict")
        self.assertIn("request-a", control.request_ids)
        with self.corrupt_retained_receipt(folded):
            invalid, invalid_queries = self.receiver_read()
            self.assertEqual(invalid.disposition, "unavailable")
            self.assertEqual(invalid_queries, original_queries)
        restored, restored_queries = self.receiver_read()
        self.assertEqual(restored, control)
        self.assertEqual(restored_queries, original_queries)
