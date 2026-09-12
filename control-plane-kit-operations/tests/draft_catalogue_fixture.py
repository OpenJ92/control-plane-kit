"""Disposable Postgres fixture for the #1762 catalogue boundary."""

from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec
import os
import uuid

import psycopg
from psycopg import sql

from control_plane_kit_core.algebra import (
    BlockSockets, DeploymentTopology, DockerRuntime, ProviderSocket,
)
from control_plane_kit_core.identity import (
    AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind, WorkspaceGrant,
)
from control_plane_kit_core.operations import (
    ControlPlaneServiceRole, operator_command_http_routes, operator_read_http_routes,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec,
    ProductIdentity, ProductInstanceConfiguration, ProductRuntimeContract,
    instantiate_product,
)
from control_plane_kit_core.topology import DeploymentGraph, compile_topology
from control_plane_kit_core.types import Protocol
from control_plane_kit_operations.cpk_server import CpkServerPlanningService, CpkServerReadService
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.products import InlineDescriptorSource
from control_plane_kit_operations.records import GraphVersionRecord, WorkspaceRecord
from control_plane_kit_operations.workflows import (
    IdempotencyKey, OperationCommandService, StartOperationSession,
)


NOW = "2026-09-06T18:00:00Z"


def principal(workspace="workspace-a", scopes=None):
    return AuthenticatedPrincipal(
        PrincipalIdentity("urn:test:draft-catalogue", "operator-a", PrincipalKind.OPERATOR),
        (WorkspaceGrant(workspace, tuple(PolicyScope) if scopes is None else scopes),),
    )


@dataclass(frozen=True)
class CatalogueRequest:
    surface: str
    route_id: str
    service_role: ControlPlaneServiceRole
    path_parameters: dict
    payload: dict
    principal: AuthenticatedPrincipal


