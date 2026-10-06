"""#1944 recorded v2 values must not acquire fresh pre-B2 authority."""
from dataclasses import replace
import unittest
from unittest import mock

from psycopg.types.json import Jsonb

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations import configuration_cleanup as values
from control_plane_kit_operations import plan_derivation as plans
from control_plane_kit_operations.admission import ExecutionAdmissionCommandService, ExecutionAdmissionConflict
from control_plane_kit_operations.approvals import ApprovalCommandService, ApprovalWorkflowError, RequestApproval
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartConflict
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.postgres.receiver_execution_scopes import _ExecutionScopeStorage
from control_plane_kit_operations.receiver_execution_scopes import derive_execution_receiver_scopes
from control_plane_kit_operations.records import ExecutionRequestIdentity
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.configuration_cleanup_execution_fixture import ConfigurationCleanupExecutionFixture
from tests.configuration_cleanup_v2_fixture import v2_wire
from tests.postgres_effect_attempt_coordinator_fixture import RecordingRuntimeAdapter
from tests.receiver_fresh_execution_fixture import load_execution_context


class PostgresConfigurationCleanupV2NonactivationTests(ConfigurationCleanupExecutionFixture, unittest.TestCase):
    def record_v2(self):
        self.assertTrue(hasattr(values, "ConfigurationCleanupProposalV2Codec"),
            "#1944 valid v2 value is missing before owner nonactivation can be exercised")
        proposal = values.ConfigurationCleanupProposalV2Codec().decode(v2_wire(self.plan.cleanup_proposal.descriptor()))
        profile = plans.PlanDerivationProfile.CONFIGURATION_CLEANUP_V2
        record = replace(self.plan, derivation_profile=profile, cleanup_proposal=proposal)
        subject = ActivityPlanApprovalSubject(record.plan_id,
            proposal_fingerprint=values.configuration_cleanup_proposal_fingerprint(proposal))
        requests = self.connection.execute("SELECT request_id,workspace_id,session_id,plan_id,"
            "receiver_scope_count,receiver_scope_digest FROM cpk_execution_requests WHERE plan_id=%s",
            (record.plan_id,)).fetchall()
        self.assertLessEqual(len(requests), 1)
        commitments = []
        for row in requests:
            identity = ExecutionRequestIdentity(*row[:4])
            original, old = _ExecutionScopeStorage(self.connection).verify(identity)
            self.assertEqual(original[0], self.plan)
            self.assertEqual(old, derive_execution_receiver_scopes(identity, *original))
            self.assertEqual(row[4:], (len(old.scopes), old.source_digest))
            new = derive_execution_receiver_scopes(identity, record, *original[1:])
            self.assertEqual(new.scopes, old.scopes)
            self.assertNotEqual(new.source_digest, old.source_digest)
            commitments.append((identity, original[1:], new))
        # Below-owner RECORDED representation fixture, not a public v2 plan,
        # approval or execution admission. Its genuine v1 chronology is complete
        # before this detached future-version suffix; no provider is involved.
        with self.connection.transaction():
            self.connection.execute("UPDATE cpk_activity_plans SET payload=%s WHERE plan_id=%s",
                (Jsonb(plans.encode_stored_activity_plan(record.plan, profile=profile, cleanup_proposal=proposal)),
                 record.plan_id))
            self.connection.execute("UPDATE cpk_approval_requests SET subject_payload=%s,review_digest=%s "
                "WHERE request_id=%s", (Jsonb(subject.descriptor()), subject.review_digest, self.approval.request_id))
            # The original immutable scope commitment includes the full plan
            # envelope. Keep this explicitly recorded suffix internally exact;
            # no new request, scope, approval or admission is manufactured.
            for identity, _, derived in commitments:
                self.connection.execute("UPDATE cpk_execution_requests SET receiver_scope_digest=%s "
                    "WHERE request_id=%s", (derived.source_digest, identity.request_id))
        for identity, pins, derived in commitments:
            self.assertEqual(_ExecutionScopeStorage(self.connection).verify(identity), ((record, *pins), derived))
        install_schema(self.connection)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.activity_history.get_plan(record.plan_id), record)
            approval = uow.stores.activity_history.get_approval_request(self.approval.request_id)
            self.assertEqual(approval.subject, subject)
        return record

    def forbidden(self):
        self.fail("pre-B2 v2 refusal sampled mutation clock/identity")

    def test_recorded_v2_fresh_approval_and_admission_refuse_without_authority_writes(self):
        self.prepare_cleanup_execution()
        record = self.record_v2()
        before = self.ceiling_truth()
        with self.assertRaisesRegex(ApprovalWorkflowError, "cleanup approval requires an exact proposal"):
            ApprovalCommandService(self.unit_of_work, clock=self.forbidden, id_factory=self.forbidden).execute(
                RequestApproval("session-a", record.plan_id, "operator-a", (PolicyScope.PLAN_REQUEST,),
                    IdempotencyKey("v2-new-approval")))
        self.assertEqual(self.ceiling_truth(), before)
        with self.assertRaisesRegex(ExecutionAdmissionConflict, "cleanup requires its exact approved proposal"):
            ExecutionAdmissionCommandService(self.unit_of_work, clock=self.forbidden, id_factory=self.forbidden).execute(
                self.command(plan_id=record.plan_id, approval_request_id=self.approval.request_id,
                    scopes=tuple(PolicyScope), key="v2-new-admission"))
        self.assertEqual(self.ceiling_truth(), before)

    def test_recorded_v2_native_start_refuses_before_capability_preparation(self):
        claimed = self.ready_cleanup("v2-start")
        command = self.native_start_command(claimed, "v2-start")
        self.record_v2()
        before = self.ceiling_truth()
        from control_plane_kit_operations.postgres.configuration_cleanup_read_ceilings import _CleanupOriginalReadCeilingsOwner
        # Instrument an actual capability boundary, not a guessed query count.
        with mock.patch.object(_CleanupOriginalReadCeilingsOwner, "capture",
                side_effect=AssertionError("v2 reached capability preparation")) as capture:
            with self.assertRaises(EffectAttemptStartConflict):
                EffectAttemptStartService(self.unit_of_work, id_factory=self.forbidden).execute(command)
            capture.assert_not_called()
        self.assertEqual(self.ceiling_truth(), before)

    def test_recorded_v2_coordinator_preserves_denial_receipt_and_never_calls_adapter(self):
        claimed = self.ready_cleanup("v2-coordinator")
        adapter = RecordingRuntimeAdapter(AssertionError("v2 reached external adapter"))
        coordinator = self.coordinator(self.unit_of_work, adapter, "v2-coordinator")
        command = self.execution_command(claimed, "v2-coordinator")
        original = load_execution_context(coordinator, command)
        self.assertIsNone(coordinator._guard_runtime_management(original, 0))
        self.record_v2()
        before = self.ceiling_truth()
        result = coordinator.execute(command)
        self.assertIs(result.status, CoordinatorStatus.UNSUPPORTED)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(adapter.legacy_calls, [])
        # These tables intentionally exclude cpk_execution_command_receipts:
        # the coordinator is allowed to record truthful idempotent denial.
        self.assertEqual(self.ceiling_truth(), before)
        receipt = self.connection.execute("SELECT receipt_status,result FROM cpk_execution_command_receipts "
            "WHERE run_id=%s AND idempotency_key=%s", (claimed.run.run_id, command.idempotency_key.value)).fetchone()
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt[0], "completed")
        self.assertEqual(coordinator.execute(command), result)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(self.ceiling_truth(), before)

    def test_v2_guard_precedes_shape_predicate_and_preserves_terminal_classification(self):
        from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
        claimed = self.ready_cleanup("v2-guard")
        coordinator = self.coordinator(self.unit_of_work, RecordingRuntimeAdapter(), "v2-guard")
        context = load_execution_context(coordinator, self.execution_command(claimed, "v2-guard"))
        record = self.record_v2()
        changed = replace(context, plan_record=record)
        # The profile denial precedes the existing shape predicate, including
        # its ordinary non-management supported branch. No fake runtime success.
        with mock.patch("control_plane_kit_operations.coordinator.runtime_management_execution_is_unsupported",
                side_effect=AssertionError("v2 reached ordinary capability predicate")) as capability:
            result = coordinator._guard_runtime_management(changed, 0)
            self.assertIs(result.status, CoordinatorStatus.UNSUPPORTED)
            capability.assert_not_called()
            terminal = replace(changed, run=replace(changed.run, status=ActivityRunStatus.SUCCEEDED,
                settled_at=self.now()))
            self.assertIsNone(coordinator._guard_runtime_management(terminal, 0))


if __name__ == "__main__":
    unittest.main()
