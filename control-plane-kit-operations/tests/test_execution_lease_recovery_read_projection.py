from __future__ import annotations

import json
import unittest

from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    RecoveryDecisionKind,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.read_pages import (
    OrdinalReadCursor,
    ReadCollection,
    ReadPage,
    ReadPageCandidate,
    ReadPageRequest,
    RunReadScope,
)
from control_plane_kit_operations.read_services import InstanceReadService
from control_plane_kit_operations.read_services.operations_history import (
    _event_descriptor as public_event_descriptor,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityRunRecord,
    AdmittedRun,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionLeaseRecoveryEvidence,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    RetryIdentity,
    WorkspaceRecord,
)


class _WorkspaceStore:
    def get(self, workspace_id):
        if workspace_id != "workspace-a":
            raise KeyError(workspace_id)
        return WorkspaceRecord("workspace-a", "Workspace A")


class _ExecutionStore:
    def __init__(self, event: ActivityEventRecord) -> None:
        self.event = event

    def get_run(self, run_id):
        return ActivityRunRecord(
            "run-a",
            "plan-a",
            AdmittedRun("request-a"),
            RetryIdentity(1),
            ActivityRunStatus.FAILED,
            "2026-08-15T03:59:10Z",
            started_at="2026-08-15T03:59:20Z",
        )

    def get_request(self, request_id):
        return ExecutionRequestRecord(
            ExecutionRequestIdentity(
                "request-a", "workspace-a", "session-a", "plan-a"
            ),
            ExecutionRequestStatus.CLAIMED,
            "operator-a",
            "2026-08-15T03:59:00Z",
            "approval-request-a",
            "approval-decision-a",
            ExecutionIdempotency("execute-a", "a" * 64),
            ClaimIdentity(
                "worker-b",
                8,
                "2026-08-15T04:01:00Z",
                "2026-08-15T04:11:00Z",
            ),
        )

    def event_page(self, request):
        return ReadPage.from_candidates(
            request,
            (
                ReadPageCandidate(
                    self.event,
                    OrdinalReadCursor(
                        ReadCollection.RUN_EVENTS,
                        request.scope,
                        self.event.ordinal,
                        self.event.event_id,
                    ),
                ),
            ),
        )


class ExecutionLeaseRecoveryReadProjectionTests(unittest.TestCase):
    def test_ordinary_event_shape_is_unchanged_and_recovery_is_conditional(self) -> None:
        ordinary = ActivityEventRecord(
            "event-ordinary",
            "run-a",
            1,
            ActivityEventKind.RUN_OPENED,
            "2026-08-15T04:00:00Z",
        )
        self.assertEqual(
            public_event_descriptor(ordinary),
            {
                "event_id": "event-ordinary",
                "run_id": "run-a",
                "ordinal": 1,
                "event_type": "run_opened",
                "occurred_at": "2026-08-15T04:00:00Z",
                "activity_id": None,
                "payload": {},
                "failure": None,
            },
        )

        recovery = ActivityEventRecord(
            "event-recovery",
            "run-a",
            2,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            "2026-08-15T04:01:00Z",
            recovery=ExecutionLeaseRecoveryEvidence(
                RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
                RunId("run-a"),
                ExecutionLeaseFence("worker-a", 7),
                ExecutionLeaseFence("worker-b", 8),
            ),
        )
        descriptor = public_event_descriptor(recovery)
        self.assertIn(
            "recovery",
            descriptor,
            "typed recovery evidence is absent from the public event projection",
        )
        self.assertEqual(
            descriptor["recovery"],
            {
                "decision": "take-over-expired-claim",
                "retained_run_id": "run-a",
                "prior_fence": {"worker_id": "worker-a", "generation": 7},
                "replacement_fence": {"worker_id": "worker-b", "generation": 8},
            },
        )
        rendered = json.dumps(descriptor, sort_keys=True)
        for forbidden in (
            "authority_reference",
            "scopes",
            "claimed_at",
            "lease_expires_at",
            "idempotency",
            "fingerprint",
            "secret",
            "endpoint",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_instance_read_service_exposes_conditional_recovery_evidence(self) -> None:
        recovery = ActivityEventRecord(
            "event-recovery",
            "run-a",
            2,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            "2026-08-15T04:01:00Z",
            recovery=ExecutionLeaseRecoveryEvidence(
                RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
                RunId("run-a"),
                ExecutionLeaseFence("worker-a", 7),
                ExecutionLeaseFence("worker-b", 8),
            ),
        )
        request = ReadPageRequest(
            ReadCollection.RUN_EVENTS,
            RunReadScope("workspace-a", "run-a"),
            10,
        )
        service = InstanceReadService(
            workspace_store=_WorkspaceStore(),
            graph_topology_store=object(),
            execution_store=_ExecutionStore(recovery),
        )

        page = service.run_events(request)

        self.assertEqual(page.request, request)
        self.assertEqual(len(page.items), 1)
        self.assertIn(
            "recovery",
            page.items[0],
            "typed recovery evidence is absent from InstanceReadService",
        )
        self.assertEqual(
            page.items[0]["recovery"],
            recovery.recovery.descriptor(),
        )
        rendered = json.dumps(page.items, sort_keys=True)
        for forbidden in (
            "authority_reference",
            "scopes",
            "claimed_at",
            "lease_expires_at",
            "idempotency",
            "fingerprint",
            "secret",
            "endpoint",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_retry_evidence_is_exact_in_event_and_read_service_projection(
        self,
    ) -> None:
        fence = ExecutionLeaseFence("worker-a", 7)
        retry = ActivityEventRecord(
            "event-retry",
            "run-a",
            2,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            "2026-08-15T04:01:00Z",
            recovery=ExecutionLeaseRecoveryEvidence(
                RecoveryDecisionKind.RETRY_AS_NEW_RUN,
                RunId("run-a"),
                fence,
                fence,
            ),
        )
        expected = {
            "decision": "retry-as-new-run",
            "retained_run_id": "run-a",
            "prior_fence": {"worker_id": "worker-a", "generation": 7},
            "replacement_fence": {
                "worker_id": "worker-a",
                "generation": 7,
            },
        }

        event_descriptor = public_event_descriptor(retry)
        self.assertEqual(event_descriptor["recovery"], expected)

        request = ReadPageRequest(
            ReadCollection.RUN_EVENTS,
            RunReadScope("workspace-a", "run-a"),
            10,
        )
        page = InstanceReadService(
            workspace_store=_WorkspaceStore(),
            graph_topology_store=object(),
            execution_store=_ExecutionStore(retry),
        ).run_events(request)
        self.assertEqual(page.request, request)
        self.assertEqual(page.items[0]["recovery"], expected)

        rendered = json.dumps(
            {"event": event_descriptor, "page": page.items},
            sort_keys=True,
        )
        for forbidden in (
            "authority_reference",
            "scopes",
            "claimed_at",
            "lease_expires_at",
            "idempotency",
            "fingerprint",
            "secret",
            "endpoint",
        ):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