class DraftCatalogueFixture:
    """Setup only: no tests, replacement stores, or model of catalogue behavior."""

    def setUp(self):
        self.database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not self.database_url:
            raise RuntimeError("Run ./control-plane-kit-operations/test.sh for Docker Postgres")
        self.schema = "draft_catalogue_" + uuid.uuid4().hex
        self.connection = psycopg.connect(self.database_url, autocommit=True)
        self.addCleanup(self.connection.close)
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        self.connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        install_schema(self.connection)
        self.product = ContainerServerProduct(
            identity=ProductIdentity("catalogue", "app", 1),
            image=OciImageReference("ghcr.io", "example/app", "sha256:" + "b" * 64),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
            display_name="Catalogue app",
        )
        self.document = ProductDescriptorCodec().encode_document(self.product)
        with self.unit_of_work() as uow:
            for workspace in ("workspace-a", "workspace-b"):
                uow.stores.workspaces.create(WorkspaceRecord(workspace, workspace))
                graph = GraphVersionRecord.from_graph(
                    graph_id=workspace + "-current", workspace_id=workspace, version=1,
                    graph=DeploymentGraph("current"), created_by="operator-a", created_at=NOW,
                )
                uow.stores.graphs.save(graph)
                uow.stores.workspaces.set_current_graph(workspace, graph.graph_id)
                uow.stores.workspaces.set_desired_graph(workspace, graph.graph_id)
            self.register_product(uow, "workspace-a")
            uow.commit()
        self.sessions = {
            workspace: self.start_session(workspace) for workspace in ("workspace-a", "workspace-b")
        }

    def drop_schema(self):
        self.connection.execute("SET search_path TO public")
        self.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def unit_of_work(self):
        return PostgresUnitOfWork(lambda: psycopg.connect(
            self.database_url,
            options=f"-c search_path={self.schema} -c lock_timeout=5000 -c statement_timeout=10000",
        ))

    def start_session(self, workspace):
        result = OperationCommandService(
            self.unit_of_work, clock=lambda: NOW, id_factory=lambda: uuid.uuid4().hex,
        ).execute(StartOperationSession(workspace, "operator-a", "Draft authoring", IdempotencyKey(uuid.uuid4().hex)))
        return result.session.session_id

    def register_product(self, uow, workspace):
        return uow.stores.registered_products.register(
            workspace_id=workspace, descriptor_document=self.document,
            source=InlineDescriptorSource(), imported_by="operator-a", imported_at=NOW,
        )

    def graph(self, name="draft"):
        block = instantiate_product(self.product, "app", ProductInstanceConfiguration())
        return compile_topology(DeploymentTopology(name, DockerRuntime(children=(block,))))

    def require_catalogue_interface(self):
        routes = {route.route_id for route in (*operator_command_http_routes(), *operator_read_http_routes())}
        required = {"command.desired-topology-draft.create", "command.desired-topology-draft.revise",
                    "read.desired-topology-drafts", "read.desired-topology-draft-revisions",
                    "read.desired-topology-draft-revision"}
        self.assertFalse(required - routes, "missing public draft catalogue routes")
        self.assertIsNotNone(find_spec("control_plane_kit_operations.desired_topology_drafts"),
                             "missing public draft catalogue command module")
        api = import_module("control_plane_kit_operations.desired_topology_drafts")
        for name in ("DesiredTopologyDraftCommandService", "CreateDesiredTopologyDraft", "ReviseDesiredTopologyDraft",
                     "DesiredTopologyDraftError", "DesiredTopologyDraftConflict"):
            self.assertTrue(callable(getattr(api, name, None)), f"missing public catalogue API {name}")

    def require_catalogue_relations(self):
        for table in ("cpk_desired_topology_drafts", "cpk_desired_topology_draft_revisions"):
            self.assertIsNotNone(self.connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0],
                                 f"missing durable catalogue relation {table}")

    def planning_adapter(self, commands):
        self.require_catalogue_interface()
        try:
            return CpkServerPlanningService(None, desired_topology_drafts=commands)
        except TypeError as error:
            if "unexpected keyword argument 'desired_topology_drafts'" not in str(error):
                raise
            self.fail("public planning adapter does not accept the draft catalogue command service")

    def catalogue(self, *, id_factory=None, unit_of_work_factory=None, clock=None):
        # Deliberately inside the test path: the existing package still collects
        # while the proposed public catalogue API is absent on the causal-red base.
        from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftCommandService

        return DesiredTopologyDraftCommandService(
            unit_of_work_factory or self.unit_of_work,
            clock=clock or (lambda: NOW), id_factory=id_factory or (lambda: uuid.uuid4().hex),
        )

    def create_command(self, *, key="create", title="Draft A", graph=None, workspace="workspace-a"):
        from control_plane_kit_operations.desired_topology_drafts import CreateDesiredTopologyDraft

        return CreateDesiredTopologyDraft(
            context=principal(workspace).command_context(workspace),
            session_id=self.sessions[workspace], title=title,
            graph=self.graph() if graph is None else graph, idempotency_key=IdempotencyKey(key),
        )

    def revise_command(self, created, *, key="revise", expected=1, graph=None, session_id=None):
        from control_plane_kit_operations.desired_topology_drafts import ReviseDesiredTopologyDraft

        return ReviseDesiredTopologyDraft(
            context=principal().command_context("workspace-a"),
            session_id=session_id or self.sessions["workspace-a"], draft_id=created.draft_id,
            expected_head_revision=expected, graph=self.graph("revised") if graph is None else graph,
            idempotency_key=IdempotencyKey(key),
        )

    def create(self, **kwargs):
        return self.catalogue().execute(self.create_command(**kwargs))

    def read(self, route="read.desired-topology-drafts", *, surface="http", workspace="workspace-a", **values):
        path = {"workspace_id": workspace}
        for key in ("draft_id", "revision"):
            if key in values:
                path[key] = values.pop(key)
        if surface == "mcp":
            values = {**path, **values}
            path = {}
        else:
            path = {key: str(value) for key, value in path.items()}
        return CpkServerReadService(self.unit_of_work).handle(CatalogueRequest(
            surface, route, ControlPlaneServiceRole.READS, path, values, principal(workspace),
        ))

    def rows(self, table):
        return self.connection.execute(
            sql.SQL("SELECT to_jsonb(t) FROM {} AS t ORDER BY to_jsonb(t)::text").format(sql.Identifier(table))
        ).fetchall()

    def catalogue_truth(self):
        return {table: self.rows(table) for table in (
            "cpk_desired_topology_drafts", "cpk_desired_topology_draft_revisions",
            "cpk_graph_versions", "cpk_operation_actions",
        )}

    def runtime_truth(self):
        return {table: self.rows(table) for table in (
            "cpk_workspaces", "cpk_realized_graph_projections", "cpk_activity_plans",
            "cpk_approval_requests", "cpk_approval_decisions", "cpk_execution_requests",
            "cpk_activity_runs", "cpk_effect_attempts", "cpk_activity_events", "cpk_observations",
        )}
