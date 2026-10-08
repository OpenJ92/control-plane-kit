"""B2 execution laws; recorded transfers do not establish a reachable producer."""
import unittest
from dataclasses import replace
from unittest import mock

from control_plane_kit_core.configuration_instances import (
    ConfigurationCleanupOutcome, ConfigurationCleanupOutcomeSet, ConfigurationCleanupStatus,
)
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptStatus, RunId
from control_plane_kit_core.runtime_effects import configuration_cleanup_result
from control_plane_kit_operations.admission import ExecutionAdmissionConflict
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.effect_attempt_fold import ExistingFold, NewlyFolded, EffectAttemptFoldConflict
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_start import ExistingAttempt, NewlyStarted, EffectAttemptStartConflict
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile as Profile
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.records import OperationsRecordError
from tests.configuration_cleanup_execution_fixture import ConfigurationCleanupExecutionFixture
from tests.configuration_cleanup_postgres_fixture import command_context
from tests.configuration_cleanup_read_ceilings_fixture import ConfigurationCleanupReadCeilingsFixture
from tests.configuration_transfer_fixture import ConfigurationTransferFixture
from tests.postgres_effect_attempt_coordinator_fixture import RecordingRuntimeAdapter
from tests.test_execution_admission import Sequence
from tests import test_postgres_configuration_cleanup_execution as execution
from tests import test_postgres_configuration_cleanup_history as history
from tests import test_postgres_configuration_cleanup_transactions as transactions


class _V2ExecutionFixture(ConfigurationCleanupExecutionFixture):
    cleanup_profile = Profile.CONFIGURATION_CLEANUP_V2
    removed_fold = transactions.PostgresConfigurationCleanupTransactionTests.removed_fold
    fault_factory = transactions.PostgresConfigurationCleanupTransactionTests.fault_factory
    assert_no_ids = transactions.PostgresConfigurationCleanupTransactionTests.assert_no_ids
    no_ids = history.PostgresConfigurationCleanupHistoryTests.no_ids

    def admit_cleanup(self, suffix="cleanup-execution"):
        self.assertIs(self.plan.derivation_profile, Profile.CONFIGURATION_CLEANUP_V2)
        try:
            return super().admit_cleanup(suffix)
        except ExecutionAdmissionConflict:
            self.fail("released v2 public cleanup admission is unavailable")


class ConfigurationCleanupV2ExecutionTests(_V2ExecutionFixture, unittest.TestCase):
    # Isomorphic public laws: same genuine receiver/D1 chronology and assertions,
    # with only the explicitly requested cleanup profile changed to v2.
    test_public_start_fold_and_no_dispatch_replay = (
        execution.PostgresConfigurationCleanupExecutionTests.
        test_public_k1_admission_lifecycle_start_fold_and_no_dispatch_replay)
    test_mixed_results_conserve_every_physical_candidate = (
        execution.PostgresConfigurationCleanupExecutionTests.
        test_valid_mixed_total_is_retained_verbatim_without_erasing_known_outcomes)
    test_shared_ledger_covers_physical_transport_and_every_tail_peak = (
        execution.PostgresConfigurationCleanupExecutionTests.
        test_public_k1_coordinator_preserves_one_complete_transport_ledger)
    test_start_writes_and_commit_are_atomic = (
        transactions.PostgresConfigurationCleanupTransactionTests.
        test_each_start_write_and_confirmed_commit_fault_rolls_back_whole_reservation)
    test_fold_writes_and_commit_are_atomic = (
        transactions.PostgresConfigurationCleanupTransactionTests.
        test_each_fold_write_and_confirmed_commit_fault_rolls_back_outcome_cas_and_members)
    test_stale_fence_and_expiry_refuse_before_ids = (
        transactions.PostgresConfigurationCleanupTransactionTests.
        test_stale_fence_and_expired_lease_refuse_before_ids_and_reservation)
    test_foreign_approval_fails_final_start_verification = (
        transactions.PostgresConfigurationCleanupTransactionTests.
        test_final_start_proof_refuses_valid_foreign_approval_before_payload_fetch)
    test_changed_result_fails_final_fold_verification = (
        transactions.PostgresConfigurationCleanupTransactionTests.
        test_final_fold_proof_refusal_rolls_back_event_outcome_cas_and_member_result)
    test_preflight_preserves_prefix_and_refuses_before_ids = (
        transactions.PostgresConfigurationCleanupTransactionTests.
        test_start_and_fold_preflight_preserve_prefix_and_refuse_before_event_ids)
    test_retained_fold_uses_original_authority_after_revocation = (
        history.PostgresConfigurationCleanupHistoryTests.
        test_dispatched_result_retains_original_after_authority_revocation)


