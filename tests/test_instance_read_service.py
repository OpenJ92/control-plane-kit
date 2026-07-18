from datetime import datetime, timezone
from unittest import main

from control_plane_kit import (
    ActivityId,
    ActivityPlan,
    ActivityEventKind,
    ActivityRunStatus,
    AdmittedRun,
    BoundedEvidence,
    BlockSockets,
    BlockSpec,
    CapabilityName,
    DeploymentRecipe,
    DockerRuntime,
    PlanOnlyImplementation,
    PlannedActivity,
    ObservationFreshness,
    EndpointContext,
    ProbeKind,
    ProbeOutcome,
    ObservationStatus,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    ExecutionRequestStatus,
    RetryIdentity,
    Protocol,
    ProxyBlock,
    ProviderSocket,
    RequirementSocket,
    RiskLevel,
    NodeTarget,
    SocketBinding,
    SocketConnection,
    StartNode,
    compile_recipe,
)
from control_plane_kit.topology.graph import DeploymentGraph
from control_plane_kit.read_services import InstanceReadService, ReadModelError
from control_plane_kit.stores import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    GraphVersionRecord,
    ObservationRecord,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    WorkspaceRecord,
)
from examples.app_with_postgres import recipe
from tests.postgres_case import PostgresStoreTestCase


