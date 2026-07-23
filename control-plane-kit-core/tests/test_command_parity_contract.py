import unittest

from control_plane_kit_core.operations import (
    ApplicationServiceBinding,
    ApprovalPolicy,
    CommandIdempotencyPolicy,
    AdapterCommandBinding,
    AdapterCommandParityContract,
    ControlPlaneServiceRole,
    DeploymentProgramBoundary,
    ExternalEffectPolicy,
    HttpApiContract,
    HttpOperationSafety,
    InvalidAdapterParityContract,
    McpStreamableHttpContract,
    ServiceTransactionBoundary,
    StoreParticipation,
    UnitOfWorkBoundary,
    operator_command_http_routes,
    operator_command_parity,
)


def _program() -> DeploymentProgramBoundary:
    return DeploymentProgramBoundary(
        tuple(
            ApplicationServiceBinding(
                role=role,
                service_name=f"{role.value}-service",
            )
            for role in ControlPlaneServiceRole
        )
    )


def _transaction_rule(role: ControlPlaneServiceRole) -> ServiceTransactionBoundary:
    if role is ControlPlaneServiceRole.READS:
        return ServiceTransactionBoundary(role, StoreParticipation.READ_ONLY)
    if role is ControlPlaneServiceRole.AUTHORIZATION:
        return ServiceTransactionBoundary(role, StoreParticipation.NONE)
    if role is ControlPlaneServiceRole.EXECUTION:
        return ServiceTransactionBoundary(
            role,
            StoreParticipation.READ_WRITE,
            owns_transaction=True,
            external_effect_policy=ExternalEffectPolicy.AFTER_COMMIT,
            uses_worker=True,
            uses_runtime_authority=True,
        )
    return ServiceTransactionBoundary(
        role,
        StoreParticipation.READ_WRITE,
        owns_transaction=True,
    )


def _uow() -> UnitOfWorkBoundary:
    return UnitOfWorkBoundary(
        program=_program(),
        services=tuple(_transaction_rule(role) for role in ControlPlaneServiceRole),
    )


