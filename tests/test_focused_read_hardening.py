from io import StringIO
import json
from datetime import datetime, timezone
from unittest import main

from fastapi.testclient import TestClient

from control_plane_kit import (
    ActivityEventKind,
    ActivityDependency,
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    ActivityRunStatus,
    AdmittedRun,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    ExecutionRequestStatus,
    ExecutionValueError,
    FailureCategory,
    FailureEvidence,
    PlannedActivity,
    RemoveRuntimeResource,
    RetryIdentity,
    RiskLevel,
    RuntimeTarget,
    StartRuntime,
)
from control_plane_kit.cli import run as run_cli
from control_plane_kit.topology.graph import DeploymentGraph
from control_plane_kit.mcp_read import McpReadError, ReadOnlyMcpAdapter
from control_plane_kit.read_services import InstanceReadService, ReadModelError
from control_plane_kit.servers import create_instance_read_app
from control_plane_kit.stores import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    GraphVersionRecord,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    WorkspaceRecord,
)
from tests.postgres_case import PostgresStoreTestCase


class FocusedReadHardeningTests(PostgresStoreTestCase):
    def test_all_adapters_preserve_canonical_focused_payloads(self):
        service = self._seed_service()
        client = TestClient(create_instance_read_app(service, token="read-token"))
        mcp = ReadOnlyMcpAdapter(service)
        opener = TestClientOpener(client)

        cases = (
            (
                service.activity_timeline("workspace-a", limit=1).descriptor(),
                "/workspaces/workspace-a/activity?limit=1",
                "get_activity_timeline",
                {"workspace_id": "workspace-a", "limit": 1},
                ["activity", "workspace-a", "--limit", "1"],
            ),
            (
                service.open_sessions("workspace-a", limit=1, offset=1).descriptor(),
                "/workspaces/workspace-a/sessions?limit=1&offset=1",
                "list_open_sessions",
                {"workspace_id": "workspace-a", "limit": 1, "offset": 1},
                ["open-sessions", "workspace-a", "--limit", "1", "--offset", "1"],
            ),
            (
                service.session_detail("workspace-a", "session-a", limit=1).descriptor(),
                "/workspaces/workspace-a/sessions/session-a?limit=1",
                "get_session_detail",
                {"workspace_id": "workspace-a", "session_id": "session-a", "limit": 1},
                ["session-detail", "workspace-a", "session-a", "--limit", "1"],
            ),
            (
                service.plan_detail("workspace-a", "plan-a", limit=1).descriptor(),
                "/workspaces/workspace-a/plans/plan-a?limit=1",
                "get_plan_detail",
                {"workspace_id": "workspace-a", "plan_id": "plan-a", "limit": 1},
                ["plan-detail", "workspace-a", "plan-a", "--limit", "1"],
            ),
            (
                service.pending_approvals("workspace-a", limit=1, offset=0).descriptor(),
                "/workspaces/workspace-a/approvals/pending?limit=1&offset=0",
                "list_pending_approvals",
                {"workspace_id": "workspace-a", "limit": 1, "offset": 0},
                ["pending-approvals", "workspace-a", "--limit", "1", "--offset", "0"],
            ),
        )

        for expected, path, tool_name, arguments, command in cases:
            with self.subTest(tool_name=tool_name):
                api_payload = client.get(
                    path,
                    headers={"Authorization": "Bearer read-token"},
                ).json()
                mcp_payload = mcp.call_tool(tool_name, arguments)["content"][0]["json"]
                stdout = StringIO()
                cli_status = run_cli(
                    [
                        "--base-url",
                        "http://instance",
                        "--token",
                        "read-token",
                        *command,
                    ],
                    opener=opener,
                    stdout=stdout,
                    stderr=StringIO(),
                    env={},
                )

                self.assertEqual(api_payload, expected)
                self.assertEqual(mcp_payload, expected)
                self.assertEqual(cli_status, 0)
                self.assertEqual(json.loads(stdout.getvalue()), expected)

    def test_failure_evidence_is_canonical_and_bounded(self):
        payload = self._seed_service().activity_timeline(
            "workspace-a",
            limit=50,
        ).descriptor()

        events = payload["sessions"][0]["plans"][0]["runs"][0]["events"]
        failure = next(
            event["failure"]
            for event in events
            if event["failure"] is not None
        )
        self.assertEqual(
            failure,
            {
                "category": "operator_review",
                "code": "compensation.non-compensatable-work",
                "message": "Completed work cannot be compensated automatically.",
                "details": {
                    "activity_ids": ["remove-runtime-a"],
                    "nested": {
                        "label": "visible",
                    },
                },
            },
        )

    def test_recovery_projection_is_reconstructed_from_complete_journal(self):
        run = self._seed_service().plan_detail(
            "workspace-a",
            "plan-a",
            limit=1,
        ).descriptor()["plan"]["runs"][0]

        self.assertEqual(len(run["events"]), 1)
        self.assertEqual(
            run["recovery"],
            {
                "run_status": "partially_failed",
                "saga_status": "compensated",
                "claim_status": "active",
                "schedule": {
                    "ready": [],
                    "running": [],
                    "waiting": [],
                    "blocked": [],
                    "succeeded": ["remove-runtime-a"],
                    "failed": [],
                    "compensating": [],
                    "compensated": ["start-runtime-a"],
                    "compensation_failed": [],
                    "compensation_ready": [],
                },
                "in_flight": {"forward": [], "compensation": []},
                "uncertainty": {"forward": [], "compensation": []},
                "failures": {
                    "original": [],
                    "compensation": [
                        {
                            "event_id": "event-8",
                            "run_id": "run-a",
                            "ordinal": 8,
                            "event_type": "run_compensation_failed",
                            "occurred_at": "2026-07-16T01:07:08Z",
                            "activity_id": None,
                            "payload": {},
                            "failure": {
                                "category": "operator_review",
                                "code": "compensation.non-compensatable-work",
                                "message": "Completed work cannot be compensated automatically.",
                                "details": {
                                    "activity_ids": ["remove-runtime-a"],
                                    "nested": {"label": "visible"},
                                },
                            },
                        }
                    ],
                },
                "non_compensatable_activity_ids": ["remove-runtime-a"],
                "decisions": [],
                "allowed_decisions": [
                    {
                        "kind": "retry-as-new-run",
                        "required_scope": "recovery:operate",
                        "activity_id": None,
                        "required_parameters": [],
                    },
                    {
                        "kind": "accept-uncompensated-failure",
                        "required_scope": "recovery:accept-loss",
                        "activity_id": None,
                        "required_parameters": [],
                    },
                    {
                        "kind": "remain-paused",
                        "required_scope": "recovery:operate",
                        "activity_id": None,
                        "required_parameters": [],
                    },
                ],
            },
        )

    def test_secret_shaped_failure_details_are_rejected_before_persistence(self):
        with self.assertRaisesRegex(
            ExecutionValueError,
            "recovery_token is secret-shaped",
        ):
            BoundedEvidence.from_mapping(
                {"nested": {"recovery_token": "secret-value"}}
            )

    def test_workspace_boundaries_fail_closed_without_disclosing_foreign_records(self):
        service = self._seed_service()
        client = TestClient(create_instance_read_app(service))
        mcp = ReadOnlyMcpAdapter(service)

        with self.assertRaisesRegex(
            ReadModelError,
            "missing session 'session-foreign' in workspace 'workspace-a'",
        ):
            service.session_detail("workspace-a", "session-foreign")
        with self.assertRaisesRegex(McpReadError, "missing plan 'plan-foreign'"):
            mcp.call_tool(
                "get_plan_detail",
                {"workspace_id": "workspace-a", "plan_id": "plan-foreign"},
            )

        response = client.get(
            "/workspaces/workspace-a/sessions/session-foreign"
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("workspace-b", response.text)

    def test_paging_is_deterministic_and_nested_sensitive_values_are_redacted(self):
        service = self._seed_service()

        first = service.open_sessions("workspace-a", limit=1, offset=0).descriptor()
        second = service.open_sessions("workspace-a", limit=1, offset=1).descriptor()
        detail = service.session_detail("workspace-a", "session-a").descriptor()

        self.assertEqual(first["total"], 2)
        self.assertEqual([item["session_id"] for item in first["items"]], ["session-a"])
        self.assertEqual([item["session_id"] for item in second["items"]], ["session-b"])
        self.assertNotIn("session-foreign", str(first) + str(second))
        action_payload = detail["session"]["actions"][0]["payload"]
        self.assertEqual(action_payload["nested"]["client_secret"], "<redacted>")
        self.assertEqual(action_payload["nested"]["label"], "visible")
        self.assertNotIn("secret-value", str(detail))

    def test_plan_detail_remains_pinned_when_workspace_desired_pointer_changes(self):
        service = self._seed_service()

        before = service.plan_detail("workspace-a", "plan-a").descriptor()
        self.stores.workspace.set_desired_graph("workspace-a", "graph-c")
        after = service.plan_detail("workspace-a", "plan-a").descriptor()

        self.assertEqual(after, before)
        self.assertEqual(after["plan"]["base_graph_id"], "graph-a")
        self.assertEqual(after["plan"]["desired_graph_id"], "graph-b")
        self.assertEqual(
            service.desired_graph("workspace-a").descriptor()["graph_id"],
            "graph-c",
        )

    def _seed_service(self) -> InstanceReadService:
        self.stores.workspace.create(
            WorkspaceRecord(workspace_id="workspace-a", name="Primary")
        )
        self.stores.workspace.create(
            WorkspaceRecord(workspace_id="workspace-b", name="Foreign")
        )
        for graph_id, workspace_id, version in (
            ("graph-a", "workspace-a", 1),
            ("graph-b", "workspace-a", 2),
            ("graph-c", "workspace-a", 3),
            ("graph-foreign", "workspace-b", 1),
        ):
            self.stores.graph_topology.save(
                GraphVersionRecord(
                    graph_id=graph_id,
                    workspace_id=workspace_id,
                    version=version,
                    graph_descriptor=DeploymentGraph(graph_id).descriptor(),
                    created_by="operator",
                    created_at=f"2026-07-16T00:0{version}:00Z",
                )
            )
        self.stores.workspace.set_current_graph("workspace-a", "graph-a")
        self.stores.workspace.set_desired_graph("workspace-a", "graph-b")

        for session_id, workspace_id in (
            ("session-b", "workspace-a"),
            ("session-a", "workspace-a"),
            ("session-foreign", "workspace-b"),
        ):
            self.stores.activity_history.add_session(
                OperationSessionRecord(
                    session_id=session_id,
                    workspace_id=workspace_id,
                    actor_id="operator",
                    title=session_id,
                    status=OperationSessionStatus.OPEN,
                    created_at="2026-07-16T01:00:00Z",
                )
            )
        self.stores.activity_history.add_action(
            OperationActionRecord(
                action_id="action-a",
                session_id="session-a",
                ordinal=1,
                action_type=OperationActionKind.CHECK_HEALTH,
                actor_id="operator",
                payload={
                    "nested": {
                        "client_secret": "secret-value",
                        "label": "visible",
                    }
                },
                created_at="2026-07-16T01:01:00Z",
            )
        )
        for plan_id, session_id, base_graph_id, desired_graph_id in (
            ("plan-a", "session-a", "graph-a", "graph-b"),
            (
                "plan-foreign",
                "session-foreign",
                "graph-foreign",
                "graph-foreign",
            ),
        ):
            self.stores.activity_history.add_plan(
                ActivityPlanRecord(
                    plan_id=plan_id,
                    session_id=session_id,
                    base_graph_id=base_graph_id,
                    desired_graph_id=desired_graph_id,
                    status="planned",
                    created_at="2026-07-16T01:02:00Z",
                    plan=(
                        ActivityPlan(
                            (
                                PlannedActivity(
                                    ActivityId("start-runtime-a"),
                                    StartRuntime(RuntimeTarget("runtime-a")),
                                ),
                                PlannedActivity(
                                    ActivityId("remove-runtime-a"),
                                    RemoveRuntimeResource(RuntimeTarget("runtime-a")),
                                    (
                                        ActivityDependency(
                                            ActivityId("start-runtime-a")
                                        ),
                                    ),
                                    risk=RiskLevel.HIGH,
                                    impact=ActivityImpact.DESTRUCTIVE,
                                ),
                            )
                        )
                        if plan_id == "plan-a"
                        else ActivityPlan(())
                    ),
                )
            )
        self.stores.activity_history.add_approval_request(
            ApprovalRequestRecord(
                request_id="approval-a",
                session_id="session-a",
                plan_id="plan-a",
                requested_by="operator",
                requested_at="2026-07-16T01:03:00Z",
                required_scope="plan:approve",
                max_risk=RiskLevel.HIGH,
                destructive=True,
            )
        )
        self.stores.activity_history.add_approval_decision(
            ApprovalDecisionRecord(
                decision_id="approval-decision-a",
                request_id="approval-a",
                actor_id="manager",
                decision=ApprovalDecisionKind.APPROVED,
                scope="plan:approve",
                decided_at="2026-07-16T01:04:00Z",
            )
        )
        self.stores.execution.add_request(
            ExecutionRequestRecord(
                identity=ExecutionRequestIdentity(
                    "execution-request-a",
                    "workspace-a",
                    "session-a",
                    "plan-a",
                ),
                status=ExecutionRequestStatus.CLAIMED,
                requested_by="operator",
                requested_at="2026-07-16T01:05:00Z",
                approval_request_id="approval-a",
                approval_decision_id="approval-decision-a",
                idempotency=ExecutionIdempotency("execute-a", "fingerprint-a"),
                claim=ClaimIdentity(
                    "worker-a",
                    "2026-07-16T01:05:00Z",
                    "2026-07-16T01:10:00Z",
                ),
            )
        )
        self.stores.execution.add_run(
            ActivityRunRecord(
                run_id="run-a",
                plan_id="plan-a",
                admission=AdmittedRun("execution-request-a"),
                retry=RetryIdentity(1),
                status=ActivityRunStatus.PARTIALLY_FAILED,
                created_at="2026-07-16T01:06:00Z",
                started_at="2026-07-16T01:06:00Z",
                settled_at="2026-07-16T01:07:00Z",
            )
        )
        failure = FailureEvidence(
            FailureCategory.OPERATOR_REVIEW,
            "compensation.non-compensatable-work",
            "Completed work cannot be compensated automatically.",
            BoundedEvidence.from_mapping(
                {
                    "activity_ids": ["remove-runtime-a"],
                    "nested": {"label": "visible"},
                }
            ),
        )
        event_values = (
            (ActivityEventKind.STEP_STARTED, "start-runtime-a", None),
            (ActivityEventKind.STEP_SUCCEEDED, "start-runtime-a", None),
            (ActivityEventKind.STEP_STARTED, "remove-runtime-a", None),
            (ActivityEventKind.STEP_SUCCEEDED, "remove-runtime-a", None),
            (ActivityEventKind.RUN_COMPENSATION_STARTED, None, None),
            (ActivityEventKind.STEP_COMPENSATION_STARTED, "start-runtime-a", None),
            (ActivityEventKind.STEP_COMPENSATION_SUCCEEDED, "start-runtime-a", None),
            (ActivityEventKind.RUN_COMPENSATION_FAILED, None, failure),
        )
        for ordinal, (kind, activity_id, event_failure) in enumerate(
            event_values,
            start=1,
        ):
            self.stores.execution.add_event(
                ActivityEventRecord(
                    event_id=f"event-{ordinal}",
                    run_id="run-a",
                    ordinal=ordinal,
                    kind=kind,
                    occurred_at=f"2026-07-16T01:07:{ordinal:02d}Z",
                    activity_id=activity_id,
                    failure=event_failure,
                )
            )
        return InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            execution_store=self.stores.execution,
            observed_state_store=self.stores.observed_state,
            clock=lambda: datetime(2026, 7, 16, 1, 8, tzinfo=timezone.utc),
        )


class TestClientResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class TestClientOpener:
    def __init__(self, client: TestClient) -> None:
        self._client = client

    def __call__(self, request):
        path = request.full_url.removeprefix("http://instance")
        response = self._client.get(path, headers=dict(request.header_items()))
        return TestClientResponse(response.content)


if __name__ == "__main__":
    main()
