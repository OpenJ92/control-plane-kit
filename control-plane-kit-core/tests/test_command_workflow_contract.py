import unittest

from control_plane_kit_core.operations import (
    ActivityHistoryPolicy,
    ApprovalPolicy,
    CommandIdempotencyPolicy,
    CommandPayloadPolicy,
    ControlPlaneServiceRole,
    DeploymentProgramStage,
    InvalidCommandWorkflowContract,
    OperatorCommandContract,
    OperatorCommandFamily,
    OperatorCommandKind,
    OperatorCommandWorkflowContract,
    canonical_operator_command_workflow_contract,
)


class CommandWorkflowContractTests(unittest.TestCase):
    def test_canonical_commands_are_closed_stage_and_service_contracts(self) -> None:
        contract = canonical_operator_command_workflow_contract()

        self.assertEqual(
            [
                (
                    command.operation_id,
                    command.kind,
                    command.family,
                    command.stage,
                    command.service_role,
                    command.idempotency,
                    command.approval,
                    command.activity_history,
                )
                for command in contract.commands
            ],
            [
                (
                    "activity-plan.request",
                    OperatorCommandKind.REQUEST_ACTIVITY_PLAN,
                    OperatorCommandFamily.ACTIVITY_PLANNING,
                    DeploymentProgramStage.PLAN,
                    ControlPlaneServiceRole.PLANNING,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.SUBMITS_FOR_APPROVAL,
                    ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
                ),
                (
                    "approval.decide",
                    OperatorCommandKind.DECIDE_APPROVAL,
                    OperatorCommandFamily.APPROVAL,
                    DeploymentProgramStage.APPROVE,
                    ControlPlaneServiceRole.APPROVAL,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.DECIDES_APPROVAL,
                    ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
                ),
                (
                    "approval.request",
                    OperatorCommandKind.REQUEST_APPROVAL,
                    OperatorCommandFamily.APPROVAL,
                    DeploymentProgramStage.PLAN,
                    ControlPlaneServiceRole.APPROVAL,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.SUBMITS_FOR_APPROVAL,
                    ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
                ),
                (
                    "desired-graph.set",
                    OperatorCommandKind.SET_DESIRED_GRAPH,
                    OperatorCommandFamily.DESIRED_GRAPH,
                    DeploymentProgramStage.PLAN,
                    ControlPlaneServiceRole.PLANNING,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.SUBMITS_FOR_APPROVAL,
                    ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
                ),
                (
                    "image-pull-authority.register",
                    OperatorCommandKind.REGISTER_IMAGE_PULL_AUTHORITY,
                    OperatorCommandFamily.PRODUCT_REGISTRATION,
                    DeploymentProgramStage.PLAN,
                    ControlPlaneServiceRole.PLANNING,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.NOT_REQUIRED,
                    ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
                ),
                (
                    "operation-session.cancel",
                    OperatorCommandKind.CANCEL_OPERATION_SESSION,
                    OperatorCommandFamily.OPERATION_SESSION,
                    DeploymentProgramStage.PLAN,
                    ControlPlaneServiceRole.LIFECYCLE,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.NOT_REQUIRED,
                    ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
                ),
                (
                    "operation-session.close",
                    OperatorCommandKind.CLOSE_OPERATION_SESSION,
                    OperatorCommandFamily.OPERATION_SESSION,
                    DeploymentProgramStage.PLAN,
                    ControlPlaneServiceRole.LIFECYCLE,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.NOT_REQUIRED,
                    ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
                ),
                (
                    "operation-session.record-action",
                    OperatorCommandKind.RECORD_OPERATION_ACTION,
                    OperatorCommandFamily.OPERATION_SESSION,
                    DeploymentProgramStage.PLAN,
                    ControlPlaneServiceRole.LIFECYCLE,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.NOT_REQUIRED,
                    ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
                ),
                (
                    "operation-session.start",
                    OperatorCommandKind.START_OPERATION_SESSION,
                    OperatorCommandFamily.OPERATION_SESSION,
                    DeploymentProgramStage.PLAN,
                    ControlPlaneServiceRole.LIFECYCLE,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.NOT_REQUIRED,
                    ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
                ),
                (
                    "product-descriptor.import",
                    OperatorCommandKind.IMPORT_PRODUCT_DESCRIPTOR,
                    OperatorCommandFamily.PRODUCT_REGISTRATION,
                    DeploymentProgramStage.PLAN,
                    ControlPlaneServiceRole.PLANNING,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.NOT_REQUIRED,
                    ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
                ),
                (
                    "workspace.create",
                    OperatorCommandKind.CREATE_WORKSPACE,
                    OperatorCommandFamily.WORKSPACE,
                    DeploymentProgramStage.PLAN,
                    ControlPlaneServiceRole.PLANNING,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.NOT_REQUIRED,
                    ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
                ),
            ],
        )

    def test_descriptor_is_bounded_secret_free_and_round_trips(self) -> None:
        contract = canonical_operator_command_workflow_contract()
        descriptor = contract.descriptor()

        self.assertEqual(descriptor["kind"], "operator-command-workflow-contract")
        self.assertEqual(
            OperatorCommandWorkflowContract.from_descriptor(descriptor),
            contract,
        )
        self.assertNotIn("postgres", repr(descriptor).lower())
        self.assertNotIn("unit_of_work", repr(descriptor).lower())
        self.assertNotIn("fastapi", repr(descriptor).lower())
        self.assertNotIn("token", repr(descriptor).lower())
        self.assertNotIn("secret", repr(descriptor).lower())

        with self.assertRaises(InvalidCommandWorkflowContract):
            OperatorCommandWorkflowContract.from_descriptor(
                {**descriptor, "extra": True}
            )

    def test_session_graph_plan_and_approval_contract_laws_are_explicit(self) -> None:
        contract = canonical_operator_command_workflow_contract()

        self.assertEqual(
            contract.command("operation-session.start").payload_policy,
            CommandPayloadPolicy.REDACT_OPERATOR_VALUES,
        )
        self.assertTrue(contract.command("operation-session.start").creates_session)
        self.assertTrue(
            contract.command("operation-session.close").terminal_session_transition
        )
        self.assertTrue(
            contract.command("operation-session.cancel").terminal_session_transition
        )
        self.assertTrue(
            contract.command("desired-graph.set").requires_open_session
        )
        self.assertIs(
            contract.command("desired-graph.set").payload_policy,
            CommandPayloadPolicy.GRAPH_DESCRIPTOR_REFERENCE,
        )
        self.assertIs(
            contract.command("activity-plan.request").payload_policy,
            CommandPayloadPolicy.PLAN_DESCRIPTOR_REFERENCE,
        )
        self.assertIs(
            contract.command("approval.request").payload_policy,
            CommandPayloadPolicy.APPROVAL_RISK_EVIDENCE,
        )

    def test_contract_rejects_duplicate_weak_or_mismatched_commands(self) -> None:
        command = canonical_operator_command_workflow_contract().command(
            "activity-plan.request"
        )

        with self.assertRaises(InvalidCommandWorkflowContract):
            OperatorCommandWorkflowContract((command, command))

        with self.assertRaises(InvalidCommandWorkflowContract):
            OperatorCommandContract(
                operation_id="activity-plan.request",
                kind=OperatorCommandKind.REQUEST_ACTIVITY_PLAN,
                family=OperatorCommandFamily.ACTIVITY_PLANNING,
                stage=DeploymentProgramStage.PLAN,
                service_role=ControlPlaneServiceRole.PLANNING,
                request_schema="RequestActivityPlan",
                response_schema="ActivityPlanningResult",
                idempotency=CommandIdempotencyPolicy.BEST_EFFORT,
                approval=ApprovalPolicy.SUBMITS_FOR_APPROVAL,
                activity_history=ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
                payload_policy=CommandPayloadPolicy.PLAN_DESCRIPTOR_REFERENCE,
                requires_open_session=True,
                creates_session=False,
                terminal_session_transition=False,
            )

        with self.assertRaises(InvalidCommandWorkflowContract):
            OperatorCommandContract(
                operation_id="desired-graph.set",
                kind=OperatorCommandKind.SET_DESIRED_GRAPH,
                family=OperatorCommandFamily.APPROVAL,
                stage=DeploymentProgramStage.PLAN,
                service_role=ControlPlaneServiceRole.PLANNING,
                request_schema="SetDesiredGraph",
                response_schema="DesiredGraphEditResult",
                idempotency=CommandIdempotencyPolicy.REQUIRED,
                approval=ApprovalPolicy.SUBMITS_FOR_APPROVAL,
                activity_history=ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
                payload_policy=CommandPayloadPolicy.GRAPH_DESCRIPTOR_REFERENCE,
                requires_open_session=True,
                creates_session=False,
                terminal_session_transition=False,
            )


if __name__ == "__main__":
    unittest.main()