class CommandParityContractTests(unittest.TestCase):
    def test_operator_command_parity_maps_routes_to_policy_and_mcp_tools(self) -> None:
        parity = operator_command_parity(
            HttpApiContract(operator_command_http_routes()),
            McpStreamableHttpContract(),
            _uow(),
        )

        self.assertEqual(
            [
                (
                    binding.operation_id,
                    binding.http_route_id,
                    binding.mcp_tool_name,
                    binding.service_role,
                    binding.idempotency,
                    binding.approval,
                )
                for binding in parity.commands
            ],
            [
                (
                    "approval.decide",
                    "command.approval.decide",
                    "decide_approval",
                    ControlPlaneServiceRole.APPROVAL,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.DECIDES_APPROVAL,
                ),
                (
                    "approval.request",
                    "command.approval.request",
                    "request_approval",
                    ControlPlaneServiceRole.APPROVAL,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.SUBMITS_FOR_APPROVAL,
                ),
                (
                    "deployment.admit",
                    "command.deployment.admit",
                    "admit_deployment",
                    ControlPlaneServiceRole.ADMISSION,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
                ),
                (
                    "deployment.execute",
                    "command.deployment.execute",
                    "execute_deployment",
                    ControlPlaneServiceRole.EXECUTION,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
                ),
                (
                    "deployment.plan",
                    "command.deployment.plan",
                    "plan_deployment",
                    ControlPlaneServiceRole.PLANNING,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.SUBMITS_FOR_APPROVAL,
                ),
                (
                    "desired-graph.set",
                    "command.desired-graph.set",
                    "set_desired_graph",
                    ControlPlaneServiceRole.PLANNING,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.SUBMITS_FOR_APPROVAL,
                ),
                (
                    "graph.advance-current",
                    "command.graph.advance-current",
                    "advance_current_graph",
                    ControlPlaneServiceRole.LIFECYCLE,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
                ),
                (
                    "operation-session.cancel",
                    "command.operation-session.cancel",
                    "cancel_operation_session",
                    ControlPlaneServiceRole.LIFECYCLE,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.NOT_REQUIRED,
                ),
                (
                    "operation-session.close",
                    "command.operation-session.close",
                    "close_operation_session",
                    ControlPlaneServiceRole.LIFECYCLE,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.NOT_REQUIRED,
                ),
                (
                    "operation-session.record-action",
                    "command.operation-session.record-action",
                    "record_operation_action",
                    ControlPlaneServiceRole.LIFECYCLE,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.NOT_REQUIRED,
                ),
                (
                    "operation-session.start",
                    "command.operation-session.start",
                    "start_operation_session",
                    ControlPlaneServiceRole.LIFECYCLE,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.NOT_REQUIRED,
                ),
                (
                    "product-descriptor.import",
                    "command.product.import",
                    "import_product_descriptor",
                    ControlPlaneServiceRole.PLANNING,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.NOT_REQUIRED,
                ),
                (
                    "recovery.decide",
                    "command.recovery.decide",
                    "decide_recovery",
                    ControlPlaneServiceRole.RECOVERY,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
                ),
                (
                    "run.claim",
                    "command.run.claim",
                    "claim_run",
                    ControlPlaneServiceRole.LIFECYCLE,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
                ),
                (
                    "run.start",
                    "command.run.start",
                    "start_run",
                    ControlPlaneServiceRole.EXECUTION,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
                ),
                (
                    "workspace.create",
                    "command.workspace.create",
                    "create_workspace",
                    ControlPlaneServiceRole.PLANNING,
                    CommandIdempotencyPolicy.REQUIRED,
                    ApprovalPolicy.NOT_REQUIRED,
                ),
            ],
        )

    def test_descriptor_is_closed_and_round_trips(self) -> None:
        parity = operator_command_parity(
            HttpApiContract(operator_command_http_routes()),
            McpStreamableHttpContract(),
            _uow(),
        )
        descriptor = parity.descriptor()

        self.assertEqual(descriptor["kind"], "adapter-command-parity-contract")
        self.assertEqual(AdapterCommandParityContract.from_descriptor(descriptor), parity)

        with self.assertRaises(InvalidAdapterParityContract):
            AdapterCommandParityContract.from_descriptor({**descriptor, "extra": True})

    def test_policy_must_match_http_route_and_transaction_boundary(self) -> None:
        http_api = HttpApiContract(operator_command_http_routes())

        with self.assertRaises(InvalidAdapterParityContract):
            AdapterCommandParityContract(
                http_api=http_api,
                mcp=McpStreamableHttpContract(),
                unit_of_work=_uow(),
                commands=(
                    AdapterCommandBinding(
                        operation_id="deployment.execute",
                        service_role=ControlPlaneServiceRole.PLANNING,
                        request_schema="ExecuteDeploymentRequest",
                        response_schema="ExecutionRunResponse",
                        http_route_id="command.deployment.execute",
                        mcp_tool_name="execute_deployment",
                        idempotency=CommandIdempotencyPolicy.REQUIRED,
                        approval=ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
                    ),
                ),
            )

        weak_execution_boundary = UnitOfWorkBoundary(
            program=_program(),
            services=tuple(
                ServiceTransactionBoundary(
                    ControlPlaneServiceRole.EXECUTION,
                    StoreParticipation.READ_WRITE,
                    owns_transaction=True,
                )
                if role is ControlPlaneServiceRole.EXECUTION
                else _transaction_rule(role)
                for role in ControlPlaneServiceRole
            ),
        )
        with self.assertRaises(InvalidAdapterParityContract):
            AdapterCommandParityContract(
                http_api=http_api,
                mcp=McpStreamableHttpContract(),
                unit_of_work=weak_execution_boundary,
                commands=(
                    AdapterCommandBinding(
                        operation_id="deployment.execute",
                        service_role=ControlPlaneServiceRole.EXECUTION,
                        request_schema="ExecuteDeploymentRequest",
                        response_schema="ExecutionRunResponse",
                        http_route_id="command.deployment.execute",
                        mcp_tool_name="execute_deployment",
                        idempotency=CommandIdempotencyPolicy.REQUIRED,
                        approval=ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
                    ),
                ),
            )

    def test_destructive_commands_require_current_approval_and_idempotency(self) -> None:
        http_api = HttpApiContract(operator_command_http_routes())

        with self.assertRaises(InvalidAdapterParityContract):
            AdapterCommandParityContract(
                http_api=http_api,
                mcp=McpStreamableHttpContract(),
                unit_of_work=_uow(),
                commands=(
                    AdapterCommandBinding(
                        operation_id="deployment.execute",
                        service_role=ControlPlaneServiceRole.EXECUTION,
                        request_schema="ExecuteDeploymentRequest",
                        response_schema="ExecutionRunResponse",
                        http_route_id="command.deployment.execute",
                        mcp_tool_name="execute_deployment",
                        idempotency=CommandIdempotencyPolicy.BEST_EFFORT,
                        approval=ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
                    ),
                ),
            )

        with self.assertRaises(InvalidAdapterParityContract):
            AdapterCommandParityContract(
                http_api=http_api,
                mcp=McpStreamableHttpContract(),
                unit_of_work=_uow(),
                commands=(
                    AdapterCommandBinding(
                        operation_id="deployment.execute",
                        service_role=ControlPlaneServiceRole.EXECUTION,
                        request_schema="ExecuteDeploymentRequest",
                        response_schema="ExecutionRunResponse",
                        http_route_id="command.deployment.execute",
                        mcp_tool_name="execute_deployment",
                        idempotency=CommandIdempotencyPolicy.REQUIRED,
                        approval=ApprovalPolicy.NOT_REQUIRED,
                    ),
                ),
            )

    def test_duplicate_command_identities_fail_closed(self) -> None:
        binding = AdapterCommandBinding(
            operation_id="deployment.plan",
            service_role=ControlPlaneServiceRole.PLANNING,
            request_schema="PlanDeploymentRequest",
            response_schema="PlanDeploymentResponse",
            http_route_id="command.deployment.plan",
            mcp_tool_name="plan_deployment",
            idempotency=CommandIdempotencyPolicy.REQUIRED,
            approval=ApprovalPolicy.SUBMITS_FOR_APPROVAL,
        )

        with self.assertRaises(InvalidAdapterParityContract):
            AdapterCommandParityContract(
                http_api=HttpApiContract(operator_command_http_routes()),
                mcp=McpStreamableHttpContract(),
                unit_of_work=_uow(),
                commands=(binding, binding),
            )


if __name__ == "__main__":
    unittest.main()
