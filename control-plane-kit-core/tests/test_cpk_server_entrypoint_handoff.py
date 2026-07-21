import unittest

from control_plane_kit_core.operations import (
    ApplicationServiceBinding,
    ControlPlaneProcessContract,
    ControlPlaneServiceRole,
    CpkServerEntrypointHandoffContract,
    DependencyReadinessKind,
    DeploymentProgramBoundary,
    EntrypointCompositionPolicy,
    ExternalEffectPolicy,
    HttpApiContract,
    InvalidCpkServerHandoffContract,
    McpStreamableHttpContract,
    ProcessStatePolicy,
    ReadinessDependency,
    ServiceTransactionBoundary,
    StoreParticipation,
    UnitOfWorkBoundary,
    canonical_cpk_server_entrypoint_handoff,
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


def _uow() -> UnitOfWorkBoundary:
    return UnitOfWorkBoundary(
        program=_program(),
        services=tuple(_transaction_rule(role) for role in ControlPlaneServiceRole),
    )


def _http_api() -> HttpApiContract:
    return HttpApiContract(
        operator_read_http_routes() + operator_command_http_routes()
    )


def _handoff() -> CpkServerEntrypointHandoffContract:
    mcp = McpStreamableHttpContract()
    projection_parity = operator_read_projection_parity(_http_api(), mcp)
    command_parity = operator_command_parity(_http_api(), mcp, _uow())
    return canonical_cpk_server_entrypoint_handoff(
        process=ControlPlaneProcessContract(
            dependencies=tuple(
                ReadinessDependency(kind)
                for kind in (
                    DependencyReadinessKind.STORE,
                    DependencyReadinessKind.RUNTIME_AUTHORITY,
                    DependencyReadinessKind.WORKER,
                    DependencyReadinessKind.HTTP_API,
                    DependencyReadinessKind.MCP_STREAMABLE_HTTP,
                    DependencyReadinessKind.OBSERVATION,
                )
            ),
            http_api=_http_api(),
            mcp=mcp,
        ),
        program=_program(),
        unit_of_work=_uow(),
        projection_parity=projection_parity,
        command_parity=command_parity,
        security_parity=operator_adapter_security_parity(
            projection_parity=projection_parity,
            command_parity=command_parity,
        ),
    )


class CpkServerEntrypointHandoffTests(unittest.TestCase):
    def test_handoff_names_external_server_owner_and_core_contracts(self) -> None:
        handoff = _handoff()

        self.assertEqual(
            handoff.implementation_package,
            "control-plane-kit-servers/cpk-server",
        )
        self.assertEqual(
            handoff.composition_policy,
            EntrypointCompositionPolicy.ONE_DEPLOYMENT_PROGRAM,
        )
        self.assertEqual(
            handoff.state_policy,
            ProcessStatePolicy.PROCESS_GLOBALS_ARE_NOT_TRUTH,
        )
        self.assertEqual(handoff.import_direction, "cpk-server-imports-core")
        self.assertIs(handoff.process.http_api, handoff.http_api)

    def test_descriptor_is_closed_and_round_trips_without_process_packaging(self) -> None:
        handoff = _handoff()
        descriptor = handoff.descriptor()

        self.assertEqual(descriptor["kind"], "cpk-server-entrypoint-handoff")
        self.assertEqual(
            CpkServerEntrypointHandoffContract.from_descriptor(descriptor),
            handoff,
        )

        rendered = repr(descriptor).lower()
        self.assertNotIn("fastapi", rendered)
        self.assertNotIn("uvicorn", rendered)
        self.assertNotIn("dockerfile", rendered)
        self.assertNotIn("oci-image", rendered)

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerEntrypointHandoffContract.from_descriptor(
                {**descriptor, "extra": True}
            )

    def test_process_http_api_must_cover_read_and_command_routes(self) -> None:
        handoff = _handoff()

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerEntrypointHandoffContract(
                process=ControlPlaneProcessContract(
                    http_api=HttpApiContract(operator_read_http_routes()),
                    mcp=handoff.mcp,
                ),
                program=handoff.program,
                unit_of_work=handoff.unit_of_work,
                projection_parity=handoff.projection_parity,
                command_parity=handoff.command_parity,
                security_parity=handoff.security_parity,
            )

    def test_handoff_rejects_process_global_truth_and_wrong_owner(self) -> None:
        handoff = _handoff()

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerEntrypointHandoffContract(
                process=handoff.process,
                program=handoff.program,
                unit_of_work=handoff.unit_of_work,
                projection_parity=handoff.projection_parity,
                command_parity=handoff.command_parity,
                security_parity=handoff.security_parity,
                state_policy=ProcessStatePolicy.PROCESS_GLOBALS_OWN_TRUTH,
            )

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerEntrypointHandoffContract(
                process=handoff.process,
                program=handoff.program,
                unit_of_work=handoff.unit_of_work,
                projection_parity=handoff.projection_parity,
                command_parity=handoff.command_parity,
                security_parity=handoff.security_parity,
                implementation_package="control-plane-kit-core",
            )


if __name__ == "__main__":
    unittest.main()
