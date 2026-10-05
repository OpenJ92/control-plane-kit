"""One receiver setup; ordinary D1 is real, cleanup execution is recorded only."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import psycopg
import rfc8785

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCompletion, configuration_invocation_correlation_for_request,
    configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.planning import (
    StartNode, StartRuntime, WaitForHealthy, StopNode, RemoveNodeResource,
    StopRuntime, RemoveRuntimeResource,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.products import (
    ProductDescriptorCodec, ProductIdentity, ProductInstanceConfiguration, instantiate_product,
)
from control_plane_kit_core.runtime_effect_observation import RuntimeEffectIntent, RuntimeEffectIntentSource
from control_plane_kit_core.runtime_effects import RuntimeEffectKind, RuntimeEffectResult
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, compile_topology, validate_graph
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.approvals import ApprovalCommandService, RequestApproval, DecideApproval
from control_plane_kit_operations.configuration_cleanup import (
    ConfigurationCleanupExpectedContext, ConfigurationCleanupSourceSelector,
    configuration_cleanup_proposal_fingerprint,
)
from control_plane_kit_operations.configuration_cleanup_planning import (
    ConfigurationCleanupPlanningService, InspectConfigurationCleanup, RequestConfigurationCleanupPlan,
)
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    _encode_runtime_effect_intent, _decode_runtime_effect_intent,
)
from control_plane_kit_operations.effect_run_prefix import _lock_effect_run_prefix
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_evidence import _joined_read
from control_plane_kit_operations.products import InlineDescriptorSource
from control_plane_kit_operations.receiver_execution_scopes import (
    ExecutionReceiverScope, derive_execution_receiver_scopes,
)
from control_plane_kit_operations.receiver_lifecycle import derive_receiver_bindings
from control_plane_kit_operations.records import (
    ActivityRunRecord, AdmittedRun, ApprovalDecisionKind, RetryIdentity,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.configuration_cleanup_postgres_fixture import command_context
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds, RecordingRuntimeAdapter
from tests.receiver_canonical_acceptance_fixture import ReceiverCanonicalAcceptanceFixture
from tests.receiver_scope_history_fixture import insert_recorded_request
from tests.test_postgres_configuration_evidence import _ObservedConnection
from tests.test_receiver_acceptance_advancement import ReceiverAcceptanceAdvancementTests
from tests.test_runtime_effect_translation import _configuration_product


class ConfigurationCleanupReadCeilingsFixture(ReceiverCanonicalAcceptanceFixture):
    accept_receiver = ReceiverAcceptanceAdvancementTests.accept_receiver

    def prepare_ceiling_premise(self):
        """No second setup, direct receiver graph save, or cleanup admission."""
        canonical = self.canonical_receiver_graph
        runtime = canonical.runtimes["docker"]
        with self.unit_of_work() as uow:
            self.registration = uow.stores.runtime_authorities.get("workspace-a", runtime.authority_ref)
        product = _configuration_product().descriptor_document.product
        product = replace(product, identity=ProductIdentity("test", "cleanup-target", 1),
            runtime_contract=replace(product.runtime_contract,
                configuration_artifacts=product.runtime_contract.configuration_artifacts[:1]))
        with self.unit_of_work() as uow:
            uow.stores.registered_products.register(workspace_id="workspace-a",
                descriptor_document=ProductDescriptorCodec().encode_document(product),
                source=InlineDescriptorSource(), imported_by="operator-a", imported_at=self.now())
            uow.commit()
        block = instantiate_product(product, "cleanup-target",
            ProductInstanceConfiguration.from_contract(product.runtime_contract))
        target = compile_topology(DeploymentTopology("selected", DockerRuntime(
            runtime_id="docker", authority_ref=runtime.authority_ref, children=(block,))))
        validate_graph(target).require_valid()
        self.desired_receiver("ceilings-install", graph=target)
        _, plan, _ = self.plan_and_admit("ceilings-install")
        self.assertCountEqual([type(a.operation) for a in plan.plan.activities],
            [StartRuntime, StartNode, WaitForHealthy])
        by_type = {type(a.operation): a for a in plan.plan.activities}
        self.assertEqual(by_type[StartRuntime].operation.target.runtime_id, "docker")
        for operation in (StartNode, WaitForHealthy):
            self.assertEqual(by_type[operation].operation.target.node_id, "cleanup-target")
        activity = by_type[StartNode]
        claimed = self.ready_run("ceilings-install")

        def installed(_context, request):
            self.assertEqual(request.operation, by_type[type(request.operation)].operation)
            self.assertEqual(request.activity_id, by_type[type(request.operation)].activity_id)
            if type(request.operation) is not StartNode:
                self.assertIn(type(request.operation), (StartRuntime, WaitForHealthy))
                self.assertIsNone(request.configuration_instances)
                return RuntimeEffectResult.succeeded(request.effect_id,
                    evidence={"fixture_premise": "simulated-ordinary-runtime-or-health"})
            correlated = configuration_invocation_correlation_for_request(request)
            self.assertEqual(len(correlated.selection.instances), 1)
            completion = ConfigurationInvocationCompletion(correlated.request_fingerprint,
                configuration_invocation_selection_fingerprint(correlated.selection))
            return RuntimeEffectResult.succeeded(request.effect_id, evidence={
                "adapter": "simulated-total-configuration-invocation",
                "configuration_invocation_completion": completion.descriptor()})

        adapter = RecordingRuntimeAdapter(installed, installed, installed)
        result = self.coordinator(self.unit_of_work, adapter, "ceilings-install").execute(
            replace(self.execution_command(claimed, "ceilings-install"), max_effects=3))
        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual([request.activity_id for _, request in adapter.runtime_calls],
            [by_type[kind].activity_id for kind in (StartRuntime, StartNode, WaitForHealthy)])
        self.assertEqual(adapter.legacy_calls, [])
        self.source_identity = EffectAttemptIdentity(RunId(claimed.run.run_id), activity.activity_id.value, 1)
        with self.unit_of_work() as uow:
            self.completion = uow.stores.configuration_completions.get(self.source_identity)
            self.assertIsNotNone(self.completion, "selected source must have actual admitted D1")
            source = uow.stores.effect_attempt_intents.get(self.source_identity)
            self.assertEqual(len(source.intent.configuration_instances.instances), 1)
            self.selected_ref = source.intent.configuration_instances.instances[0]
        self.assertEqual((self.selected_ref.runtime_id, self.selected_ref.node_id,
            self.selected_ref.artifact_id), ("docker", "cleanup-target", "settings"))
        self.advance(claimed, "ceilings-install")

        self.assert_registration_unchanged()
        # A real full teardown avoids unsupported managed UpdateDeployment.
        self.desired_receiver("ceilings-depart", graph=DeploymentGraph("ceilings-empty"))
        _, departure, _ = self.plan_and_admit("ceilings-depart")
        self.assertCountEqual([type(a.operation) for a in departure.plan.activities],
            [StopNode, RemoveNodeResource, StopRuntime, RemoveRuntimeResource])
        departed = self.ready_run("ceilings-depart")
        removed = RecordingRuntimeAdapter()
        result = self.coordinator(self.unit_of_work, removed, "ceilings-depart").execute(
            replace(self.execution_command(departed, "ceilings-depart"), max_effects=1024))
        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertCountEqual([(request.activity_id, request.operation) for _, request in removed.runtime_calls],
            [(a.activity_id, a.operation) for a in departure.plan.activities])
        self.assertEqual(removed.legacy_calls, [])
        self.advance(departed, "ceilings-depart")
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
            current = DEFAULT_GRAPH_CODEC.decode(uow.stores.realized_graphs.get(
                workspace.current_realized_projection_id).graph_descriptor)
            self.assertFalse(current.nodes or current.runtimes or current.edges
                or current.public_ingresses or current.delegation_authorities)
        self.assert_registration_unchanged()
        _, self.companion_acceptance, self.companion_origin = self.accept_receiver("ceilings-companion")
        self.assert_registration_unchanged()
        self.assertEqual(self.receiver_origin(), self.companion_origin)
        self._publish_cleanup(runtime)

    def assert_registration_unchanged(self):
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.runtime_authorities.get("workspace-a", self.registration.authority_ref),
                self.registration)

    def _publish_cleanup(self, runtime):
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
        pins = ConfigurationCleanupExpectedContext(workspace.current_graph_id,
            workspace.current_realized_projection_id, workspace.desired_graph_id,
            workspace.desired_realized_projection_id, workspace.desired_graph_revision)
        self.assertEqual(pins.base_graph_id, pins.desired_graph_id)
        self.assertEqual(pins.base_realized_projection_id, pins.desired_realized_projection_id)
        query = InspectConfigurationCleanup("session-a", "workspace-a", pins, (
            ConfigurationCleanupSourceSelector(self.source_identity, "settings", self.selected_ref),))
        service = ConfigurationCleanupPlanningService(self.unit_of_work, clock=self.now,
            id_factory=GeneratedIds("ceilings-cleanup"))
        inspected = service.inspect(query, context=command_context())
        self.assertEqual(inspected.state, "complete")
        self.plan = service.request_plan(RequestConfigurationCleanupPlan("session-a", "workspace-a",
            IdempotencyKey("ceilings-cleanup"), pins, query.selectors,
            inspected.inspection.evidence_digest), context=command_context()).plan_record
        approvals = ApprovalCommandService(self.unit_of_work, clock=self.now,
            id_factory=GeneratedIds("ceilings-approval"))
        self.approval = approvals.execute(RequestApproval("session-a", self.plan.plan_id, "operator-a",
            tuple(PolicyScope), IdempotencyKey("ceilings-ask"))).request
        self.decision = approvals.execute(DecideApproval("session-a", self.approval.request_id, "manager-a",
            tuple(PolicyScope), ApprovalDecisionKind.APPROVED, IdempotencyKey("ceilings-decide"))).decision
        activity = self.plan.plan.activities[0]
        self.identity = EffectAttemptIdentity(RunId("read-ceilings-run"), activity.activity_id.value, 1)
        self.intent = RuntimeEffectIntent(RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1, runtime.kind,
            RuntimeEffectIntentSource("workspace-a", "read-ceilings-request", self.identity.run_id,
                self.plan.plan_id, self.plan.base_graph_id, self.plan.desired_graph_id),
            activity.activity_id, activity.operation, runtime.authority_ref, (), ())
        now = self.now()
        lease = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with self.unit_of_work() as uow:
            # Explicit recorded request/run premise; never an admission receipt.
            insert_recorded_request(uow.stores.connection, request_id=self.intent.source.request_id,
                workspace_id="workspace-a", session_id="session-a", plan_id=self.plan.plan_id,
                approval_request_id=self.approval.request_id, approval_decision_id=self.decision.decision_id,
                idempotency_key="read-ceilings-recorded", intent_fingerprint="recorded-history-only",
                requested_at=now, status="claimed", claim_worker_id="recorded-worker", claim_generation=1,
                claimed_at=now, lease_expires_at=lease)
            uow.stores.execution._add_run(ActivityRunRecord(self.identity.run_id.value, self.plan.plan_id,
                AdmittedRun(self.intent.source.request_id), RetryIdentity(1), ActivityRunStatus.RUNNING,
                now, started_at=now))
            uow.commit()

    @contextmanager
    def read_premise(self):
        observations = dict(bytes=0, rows=0, largest_cell=0, statements=0)
        factory = lambda: _ObservedConnection(psycopg.connect(self.database_url), observations)
        with _configuration_accounting(self.identity.run_id.value), PostgresUnitOfWork(factory) as uow:
            with _joined_read(uow.stores.connection) as read:
                guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                request = uow.stores.execution.get_request_for_update(self.intent.source.request_id)
                prefix = _lock_effect_run_prefix(uow, request, self.identity.run_id.value, latest_required=True)
                yield uow, guard, prefix, read, observations

    def assert_existing_premise(self, uow, guard, prefix):
        stores = uow.stores
        request = prefix.request
        original, derived = stores.execution._receiver_execution_material(request.identity, guard)
        plan, base, desired = original
        self.assertEqual(plan, self.plan)
        self.assertEqual(derived, derive_execution_receiver_scopes(request.identity, plan, base, desired))
        self.assertEqual(derived.scopes, (ExecutionReceiverScope("docker"),))
        bindings = []
        for projection in (base, desired):
            graph = stores.graphs.get(projection.source_authored_graph_id)
            stored = stores.graphs.receiver_bindings("workspace-a", graph.graph_id, projection.projection_id)
            self.assertEqual(stored, derive_receiver_bindings("workspace-a", graph.graph_id,
                projection.projection_id, projection.graph_descriptor))
            self.assertEqual(stored, derive_receiver_bindings("workspace-a", graph.graph_id,
                projection.projection_id, graph.graph_descriptor))
            self.assertTrue(stored)
            self.assertEqual(tuple((b.runtime_id, b.node_id, b.receiver_id) for b in stored),
                (("docker", "api", "a" * 32),))
            material = DEFAULT_GRAPH_CODEC.decode(projection.graph_descriptor)
            self.assertNotIn("cleanup-target", material.nodes)
            self.assertEqual(material.runtimes["docker"].authority_ref, self.intent.authority_ref)
            self.assertEqual(material.runtimes["docker"].kind, self.intent.runtime_kind)
            bindings.append(stored)
        self.assertEqual(*bindings)
        self.assertIsNotNone(self.intent.authority_ref)
        registration = stores.runtime_authorities.get("workspace-a", self.intent.authority_ref)
        self.assertEqual(registration.status.value, "active")
        self.assertEqual(registration, self.registration)
        self.assertEqual(registration.runtime_kind, self.intent.runtime_kind)
        self.assertEqual(registration.authority_ref, self.intent.authority_ref)
        self.assertEqual(stores.graphs.receiver_introduction("workspace-a", "a" * 32), self.companion_origin)
        self.assertEqual(self.companion_origin.first_accepted_action_id, self.companion_acceptance.action.action_id)
        self.assertEqual(stores.activity_history.get_plan(self.plan.plan_id), self.plan)
        self.assertEqual(request.approval_request_id, self.approval.request_id)
        self.assertEqual(request.approval_decision_id, self.decision.decision_id)
        self.assertTrue(self.approval.destructive)
        self.assertEqual(self.approval.subject, ActivityPlanApprovalSubject(self.plan.plan_id,
            proposal_fingerprint=configuration_cleanup_proposal_fingerprint(self.plan.cleanup_proposal)))
        self.assertEqual(self.intent.operation.instances, (self.selected_ref,))
        self.assertEqual(stores.configuration_completions.get(self.source_identity), self.completion)
        self.assertEqual(_decode_runtime_effect_intent(_encode_runtime_effect_intent(self.intent)), self.intent)
        prefix.require(uow, request, self.identity.run_id.value, latest_required=True)
        for table in ("cpk_effect_attempt_intents", "cpk_effect_attempts"):
            self.assertEqual(stores.connection.execute(f"SELECT count(*) FROM {table} WHERE run_id=%s",
                (self.identity.run_id.value,)).fetchone(), (0,))
        self.assertEqual(stores.connection.execute("SELECT count(*) FROM cpk_activity_events WHERE run_id=%s",
            (self.identity.run_id.value,)).fetchone(), (0,))
        for table in ("cpk_configuration_cleanup_reservations", "cpk_configuration_cleanup_members",
                "cpk_configuration_invocation_closures", "cpk_configuration_claim_closures"):
            self.assertEqual(stores.connection.execute(f"SELECT count(*) FROM {table}").fetchone(), (0,))

    def width_evidence(self, connection):
        """Actual PostgreSQL representation, independent of proposed issuer."""
        row = connection.execute("SELECT payload::text FROM cpk_activity_plans WHERE plan_id=%s",
            (self.plan.plan_id,)).fetchone()
        sql_payload = row[0].encode("utf-8")
        import json
        self.assertGreater(len(sql_payload), len(rfc8785.dumps(json.loads(row[0]))))
        widths = connection.execute("SELECT octet_length(graph_descriptor::text),octet_length(metadata::text) "
            "FROM cpk_graph_versions WHERE graph_id=%s", (self.plan.base_graph_id,)).fetchone()
        self.assertGreater(widths[0], 0)
        self.assertGreater(widths[1], 0)
        return len(sql_payload), widths, len(_encode_runtime_effect_intent(self.intent))

    def ceiling_truth(self):
        """Originals, approvals, execution, accepted membership and cleanup truth."""
        extra = ("cpk_activity_plans", "cpk_approval_requests", "cpk_approval_decisions",
            "cpk_runtime_authorities",
            "cpk_configuration_claims", "cpk_effect_configuration_refs",
            "cpk_configuration_invocation_completions", "cpk_configuration_acceptances",
            "cpk_configuration_accepted_slots", "cpk_configuration_cleanup_reservations",
            "cpk_configuration_cleanup_members", "cpk_configuration_invocation_closures",
            "cpk_configuration_claim_closures", "cpk_configuration_cleanup_member_outcomes")
        return self.acceptance_truth(), tuple((table, self.connection.execute(
            f"SELECT to_jsonb(t) FROM {table} AS t ORDER BY to_jsonb(t)::text").fetchall()) for table in extra)