class InstanceReadServiceTests(PostgresStoreTestCase):
    def test_workspace_read_model_includes_current_and_desired_graphs(self):
        service = self._service_with_workspace_and_graphs()

        payload = service.workspace("workspace-a").descriptor()

        self.assertEqual(payload["workspace"]["workspace_id"], "workspace-a")
        self.assertEqual(payload["current_graph"]["graph_id"], "graph-current")
        self.assertEqual(payload["current_graph"]["graph_name"], "current")
        self.assertEqual(payload["desired_graph"]["graph_id"], "graph-desired")
        self.assertEqual(payload["desired_graph"]["graph_name"], "desired")

    def test_graph_descriptor_redacts_addresses_and_environment_values(self):
        service = self._service_with_workspace_and_graphs()

        payload = service.current_graph("workspace-a").descriptor()

        descriptor = payload["graph_descriptor"]
        postgres = descriptor["nodes"]["postgres"]
        api = descriptor["nodes"]["orders-api"]
        edge = descriptor["edges"]["postgres.internal-to-orders-api.DATABASE_URL"]
        self.assertEqual(postgres["endpoints"]["internal"]["address"], "<redacted>")
        self.assertEqual(api["environment"], "<redacted>")
        self.assertEqual(edge["env_assignments"], "<redacted>")
        self.assertNotIn("postgres:postgres", str(descriptor))

    def test_missing_workspace_fails_at_service_boundary(self):
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
        )

        with self.assertRaisesRegex(ReadModelError, "missing workspace 'missing'"):
            service.workspace("missing")

    def test_unassigned_graph_pointers_are_explicit(self):
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
        )

        payload = service.workspace("workspace-a").descriptor()

        self.assertEqual(payload["current_graph"]["pointer"], "current")
        self.assertFalse(payload["current_graph"]["assigned"])
        self.assertEqual(payload["desired_graph"]["pointer"], "desired")
        self.assertFalse(payload["desired_graph"]["assigned"])

    def test_operator_graph_uses_shared_projection(self):
        service = self._service_with_workspace_and_graphs()

        payload = service.operator_graph("workspace-a").descriptor()

        self.assertTrue(payload["assigned"])
        self.assertIn("operator_graph", payload)
        self.assertEqual(payload["operator_graph"]["name"], "current")
        self.assertEqual(
            [edge["edge_id"] for edge in payload["operator_graph"]["edges"]],
            ["postgres.internal-to-orders-api.DATABASE_URL"],
        )

    def test_unknown_graph_pointer_fails_loudly(self):
        service = self._service_with_workspace_and_graphs()

        with self.assertRaisesRegex(ReadModelError, "unknown graph pointer 'future'"):
            service.operator_graph("workspace-a", pointer="future")

    def test_activity_timeline_is_bounded_and_redacted(self):
        service = self._service_with_activity()

        payload = service.activity_timeline("workspace-a", limit=1).descriptor()

        self.assertEqual(payload["limit"], 1)
        self.assertEqual(len(payload["sessions"]), 1)
        session = payload["sessions"][0]
        self.assertEqual([action["action_id"] for action in session["actions"]], ["action-a"])
        self.assertEqual(session["actions"][0]["payload"]["api_token"], "<redacted>")
        self.assertEqual(
            session["plans"][0]["payload"]["schema"],
            "control-plane-kit.activity-plan",
        )
        event_payload = session["plans"][0]["runs"][0]["events"][0]["payload"]
        self.assertEqual(event_payload["target"], "api")
        self.assertNotIn("password", event_payload)

    def test_activity_timeline_rejects_invalid_limits(self):
        service = self._service_with_activity()

        with self.assertRaisesRegex(ReadModelError, "limit must be positive, got 0"):
            service.activity_timeline("workspace-a", limit=0)

    def test_activity_timeline_requires_workspace_truth(self):
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            execution_store=self.stores.execution,
            observed_state_store=self.stores.observed_state,
        )

        with self.assertRaisesRegex(ReadModelError, "missing workspace 'missing'"):
            service.activity_timeline("missing")

    def test_activity_timeline_requires_configured_activity_store(self):
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
        )

        with self.assertRaisesRegex(ReadModelError, "activity history store is not configured"):
            service.activity_timeline("workspace-a")

    def test_activity_timeline_does_not_cross_workspace_boundary(self):
        service = self._service_with_activity()

        payload = service.activity_timeline("workspace-a", limit=10).descriptor()

        self.assertEqual([session["session_id"] for session in payload["sessions"]], ["session-a"])
        self.assertNotIn("session-other", str(payload))

    def test_observed_state_reports_latest_and_stale_markers(self):
        service = self._service_with_observations()

        payload = service.observed_state("workspace-a").descriptor()

        self.assertEqual(
            [(record["subject_id"], record["status"], record["stale"]) for record in payload["observations"]],
            [("api", "healthy", False), ("router", "unknown", True)],
        )
        self.assertEqual(
            payload["observations"][0]["payload"]["callback_url"],
            "<redacted>",
        )
        self.assertEqual(payload["observations"][1]["payload"]["details"], "not checked yet")

    def test_observed_state_requires_configured_observed_state_store(self):
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
        )

        with self.assertRaisesRegex(ReadModelError, "observed state store is not configured"):
            service.observed_state("workspace-a")

    def test_observed_state_does_not_cross_workspace_boundary(self):
        service = self._service_with_observations()

        payload = service.observed_state("workspace-a").descriptor()

        self.assertNotIn("workspace-b", str(payload))
        self.assertNotIn("other-api", str(payload))

    def test_nested_payload_redaction_reaches_lists_and_mappings(self):
        service = self._service_with_nested_payloads()

        payload = service.activity_timeline("workspace-a").descriptor()

        events = payload["sessions"][0]["plans"][0]["runs"][0]["events"]
        event_payload = next(
            event["payload"]
            for event in events
            if event["event_type"] == ActivityEventKind.STEP_SUCCEEDED.value
        )
        self.assertEqual(event_payload["nested"]["label"], "visible")
        self.assertEqual(event_payload["items"][0]["callback_url"], "<redacted>")
        self.assertEqual(event_payload["items"][0]["label"], "visible")

    def test_control_surface_lists_declared_capabilities_routes_and_sockets(self):
        service = self._service_with_control_surface()

        payload = service.control_surface("workspace-a").descriptor()

        self.assertTrue(payload["assigned"])
        router = _node(payload, "api-router")
        self.assertEqual(router["display_name"], "API Router")
        self.assertEqual(
            [capability["name"] for capability in router["capabilities"]],
            ["health-checkable", "target-mutable", "switchable", "drainable"],
        )
        self.assertEqual(
            [route_set["name"] for route_set in router["control_route_sets"]],
            ["common-status", "targets"],
        )
        target_routes = _route_set(router, "targets")["routes"]
        self.assertIn(
            {
                "name": "active-target",
                "method": "POST",
                "path": "/__deploy/active-target",
                "scope": "signal:send",
                "description": "Switch the active downstream target.",
            },
            target_routes,
        )
        self.assertEqual(router["providers"]["internal"]["protocol"], "http")
        self.assertEqual(
            router["requirements"]["active"],
            {
                "protocol": "http",
                "binding": "environment",
                "env_bindings": ["ACTIVE_TARGET_URL"],
                "required": True,
            },
        )

    def test_control_surface_distinguishes_runtime_control_from_environment_binding(self):
        service = self._service_with_control_surface_descriptor(
            _control_surface_graph(SocketBinding.RUNTIME_CONTROL).descriptor()
        )

        router = _node(service.control_surface("workspace-a").descriptor(), "api-router")

        self.assertEqual(
            router["requirements"]["active"],
            {
                "protocol": "http",
                "binding": "runtime-control",
                "env_bindings": [],
                "required": True,
            },
        )

    def test_control_surface_redacts_address_metadata_but_keeps_labels(self):
        service = self._service_with_control_surface()

        router = _node(service.control_surface("workspace-a").descriptor(), "api-router")

        self.assertEqual(router["metadata"]["dashboard_url"], "<redacted>")
        self.assertEqual(router["metadata"]["label"], "visible")
        self.assertNotIn("http://private", str(router))

    def test_control_surface_warns_on_unknown_route_sets(self):
        service = self._service_with_control_surface_descriptor(_control_surface_descriptor_with_unknown_route_set())

        router = _node(service.control_surface("workspace-a").descriptor(), "api-router")

        self.assertEqual(router["warnings"], ["unknown control route set 'legacy-magic'"])
        self.assertEqual(
            [route_set["name"] for route_set in router["control_route_sets"]],
            ["common-status", "targets"],
        )

    def test_control_surface_unassigned_pointer_is_explicit(self):
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
        )

        payload = service.control_surface("workspace-a").descriptor()

        self.assertFalse(payload["assigned"])
        self.assertEqual(payload["nodes"], [])

    def test_control_surface_rejects_unknown_pointer(self):
        service = self._service_with_control_surface()

        with self.assertRaisesRegex(ReadModelError, "unknown graph pointer 'future'"):
            service.control_surface("workspace-a", pointer="future")

    def _service_with_workspace_and_graphs(self) -> InstanceReadService:
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        current = GraphVersionRecord.from_graph(
            graph_id="graph-current",
            workspace_id="workspace-a",
            version=1,
            graph=_compiled_graph_named("current"),
            created_by="jacob",
            created_at="2026-07-15T00:00:00Z",
        )
        desired = GraphVersionRecord(
            graph_id="graph-desired",
            workspace_id="workspace-a",
            version=2,
            graph_descriptor=DeploymentGraph("desired").descriptor(),
            created_by="jacob",
            created_at="2026-07-15T00:01:00Z",
        )
        self.stores.graph_topology.save(current)
        self.stores.graph_topology.save(desired)
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")
        self.stores.workspace.set_desired_graph("workspace-a", "graph-desired")
        return InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            execution_store=self.stores.execution,
            observed_state_store=self.stores.observed_state,
        )

    def _service_with_activity(self) -> InstanceReadService:
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-b", name="Other"))
        self.stores.activity_history.add_session(
            OperationSessionRecord(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="jacob",
                title="Swap API",
                status=OperationSessionStatus.OPEN,
                created_at="2026-07-15T00:00:00Z",
            )
        )
        self.stores.activity_history.add_session(
            OperationSessionRecord(
                session_id="session-other",
                workspace_id="workspace-b",
                actor_id="jacob",
                title="Other workspace",
                status=OperationSessionStatus.OPEN,
                created_at="2026-07-15T00:00:01Z",
            )
        )
        self.stores.activity_history.add_action(
            OperationActionRecord(
                action_id="action-a",
                session_id="session-a",
                ordinal=1,
                action_type=OperationActionKind.PATCH_VARIABLE,
                actor_id="jacob",
                payload={"api_token": "secret", "note": "visible"},
                created_at="2026-07-15T00:01:00Z",
            )
        )
        self.stores.activity_history.add_action(
            OperationActionRecord(
                action_id="action-b",
                session_id="session-a",
                ordinal=2,
                action_type=OperationActionKind.CHECK_HEALTH,
                actor_id="jacob",
                payload={"note": "bounded away"},
                created_at="2026-07-15T00:02:00Z",
            )
        )
        self.stores.activity_history.add_plan(
            ActivityPlanRecord(
                plan_id="plan-a",
                session_id="session-a",
                base_graph_id="graph-a",
                desired_graph_id="graph-b",
                status="planned",
                created_at="2026-07-15T00:03:00Z",
                plan=_start_api_plan(),
            )
        )
        self.stores.activity_history.add_approval_request(
            ApprovalRequestRecord(
                request_id="approval-request-a",
                session_id="session-a",
                plan_id="plan-a",
                requested_by="jacob",
                requested_at="2026-07-15T00:02:15Z",
                required_scope="plan:approve",
                max_risk=RiskLevel.LOW,
                destructive=False,
            )
        )
        self.stores.activity_history.add_approval_decision(
            ApprovalDecisionRecord(
                decision_id="approval-decision-a",
                request_id="approval-request-a",
                actor_id="manager",
                decision=ApprovalDecisionKind.APPROVED,
                scope="plan:approve",
                decided_at="2026-07-15T00:02:30Z",
            )
        )
        self.stores.execution.add_request(
            ExecutionRequestRecord(
                identity=ExecutionRequestIdentity(
                    "execution-request-a", "workspace-a", "session-a", "plan-a"
                ),
                status=ExecutionRequestStatus.QUEUED,
                requested_by="jacob",
                requested_at="2026-07-15T00:03:00Z",
                approval_request_id="approval-request-a",
                approval_decision_id="approval-decision-a",
                idempotency=ExecutionIdempotency("execute-a", "fingerprint-a"),
            )
        )
        self.stores.execution.add_run(
            ActivityRunRecord(
                run_id="run-a",
                plan_id="plan-a",
                admission=AdmittedRun("execution-request-a"),
                retry=RetryIdentity(1),
                status=ActivityRunStatus.RUNNING,
                created_at="2026-07-15T00:04:00Z",
                started_at="2026-07-15T00:04:00Z",
                metadata=BoundedEvidence.from_mapping({"worker": "agent-a"}),
            )
        )
        self.stores.execution.add_event(
            ActivityEventRecord(
                event_id="event-a",
                run_id="run-a",
                ordinal=1,
                kind=ActivityEventKind.STEP_STARTED,
                occurred_at="2026-07-15T00:05:00Z",
                activity_id="start-api",
                evidence=BoundedEvidence.from_mapping({"target": "api"}),
            )
        )
        return InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            execution_store=self.stores.execution,
            observed_state_store=self.stores.observed_state,
        )

    def _service_with_observations(self) -> InstanceReadService:
        self.stores.workspace.create(
            WorkspaceRecord(
                workspace_id="workspace-a",
                name="Demo",
                current_graph_id="graph-current",
            )
        )
        self.stores.graph_topology.save(
            GraphVersionRecord(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph_descriptor=DeploymentGraph("current").descriptor(),
                created_by="jacob",
                created_at="2026-07-15T00:00:00Z",
            )
        )
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-b", name="Other"))
        self.stores.observed_state.put(
            ObservationRecord(
                observation_id="obs-api-old",
                workspace_id="workspace-a",
                subject_id="api",
                status=ObservationStatus.STARTING,
                observed_at="2026-07-15T00:00:00Z",
                graph_id="graph-current",
                probe_kind=ProbeKind.PROCESS,
                probe_outcome=ProbeOutcome.PROCESS_RUNNING,
            )
        )
        self.stores.observed_state.put(
            ObservationRecord(
                observation_id="obs-api-new",
                workspace_id="workspace-a",
                subject_id="api",
                status=ObservationStatus.HEALTHY,
                observed_at="2026-07-15T00:01:00Z",
                evidence=BoundedEvidence.from_mapping(
                    {"callback_url": "http://private"}
                ),
                graph_id="graph-current",
                probe_kind=ProbeKind.APPLICATION_HEALTH,
                probe_outcome=ProbeOutcome.HEALTHY,
                endpoint_context=EndpointContext.RUNTIME_PRIVATE,
            )
        )
        self.stores.observed_state.put(
            ObservationRecord(
                observation_id="obs-router",
                workspace_id="workspace-a",
                subject_id="router",
                status=ObservationStatus.UNKNOWN,
                observed_at="2026-07-15T00:01:00Z",
                evidence=BoundedEvidence.from_mapping(
                    {"details": "not checked yet"}
                ),
                freshness=ObservationFreshness.STALE,
            )
        )
        self.stores.observed_state.put(
            ObservationRecord(
                observation_id="obs-other",
                workspace_id="workspace-b",
                subject_id="other-api",
                status=ObservationStatus.HEALTHY,
                observed_at="2026-07-15T00:02:00Z",
            )
        )
        return InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            execution_store=self.stores.execution,
            observed_state_store=self.stores.observed_state,
            clock=lambda: datetime(2026, 7, 15, 0, 2, tzinfo=timezone.utc),
        )

    def _service_with_nested_payloads(self) -> InstanceReadService:
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        self.stores.activity_history.add_session(
            OperationSessionRecord(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="jacob",
                title="Nested",
                status=OperationSessionStatus.OPEN,
                created_at="2026-07-15T00:00:00Z",
            )
        )
        self.stores.activity_history.add_plan(
            ActivityPlanRecord(
                plan_id="plan-a",
                session_id="session-a",
                base_graph_id="graph-a",
                desired_graph_id="graph-b",
                status="planned",
                created_at="2026-07-15T00:01:00Z",
                plan=_start_api_plan(),
            )
        )
        self.stores.activity_history.add_approval_request(
            ApprovalRequestRecord(
                request_id="approval-request-a",
                session_id="session-a",
                plan_id="plan-a",
                requested_by="jacob",
                requested_at="2026-07-15T00:01:15Z",
                required_scope="plan:approve",
                max_risk=RiskLevel.LOW,
                destructive=False,
            )
        )
        self.stores.activity_history.add_approval_decision(
            ApprovalDecisionRecord(
                decision_id="approval-decision-a",
                request_id="approval-request-a",
                actor_id="manager",
                decision=ApprovalDecisionKind.APPROVED,
                scope="plan:approve",
                decided_at="2026-07-15T00:01:30Z",
            )
        )
        self.stores.execution.add_request(
            ExecutionRequestRecord(
                identity=ExecutionRequestIdentity(
                    "execution-request-a", "workspace-a", "session-a", "plan-a"
                ),
                status=ExecutionRequestStatus.QUEUED,
                requested_by="jacob",
                requested_at="2026-07-15T00:01:45Z",
                approval_request_id="approval-request-a",
                approval_decision_id="approval-decision-a",
                idempotency=ExecutionIdempotency("execute-a", "fingerprint-a"),
            )
        )
        self.stores.execution.add_run(
            ActivityRunRecord(
                run_id="run-a",
                plan_id="plan-a",
                admission=AdmittedRun("execution-request-a"),
                retry=RetryIdentity(1),
                status=ActivityRunStatus.RUNNING,
                created_at="2026-07-15T00:02:00Z",
                started_at="2026-07-15T00:02:00Z",
            )
        )
        self.stores.execution.add_event(
            ActivityEventRecord(
                event_id="event-a",
                run_id="run-a",
                ordinal=1,
                kind=ActivityEventKind.STEP_STARTED,
                occurred_at="2026-07-15T00:02:30Z",
                activity_id="start-api",
            )
        )
        self.stores.execution.add_event(
            ActivityEventRecord(
                event_id="event-b",
                run_id="run-a",
                ordinal=2,
                kind=ActivityEventKind.STEP_SUCCEEDED,
                occurred_at="2026-07-15T00:03:00Z",
                activity_id="start-api",
                evidence=BoundedEvidence.from_mapping({
                    "nested": {"label": "visible"},
                    "items": [{"callback_url": "http://private", "label": "visible"}],
                }),
            )
        )
        return InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            execution_store=self.stores.execution,
            observed_state_store=self.stores.observed_state,
        )

    def _service_with_control_surface(self) -> InstanceReadService:
        return self._service_with_control_surface_descriptor(_control_surface_graph().descriptor())

    def _service_with_control_surface_descriptor(self, graph_descriptor: dict[str, object]) -> InstanceReadService:
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        self.stores.graph_topology.save(
            GraphVersionRecord(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph_descriptor=graph_descriptor,
                created_by="jacob",
                created_at="2026-07-15T00:00:00Z",
            )
        )
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")
        return InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            execution_store=self.stores.execution,
            observed_state_store=self.stores.observed_state,
        )