class ConfigurationCleanupV2RecordedTransferExecutionTests(_V2ExecutionFixture, unittest.TestCase):
    require_transfer_schema = ConfigurationTransferFixture.require_transfer_schema
    key = ConfigurationTransferFixture.key
    transfer_snapshot = ConfigurationTransferFixture.transfer_snapshot
    reconciliation = history.PostgresConfigurationCleanupHistoryTests.reconciliation

    def ready_cleanup(self, suffix="cleanup-execution", *, artifact_ids=("settings",)):
        self.prepare_transfers(artifact_ids=artifact_ids)
        self.admit_cleanup(suffix)
        return self.ready_run(suffix)

    def test_zero_claim_transport_accounts_for_transfer_proofs_and_empty_sentinels(self):
        self.expect_transferred_pairs = True
        (execution.PostgresConfigurationCleanupExecutionTests.
            test_public_k1_coordinator_preserves_one_complete_transport_ledger)(self)

    def test_generic_observed_zero_claim_success_preserves_transfer_and_exclusion(self):
        self.expected_cleanup_counts = (1, 0, 0)
        (history.PostgresConfigurationCleanupHistoryTests.
            test_generic_observed_success_retains_exclusion_without_member_retirement)(self)
        row = self.connection.execute(
            "SELECT cleanup_run_id,cleanup_activity_id,cleanup_attempt "
            "FROM cpk_configuration_cleanup_reservations").fetchall()
        self.assertEqual(len(row), 1)
        identity = EffectAttemptIdentity(RunId(row[0][0]), row[0][1], row[0][2])
        record = self.assert_reservation(identity, candidates=self.refs, claims=(), completions=(),
            transferred=self.refs, status=EffectAttemptStatus.SUCCEEDED)
        self.assertIsNone(record.outcomes)
        from control_plane_kit_operations.configuration_cleanup_planning import ConfigurationCleanupPlanningService
        inspection = ConfigurationCleanupPlanningService(self.unit_of_work,
            clock=self.no_ids, id_factory=self.no_ids).inspect(self.cleanup_query, context=command_context())
        self.assertEqual(inspection.state, "unavailable")

    def prepare_transfers(self, *, artifact_ids=("settings",), transferred=None, candidates=None):
        self.transferred_artifacts = artifact_ids if transferred is None else transferred
        self.candidate_artifacts = artifact_ids if candidates is None else candidates
        self.prepare_ceiling_premise(recorded_cleanup=False, artifact_ids=artifact_ids)

    def _publish_cleanup(self, runtime, *, distinct_pins, recorded_cleanup):
        # The complete real chronology has already accepted the original D1 and
        # departed it. B1's existing suffix records that historical transfer; it
        # neither manufactures approval nor represents C's future producer.
        self.original = self.configuration_source
        self.refs = self.selected_refs
        self.revision = self.configuration_acceptance.desired_graph_revision
        selected = tuple(ref for ref in self.refs if ref.artifact_id in self.transferred_artifacts)
        ConfigurationTransferFixture.record_transfer(self, selected, unit_of_work=self.unit_of_work)
        self.recorded_transfers = self.transfer_snapshot()
        self.cleanup_runtime = runtime
        candidates = tuple(ref for ref in self.refs if ref.artifact_id in self.candidate_artifacts)
        ConfigurationCleanupReadCeilingsFixture._publish_cleanup(self, runtime,
            distinct_pins=distinct_pins, recorded_cleanup=recorded_cleanup, refs=candidates)

    def assert_reservation(self, identity, *, candidates, claims, completions, transferred, status):
        with self.unit_of_work() as uow:
            record = uow.stores.configuration_cleanup_ownership.get(identity)
        self.assertIs(record.derivation_profile, Profile.CONFIGURATION_CLEANUP_V2)
        self.assertIs(record.status, status)
        self.assertEqual({value.ref for value in record.members}, set(candidates))
        self.assertEqual({value.ref for value in record.claims}, set(claims))
        self.assertEqual(record.completions, completions)
        self.assertEqual({value.ref for value in record.accepted_transfers}, set(transferred))
        self.assertEqual(self.transfer_snapshot()[0], self.recorded_transfers[0])
        return record

    def run_removed(self, suffix):
        self.admit_cleanup(suffix)
        claimed = self.ready_run(suffix)
        candidates = self.plan.plan.activities[0].operation.instances
        outcomes = ConfigurationCleanupOutcomeSet(tuple(ConfigurationCleanupOutcome(
            ref, ConfigurationCleanupStatus.REMOVED, None) for ref in candidates))
        adapter = RecordingRuntimeAdapter(lambda _context, request: configuration_cleanup_result(request, outcomes))
        coordinator = self.coordinator(self.unit_of_work, adapter, suffix)
        command = self.execution_command(claimed, suffix)
        self.assertIs(coordinator.execute(command).status, CoordinatorStatus.COMPLETED)
        self.assertEqual(len(adapter.runtime_calls), 1)
        request = adapter.runtime_calls[0][1]
        self.assertEqual(request.operation.instances, candidates)
        identity = EffectAttemptIdentity(RunId(claimed.run.run_id), request.activity_id.value, 1)
        before = self.ceiling_truth(), self.transfer_snapshot()
        self.assertIs(coordinator.execute(command).status, CoordinatorStatus.COMPLETED)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual((self.ceiling_truth(), self.transfer_snapshot()), before)
        return identity

    def test_zero_claim_native_start_fold_and_replay_require_positive_original_transfer(self):
        from control_plane_kit_operations.postgres.configuration_cleanup_ownership_store import ConfigurationCleanupOwnershipStore
        from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead
        self.prepare_transfers()
        self.admit_cleanup()
        claimed = self.ready_run("cleanup-execution")
        fresh, final, active = [], [], []
        actual_fresh = ConfigurationCleanupOwnershipStore._fresh_start
        actual_get = ConfigurationCleanupOwnershipStore._get
        actual_query = _EvidenceRead.query

        def proof(measurements, invoke):
            count = [0]
            measurements.append(count)
            active.append(count)
            try:
                return invoke()
            finally:
                active.pop()

        def fresh_proof(store, prepared):
            return proof(fresh, lambda: actual_fresh(store, prepared))

        def retained_proof(store, identity, read, **kwargs):
            if kwargs.get("expected") is not None:
                return proof(final, lambda: actual_get(store, identity, read, **kwargs))
            return actual_get(store, identity, read, **kwargs)

        def query(reader, sql, params, **kwargs):
            if active and "r FULL JOIN " in sql and "cpk_configuration_claims" in sql:
                active[-1][0] += 1
            return actual_query(reader, sql, params, **kwargs)

        with mock.patch.object(ConfigurationCleanupOwnershipStore, "_fresh_start", fresh_proof), \
                mock.patch.object(ConfigurationCleanupOwnershipStore, "_get", retained_proof), \
                mock.patch.object(_EvidenceRead, "query", query):
            command, started = self.start_cleanup(claimed)
        self.assertIs(type(started), NewlyStarted)
        # One initial fresh proof and all three issued checks must actually
        # re-read paired truth, including both equal seed/birth visits. T memo
        # hits cannot turn later passes or final verification into permission.
        self.assertEqual(len(fresh), 4)
        self.assertTrue(all(count[0] >= 6 for count in fresh), fresh)
        self.assertEqual(len({count[0] for count in fresh}), 1)
        self.assertEqual(len(final), 1)
        self.assertGreaterEqual(final[0][0], 3)
        identity = started.attempt.state.identity
        record = self.assert_reservation(identity, candidates=self.refs, claims=(), completions=(),
            transferred=self.refs, status=EffectAttemptStatus.STARTED)
        self.assertEqual((record.approval_request_id, record.approval_decision_id),
            (self.approval.request_id, self.decision.decision_id))
        for table in ("cpk_configuration_invocation_closures", "cpk_configuration_claim_closures"):
            self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {table}").fetchone(), (0,))
        # Even zero-use retained history needs positive T. The corruption lives
        # only in the reader's transaction and is rolled back on scope exit.
        before = self.ceiling_truth(), self.transfer_snapshot()
        with self.unit_of_work() as uow:
            removed = uow.stores.connection.execute("DELETE FROM cpk_configuration_claim_transfers "
                "WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", self.key(self.refs[0]))
            self.assertEqual(removed.rowcount, 1)
            with self.assertRaises(OperationsRecordError):
                uow.stores.configuration_cleanup_ownership.get(identity)
        self.assertEqual((self.ceiling_truth(), self.transfer_snapshot()), before)
        # A candidate-local transfer value is not itself authorization. Even
        # if a lower reader supplies an extra such value, the retained owner
        # must require exactly the approved seed/birth/S proof set.
        actual_transfers = ConfigurationCleanupOwnershipStore._transfer_proofs
        extra = replace(record.accepted_transfers[0],
            identity=EffectAttemptIdentity(RunId("unrelated-seed"), "unrelated-activity", 1))
        def unrelated(store, plan, read):
            return (*actual_transfers(store, plan, read), extra)
        with mock.patch.object(ConfigurationCleanupOwnershipStore, "_transfer_proofs", unrelated):
            with self.unit_of_work() as uow, self.assertRaises(OperationsRecordError):
                uow.stores.configuration_cleanup_ownership.get(identity)
        self.assertEqual((self.ceiling_truth(), self.transfer_snapshot()), before)
        self.connection.execute("UPDATE cpk_runtime_authorities SET status='revoked' WHERE registration_id=%s",
            (self.registration.registration_id,))
        fold = self.removed_fold(command, started)
        self.assertIs(type(EffectAttemptFoldService(self.unit_of_work,
            id_factory=Sequence("cleanup-terminal")).execute(fold)), NewlyFolded)
        self.assert_reservation(identity, candidates=self.refs, claims=(), completions=(),
            transferred=self.refs, status=EffectAttemptStatus.SUCCEEDED)
        before = self.ceiling_truth(), self.transfer_snapshot()
        self.assertIs(type(EffectAttemptStartService(self.unit_of_work,
            id_factory=self.assert_no_ids).execute(command)), ExistingAttempt)
        self.assertIs(type(EffectAttemptFoldService(self.unit_of_work,
            id_factory=self.assert_no_ids).execute(fold)), ExistingFold)
        self.assertEqual((self.ceiling_truth(), self.transfer_snapshot()), before)

    def test_active_zero_row_consumers_and_transfer_parents_cannot_fall_back_when_missing(self):
        from control_plane_kit_operations.postgres import configuration_cleanup_phase_read_bounds as bounds
        from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead
        self.prepare_transfers()
        self.admit_cleanup()
        claimed = self.ready_run("cleanup-execution")
        command = self.native_start_command(claimed, "cleanup-execution")
        actual_entries = bounds._entries
        actual_bound = bounds._bound
        actual_require = bounds._phase_require
        actual_query = _EvidenceRead.query
        original = self.original.identity
        identities = {
            "outstanding-allocation-refs": (self.selected_ref.workspace_id, self.selected_ref.allocation_id),
            "outstanding-allocation-claims": (self.selected_ref.workspace_id, self.selected_ref.allocation_id),
            "invocation-refs": (original.run_id.value, original.activity_id, original.attempt),
            "header": (self.selected_ref.workspace_id, self.revision),
            "receipt-action": (self.configuration_acceptance.action.action_id,),
            "receipt-event": (self.configuration_acceptance.event.event_id,),
        }

        def missing(role, invoke, conflict):
            touched, consumer, native_reads = [], [], []
            before = self.ceiling_truth(), self.transfer_snapshot()
            table = bounds._shape(role)[0].split()[0]
            target = identities[role]

            def entries(issued, requested):
                original = actual_entries(issued, requested)
                if consumer and consumer[-1] == (role, target) and requested == role:
                    matched = tuple(entry for entry in original if entry.identity == target)
                    self.assertEqual(len(matched), 1, "fault must remove the exact captured T dependency")
                    touched.append((issued.retained_identity, matched))
                    return tuple(entry for entry in original if entry.identity != target)
                return original

            def bound(connection, requested, identity):
                consumer.append((requested, identity))
                try:
                    return actual_bound(connection, requested, identity)
                finally:
                    consumer.pop()

            def require(connection, parent_role, parent_identity, child_role, child_identity):
                consumer.append((child_role, child_identity))
                try:
                    return actual_require(connection, parent_role, parent_identity, child_role, child_identity)
                finally:
                    consumer.pop()

            def query(reader, sql, params, **kwargs):
                if touched and table in str(sql):
                    native_reads.append(str(sql))
                return actual_query(reader, sql, params, **kwargs)

            # Forecast construction sees the intact captured roles. Remove a
            # dependency only while an actual bounded read or child traversal
            # consumes it, so capacity inflation cannot masquerade as refusal.
            with mock.patch.object(bounds, "_entries", entries), \
                    mock.patch.object(bounds, "_bound", bound), \
                    mock.patch.object(bounds, "_phase_require", require), \
                    mock.patch.object(_EvidenceRead, "query", query), self.assertRaises(conflict):
                invoke()
            self.assertTrue(touched, "the actual consumer must request the missing role")
            self.assertEqual(native_reads, [], "missing captured dependencies must refuse before native reads")
            self.assertEqual((self.ceiling_truth(), self.transfer_snapshot()), before)

        for role in ("outstanding-allocation-refs", "outstanding-allocation-claims", "invocation-refs", "header"):
            with self.subTest(role=role):
                missing(role, lambda: EffectAttemptStartService(self.unit_of_work,
                    id_factory=self.assert_no_ids).execute(command), EffectAttemptStartConflict)
        started = EffectAttemptStartService(self.unit_of_work,
            id_factory=Sequence("cleanup-original")).execute(command)
        fold = self.removed_fold(command, started)
        for role in ("invocation-refs", "header", "receipt-action", "receipt-event"):
            with self.subTest(role=role):
                missing(role, lambda: EffectAttemptFoldService(self.unit_of_work,
                    id_factory=self.assert_no_ids).execute(fold), EffectAttemptFoldConflict)
        self.assertIs(type(EffectAttemptFoldService(self.unit_of_work,
            id_factory=Sequence("cleanup-terminal")).execute(fold)), NewlyFolded)

    def test_a_only_then_b_closes_only_outstanding_b_and_never_redispatches_a(self):
        self.prepare_transfers(artifact_ids=("settings", "limits"),
            transferred=("settings",), candidates=("settings",))
        a, b = (next(ref for ref in self.refs if ref.artifact_id == name) for name in ("settings", "limits"))
        a_identity = self.run_removed("cleanup-a")
        self.assert_reservation(a_identity, candidates=(a,), claims=(), completions=(),
            transferred=(a,), status=EffectAttemptStatus.SUCCEEDED)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_configuration_invocation_closures").fetchone(), (0,))
        ConfigurationCleanupReadCeilingsFixture._publish_cleanup(self, self.cleanup_runtime,
            distinct_pins=False, recorded_cleanup=False, suffix="cleanup-b", refs=(b,))
        document = self.plan.cleanup_proposal.descriptor()
        self.assertEqual(len(document["invocations"]), 1)
        self.assertEqual({row["allocation_id"] for row in document["invocations"][0]["selection_members"]},
            {a.allocation_id, b.allocation_id})
        b_identity = self.run_removed("cleanup-b")
        self.assert_reservation(b_identity, candidates=(b,), claims=(b,), completions=(self.completion,),
            transferred=(a,), status=EffectAttemptStatus.SUCCEEDED)
        self.assert_reservation(a_identity, candidates=(a,), claims=(), completions=(),
            transferred=(a,), status=EffectAttemptStatus.SUCCEEDED)
        for table, expected in (("cpk_configuration_cleanup_members", 2),
                ("cpk_configuration_invocation_closures", 1), ("cpk_configuration_claim_closures", 1)):
            self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {table}").fetchone(), (expected,))
        before = self.ceiling_truth(), self.transfer_snapshot()
        install_schema(self.connection)
        self.assertEqual((self.ceiling_truth(), self.transfer_snapshot()), before)

    def test_zero_claim_ambiguous_dispatch_keeps_exclusion_and_never_redispatches(self):
        self.prepare_transfers()
        self.admit_cleanup()
        claimed = self.ready_run("cleanup-execution")
        adapter = RecordingRuntimeAdapter(RuntimeError("PROVIDER-CANARY"))
        coordinator = self.coordinator(self.unit_of_work, adapter, "cleanup-execution")
        command = self.execution_command(claimed, "cleanup-execution")
        self.assertIs(coordinator.execute(command).status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(len(adapter.runtime_calls), 1)
        request = adapter.runtime_calls[0][1]
        identity = EffectAttemptIdentity(request.source.run_id, request.activity_id.value, 1)
        record = self.assert_reservation(identity, candidates=self.refs, claims=(), completions=(),
            transferred=self.refs, status=EffectAttemptStatus.UNCERTAIN)
        self.assertEqual(tuple(value.status for value in record.outcomes.outcomes),
            (ConfigurationCleanupStatus.UNKNOWN,))
        from control_plane_kit_operations.configuration_cleanup_planning import ConfigurationCleanupPlanningService
        inspection = ConfigurationCleanupPlanningService(self.unit_of_work,
            clock=self.no_ids, id_factory=self.no_ids).inspect(
            self.cleanup_query, context=command_context())
        self.assertEqual(inspection.state, "unavailable")
        before = self.ceiling_truth(), self.transfer_snapshot()
        self.assertNotIn("PROVIDER-CANARY", repr(before))
        self.assertIs(coordinator.execute(command).status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual((self.ceiling_truth(), self.transfer_snapshot()), before)
