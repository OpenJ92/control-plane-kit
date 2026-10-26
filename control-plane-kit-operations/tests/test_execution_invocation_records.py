"""Closed invocation evidence is not a permit or a retained provider result."""

from dataclasses import replace
import unittest

from control_plane_kit_operations.effect_permissions import ExecutionCommandReference
from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationRecord, ExecutionInvocationKind as Kind,
    ExecutionInvocationDecision as Decision, ExecutionInvocationDisposition as Disposition,
    ExecutionInvocationOutcome as Outcome, ExecutionInvocationCursor,
)
from control_plane_kit_operations.records import OperationsRecordError


class ExecutionInvocationRecordTests(unittest.TestCase):
    def planned(self):
        return ExecutionInvocationRecord(ExecutionCommandReference("run-a", "execute-\x7f-a", "a" * 64),
            "workspace-a", "start-runtime", Kind.PREPARED_RUNTIME_DISPATCH, "b" * 64,
            "2030-01-01T00:00:00Z", attempt=1, original_event_id="original-start", request_fingerprint="c" * 64)

    def test_exact_codec_preserves_original_correlation_across_decision_and_classification(self):
        planned = self.planned()
        admitted = planned.decided(Decision(1), "2030-01-01T00:00:00Z")
        returned = admitted.completed(Outcome.RETURNED, "2030-01-01T00:00:01Z")
        for record in (planned, admitted, returned):
            self.assertEqual(ExecutionInvocationRecord.from_descriptor(record.descriptor()), record)
            self.assertEqual(record.invocation_id, planned.invocation_id)
        self.assertIs(returned.outcome, Outcome.RETURNED)
        with self.assertRaises(OperationsRecordError):
            ExecutionInvocationRecord.from_descriptor(dict(returned.descriptor(), provider_result={"success": True}))

    def test_planned_and_denied_cannot_carry_admitted_outcomes_or_partial_attempts(self):
        planned = self.planned()
        denied = planned.decided(Decision(2, denial_code="required-scope-denied"), "2030-01-01T00:00:01Z")
        self.assertIs(denied.disposition, Disposition.DENIED)
        for record, changes in (
            (planned, {"outcome": Outcome.PENDING}),
            (planned, {"kind": Kind.ENDPOINT_OBSERVATION}),
            (planned, {"original_event_id": None}),
            (denied, {"decision": Decision(2)}),
            (denied, {"outcome": Outcome.FAILED, "completed_at": "2030-01-01T00:00:02Z"}),
        ):
            with self.subTest(changes=changes), self.assertRaises(OperationsRecordError):
                replace(record, **changes)
        with self.assertRaises(OperationsRecordError):
            denied.decided(Decision(3), "2030-01-01T00:00:02Z")

    def test_successful_assignment_evidence_cannot_claim_a_denied_or_unobserved_a_b_check(self):
        assignment = "mra_" + "d" * 64
        invalid = (
            dict(permission_revision=None, denial_code="workspace-permission-denied"),
            dict(permission_revision=2, denial_code="required-scope-denied"),
            dict(permission_revision=2, denial_code="management-assignment-denied"),
        )
        for fields in invalid:
            payload = dict(fields, assignment_id=assignment, assignment_revision=1)
            with self.subTest(payload=payload):
                with self.assertRaises(OperationsRecordError):
                    Decision(**payload)
                with self.assertRaises(OperationsRecordError):
                    Decision.from_descriptor(payload)
        later_denial = Decision(2, assignment, 1, "secret-use-denied")
        self.assertEqual(Decision.from_descriptor(later_denial.descriptor()), later_denial)

    def test_classification_codes_and_times_cannot_contradict_the_retained_result_class(self):
        admitted = self.planned().decided(Decision(1), "2030-01-01T00:00:01Z")
        for outcome, code in ((Outcome.RETURNED, "capability-failed"),
                (Outcome.FAILED, "capability-outcome-unknown"), (Outcome.UNKNOWN, "capability-failed")):
            with self.subTest(outcome=outcome), self.assertRaises(OperationsRecordError):
                admitted.completed(outcome, "2030-01-01T00:00:02Z", code=code)
        with self.assertRaises(OperationsRecordError):
            admitted.completed(Outcome.UNKNOWN, "2030-01-01T00:00:00Z")
        unknown = admitted.completed(Outcome.UNKNOWN, "2030-01-01T00:00:02Z", code="recording-uncertain")
        self.assertEqual(ExecutionInvocationRecord.from_descriptor(unknown.descriptor()), unknown)

    def test_codec_rejects_changed_identity_extra_fields_and_unknown_versions_without_echoing_them(self):
        descriptor = self.planned().descriptor()
        for changed in (dict(descriptor, invocation_id="f" * 64), dict(descriptor, version=2),
                dict(descriptor, slot_key="retry-2"), dict(descriptor, private_payload="DO-NOT-ECHO")):
            with self.subTest(keys=tuple(changed)):
                with self.assertRaises(OperationsRecordError) as raised:
                    ExecutionInvocationRecord.from_descriptor(changed)
                self.assertNotIn("DO-NOT-ECHO", str(raised.exception))
        cursor = ExecutionInvocationCursor(self.planned().command, self.planned().invocation_id)
        self.assertEqual(ExecutionInvocationCursor.from_descriptor(cursor.descriptor()), cursor)
