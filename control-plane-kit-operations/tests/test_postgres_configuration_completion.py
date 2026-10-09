"""D1 admission belongs to the original fold; profiles alone grant no authority.

Uses real preparation/fold/Postgres owners. SQL corruption and injected faults
are negative evidence only; none represents an admitted provider completion.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import queue
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCompletion, configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict, ExistingFold, FoldEffectAttempt, NewlyFolded,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_outcome_evidence import (
    ExecutionEffectOutcome, effect_outcome_failure, effect_outcome_transition,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.postgres.configuration_preparation_store import ConfigurationPreparationStore
from control_plane_kit_operations.postgres.schema import SchemaInstallationError
from control_plane_kit_operations.records import OperationsRecordError
from tests.configuration_preparation_fixture import ConfigurationPreparationFixture
from tests.execution_lease_recovery_fixture import Sequence
from tests import test_postgres_effect_attempt_start_concurrency as concurrency
from tests.test_postgres_effect_attempt_start_eligibility_rollback import _CommitFailureConnection


RELATION = "cpk_configuration_invocation_completions"


class _AfterWriteFailure:
    def __init__(self, connection, statement, fault):
        self.connection, self.statement, self.fault = connection, statement, fault

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, query, *args, **kwargs):
        result = self.connection.execute(query, *args, **kwargs)
        text = query.as_string(self.connection) if hasattr(query, "as_string") else str(query)
        if self.statement in " ".join(text.lower().replace('"', '').split()):
            raise self.fault
        return result


class PostgresConfigurationCompletionTests(ConfigurationPreparationFixture, unittest.TestCase):
    _factory_with_pids = concurrency.PostgresEffectAttemptStartConcurrencyTests._factory_with_pids
    _wait_until_blocked_by = concurrency.PostgresEffectAttemptStartConcurrencyTests._wait_until_blocked_by

    def started(self):
        command = self.configuration_command()
        return command, self.start_service("completion-start").execute(command)

    def completion_store(self, stores):
        store = getattr(stores, "configuration_completions", None)
        self.assertIsNotNone(store, "missing D1 original-fold completion owner")
        self.assertTrue(callable(getattr(store, "get", None)), "missing D1 admitted point reader")
        return store

    def admitted(self, identity):
        with self.unit_of_work() as uow:
            return self.completion_store(uow.stores).get(identity)

    def fold_command(self, original, started, *, status="succeeded", profile=True, changes=None):
        attempt = started.attempt
        evidence = {"fixture": "simulated-invocation-only", "retained": {"value": "ordinary-result"}}
        if profile:
            completion = ConfigurationInvocationCompletion(attempt.state.request_fingerprint,
                configuration_invocation_selection_fingerprint(original.intent.configuration_instances))
            evidence["configuration_invocation_completion"] = completion.descriptor() | (changes or {})
        effect_id = attempt.original_start_event.event_id
        if status == "succeeded":
            result = RuntimeEffectResult.succeeded(effect_id, evidence=evidence)
        else:
            result = replace(getattr(RuntimeEffectResult, status)(effect_id,
                RuntimeEffectFailure("configuration.test-outcome", "Bounded test outcome.")), evidence=evidence)
        outcome = ExecutionEffectOutcome(attempt.state.identity, attempt.state.request_fingerprint, result)
        return FoldEffectAttempt("request-a", effect_outcome_transition(outcome), self.authority(), self.fence(),
                                 effect_outcome_failure(outcome), outcome)

    def fold(self, command, *, factory=None, ids=None):
        return EffectAttemptFoldService(factory or self.unit_of_work,
            id_factory=ids or Sequence("completion-terminal")).execute(command)

    def durable_snapshot(self):
        rows = []
        for relation in ("cpk_effect_attempt_outcomes", "cpk_effect_attempt_outcome_observations", RELATION):
            present = self.connection.execute("SELECT to_regclass(%s)", (relation,)).fetchone()[0]
            rows.append((relation, None if present is None else tuple(self.connection.execute(
                f"SELECT * FROM {relation} ORDER BY run_id, activity_id, attempt").fetchall())))
        return self.complete_start_snapshot(), tuple(rows)

    def test_success_and_failure_admit_exact_original_completion_without_releasing_claims(self):
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                self.reset_start_truth()
                original, started = self.started()
                identity = started.attempt.state.identity
                self.assertIsNone(self.admitted(identity))
                protection = self.protection_rows()
                command = self.fold_command(original, started, status=status)
                folded = self.fold(command)
                self.assertIsInstance(folded, NewlyFolded)
                record = self.admitted(identity)
                self.assertIsNotNone(record)
                self.assertEqual((record.identity, record.workspace_id, record.request_fingerprint),
                    (identity, "workspace-a", started.attempt.state.request_fingerprint))
                self.assertEqual(record.selection_fingerprint,
                    configuration_invocation_selection_fingerprint(original.intent.configuration_instances))
                self.assertEqual(record.outcome_fingerprint, folded.attempt.state.outcome_fingerprint)
                self.assertEqual((record.original_event_id, record.original_event_ordinal),
                    (started.attempt.original_start_event.event_id, started.attempt.original_start_event.ordinal))
                self.assertEqual((record.direct_event_id, record.direct_event_ordinal),
                    (folded.attempt.latest_transition_event.event_id, folded.attempt.latest_transition_event.ordinal))
                with self.assertRaises(FrozenInstanceError):
                    record.selection_fingerprint = "f" * 64
                self.assertEqual(folded.outcome_record.outcome, command.outcome)
                self.assertEqual(self.protection_rows(), protection)
                before = self.durable_snapshot()
                install_schema(self.connection)
                self.assertEqual(self.durable_snapshot(), before)
                ids = Sequence("must-not-be-used")
                replay = self.fold(command, ids=ids)
                self.assertIsInstance(replay, ExistingFold)
                self.assertEqual(replay.attempt, folded.attempt)
                self.assertEqual(replay.outcome_record, folded.outcome_record)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.admitted(identity), record)
                self.assertEqual(self.durable_snapshot(), before)

    def test_unprofiled_terminal_outcomes_remain_unadmitted(self):
        for status in ("succeeded", "failed", "uncertain", "unsupported"):
            with self.subTest(status=status):
                self.reset_start_truth()
                original, started = self.started()
                protection = self.protection_rows()
                self.assertIsNone(self.admitted(started.attempt.state.identity))
                folded = self.fold(self.fold_command(original, started, status=status, profile=False))
                self.assertIsInstance(folded, NewlyFolded)
                self.assertIsNone(self.admitted(started.attempt.state.identity))
                self.assertEqual(self.protection_rows(), protection)

    def test_wrong_original_profile_or_nonterminal_assertion_refuses_before_ids_and_writes(self):
        cases = (("succeeded", {"request_fingerprint": "f" * 64}),
                 ("succeeded", {"selection_fingerprint": "f" * 64}), ("uncertain", {}))
        for status, changes in cases:
            with self.subTest(status=status, changes=changes):
                self.reset_start_truth()
                original, started = self.started()
                before = self.durable_snapshot()
                command = self.fold_command(original, started, status=status, changes=changes)
                ids = Sequence("must-not-be-used")
                with self.assertRaises(EffectAttemptFoldConflict):
                    self.fold(command, ids=ids)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.durable_snapshot(), before)

    def test_historical_profile_without_link_replays_without_backfill_or_permission(self):
        original, started = self.started()
        command = self.fold_command(original, started)
        folded = self.fold(command)
        self.assertIsNotNone(self.admitted(started.attempt.state.identity))
        # Construct the supported historical state: retained original profile,
        # no admitted link. Deletion grants no authority and does not change claims.
        self.connection.execute(f"DELETE FROM {RELATION}")
        before = self.durable_snapshot()
        ids = Sequence("must-not-be-used")
        replay = self.fold(command, ids=ids)
        self.assertIsInstance(replay, ExistingFold)
        self.assertEqual((replay.attempt, replay.outcome_record), (folded.attempt, folded.outcome_record))
        self.assertIsNone(self.admitted(started.attempt.state.identity))
        install_schema(self.connection)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.durable_snapshot(), before)

    def test_generic_outcome_store_does_not_recreate_admission(self):
        original, started = self.started()
        folded = self.fold(self.fold_command(original, started))
        identity = started.attempt.state.identity
        self.assertIsNotNone(self.admitted(identity))
        protection = self.protection_rows()
        # Retain an actual owner's immutable result, then replay only the generic
        # outcome insertion boundary. This is below-owner negative setup.
        self.connection.execute(f"DELETE FROM {RELATION}")
        self.connection.execute("DELETE FROM cpk_effect_attempt_outcomes WHERE run_id='run-a'")
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.effect_outcomes.insert(folded.outcome_record), folded.outcome_record)
            uow.commit()
        self.assertIsNone(self.admitted(identity))
        self.assertEqual(self.protection_rows(), protection)

    def test_corrupt_selection_link_refuses_read_replay_and_current_verification_without_repair(self):
        original, started = self.started()
        command = self.fold_command(original, started)
        self.fold(command)
        self.assertIsNotNone(self.admitted(started.attempt.state.identity))
        self.connection.execute(f"UPDATE {RELATION} SET selection_fingerprint=%s", ("f" * 64,))
        before = self.durable_snapshot()
        with self.assertRaises(OperationsRecordError):
            self.admitted(started.attempt.state.identity)
        with self.assertRaises(SchemaInstallationError):
            install_schema(self.connection)
        ids = Sequence("must-not-be-used")
        with self.assertRaises(EffectAttemptFoldConflict):
            self.fold(command, ids=ids)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.durable_snapshot(), before)

    def test_completion_fk_refuses_wrong_outcome_commitment(self):
        original, started = self.started()
        self.fold(self.fold_command(original, started))
        self.assertIsNotNone(self.admitted(started.attempt.state.identity))
        before = self.durable_snapshot()
        with self.assertRaises(psycopg.IntegrityError):
            with psycopg.connect(self.database_url) as connection:
                connection.execute(f"UPDATE {RELATION} SET outcome_fingerprint=%s", ("f" * 64,))
        self.assertEqual(self.durable_snapshot(), before)

    def test_private_insert_requires_the_issued_guard_in_the_original_uow(self):
        original, started = self.started()
        with self.unit_of_work() as uow:
            owner = type(self.completion_store(uow.stores))
        insert = getattr(owner, "_insert", None)
        self.assertTrue(callable(insert), "missing D1 guarded completion insertion")
        captured = []
        def check_issued(store, prepared, outcome_record):
            # The private owner boundary must reject an absent or reconstructed
            # token even on the correct connection, before touching SQL.
            for forged in (None, replace(prepared)):
                with self.assertRaises(OperationsRecordError):
                    insert(store, forged, outcome_record)
            captured.append(prepared)
            return insert(store, prepared, outcome_record)
        with mock.patch.object(owner, "_insert", check_issued):
            folded = self.fold(self.fold_command(original, started))
        self.assertEqual(len(captured), 1)
        self.assertIsNotNone(self.admitted(started.attempt.state.identity))
        before = self.durable_snapshot()
        with self.unit_of_work() as uow:
            uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            with self.assertRaises(OperationsRecordError):
                self.completion_store(uow.stores)._insert(captured[0], folded.outcome_record)
        self.assertEqual(self.durable_snapshot(), before)

    def test_event_outcome_attempt_link_and_commit_faults_roll_back_the_entire_fold(self):
        original, started = self.started()
        self.assertIsNone(self.admitted(started.attempt.state.identity))
        command = self.fold_command(original, started)
        before = self.durable_snapshot()
        stages = ("insert into cpk_activity_events", "insert into cpk_effect_attempt_outcomes",
                  "update cpk_effect_attempts", f"insert into {RELATION}", "commit")
        for stage in stages:
            with self.subTest(stage=stage):
                fault = RuntimeError("injected completion transaction failure")
                def factory():
                    return PostgresUnitOfWork(lambda: _CommitFailureConnection(
                        psycopg.connect(self.database_url), fault) if stage == "commit" else
                        _AfterWriteFailure(psycopg.connect(self.database_url), stage, fault))
                with self.assertRaises(RuntimeError) as caught:
                    self.fold(command, factory=factory)
                self.assertIs(caught.exception, fault)
                self.assertEqual(self.durable_snapshot(), before)
        self.assertIsInstance(self.fold(command), NewlyFolded)
        self.assertIsNotNone(self.admitted(started.attempt.state.identity))

    def test_completion_fresh_fold_waits_for_l_before_ids(self):
        original, started = self.started()
        self.assertIsNone(self.admitted(started.attempt.state.identity))
        command = self.fold_command(original, started)
        pids, ids = queue.Queue(), Sequence("completion-locked")
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.unit_of_work() as held:
                held.stores.graphs.lock_receiver_lifecycle("workspace-a")
                holder = held.stores.connection.execute("SELECT pg_backend_pid()").fetchone()[0]
                future = executor.submit(self.fold, command, factory=self._factory_with_pids(pids), ids=ids)
                try:
                    self._wait_until_blocked_by(pids.get(timeout=5), holder)
                except AssertionError:
                    if future.done():
                        future.result(timeout=1)
                    raise
                self.assertEqual(ids.calls, [])
            folded = future.result(timeout=10)
        self.assertIsInstance(folded, NewlyFolded)
        self.assertIsNotNone(self.admitted(started.attempt.state.identity))

    def test_completion_preflight_refuses_a_nearly_exhausted_fold_budget_before_ids(self):
        from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
        original, started = self.started()
        command = self.fold_command(original, started)
        before = self.durable_snapshot()
        actual, injected = ConfigurationPreparationStore._require_original, []
        def consume_prelude(store, *args, **kwargs):
            result = actual(store, *args, **kwargs)
            accounting = _ACCOUNTING.get()
            self.assertIsNotNone(accounting)
            self.assertTrue(accounting.active)
            self.assertGreater(accounting.used.records, 0)
            # Preserved capacity refusal after real original proof. This can
            # refuse at observation and alone does not prove ledger continuity.
            accounting.used = ConfigurationEvidenceFootprint(4095, 0, 0, 0)
            injected.append(accounting)
            return result
        ids = Sequence("must-not-be-used")
        with mock.patch.object(ConfigurationPreparationStore, "_require_original", consume_prelude):
            with self.assertRaises(EffectAttemptFoldConflict):
                self.fold(command, ids=ids)
        self.assertEqual(len(injected), 1)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.durable_snapshot(), before)

    def test_completion_uses_one_fold_ledger_and_preserves_every_prior_query_charge(self):
        from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
        from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead
        original, started = self.started()
        with self.unit_of_work() as uow:
            owner = type(self.completion_store(uow.stores))
        insert = getattr(owner, "_insert", None)
        self.assertTrue(callable(insert), "missing D1 guarded completion insertion")
        query = _EvidenceRead.query
        root, traces, inserted = [], [], []
        def components(footprint):
            return (footprint.records, footprint.value_octets, footprint.scalar_markers, footprint.statements)
        def assert_prior_charges(accounting):
            self.assertIs(accounting, root[0], "completion replaced the original fold ledger")
            if traces:
                for actual, prior in zip(components(accounting.used), components(traces[-1])):
                    self.assertGreaterEqual(actual, prior, "completion erased accumulated evidence charges")
        def factory():
            # Capture before routing/original-source queries, while the fold
            # owner exists but configuration accounting may still be inactive.
            root.append(_ACCOUNTING.get())
            self.assertIsNotNone(root[-1])
            return self.unit_of_work()
        def traced_query(reader, *args, **kwargs):
            assert_prior_charges(reader.accounting)
            result = query(reader, *args, **kwargs)
            self.assertIs(_ACCOUNTING.get(), root[0])
            traces.append(reader.used)
            return result
        def traced_insert(store, prepared, outcome_record):
            accounting = _ACCOUNTING.get()
            assert_prior_charges(accounting)
            self.assertTrue(accounting.active)
            self.assertTrue(traces, "completion insertion had no original evidence prelude")
            self.assertGreater(accounting.used.records, 0)
            result = insert(store, prepared, outcome_record)
            assert_prior_charges(_ACCOUNTING.get())
            inserted.append(True)
            return result
        with mock.patch.object(_EvidenceRead, "query", traced_query), \
                mock.patch.object(owner, "_insert", traced_insert):
            folded = self.fold(self.fold_command(original, started), factory=factory)
        self.assertIsInstance(folded, NewlyFolded)
        self.assertEqual(len(root), 1)
        self.assertEqual(inserted, [True])
        self.assertGreater(traces[-1].records, traces[0].records)
        self.assertGreater(traces[-1].statements, traces[0].statements)
        self.assertIsNotNone(self.admitted(started.attempt.state.identity))

    def test_private_preparation_cannot_backfill_terminal_or_stale_started_history(self):
        from control_plane_kit_operations._configuration_preparation import _configuration_accounting
        from control_plane_kit_operations.effect_run_prefix import _lock_effect_run_prefix
        from control_plane_kit_operations.postgres.configuration_evidence import _joined_read
        original, started = self.started()
        command = self.fold_command(original, started)
        folded = self.fold(command)
        self.assertIsNotNone(self.admitted(started.attempt.state.identity))
        self.connection.execute(f"DELETE FROM {RELATION}")
        before = self.durable_snapshot()
        for supplied in (folded.attempt, started.attempt):
            with self.subTest(status=supplied.state.status):
                with _configuration_accounting("run-a"), self.unit_of_work() as uow:
                    stores = uow.stores
                    with _joined_read(stores.connection):
                        guard = stores.graphs.lock_receiver_lifecycle("workspace-a")
                        request = stores.execution.get_request_for_update("request-a")
                        prefix = _lock_effect_run_prefix(uow, request, "run-a", latest_required=True)
                        current = stores.effect_attempts.get_for_update(started.attempt.state.identity)
                        retained = stores.effect_attempt_intents.get(current.state.identity)
                        self.assertEqual(current, folded.attempt)
                        with self.assertRaises(OperationsRecordError):
                            self.completion_store(stores)._prepare(stores, guard, retained, supplied, command.outcome,
                                unit_of_work=uow, prefix=prefix, request=request, fence=started.attempt.state.fence)
                        uow.commit()
                self.assertIsNone(self.admitted(started.attempt.state.identity))
                self.assertEqual(self.durable_snapshot(), before)

    def verification_fold(self):
        from control_plane_kit_core.products import ProductDescriptorCodec
        from control_plane_kit_core.verification import (
            HttpCheck, HttpVerificationEvidence, VerificationCapability, VerificationCompleted,
            VerificationContract, VerificationIdentity, VerificationOutcome,
        )
        from control_plane_kit_operations.products import RegisteredProduct
        from tests.test_runtime_effect_translation import _configuration_product
        registered = _configuration_product()
        product = registered.descriptor_document.product
        contract = replace(product.runtime_contract, verification=VerificationContract((
            HttpCheck(check_id="completion-ready", provider_socket="http", path="/", expected_body_sha256="d" * 64),)))
        registered = RegisteredProduct.from_document(workspace_id="workspace-a",
            descriptor_document=ProductDescriptorCodec().encode_document(replace(product, runtime_contract=contract)),
            source=registered.source, imported_by=registered.imported_by, imported_at=registered.imported_at)
        # Only select fixture input; real registered product, graph, preparation,
        # request and original-intent owners persist and validate that input.
        with mock.patch("tests.configuration_preparation_fixture._configuration_product", return_value=registered):
            self.reset_start_truth()
        original, started = self.started()
        command = self.fold_command(original, started)
        verification = VerificationCompleted(
            VerificationIdentity("api", original.intent.source.desired_graph_id, "completion-ready"),
            VerificationCapability.HTTP, VerificationOutcome.PASSED, 1,
            HttpVerificationEvidence(200, 21, expected_body_sha256="d" * 64, body_sha256_matches=True))
        result = replace(command.outcome.result, observations=(verification,))
        outcome = ExecutionEffectOutcome(command.outcome.identity, command.outcome.request_fingerprint, result)
        return replace(command, outcome=outcome, transition=effect_outcome_transition(outcome),
                       failure=effect_outcome_failure(outcome)), started

    def test_completion_preserves_real_product_bound_verification_membership(self):
        command, started = self.verification_fold()
        folded = self.fold(command, ids=Sequence("completion-terminal", "completion-verification"))
        self.assertEqual(folded.outcome_record.outcome, command.outcome)
        self.assertEqual(len(folded.outcome_record.endpoint_observations), 1)
        self.assertIsNotNone(self.admitted(started.attempt.state.identity))
        with self.unit_of_work() as uow:
            retained = uow.stores.effect_outcomes.get(started.attempt.state.identity,
                folded.attempt.latest_transition_event.event_id)
        self.assertEqual(retained, folded.outcome_record)
        install_schema(self.connection)

    def test_verification_completion_reserves_its_full_tail_before_ids(self):
        from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead
        command, started = self.verification_fold()
        before = self.durable_snapshot()
        actual, injected = _EvidenceRead.query, []
        def restrict_tail(reader, sql, params, **kwargs):
            result = actual(reader, sql, params, **kwargs)
            if f"FROM {RELATION} WHERE" in sql and not injected:
                # Fifteen remaining relational identities cover the ordinary
                # tail but not the supported verification's repeated source
                # and event reads plus generic ownership evidence.
                reader.used = replace(reader.used, records=4096 - 15)
                injected.append(True)
            return result
        ids = Sequence("must-not-be-used")
        with mock.patch.object(_EvidenceRead, "query", restrict_tail):
            with self.assertRaises(EffectAttemptFoldConflict):
                self.fold(command, ids=ids)
        self.assertEqual(injected, [True])
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.durable_snapshot(), before)
        self.assertIsNone(self.admitted(started.attempt.state.identity))


if __name__ == "__main__":
    unittest.main()
