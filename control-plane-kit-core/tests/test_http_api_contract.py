import unittest

from control_plane_kit_core.operations import (
    ControlPlaneServiceRole,
    HttpApiContract,
    HttpApiRouteContract,
    HttpAuthScope,
    HttpErrorContract,
    HttpMethod,
    HttpOperationSafety,
    HttpSchemaRef,
    InvalidHttpApiContract,
    operator_read_http_routes,
)


class HttpApiContractTests(unittest.TestCase):
    def test_operator_read_routes_preserve_frozen_route_inventory(self) -> None:
        contract = HttpApiContract(operator_read_http_routes())

        self.assertEqual(
            [(route.method.value, route.path_template) for route in contract.routes],
            [
                ("GET", "/workspaces/{workspace_id}"),
                ("GET", "/workspaces/{workspace_id}/activity"),
                ("GET", "/workspaces/{workspace_id}/approvals/pending"),
                ("GET", "/workspaces/{workspace_id}/approvals/{approval_id}"),
                ("GET", "/workspaces/{workspace_id}/control-surface"),
                ("GET", "/workspaces/{workspace_id}/graphs/current"),
                ("GET", "/workspaces/{workspace_id}/graphs/desired"),
                ("GET", "/workspaces/{workspace_id}/observed-state"),
                ("GET", "/workspaces/{workspace_id}/operator-graph"),
                ("GET", "/workspaces/{workspace_id}/plans/{plan_id}"),
                ("GET", "/workspaces/{workspace_id}/sessions"),
                ("GET", "/workspaces/{workspace_id}/sessions/{session_id}"),
            ],
        )
        self.assertEqual(
            {route.service_role for route in contract.routes},
            {ControlPlaneServiceRole.READS},
        )
        self.assertEqual(
            {route.safety for route in contract.routes},
            {HttpOperationSafety.READ_ONLY},
        )

    def test_route_contract_names_service_auth_safety_and_bounded_shapes(self) -> None:
        route = HttpApiRouteContract(
            route_id="planning.create-plan",
            method=HttpMethod.POST,
            path_template="/workspaces/{workspace_id}/plans",
            service_role=ControlPlaneServiceRole.PLANNING,
            auth_scope=HttpAuthScope.PLAN_WRITE,
            safety=HttpOperationSafety.COMMAND,
            request_schema=HttpSchemaRef("PlanTransitionRequest", max_bytes=65536),
            response_schema=HttpSchemaRef("PlanPreparedResponse", max_bytes=65536),
        )

        self.assertEqual(
            route.descriptor(),
            {
                "route_id": "planning.create-plan",
                "method": "POST",
                "path_template": "/workspaces/{workspace_id}/plans",
                "service_role": "planning",
                "auth_scope": "plan:write",
                "safety": "command",
                "request_schema": {
                    "name": "PlanTransitionRequest",
                    "max_bytes": 65536,
                },
                "response_schema": {
                    "name": "PlanPreparedResponse",
                    "max_bytes": 65536,
                },
                "errors": {
                    "statuses": [400, 401, 403, 404, 409, 422, 503],
                    "schema": {"name": "BoundedError", "max_bytes": 8192},
                },
            },
        )
        self.assertEqual(
            HttpApiRouteContract.from_descriptor(route.descriptor()),
            route,
        )

    def test_contract_descriptor_is_closed_deterministic_and_round_trips(self) -> None:
        contract = HttpApiContract(tuple(reversed(operator_read_http_routes())))
        descriptor = contract.descriptor()

        self.assertEqual(descriptor["kind"], "http-api-contract")
        self.assertEqual(
            [route["path_template"] for route in descriptor["routes"]],
            [
                "/workspaces/{workspace_id}",
                "/workspaces/{workspace_id}/activity",
                "/workspaces/{workspace_id}/approvals/pending",
                "/workspaces/{workspace_id}/approvals/{approval_id}",
                "/workspaces/{workspace_id}/control-surface",
                "/workspaces/{workspace_id}/graphs/current",
                "/workspaces/{workspace_id}/graphs/desired",
                "/workspaces/{workspace_id}/observed-state",
                "/workspaces/{workspace_id}/operator-graph",
                "/workspaces/{workspace_id}/plans/{plan_id}",
                "/workspaces/{workspace_id}/sessions",
                "/workspaces/{workspace_id}/sessions/{session_id}",
            ],
        )
        self.assertEqual(HttpApiContract.from_descriptor(descriptor), contract)

        with self.assertRaises(InvalidHttpApiContract):
            HttpApiContract.from_descriptor({**descriptor, "extra": True})

    def test_routes_fail_closed_for_invalid_identity_paths_and_duplicates(self) -> None:
        with self.assertRaises(InvalidHttpApiContract):
            HttpApiRouteContract(
                route_id="bad path",
                method=HttpMethod.GET,
                path_template="/workspaces/{workspace_id}",
                service_role=ControlPlaneServiceRole.READS,
                auth_scope=HttpAuthScope.READ,
                safety=HttpOperationSafety.READ_ONLY,
            )

        with self.assertRaises(InvalidHttpApiContract):
            HttpApiRouteContract(
                route_id="read.workspace",
                method=HttpMethod.GET,
                path_template="/workspaces/{workspace_id}?debug=true",
                service_role=ControlPlaneServiceRole.READS,
                auth_scope=HttpAuthScope.READ,
                safety=HttpOperationSafety.READ_ONLY,
            )

        route = operator_read_http_routes()[0]
        with self.assertRaises(InvalidHttpApiContract):
            HttpApiContract((route, route))

    def test_safety_and_scope_are_consistent_with_service_role(self) -> None:
        with self.assertRaises(InvalidHttpApiContract):
            HttpApiRouteContract(
                route_id="read.bad",
                method=HttpMethod.POST,
                path_template="/workspaces/{workspace_id}",
                service_role=ControlPlaneServiceRole.READS,
                auth_scope=HttpAuthScope.READ,
                safety=HttpOperationSafety.READ_ONLY,
            )

        with self.assertRaises(InvalidHttpApiContract):
            HttpApiRouteContract(
                route_id="execution.bad",
                method=HttpMethod.POST,
                path_template="/runs/{run_id}/execution",
                service_role=ControlPlaneServiceRole.EXECUTION,
                auth_scope=HttpAuthScope.PLAN_WRITE,
                safety=HttpOperationSafety.DESTRUCTIVE,
            )

    def test_error_contract_rejects_success_statuses_and_unknown_shape(self) -> None:
        with self.assertRaises(InvalidHttpApiContract):
            HttpErrorContract(statuses=(200,))

        descriptor = HttpErrorContract().descriptor()
        with self.assertRaises(InvalidHttpApiContract):
            HttpErrorContract.from_descriptor({**descriptor, "headers": []})

    def test_http_contract_does_not_smuggle_process_or_mcp_descriptor_state(self) -> None:
        descriptor = HttpApiContract(operator_read_http_routes()).descriptor()
        rendered = repr(descriptor).lower()

        self.assertNotIn("fastapi", rendered)
        self.assertNotIn("uvicorn", rendered)
        self.assertNotIn("dockerfile", rendered)
        self.assertNotIn("mcp-streamable-http", rendered)


if __name__ == "__main__":
    unittest.main()
