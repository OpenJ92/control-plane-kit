"""Recorded cleanup-closed premises at actual transfer publication readback."""
import unittest
from unittest import mock

import psycopg
from psycopg import sql

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.planning import RuntimeTarget, StartRuntime
from control_plane_kit_core.topology import compile_topology
from control_plane_kit_operations.advancement import (
    CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict,
)
from control_plane_kit_operations import advancement as advancement_module
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead, _Unavailable
from control_plane_kit_operations.postgres.configuration_preparation_store import _paired_disposition
from tests.configuration_transfer_fixture import ConfigurationTransferredConsumerFixture


_RESERVATION_FKS = (
    ("cpk_configuration_cleanup_members", "cpk_cleanup_members_reservation_fk"),
    ("cpk_configuration_invocation_closures", "cpk_invocation_closures_reservation_fk"),
)
_CLOSURES = (
    ("cpk_configuration_cleanup_members", "cleanup_run_id,cleanup_activity_id,cleanup_attempt,allocation_id"),
    ("cpk_configuration_invocation_closures", "run_id,activity_id,attempt"),
    ("cpk_configuration_claim_closures", "run_id,activity_id,attempt,artifact_id"),
)


class PostgresConfigurationTransferReadbackTests(ConfigurationTransferredConsumerFixture, unittest.TestCase):
    def setUp(self):
        self.constraint_definitions = None
        # LIFO: the existing nested fixture first removes its data and closes
        # its connection. Restore exact FKs last, even if expected red commits.
        self.addCleanup(self.restore_constraints)
        super().setUp()
        self.constraint_definitions = self.constraints(self.connection)
        self.assertTrue(all(definition is not None for definition in self.constraint_definitions))

    def constraints(self, connection):
        return tuple(connection.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid=%s::regclass AND conname=%s", (table, name)).fetchone()
            for table, name in _RESERVATION_FKS)

    def restore_constraints(self):
        if self.constraint_definitions is None:
            return
        with psycopg.connect(self.base.database_url, autocommit=True) as connection:
            for table, _ in _CLOSURES:
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone(), (0,))
            for (table, name), original, current in zip(_RESERVATION_FKS,
                    self.constraint_definitions, self.constraints(connection), strict=True):
                if current is None:
                    connection.execute(sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} ").format(
                        sql.Identifier(table), sql.Identifier(name)) + sql.SQL(original[0]))
            self.assertEqual(self.constraints(connection), self.constraint_definitions)

    def snapshot(self):
        return self.proof_snapshot(), tuple(self.connection.execute(
            f"SELECT * FROM {table} ORDER BY {order}").fetchall() for table, order in _CLOSURES), \
            self.constraints(self.connection)

    def record_closed(self, connection):
        """Defensive-reader premise, never a lawful cleanup/transfer producer.

        Only reservation FKs are relaxed in this disposable test transaction.
        All exact completion/ref/member/claim anchors and checks remain intact.
        """
        ref, key = self.refs[0], self.key(self.refs[0])
        cleanup = ("recorded-readback-cleanup", "recorded-cleanup", 1)
        for table, name in _RESERVATION_FKS:
            connection.execute(sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                sql.Identifier(table), sql.Identifier(name)))
        inserted = connection.execute("INSERT INTO cpk_configuration_cleanup_members "
            "(cleanup_run_id,cleanup_activity_id,cleanup_attempt,workspace_id,allocation_id,"
            "birth_run_id,birth_activity_id,birth_attempt,birth_artifact_id,full_ref_digest) "
            "SELECT %s,%s,%s,workspace_id,allocation_id,run_id,activity_id,attempt,artifact_id,ref_digest "
            "FROM cpk_effect_configuration_refs WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)",
            (*cleanup, *key))
        self.assertEqual(inserted.rowcount, 1)
        inserted = connection.execute("INSERT INTO cpk_configuration_invocation_closures "
            "(run_id,activity_id,attempt,cleanup_run_id,cleanup_activity_id,cleanup_attempt,workspace_id,"
            "request_fingerprint,selection_fingerprint,outcome_fingerprint) "
            "SELECT run_id,activity_id,attempt,%s,%s,%s,workspace_id,request_fingerprint,"
            "selection_fingerprint,outcome_fingerprint FROM cpk_configuration_invocation_completions "
            "WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", (*cleanup, *key[:3]))
        self.assertEqual(inserted.rowcount, 1)
        inserted = connection.execute("INSERT INTO cpk_configuration_claim_closures "
            "(run_id,activity_id,attempt,artifact_id,cleanup_run_id,cleanup_activity_id,cleanup_attempt,"
            "workspace_id,allocation_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (*key, *cleanup, ref.workspace_id, ref.allocation_id))
        self.assertEqual(inserted.rowcount, 1)
        # A closed premise cannot retain an accepted-current assertion at the
        # old revision: both reverse transfer FKs remain enforced at commit.
        removed = connection.execute("DELETE FROM cpk_configuration_claim_transfers WHERE "
            "(run_id,activity_id,attempt,artifact_id,workspace_id,allocation_id,acceptance_revision)="
            "(%s,%s,%s,%s,%s,%s,%s)", (*key, ref.workspace_id, ref.allocation_id, self.revision))
        self.assertEqual(removed.rowcount, 1)
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            changed = connection.execute(f"UPDATE {table} SET accepted_revision=NULL,cleanup_run_id=%s,"
                "cleanup_activity_id=%s,cleanup_attempt=%s "
                "WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", (*cleanup, *key))
            self.assertEqual(changed.rowcount, 1)
        # A new reader performs the real reciprocal pair and closure-anchor
        # queries. Missing/corrupt anchors cannot earn the refusal assertion.
        disposition = _paired_disposition(_EvidenceRead(connection, standalone=True), key, ref)
        self.assertEqual(disposition.kind, "cleanup-closed")

    def late_closed_readback(self, *, reused):
        if reused:
            command = self.reuse.execute_reuse()[0]
        else:
            extra = compile_topology(DeploymentTopology("extra", DockerRuntime(runtime_id="runtime-b")))
            command = self.carry.prepare("closed-readback", "graph-closed-readback",
                StartRuntime(RuntimeTarget("runtime-b")),
                graph=self.carry.graph.add_runtime(extra.runtimes["runtime-b"]))
        before, prepared_values, injected = self.snapshot(), [], []
        refused, finished = [], []
        preflight, receipt = ConfigurationAcceptanceStore._preflight, ConfigurationAcceptanceStore._receipt
        prove_use, finish = ConfigurationAcceptanceStore._prove_use, advancement_module._finish_receiver_advancement

        def prepared(store, value):
            result = preflight(store, value)
            prepared_values.append(value)
            return result

        def readback(store, workspace, revision, *, read=None):
            if (prepared_values and not injected and read is prepared_values[0].evidence_read
                    and revision == prepared_values[0].plan.desired_graph_revision):
                value = prepared_values[0]
                self.assertTrue(store._publication_published)
                slot = next(row for row in value.slots if row[7:11] == self.key(self.refs[0]))
                self.assertEqual(slot[3:7] != slot[7:11], reused)
                connection = store._connection
                self.assertEqual(connection.execute("SELECT action_id,event_id,slot_count FROM "
                    "cpk_configuration_acceptances WHERE workspace_id=%s AND pinned_revision=%s",
                    (workspace, revision)).fetchone(), (value.action.action_id, value.event.event_id, len(value.slots)))
                self.assertEqual(connection.execute("SELECT count(*) FROM cpk_configuration_accepted_slots "
                    "WHERE workspace_id=%s AND pinned_revision=%s", (workspace, revision)).fetchone(),
                    (len(value.slots),))
                self.assertEqual(connection.execute("SELECT 1 FROM cpk_operation_actions WHERE action_id=%s",
                    (value.action.action_id,)).fetchone(), (1,))
                self.assertEqual(connection.execute("SELECT 1 FROM cpk_activity_events WHERE event_id=%s",
                    (value.event.event_id,)).fetchone(), (1,))
                self.record_closed(connection)
                injected.append(slot)
            return receipt(store, workspace, revision, read=read)

        def observed_use(store, read, row, plan, request, run):
            target = (injected and prepared_values and read is prepared_values[0].evidence_read
                and row == injected[0])
            try:
                return prove_use(store, read, row, plan, request, run)
            except _Unavailable:
                if target:
                    refused.append(row)
                raise

        def observed_finish(*args, **kwargs):
            finished.append(True)
            return finish(*args, **kwargs)

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", prepared), \
                mock.patch.object(ConfigurationAcceptanceStore, "_receipt", readback), \
                mock.patch.object(ConfigurationAcceptanceStore, "_prove_use", observed_use), \
                mock.patch.object(advancement_module, "_finish_receiver_advancement", observed_finish), \
                self.assertRaises(CurrentGraphAdvancementConflict):
            CurrentGraphAdvancementCommandService(self.base.unit_of_work,
                clock=lambda: "2026-07-22T13:05:00Z",
                id_factory=iter(("event-closed-readback", "action-closed-readback")).__next__).execute(command)
        self.assertEqual(len(injected), 1)
        self.assertEqual(refused, injected, "the real selected readback proof must propagate refusal")
        self.assertEqual(finished, [], "refusal must precede receiver finish")
        # This precedes all teardown: the real command must undo both tentative
        # publication and the fault, including DDL, in its own transaction.
        self.assertEqual(self.snapshot(), before)

    def test_late_closed_carried_source_refuses_and_rolls_back(self):
        self.late_closed_readback(reused=False)

    def test_late_closed_distinct_birth_refuses_and_rolls_back(self):
        self.late_closed_readback(reused=True)

    def test_unbound_closed_history_and_completion_sibling_remain_readable(self):
        before = self.snapshot()
        with self.base.unit_of_work() as uow:
            self.record_closed(uow.stores.connection)
            current = uow.stores.configuration_acceptance.read_current_configuration("workspace-a", node_id="api")
            self.assertEqual(current.state, "complete")
            self.assertEqual(tuple(binding.ref for binding in current.bindings), self.refs)
            self.assertEqual(uow.stores.configuration_completions.get(self.original.identity), self.completion)
            self.assertEqual(self.prove_transfer(uow, self.refs[1]).acceptance_revision, self.revision)
        self.assertEqual(self.snapshot(), before)

    def selected_guard_rollback(self, *, reused):
        if reused:
            command = self.reuse.execute_reuse()[0]
        else:
            extra = compile_topology(DeploymentTopology("extra", DockerRuntime(runtime_id="runtime-b")))
            command = self.carry.prepare("guard-rollback", "graph-guard-rollback",
                StartRuntime(RuntimeTarget("runtime-b")),
                graph=self.carry.graph.add_runtime(extra.runtimes["runtime-b"]))
        before = self.snapshot()
        actual = ConfigurationAcceptanceStore._require_current
        finish = advancement_module._finish_receiver_advancement
        for selected in range(1, 5):
            for fault in ("pair", "exclusion"):
                with self.subTest(entrance=selected, fault=fault, distinct_birth=reused):
                    calls, injected, refused, finished = [], [], [], []

                    def guard(store, value):
                        calls.append(len(calls) + 1)
                        entrance = calls[-1]
                        if entrance != selected:
                            return actual(store, value)
                        self.assertIsNotNone(value.read_bounds)
                        self.assertFalse(store._publication_published)
                        slot = next(row for row in value.slots if row[7:11] == self.key(self.refs[0]))
                        self.assertEqual(slot[3:7] != slot[7:11], reused)
                        connection = store._connection
                        pointer = connection.execute("SELECT current_graph_id,current_realized_projection_id "
                            "FROM cpk_workspaces WHERE workspace_id=%s", (value.workspace.workspace_id,)).fetchone()
                        expected = ((value.workspace.current_graph_id, value.workspace.current_realized_projection_id)
                            if entrance == 1 else (value.plan.desired_graph_id, value.desired_projection.projection_id))
                        self.assertEqual(pointer, expected)
                        self.assertEqual(connection.execute("SELECT count(*) FROM cpk_activity_events WHERE event_id=%s",
                            (value.event.event_id,)).fetchone(), (int(entrance >= 3),))
                        self.assertEqual(connection.execute("SELECT count(*) FROM cpk_operation_actions WHERE action_id=%s",
                            (value.action.action_id,)).fetchone(), (int(entrance >= 4),))
                        for table in ("cpk_configuration_acceptances", "cpk_configuration_accepted_slots"):
                            self.assertEqual(connection.execute(f"SELECT count(*) FROM {table} "
                                "WHERE workspace_id=%s AND pinned_revision=%s",
                                (value.workspace.workspace_id, value.plan.desired_graph_revision)).fetchone(), (0,))
                        injected.append(entrance)
                        try:
                            if fault == "exclusion":
                                # Restore only this recorded local premise on
                                # unwind. The actual refusal must still escape
                                # to the command and roll back earlier writes.
                                with self.recorded_exclusion(connection):
                                    return actual(store, value)
                            changed = connection.execute("UPDATE cpk_configuration_claims SET accepted_revision=NULL "
                                "WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", self.key(self.refs[0]))
                            self.assertEqual(changed.rowcount, 1)
                            return actual(store, value)
                        except _Unavailable:
                            refused.append(entrance)
                            raise

                    def observed_finish(*args, **kwargs):
                        finished.append(True)
                        return finish(*args, **kwargs)

                    with mock.patch.object(ConfigurationAcceptanceStore, "_require_current", guard), \
                            mock.patch.object(advancement_module, "_finish_receiver_advancement", observed_finish), \
                            self.assertRaises(CurrentGraphAdvancementConflict):
                        CurrentGraphAdvancementCommandService(self.base.unit_of_work,
                            clock=lambda: "2026-07-22T13:05:00Z",
                            id_factory=iter(("event-guard-rollback", "action-guard-rollback")).__next__).execute(command)
                    self.assertEqual(calls, list(range(1, selected + 1)))
                    self.assertEqual(injected, [selected])
                    self.assertEqual(refused, [selected], "the real selected guard must propagate refusal")
                    self.assertEqual(finished, [], "selected guard refusal must precede receiver finish")
                    self.assertEqual(self.snapshot(), before, "the command must roll back before fixture cleanup")

    def test_each_carried_source_guard_refuses_and_rolls_back(self):
        self.selected_guard_rollback(reused=False)

    def test_each_distinct_birth_guard_refuses_and_rolls_back(self):
        self.selected_guard_rollback(reused=True)
