"""O2 PostgreSQL fixture: real authoring, plan, admission, claim and start owners.

Only predecessor journal completion is a recorded premise for direct health
tests. It is not provider delivery, receiver acceptance or deployment evidence.
No health first start happens in setUp. Managed coordinator tests do not use
the predecessor premise and instead use the existing recording effect ports.
"""

from contextlib import contextmanager, ExitStack
from dataclasses import replace
from datetime import datetime
import json
import os
from unittest import mock

from control_plane_kit_core.algebra import BlockSockets
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.node_health_read_results import NodeHealthReadOutcome
from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.operations import (
    ControlPlaneServiceRole, EffectAttemptIdentity, EffectAttemptTransition, EffectAttemptTransitionKind, RunId,
)
from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.planning import (
    ObserveManagementBootstrap, ObserveNodeHealth, derive_schedule, project_activity_journal,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec, ProductIdentity,
    ProductReference, ProductRuntimeContract, ProviderRuntimePort,
)
from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfiguration, ReceiverNodeControlConfigurationCodec,
)
from control_plane_kit_core.receiver_health_read_results import ReceiverHealthReadResult
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_core.wrapper_configuration import WorkloadNodeControlConfigurationCodec
from control_plane_kit_operations import health_receiver_trust
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.coordinator import ExecuteActivityRun, ExecutionCoordinator
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyRegistrationService, RevokeDelegationSigningKeyCommand,
)
from control_plane_kit_operations.effect_attempt_start import StartEffectAttempt
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_attempt_fold import FoldEffectAttempt, GuardedHealthEffectFold
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.effect_outcome_evidence import ExecutionEffectOutcome, effect_outcome_transition
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.health_effect_attempt_start import StartHealthEffectAttempt
from control_plane_kit_operations.health_signing_authority import (
    HealthSigningAuthorityReloadService, ReloadHealthSigningAuthority,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority, RunLifecycleCommandService
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.delegation_signing_key_store import DelegationSigningKeyStore
from control_plane_kit_operations.postgres.secret_provider_store import SecretUseAuthorizationStore
from control_plane_kit_operations.records import ActivityEventRecord
from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.execution_lease_recovery_fixture import Sequence
from tests.health_effect_start_fixture import trusted_health_context
from tests.health_receiver_trust_fixture import artifact, bindings, ByteDecoder, public_key, reference, wrapper_environment
from tests.receiver_health_preparation_fixture import receiver_target
from tests.receiver_fresh_execution_fixture import load_execution_context
from tests.runtime_management_fixtures import bootstrap_management_graph
from tests.test_cpk_server_adapters import operator_principal
from tests.test_managed_application_chain import (
    ForbiddenRecovery, ManagedApplicationFixture, RecordingManagedHealth, now,
)


class RecordingReceiverHealth(RecordingManagedHealth):
    def __init__(self, test):
        super().__init__(test)
        self.instrumentation_errors = []

    async def observe_signed(self, realization, request, authority):
        try:
            return await self._observe_signed(realization, request, authority)
        except Exception as error:
            # The coordinator intentionally catches port exceptions. Keep
            # fixture failures outside that catch so uncertainty earns no
            # credit for a broken recording port or an internal assertion.
            self.instrumentation_errors.append(type(error).__name__)
            raise

    async def _observe_signed(self, realization, request, authority):
        test = self.test
        test.assertEqual(test.tracker.active, 0)
        preparation = authority.preparation
        test.assertEqual(preparation.original_event_id, request.effect_id)
        test.assertEqual(preparation.identity.activity_id, request.activity_id.value)
        test.assertEqual(preparation.identity.run_id, request.source.run_id)
        with test.unit_of_work() as uow:
            test.assertEqual(uow.stores.health_effect_preparations.get(preparation.identity), preparation)
        node = DEFAULT_GRAPH_CODEC.decode(realization.desired_graph.graph_descriptor).node(
            preparation.request.target.node_id.value)
        declaration = WorkloadNodeControlSurfaceDeclaration(node.block_spec.control_surfaces[0],
            WorkloadNodeControlSurfaceDeclarationProfile.V2)
        path = (type(request.operation) is ObserveManagementBootstrap
            and request.operation.stage.value == "authenticated-management-path")
        result = ReceiverHealthReadResult(preparation.request, declaration,
            NodeHealthReadOutcome.UNKNOWN if path else NodeHealthReadOutcome.HEALTHY)
        self.signed_reads.append((request, preparation, result))
        return result


class ReceiverHealthExecutionFixture(ManagedApplicationFixture):
    """Reuse fixture owners, without recollecting the V1 application tests."""

    def setUp(self):
        super().setUp()
        self.database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        self.health = RecordingReceiverHealth(self)
        self.addCleanup(lambda: self.assertEqual(self.health.instrumentation_errors, []))

    def authored_graph(self):
        graph = bootstrap_management_graph(self)
        nodes, documents = {}, {}
        self.targets = {"api": receiver_target("api", "a" * 32),
            "gateway": receiver_target("gateway", "b" * 32, "control")}
        self.configurations = {}
        self.decoder = ByteDecoder(health_receiver_trust)
        for name, original in graph.nodes.items():
            node = original
            if name == "gateway":
                # Transit stays on http; own control identity uses control.
                control = replace(node.sockets.providers[0], name="control")
                surface = replace(node.block_spec.control_surfaces[0],
                    provider_socket_name=reference("provider-socket", "control"))
                node = replace(node, sockets=BlockSockets(providers=(*node.sockets.providers, control)),
                    endpoints={**node.endpoints, "control": node.endpoints["http"]},
                    block_spec=replace(node.block_spec, control_surfaces=(surface,)))
            chosen = ()
            if name in self.targets:
                declaration = WorkloadNodeControlSurfaceDeclaration(node.block_spec.control_surfaces[0],
                    WorkloadNodeControlSurfaceDeclarationProfile.V2)
                old_artifact = artifact("workload", declaration, node=name,
                    socket=self.targets[name].provider_socket_name.value)
                old = WorkloadNodeControlConfigurationCodec().decode_bytes(old_artifact.content.encode())
                configuration = ReceiverNodeControlConfiguration(self.targets[name], declaration, old.verifiers)
                self.configurations[name] = configuration
                chosen = (replace(old_artifact,
                    content=ReceiverNodeControlConfigurationCodec().encode_bytes(configuration).decode()),)
                if name == "gateway":
                    chosen = (artifact("transit", declaration, node=name), *chosen)
            environment = (*node.public_environment, *wrapper_environment(chosen)) if chosen else node.public_environment
            contract = ProductRuntimeContract(sockets=node.sockets,
                provider_ports=tuple(ProviderRuntimePort(socket.name, 8000 + index)
                    for index, socket in enumerate(node.sockets.providers)),
                capabilities=node.block_spec.capabilities, control_surfaces=node.block_spec.control_surfaces,
                gateway_transit=node.block_spec.gateway_transit,
                configuration_artifacts=chosen, public_environment=environment)
            document = ProductDescriptorCodec().encode_document(ContainerServerProduct(
                ProductIdentity("test", "receiver-health-" + name, 1),
                OciImageReference("ghcr.io", "test/receiver-health-" + name, "sha256:" + "a" * 64), contract))
            documents[name] = document
            product = ProductReference.from_document(document)
            nodes[name] = replace(node, configuration_artifacts=chosen, public_environment=environment,
                metadata={"product_identity": product.identity.key,
                    "product_descriptor_digest": product.descriptor_sha256.value})
        from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
        graph = replace(graph, nodes=nodes, runtimes={"docker": replace(graph.runtimes["docker"],
            authority_ref=RuntimeAuthorityReference("local-docker"))})
        graph = self.native_before_path_graph(graph)
        validate_graph(graph).require_valid()
        registry = health_receiver_trust.HealthReceiverDecoders(bindings(health_receiver_trust,
            {"transit": documents["gateway"]}, self.decoder))
        return graph, documents, registry

    def next_authored_world(self, world):
        connector = self.graph.node("connector")
        graph = replace(self.graph, nodes={**self.graph.nodes,
            "connector": replace(connector, metadata={**connector.metadata, "health-example": world})})
        documents = {}
        if world == "C":
            node = graph.node("api")
            surface = replace(node.block_spec.control_surfaces[0],
                health_reads=(NodeHealthReadKind.LIVENESS, NodeHealthReadKind.READINESS))
            declaration = WorkloadNodeControlSurfaceDeclaration(surface, WorkloadNodeControlSurfaceDeclarationProfile.V2)
            old = self.configurations["api"]
            health = old.verifiers[-1]
            configured = replace(old, declaration=declaration,
                verifiers=(*old.verifiers[:-1], replace(health, public_keys=(*health.public_keys, public_key("other")))))
            self.configurations["api"] = configured
            chosen = (replace(node.configuration_artifacts[0],
                content=ReceiverNodeControlConfigurationCodec().encode_bytes(configured).decode()),)
            contract = ProductRuntimeContract(sockets=node.sockets, provider_ports=(ProviderRuntimePort("http", 8000),),
                capabilities=node.block_spec.capabilities, control_surfaces=(surface,),
                configuration_artifacts=chosen, public_environment=node.public_environment)
            document = ProductDescriptorCodec().encode_document(ContainerServerProduct(
                ProductIdentity("test", "receiver-health-api", 2),
                OciImageReference("ghcr.io", "test/receiver-health-api", "sha256:" + "a" * 64), contract))
            product = ProductReference.from_document(document)
            documents["api-C"] = document
            graph = replace(graph, nodes={**graph.nodes, "api": replace(node,
                block_spec=replace(node.block_spec, control_surfaces=(surface,)), configuration_artifacts=chosen,
                metadata={"product_identity": product.identity.key,
                    "product_descriptor_digest": product.descriptor_sha256.value})})
        validate_graph(graph).require_valid()
        return graph, documents

    async def prepare_and_start(self):
        """A/B/C selection completes before any request occupies receiver scope."""
        self.require_managed_interface()
        self.app = self.application()
        created = await self.invoke("command.workspace.create", ControlPlaneServiceRole.PLANNING,
            payload={"workspace_id": "workspace-a", "name": "Receiver health", "idempotency_key": "workspace"})
        self.workspace = created["workspace"]
        self.register_prerequisites()
        self.world_contexts = []
        documents = self.documents
        for world in ("A", "B", "C"):
            if world != "A":
                self.graph, documents = self.next_authored_world(world)
            for name, document in documents.items():
                await self.invoke("command.product.import", ControlPlaneServiceRole.PLANNING,
                    path={"workspace_id": "workspace-a"}, payload={"descriptor_document": json.loads(document.content),
                        "imported_at": now(), "idempotency_key": "product-" + name})
            with self.unit_of_work() as uow:
                workspace = uow.stores.workspaces.get("workspace-a")
            expected_desired = None if workspace.desired_graph_id is None else {
                "authored_graph_id": workspace.desired_graph_id,
                "realized_projection_id": workspace.desired_realized_projection_id}
            prepared = await self.invoke("command.deployment.prepare", ControlPlaneServiceRole.PLANNING,
                path={"workspace_id": "workspace-a"}, payload={"desired_graph": DEFAULT_GRAPH_CODEC.encode(self.graph),
                    "expected_current": {"authored_graph_id": workspace.current_graph_id,
                        "realized_projection_id": workspace.current_realized_projection_id},
                    "expected_desired": expected_desired, "expected_desired_graph_revision": workspace.desired_graph_revision,
                    "title": "Receiver health " + world, "idempotency_key": "prepare-" + world})
            self.assertEqual(prepared["status"], "approval-required")
            self.plan_id = prepared["plan_id"]
            detail = await self.invoke("read.plan-detail", ControlPlaneServiceRole.READS,
                path={"workspace_id": "workspace-a", "plan_id": self.plan_id})
            self.plan_descriptor = detail["plan"]
            with self.unit_of_work() as uow:
                record = uow.stores.activity_history.get_plan(self.plan_id)
                self.plan = record.plan
                self.world_contexts.append((record.desired_graph_id, record.desired_realized_projection_id))
            self.assertEqual(self.rows("cpk_execution_requests"), ())
            if world == getattr(self, "selected_world", "A"):
                break
        native, = (activity for activity in self.plan.activities
            if type(activity.operation) is ObserveManagementBootstrap
            and activity.operation.stage.value == "connector-connected")
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

    async def prepare_health(self, *, stage=None):
        await self.prepare_and_start()
        self.integral_observations()
        candidates = tuple(activity for activity in self.plan.activities
            if (type(activity.operation) is ObserveNodeHealth and activity.operation.node_id == "api")
            if stage is None)
        if stage is not None:
            candidates = tuple(activity for activity in self.plan.activities
                if type(activity.operation) is ObserveManagementBootstrap and activity.operation.stage is stage)
        self.assertEqual(len(candidates), 1)
        self.health_activity, = candidates
        self.prepare_health_journal()

    def integral_observations(self):
        # Direct owner tests have no async coordinator's sub-second wait.
        # Keep real database lease/expiry truth, but choose integral-second
        # observations so the first reload is inside its original grant.
        observe = PostgresExecutionStore.observe_request_lease_for_update
        def integral_observation(store, request_id):
            value = observe(store, request_id)
            at = datetime.fromisoformat(value.observed_at.replace("Z", "+00:00"))
            return replace(value, observed_at=at.replace(microsecond=0).isoformat().replace("+00:00", "Z"))
        timer = mock.patch.object(PostgresExecutionStore, "observe_request_lease_for_update", integral_observation)
        timer.start()
        self.addCleanup(timer.stop)

    def prepare_health_journal(self):
        with self.unit_of_work() as uow:
            record = uow.stores.activity_history.get_plan(self.plan_id)
            self.selected_context = (record.desired_graph_id, record.desired_realized_projection_id)
            origins = [uow.stores.graphs.receiver_introduction("workspace-a", target.receiver_id)
                for target in self.targets.values()]
            self.assertTrue(all(origin.first_accepted_action_id is None for origin in origins))
            self.assertTrue(all(origin.introducing_action_id for origin in origins))
            bound = uow.stores.graphs.receiver_bindings("workspace-a", *self.selected_context)
            self.assertEqual({item.receiver_id for item in bound}, {item.receiver_id for item in self.targets.values()})
            # Explicit upstream-ready journal premise. No attempts, provider
            # evidence, acceptance rows or graph pointers are fabricated.
            events = list(uow.stores.execution.events_for_run(self.run_id))
            for _ in range(len(self.plan.activities) + 1):
                journal = project_activity_journal(self.plan, activity_journal_events(events))
                ready = derive_schedule(self.plan, journal.state).ready
                if any(item.activity_id == self.health_activity.activity_id for item in ready):
                    break
                self.assertTrue(ready)
                for kind in (ActivityEventKind.STEP_STARTED, ActivityEventKind.STEP_SUCCEEDED):
                    ordinal = len(events) + 1
                    event = ActivityEventRecord(f"receiver-health-premise-{ordinal}", self.run_id,
                        ordinal, kind, now(), activity_id=ready[0].activity_id.value)
                    uow.stores.execution.add_event(event)
                    events.append(event)
            else:
                self.fail("health activity never became ready under the original dependency graph")
            uow.commit()
        self.assertEqual(self.rows("cpk_health_effect_preparations"), ())
        self.assertEqual(self.rows("cpk_secret_use_authorizations"), ())
        self.start_command = self.health_start_command()

    def health_start_command(self):
        context, run_command = self.execution_context()
        intent = _runtime_effect_intent_for_context(context, self.health_activity)
        identity = EffectAttemptIdentity(RunId(self.run_id), self.health_activity.activity_id.value, 1)
        transition = EffectAttemptTransition(EffectAttemptTransitionKind.STARTED, identity,
            runtime_effect_intent_fingerprint(intent))
        return StartHealthEffectAttempt(StartEffectAttempt(self.request_id, transition, intent,
            run_command.authority, run_command.fence), trusted_health_context())

    def execution_context(self):
        authority = ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,))
        # The claim was made by the authenticated fixture worker.
        with self.unit_of_work() as uow:
            request = uow.stores.execution.get_request(self.request_id)
        authority = replace(authority, worker_id=request.claim.worker_id)
        run_command = ExecuteActivityRun(self.run_id, authority, request.claim.fence,
            IdempotencyKey("receiver-health-context"))
        fold = EffectAttemptFoldService(self.unit_of_work, id_factory=self.ids["effect-fold"])
        execution = ExecutionCoordinator(self.unit_of_work,
            lifecycle=RunLifecycleCommandService(self.unit_of_work, clock=now, id_factory=self.ids["lifecycle"]),
            adapter=self.runtime, start_service=self.start_service(), fold_service=fold,
            reconciliation_service=EffectAttemptReconciliationService(self.unit_of_work, ForbiddenRecovery(), fold),
            clock=now, id_factory=self.ids["execution"])
        context = load_execution_context(execution, run_command)
        return context, run_command

    def start_service(self, *, factory=None, ids=None):
        return EffectAttemptStartService(factory or self.unit_of_work,
            id_factory=self.ids["effect-start"] if ids is None else ids,
            health_receiver_decoders=self.registry)

    def reload_command(self):
        first = self.start_command
        return ReloadHealthSigningAuthority(first.start.request_id, first.start.transition.identity,
            first.context, first.start.authority, first.start.fence)

    def reload_service(self, factory=None, *, registry=None):
        return HealthSigningAuthorityReloadService(factory or self.unit_of_work,
            health_receiver_decoders=self.registry if registry is None else registry)

    def health_fold(self, preparation):
        with self.unit_of_work() as uow:
            intent = uow.stores.effect_attempt_intents.get(preparation.identity)
            runtime = uow.stores.runtime_authorities.get_active_for_update(
                "workspace-a", self.start_command.start.intent.authority_ref)
        outcome = ExecutionEffectOutcome(preparation.identity, intent.request_fingerprint,
            RuntimeEffectResult.succeeded(preparation.original_event_id))
        return GuardedHealthEffectFold(FoldEffectAttempt(self.request_id, effect_outcome_transition(outcome),
            self.start_command.start.authority, self.start_command.start.fence, None, outcome),
            self.start_command.context, intent, preparation, runtime)

    def fold(self, command, factory=None, *, ids=None):
        factory = factory or self.unit_of_work
        return EffectAttemptFoldService(factory, id_factory=ids or Sequence("receiver-health-folded")).execute_health(
            command, signing_authority=self.reload_service(factory))

    def revoke_workload_key(self):
        key = self.keys["workload"]
        return DelegationSigningKeyRegistrationService(self.unit_of_work).revoke(RevokeDelegationSigningKeyCommand(
            workspace_id="workspace-a", purpose=key.purpose, issuer=key.issuer, key_id=key.key_id,
            revoked_by="operator-a", revoked_at=now(), actor_scopes=(PolicyScope.DELEGATION_KEY_REVOKE,)))

    def receiver_snapshot(self):
        return tuple((name, self.rows(name)) for name in ("cpk_workspaces", "cpk_graph_versions",
            "cpk_realized_graph_projections", "cpk_graph_receiver_introductions",
            "cpk_graph_receiver_bindings", "cpk_operation_actions"))

    @contextmanager
    def forbid_current_health(self):
        # Counters are checked outside the production sanitization boundary.
        # An AssertionError swallowed into a normal refusal is still a failure.
        patches = []
        with ExitStack() as stack:
            for owner, method in ((PostgresExecutionStore, "observe_request_lease_for_update"),
                    (DelegationSigningKeyStore, "require_unambiguous_active"),
                    (SecretUseAuthorizationStore, "add"), (ByteDecoder, "decode")):
                patches.append(stack.enter_context(mock.patch.object(owner, method,
                    side_effect=AssertionError("history entered current health: " + method))))
            try:
                yield
            finally:
                self.assertEqual([patch.call_count for patch in patches], [0] * len(patches))
