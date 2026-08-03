from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import unittest

import psycopg

from control_plane_kit_core.operations import (
    ControlPlaneServiceRole,
    operator_command_http_routes,
    operator_read_http_routes,
)
from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    PrincipalIdentity,
    PrincipalKind,
    WorkspaceGrant,
)
from control_plane_kit_core.planning import ActivityPlan
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_core.algebra import (
    BlockSockets,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
)
from control_plane_kit_core.runtime_effects import ImagePullAuthority
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    ProductRuntimeContract,
    instantiate_product,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, compile_topology
from control_plane_kit_core.types import Protocol
from control_plane_kit_operations.cpk_server import (
    _ROUTE_AUTHORIZATION_POLICIES,
    CpkServerAdmissionService,
    CpkServerApplicationError,
    CpkServerApprovalService,
    CpkServerLifecycleService,
    CpkServerOperationsApplication,
    CpkServerPlanningService,
    CpkServerReadService,
    CpkServerUnsupportedService,
    cpk_server_services,
)
from control_plane_kit_operations.admission import ExecutionAdmissionCommandService
from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService
from control_plane_kit_operations.approvals import ApprovalCommandService, RequestApproval
from control_plane_kit_operations.coordinator import (
    ActivityExecutionOutcome,
    ExecutionCoordinator,
)
from control_plane_kit_operations.lifecycle import RunLifecycleCommandService
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyRegistrationService,
)
from control_plane_kit_operations.ingress_authorities import (
    IngressAuthorityRegistrationService,
)
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService,
    DesiredGraphCommandService,
    RequestActivityPlan,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.products import (
    ImagePullAuthorityRegistrationService,
    ProductRegistrationService,
)
from control_plane_kit_operations.records import (
    ActivityPlanRecord,
    ActivityPlanStatus,
    BoundedEvidence,
    GraphVersionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    WorkspaceRecord,
)
from control_plane_kit_operations.runtime_authorities import (
    RuntimeAuthorityRegistrationService,
)
from control_plane_kit_operations.secret_providers import (
    SecretProviderRegistrationService,
)
from control_plane_kit_operations.workflows import OperationCommandService
from control_plane_kit_operations.workspaces import WorkspaceCommandService


def operator_principal(
    *,
    subject_id: str = "operator-a",
    workspace_ids: tuple[str, ...] = ("workspace-a", "missing"),
    scopes: tuple[PolicyScope, ...] = tuple(PolicyScope),
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        PrincipalIdentity(
            issuer="urn:test:cpk-server-adapters",
            subject_id=subject_id,
            kind=PrincipalKind.OPERATOR,
        ),
        tuple(WorkspaceGrant(workspace_id, scopes) for workspace_id in workspace_ids),
    )


def worker_principal(
    *,
    subject_id: str = "worker-a",
    scopes: tuple[PolicyScope, ...] = (PolicyScope.EXECUTION_OPERATE,),
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        PrincipalIdentity(
            issuer="urn:test:cpk-server-adapters",
            subject_id=subject_id,
            kind=PrincipalKind.WORKER,
        ),
        (WorkspaceGrant("workspace-a", scopes),),
    )


@dataclass(frozen=True)
class RouteRequest:
    surface: str
    route_id: str
    service_role: ControlPlaneServiceRole
    path_parameters: dict[str, str]
    payload: dict[str, object]
    principal: AuthenticatedPrincipal = field(default_factory=operator_principal)


class RecordingService:
    def __init__(self) -> None:
        self.commands: list[object] = []

    def execute(self, command: object):
        self.commands.append(command)
        return DescriptorResult({"command_type": type(command).__name__})


class DescriptorResult:
    def __init__(self, descriptor: dict[str, object]) -> None:
        self._descriptor = descriptor

    def descriptor(self) -> dict[str, object]:
        return dict(self._descriptor)


class GeneratedIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.next = 0

    def __call__(self) -> str:
        self.next += 1
        return f"{self.prefix}-{self.next}"


class SucceedingActivityAdapter:
    def __init__(self) -> None:
        self.activities: list[str] = []

    def execute(self, context) -> ActivityExecutionOutcome:
        activity_id = context.activity.activity_id.value
        self.activities.append(activity_id)
        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping({"activity_id": activity_id})
        )


class CpkServerOperationsAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def seed_workspace(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-a", "Workspace A")
            )
            unit_of_work.stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-current",
                    workspace_id="workspace-a",
                    version=1,
                    graph=DeploymentGraph("current"),
                    created_by="operator-a",
                    created_at="2026-07-22T10:00:00Z",
                )
            )
            unit_of_work.stores.workspaces.set_current_graph(
                "workspace-a",
                "graph-current",
            )
            unit_of_work.commit()

    def seed_reviewable_plan(self) -> None:
        self.seed_workspace()
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-desired",
                    workspace_id="workspace-a",
                    version=2,
                    graph=DeploymentGraph("desired"),
                    created_by="operator-a",
                    created_at="2026-07-22T10:01:00Z",
                )
            )
            unit_of_work.stores.workspaces.set_desired_graph(
                "workspace-a",
                "graph-desired",
            )
            unit_of_work.stores.activity_history.add_session(
                OperationSessionRecord(
                    session_id="session-a",
                    workspace_id="workspace-a",
                    actor_id="operator-a",
                    title="Initial deployment",
                    status=OperationSessionStatus.OPEN,
                    created_at="2026-07-22T10:02:00Z",
                )
            )
            unit_of_work.stores.activity_history.add_plan(
                ActivityPlanRecord(
                    plan_id="plan-a",
                    session_id="session-a",
                    base_graph_id="graph-current",
                    desired_graph_id="graph-desired",
                    status=ActivityPlanStatus.PLANNED,
                    created_at="2026-07-22T10:03:00Z",
                    plan=ActivityPlan(()),
                )
            )
            unit_of_work.commit()

    def test_every_public_route_has_an_explicit_authorization_policy(self) -> None:
        route_ids = {
            route.route_id
            for route in operator_read_http_routes() + operator_command_http_routes()
        }

        self.assertEqual(set(_ROUTE_AUTHORIZATION_POLICIES), route_ids)

    def test_forged_payload_scopes_do_not_authorize_an_ungranted_principal(self) -> None:
        recording = RecordingService()
        service = CpkServerApprovalService(recording)

        with self.assertRaises(CpkServerApplicationError) as raised:
            service.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.approval.request",
                    service_role=ControlPlaneServiceRole.APPROVAL,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={
                        "session_id": "session-a",
                        "plan_id": "plan-a",
                        "actor_id": "forged-operator",
                        "actor_scopes": [scope.value for scope in PolicyScope],
                        "idempotency_key": "approval-a",
                    },
                    principal=AuthenticatedPrincipal(
                        PrincipalIdentity(
                            issuer="urn:test:cpk-server-adapters",
                            subject_id="ungranted-operator",
                            kind=PrincipalKind.OPERATOR,
                        )
                    ),
                )
            )

        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(recording.commands, [])

    def test_principal_for_another_workspace_is_denied_before_store_access(self) -> None:
        store_accessed = False

        def forbidden_unit_of_work():
            nonlocal store_accessed
            store_accessed = True
            raise AssertionError("authorization must precede store access")

        service = CpkServerReadService(forbidden_unit_of_work)

        with self.assertRaises(CpkServerApplicationError) as raised:
            service.handle(
                RouteRequest(
                    surface="http",
                    route_id="read.workspace",
                    service_role=ControlPlaneServiceRole.READS,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={},
                    principal=operator_principal(workspace_ids=("workspace-b",)),
                )
            )

        self.assertEqual(raised.exception.status, 403)
        self.assertFalse(store_accessed)

    def test_command_provenance_and_scopes_come_from_trusted_principal(self) -> None:
        recording = RecordingService()
        service = CpkServerApprovalService(recording)
        principal = operator_principal(
            subject_id="trusted-operator",
            scopes=(PolicyScope.PLAN_REQUEST,),
        )

        service.handle(
            RouteRequest(
                surface="http",
                route_id="command.approval.request",
                service_role=ControlPlaneServiceRole.APPROVAL,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "session_id": "session-a",
                    "plan_id": "plan-a",
                    "actor_id": "forged-operator",
                    "actor_scopes": [PolicyScope.PLAN_APPROVE.value],
                    "idempotency_key": "approval-a",
                },
                principal=principal,
            )
        )

        command = recording.commands[0]
        self.assertEqual(command.actor_id, "trusted-operator")
        self.assertEqual(command.actor_scopes, (PolicyScope.PLAN_REQUEST,))

    def test_use_and_read_permissions_do_not_imply_execution_or_mutation(self) -> None:
        admission_recording = RecordingService()
        admission = CpkServerAdmissionService(admission_recording)

        with self.assertRaises(CpkServerApplicationError) as use_only:
            admission.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.deployment.admit",
                    service_role=ControlPlaneServiceRole.ADMISSION,
                    path_parameters={
                        "workspace_id": "workspace-a",
                        "plan_id": "plan-a",
                    },
                    payload={
                        "session_id": "session-a",
                        "approval_request_id": "approval-a",
                        "actor_scopes": [PolicyScope.PLAN_EXECUTE.value],
                        "idempotency_key": "admit-a",
                    },
                    principal=operator_principal(
                        scopes=(PolicyScope.RUNTIME_AUTHORITY_USE,)
                    ),
                )
            )

        self.assertEqual(use_only.exception.status, 403)
        self.assertEqual(admission_recording.commands, [])

        planning_recording = RecordingService()
        desired_recording = RecordingService()
        planning = CpkServerPlanningService(
            planning_recording,
            desired_graphs=desired_recording,
        )
        with self.assertRaises(CpkServerApplicationError) as read_only:
            planning.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.desired-graph.set",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={
                        "session_id": "session-a",
                        "graph": DEFAULT_GRAPH_CODEC.encode(
                            DeploymentGraph("desired")
                        ),
                        "idempotency_key": "desired-a",
                    },
                    principal=operator_principal(
                        scopes=(PolicyScope.INSTANCE_WORKSPACE_READ,)
                    ),
                )
            )

        self.assertEqual(read_only.exception.status, 403)
        self.assertEqual(planning_recording.commands, [])
        self.assertEqual(desired_recording.commands, [])

    def test_http_read_route_uses_operations_read_projection_not_demo_echo(self) -> None:
        self.seed_workspace()
        service = CpkServerReadService(
            self.unit_of_work,
            clock=lambda: datetime(2026, 7, 22, 13, 0, tzinfo=timezone.utc),
        )

        result = service.handle(
            RouteRequest(
                surface="http",
                route_id="read.current-graph",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={"workspace_id": "workspace-a"},
                payload={},
            )
        )

        self.assertEqual(result["graph_id"], "graph-current")
        self.assertEqual(result["graph_name"], "current")
        self.assertNotIn("service", result)
        self.assertNotIn("payload", result)

    def test_mcp_read_arguments_use_same_read_service_boundary(self) -> None:
        self.seed_workspace()
        service = CpkServerReadService(self.unit_of_work)

        result = service.handle(
            RouteRequest(
                surface="mcp",
                route_id="read.workspace",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={},
                payload={"workspace_id": "workspace-a"},
            )
        )

        self.assertEqual(result["workspace"]["workspace_id"], "workspace-a")
        self.assertEqual(result["current_graph"]["graph_id"], "graph-current")

    def test_read_errors_are_bounded_without_sql_or_secret_leakage(self) -> None:
        service = CpkServerReadService(self.unit_of_work)

        with self.assertRaises(CpkServerApplicationError) as raised:
            service.handle(
                RouteRequest(
                    surface="http",
                    route_id="read.workspace",
                    service_role=ControlPlaneServiceRole.READS,
                    path_parameters={"workspace_id": "missing"},
                    payload={"api_token": "do-not-disclose"},
                )
            )

        self.assertEqual(raised.exception.status, 404)
        descriptor = raised.exception.descriptor()
        self.assertIn("missing workspace", descriptor["error"]["message"])
        self.assertNotIn("do-not-disclose", str(descriptor))
        self.assertNotIn("SELECT", str(descriptor).upper())

    def test_setup_routes_create_workspace_import_product_and_set_desired_graph(self) -> None:
        product_document = ProductDescriptorCodec().encode_document(
            self.product("hello-server")
        )
        graph = self.graph_from_document(product_document.product)
        planning = CpkServerPlanningService(
            RecordingService(),
            workspaces=WorkspaceCommandService(
                self.unit_of_work,
                clock=lambda: "2026-07-22T10:00:00Z",
                id_factory=self.ids("graph-empty"),
            ),
            products=ProductRegistrationService(self.unit_of_work),
            image_pull_authorities=ImagePullAuthorityRegistrationService(
                self.unit_of_work
            ),
            desired_graphs=DesiredGraphCommandService(
                self.unit_of_work,
                clock=lambda: "2026-07-22T10:05:00Z",
                id_factory=self.ids("graph-desired", "action-desired"),
            ),
        )
        lifecycle = CpkServerLifecycleService(
            RecordingService(),
            operations=OperationCommandService(
                self.unit_of_work,
                clock=lambda: "2026-07-22T10:01:00Z",
                id_factory=self.ids("session-a", "action-start"),
            ),
        )

        workspace = planning.handle(
            RouteRequest(
                surface="http",
                route_id="command.workspace.create",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "name": "Workspace A",
                    "actor_id": "operator-a",
                    "idempotency_key": "workspace-a",
                },
            )
        )
        self.assertEqual(workspace["workspace"]["current_graph_id"], "graph-empty")

        authority = planning.handle(
            RouteRequest(
                surface="http",
                route_id="command.image-pull-authority.register",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "registry": "ghcr.io",
                    "repository": "openj92/control-plane-kit-servers",
                    "credential_reference": "secret://docker-config/ghcr.io",
                    "actor_id": "operator-a",
                    "admitted_at": "2026-07-22T10:01:30Z",
                    "idempotency_key": "pull-authority-a",
                },
            )
        )
        self.assertEqual(authority["workspace_id"], "workspace-a")
        self.assertEqual(authority["authority"]["registry"], "ghcr.io")
        self.assertEqual(
            authority["authority"]["credential_reference"],
            "secret://docker-config/ghcr.io",
        )
        self.assertNotIn("token", str(authority).lower())

        registered = planning.handle(
            RouteRequest(
                surface="http",
                route_id="command.product.import",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "descriptor_document": json.loads(
                        product_document.content.decode("utf-8")
                    ),
                    "actor_id": "operator-a",
                    "imported_at": "2026-07-22T10:02:00Z",
                    "idempotency_key": "import-product-a",
                },
            )
        )
        self.assertEqual(
            registered["reference"]["identity"]["name"],
            "hello-server",
        )
        self.assertEqual(registered["status"], "active")

        session = lifecycle.handle(
            RouteRequest(
                surface="http",
                route_id="command.operation-session.start",
                service_role=ControlPlaneServiceRole.LIFECYCLE,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "actor_id": "operator-a",
                    "title": "Initial deployment",
                    "idempotency_key": "session-a",
                },
            )
        )
        self.assertEqual(session["session_id"], "session-a")

        desired = planning.handle(
            RouteRequest(
                surface="http",
                route_id="command.desired-graph.set",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "session_id": "session-a",
                    "actor_id": "operator-a",
                    "graph": DEFAULT_GRAPH_CODEC.encode(graph),
                    "expected_desired_graph_id": None,
                    "idempotency_key": "desired-a",
                },
            )
        )
        self.assertEqual(desired["desired_graph_id"], "graph-desired")

        with self.unit_of_work() as unit_of_work:
            workspace_record = unit_of_work.stores.workspaces.get("workspace-a")
            self.assertEqual(workspace_record.current_graph_id, "graph-empty")
            self.assertEqual(workspace_record.desired_graph_id, "graph-desired")
            self.assertEqual(
                unit_of_work.stores.activity_history.get_session("session-a").workspace_id,
                "workspace-a",
            )

    def test_ingress_authority_routes_share_operations_boundary_and_scopes(self) -> None:
        self.seed_workspace()
        planning = CpkServerPlanningService(
            RecordingService(),
            ingress_authorities=IngressAuthorityRegistrationService(self.unit_of_work),
        )
        reads = CpkServerReadService(self.unit_of_work)
        authority_payload = {
            "authority_ref": "openj92-public-ingress",
            "authority": {
                "provider_kind": "cloudflare",
                "account_id": "account-openj92",
                "zone_id": "zone-openj92",
                "zone_name": "openj92.dev",
                "api_token_ref": "secret://cloudflare/openj92/api-token",
                "allowed_hostname_pattern": "cpk-gateway-*.openj92.dev",
                "generated_secret_provider_registration_id": (
                    "sprov-generated-ingress"
                ),
                "generated_secret_reference_prefix": (
                    "secret://generated/ingress"
                ),
            },
            "actor_id": "operator-a",
            "admitted_at": "2026-07-27T22:50:00Z",
            "idempotency_key": "ingress-authority-a",
            "actor_scopes": [PolicyScope.PLAN_EXECUTE.value],
        }

        with self.assertRaises(CpkServerApplicationError) as denied:
            planning.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.ingress-authority.register",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload=authority_payload,
                    principal=operator_principal(
                        scopes=(PolicyScope.PLAN_EXECUTE,)
                    ),
                )
            )
        self.assertEqual(denied.exception.status, 403)

        registered = planning.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.ingress-authority.register",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    **authority_payload,
                    "actor_scopes": [PolicyScope.INGRESS_AUTHORITY_REGISTER.value],
                },
            )
        )
        self.assertEqual(registered["authority_ref"], "openj92-public-ingress")
        self.assertEqual(registered["provider_kind"], "cloudflare")
        self.assertNotIn("cf_api_token", repr(registered).lower())
        self.assertNotIn("bearer", repr(registered).lower())

        with self.assertRaises(CpkServerApplicationError) as read_denied:
            reads.handle(
                RouteRequest(
                    surface="http",
                    route_id="read.ingress-authorities",
                    service_role=ControlPlaneServiceRole.READS,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={"actor_scopes": [PolicyScope.PLAN_EXECUTE.value]},
                    principal=operator_principal(
                        scopes=(PolicyScope.PLAN_EXECUTE,)
                    ),
                )
            )
        self.assertEqual(read_denied.exception.status, 403)

        listed = reads.handle(
            RouteRequest(
                surface="http",
                route_id="read.ingress-authorities",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={"workspace_id": "workspace-a"},
                payload={"actor_scopes": [PolicyScope.INGRESS_AUTHORITY_READ.value]},
            )
        )
        detail = reads.handle(
            RouteRequest(
                surface="mcp",
                route_id="read.ingress-authority-detail",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "authority_ref": "openj92-public-ingress",
                    "actor_scopes": [PolicyScope.INGRESS_AUTHORITY_READ.value],
                },
            )
        )

        self.assertEqual(listed["items"][0]["authority_ref"], "openj92-public-ingress")
        self.assertEqual(
            detail["ingress_authority"]["registration_id"],
            registered["registration_id"],
        )

        with self.unit_of_work() as unit_of_work:
            selected = unit_of_work.stores.ingress_authorities.require_active_for_hostname(
                "workspace-a",
                IngressAuthorityReference("openj92-public-ingress"),
                "cpk-gateway-001.openj92.dev",
            )
            self.assertEqual(selected.registration_id, registered["registration_id"])

        with self.assertRaises(CpkServerApplicationError) as revoke_denied:
            planning.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.ingress-authority.revoke",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={
                        "workspace_id": "workspace-a",
                        "authority_ref": "openj92-public-ingress",
                    },
                    payload={
                        "idempotency_key": "revoke-ingress-a",
                        "actor_scopes": [PolicyScope.PLAN_EXECUTE.value],
                    },
                    principal=operator_principal(
                        scopes=(PolicyScope.PLAN_EXECUTE,)
                    ),
                )
            )
        self.assertEqual(revoke_denied.exception.status, 403)

        revoked = planning.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.ingress-authority.revoke",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={
                    "workspace_id": "workspace-a",
                    "authority_ref": "openj92-public-ingress",
                },
                payload={
                    "idempotency_key": "revoke-ingress-a",
                    "actor_scopes": [PolicyScope.INGRESS_AUTHORITY_REVOKE.value],
                },
            )
        )
        self.assertEqual(revoked["status"], "revoked")

    def test_command_route_translates_payload_to_existing_planning_command(self) -> None:
        recording = RecordingService()
        service = CpkServerPlanningService(recording)
        payload = {
            "session_id": "session-a",
            "actor_id": "operator-a",
            "expected_current_graph_id": "graph-current",
            "expected_desired_graph_id": "graph-desired",
            "expected_current_realized_projection_id": "projection-current",
            "expected_desired_realized_projection_id": "projection-desired",
            "expected_desired_graph_revision": 7,
            "idempotency_key": "plan-a",
        }

        for surface in ("http", "mcp"):
            result = service.handle(
                RouteRequest(
                    surface=surface,
                    route_id="command.deployment.plan",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload=payload,
                )
            )
            self.assertEqual(result, {"command_type": "RequestActivityPlan"})

        self.assertEqual(recording.commands[0], recording.commands[1])
        command = recording.commands[0]
        self.assertIsInstance(command, RequestActivityPlan)
        self.assertEqual(command.workspace_id, "workspace-a")
        self.assertEqual(command.expected_current_graph_id, "graph-current")
        self.assertEqual(
            command.expected_current_realized_projection_id,
            "projection-current",
        )
        self.assertEqual(
            command.expected_desired_realized_projection_id,
            "projection-desired",
        )
        self.assertEqual(command.expected_desired_graph_revision, 7)
        self.assertEqual(command.idempotency_key.value, "plan-a")

    def test_approval_request_route_translates_payload_to_existing_command(self) -> None:
        recording = RecordingService()
        service = CpkServerApprovalService(recording)

        result = service.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.approval.request",
                service_role=ControlPlaneServiceRole.APPROVAL,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "session_id": "session-a",
                    "plan_id": "plan-a",
                    "actor_id": "operator-a",
                    "actor_scopes": [PolicyScope.PLAN_REQUEST.value],
                    "idempotency_key": "request-approval-a",
                    "comment": "Please review the deployment.",
                },
                principal=operator_principal(scopes=(PolicyScope.PLAN_REQUEST,)),
            )
        )

        self.assertEqual(result, {"command_type": "RequestApproval"})
        command = recording.commands[0]
        self.assertIsInstance(command, RequestApproval)
        self.assertEqual(command.session_id, "session-a")
        self.assertEqual(command.plan_id, "plan-a")
        self.assertEqual(command.idempotency_key.value, "request-approval-a")
        self.assertEqual(
            command.actor_scopes,
            (PolicyScope.PLAN_REQUEST,),
        )

    def test_public_approval_loop_persists_and_reads_queue_detail_and_decision(self) -> None:
        self.seed_reviewable_plan()
        approval = CpkServerApprovalService(
            ApprovalCommandService(
                self.unit_of_work,
                clock=lambda: "2026-07-22T10:04:00Z",
                id_factory=self.ids(
                    "approval-a",
                    "action-approval",
                    "decision-a",
                    "action-decision",
                ),
            )
        )
        reads = CpkServerReadService(self.unit_of_work)

        requested = approval.handle(
            RouteRequest(
                surface="http",
                route_id="command.approval.request",
                service_role=ControlPlaneServiceRole.APPROVAL,
                path_parameters={
                    "workspace_id": "workspace-a",
                    "plan_id": "plan-a",
                },
                payload={
                    "session_id": "session-a",
                    "actor_id": "operator-a",
                    "actor_scopes": [PolicyScope.PLAN_REQUEST.value],
                    "idempotency_key": "request-approval-a",
                    "comment": "Please review the deployment.",
                },
            )
        )
        self.assertEqual(requested["request_id"], "approval-a")
        self.assertEqual(requested["state"], "pending")

        pending = reads.handle(
            RouteRequest(
                surface="http",
                route_id="read.pending-approvals",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={"workspace_id": "workspace-a"},
                payload={"limit": 10, "offset": 0},
            )
        )
        self.assertEqual(pending["items"][0]["request_id"], "approval-a")

        detail = reads.handle(
            RouteRequest(
                surface="mcp",
                route_id="read.approval-detail",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "approval_id": "approval-a",
                },
            )
        )
        self.assertEqual(detail["approval"]["request_id"], "approval-a")
        self.assertEqual(detail["plan"]["plan_id"], "plan-a")
        self.assertEqual(detail["plan"]["risk_summary"]["ready_for_execution"], True)

        decided = approval.handle(
            RouteRequest(
                surface="http",
                route_id="command.approval.decide",
                service_role=ControlPlaneServiceRole.APPROVAL,
                path_parameters={
                    "workspace_id": "workspace-a",
                    "approval_id": "approval-a",
                },
                payload={
                    "session_id": "session-a",
                    "actor_id": "manager-a",
                    "actor_scopes": [requested["required_scope"]],
                    "decision": "approved",
                    "idempotency_key": "decide-approval-a",
                    "comment": "Approved.",
                },
                principal=operator_principal(
                    subject_id="manager-a",
                    scopes=(PolicyScope.PLAN_APPROVE,),
                ),
            )
        )
        self.assertEqual(decided["state"], "approved")
        self.assertEqual(decided["request_id"], "approval-a")

    def test_public_workflow_routes_plan_approve_admit_claim_execute_and_advance(self) -> None:
        adapter = SucceedingActivityAdapter()
        lifecycle = RunLifecycleCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T10:10:00Z",
            id_factory=GeneratedIds("lifecycle"),
        )
        application = CpkServerOperationsApplication(
            cpk_server_services(
                unit_of_work_factory=self.unit_of_work,
                planning=ActivityPlanningCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:04:00Z",
                    id_factory=GeneratedIds("plan"),
                ),
                workspaces=WorkspaceCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:00:00Z",
                    id_factory=GeneratedIds("workspace"),
                ),
                products=ProductRegistrationService(self.unit_of_work),
                desired_graphs=DesiredGraphCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:02:00Z",
                    id_factory=GeneratedIds("desired"),
                ),
                approval=ApprovalCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:05:00Z",
                    id_factory=GeneratedIds("approval"),
                ),
                admission=ExecutionAdmissionCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:06:00Z",
                    id_factory=GeneratedIds("admission"),
                ),
                lifecycle=lifecycle,
                operations=OperationCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:01:00Z",
                    id_factory=GeneratedIds("session"),
                ),
                execution=ExecutionCoordinator(
                    self.unit_of_work,
                    lifecycle=lifecycle,
                    adapter=adapter,
                    clock=lambda: "2026-07-22T10:11:00Z",
                    id_factory=GeneratedIds("execution"),
                ),
                advancement=CurrentGraphAdvancementCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-07-22T10:12:00Z",
                    id_factory=GeneratedIds("advance"),
                ),
                clock=lambda: datetime(2026, 7, 22, 10, 13, tzinfo=timezone.utc),
            )
        )
        product_document = ProductDescriptorCodec().encode_document(
            self.product("hello-server")
        )

        workspace = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.workspace.create",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "name": "Workspace A",
                    "actor_id": "operator-a",
                    "idempotency_key": "workspace-a",
                },
            )
        )
        current_graph_id = str(workspace["workspace"]["current_graph_id"])
        current_projection_id = str(
            workspace["workspace"]["current_realized_projection_id"]
        )

        application.handle(
            RouteRequest(
                surface="http",
                route_id="command.product.import",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "descriptor_document": json.loads(
                        product_document.content.decode("utf-8")
                    ),
                    "actor_id": "operator-a",
                    "imported_at": "2026-07-22T10:00:30Z",
                    "idempotency_key": "import-product-a",
                },
            )
        )

        session = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.operation-session.start",
                service_role=ControlPlaneServiceRole.LIFECYCLE,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "actor_id": "operator-a",
                    "title": "Initial deployment",
                    "idempotency_key": "session-a",
                },
            )
        )
        session_id = str(session["session_id"])

        desired = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.desired-graph.set",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "session_id": session_id,
                    "actor_id": "operator-a",
                    "graph": DEFAULT_GRAPH_CODEC.encode(
                        self.graph_from_document(product_document.product)
                    ),
                    "expected_desired_graph_id": None,
                    "expected_desired_realized_projection_id": None,
                    "expected_desired_graph_revision": 0,
                    "idempotency_key": "desired-a",
                },
            )
        )
        desired_graph_id = str(desired["desired_graph_id"])
        desired_projection_id = str(
            desired["desired_realized_projection_id"]
        )
        desired_revision = int(desired["desired_graph_revision"])

        planned = application.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.deployment.plan",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "session_id": session_id,
                    "actor_id": "operator-a",
                    "expected_current_graph_id": current_graph_id,
                    "expected_desired_graph_id": desired_graph_id,
                    "expected_current_realized_projection_id": (
                        current_projection_id
                    ),
                    "expected_desired_realized_projection_id": (
                        desired_projection_id
                    ),
                    "expected_desired_graph_revision": desired_revision,
                    "idempotency_key": "plan-a",
                },
            )
        )
        plan_id = str(planned["plan_id"])
        self.assertEqual(
            planned["base_realized_projection_id"],
            current_projection_id,
        )
        self.assertEqual(
            planned["desired_realized_projection_id"],
            desired_projection_id,
        )
        self.assertEqual(planned["desired_graph_revision"], desired_revision)

        requested = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.approval.request",
                service_role=ControlPlaneServiceRole.APPROVAL,
                path_parameters={"workspace_id": "workspace-a", "plan_id": plan_id},
                payload={
                    "session_id": session_id,
                    "actor_id": "operator-a",
                    "actor_scopes": [PolicyScope.PLAN_REQUEST.value],
                    "idempotency_key": "approval-request-a",
                },
            )
        )
        approval_request_id = str(requested["request_id"])

        pending = application.handle(
            RouteRequest(
                surface="http",
                route_id="read.pending-approvals",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={"workspace_id": "workspace-a"},
                payload={"limit": 10, "offset": 0},
            )
        )
        self.assertEqual(pending["items"][0]["request_id"], approval_request_id)

        detail = application.handle(
            RouteRequest(
                surface="mcp",
                route_id="read.approval-detail",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={},
                payload={"workspace_id": "workspace-a", "approval_id": approval_request_id},
            )
        )
        self.assertEqual(detail["plan"]["plan_id"], plan_id)

        application.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.approval.decide",
                service_role=ControlPlaneServiceRole.APPROVAL,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "session_id": session_id,
                    "request_id": approval_request_id,
                    "actor_id": "manager-a",
                    "actor_scopes": [requested["required_scope"]],
                    "decision": "approved",
                    "idempotency_key": "approval-decision-a",
                },
                principal=operator_principal(
                    subject_id="manager-a",
                    scopes=(PolicyScope.PLAN_APPROVE,),
                ),
            )
        )

        admitted = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.deployment.admit",
                service_role=ControlPlaneServiceRole.ADMISSION,
                path_parameters={"workspace_id": "workspace-a", "plan_id": plan_id},
                payload={
                    "session_id": session_id,
                    "approval_request_id": approval_request_id,
                    "actor_id": "operator-a",
                    "actor_scopes": [PolicyScope.PLAN_EXECUTE.value],
                    "idempotency_key": "admit-a",
                    "readiness": [],
                },
            )
        )
        request_id = str(admitted["execution_request_id"])

        claimed = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.run.claim",
                service_role=ControlPlaneServiceRole.LIFECYCLE,
                path_parameters={"workspace_id": "workspace-a", "run_id": request_id},
                payload={
                    "worker_id": "worker-a",
                    "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
                    "lease_expires_at": "2026-07-22T10:30:00Z",
                    "idempotency_key": "claim-a",
                },
                principal=worker_principal(),
            )
        )
        run_id = str(claimed["run_id"])

        application.handle(
            RouteRequest(
                surface="http",
                route_id="command.run.start",
                service_role=ControlPlaneServiceRole.EXECUTION,
                path_parameters={"workspace_id": "workspace-a", "run_id": run_id},
                payload={
                    "worker_id": "worker-a",
                    "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
                    "idempotency_key": "start-a",
                },
                principal=worker_principal(),
            )
        )

        executed = application.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.deployment.execute",
                service_role=ControlPlaneServiceRole.EXECUTION,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "run_id": run_id,
                    "worker_id": "worker-a",
                    "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
                    "idempotency_key": "execute-a",
                    "max_effects": 10,
                },
                principal=worker_principal(),
            )
        )
        self.assertEqual(executed["coordinator_status"], "completed")
        self.assertEqual(executed["run_status"], "succeeded")
        self.assertEqual(
            [activity.split(":", 1)[0] for activity in adapter.activities],
            ["start-runtime", "start-node", "wait-healthy"],
        )

        advance_payload = {
            "plan_id": plan_id,
            "expected_current_graph_id": current_graph_id,
            "expected_current_realized_projection_id": current_projection_id,
            "desired_graph_id": desired_graph_id,
            "desired_realized_projection_id": desired_projection_id,
            "expected_desired_graph_revision": desired_revision,
            "worker_id": "worker-a",
            "actor_scopes": [PolicyScope.EXECUTION_OPERATE.value],
            "idempotency_key": "advance-a",
        }
        advanced = application.handle(
            RouteRequest(
                surface="http",
                route_id="command.graph.advance-current",
                service_role=ControlPlaneServiceRole.LIFECYCLE,
                path_parameters={"workspace_id": "workspace-a", "run_id": run_id},
                payload=advance_payload,
                principal=worker_principal(),
            )
        )
        advanced_mcp = application.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.graph.advance-current",
                service_role=ControlPlaneServiceRole.LIFECYCLE,
                path_parameters={
                    "workspace_id": "workspace-a",
                    "run_id": run_id,
                },
                payload=advance_payload,
                principal=worker_principal(),
            )
        )
        self.assertEqual(advanced["from_graph_id"], current_graph_id)
        self.assertEqual(advanced["to_graph_id"], desired_graph_id)
        self.assertEqual(
            advanced["from_realized_projection_id"],
            current_projection_id,
        )
        self.assertEqual(
            advanced["to_realized_projection_id"],
            desired_projection_id,
        )
        self.assertEqual(advanced["desired_graph_revision"], desired_revision)
        self.assertTrue(advanced_mcp["replayed"])
        self.assertEqual(
            advanced_mcp["to_realized_projection_id"],
            advanced["to_realized_projection_id"],
        )

        current = application.handle(
            RouteRequest(
                surface="http",
                route_id="read.current-graph",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={"workspace_id": "workspace-a"},
                payload={},
            )
        )
        self.assertEqual(current["graph_id"], desired_graph_id)
        self.assertEqual(current["authored_graph_id"], desired_graph_id)
        self.assertEqual(
            current["realized_projection_id"],
            desired_projection_id,
        )
        current_mcp = application.handle(
            RouteRequest(
                surface="mcp",
                route_id="read.current-graph",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={},
                payload={"workspace_id": "workspace-a"},
            )
        )
        self.assertEqual(current_mcp, current)

    def test_unsupported_services_fail_closed_until_extracted(self) -> None:
        service = CpkServerUnsupportedService(ControlPlaneServiceRole.RECOVERY)

        with self.assertRaises(CpkServerApplicationError) as raised:
            service.handle(
                RouteRequest(
                    surface="mcp",
                    route_id="command.recovery.decide",
                    service_role=ControlPlaneServiceRole.RECOVERY,
                    path_parameters={"workspace_id": "workspace-a", "run_id": "run-a"},
                    payload={"actor_scopes": [PolicyScope.EXECUTION_OPERATE.value]},
                    principal=worker_principal(),
                )
            )

        self.assertEqual(raised.exception.status, 501)
        self.assertIn("not implemented", raised.exception.message)

    def test_image_pull_authority_route_requires_service_and_idempotency_key(self) -> None:
        service = CpkServerPlanningService(RecordingService())

        with self.assertRaises(CpkServerApplicationError) as not_configured:
            service.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.image-pull-authority.register",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={
                        "registry": "ghcr.io",
                        "credential_reference": "secret://docker-config/ghcr.io",
                        "actor_id": "operator-a",
                        "admitted_at": "2026-07-22T10:01:30Z",
                        "idempotency_key": "pull-authority-a",
                    },
                )
            )
        self.assertEqual(not_configured.exception.status, 501)

        service = CpkServerPlanningService(
            RecordingService(),
            image_pull_authorities=ImagePullAuthorityRegistrationService(
                self.unit_of_work
            ),
        )
        with self.assertRaises(CpkServerApplicationError) as missing_key:
            service.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.image-pull-authority.register",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={
                        "registry": "ghcr.io",
                        "credential_reference": "secret://docker-config/ghcr.io",
                        "actor_id": "operator-a",
                        "admitted_at": "2026-07-22T10:01:30Z",
                    },
                )
            )
        self.assertEqual(missing_key.exception.status, 400)
        self.assertIn("idempotency_key", missing_key.exception.message)

    def test_runtime_authority_routes_enforce_focused_scopes_and_redact_detail(self) -> None:
        self.seed_workspace()
        planning = CpkServerPlanningService(
            RecordingService(),
            runtime_authorities=RuntimeAuthorityRegistrationService(self.unit_of_work),
        )
        reads = CpkServerReadService(self.unit_of_work)

        with self.assertRaises(CpkServerApplicationError) as missing_register_scope:
            planning.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.runtime-authority.register",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={
                        "authority_ref": "remote-docker",
                        "runtime_kind": "docker",
                        "authority": {
                            "kind": "remote-docker-tls",
                            "endpoint": "tcp://mac-mini.local:2376",
                            "ca_certificate": "secret://docker/ca",
                            "client_certificate": "secret://docker/cert",
                            "client_key": "secret://docker/key",
                        },
                        "actor_id": "operator-a",
                        "actor_scopes": [PolicyScope.PLAN_EXECUTE.value],
                        "admitted_at": "2026-07-22T10:01:30Z",
                        "idempotency_key": "runtime-authority-a",
                    },
                    principal=operator_principal(
                        scopes=(PolicyScope.PLAN_EXECUTE,)
                    ),
                )
            )
        self.assertEqual(missing_register_scope.exception.status, 403)
        self.assertIn("runtime-authority:register", missing_register_scope.exception.message)

        registered = planning.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.runtime-authority.register",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "authority_ref": "remote-docker",
                    "runtime_kind": "docker",
                    "authority": {
                        "kind": "remote-docker-tls",
                        "endpoint": "tcp://mac-mini.local:2376",
                        "ca_certificate": "secret://docker/ca",
                        "client_certificate": "secret://docker/cert",
                        "client_key": "secret://docker/key",
                    },
                    "actor_id": "operator-a",
                    "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_REGISTER.value],
                    "admitted_at": "2026-07-22T10:01:30Z",
                    "idempotency_key": "runtime-authority-a",
                },
            )
        )

        self.assertEqual(registered["workspace_id"], "workspace-a")
        self.assertEqual(registered["authority_ref"], "remote-docker")
        self.assertEqual(registered["authority"]["endpoint"], "<redacted>")
        self.assertNotIn("mac-mini.local", repr(registered))
        self.assertNotIn("2376", repr(registered))

        with self.assertRaises(CpkServerApplicationError) as missing_read_scope:
            reads.handle(
                RouteRequest(
                    surface="http",
                    route_id="read.runtime-authorities",
                    service_role=ControlPlaneServiceRole.READS,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={"actor_scopes": [PolicyScope.PLAN_EXECUTE.value]},
                    principal=operator_principal(
                        scopes=(PolicyScope.PLAN_EXECUTE,)
                    ),
                )
            )
        self.assertEqual(missing_read_scope.exception.status, 403)
        self.assertIn("runtime-authority:read", missing_read_scope.exception.message)

        listed = reads.handle(
            RouteRequest(
                surface="http",
                route_id="read.runtime-authorities",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={"workspace_id": "workspace-a"},
                payload={"actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_READ.value]},
            )
        )
        self.assertEqual(listed["items"][0]["authority_ref"], "remote-docker")
        self.assertNotIn("mac-mini.local", repr(listed))

        detail = reads.handle(
            RouteRequest(
                surface="mcp",
                route_id="read.runtime-authority-detail",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "authority_ref": "remote-docker",
                    "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_READ.value],
                },
            )
        )
        self.assertEqual(
            detail["runtime_authority"]["authority"]["endpoint"],
            "<redacted>",
        )
        self.assertNotIn("mac-mini.local", repr(detail))

        delivery_payload = {
            "authority_ref": {"reference_id": "remote-docker"},
            "delivery_kind": "remote-docker-tls-secret-files",
            "secret_references": [
                {
                    "label": "ca-cert",
                    "reference_id": "secret://docker/ca",
                },
                {
                    "label": "client-cert",
                    "reference_id": "secret://docker/cert",
                },
                {
                    "label": "client-key",
                    "reference_id": "secret://docker/key",
                },
            ],
        }
        with self.assertRaises(CpkServerApplicationError) as missing_delivery_scope:
            planning.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.runtime-authority-delivery.register",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={
                        "delivery": delivery_payload,
                        "actor_id": "operator-a",
                        "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_REGISTER.value],
                        "admitted_at": "2026-07-22T10:02:00Z",
                        "idempotency_key": "runtime-authority-delivery-a",
                    },
                    principal=operator_principal(
                        scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,)
                    ),
                )
            )
        self.assertEqual(missing_delivery_scope.exception.status, 403)
        self.assertIn(
            "runtime-authority-delivery:register",
            missing_delivery_scope.exception.message,
        )

        delivery = planning.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.runtime-authority-delivery.register",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "delivery": delivery_payload,
                    "actor_id": "operator-a",
                    "actor_scopes": [
                        PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER.value
                    ],
                    "admitted_at": "2026-07-22T10:02:00Z",
                    "idempotency_key": "runtime-authority-delivery-a",
                },
            )
        )
        self.assertEqual(delivery["workspace_id"], "workspace-a")
        self.assertEqual(delivery["authority_ref"], "remote-docker")
        self.assertEqual(delivery["delivery_kind"], "remote-docker-tls-secret-files")
        self.assertNotIn("mac-mini.local", repr(delivery))
        self.assertNotIn("PRIVATE KEY", repr(delivery))

        listed_deliveries = reads.handle(
            RouteRequest(
                surface="http",
                route_id="read.runtime-authority-deliveries",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "actor_scopes": [
                        PolicyScope.RUNTIME_AUTHORITY_DELIVERY_READ.value
                    ]
                },
            )
        )
        self.assertEqual(
            listed_deliveries["items"][0]["delivery"]["secret_references"],
            "<redacted>",
        )
        self.assertNotIn("secret://docker/key", repr(listed_deliveries))

        delivery_detail = reads.handle(
            RouteRequest(
                surface="mcp",
                route_id="read.runtime-authority-delivery-detail",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "authority_ref": "remote-docker",
                    "actor_scopes": [
                        PolicyScope.RUNTIME_AUTHORITY_DELIVERY_READ.value
                    ],
                },
            )
        )
        self.assertEqual(
            delivery_detail["runtime_authority_delivery"]["authority_ref"],
            "remote-docker",
        )

        with self.assertRaises(CpkServerApplicationError) as missing_delivery_revoke:
            planning.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.runtime-authority-delivery.revoke",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={
                        "workspace_id": "workspace-a",
                        "authority_ref": "remote-docker",
                    },
                    payload={
                        "actor_scopes": [
                            PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER.value
                        ],
                        "idempotency_key": "revoke-runtime-authority-delivery-a",
                    },
                    principal=operator_principal(
                        scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER,)
                    ),
                )
            )
        self.assertEqual(missing_delivery_revoke.exception.status, 403)
        self.assertIn(
            "runtime-authority-delivery:revoke",
            missing_delivery_revoke.exception.message,
        )

        revoked_delivery = planning.handle(
            RouteRequest(
                surface="http",
                route_id="command.runtime-authority-delivery.revoke",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={
                    "workspace_id": "workspace-a",
                    "authority_ref": "remote-docker",
                },
                payload={
                    "actor_scopes": [
                        PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REVOKE.value
                    ],
                    "idempotency_key": "revoke-runtime-authority-delivery-a",
                },
            )
        )
        self.assertEqual(revoked_delivery["status"], "revoked")

        with self.assertRaises(CpkServerApplicationError) as missing_revoke_scope:
            planning.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.runtime-authority.revoke",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={
                        "workspace_id": "workspace-a",
                        "authority_ref": "remote-docker",
                    },
                    payload={
                        "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_REGISTER.value],
                        "idempotency_key": "revoke-runtime-authority-a",
                    },
                    principal=operator_principal(
                        scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,)
                    ),
                )
            )
        self.assertEqual(missing_revoke_scope.exception.status, 403)
        self.assertIn("runtime-authority:revoke", missing_revoke_scope.exception.message)

        revoked = planning.handle(
            RouteRequest(
                surface="http",
                route_id="command.runtime-authority.revoke",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={
                    "workspace_id": "workspace-a",
                    "authority_ref": "remote-docker",
                },
                payload={
                    "actor_scopes": [PolicyScope.RUNTIME_AUTHORITY_REVOKE.value],
                    "idempotency_key": "revoke-runtime-authority-a",
                },
            )
        )
        self.assertEqual(revoked["status"], "revoked")

    def test_secret_provider_routes_use_trusted_context_and_public_references(
        self,
    ) -> None:
        self.seed_workspace()
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-b", "Workspace B")
            )
            unit_of_work.commit()
        secret_providers = SecretProviderRegistrationService(self.unit_of_work)
        planning = CpkServerPlanningService(
            RecordingService(),
            secret_providers=secret_providers,
        )
        reads = CpkServerReadService(self.unit_of_work)
        provider_payload = {
            "workspace_id": "workspace-a",
            "provider_id": "workspace-secrets",
            "provider_kind": "control-plane-kit-secrets",
            "display_name": "Workspace secrets",
            "endpoint_reference": "workspace-secrets",
            "credential_reference": "secret://bootstrap/provider/client-token",
            "allowed_reference_prefixes": [
                "secret://workspace-secrets/workspace-a"
            ],
            "allowed_intents": [
                "application.control-token",
                "postgres.password",
            ],
            "admitted_at": "2026-07-30T12:00:00Z",
            "idempotency_key": "provider-a",
            "actor_id": "forged-actor",
            "actor_scopes": [scope.value for scope in PolicyScope],
            "metadata": {"environment": "test"},
        }

        unrelated_scopes = (
            PolicyScope.SECRET_PROVIDER_READ,
            PolicyScope.SECRET_PROVIDER_USE,
            PolicyScope.SECRET_PROVIDER_REVOKE,
            PolicyScope.RUNTIME_AUTHORITY_REGISTER,
            PolicyScope.INGRESS_AUTHORITY_REGISTER,
            PolicyScope.EXECUTION_OPERATE,
        )
        for scope in unrelated_scopes:
            with self.assertRaises(CpkServerApplicationError) as denied:
                planning.handle(
                    RouteRequest(
                        surface="http",
                        route_id="command.secret-provider.register",
                        service_role=ControlPlaneServiceRole.PLANNING,
                        path_parameters={"workspace_id": "workspace-a"},
                        payload=provider_payload,
                        principal=operator_principal(scopes=(scope,)),
                    )
                )
            self.assertEqual(denied.exception.status, 403)

        provider = planning.handle(
            RouteRequest(
                surface="http",
                route_id="command.secret-provider.register",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={"workspace_id": "workspace-a"},
                payload=provider_payload,
                principal=operator_principal(
                    subject_id="trusted-operator",
                    scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
                ),
            )
        )
        self.assertEqual(provider["admitted_by"], "trusted-operator")
        self.assertEqual(provider["endpoint_reference"], "workspace-secrets")
        self.assertEqual(
            provider["credential_reference"],
            "secret://bootstrap/provider/client-token",
        )
        provider_registration_id = str(provider["registration_id"])

        with self.assertRaises(CpkServerApplicationError) as register_cannot_read:
            reads.handle(
                RouteRequest(
                    surface="http",
                    route_id="read.secret-providers",
                    service_role=ControlPlaneServiceRole.READS,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={"actor_scopes": [PolicyScope.SECRET_PROVIDER_READ.value]},
                    principal=operator_principal(
                        scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)
                    ),
                )
            )
        self.assertEqual(register_cannot_read.exception.status, 403)

        listed_providers = reads.handle(
            RouteRequest(
                surface="http",
                route_id="read.secret-providers",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={"workspace_id": "workspace-a"},
                payload={},
                principal=operator_principal(
                    scopes=(PolicyScope.SECRET_PROVIDER_READ,)
                ),
            )
        )
        self.assertEqual(len(listed_providers["items"]), 1)
        self.assertEqual(
            listed_providers["items"][0]["credential_reference"],
            "secret://bootstrap/provider/client-token",
        )

        provider_detail = reads.handle(
            RouteRequest(
                surface="mcp",
                route_id="read.secret-provider-detail",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "provider_id": "workspace-secrets",
                },
                principal=operator_principal(
                    scopes=(PolicyScope.SECRET_PROVIDER_READ,)
                ),
            )
        )
        self.assertEqual(
            provider_detail["secret_provider"]["registration_id"],
            provider_registration_id,
        )

        reference = planning.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.secret-reference.register",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "reference": (
                        "secret://workspace-secrets/workspace-a/postgres/password"
                    ),
                    "provider_registration_id": provider_registration_id,
                    "allowed_intents": ["postgres.password"],
                    "admitted_at": "2026-07-30T12:01:00Z",
                    "idempotency_key": "reference-a",
                    "actor_id": "forged-actor",
                    "actor_scopes": [scope.value for scope in PolicyScope],
                    "metadata": {"purpose": "postgres"},
                },
                principal=operator_principal(
                    subject_id="trusted-operator",
                    scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
                ),
            )
        )
        self.assertEqual(reference["admitted_by"], "trusted-operator")
        reference_registration_id = str(reference["registration_id"])

        listed_references = reads.handle(
            RouteRequest(
                surface="mcp",
                route_id="read.secret-references",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={},
                payload={"workspace_id": "workspace-a"},
                principal=operator_principal(
                    scopes=(PolicyScope.SECRET_PROVIDER_READ,)
                ),
            )
        )
        self.assertEqual(
            listed_references["items"][0]["reference_id"],
            "secret://workspace-secrets/workspace-a/postgres/password",
        )
        reference_detail = reads.handle(
            RouteRequest(
                surface="http",
                route_id="read.secret-reference-detail",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={
                    "workspace_id": "workspace-a",
                    "registration_id": reference_registration_id,
                },
                payload={},
                principal=operator_principal(
                    scopes=(PolicyScope.SECRET_PROVIDER_READ,)
                ),
            )
        )
        self.assertEqual(
            reference_detail["secret_reference"]["provider_registration_id"],
            provider_registration_id,
        )

        with self.assertRaises(CpkServerApplicationError) as cross_workspace:
            reads.handle(
                RouteRequest(
                    surface="http",
                    route_id="read.secret-reference-detail",
                    service_role=ControlPlaneServiceRole.READS,
                    path_parameters={
                        "workspace_id": "workspace-b",
                        "registration_id": reference_registration_id,
                    },
                    payload={},
                    principal=operator_principal(
                        workspace_ids=("workspace-b",),
                        scopes=(PolicyScope.SECRET_PROVIDER_READ,),
                    ),
                )
            )
        self.assertEqual(cross_workspace.exception.status, 404)
        self.assertNotIn(reference_registration_id, cross_workspace.exception.message)

        with self.assertRaises(CpkServerApplicationError) as read_cannot_revoke:
            planning.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.secret-reference.revoke",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={
                        "workspace_id": "workspace-a",
                        "registration_id": reference_registration_id,
                    },
                    payload={
                        "revoked_at": "2026-07-30T12:02:00Z",
                        "idempotency_key": "revoke-reference-a",
                    },
                    principal=operator_principal(
                        scopes=(PolicyScope.SECRET_PROVIDER_READ,)
                    ),
                )
            )
        self.assertEqual(read_cannot_revoke.exception.status, 403)

        revoked_reference = planning.handle(
            RouteRequest(
                surface="http",
                route_id="command.secret-reference.revoke",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={
                    "workspace_id": "workspace-a",
                    "registration_id": reference_registration_id,
                },
                payload={
                    "revoked_at": "2026-07-30T12:02:00Z",
                    "idempotency_key": "revoke-reference-a",
                },
                principal=operator_principal(
                    subject_id="trusted-revoker",
                    scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,),
                ),
            )
        )
        self.assertEqual(revoked_reference["revoked_by"], "trusted-revoker")

        revoked_provider = planning.handle(
            RouteRequest(
                surface="mcp",
                route_id="command.secret-provider.revoke",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "provider_id": "workspace-secrets",
                    "revoked_at": "2026-07-30T12:03:00Z",
                    "idempotency_key": "revoke-provider-a",
                },
                principal=operator_principal(
                    subject_id="trusted-revoker",
                    scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,),
                ),
            )
        )
        self.assertEqual(revoked_provider["status"], "revoked")
        self.assertEqual(revoked_provider["revoked_by"], "trusted-revoker")
        leaked = repr(
            (
                provider,
                listed_providers,
                provider_detail,
                reference,
                listed_references,
                reference_detail,
                revoked_reference,
                revoked_provider,
            )
        ).lower()
        for forbidden in (
            "https://secrets.internal",
            "raw-provider-token",
            "plaintext",
            "ciphertext",
            "bearer ",
        ):
            self.assertNotIn(forbidden, leaked)

    def test_delegation_key_routes_drive_overlap_through_http_and_mcp(self) -> None:
        self.seed_workspace()
        planning = CpkServerPlanningService(
            RecordingService(),
            secret_providers=SecretProviderRegistrationService(self.unit_of_work),
            delegation_signing_keys=DelegationSigningKeyRegistrationService(
                self.unit_of_work
            ),
        )
        reads = CpkServerReadService(self.unit_of_work)
        register_principal = operator_principal(
            subject_id="security-operator",
            scopes=(
                PolicyScope.SECRET_PROVIDER_REGISTER,
                PolicyScope.DELEGATION_KEY_REGISTER,
                PolicyScope.DELEGATION_KEY_ACTIVATE,
                PolicyScope.DELEGATION_KEY_RETIRE,
            ),
        )
        provider = planning.handle(
            RouteRequest(
                surface="http",
                route_id="command.secret-provider.register",
                service_role=ControlPlaneServiceRole.PLANNING,
                path_parameters={"workspace_id": "workspace-a"},
                payload={
                    "provider_id": "delegation-secrets",
                    "provider_kind": "control-plane-kit-secrets",
                    "display_name": "Delegation secrets",
                    "endpoint_reference": "delegation-secrets",
                    "credential_reference": "secret://bootstrap/provider/client-token",
                    "allowed_reference_prefixes": [
                        "secret://delegation-secrets/workspace-a"
                    ],
                    "allowed_intents": ["gateway.probe-signing-key"],
                    "admitted_at": "2026-08-01T10:00:00Z",
                    "idempotency_key": "provider-delegation",
                },
                principal=register_principal,
            )
        )
        for key_id in ("key-a", "key-b"):
            planning.handle(
                RouteRequest(
                    surface="mcp",
                    route_id="command.secret-reference.register",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={},
                    payload={
                        "workspace_id": "workspace-a",
                        "reference": (
                            f"secret://delegation-secrets/workspace-a/{key_id}"
                        ),
                        "provider_registration_id": provider["registration_id"],
                        "allowed_intents": ["gateway.probe-signing-key"],
                        "admitted_at": "2026-08-01T10:01:00Z",
                        "idempotency_key": f"reference-{key_id}",
                    },
                    principal=register_principal,
                )
            )

        public_keys = {
            "key-a": """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
-----END PUBLIC KEY-----
""",
            "key-b": """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=
-----END PUBLIC KEY-----
""",
        }
        for surface, key_id in (("http", "key-a"), ("mcp", "key-b")):
            registered = planning.handle(
                RouteRequest(
                    surface=surface,
                    route_id="command.delegation-key.register",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={
                        "purpose": "gateway-probe",
                        "issuer": "cpk-server-a",
                        "key_id": key_id,
                        "algorithm": "ed25519",
                        "public_key_pem": public_keys[key_id],
                        "private_key_reference": (
                            f"secret://delegation-secrets/workspace-a/{key_id}"
                        ),
                        "admitted_at": "2026-08-01T10:02:00Z",
                        "idempotency_key": f"register-{key_id}",
                    },
                    principal=register_principal,
                )
            )
            self.assertEqual(registered["admitted_by"], "security-operator")
            self.assertNotIn("PUBLIC KEY", str(registered))

        for key_id, activated_at in (
            ("key-a", "2026-08-01T10:03:00Z"),
            ("key-b", "2026-08-01T10:04:00Z"),
        ):
            planning.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.delegation-key.activate",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={
                        "workspace_id": "workspace-a",
                        "issuer": "cpk-server-a",
                        "key_id": key_id,
                    },
                    payload={
                        "activated_at": activated_at,
                        "idempotency_key": f"activate-{key_id}",
                    },
                    principal=register_principal,
                )
            )

        read_principal = operator_principal(
            scopes=(PolicyScope.DELEGATION_KEY_READ,)
        )
        listed = reads.handle(
            RouteRequest(
                surface="http",
                route_id="read.delegation-keys",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={"workspace_id": "workspace-a"},
                payload={},
                principal=read_principal,
            )
        )
        self.assertEqual(
            {item["key_id"]: item["status"] for item in listed["items"]},
            {"key-a": "verify-only", "key-b": "active"},
        )
        configuration = reads.handle(
            RouteRequest(
                surface="mcp",
                route_id="read.gateway-verifier-configuration",
                service_role=ControlPlaneServiceRole.READS,
                path_parameters={},
                payload={
                    "workspace_id": "workspace-a",
                    "gateway_node_id": "gateway-renamed",
                },
                principal=read_principal,
            )
        )["gateway_verifier_configuration"]
        self.assertEqual(
            configuration["audience"],
            "gateway:workspace-a:gateway-renamed",
        )
        self.assertEqual(
            [value["key_id"] for value in configuration["public_keys"]],
            ["key-a", "key-b"],
        )
        self.assertNotIn("private_key_reference", str(configuration))

        with self.assertRaises(CpkServerApplicationError) as denied:
            reads.handle(
                RouteRequest(
                    surface="http",
                    route_id="read.delegation-keys",
                    service_role=ControlPlaneServiceRole.READS,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={},
                    principal=operator_principal(
                        scopes=(PolicyScope.DELEGATION_KEY_REGISTER,)
                    ),
                )
            )
        self.assertEqual(denied.exception.status, 403)

    def test_secret_provider_payload_rejects_raw_endpoint_and_secret_material(
        self,
    ) -> None:
        self.seed_workspace()
        planning = CpkServerPlanningService(
            RecordingService(),
            secret_providers=SecretProviderRegistrationService(self.unit_of_work),
        )
        base = {
            "provider_id": "workspace-secrets",
            "provider_kind": "control-plane-kit-secrets",
            "display_name": "Workspace secrets",
            "endpoint_reference": "workspace-secrets",
            "credential_reference": "secret://bootstrap/provider/client-token",
            "allowed_reference_prefixes": [
                "secret://workspace-secrets/workspace-a"
            ],
            "allowed_intents": ["postgres.password"],
            "admitted_at": "2026-07-30T12:00:00Z",
            "idempotency_key": "provider-a",
        }
        for changed in (
            {"endpoint_reference": "https://secrets.internal"},
            {"credential_reference": "raw-provider-token"},
            {"metadata": {"api_token": "raw-provider-token"}},
            {"metadata": {"note": "Bearer raw-provider-token"}},
        ):
            with self.assertRaises(CpkServerApplicationError) as rejected:
                planning.handle(
                    RouteRequest(
                        surface="http",
                        route_id="command.secret-provider.register",
                        service_role=ControlPlaneServiceRole.PLANNING,
                        path_parameters={"workspace_id": "workspace-a"},
                        payload={**base, **changed},
                        principal=operator_principal(
                            scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)
                        ),
                    )
                )
            self.assertEqual(rejected.exception.status, 400)
            rendered = repr(rejected.exception.descriptor()).lower()
            self.assertNotIn("https://secrets.internal", rendered)
            self.assertNotIn("raw-provider-token", rendered)

    def test_product_import_requires_public_command_idempotency_key(self) -> None:
        product_document = ProductDescriptorCodec().encode_document(
            self.product("hello-server")
        )
        service = CpkServerPlanningService(
            RecordingService(),
            products=ProductRegistrationService(self.unit_of_work),
        )

        with self.assertRaises(CpkServerApplicationError) as raised:
            service.handle(
                RouteRequest(
                    surface="http",
                    route_id="command.product.import",
                    service_role=ControlPlaneServiceRole.PLANNING,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={
                        "descriptor_document": json.loads(
                            product_document.content.decode("utf-8")
                        ),
                        "actor_id": "operator-a",
                        "imported_at": "2026-07-22T10:02:00Z",
                    },
                )
            )

        self.assertEqual(raised.exception.status, 400)
        self.assertIn("idempotency_key", raised.exception.message)

    def ids(self, *values: str):
        remaining = list(values)

        def next_id() -> str:
            if not remaining:
                raise AssertionError("id factory exhausted")
            return remaining.pop(0)

        return next_id

    def product(
        self,
        name: str,
        *,
        digest: str = "sha256:" + "b" * 64,
    ) -> ContainerServerProduct:
        return ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", name, 1),
            image=OciImageReference(
                "ghcr.io",
                f"openj92/control-plane-kit-servers/{name}",
                digest,
                tag="v1",
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
            display_name=name,
            description="Server product used for cpk-server adapter tests.",
        )

    def graph_from_document(self, product: ContainerServerProduct) -> DeploymentGraph:
        block = instantiate_product(product, "app", ProductInstanceConfiguration())
        return compile_topology(
            DeploymentTopology("desired", DockerRuntime(children=(block,)))
        )

    def test_payload_actor_fields_cannot_change_trusted_command_authority(self) -> None:
        recording = RecordingService()
        service = CpkServerApprovalService(recording)

        service.handle(
            RouteRequest(
                surface="http",
                route_id="command.approval.decide",
                service_role=ControlPlaneServiceRole.APPROVAL,
                path_parameters={
                    "workspace_id": "workspace-a",
                    "approval_id": "approval-a",
                },
                payload={
                    "session_id": "session-a",
                    "actor_id": "forged-operator",
                    "actor_scopes": [PolicyScope.INSTANCE_WORKSPACE_READ.value, 17],
                    "decision": "approved",
                    "idempotency_key": "approval-a",
                },
                principal=operator_principal(
                    subject_id="trusted-manager",
                    scopes=(PolicyScope.PLAN_APPROVE,),
                ),
            )
        )

        command = recording.commands[0]
        self.assertEqual(command.actor_id, "trusted-manager")
        self.assertEqual(command.actor_scopes, (PolicyScope.PLAN_APPROVE,))

    def test_application_boundary_requires_one_service_for_every_role(self) -> None:
        services = {
            role: CpkServerUnsupportedService(role) for role in ControlPlaneServiceRole
        }
        application = CpkServerOperationsApplication(services)

        with self.assertRaises(CpkServerApplicationError) as raised:
            application.handle(
                RouteRequest(
                    surface="http",
                    route_id="read.workspace",
                    service_role=ControlPlaneServiceRole.READS,
                    path_parameters={"workspace_id": "workspace-a"},
                    payload={},
                )
            )

        self.assertEqual(raised.exception.status, 501)


if __name__ == "__main__":
    unittest.main()
