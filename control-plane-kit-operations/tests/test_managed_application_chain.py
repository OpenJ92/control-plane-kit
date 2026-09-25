"""Managed deployment through the authenticated Operations application.

Postgres and all Operations owners are real. Only runtime, ingress-provider,
receiver-decoder and health-transport ports are recording implementations.
No predecessor events, attempts, owned ingress or generated tokens are seeded.
This proves composition, not live Docker/Cloudflare reachability or signatures.
"""
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.identity import (
    AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind, WorkspaceGrant,
)
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.node_health_read_results import NodeHealthReadOutcome, NodeHealthReadResult
from control_plane_kit_core.operations import ControlPlaneServiceRole, EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import (
    ManagementBootstrapStage, ObserveManagementBootstrap, ObserveNodeHealth,
    StartNode, StartRuntime, compile_graph_activity_plan, derive_schedule, project_activity_journal,
)
from control_plane_kit_core.planning.saga import SagaStepId
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec,
    ProductIdentity, ProductReference, ProductRuntimeContract, ProviderRuntimePort,
)
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference, SecretProviderId, SecretReference, SecretUseIntent,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, validate_graph
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations import coordinator, health_receiver_trust
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.admission import ExecutionAdmissionCommandService
from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService
from control_plane_kit_operations.approvals import ApprovalCommandService
from control_plane_kit_operations.coordinator import (
    ActivityExecutionDispatcher, ExecutionCoordinator, ExecutionCoordinatorConflict,
    ExecutionCoordinatorDenied, RuntimeInterpreterDispatcher,
)
from control_plane_kit_operations.cpk_server import (
    CpkServerApplicationError, CpkServerOperationsApplication, cpk_server_services,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_outcome_evidence import (
    NativeConnectionObservation, NativeConnectionOutcome,
)
from control_plane_kit_operations.health_signing_authority import HealthSigningAuthorityReloadService
from control_plane_kit_operations.ingress_authorities import CloudflareZoneIngressAuthority, IngressAuthorityProviderKind
from control_plane_kit_operations.ingress_realization import IngressRealizationAdapter
from control_plane_kit_operations.lifecycle import RunLifecycleCommandService
from control_plane_kit_operations.planning import ActivityPlanningCommandService, DesiredGraphCommandService
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.products import ProductRegistrationService
from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand, RegisterSecretReferenceCommand,
    SecretProviderKind, SecretProviderRegistrationService, SecretUseAuthorizationService,
)
from control_plane_kit_operations.workflows import OperationCommandService
from control_plane_kit_operations.workspaces import WorkspaceCommandService
from tests import postgres_health_effect_start_fixture as health_registration_fixture
from tests.health_receiver_trust_fixture import ByteDecoder, artifact, bindings
from tests.runtime_management_fixtures import bootstrap_management_graph
from tests.test_cpk_server_adapters import GeneratedIds, RouteRequest, operator_principal, worker_principal
from tests.test_ingress_realization import RecordingIngressInterpreter, TrackingUnitOfWorkFactory


def now():
    instant = datetime.now(timezone.utc)
    return instant.isoformat(timespec="microseconds" if instant.microsecond else "seconds").replace("+00:00", "Z")


class ForbiddenRecovery:
    def observe(self, *args, **kwargs):
        raise AssertionError("completed native reads must not enter mutation recovery")


class RecordingRuntime:
    """Provider-boundary state only; never writes Operations records."""

    def __init__(self, test):
        self.test = test
        self.calls = []
        self.containers = {}

    def execute(self, request):
        raise AssertionError("the managed runtime must use registered authority")

    def execute_with_authority(self, request, authority):
        test = self.test
        test.assertEqual(test.tracker.active, 0)
        test.assertEqual(authority.authority_ref, request.authority_ref)
        test.assertTrue(test.health.selections, "complete health preflight must precede mutations")
        with test.unit_of_work() as uow:
            event = uow.stores.execution.get_event(request.effect_id)
            test.assertEqual(event.activity_id, request.activity_id.value)
        test.assertIsInstance(request.operation, (StartRuntime, StartNode))
        self.calls.append(request)
        if type(request.operation) is StartNode:
            node_id = request.operation.target.node_id
            test.assertNotIn(node_id, self.containers, "a container was allocated twice")
            self.containers[node_id] = hashlib.sha256(request.effect_id.encode()).hexdigest()
            if node_id == "connector":
                generated = test.rows("cpk_generated_ingress_secret_references")
                test.assertEqual(len(generated), 1)
                token_reference = generated[0][2]
                grants = tuple(grant for grant in request.secret_resolution_grants
                    if grant.permits(SecretReference(token_reference), SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN))
                test.assertEqual(len(grants), 1, "connector creation requires the actually generated token grant")
        return RuntimeEffectResult.succeeded(request.effect_id,
            evidence={"recording_provider": True, "activity_id": request.activity_id.value})