def _start_api_plan() -> ActivityPlan:
    return ActivityPlan((
        PlannedActivity(
            ActivityId("start-api"),
            StartNode(NodeTarget("api")),
        ),
    ))


def _compiled_graph_named(name: str) -> DeploymentGraph:
    graph = compile_recipe(recipe())
    return DeploymentGraph(
        name=name,
        nodes=graph.nodes,
        edges=graph.edges,
        runtimes=graph.runtimes,
    )


def _control_surface_graph(
    binding: SocketBinding = SocketBinding.ENVIRONMENT,
) -> DeploymentGraph:
    target = ProxyBlock(
        spec=BlockSpec("api-v1", display_name="API v1"),
        implementation=PlanOnlyImplementation(kind="plan-api", output_urls={"internal": "http://api-v1:8080"}),
        sockets=BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    router = ProxyBlock(
        spec=BlockSpec(
            "api-router",
            display_name="API Router",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.TARGET_MUTABLE,
                CapabilityName.SWITCHABLE,
                CapabilityName.DRAINABLE,
            ),
            metadata={"dashboard_url": "http://private-dashboard", "label": "visible"},
        ),
        implementation=PlanOnlyImplementation(kind="plan-router", output_urls={"internal": "http://router:8080"}),
        sockets=BlockSockets(
            requirements=(
                RequirementSocket(
                    "active",
                    Protocol.HTTP,
                    ("ACTIVE_TARGET_URL",)
                    if binding is SocketBinding.ENVIRONMENT
                    else (),
                    binding=binding,
                ),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    return compile_recipe(
        DeploymentRecipe(
            "control-surface",
            DockerRuntime(
                children=(
                    target,
                    router,
                    SocketConnection("api-v1", "internal", "api-router", "active"),
                )
            ),
        )
    )


def _control_surface_descriptor_with_unknown_route_set() -> dict[str, object]:
    descriptor = _control_surface_graph().descriptor()
    router = descriptor["nodes"]["api-router"]
    metadata = router["metadata"]
    metadata["capabilities"] = [
        *metadata["capabilities"],
        {
            "name": "legacy",
            "label": "Legacy",
            "description": "Old descriptor data from a previous package version.",
            "route_set": "legacy-magic",
        },
    ]
    return descriptor


def _node(payload: dict[str, object], node_id: str) -> dict[str, object]:
    for node in payload["nodes"]:
        if node["node_id"] == node_id:
            return node
    raise AssertionError(f"missing node {node_id!r}")


def _route_set(node: dict[str, object], name: str) -> dict[str, object]:
    for route_set in node["control_route_sets"]:
        if route_set["name"] == name:
            return route_set
    raise AssertionError(f"missing route set {name!r}")


if __name__ == "__main__":
    main()
