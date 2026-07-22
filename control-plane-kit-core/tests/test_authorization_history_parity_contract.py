import unittest

from control_plane_kit_core.operations import (
    ActivityHistoryPolicy,
    AdapterOperationSecurityBinding,
    AdapterOperationSecurityParityContract,
    ApplicationServiceBinding,
    ControlPlaneServiceRole,
    DeploymentProgramBoundary,
    ErrorDisclosurePolicy,
    ExternalEffectPolicy,
    HttpApiContract,
    HttpAuthScope,
    HttpOperationSafety,
    InvalidAdapterParityContract,
    McpStreamableHttpContract,
    ServiceTransactionBoundary,
    StoreParticipation,
    UnitOfWorkBoundary,
    operator_adapter_security_parity,
    operator_command_http_routes,
    operator_command_parity,
    operator_read_http_routes,
    operator_read_projection_parity,
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


def _security_parity() -> AdapterOperationSecurityParityContract:
    mcp = McpStreamableHttpContract()
    return operator_adapter_security_parity(
        projection_parity=operator_read_projection_parity(
            HttpApiContract(operator_read_http_routes()),
            mcp,
        ),
        command_parity=operator_command_parity(
            HttpApiContract(operator_command_http_routes()),
            mcp,
            UnitOfWorkBoundary(
                program=_program(),
                services=tuple(
                    _transaction_rule(role) for role in ControlPlaneServiceRole
                ),
            ),
        ),
    )


class AuthorizationHistoryParityTests(unittest.TestCase):
    def test_security_parity_covers_read_and_command_operations(self) -> None:
        parity = _security_parity()

        self.assertEqual(len(parity.operations), 24)
        command = parity.operation("deployment.execute")
        self.assertEqual(command.auth_scope, HttpAuthScope.EXECUTION_RUN)
        self.assertEqual(command.safety, HttpOperationSafety.DESTRUCTIVE)
        self.assertEqual(
            command.activity_history,
            ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
        )
        self.assertEqual(command.error_disclosure, ErrorDisclosurePolicy.BOUNDED_REDACTED)

        read = parity.operation("read.workspace")
        self.assertEqual(read.auth_scope, HttpAuthScope.READ)
        self.assertEqual(read.safety, HttpOperationSafety.READ_ONLY)
        self.assertEqual(read.activity_history, ActivityHistoryPolicy.NOT_RECORDED)

        setup = parity.operation("product-descriptor.import")
        self.assertEqual(setup.auth_scope, HttpAuthScope.ADMIN)
        self.assertEqual(setup.safety, HttpOperationSafety.COMMAND)
        self.assertEqual(
            setup.activity_history,
            ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS,
        )

    def test_descriptor_is_closed_redacted_and_round_trips(self) -> None:
        parity = _security_parity()
        descriptor = parity.descriptor()

        self.assertEqual(descriptor["kind"], "adapter-operation-security-parity")
        self.assertEqual(
            AdapterOperationSecurityParityContract.from_descriptor(descriptor),
            parity,
        )

        rendered = repr(descriptor).lower()
        for forbidden in ("token", "secret", "password", "private_url"):
            self.assertNotIn(forbidden, rendered)

        with self.assertRaises(InvalidAdapterParityContract):
            AdapterOperationSecurityParityContract.from_descriptor(
                {**descriptor, "extra": True}
            )

    def test_read_operations_must_remain_read_only_and_read_scoped(self) -> None:
        parity = _security_parity()
        operations = tuple(
            AdapterOperationSecurityBinding(
                operation_id=operation.operation_id,
                service_role=operation.service_role,
                http_route_id=operation.http_route_id,
                mcp_name=operation.mcp_name,
                auth_scope=HttpAuthScope.ADMIN
                if operation.operation_id == "read.workspace"
                else operation.auth_scope,
                safety=operation.safety,
                activity_history=operation.activity_history,
                error_disclosure=operation.error_disclosure,
            )
            for operation in parity.operations
        )

        with self.assertRaises(InvalidAdapterParityContract):
            AdapterOperationSecurityParityContract(
                projection_parity=parity.projection_parity,
                command_parity=parity.command_parity,
                operations=operations,
            )

    def test_commands_must_leave_activity_history_and_match_route_safety(self) -> None:
        parity = _security_parity()
        operations = tuple(
            AdapterOperationSecurityBinding(
                operation_id=operation.operation_id,
                service_role=operation.service_role,
                http_route_id=operation.http_route_id,
                mcp_name=operation.mcp_name,
                auth_scope=operation.auth_scope,
                safety=HttpOperationSafety.COMMAND
                if operation.operation_id == "deployment.execute"
                else operation.safety,
                activity_history=operation.activity_history,
                error_disclosure=operation.error_disclosure,
            )
            for operation in parity.operations
        )

        with self.assertRaises(InvalidAdapterParityContract):
            AdapterOperationSecurityParityContract(
                projection_parity=parity.projection_parity,
                command_parity=parity.command_parity,
                operations=operations,
            )

        operations = tuple(
            AdapterOperationSecurityBinding(
                operation_id=operation.operation_id,
                service_role=operation.service_role,
                http_route_id=operation.http_route_id,
                mcp_name=operation.mcp_name,
                auth_scope=operation.auth_scope,
                safety=operation.safety,
                activity_history=ActivityHistoryPolicy.NOT_RECORDED
                if operation.operation_id == "deployment.plan"
                else operation.activity_history,
                error_disclosure=operation.error_disclosure,
            )
            for operation in parity.operations
        )

        with self.assertRaises(InvalidAdapterParityContract):
            AdapterOperationSecurityParityContract(
                projection_parity=parity.projection_parity,
                command_parity=parity.command_parity,
                operations=operations,
            )

    def test_error_disclosure_policy_must_be_bounded_and_redacted(self) -> None:
        parity = _security_parity()
        operations = tuple(
            AdapterOperationSecurityBinding(
                operation_id=operation.operation_id,
                service_role=operation.service_role,
                http_route_id=operation.http_route_id,
                mcp_name=operation.mcp_name,
                auth_scope=operation.auth_scope,
                safety=operation.safety,
                activity_history=operation.activity_history,
                error_disclosure=ErrorDisclosurePolicy.TRANSPORT_PRIVATE
                if operation.operation_id == "deployment.execute"
                else operation.error_disclosure,
            )
            for operation in parity.operations
        )

        with self.assertRaises(InvalidAdapterParityContract):
            AdapterOperationSecurityParityContract(
                projection_parity=parity.projection_parity,
                command_parity=parity.command_parity,
                operations=operations,
            )


if __name__ == "__main__":
    unittest.main()