class RecordingManagedHealth:
    def __init__(self, test):
        self.test = test
        self.selections = []
        self.native_reads = []
        self.signed_reads = []
        self.responses = [NativeConnectionOutcome.DISCONNECTED,
            NativeConnectionOutcome.UNKNOWN, NativeConnectionOutcome.CONNECTED]

    def select(self, *, plan, current, desired, registered_products, runtime_authorities):
        # Pure capability selection: no database/provider reads or inferred authority.
        selected = tuple(activity.activity_id for activity in plan.activities
            if type(activity.operation) in (ObserveManagementBootstrap, ObserveNodeHealth))
        self.test.assertTrue(selected)
        self.test.assertFalse(current.nodes)
        self.test.assertEqual(set(desired.nodes), {"gateway", "connector", "api"})
        self.test.assertEqual(len(registered_products), 3)
        self.test.assertTrue(any(value.authority_ref == desired.runtimes["docker"].authority_ref
            for value in runtime_authorities))
        self.selections.append(selected)
        return selected

    async def observe_connection(self, realization, request, authority):
        test = self.test
        test.assertEqual(test.tracker.active, 0)
        test.assertIs(request.operation.stage, ManagementBootstrapStage.CONNECTOR_CONNECTED)
        test.assertEqual(request.effect_id, realization.intent_event.event_id)
        test.assertEqual(request.activity_id, realization.activity.activity_id)
        test.assertEqual(authority.authority_ref, request.authority_ref)
        test.assertIn("connector", test.runtime.containers)
        test.assertTrue(self.responses, "an unrequested native read was dispatched")
        kind = self.responses.pop(0)
        self.native_reads.append(request)
        if kind is NativeConnectionOutcome.UNKNOWN:
            return NativeConnectionObservation(kind, request.effect_id, request.activity_id.value)
        stamp = now()
        return NativeConnectionObservation(kind, request.effect_id, request.activity_id.value,
            test.runtime.containers["connector"], stamp, stamp,
            1 if kind is NativeConnectionOutcome.CONNECTED else 0,
            "11111111-1111-4111-8111-111111111111")

    async def observe_signed(self, realization, request, authority):
        test = self.test
        test.assertEqual(test.tracker.active, 0)
        preparation = authority.preparation
        test.assertEqual(preparation.original_event_id, request.effect_id)
        test.assertEqual(preparation.identity.activity_id, request.activity_id.value)
        test.assertEqual(preparation.identity.run_id.value, request.source.run_id.value)
        with test.unit_of_work() as uow:
            test.assertEqual(uow.stores.health_effect_preparations.get(preparation.identity), preparation)
        node = DEFAULT_GRAPH_CODEC.decode(realization.desired_graph.graph_descriptor).node(
            preparation.request.target.node_id.value)
        declarations = tuple(WorkloadNodeControlSurfaceDeclaration(surface,
            WorkloadNodeControlSurfaceDeclarationProfile.V2) for surface in node.block_spec.control_surfaces)
        declaration, = (value for value in declarations
            if value.identity() == preparation.request.declaration_identity)
        path = (type(request.operation) is ObserveManagementBootstrap
            and request.operation.stage is ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)
        result = NodeHealthReadResult(preparation.request, declaration,
            NodeHealthReadOutcome.UNKNOWN if path else NodeHealthReadOutcome.HEALTHY)
        self.signed_reads.append((request, preparation, result))
        return result


class ManagedApplicationFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError("Run ./control-plane-kit-operations/test.sh with its owning PostgreSQL service")
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.addCleanup(self.connection.close)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.tracker = TrackingUnitOfWorkFactory(database_url)
        self.unit_of_work = self.tracker
        self.health = RecordingManagedHealth(self)
        self.runtime = RecordingRuntime(self)
        self.ingress = RecordingIngressInterpreter(self.tracker)
        self.ids = {name: GeneratedIds("chain-" + name) for name in (
            "workspace", "plan", "desired", "approval", "admission", "lifecycle",
            "session", "execution", "effect-start", "effect-fold", "advance")}
        # This is the injected owner's first ID, also pinned in authored receiver bytes.
        self.authored_revision = "chain-desired-1"
        self.graph, self.documents, self.registry = self.authored_graph()
        self.worker = worker_principal(scopes=tuple(PolicyScope))
        self.execution_number = 0

    def require_managed_interface(self):
        parameters = inspect.signature(ExecutionCoordinator).parameters
        for name in ("managed_health", "health_signing_authority"):
            self.assertIn(name, parameters, "managed execution owner composition is missing")
        self.assertTrue(callable(getattr(ExecutionCoordinator, "execute_managed", None)))
        self.assertTrue(callable(getattr(ExecutionCoordinator, "reobserve", None)))
        self.assertIsNotNone(getattr(coordinator, "ReobserveConnectorConnection", None))

    def authored_graph(self):
        graph = bootstrap_management_graph(self)
        nodes, documents, decoder_bindings = {}, {}, []
        decoder = ByteDecoder(health_receiver_trust)
        for name, node in graph.nodes.items():
            families = ("transit", "workload") if name == "gateway" else (
                ("workload",) if name == "api" else ())
            chosen = ()
            if families:
                declaration = WorkloadNodeControlSurfaceDeclaration(node.block_spec.control_surfaces[0],
                    WorkloadNodeControlSurfaceDeclarationProfile.V2)
                chosen = tuple(artifact(family, declaration, node=name, revision=self.authored_revision)
                    for family in families)
            product = ContainerServerProduct(ProductIdentity("test", "chain-" + name, 1),
                OciImageReference("ghcr.io", "test/chain-" + name, "sha256:" + "a" * 64),
                ProductRuntimeContract(sockets=node.sockets,
                    provider_ports=tuple(ProviderRuntimePort(socket.name, 8000) for socket in node.sockets.providers),
                    capabilities=node.block_spec.capabilities, control_surfaces=node.block_spec.control_surfaces,
                    gateway_transit=node.block_spec.gateway_transit, configuration_artifacts=chosen))
            document = ProductDescriptorCodec().encode_document(product)
            documents[name] = document
            reference = ProductReference.from_document(document)
            nodes[name] = replace(node, configuration_artifacts=chosen, metadata={
                "product_identity": reference.identity.key,
                "product_descriptor_digest": reference.descriptor_sha256.value})
            decoder_bindings.extend(bindings(health_receiver_trust,
                {family: document for family in families}, decoder))
        graph = replace(graph, nodes=nodes, runtimes={"docker": replace(graph.runtimes["docker"],
            authority_ref=RuntimeAuthorityReference("local-docker"))})
        graph = self.native_before_path_graph(graph)
        validate_graph(graph).require_valid()
        return graph, documents, health_receiver_trust.HealthReceiverDecoders(tuple(decoder_bindings))

    def native_before_path_graph(self, graph):
        # The overlap tests need a completed native wait while independent PATH
        # is still available. Core orders sibling observations by hashed IDs,
        # so choose only a lawful authored hostname, using its real compiler.
        # This is a bounded test-input choice, not a required runtime ordering:
        # preserve all products, permissions and dependency edges, and let the
        # application compile again. Tests check its actual plan before effects.
        current = validate_graph(DeploymentGraph("empty"))
        for number in range(1, 17):
            candidate = replace(graph, public_ingresses=(replace(graph.public_ingresses[0],
                hostname=f"cpk-gateway-{number:03d}.openj92.dev"),))
            plan = compile_graph_activity_plan(current, validate_graph(candidate))
            native, = (activity for activity in plan.activities
                if type(activity.operation) is ObserveManagementBootstrap
                and activity.operation.stage is ManagementBootstrapStage.CONNECTOR_CONNECTED)
            path, = (activity for activity in plan.activities
                if type(activity.operation) is ObserveManagementBootstrap
                and activity.operation.stage is ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)
            self.assertEqual(native.dependencies, path.dependencies)
            if plan.activities.index(native) < plan.activities.index(path):
                return candidate
        self.fail("bounded authored hostnames did not provide native-before-PATH fixture ordering")

    def application(self):
        uow = self.unit_of_work
        lifecycle = RunLifecycleCommandService(uow, clock=now, id_factory=self.ids["lifecycle"])
        fold = EffectAttemptFoldService(uow, id_factory=self.ids["effect-fold"])
        authorizer = SecretUseAuthorizationService(uow)
        adapter = ActivityExecutionDispatcher(
            runtime=RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: self.runtime}, authorizer),
            ingress=IngressRealizationAdapter(uow,
                {IngressAuthorityProviderKind.CLOUDFLARE: self.ingress}, now, authorizer))
        execution = ExecutionCoordinator(uow, lifecycle=lifecycle, adapter=adapter,
            start_service=EffectAttemptStartService(uow, id_factory=self.ids["effect-start"],
                health_receiver_decoders=self.registry), fold_service=fold,
            reconciliation_service=EffectAttemptReconciliationService(uow, ForbiddenRecovery(), fold),
            clock=now, id_factory=self.ids["execution"], managed_health=self.health,
            health_signing_authority=HealthSigningAuthorityReloadService(uow,
                health_receiver_decoders=self.registry))
        return CpkServerOperationsApplication(cpk_server_services(unit_of_work_factory=uow,
            planning=ActivityPlanningCommandService(uow, clock=now, id_factory=self.ids["plan"]),
            workspaces=WorkspaceCommandService(uow, clock=now, id_factory=self.ids["workspace"]),
            products=ProductRegistrationService(uow),
            desired_graphs=DesiredGraphCommandService(uow, clock=now, id_factory=self.ids["desired"]),
            approval=ApprovalCommandService(uow, clock=now, id_factory=self.ids["approval"]),
            admission=ExecutionAdmissionCommandService(uow, clock=now, id_factory=self.ids["admission"]),
            lifecycle=lifecycle, operations=OperationCommandService(uow, clock=now,
                id_factory=self.ids["session"]), execution=execution,
            advancement=CurrentGraphAdvancementCommandService(uow, clock=now, id_factory=self.ids["advance"]),
            clock=lambda: datetime.now(timezone.utc)))

    async def invoke(self, route, role, *, path=None, payload=None, principal=None, app=None, surface="http"):
        path, payload = dict(path or {}), dict(payload or {})
        if surface == "mcp":
            path, payload = {}, {**path, **payload}
        return dict(await (self.app if app is None else app).handle_async(RouteRequest(
            surface=surface, route_id=route, service_role=role, path_parameters=path,
            payload=payload, principal=operator_principal() if principal is None else principal)))

    def register_prerequisites(self):
        # Registration-only helper: it does not call seed_ready_health or seed events.
        health_registration_fixture.PostgresHealthEffectStartFixture.seed_health_registrations(self)
        service = SecretProviderRegistrationService(self.unit_of_work)
        for name, intent in (("cloudflare", SecretUseIntent.CLOUDFLARE_API_TOKEN),
                ("generated", SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN)):
            provider = service.register_provider(RegisterSecretProviderCommand(
                workspace_id="workspace-a", provider_id=SecretProviderId(name),
                provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS, display_name=name,
                endpoint_reference=SecretProviderEndpointReference("workspace-secrets"),
                credential_reference=SecretReference("secret://bootstrap/provider-token"),
                allowed_reference_prefixes=(SecretReference("secret://" + name + "/ingress"),),
                allowed_intents=(intent,), admitted_by="operator-a", admitted_at=now(),
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
            if name == "cloudflare":
                api_token = SecretReference("secret://cloudflare/ingress/api-token")
                service.register_reference(RegisterSecretReferenceCommand(workspace_id="workspace-a",
                    reference=api_token, provider_registration_id=provider.registration_id,
                    allowed_intents=(intent,), admitted_by="operator-a", admitted_at=now(),
                    actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
            else:
                generated = provider
        with self.unit_of_work() as uow:
            uow.stores.runtime_authorities.register(workspace_id="workspace-a",
                authority_ref=self.graph.runtimes["docker"].authority_ref, runtime_kind=RuntimeKind.DOCKER,
                authority=LocalDockerSocketAuthority(), admitted_by="operator-a", admitted_at=now())
            uow.stores.ingress_authorities.register(workspace_id="workspace-a",
                authority_ref=self.graph.public_ingresses[0].authority_ref,
                authority=CloudflareZoneIngressAuthority(account_id="account-chain", zone_id="zone-chain",
                    zone_name="openj92.dev", api_token_ref=api_token,
                    allowed_hostname_pattern="cpk-gateway-*.openj92.dev",
                    generated_secret_provider_registration_id=generated.registration_id,
                    generated_secret_reference_prefix=SecretReference("secret://generated/ingress")),
                admitted_by="operator-a", admitted_at=now())
            uow.commit()

    def rows(self, relation):
        return tuple(self.connection.execute("SELECT * FROM " + relation + " ORDER BY 1, 2, 3").fetchall())

    def durable_snapshot(self):
        return tuple((name, self.rows(name)) for name in (
            "cpk_activity_events", "cpk_activity_runs", "cpk_execution_requests",
            "cpk_execution_command_receipts", "cpk_effect_attempts", "cpk_effect_attempt_intents",
            "cpk_effect_attempt_outcomes", "cpk_health_effect_preparations",
            "cpk_secret_use_authorizations", "cpk_cloudflare_ingress_resources",
            "cpk_generated_ingress_secret_references"))

    def effects(self):
        return (tuple(self.runtime.calls), tuple(self.ingress.create_allocation_names),
            tuple(self.ingress.create_custody_grants), tuple(self.health.native_reads), tuple(self.health.signed_reads))

    def history(self):
        with self.unit_of_work() as uow:
            return uow.stores.execution.events_for_run(self.run_id)

    def native_attempt(self, number):
        identity = EffectAttemptIdentity(RunId(self.run_id), self.native_id, number)
        with self.unit_of_work() as uow:
            attempt = uow.stores.effect_attempts.get(identity)
            outcome = uow.stores.effect_outcomes.get(identity, attempt.latest_transition_event.event_id)
            return attempt, outcome

    def mutation_records(self, requests):
        records = []
        with self.unit_of_work() as uow:
            for request in requests:
                identity = EffectAttemptIdentity(request.source.run_id, request.activity_id.value, 1)
                intent = uow.stores.effect_attempt_intents.get(identity)
                attempt = uow.stores.effect_attempts.get(identity)
                self.assertEqual(attempt.state.status.value, "succeeded")
                outcome = uow.stores.effect_outcomes.get(identity, attempt.latest_transition_event.event_id)
                records.append((intent, attempt, outcome))
        return tuple(records)

    async def prepare_and_start(self):
        self.require_managed_interface()
        self.app = self.application()
        workspace = await self.invoke("command.workspace.create", ControlPlaneServiceRole.PLANNING,
            payload={"workspace_id": "workspace-a", "name": "Managed chain", "idempotency_key": "workspace"})
        self.workspace = workspace["workspace"]
        self.register_prerequisites()
        for name, document in self.documents.items():
            await self.invoke("command.product.import", ControlPlaneServiceRole.PLANNING,
                path={"workspace_id": "workspace-a"}, payload={"descriptor_document": json.loads(document.content),
                    "imported_at": now(), "idempotency_key": "product-" + name})
        for name in ("cpk_cloudflare_ingress_resources", "cpk_generated_ingress_secret_references",
                "cpk_effect_attempts", "cpk_effect_attempt_outcomes", "cpk_secret_use_authorizations"):
            self.assertEqual(self.rows(name), (), name)
        self.assertEqual(self.effects(), ((), (), (), (), ()))
        prepared = await self.invoke("command.deployment.prepare", ControlPlaneServiceRole.PLANNING,
            path={"workspace_id": "workspace-a"}, payload={"desired_graph": DEFAULT_GRAPH_CODEC.encode(self.graph),
                "expected_current": {"authored_graph_id": self.workspace["current_graph_id"],
                    "realized_projection_id": self.workspace["current_realized_projection_id"]},
                "expected_desired": None, "expected_desired_graph_revision": self.workspace["desired_graph_revision"],
                "title": "Managed Hello", "idempotency_key": "prepare"})
        self.assertEqual(prepared["status"], "approval-required")
        self.plan_id = prepared["plan_id"]
        detail = await self.invoke("read.plan-detail", ControlPlaneServiceRole.READS,
            path={"workspace_id": "workspace-a", "plan_id": self.plan_id})
        self.plan_descriptor = detail["plan"]
        self.assertEqual(self.plan_descriptor["desired_graph_id"], self.authored_revision)
        with self.unit_of_work() as uow:
            self.plan = uow.stores.activity_history.get_plan(self.plan_id).plan
        native, = (activity for activity in self.plan.activities
            if type(activity.operation) is ObserveManagementBootstrap
            and activity.operation.stage is ManagementBootstrapStage.CONNECTOR_CONNECTED)
        self.native_id = native.activity_id.value
        await self.invoke("command.approval.decide", ControlPlaneServiceRole.APPROVAL,
            path={"workspace_id": "workspace-a", "approval_id": prepared["approval_request_id"]},
            payload={"session_id": self.plan_descriptor["session_id"], "decision": "approved", "idempotency_key": "approve"},
            principal=operator_principal(subject_id="manager-a", scopes=(PolicyScope.PLAN_APPROVE,)))
        admitted = await self.invoke("command.deployment.admit", ControlPlaneServiceRole.ADMISSION,
            path={"workspace_id": "workspace-a", "plan_id": self.plan_id}, payload={
                "session_id": self.plan_descriptor["session_id"], "approval_request_id": prepared["approval_request_id"],
                "readiness": [], "idempotency_key": "admit"})
        self.request_id = admitted["execution_request_id"]
        claimed = await self.invoke("command.run.claim", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": self.request_id}, principal=self.worker,
            payload={"lease_duration_seconds": 1800, "idempotency_key": "claim"})
        self.run_id, self.generation = claimed["run_id"], claimed["claim_generation"]
        await self.invoke("command.run.start", ControlPlaneServiceRole.EXECUTION,
            path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker,
            payload={"claim_generation": self.generation, "idempotency_key": "start"})

    async def execute_one(self):
        self.execution_number += 1
        return await self.invoke("command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
            path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker,
            payload={"claim_generation": self.generation, "idempotency_key": f"execute-{self.execution_number}",
                "max_effects": 1})

    async def reach_native_wait(self):
        await self.prepare_and_start()
        for _ in range(len(self.plan.activities) + 2):
            result = await self.execute_one()
            if self.health.native_reads:
                break
            self.assertEqual(result["coordinator_status"], "progressed", result)
        self.assertEqual(len(self.health.native_reads), 1)
        # Independent PATH/readiness may progress; ordinary execute must not re-read native.
        for _ in range(len(self.plan.activities) + 2):
            result = await self.execute_one()
            self.assertEqual(len(self.health.native_reads), 1)
            if result["coordinator_status"] != "progressed":
                break
        self.assertEqual(result["run_status"], "running")
        self.assertEqual(result["coordinator_status"], "blocked")
        journal = project_activity_journal(self.plan, activity_journal_events(self.history()))
        self.assertEqual(journal.state.step(SagaStepId(self.native_id)).status.value, "waiting")
        self.assertFalse(derive_schedule(self.plan, journal.state).successful)
        attempt, retained = self.native_attempt(1)
        self.assertEqual(attempt.state.status.value, "not_ready")
        self.assertIs(retained.outcome.observation.outcome, NativeConnectionOutcome.DISCONNECTED)
        self.assertEqual(len(self.rows("cpk_cloudflare_ingress_resources")), 1)
        self.assertEqual(len(self.rows("cpk_generated_ingress_secret_references")), 1)
        self.assertEqual(self.ingress.create_active_counts, [0])
        self.assertEqual(len(self.ingress.create_custody_grants), 1)
        current = await self.invoke("read.current-graph", ControlPlaneServiceRole.READS,
            path={"workspace_id": "workspace-a"})
        self.assertEqual(current["graph_id"], self.workspace["current_graph_id"])

    async def reobserve(self, prior, key, **options):
        return await self.invoke("command.deployment.reobserve-connector", ControlPlaneServiceRole.EXECUTION,
            path={"workspace_id": "workspace-a", "run_id": self.run_id},
            payload={"activity_id": self.native_id, "prior_attempt": prior,
                "claim_generation": self.generation, "idempotency_key": key},
            principal=options.pop("principal", self.worker), **options)


class ManagedApplicationChainTests(ManagedApplicationFixture):
    async def test_empty_deployment_waits_then_explicit_reads_finish_without_recreating(self):
        await self.reach_native_wait()
        first = self.native_attempt(1)
        previous_events = self.history()
        mutation_calls = tuple(self.runtime.calls)
        mutation_records = self.mutation_records(mutation_calls)
        owned = self.rows("cpk_cloudflare_ingress_resources")
        tokens = self.rows("cpk_generated_ingress_secret_references")
        second_receipt = await self.reobserve(1, "observe-next-2")
        self.assertEqual(len(self.health.native_reads), 2)
        second = self.native_attempt(2)
        self.assertEqual(second[0].state.status.value, "not_ready")
        self.assertIs(second[1].outcome.observation.outcome, NativeConnectionOutcome.UNKNOWN)
        self.assertEqual(second[0].state.prior_attempt, first[0].state.identity)
        await self.reobserve(2, "observe-next-3")
        self.assertEqual(len(self.health.native_reads), 3)
        third = self.native_attempt(3)
        self.assertEqual(third[0].state.status.value, "succeeded")
        self.assertEqual(third[0].state.prior_attempt, second[0].state.identity)
        self.assertEqual(len({request.effect_id for request in self.health.native_reads}), 3)
        self.assertEqual(tuple(self.runtime.calls), mutation_calls)
        self.assertEqual(self.mutation_records(mutation_calls), mutation_records)
        self.assertEqual(self.native_attempt(1), first)
        self.assertEqual(self.native_attempt(2), second)
        self.assertEqual(self.history()[:len(previous_events)], previous_events)
        self.assertEqual(self.rows("cpk_cloudflare_ingress_resources"), owned)
        self.assertEqual(self.rows("cpk_generated_ingress_secret_references"), tokens)
        before, effects = self.durable_snapshot(), self.effects()
        replay = await self.reobserve(1, "observe-next-2", app=self.application(), surface="mcp")
        self.assertEqual(replay, second_receipt)
        self.assertEqual((self.durable_snapshot(), self.effects()), (before, effects))
        for _ in range(len(self.plan.activities) + 2):
            result = await self.execute_one()
            if result["coordinator_status"] == "completed":
                break
            self.assertEqual(result["coordinator_status"], "progressed", result)
        self.assertEqual((result["coordinator_status"], result["run_status"]), ("completed", "succeeded"))
        self.assertEqual(len(self.ingress.create_allocation_names), 1)
        self.assertEqual(len(self.ingress.create_custody_grants), 1)
        self.assertEqual(Counter(request.operation.target.node_id for request in self.runtime.calls
            if type(request.operation) is StartNode), Counter({"gateway": 1, "connector": 1, "api": 1}))
        self.assertEqual(sum(type(request.operation) is StartRuntime for request in self.runtime.calls), 1)
        self.assertEqual(self.health.responses, [])
        path_results = [value for request, _, value in self.health.signed_reads
            if type(request.operation) is ObserveManagementBootstrap
            and request.operation.stage is ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH]
        self.assertEqual(len(path_results), 1)
        self.assertIs(path_results[0].outcome, NodeHealthReadOutcome.UNKNOWN)
        self.assertEqual(self.rows("cpk_cloudflare_ingress_resources"), owned)
        self.assertEqual(self.rows("cpk_generated_ingress_secret_references"), tokens)
        self.assertEqual(self.mutation_records(mutation_calls), mutation_records)
        plan = self.plan_descriptor
        advanced = await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker,
            payload={"plan_id": self.plan_id, "expected_current_graph_id": self.workspace["current_graph_id"],
                "expected_current_realized_projection_id": self.workspace["current_realized_projection_id"],
                "desired_graph_id": plan["desired_graph_id"],
                "desired_realized_projection_id": plan["desired_realized_projection_id"],
                "expected_desired_graph_revision": plan["desired_graph_revision"],
                "claim_generation": self.generation, "idempotency_key": "advance"})
        self.assertEqual(advanced["to_graph_id"], plan["desired_graph_id"])

    async def test_explicit_next_read_requires_current_actor_workspace_and_unconsumed_predecessor(self):
        await self.reach_native_wait()
        for principal, error_type in (
                (worker_principal(subject_id="foreign-worker", scopes=tuple(PolicyScope)), ExecutionCoordinatorDenied),
                (operator_principal(workspace_ids=("foreign-workspace",)), CpkServerApplicationError),
                (worker_principal(scopes=()), CpkServerApplicationError)):
            before, effects = self.durable_snapshot(), self.effects()
            with self.assertRaises(error_type):
                await self.reobserve(1, "denied-next", principal=principal)
            self.assertEqual((self.durable_snapshot(), self.effects()), (before, effects))
        before, effects = self.durable_snapshot(), self.effects()
        with self.assertRaises(ExecutionCoordinatorDenied):
            await self.invoke("command.deployment.reobserve-connector", ControlPlaneServiceRole.EXECUTION,
                path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker,
                payload={"activity_id": self.native_id, "prior_attempt": 1,
                    "claim_generation": self.generation + 1, "idempotency_key": "bad-fence"})
        self.assertEqual((self.durable_snapshot(), self.effects()), (before, effects))
        await self.reobserve(1, "accepted-next")
        before, effects = self.durable_snapshot(), self.effects()
        with self.assertRaises(ExecutionCoordinatorConflict):
            await self.reobserve(1, "different-key-consumed-predecessor")
        self.assertEqual((self.durable_snapshot(), self.effects()), (before, effects))

    async def test_completed_reobserve_replay_binds_issuer_kind_and_complete_grants(self):
        await self.reach_native_wait()
        receipt = await self.reobserve(1, "retained-next")
        identity = self.worker.identity
        all_scopes = tuple(PolicyScope)
        reduced_scopes = tuple(scope for scope in all_scopes
            if scope is not PolicyScope.DELEGATION_KEY_REGISTER)
        for label, issuer, kind, scopes in (
                ("issuer", "urn:test:another-authenticator", identity.kind, all_scopes),
                ("kind", identity.issuer, PrincipalKind.SERVICE, all_scopes),
                ("grants", identity.issuer, identity.kind, reduced_scopes)):
            with self.subTest(changed=label):
                principal = AuthenticatedPrincipal(
                    PrincipalIdentity(issuer, identity.subject_id, kind),
                    (WorkspaceGrant("workspace-a", scopes),))
                self.assertEqual(principal.identity.subject_id, identity.subject_id)
                self.assertIn(PolicyScope.EXECUTION_OPERATE, scopes)
                before, effects = self.durable_snapshot(), self.effects()
                with self.assertRaises(ExecutionCoordinatorConflict):
                    await self.reobserve(1, "retained-next", principal=principal, app=self.application())
                self.assertEqual((self.durable_snapshot(), self.effects()), (before, effects))
        before, effects = self.durable_snapshot(), self.effects()
        self.assertEqual(await self.reobserve(1, "retained-next", app=self.application()), receipt)
        self.assertEqual((self.durable_snapshot(), self.effects()), (before, effects))

    async def test_incomplete_configured_health_selection_refuses_before_creation(self):
        await self.prepare_and_start()
        select = self.health.select

        def incomplete_selection(**values):
            selected = select(**values)
            self.assertGreater(len(selected), 1)
            return selected[:-1]

        with mock.patch.object(self.health, "select", side_effect=incomplete_selection):
            result = await self.execute_one()
        self.assertTrue(self.health.selections, "the configured port must participate in preflight")
        self.assertEqual(result["coordinator_status"], "unsupported")
        self.assertEqual(result["effects_attempted"], 0)
        self.assertEqual(self.effects(), ((), (), (), (), ()))
        self.assertEqual(self.ingress.create_active_counts, [])
        for relation in ("cpk_effect_attempts", "cpk_effect_attempt_intents", "cpk_effect_attempt_outcomes",
                "cpk_health_effect_preparations", "cpk_secret_use_authorizations",
                "cpk_cloudflare_ingress_resources", "cpk_generated_ingress_secret_references"):
            self.assertEqual(self.rows(relation), (), relation)
