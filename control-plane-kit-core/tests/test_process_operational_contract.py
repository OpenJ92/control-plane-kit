import unittest

from control_plane_kit_core.operations import (
    ControlPlaneProcessContract,
    DependencyReadinessKind,
    HttpApiContract,
    HttpStatusProbeContract,
    InvalidProcessOperationalContract,
    McpStreamableHttpContract,
    ObservationHandoffContract,
    ProcessEndpointKind,
    ReadinessDependency,
    ShutdownContract,
    operator_read_http_routes,
)
from control_plane_kit_core.verification import HttpCheck, VerificationContract


class ProcessOperationalContractTests(unittest.TestCase):
    def test_liveness_and_readiness_are_distinct_endpoint_contracts(self) -> None:
        liveness = HttpStatusProbeContract.liveness()
        readiness = HttpStatusProbeContract.readiness()

        self.assertEqual(liveness.kind, ProcessEndpointKind.LIVENESS)
        self.assertEqual(readiness.kind, ProcessEndpointKind.READINESS)
        self.assertEqual(liveness.path, "/health/live")
        self.assertEqual(readiness.path, "/health/ready")
        self.assertTrue(liveness.public)
        self.assertFalse(readiness.public)
        self.assertFalse(liveness.reveals_sensitive_state)

    def test_process_contract_names_required_dependencies_without_hosting_process(self) -> None:
        contract = _contract()

        self.assertEqual(
            [dependency.kind for dependency in contract.dependencies],
            [
                DependencyReadinessKind.STORE,
                DependencyReadinessKind.RUNTIME_AUTHORITY,
                DependencyReadinessKind.WORKER,
                DependencyReadinessKind.HTTP_API,
                DependencyReadinessKind.MCP_STREAMABLE_HTTP,
                DependencyReadinessKind.OBSERVATION,
            ],
        )
        self.assertIsInstance(contract.http_api, HttpApiContract)
        self.assertIsInstance(contract.mcp, McpStreamableHttpContract)

    def test_descriptor_is_closed_bounded_and_round_trips(self) -> None:
        contract = _contract()
        descriptor = contract.descriptor()

        self.assertEqual(descriptor["kind"], "control-plane-process-contract")
        self.assertEqual(descriptor["liveness"]["path"], "/health/live")
        self.assertEqual(descriptor["readiness"]["path"], "/health/ready")
        self.assertEqual(
            descriptor["observation"],
            {
                "projection": "append-only",
                "graph_truth_policy": "never-rewrite-desired-graph",
                "maximum_evidence_bytes": 16384,
            },
        )
        self.assertEqual(
            descriptor["shutdown"],
            {
                "graceful_timeout_seconds": 30.0,
                "retained_data_policy": "preserve-retained-data",
                "records_observation": True,
            },
        )
        self.assertEqual(ControlPlaneProcessContract.from_descriptor(descriptor), contract)

        with self.assertRaises(InvalidProcessOperationalContract):
            ControlPlaneProcessContract.from_descriptor({**descriptor, "extra": True})

    def test_invalid_readiness_dependencies_and_secret_like_evidence_fail_closed(self) -> None:
        with self.assertRaises(InvalidProcessOperationalContract):
            ReadinessDependency(
                DependencyReadinessKind.STORE,
                evidence_key="database_password",
            )

        with self.assertRaises(InvalidProcessOperationalContract):
            ControlPlaneProcessContract(
                liveness=HttpStatusProbeContract.liveness(),
                readiness=HttpStatusProbeContract.liveness(),
                dependencies=_dependencies(),
            )

    def test_readiness_requires_matching_http_and_mcp_contracts(self) -> None:
        with self.assertRaises(InvalidProcessOperationalContract):
            ControlPlaneProcessContract(
                dependencies=_dependencies(),
                http_api=None,
                mcp=McpStreamableHttpContract(),
            )

        with self.assertRaises(InvalidProcessOperationalContract):
            ControlPlaneProcessContract(
                dependencies=_dependencies(),
                http_api=HttpApiContract(operator_read_http_routes()),
                mcp=None,
            )

    def test_shutdown_preserves_retained_data_and_records_observation(self) -> None:
        with self.assertRaises(InvalidProcessOperationalContract):
            ShutdownContract(retained_data_policy="delete-retained-data")

        with self.assertRaises(InvalidProcessOperationalContract):
            ShutdownContract(records_observation=False)

    def test_contract_does_not_smuggle_process_or_secret_state(self) -> None:
        descriptor = _contract().descriptor()
        rendered = repr(descriptor).lower()

        self.assertNotIn("fastapi", rendered)
        self.assertNotIn("uvicorn", rendered)
        self.assertNotIn("dockerfile", rendered)
        self.assertNotIn("token", rendered)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("password", rendered)


def _contract() -> ControlPlaneProcessContract:
    return ControlPlaneProcessContract(
        dependencies=_dependencies(),
        http_api=HttpApiContract(operator_read_http_routes()),
        mcp=McpStreamableHttpContract(),
        verification=VerificationContract(
            (
                HttpCheck(
                    check_id="operator-api-ready",
                    provider_socket="operator-api",
                    path="/health/ready",
                ),
            )
        ),
    )


def _dependencies() -> tuple[ReadinessDependency, ...]:
    return (
        ReadinessDependency(DependencyReadinessKind.STORE),
        ReadinessDependency(DependencyReadinessKind.RUNTIME_AUTHORITY),
        ReadinessDependency(DependencyReadinessKind.WORKER),
        ReadinessDependency(DependencyReadinessKind.HTTP_API),
        ReadinessDependency(DependencyReadinessKind.MCP_STREAMABLE_HTTP),
        ReadinessDependency(DependencyReadinessKind.OBSERVATION),
    )


if __name__ == "__main__":
    unittest.main()
