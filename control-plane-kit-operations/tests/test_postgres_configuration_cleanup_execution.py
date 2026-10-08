"""#1936 public-owner targets; simulated adapter results are not provider proof."""
import unittest
from dataclasses import replace
from unittest import mock

import psycopg

from control_plane_kit_core.configuration_instances import (
    ConfigurationCleanupOutcome, ConfigurationCleanupOutcomeSet, ConfigurationCleanupStatus,
    ConfigurationCleanupReason,
)
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptStatus, RunId
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectFailure, RuntimeEffectResult, configuration_cleanup_result, configuration_cleanup_outcomes,
)
from control_plane_kit_operations.coordinator import CoordinatorStatus, RuntimeInterpreterDispatcher
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING, _configuration_accounting
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from tests.configuration_cleanup_execution_fixture import ConfigurationCleanupExecutionFixture
from tests.configuration_cleanup_phase_read_bounds_fixture import _PhaseConnection, _components
from tests.postgres_effect_attempt_coordinator_fixture import RecordingRuntimeAdapter


class PostgresConfigurationCleanupExecutionTests(ConfigurationCleanupExecutionFixture, unittest.TestCase):
    def test_public_k1_admission_lifecycle_start_fold_and_no_dispatch_replay(self):
        self.prepare_cleanup_execution()
        admitted = self.admit_cleanup()
        self.assertEqual(admitted.request.identity.plan_id, self.plan.plan_id)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_configuration_cleanup_reservations").fetchone(), (0,))
        claimed = self.ready_run("cleanup-execution")
        expected = ConfigurationCleanupOutcomeSet((ConfigurationCleanupOutcome(
            self.selected_ref, ConfigurationCleanupStatus.REMOVED, None),))

        def removed(_context, request):
            self.assertEqual(request.operation.instances, (self.selected_ref,))
            self.assertEqual(request.authority_ref, self.registration.authority_ref)
            identity = EffectAttemptIdentity(request.source.run_id, request.activity_id.value, 1)
            # An independent transaction must see the complete committed start
            # before the coordinator is permitted to invoke its runtime adapter.
            # This observer is test instrumentation, outside the coordinator's
            # work. Its own ledger must not consume the measured command budget.
            with _configuration_accounting(("committed-start-observer", identity)), self.unit_of_work() as uow:
                reservation = uow.stores.configuration_cleanup_ownership.get(identity)
                self.assertIsNotNone(reservation)
                self.assertIs(reservation.status, EffectAttemptStatus.STARTED)
                self.assertEqual(tuple(member.ref for member in reservation.members), (self.selected_ref,))
                self.assertEqual(len(reservation.claims), 1)
                self.assertEqual(reservation.completions, (self.completion,))
            return configuration_cleanup_result(request, expected)

        adapter = RecordingRuntimeAdapter(removed)
        coordinator = self.coordinator(self.unit_of_work, adapter, "cleanup-execution")
        command = self.execution_command(claimed, "cleanup-execution")
        result = coordinator.execute(command)
        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual(adapter.legacy_calls, [])
        request = adapter.runtime_calls[0][1]
        identity = EffectAttemptIdentity(RunId(claimed.run.run_id), request.activity_id.value, 1)
        with self.unit_of_work() as uow:
            reservation = uow.stores.configuration_cleanup_ownership.get(identity)
            self.assertIs(reservation.status, EffectAttemptStatus.SUCCEEDED)
            self.assertEqual(reservation.outcomes, expected)
            attempt = uow.stores.effect_attempts.get(identity)
            outcome = uow.stores.effect_outcomes.get(identity, attempt.latest_transition_event.event_id)
            self.assertEqual(configuration_cleanup_outcomes(request, outcome.outcome.result), expected)
        before = self.ceiling_truth()
        replay = coordinator.execute(command)
        self.assertIs(replay.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual(self.ceiling_truth(), before)

    def assert_uncertain_cleanup(self, producer, *, reason=ConfigurationCleanupReason.PROVIDER_UNCERTAIN):
        claimed = self.ready_cleanup()
        adapter = RecordingRuntimeAdapter(producer)
        coordinator = self.coordinator(self.unit_of_work, adapter, "cleanup-execution")
        command = self.execution_command(claimed, "cleanup-execution")
        result = coordinator.execute(command)
        self.assertIs(result.status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(len(adapter.runtime_calls), 1)
        request = adapter.runtime_calls[0][1]
        expected = ConfigurationCleanupOutcomeSet((ConfigurationCleanupOutcome(
            self.selected_ref, ConfigurationCleanupStatus.UNKNOWN, reason),))
        identity = EffectAttemptIdentity(request.source.run_id, request.activity_id.value, 1)
        with self.unit_of_work() as uow:
            reservation = uow.stores.configuration_cleanup_ownership.get(identity)
            self.assertIs(reservation.status, EffectAttemptStatus.UNCERTAIN)
            self.assertEqual(reservation.outcomes, expected)
            self.assertEqual(len(reservation.members), 1)
            self.assertEqual(len(reservation.claims), 1)
            self.assertEqual(reservation.completions, (self.completion,))
            attempt = uow.stores.effect_attempts.get(identity)
            outcome = uow.stores.effect_outcomes.get(identity, attempt.latest_transition_event.event_id)
            self.assertEqual(configuration_cleanup_outcomes(request, outcome.outcome.result), expected)
        before = self.ceiling_truth()
        self.assertNotIn("PROVIDER-CANARY", repr(before))
        replay = coordinator.execute(command)
        self.assertIs(replay.status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual(self.ceiling_truth(), before)

    def test_adapter_exception_retains_total_unknown_and_never_redispatches(self):
        self.assert_uncertain_cleanup(RuntimeError("PROVIDER-CANARY"))

    def test_wrong_adapter_result_type_retains_total_unknown(self):
        self.assert_uncertain_cleanup({"provider": "PROVIDER-CANARY"})

    def test_wrong_effect_id_retains_total_unknown(self):
        self.assert_uncertain_cleanup(RuntimeEffectResult.succeeded("foreign-event"))

    def test_invoked_adapter_unsupported_is_provider_uncertain_not_no_call(self):
        self.assert_uncertain_cleanup(lambda _context, request: RuntimeEffectResult.unsupported(
            request.effect_id, RuntimeEffectFailure("provider.unsupported", "PROVIDER-CANARY")))

    def test_generic_failed_result_is_not_a_conserved_cleanup_outcome(self):
        self.assert_uncertain_cleanup(lambda _context, request: RuntimeEffectResult.failed(
            request.effect_id, RuntimeEffectFailure("provider.failed", "PROVIDER-CANARY")))

    def test_missing_cleanup_evidence_is_not_success(self):
        self.assert_uncertain_cleanup(lambda _context, request: RuntimeEffectResult.succeeded(request.effect_id))

    def test_malformed_cleanup_evidence_retains_total_unknown(self):
        self.assert_uncertain_cleanup(lambda _context, request: RuntimeEffectResult.succeeded(
            request.effect_id, evidence={"configuration_cleanup": {
                "profile": "PROVIDER-CANARY", "outcomes": []}}))

    def test_nonconserved_cleanup_evidence_retains_original_candidates(self):
        def foreign_candidate(_context, request):
            rows = ConfigurationCleanupOutcomeSet((ConfigurationCleanupOutcome(
                self.selected_ref, ConfigurationCleanupStatus.REMOVED, None),))
            result = configuration_cleanup_result(request, rows)
            evidence = result.descriptor()["evidence"]
            evidence["configuration_cleanup"]["outcomes"][0]["ref"]["allocation_id"] = "foreign-allocation"
            return replace(result, evidence=evidence)
        self.assert_uncertain_cleanup(foreign_candidate)

    def test_missing_interpreter_is_known_not_attempted(self):
        dispatcher = RuntimeInterpreterDispatcher({})
        self.assert_uncertain_cleanup(dispatcher.execute_runtime,
            reason=ConfigurationCleanupReason.NOT_ATTEMPTED)

    def test_missing_authority_at_dispatch_is_known_not_attempted(self):
        calls = []

        class Interpreter:
            def execute(self, request):
                calls.append(request)
                raise AssertionError("missing authority must refuse before interpreter invocation")

        def missing_authority(context, request):
            # A below-adapter fault removes delivered authority, not durable
            # registration. The real dispatcher must refuse before calling.
            dispatcher = RuntimeInterpreterDispatcher({request.runtime_kind: Interpreter()})
            return dispatcher.execute_runtime(replace(context, runtime_authorities=()), request)

        self.assert_uncertain_cleanup(missing_authority, reason=ConfigurationCleanupReason.NOT_ATTEMPTED)
        self.assertEqual(calls, [])

    def test_interpreter_without_authority_entrypoint_is_known_not_attempted(self):
        calls = []

        class Interpreter:
            def execute(self, request):
                calls.append(request)
                raise AssertionError("registered authority cannot fall back to execute")

        def unsupported_authority(context, request):
            return RuntimeInterpreterDispatcher({request.runtime_kind: Interpreter()}).execute_runtime(context, request)

        self.assert_uncertain_cleanup(unsupported_authority, reason=ConfigurationCleanupReason.NOT_ATTEMPTED)
        self.assertEqual(calls, [])

    def test_inner_interpreter_exception_retains_total_unknown(self):
        calls = []

        class FailingInterpreter:
            def execute(self, request):
                raise AssertionError("registered authority must use execute_with_authority")

            def execute_with_authority(self, request, authority):
                calls.append((request, authority))
                raise RuntimeError("PROVIDER-CANARY")

        def dispatch(context, request):
            return RuntimeInterpreterDispatcher({request.runtime_kind: FailingInterpreter()}).execute_runtime(
                context, request)

        self.assert_uncertain_cleanup(dispatch)
        self.assertEqual(len(calls), 1)

    def test_valid_mixed_total_is_retained_verbatim_without_erasing_known_outcomes(self):
        claimed = self.ready_cleanup(artifact_ids=("settings", "limits"))
        refs = self.plan.plan.activities[0].operation.instances
        self.assertEqual(len(refs), 2)
        expected = ConfigurationCleanupOutcomeSet((
            ConfigurationCleanupOutcome(refs[0], ConfigurationCleanupStatus.REMOVED, None),
            ConfigurationCleanupOutcome(refs[1], ConfigurationCleanupStatus.UNKNOWN,
                ConfigurationCleanupReason.PROVIDER_UNCERTAIN),
        ))
        returned = []

        def mixed(_context, request):
            result = configuration_cleanup_result(request, expected)
            returned.append(result)
            return result

        adapter = RecordingRuntimeAdapter(mixed)
        coordinator = self.coordinator(self.unit_of_work, adapter, "cleanup-execution")
        command = self.execution_command(claimed, "cleanup-execution")
        self.assertIs(coordinator.execute(command).status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(len(adapter.runtime_calls), 1)
        request = adapter.runtime_calls[0][1]
        identity = EffectAttemptIdentity(request.source.run_id, request.activity_id.value, 1)
        with self.unit_of_work() as uow:
            reservation = uow.stores.configuration_cleanup_ownership.get(identity)
            self.assertEqual((len(reservation.members), len(reservation.completions), len(reservation.claims)), (2, 1, 2))
            self.assertEqual(reservation.outcomes, expected)
            attempt = uow.stores.effect_attempts.get(identity)
            outcome = uow.stores.effect_outcomes.get(identity, attempt.latest_transition_event.event_id)
            self.assertEqual(outcome.outcome.result, returned[0])
        before = self.ceiling_truth()
        self.assertIs(coordinator.execute(command).status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual(self.ceiling_truth(), before)

    def test_public_k1_coordinator_preserves_one_complete_transport_ledger(self):
        from control_plane_kit_operations import _configuration_cleanup_ownership as cleanup
        from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead

        claimed = self.ready_cleanup()
        observed = dict(bytes=0, rows=0, largest_cell=0, statements=0, queries=[],
            accounting=None, role_label=lambda _query, _params: None)
        tails = []
        pair_reservations = set()
        test = self

        actual_query = _EvidenceRead.query

        def observe_pair(reader, sql, params, **kwargs):
            is_pair = ("r FULL JOIN " in sql and "cpk_effect_configuration_refs" in sql
                and "cpk_configuration_claims" in sql)
            if is_pair:
                self.assertIs(reader.accounting, observed["accounting"])
                before, offset = _components(reader.used), len(observed["queries"])
            rows = actual_query(reader, sql, params, **kwargs)
            if is_pair:
                entries = observed["queries"][offset:]
                self.assertEqual(len(entries), 1)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][6:8], (None, None))
                disposition = "outstanding" if rows[0][8] else "cleanup-closed"
                # The connection records this peak before execute/fetch; use
                # neither query kwargs nor a copied reservation declaration.
                reservation = tuple(peak - used for peak, used in
                    zip(entries[0]["peak"], before, strict=True))
                pair_reservations.add((disposition, reservation))
            return rows

        class MeasuredUnitOfWork(PostgresUnitOfWork):
            def __exit__(uow, *args):
                super().__exit__(*args)
                for tail in tails:
                    if tail["uow"] is uow:
                        test.assertNotIn("end", tail, "tail interval ended twice")
                        test.assertIs(_ACCOUNTING.get(), tail["accounting"])
                        tail["end"] = tail["accounting"].used
                        tail["query_end"] = len(observed["queries"])

        admit_tail = cleanup._admit_tail

        def measure_tail(owner, future, unmetered):
            self.assertIsInstance(owner.uow, MeasuredUnitOfWork)
            self.assertIs(owner.connection, owner.uow.stores.connection)
            self.assertIs(owner.accounting, observed["accounting"])
            self.assertFalse(any(tail["uow"] is owner.uow for tail in tails))
            # Capture before the real admission precharges raw writes. The
            # interval ends at this exact UoW, excluding later coordinator work.
            phase = "start" if type(owner.issued) is cleanup._PreparedCleanupStart else "fold"
            self.assertIn(type(owner.issued), (cleanup._PreparedCleanupStart, cleanup._PreparedCleanupFold))
            tails.append(dict(phase=phase, uow=owner.uow, accounting=owner.accounting,
                prefix=owner.accounting.used, forecast=future, query_start=len(observed["queries"])))
            return admit_tail(owner, future, unmetered)

        def factory():
            accounting = _ACCOUNTING.get()
            self.assertIsNotNone(accounting, "public coordinator must establish command accounting")
            if observed["accounting"] is None:
                observed["accounting"] = accounting
            self.assertIs(accounting, observed["accounting"], "phase transition reset cumulative accounting")
            return MeasuredUnitOfWork(lambda: _PhaseConnection(psycopg.connect(self.database_url), observed))

        def removed(_context, request):
            return configuration_cleanup_result(request, ConfigurationCleanupOutcomeSet((
                ConfigurationCleanupOutcome(self.selected_ref, ConfigurationCleanupStatus.REMOVED, None),)))

        adapter = RecordingRuntimeAdapter(removed)
        with mock.patch.object(cleanup, "_admit_tail", measure_tail), \
                mock.patch.object(_EvidenceRead, "query", observe_pair):
            result = self.coordinator(factory, adapter, "cleanup-execution").execute(
                self.execution_command(claimed, "cleanup-execution"))
        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(len(adapter.runtime_calls), 1)
        used = observed["accounting"].used
        self.assertGreater(len(observed["queries"]), 0)
        self.assertLessEqual(used.records, 4096)
        self.assertLessEqual(used.accounted_bytes, 16 * 1024 * 1024)
        self.assertGreaterEqual(used.records, observed["rows"])
        self.assertGreaterEqual(used.accounted_bytes, observed["bytes"])
        self.assertEqual(used.statements, observed["statements"])
        self.assertEqual([tail["phase"] for tail in tails], ["start", "fold"])
        tail_metrics = []
        for tail in tails:
            prefix, forecast = _components(tail["prefix"]), _components(tail["forecast"])
            delta = tuple(end - before for end, before in zip(_components(tail["end"]), prefix, strict=True))
            queries = observed["queries"][tail["query_start"]:tail["query_end"]]
            self.assertTrue(queries)
            # Wire widths come from PostgreSQL results, independently of the
            # production forecasting algebra and ledger settlement calculation.
            rows = [widths for query in queries for widths in query["widths"]]
            physical = (len(rows), sum(sum(widths) for widths in rows),
                sum(len(widths) for widths in rows), len(queries))
            self.assertEqual(delta[3], physical[3], tail["phase"])
            for charged, actual in zip(delta, physical, strict=True):
                self.assertGreaterEqual(charged, actual, tail["phase"])
            for actual in (delta, physical, *(tuple(peak - before for peak, before in
                    zip(query["peak"], prefix, strict=True)) for query in queries)):
                for needed, declared in zip(actual, forecast, strict=True):
                    self.assertLessEqual(needed, declared, tail["phase"])
            tail_metrics.append(dict(phase=tail["phase"], prefix=prefix, forecast=forecast,
                settled_delta=delta, physical=physical, query_peak_delta=tuple(
                    max(query["peak"][i] - prefix[i] for query in queries) for i in range(4))))
        peaks = []
        for query in observed["queries"]:
            records, octets, cells, statements = query["peak"]
            peak = octets + 128 * records + 16 * cells + 256 * statements
            self.assertLessEqual(records, 4096)
            self.assertLessEqual(peak, 16 * 1024 * 1024)
            peaks.append(peak)
        print("#1936 public K1 coordinator transport", dict(used=_components(used),
            charged_bytes=used.accounted_bytes, physical_weighted_bytes=observed["bytes"],
            maximum_query_reservation_bytes=max(peaks), statements=observed["statements"]))
        print("#1936 public K1 admitted tails", tail_metrics)
        self.assertEqual({kind for kind, _ in pair_reservations},
            {"outstanding", "cleanup-closed"})
        for disposition, reservation in sorted(pair_reservations):
            for component, needed, declared in zip(
                    ("records", "value_octets", "scalar_markers", "statements"),
                    reservation, _components(cleanup._PAIR), strict=True):
                with self.subTest(disposition=disposition, component=component):
                    self.assertLessEqual(needed, declared)
