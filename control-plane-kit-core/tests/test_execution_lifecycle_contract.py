import unittest

from control_plane_kit_core.operations import (
    ActivityEventKind,
    ActivityEventScope,
    ActivityRunStatus,
    ContractEnforcementOwner,
    ExecutionLifecycleContractSet,
    ExecutionRequestStatus,
    FailureCategory,
    InvalidExecutionLifecycleContract,
    LifecycleOperationKind,
    RecoveryDecisionKind,
    RecoveryScope,
    activity_event_scope,
    canonical_execution_lifecycle_contract_set,
)


class ExecutionLifecycleContractTests(unittest.TestCase):
    def test_canonical_contract_names_closed_values_without_runtime_state(self) -> None:
        contract = canonical_execution_lifecycle_contract_set()
        descriptor = contract.descriptor()

        self.assertEqual(
            descriptor["request_statuses"],
            [status.value for status in ExecutionRequestStatus],
        )
        self.assertEqual(
            descriptor["failure_categories"],
            [category.value for category in FailureCategory],
        )
        self.assertEqual(
            [item.status for item in contract.timing],
            sorted(ActivityRunStatus, key=lambda status: status.value),
        )
        self.assertEqual(
            [item.kind for item in contract.events],
            sorted(ActivityEventKind, key=lambda kind: kind.value),
        )
        self.assertEqual(
            [item.kind for item in contract.recovery_decisions],
            sorted(RecoveryDecisionKind, key=lambda kind: kind.value),
        )
        self.assertEqual(
            {item.kind for item in contract.operations},
            set(LifecycleOperationKind),
        )

        serialized = repr(descriptor).lower()
        for forbidden in (
            "postgres",
            "unitofwork",
            "sql",
            "store",
            "database",
            "fastapi",
            "mcp-server",
            "token",
            "secret",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)

    def test_descriptor_round_trips_strictly_and_rejects_open_values(self) -> None:
        contract = canonical_execution_lifecycle_contract_set()
        descriptor = contract.descriptor()

        self.assertEqual(
            ExecutionLifecycleContractSet.from_descriptor(descriptor),
            contract,
        )

        with self.assertRaises(InvalidExecutionLifecycleContract):
            ExecutionLifecycleContractSet.from_descriptor({**descriptor, "extra": True})

        mutated = dict(descriptor)
        mutated["request_statuses"] = [
            *descriptor["request_statuses"],
            "invented",
        ]
        with self.assertRaises(InvalidExecutionLifecycleContract):
            ExecutionLifecycleContractSet.from_descriptor(mutated)

        mutated = dict(descriptor)
        events = list(descriptor["events"])
        event = dict(events[0])
        event["kind"] = "invented"
        events[0] = event
        mutated["events"] = events
        with self.assertRaises(InvalidExecutionLifecycleContract):
            ExecutionLifecycleContractSet.from_descriptor(mutated)

    def test_event_scope_and_recovery_payload_laws_are_closed(self) -> None:
        contract = canonical_execution_lifecycle_contract_set()

        for kind in ActivityEventKind:
            with self.subTest(kind=kind):
                event = contract.event(kind)
                expected_scope = (
                    ActivityEventScope.ACTIVITY
                    if kind.value.startswith("step_")
                    else ActivityEventScope.RUN
                )
                self.assertIs(activity_event_scope(kind), expected_scope)
                self.assertIs(event.scope, expected_scope)
                self.assertIs(
                    event.descriptor()["requires_activity_id"],
                    expected_scope is ActivityEventScope.ACTIVITY,
                )
                self.assertIs(
                    event.may_carry_recovery,
                    kind is ActivityEventKind.RECOVERY_DECISION_RECORDED,
                )

        descriptor = contract.event(ActivityEventKind.RUN_STARTED).descriptor()
        descriptor["requires_activity_id"] = True
        with self.assertRaises(InvalidExecutionLifecycleContract):
            type(contract.event(ActivityEventKind.RUN_STARTED)).from_descriptor(
                descriptor
            )

    def test_run_timing_and_transition_domains_match_frozen_lifecycle_algebra(self) -> None:
        contract = canonical_execution_lifecycle_contract_set()
        timing = {
            item.status: (item.requires_started_at, item.requires_settled_at)
            for item in contract.timing
        }

        self.assertEqual(
            timing,
            {
                ActivityRunStatus.CLAIMED: (False, False),
                ActivityRunStatus.RUNNING: (True, False),
                ActivityRunStatus.PAUSED: (True, False),
                ActivityRunStatus.SUCCEEDED: (True, True),
                ActivityRunStatus.FAILED: (True, False),
                ActivityRunStatus.COMPENSATING: (True, False),
                ActivityRunStatus.COMPENSATED: (True, True),
                ActivityRunStatus.PARTIALLY_FAILED: (True, True),
                ActivityRunStatus.UNCOMPENSATED_FAILURE: (True, True),
                ActivityRunStatus.CANCELLED: (False, True),
            },
        )
        self.assertEqual(
            contract.operation("run.start").accepted_run_statuses,
            (ActivityRunStatus.CLAIMED,),
        )
        self.assertEqual(
            contract.operation("run.fail").accepted_run_statuses,
            (ActivityRunStatus.RUNNING, ActivityRunStatus.PAUSED),
        )
        self.assertEqual(
            contract.operation("run.cancel").accepted_run_statuses,
            (
                ActivityRunStatus.CLAIMED,
                ActivityRunStatus.RUNNING,
                ActivityRunStatus.PAUSED,
            ),
        )

    def test_recovery_decision_contracts_preserve_scopes_and_preconditions(self) -> None:
        contract = canonical_execution_lifecycle_contract_set()

        self.assertIs(
            contract.recovery_decision(
                RecoveryDecisionKind.CONFIRM_EFFECT_SUCCEEDED
            ).required_scope,
            RecoveryScope.RESOLVE_UNCERTAINTY,
        )
        self.assertTrue(
            contract.recovery_decision(
                RecoveryDecisionKind.CONFIRM_EFFECT_FAILED
            ).requires_uncertainty
        )
        self.assertTrue(
            contract.recovery_decision(
                RecoveryDecisionKind.RESUME_SAME_INTENT
            ).requires_no_uncertainty
        )
        self.assertTrue(
            contract.recovery_decision(
                RecoveryDecisionKind.BEGIN_COMPENSATION
            ).requires_compensation_available
        )
        self.assertFalse(
            contract.recovery_decision(
                RecoveryDecisionKind.REMAIN_PAUSED
            ).requires_intent_match
        )
        for kind, scope in (
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, RecoveryScope.RENEW_CLAIM),
            (
                RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
                RecoveryScope.TAKE_OVER_CLAIM,
            ),
            (
                RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
                RecoveryScope.ABANDON_CLAIM,
            ),
        ):
            with self.subTest(kind=kind):
                decision = contract.recovery_decision(kind)
                self.assertIs(decision.required_scope, scope)
                self.assertTrue(decision.requires_expired_claim)

    def test_durable_enforcement_and_graph_advancement_are_handoff_obligations(self) -> None:
        contract = canonical_execution_lifecycle_contract_set()

        self.assertTrue(
            all(
                operation.enforcement_owner is ContractEnforcementOwner.OPERATIONS
                for operation in contract.operations
            )
        )
        self.assertTrue(
            all(operation.requires_current_approval for operation in contract.operations)
        )
        self.assertEqual(
            [
                operation.operation_id
                for operation in contract.operations
                if operation.writes_current_graph
            ],
            ["graph.advance-current"],
        )
        advancement = contract.operation("graph.advance-current")
        self.assertTrue(advancement.requires_worker_scope)
        self.assertTrue(advancement.requires_current_graph_match)
        self.assertEqual(
            advancement.event_kinds,
            (ActivityEventKind.CURRENT_GRAPH_ADVANCED,),
        )


if __name__ == "__main__":
    unittest.main()
