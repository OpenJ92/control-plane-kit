"""C-L08/09/10 and C-N02/03/04: actual owner evidence, fixed bounded refusals."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.configuration_invocation import configuration_invocation_selection_fingerprint
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptStatus, RunId
from control_plane_kit_core.planning import NodeTarget, RemoveNodeResource, StartNode
from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead, _Unavailable
from control_plane_kit_operations.postgres.configuration_source import read_source
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from tests.configuration_cleanup_postgres_fixture import (
    ConfigurationCleanupPostgresFixture, command_context, completion_result,
)
from tests.receiver_authoring_context_fixture import ContextObserver
from tests.test_postgres_configuration_evidence import _ObservedConnection


class PostgresConfigurationCleanupEvidenceTests(ConfigurationCleanupPostgresFixture, unittest.TestCase):
    def descriptors(self, result):
        self.assertEqual(result.state, "complete")
        return result.inspection.descriptor()["candidates"]

    def test_full_original_selection_and_terminal_result_keep_all_correlated_evidence(self):
        before = self.truth()
        rows = self.descriptors(self.inspect())
        self.assertEqual(len(rows), len(self.refs))
        self.assertNotIn("configuration-test.invalid", repr(rows))
        self.assertNotIn("simulated-total-configuration-invocation", repr(rows))
        expected = configuration_invocation_selection_fingerprint(self.original.intent.configuration_instances)
        for row in rows:
            self.assertEqual(row["blockers"], [])
            self.assertEqual(len(row["invocations"]), 1)
            witness = row["invocations"][0]
            self.assertEqual((witness["kind"], witness["selection_count"], witness["unselected_count"]),
                             ("completed", len(self.refs), 0))
            self.assertEqual((witness["request_fingerprint"], witness["selection_fingerprint"]),
                             (self.original.request_fingerprint, expected))
            self.assertEqual(witness["original_event_id"], self.original.original_start_event.event_id)
            self.assertEqual(witness["direct_event_id"], self.member.direct_event_id)
        self.assertEqual(self.truth(), before)
        self.assertEqual(self.sampled, [])

    def test_terminal_reader_cache_orders_preserve_b_success_only_law(self):
        self.member.advance()

        def failed(request):
            result = RuntimeEffectResult.failed(request.effect_id,
                RuntimeEffectFailure("test.failure", "simulated failure", {"ordinary": "retained"}))
            completed = completion_result(request)
            return replace(result, evidence=completed.evidence, observations=completed.observations)

        command, _ = self.later_use("failed", producer=failed, expected_status=CoordinatorStatus.FAILED)
        failed_identity = EffectAttemptIdentity(RunId(command.run_id), "activity-failed", 1)
        for identity, expected_status in ((failed_identity, EffectAttemptStatus.FAILED),
                                          (self.original.identity, EffectAttemptStatus.SUCCEEDED)):
            for terminal_first in (False, True):
                with self.subTest(identity=identity, terminal_first=terminal_first), self.unit_of_work() as uow:
                    read = _EvidenceRead(uow.stores.connection, standalone=True)
                    source = read_source(uow.stores.connection, identity, self.refs[0], read=read)
                    self.assertEqual(source.state, "complete")
                    store = uow.stores.effect_outcomes
                    terminal = getattr(store, "_configuration_terminal", None)
                    self.assertTrue(callable(terminal), "#1928 neutral same-original terminal reader is missing")
                    if terminal_first:
                        outcome, attempt = terminal(source.source, read)
                    if expected_status is EffectAttemptStatus.FAILED:
                        with self.assertRaises(_Unavailable):
                            store._configuration_success(source.source, read)
                    else:
                        self.assertIs(store._configuration_success(source.source, read)[1].state.status,
                                      EffectAttemptStatus.SUCCEEDED)
                    outcome, attempt = terminal(source.source, read)
                    self.assertIs(attempt.state.status, expected_status)
                    generic = store.get(identity, attempt.latest_transition_event.event_id)
                    self.assertEqual(generic.outcome.result, outcome.result)
                    self.assertEqual(generic.outcome.result.observations, outcome.result.observations)
                    self.assertEqual(len(outcome.result.observations), 1)
                    self.assertEqual(outcome.result.observations[0].address.value,
                                     "http://configuration-test.invalid:8080")
                    if expected_status is EffectAttemptStatus.FAILED:
                        self.assertEqual(outcome.result.failure.details, {"ordinary": "retained"})
                        with self.assertRaises(_Unavailable):
                            store._configuration_success(source.source, read)

    def test_active_requires_verified_started_and_proven_outcome_absence(self):
        self.member.advance()
        entered, release = threading.Event(), threading.Event()

        def paused(request):
            entered.set()
            if not release.wait(15):
                raise AssertionError("active invocation producer was not released")
            return completion_result(request)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.later_use, "active", producer=paused)
            try:
                self.assertTrue(entered.wait(10), "real owner did not reach adapter")
                query = self.query()
                active_inspection = self.inspect(query)
                rows = self.descriptors(active_inspection)
                for row in rows:
                    self.assertIn("unresolved-invocation", row["blockers"])
                    active = [item for item in row["invocations"] if item["kind"] == "active"]
                    self.assertEqual(len(active), 1)
                    self.assertNotIn("outcome_fingerprint", active[0])
                    self.assertEqual(active[0]["source_identity"]["run_id"], "run-active")
            finally:
                release.set()
            future.result(timeout=15)
        self.assertEqual(self.pins(), query.expected_context)
        completed_inspection = self.inspect(query)
        self.assertNotEqual(completed_inspection.inspection.evidence_digest,
                            active_inspection.inspection.evidence_digest)
        for row in self.descriptors(completed_inspection):
            self.assertNotIn("unresolved-invocation", row["blockers"])
            self.assertEqual({item["kind"] for item in row["invocations"]}, {"completed"})

    def test_corrupt_status_event_source_result_or_present_profile_is_unavailable(self):
        identity = self.original.identity
        key = (identity.run_id.value, identity.activity_id, identity.attempt)
        before = self.truth()
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with self.connection.transaction():
                self.connection.execute("UPDATE cpk_effect_attempt_outcomes SET outcome_fingerprint=%s "
                    "WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", ("a" * 64, *key))
        self.assertEqual(self.truth(), before)
        # Explicit archived no-link fixture: preserve corruption-reader laws
        # without weakening the admitted completion's immutable-parent FK.
        self.assertEqual(self.connection.execute("DELETE FROM cpk_configuration_invocation_completions "
            "WHERE (run_id,activity_id,attempt)=(%s,%s,%s) RETURNING 1", key).fetchone(), (1,))
        cases = (
            ("cpk_effect_attempts", "status", "failed", "run_id='run-config'"),
            ("cpk_effect_attempt_intents", "preimage", b"bad-original-CANARY", "run_id='run-config'"),
            ("cpk_effect_attempt_outcomes", "preimage", b"bad-result-CANARY", "run_id='run-config'"),
            ("cpk_effect_attempt_outcomes", "outcome_fingerprint", "a" * 64, "run_id='run-config'"),
        )
        for table, field, invalid, where in cases:
            original = self.connection.execute(f"SELECT {field} FROM {table} WHERE {where}").fetchone()[0]
            try:
                self.connection.execute(f"UPDATE {table} SET {field}=%s WHERE {where}", (invalid,))
                before = self.truth()
                self.assert_unavailable(self.inspect())
                self.assertEqual(self.truth(), before)
            finally:
                self.connection.execute(f"UPDATE {table} SET {field}=%s WHERE {where}", (original,))
        self.member.advance()
        command, _ = self.later_use("invalid-profile", producer=lambda request:
            replace(completion_result(request), observations=()))
        self.descriptors(self.inspect())
        malformed = self.retain_historical_malformed_completion(
            EffectAttemptIdentity(RunId(command.run_id), "activity-invalid-profile", 1))
        self.assertEqual(malformed.evidence["configuration_invocation_completion"], {"profile": "CANARY"})
        before = self.truth()
        self.assert_unavailable(self.inspect())
        self.assertEqual(self.truth(), before)

    def test_all_current_and_old_claims_protect_without_desired_digest_inference(self):
        self.member.advance()
        self.later_use("unprofiled", producer=lambda request: RuntimeEffectResult.succeeded(request.effect_id))
        with self.unit_of_work() as uow:
            uow.stores.workspaces.set_desired_graph("workspace-a", "graph-current")
            uow.commit()
        before = self.truth()
        for row in self.descriptors(self.inspect()):
            self.assertEqual(row["blockers"], ["current-selected-use", "unresolved-invocation"])
            self.assertEqual({item["source_identity"]["run_id"] for item in row["invocations"]},
                             {"run-config", "run-unprofiled"})
        self.assertEqual(self.truth(), before)

    def test_partial_selection_reports_counts_without_sibling_disclosure_or_expansion(self):
        self.assertGreater(len(self.refs), 1)
        rows = self.descriptors(self.inspect(self.query(self.refs[:1])))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["blockers"], ["incomplete-invocation-selection"])
        self.assertEqual(rows[0]["invocations"][0]["unselected_count"], len(self.refs) - 1)
        for sibling in self.refs[1:]:
            self.assertNotIn(sibling.allocation_id, repr(rows))
            self.assertNotIn(sibling.target_path, repr(rows))
        self.assertTrue(all(not row["blockers"] for row in self.descriptors(self.inspect())))

    def test_shared_completed_invocations_require_every_explicit_full_selection(self):
        self.member.advance()
        self.later_use("second")
        for row in self.descriptors(self.inspect()):
            self.assertEqual(row["blockers"], ["current-selected-use"])
            self.assertEqual(len(row["invocations"]), 2)
            self.assertEqual({item["kind"] for item in row["invocations"]}, {"completed"})
        for row in self.descriptors(self.inspect(self.query(self.refs[:1]))):
            self.assertEqual(row["blockers"], ["current-selected-use", "incomplete-invocation-selection"])
            self.assertTrue(all(item["unselected_count"] == len(self.refs) - 1 for item in row["invocations"]))

    def test_same_material_historical_incarnations_remain_distinct(self):
        self.member.advance()
        operator = self.carry_operator()
        empty = replace(operator.graph, nodes={}, runtimes={"runtime-a": replace(
            operator.graph.runtimes["runtime-a"], children=())})
        departed = operator.prepare("depart-cleanup", "graph-depart-cleanup", RemoveNodeResource(NodeTarget("api")),
                                    graph=empty)
        operator.advance(departed)
        # Selecting the same desired bytes cannot protect the old incarnation.
        with self.unit_of_work() as uow:
            uow.stores.workspaces.set_desired_graph("workspace-a", "graph-configured")
            uow.commit()
        self.assertTrue(all(not row["blockers"] for row in self.descriptors(self.inspect())))
        command = operator.admit("new-birth", "graph-configured", StartNode(NodeTarget("api")),
                                 reuse_selected_desired=True)
        self.execute_later(command, "new-birth")
        identity = EffectAttemptIdentity(RunId(command.run_id), "activity-new-birth", 1)
        with self.unit_of_work() as uow:
            original = uow.stores.effect_attempt_intents.get(identity)
        new_refs = original.intent.configuration_instances.instances
        self.assertEqual({replace(ref, allocation_id="placeholder") for ref in new_refs},
                         {replace(ref, allocation_id="placeholder") for ref in self.refs})
        self.assertTrue({ref.allocation_id for ref in new_refs}.isdisjoint(ref.allocation_id for ref in self.refs))
        old_query, new_query = self.query(), self.query(new_refs, identity=identity)
        combined = replace(old_query, selectors=old_query.selectors + new_query.selectors)
        rows = self.descriptors(self.inspect(combined))
        self.assertEqual(len(rows), len(self.refs) + len(new_refs))
        self.assertTrue(all(not row["blockers"] for row in rows))

    def test_shared_reads_charge_actual_transport_once_and_never_reset_global_budget(self):
        observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)
        seen = []
        actual_query = _EvidenceRead.query

        def record(reader, sql, params, **kwargs):
            result = actual_query(reader, sql, params, **kwargs)
            seen.append((id(reader.accounting), str(sql), reader.used))
            return result

        factory = lambda: PostgresUnitOfWork(lambda: _ObservedConnection(psycopg.connect(self.database_url), observed))
        with mock.patch.object(_EvidenceRead, "query", record):
            result = self.inspect(factory=factory)
        self.assertEqual(result.state, "complete")
        self.assertTrue(seen)
        self.assertEqual(len({row[0] for row in seen}), 1)
        original_reads = [sql for _, sql, _ in seen if "SELECT * FROM cpk_effect_attempt_intents" in sql]
        self.assertEqual(len(original_reads), 1, "the full original selection was fetched once per selected ref")
        footprint = seen[-1][2]
        self.assertLessEqual(footprint.records, 4096)
        self.assertLessEqual(footprint.accounted_bytes, 16 * 1024 * 1024)
        # The observer counts the explicit BEGIN (one statement, zero rows).
        # All evidence SQL and its actual values must be covered by the ledger.
        self.assertEqual(footprint.statements, observed["statements"] - 1)
        self.assertGreaterEqual(footprint.records, observed["rows"])
        self.assertGreaterEqual(footprint.accounted_bytes, observed["bytes"] - 256)
        self.assertGreater(observed["rows"], 0)
        # Explicit fault injection proves global-budget exhaustion is retained
        # across the next helper. It is not a claim of natural producer capacity.
        for exhausted in (ConfigurationEvidenceFootprint(4096, 0, 0, 0),
                          ConfigurationEvidenceFootprint(0, 16 * 1024 * 1024, 0, 0)):
            entered = []

            def exhaust(reader, sql, params, **kwargs):
                if not entered:
                    entered.append(True)
                    reader.used = exhausted
                return actual_query(reader, sql, params, **kwargs)

            before = self.truth()
            with mock.patch.object(_EvidenceRead, "query", exhaust):
                self.assert_unavailable(self.inspect(), "capacity")
            self.assertEqual(self.truth(), before)

    def test_snapshot_is_coherent_read_only_fresh_and_rolls_back_on_all_exits(self):
        original = self.inspect()
        observed = []
        entered, release = threading.Event(), threading.Event()

        def pause():
            entered.set()
            if not release.wait(15):
                raise AssertionError("snapshot was not released")

        def factory():
            observer = ContextObserver(psycopg.connect(self.database_url), pause)
            observed.append(observer)
            return PostgresUnitOfWork(lambda: observer)

        query = self.query()
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(self.inspect, query, factory=factory)
            try:
                self.assertTrue(entered.wait(10))
                pool.submit(self.member.advance).result(timeout=10)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=15), original)
        self.assertEqual(len(observed), 1)
        self.assertEqual(set(observed[0].modes), {("repeatable read", "on")})
        self.assertEqual((observed[0].rollbacks, observed[0].closes), (1, 1))
        self.assertEqual(observed[0].observation_failures, [])
        self.assertNotEqual(self.inspect().inspection.evidence_digest, original.inspection.evidence_digest)
        with self.unit_of_work().configuration_cleanup_snapshot():
            pass
        uow = self.unit_of_work()
        with uow.configuration_cleanup_snapshot():
            pass
        with self.assertRaises(RuntimeError):
            with uow.configuration_cleanup_snapshot():
                self.fail("used snapshot was reentered")
        # Physical read-only enforcement, even if code reaches the connection.
        observer = ContextObserver(psycopg.connect(self.database_url))
        uow = PostgresUnitOfWork(lambda: observer)
        with uow.configuration_cleanup_snapshot():
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                observer.execute("UPDATE cpk_workspaces SET name=name WHERE workspace_id='workspace-a'")
        self.assertEqual((observer.rollbacks, observer.closes), (1, 1))
        # An already-used connection must not smuggle in an older snapshot.
        observer = ContextObserver(psycopg.connect(self.database_url))
        observer.connection.execute("SELECT 1")
        with self.assertRaises(RuntimeError):
            with PostgresUnitOfWork(lambda: observer).configuration_cleanup_snapshot():
                self.fail("used connection was admitted")
        self.assertEqual((observer.rollbacks, observer.closes), (1, 1))

        class RejectMode(ContextObserver):
            def execute(self, query, params=()):
                if "repeatable read" in str(query).lower():
                    raise psycopg.OperationalError("injected mode setup failure")
                return super().execute(query, params)

        observer = RejectMode(psycopg.connect(self.database_url))
        with self.assertRaises(psycopg.OperationalError):
            with PostgresUnitOfWork(lambda: observer).configuration_cleanup_snapshot():
                self.fail("failed transaction setup yielded a facade")
        self.assertEqual((observer.rollbacks, observer.closes), (1, 1))
        # Cleanup-specific evidence decode failure still closes its snapshot.
        self.connection.execute("UPDATE cpk_effect_attempt_outcomes SET preimage=%s WHERE run_id='run-config'",
                                (b"invalid-result",))
        observer = ContextObserver(psycopg.connect(self.database_url))
        self.assert_unavailable(self.inspect(factory=lambda: PostgresUnitOfWork(lambda: observer)))
        self.assertEqual((observer.rollbacks, observer.closes), (1, 1))


class PostgresConfigurationCleanupCapacityTests(ConfigurationCleanupPostgresFixture, unittest.TestCase):
    single_artifact = True

    def test_64_claim_owner_history_is_complete_and_65th_claim_is_capacity(self):
        self.assertEqual(len(self.refs), 1)
        self.member.advance()
        for number in range(1, 64):
            command, _ = self.later_use(f"bounded-{number:02d}")
            # B requires each previous use's genuine original acceptance before
            # admitting another reuse. Completion alone is not that receipt.
            self.carry_operator().advance(command)
        self.assertEqual(len(self.member.protective_claims()), 64)
        before = self.truth()
        result = self.inspect()
        self.assertEqual(result.state, "complete")
        rows = result.inspection.descriptor()["candidates"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["invocations"]), 64)
        self.assertEqual(rows[0]["blockers"], ["current-selected-use"])
        self.assertEqual(self.truth(), before)
        # Defensive 65th sentinel: this deliberately invalid copied index row
        # is reader-boundary evidence, NEVER an additional admitted invocation.
        with self.connection.transaction():
            self.connection.execute("INSERT INTO cpk_effect_configuration_refs SELECT run_id,activity_id,attempt,"
                "'overflow',workspace_id,allocation_id,runtime_id,node_id,ref_preimage,ref_digest,request_fingerprint,"
                "original_event_id,birth_run_id,birth_activity_id,birth_attempt,birth_artifact_id,false "
                "FROM cpk_effect_configuration_refs WHERE run_id='run-config'")
            self.connection.execute("INSERT INTO cpk_configuration_claims SELECT run_id,activity_id,attempt,artifact_id,"
                "workspace_id,allocation_id FROM cpk_effect_configuration_refs WHERE artifact_id='overflow'")
        self.assert_unavailable(self.inspect(), "capacity")


if __name__ == "__main__":
    unittest.main()
