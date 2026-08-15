from __future__ import annotations

import json
import unittest

from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    RecoveryDecisionKind,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.read_services.operations_history import (
    _event_descriptor as public_event_descriptor,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ExecutionLeaseRecoveryEvidence,
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


if __name__ == "__main__":
    unittest.main()
