"""#1902 admission and bounded scope history through the owning SQL store."""

from dataclasses import replace
import unittest

import psycopg

from control_plane_kit_core.operations import ExecutionRequestStatus
from control_plane_kit_core.planning import ActivityId, ActivityPlan, NodeTarget, PlannedActivity, StartNode, WaitForHealthy
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.postgres.schema import SchemaInstallationError
from control_plane_kit_operations.records import ExecutionIdempotency, ExecutionRequestRecord
from tests.receiver_execution_scope_fixture import ReceiverExecutionScopeFixture


class PostgresReceiverExecutionScopeTests(ReceiverExecutionScopeFixture, unittest.TestCase):
    def seed_pair_plans(self):
        self.seed_graphs("pair-base", self.empty_graph("pair-base"),
                         "pair-desired", self.graph(nodes=("app", "other")))
        with self.unit_of_work() as uow:
            uow.stores.workspaces.set_current_graph("workspace-a", "pair-base")
            uow.stores.workspaces.set_desired_graph("workspace-a", "pair-desired")
            uow.commit()
        for name, nodes in (("both", ("app", "other")), ("app", ("app",)), ("other", ("other",))):
            plan = ActivityPlan(tuple(PlannedActivity(ActivityId("start-" + node),
                StartNode(NodeTarget(node))) for node in nodes))
            self.seed_plan_truth(plan_id="pair-plan-" + name, approval_request_id="pair-approval-" + name,
                approval_decision_id="pair-decision-" + name, plan=plan,
                base_graph_id="pair-base", desired_graph_id="pair-desired")

    def admit_pair_plan(self, name, suffix):
        with self.unit_of_work() as uow:
            template = uow.stores.activity_history.get_plan("pair-plan-" + name)
        self.seed_plan_truth(plan_id="pair-copy-plan-" + suffix,
            approval_request_id="pair-copy-approval-" + suffix,
            approval_decision_id="pair-copy-decision-" + suffix, plan=template.plan,
            base_graph_id=template.base_graph_id, desired_graph_id=template.desired_graph_id)
        return self.admission_service("pair-request-" + suffix, "pair-action-" + suffix).execute(
            self.command(plan_id="pair-copy-plan-" + suffix, approval_request_id="pair-copy-approval-" + suffix,
                         key="pair-" + suffix))

    def test_known_64_requests_on_later_distinct_prefix_fit_but_new_65th_refuses(self):
        module = self.require_scopes()
        self.seed_pair_plans()
        for index in range(64):
            self.admit_pair_plan("both", str(index))
        scopes = tuple(module.ExecutionReceiverScope("docker", node) for node in ("app", "other"))
        result = self.evidence(*scopes)
        self.assertEqual(result.disposition, "conflict")
        self.assertEqual(set(result.request_ids), {"pair-request-" + str(index) for index in range(64)})
        self.assertEqual(len(result.request_ids), 64)
        # The later node prefix repeats all known IDs before this new identity.
        self.admit_pair_plan("other", "zz-overflow")
        self.assertEqual(self.evidence(*scopes).disposition, "capacity")

    def test_node_point_does_not_consume_unrelated_nodes_on_same_runtime(self):
        module = self.require_scopes()
        self.seed_pair_plans()
        for index in range(65):
            self.admit_pair_plan("other", str(index))
        self.admit_pair_plan("app", "app")
        result = self.evidence(module.ExecutionReceiverScope("docker", "app"))
        self.assertEqual(result.disposition, "conflict")
        self.assertEqual(result.request_ids, ("pair-request-app",))
        self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", None)).disposition, "capacity")

    def test_forged_digest_with_intact_rows_refuses_online_and_current_verification(self):
        module = self.require_scopes()
        self.admit()
        original = self.scope_header()
        forged = "0" * 64 if original[1] != "0" * 64 else "1" * 64
        self.connection.execute("UPDATE cpk_execution_requests SET receiver_scope_digest=%s WHERE request_id='execution-a'", (forged,))
        try:
            rows = self.scope_rows()
            result = self.evidence(module.ExecutionReceiverScope("docker", "app"))
            self.assertEqual(result.disposition, "unavailable")
            with self.assertRaises(SchemaInstallationError):
                install_schema(self.connection)
            self.assertEqual(self.scope_header(), (original[0], forged))
            self.assertEqual(self.scope_rows(), rows)
        finally:
            self.connection.execute("UPDATE cpk_execution_requests SET receiver_scope_digest=%s WHERE request_id='execution-a'", (original[1],))

    def test_current_verifier_detects_an_entire_missing_scope_set_without_backfill(self):
        self.require_catalog()
        self.admit()
        original = self.scope_header(), self.scope_rows()
        with self.unit_of_work() as uow:
            connection = uow.stores.connection
            self.assertEqual(connection.execute("DELETE FROM cpk_execution_receiver_scopes WHERE request_id='execution-a'").rowcount, 1)
            with self.assertRaises(SchemaInstallationError):
                install_schema(connection)
            self.assertEqual(connection.execute("SELECT count(*) FROM cpk_execution_receiver_scopes WHERE request_id='execution-a'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT receiver_scope_count,receiver_scope_digest FROM cpk_execution_requests WHERE request_id='execution-a'").fetchone(), original[0])
        self.assertEqual((self.scope_header(), self.scope_rows()), original)
        # No online reverse-omission discovery is claimed for privileged SQL.

    def test_admission_commits_exact_scope_header_rows_and_real_action(self):
        module = self.require_scopes()
        self.require_catalog()
        admitted = self.admit()
        derived = module.derive_execution_receiver_scopes(*self.source())
        self.assertEqual(self.scope_rows(), [(0, "node", "docker", "app")])
        self.assertEqual(self.scope_header(), (1, derived.source_digest))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.get_request("execution-a"), admitted.request)
            action = uow.stores.activity_history.action_for_idempotency("session-a", "execute-a")
        self.assertEqual(action, admitted.action)
        self.assertEqual(action.payload["execution_request_id"], "execution-a")

    def test_exact_replay_returns_original_witness_without_repair_or_replacement(self):
        self.require_catalog()
        first = self.admit()
        before = self.scope_header(), self.scope_rows()
        replay = self.admission_service("unused", "unused-action").execute(self.command(key="execute-a"))
        self.assertTrue(replay.replayed)
        self.assertEqual((replay.request, replay.action), (first.request, first.action))
        self.assertEqual((self.scope_header(), self.scope_rows()), before)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_requests").fetchone()[0], 1)

    def test_late_action_failure_rolls_back_request_header_and_all_scope_rows(self):
        self.require_catalog()
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.admission_service("execution-a", "action-start").execute(self.command())
        self.assertIsNone(self.scope_header())
        self.assertEqual(self.scope_rows(), [])

    def test_scope_row_failure_rolls_back_request_and_admission_action(self):
        self.require_catalog()
        # Intentional temporary catalog corruption creates a late SQL failure.
        # It is removed even on assertion failure; it is not supported mutation.
        self.connection.execute("ALTER TABLE cpk_execution_receiver_scopes ADD CONSTRAINT test_reject_node CHECK (scope_kind <> 'node')")
        try:
            with self.assertRaises(psycopg.errors.CheckViolation):
                self.admit()
            self.assertIsNone(self.scope_header())
            self.assertEqual(self.scope_rows(), [])
            self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_operation_actions WHERE action_id='action-execute-a'").fetchone()[0], 0)
        finally:
            self.connection.execute("ALTER TABLE cpk_execution_receiver_scopes DROP CONSTRAINT test_reject_node")

    def test_direct_affecting_request_refuses_without_creating_header_or_rows(self):
        module = self.require_scopes()
        identity, _, _, _ = self.source()
        request = ExecutionRequestRecord(identity, ExecutionRequestStatus.QUEUED, "operator-a",
            "2026-07-22T12:04:00Z", "scope-approval-a", "scope-decision-a",
            ExecutionIdempotency("bare", "bare-fingerprint"))
        with self.assertRaises(module.ReceiverScopeUnavailable):
            with self.unit_of_work() as uow:
                uow.stores.execution.add_request(request)
                uow.commit()
        self.assertIsNone(self.scope_header())
        self.assertEqual(self.scope_rows(), [])

    def test_direct_nonaffecting_request_derives_real_empty_header_from_originals(self):
        module = self.require_scopes()
        identity, plan, base, desired = self.source_with_operations(WaitForHealthy(NodeTarget("app")))
        plan = replace(plan, plan_id="observation-plan")
        self.seed_plan_truth(plan_id=plan.plan_id, approval_request_id="observation-approval",
            approval_decision_id="observation-decision", plan=plan.plan)
        identity = replace(identity, plan_id=plan.plan_id)
        request = ExecutionRequestRecord(identity, ExecutionRequestStatus.QUEUED, "operator-a",
            "2026-07-22T12:04:00Z", "observation-approval", "observation-decision",
            ExecutionIdempotency("observation", "observation-fingerprint"))
        with self.unit_of_work() as uow:
            actual_plan = uow.stores.activity_history.get_plan(plan.plan_id)
            self.assertEqual(uow.stores.execution.add_request(request), request)
            uow.commit()
        witness = module.derive_execution_receiver_scopes(identity, actual_plan, base, desired)
        self.assertEqual(self.scope_header(), (0, witness.source_digest))
        self.assertEqual(self.scope_rows(), [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_operation_actions WHERE action_type='admit-execution'").fetchone()[0], 0)

    def test_node_and_runtime_queries_find_queued_legacy_history_without_attempts(self):
        module = self.require_scopes()
        self.admit()
        for scope in (module.ExecutionReceiverScope("docker", "app"), module.ExecutionReceiverScope("docker", None)):
            with self.subTest(scope=scope):
                result = self.evidence(scope)
                self.assertEqual(result.disposition, "conflict")
                self.assertEqual(result.request_ids, ("execution-a",))
                self.assertEqual(result.run_ids, ())
        other = self.evidence(module.ExecutionReceiverScope("docker", "unrelated"))
        self.assertEqual(other.disposition, "nonconflicting")
        self.assertEqual(other.request_ids, ())

    def test_runtime_query_finds_historical_node_after_current_pointers_change(self):
        module = self.require_scopes()
        self.admit()
        with self.unit_of_work() as uow:
            uow.stores.workspaces.set_desired_graph("workspace-a", "graph-current")
            uow.commit()
        result = self.evidence(module.ExecutionReceiverScope("docker", None))
        self.assertEqual(result.disposition, "conflict")
        self.assertEqual(result.request_ids, ("execution-a",))

    def test_encountered_incomplete_witness_refuses_and_current_reentry_is_query_only(self):
        module = self.require_scopes()
        self.admit()
        original = self.scope_header(), self.scope_rows()
        install_schema(self.connection)
        self.assertEqual((self.scope_header(), self.scope_rows()), original)
        # Privileged corruption is intentional; the surviving candidate makes
        # this discoverable online without claiming reverse-omission detection.
        self.connection.execute("UPDATE cpk_execution_requests SET receiver_scope_count=2 WHERE request_id='execution-a'")
        try:
            result = self.evidence(module.ExecutionReceiverScope("docker", "app"))
            self.assertEqual(result.disposition, "unavailable")
            before = self.scope_header(), self.scope_rows()
            with self.assertRaises(SchemaInstallationError):
                install_schema(self.connection)
            self.assertEqual((self.scope_header(), self.scope_rows()), before)
        finally:
            self.connection.execute("UPDATE cpk_execution_requests SET receiver_scope_count=%s WHERE request_id='execution-a'", (original[0][0],))

    def test_64_distinct_candidates_fit_65_refuse_and_duplicate_prefixes_share_budget(self):
        module = self.require_scopes()
        for index in range(64):
            self.admit(str(index))
        scopes = (module.ExecutionReceiverScope("docker", "app"), module.ExecutionReceiverScope("docker", None))
        result = self.evidence(*scopes, *scopes)
        self.assertEqual(result.disposition, "conflict")
        self.assertEqual(len(result.request_ids), 64)
        self.admit("overflow")
        result = self.evidence(*scopes)
        self.assertEqual(result.disposition, "capacity")
        # Current verification has its own per-request budget and exhaustively
        # walks identities; it must not mistake the online64-candidate ceiling
        # for a whole-database verification limit or alter recorded coverage.
        before = self.connection.execute("SELECT request_id,receiver_scope_count,receiver_scope_digest FROM cpk_execution_requests ORDER BY request_id").fetchall()
        install_schema(self.connection)
        self.assertEqual(self.connection.execute("SELECT request_id,receiver_scope_count,receiver_scope_digest FROM cpk_execution_requests ORDER BY request_id").fetchall(), before)
        last_id, count, digest = before[64]
        self.assertEqual(last_id, "execution-overflow")
        forged = "0" * 64 if digest != "0" * 64 else "1" * 64
        self.connection.execute("UPDATE cpk_execution_requests SET receiver_scope_digest=%s WHERE request_id=%s", (forged, last_id))
        try:
            with self.assertRaises(SchemaInstallationError):
                install_schema(self.connection)
            self.assertEqual(self.scope_header(last_id), (count, forged))
        finally:
            self.connection.execute("UPDATE cpk_execution_requests SET receiver_scope_digest=%s WHERE request_id=%s", (digest, last_id))
