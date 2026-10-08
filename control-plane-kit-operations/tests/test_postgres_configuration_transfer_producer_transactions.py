"""Own eligibility and transaction faults on the unmodified transfer writer."""
import unittest
from unittest import mock

import psycopg

from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead, _Capacity
from tests import test_postgres_configuration_transfer_producer as producer


class PostgresConfigurationTransferProducerTransactionTests(unittest.TestCase):
    def member(self, **options):
        fixture = producer.PostgresConfigurationTransferProducerTests()
        self.addCleanup(lambda: self.assertTrue(fixture.doCleanups(), "producer fixture cleanup failed"))
        return fixture.configured_member(**options)

    def snapshot(self, member):
        return member.fixture.retained_snapshot(), tuple(
            (table, member.connection.execute("SELECT * FROM " + table
                + " ORDER BY run_id,activity_id,attempt,artifact_id").fetchall())
            for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims", "cpk_configuration_claim_transfers")), \
            member.connection.execute("SELECT * FROM cpk_configuration_invocation_completions "
                "ORDER BY run_id,activity_id,attempt").fetchall()

    def service(self, member, factory=None, *, replay=False):
        def forbidden():
            self.fail("replay or pre-ID refusal allocated an identity or consulted the clock")
        return CurrentGraphAdvancementCommandService(factory or member.fixture.unit_of_work,
            clock=forbidden if replay else lambda: "2026-07-22T13:05:00Z",
            id_factory=forbidden if replay else iter(("event-advance-config", "action-advance-config")).__next__)

    def test_mixed_invocations_transfer_only_claims_with_their_own_completion(self):
        member = self.member(node_ids=("api", "worker"), profiled_nodes={"worker"})
        with member.fixture.unit_of_work() as uow:
            self.assertIsNone(uow.stores.configuration_completions.get(member.originals["api"].identity))
            self.assertIsNotNone(uow.stores.configuration_completions.get(member.originals["worker"].identity))
        accepted = member.advance()
        self.assertEqual(member.connection.execute("SELECT activity_id,artifact_id,acceptance_revision "
            "FROM cpk_configuration_claim_transfers ORDER BY activity_id,artifact_id").fetchall(),
            [("start-worker", ref.artifact_id, accepted.desired_graph_revision)
                for ref in member.refs if ref.node_id == "worker"])
        expected = [("start-" + ref.node_id, ref.artifact_id, ref.node_id == "api",
            None if ref.node_id == "api" else accepted.desired_graph_revision) for ref in member.refs]
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(member.connection.execute("SELECT activity_id,artifact_id,protective,accepted_revision FROM "
                + table + " ORDER BY activity_id,artifact_id").fetchall(), expected)
        with member.fixture.unit_of_work() as uow:
            current = uow.stores.configuration_acceptance.read_current_configuration("workspace-a")
        self.assertEqual((current.state, current.manifest_slot_count), ("complete", 4))
        self.assertEqual(tuple(binding.ref for binding in current.bindings), member.refs)

    def test_each_publication_write_failure_rolls_back_the_entire_transfer(self):
        member = self.member()
        before, query = self.snapshot(member), _EvidenceRead.query
        prefixes = ("UPDATE cpk_workspaces ", "INSERT INTO cpk_activity_events ",
            "INSERT INTO cpk_operation_actions ", "INSERT INTO cpk_configuration_acceptances ",
            "INSERT INTO cpk_configuration_accepted_slots ", "INSERT INTO cpk_configuration_claim_transfers ",
            "UPDATE cpk_effect_configuration_refs ", "UPDATE cpk_configuration_claims ")
        # CAS, event, action, header, two slots, then two (T/ref/claim) triples.
        expected = (0, 1, 2, 3, 4, 4, 5, 6, 7, 5, 6, 7)
        for fail_at in range(1, len(expected) + 1):
            with self.subTest(write=fail_at):
                visited = []
                def fail(reader, statement, params=(), **options):
                    result = query(reader, statement, params, **options)
                    selected = next((index for index, prefix in enumerate(prefixes) if statement.startswith(prefix)), None)
                    if selected is not None:
                        visited.append(selected)
                        if len(visited) == fail_at:
                            raise RuntimeError("injected after publication write")
                    return result
                with mock.patch.object(_EvidenceRead, "query", fail), \
                        self.assertRaisesRegex(RuntimeError, "^injected after publication write$"):
                    self.service(member).execute(member.command())
                self.assertEqual(tuple(visited), expected[:fail_at])
                self.assertEqual(self.snapshot(member), before)
        self.assertFalse(member.advance().replayed, "the same original intent remains retryable")

    def test_deferred_paired_constraint_failure_rolls_back_all_transfer_truth(self):
        member = self.member()
        before, tentative = self.snapshot(member), []
        class CommitFault:
            def __init__(self, connection):
                self.connection = connection
            def __getattr__(self, name):
                return getattr(self.connection, name)
            def commit(self):
                tentative.append(self.connection.execute("SELECT count(*) FROM cpk_configuration_claim_transfers").fetchone())
                # Deliberately break one deferred reciprocal pair after the
                # real producer/finish. The actual PostgreSQL commit must fail.
                self.connection.execute("UPDATE cpk_configuration_claims SET accepted_revision=NULL "
                    "WHERE run_id='run-config' AND activity_id='start-api' AND artifact_id='limits'")
                self.connection.commit()
        factory = lambda: PostgresUnitOfWork(lambda: CommitFault(psycopg.connect(member.fixture.database_url)))
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.service(member, factory).execute(member.command())
        self.assertEqual(tentative, [(2,)])
        self.assertEqual(self.snapshot(member), before)

    def test_lost_commit_acknowledgement_replays_without_ids_or_new_transfers(self):
        member = self.member()
        class LostAcknowledgement:
            def __init__(self, connection):
                self.connection = connection
            def __getattr__(self, name):
                return getattr(self.connection, name)
            def commit(self):
                self.connection.commit()
                raise ConnectionError("committed acknowledgement lost")
        factory = lambda: PostgresUnitOfWork(lambda: LostAcknowledgement(psycopg.connect(member.fixture.database_url)))
        with self.assertRaisesRegex(ConnectionError, "^committed acknowledgement lost$"):
            self.service(member, factory).execute(member.command())
        committed = self.snapshot(member)
        self.assertEqual(member.connection.execute("SELECT count(*) FROM cpk_configuration_claim_transfers").fetchone(), (2,))
        replay = self.service(member, replay=True).execute(member.command())
        self.assertTrue(replay.replayed)
        self.assertEqual((replay.action.action_id, replay.event.event_id), ("action-advance-config", "event-advance-config"))
        self.assertEqual(self.snapshot(member), committed)

    def test_actual_prefix_evidence_exhaustion_refuses_before_clock_and_ids(self):
        member = self.member()
        before, reached, rejections = self.snapshot(member), [], []
        preflight = ConfigurationAcceptanceStore._preflight
        def admit(store, prepared):
            self.assertIsNone(prepared.event)
            _, _, remaining = store._publication_budgets(prepared)
            extra = 4096 - prepared.evidence_read.used.records - remaining.peak.records + 1
            self.assertGreater(extra, 0)
            # Transport actual harmless scalar evidence into the same ledger;
            # do not edit accounting counters or fabricate a prior footprint.
            prepared.evidence_read.query("SELECT 1 FROM generate_series(1,%s)", (extra,),
                records=extra, octets=extra, cells=1)
            reached.append(prepared.evidence_read.used.records)
            try:
                return preflight(store, prepared)
            except _Capacity:
                rejections.append("actual remaining-budget gate")
                raise
        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", admit), self.assertRaises(CurrentGraphAdvancementConflict):
            self.service(member, replay=True).execute(member.command())
        self.assertEqual(len(reached), 1)
        self.assertEqual(rejections, ["actual remaining-budget gate"])
        self.assertEqual(self.snapshot(member), before)
