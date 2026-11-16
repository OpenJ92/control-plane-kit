"""#1987 original-source publication laws and retained scalar input measurements."""
import os
import json
import unittest
import traceback
from collections import Counter
from dataclasses import replace
from unittest import mock
from hashlib import sha256

import psycopg
from psycopg.types.json import Jsonb
import rfc8785

from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_core.planning import (
    ActivityId, AllocatePublicIngress, ManagementBootstrapStage, ObserveManagementBootstrap,
    ObserveNodeHealth, StartNode,
)
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec,
    ProductIdentity, ProductReference, ProductRuntimeContract, ProviderRuntimePort,
)
from control_plane_kit_core.public_ingress import PublicIngressTarget
from control_plane_kit_core.secrets import SecretUseIntent
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_core.topology.graph import Endpoint, LiteralAddress
from control_plane_kit_core.planning.saga import SagaStepId, project_activity_journal
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead
from control_plane_kit_operations.postgres.execution import _activity_event
from control_plane_kit_operations.runtime_adapter_registration import RuntimeAdapterRegistration
from tests.configuration_cleanup_phase_read_bounds_fixture import _PhaseConnection
from tests.test_managed_application_chain import ManagedApplicationFixture, management_routes
from tests.health_receiver_trust_fixture import artifact, wrapper_environment
from tests.test_ingress_realization import RecordingIngressInterpreter
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.receiver_management_routes import ReceiverManagementRoutesCodec
from control_plane_kit_operations.effect_outcome_evidence import NativeConnectionOutcome
from tests.test_postgres_configuration_installation_capacity import (
    PostgresConfigurationInstallationCapacityTests, measurement_wire_report,
)
from tests.test_postgres_configuration_transfer_producer_receiver import ProfiledRecordingRuntime
from tests.test_postgres_configuration_publication_bounds import (
    _publication_budget_observation, _assert_publication_and_cold_fit,
    _BudgetObservedConnection, _trace, _within,
)
from control_plane_kit_operations.advancement import (
    CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict,
)
from control_plane_kit_operations.postgres import install_schema, SchemaInstallationError


class PostgresAcceptedInstallationSourcePublicationTests(unittest.TestCase):
    def setUp(self):
        self.capacity = PostgresConfigurationInstallationCapacityTests()
        self.addCleanup(self.capacity.doCleanups)
        self.member = self.capacity.installation_witness()
        self.member.setUp()

    def replay_service(self):
        def unexpected():
            raise AssertionError("replay must not allocate identities or timestamps")
        return CurrentGraphAdvancementCommandService(self.member.fixture.unit_of_work,
            clock=unexpected, id_factory=unexpected)

    def assert_full_receipt_fits(self, result, trace):
        cold = _trace()
        with _configuration_accounting(("1987-cold", result.desired_graph_revision)):
            with PostgresUnitOfWork(lambda: _BudgetObservedConnection(
                    psycopg.connect(self.member.fixture.database_url), cold, cold=True)) as uow:
                _, action, event = uow.stores.configuration_acceptance._receipt(
                    "workspace-a", result.desired_graph_revision)
                self.assertEqual((action, event), (result.action, result.event))
        for phase in trace["phases"].values():
            _within(self, cold["accounting"].used, phase["future"].settled)
            for peak in cold["peaks"]:
                _within(self, peak, phase["future"].peak)
        self.assertEqual(cold["accounting"].used.statements, cold["wire"]["statements"])

    def test_selected_consumer_acceptance_commits_exact_original_source(self):
        with _publication_budget_observation(self, self.member.fixture.database_url) as (trace, factory):
            result = self.member.advance(factory)
        _assert_publication_and_cold_fit(self, trace, self.member.fixture.database_url)
        preimage, count, digest = self.member.connection.execute(
            "SELECT installation_sources_preimage,installation_source_count,installation_sources_digest "
            "FROM cpk_configuration_acceptances WHERE workspace_id=%s AND pinned_revision=%s",
            ("workspace-a", result.desired_graph_revision)).fetchone()
        original = self.member.original
        with self.member.fixture.unit_of_work() as uow:
            attempt = uow.stores.effect_attempts.get(original.identity)
            outcome = uow.stores.effect_outcomes.get(original.identity, attempt.latest_transition_event.event_id)
        expected = {"profile": "accepted-installation-sources.v1", "bindings": [{
            "runtime_id": "runtime-a", "node_id": "api",
            "source": {"run_id": "run-config", "activity_id": "start-api", "attempt": 1},
            "accepted_revision": result.desired_graph_revision,
            "original_request_fingerprint": original.request_fingerprint,
            "outcome_fingerprint": outcome.outcome.outcome_fingerprint,
            "generated_allocations": [],
        }]}
        self.assertEqual(json.loads(preimage), expected)
        self.assertEqual(preimage.encode(), rfc8785.dumps(expected))
        self.assertEqual(count, 1)
        self.assertEqual(digest, sha256(b"cpk.accepted-installation-sources.v1\0" + preimage.encode()).hexdigest())
        for evidence in (result.event.evidence.descriptor(), result.action.payload):
            self.assertEqual(evidence["installation_source_count"], count)
            self.assertEqual(evidence["installation_sources_digest"], digest)
        # This witness has real direct completion evidence. Its existing
        # transfer owner releases protective claims at acceptance.
        self.assertEqual(self.member.protective_claims(), [(*row[:-1], False) for row in self.member.claims])
        self.assertEqual(self.member.connection.execute("SELECT count(*) FROM cpk_configuration_claim_transfers "
            "WHERE run_id='run-config'").fetchone(), (2,))
        before = self.member.fixture.retained_snapshot()
        replay = self.member.advance()
        self.assertTrue(replay.replayed)
        self.assertEqual((replay.event, replay.action), (result.event, result.action))
        self.assertEqual(self.member.fixture.retained_snapshot(), before)
        self.assert_full_receipt_fits(result, trace)
        install_schema(self.member.connection)

    def test_corrupted_source_commitment_refuses_replay_and_schema_without_repair(self):
        result = self.member.advance()
        self.member.connection.execute("UPDATE cpk_configuration_acceptances "
            "SET installation_sources_preimage=installation_sources_preimage || ' ' "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (result.desired_graph_revision,))
        before = self.member.fixture.retained_snapshot()
        with self.assertRaises(CurrentGraphAdvancementConflict):
            self.replay_service().execute(self.member.command())
        with self.assertRaises(SchemaInstallationError):
            install_schema(self.member.connection)
        self.assertEqual(self.member.fixture.retained_snapshot(), before)

    def test_publication_original_intent_membership_and_growth_are_bounded(self):
        from dataclasses import replace
        from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
        from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
        from control_plane_kit_operations.postgres.configuration_evidence import _Capacity, _Unavailable
        from control_plane_kit_operations.postgres.effect_attempt_intent_store import EffectAttemptIntentStore

        class ProbeComplete(Exception):
            pass

        before = self.member.fixture.retained_snapshot()
        identity = self.member.original.identity
        trace, checked = _trace(), []
        original = ConfigurationAcceptanceStore._preflight

        def preflight(store, prepared):
            original(store, prepared)
            bound, = (entry for role, entry in prepared.read_bounds.collections
                if role == "intents" and entry.identity == (identity.run_id.value,))
            self.assertIn((identity.run_id.value, identity.activity_id, str(identity.attempt)), bound.keys)
            getter = EffectAttemptIntentStore(store._connection)
            for foreign in (replace(identity, activity_id="not-a-captured-activity"),
                    replace(identity, attempt=identity.attempt + 1)):
                with self.subTest(foreign_member=foreign.activity_id == identity.activity_id):
                    with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError(
                            "foreign original intent reached SQL")), self.assertRaises(_Unavailable):
                        getter.get(foreign)
            width = bound.widths[9]
            grown = width + 65536
            self.assertLess(grown, 1048576)
            store._connection.execute("UPDATE cpk_effect_attempt_intents SET preimage=preimage || "
                "convert_to(repeat(' ',%s-octet_length(preimage)),'UTF8') "
                "WHERE run_id=%s AND activity_id=%s AND attempt=%s",
                (grown, identity.run_id.value, identity.activity_id, identity.attempt))
            trace["accounting"] = _ACCOUNTING.get()
            trace["wire"]["largest_cell"] = 0
            with self.assertRaises(_Capacity):
                getter.get(identity)
            self.assertLess(trace["wire"]["largest_cell"], grown,
                "intent beyond frozen width crossed the transport boundary")
            checked.append(True)
            raise ProbeComplete

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight), \
                self.assertRaises(ProbeComplete):
            self.member.advance(lambda: PostgresUnitOfWork(lambda: _BudgetObservedConnection(
                psycopg.connect(self.member.fixture.database_url), trace)))
        self.assertEqual(checked, [True])
        self.assertEqual(self.member.fixture.retained_snapshot(), before)

    def test_publication_original_intent_fetch_failure_retains_reserved_peak(self):
        from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
        from tests.test_postgres_configuration_publication_bounds import _difference

        before = self.member.fixture.retained_snapshot()
        failed, after_failure = [], []
        with _publication_budget_observation(self, self.member.fixture.database_url) as (trace, _):
            class FetchFailure(_BudgetObservedConnection):
                def execute(self, sql, *args, **kwargs):
                    if failed:
                        after_failure.append(str(sql))
                    cursor = super().execute(sql, *args, **kwargs)
                    if (not failed and "bound" in trace["phases"]
                            and _ACCOUNTING.get() is trace.get("accounting")
                            and "FROM cpk_effect_attempt_intents" in str(sql)
                            and "activity_id=%s AND attempt=%s" in str(sql)
                            and "CASE WHEN" in str(sql)):
                        failed.append(_ACCOUNTING.get().used)
                        raise psycopg.DataError("injected publication original-intent fetch failure")
                    return cursor

            with self.assertRaises((psycopg.DataError, CurrentGraphAdvancementConflict)):
                self.member.advance(lambda: PostgresUnitOfWork(lambda: FetchFailure(
                    psycopg.connect(self.member.fixture.database_url), trace)))
        self.assertEqual(len(failed), 1)
        self.assertEqual(after_failure, ["SELECT txid_current()"])
        self.assertEqual(tuple(trace["phases"]), ("pre_id", "bound"))
        for current in trace["phases"].values():
            for peak in trace["peaks"][current["peak_offset"]:]:
                _within(self, _difference(peak, current["prior"]), current["publication"].peak)
            _within(self, _difference(trace["accounting"].used, current["prior"]), current["publication"].peak)
        retained = _difference(trace["accounting"].used, failed[0])
        self.assertEqual((retained.records, retained.scalar_markers, retained.statements), (1, 1, 1))
        self.assertGreater(retained.value_octets, 0)
        self.assertEqual(self.member.fixture.retained_snapshot(), before)

    def test_commit_failure_rolls_back_source_bundle_and_pointer_together(self):
        before, tentative = self.member.fixture.retained_snapshot(), []
        class CommitFailure:
            def __init__(self, connection):
                self.connection = connection
            def __getattr__(self, name):
                return getattr(self.connection, name)
            def commit(self):
                tentative.append(self.connection.execute("SELECT installation_source_count "
                    "FROM cpk_configuration_acceptances WHERE workspace_id='workspace-a' AND pinned_revision=2").fetchall())
                raise RuntimeError("injected source acceptance commit failure")
        with self.assertRaisesRegex(RuntimeError, "^injected source acceptance commit failure$"):
            self.member.advance(lambda: PostgresUnitOfWork(lambda: CommitFailure(
                psycopg.connect(self.member.fixture.database_url))))
        self.assertEqual(tentative, [[(1,)]])
        self.assertEqual(self.member.fixture.retained_snapshot(), before)
        self.assertEqual(self.member.protective_claims(), self.member.claims)

    def test_unrelated_carry_keeps_original_source_and_refuses_corrupted_origin(self):
        from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
        from control_plane_kit_core.planning import StartRuntime, RuntimeTarget
        from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, compile_topology
        from tests.test_postgres_configuration_carry import PostgresConfigurationCarryTests
        original = self.member.advance()
        connection = self.member.connection
        original_bytes = connection.execute("SELECT installation_sources_preimage FROM cpk_configuration_acceptances "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (original.desired_graph_revision,)).fetchone()[0]
        carry = PostgresConfigurationCarryTests()
        carry.base, carry.fixture, carry.graph_version = self.member.fixture, self.member, 3
        with self.member.fixture.unit_of_work() as uow:
            graph = DEFAULT_GRAPH_CODEC.decode(uow.stores.graphs.get("graph-configured").graph_descriptor)
        extra = compile_topology(DeploymentTopology("extra", DockerRuntime(runtime_id="runtime-b")))
        command = carry.prepare("source-carry", "graph-source-carry", StartRuntime(RuntimeTarget("runtime-b")),
            graph=graph.add_runtime(extra.runtimes["runtime-b"]), expected_claims=self.member.protective_claims())
        with _publication_budget_observation(self, self.member.fixture.database_url) as (trace, factory):
            identities = iter(("event-advance-" + command.run_id, "action-advance-" + command.run_id))
            result = CurrentGraphAdvancementCommandService(factory,
                clock=lambda: "2026-07-22T13:05:00Z", id_factory=identities.__next__).execute(command)
        _assert_publication_and_cold_fit(self, trace, self.member.fixture.database_url)
        self.assert_full_receipt_fits(result, trace)
        self.assertNotEqual(result.desired_graph_revision, original.desired_graph_revision)
        self.assertEqual(connection.execute("SELECT installation_sources_preimage FROM cpk_configuration_acceptances "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (result.desired_graph_revision,)).fetchone(),
            (original_bytes,))
        self.assertTrue(carry.advance(command).replayed)
        connection.execute("UPDATE cpk_configuration_acceptances SET installation_sources_preimage='{}' "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (original.desired_graph_revision,))
        before = self.member.fixture.retained_snapshot()
        with self.assertRaises(CurrentGraphAdvancementConflict):
            self.replay_service().execute(command)
        self.assertEqual(self.member.fixture.retained_snapshot(), before)


    def test_same_material_replacement_carries_exact_x2_without_fallback_to_x1(self):
        from dataclasses import replace
        from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
        from control_plane_kit_core.planning import NodeTarget, ReconcileNode, RemoveRuntimeResource, RuntimeTarget, StartRuntime
        from control_plane_kit_core.policies import PolicyScope
        from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, compile_topology
        from control_plane_kit_operations.coordinator import CoordinatorStatus
        from control_plane_kit_operations._accepted_installation_sources import _InstallationSources
        from tests.test_postgres_configuration_carry import PostgresConfigurationCarryTests
        from tests.test_execution_coordinator import RecordingAdapter

        first = self.member.advance()
        connection = self.member.connection
        carry = PostgresConfigurationCarryTests()
        carry.base, carry.fixture, carry.graph_version = self.member.fixture, self.member, 3
        with self.member.fixture.unit_of_work() as uow:
            graph = DEFAULT_GRAPH_CODEC.decode(uow.stores.graphs.get("graph-configured").graph_descriptor)
        command = carry.admit("source-x2", "graph-source-x2", ReconcileNode(NodeTarget("api")), graph=graph)
        engine = self.member.fixture.engine
        requests = []

        def replaced(_context, request):
            result = self.capacity.simulated_installation(request)
            evidence = dict(result.evidence)
            for name in ("secret_installation_receipt", "configuration_installation_receipt"):
                evidence[name] = {**evidence[name], "disposition": "replaced",
                    "prior_realization_fingerprint": "b" * 64, "current_realization_fingerprint": "c" * 64}
            requests.append(request)
            return replace(result, evidence=evidence)

        adapter = RecordingAdapter(engine.tracker, replaced)
        completed = engine.coordinator(adapter).execute(replace(engine.command(
            generation=command.fence.generation, idempotency_key="execute-source-x2",
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE)), run_id=command.run_id))
        self.assertIs(completed.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(adapter.calls, ["activity-source-x2"])
        self.assertEqual(adapter.active_during_calls, [0])
        self.assertEqual(requests[0].products, self.capacity.installation_requests[0].products)

        def advance_observed(selected):
            identities = iter(("event-advance-" + selected.run_id, "action-advance-" + selected.run_id))
            with _publication_budget_observation(self, self.member.fixture.database_url) as (trace, factory):
                result = CurrentGraphAdvancementCommandService(factory,
                    clock=lambda: "2026-07-22T13:05:00Z", id_factory=identities.__next__).execute(selected)
            _assert_publication_and_cold_fit(self, trace, self.member.fixture.database_url)
            self.assert_full_receipt_fits(result, trace)
            return result

        second = advance_observed(command)
        def bundle(result):
            return connection.execute("SELECT installation_sources_preimage FROM cpk_configuration_acceptances "
                "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (result.desired_graph_revision,)).fetchone()[0]
        first_bytes, second_bytes = bundle(first), bundle(second)
        first_binding, = json.loads(first_bytes)["bindings"]
        second_binding, = json.loads(second_bytes)["bindings"]
        self.assertNotEqual(first_binding["source"], second_binding["source"])
        self.assertEqual(second_binding["source"], {"run_id": command.run_id,
            "activity_id": "activity-source-x2", "attempt": 1})
        self.assertEqual(second_binding["accepted_revision"], second.desired_graph_revision)
        self.assertNotEqual(first_binding["original_request_fingerprint"], second_binding["original_request_fingerprint"])
        self.assertNotEqual(first_binding["outcome_fingerprint"], second_binding["outcome_fingerprint"])
        extra = compile_topology(DeploymentTopology("extra", DockerRuntime(runtime_id="runtime-b")))
        middle_command = carry.prepare("x2-middle", "graph-x2-middle", StartRuntime(RuntimeTarget("runtime-b")),
            graph=graph.add_runtime(extra.runtimes["runtime-b"]), expected_claims=self.member.protective_claims())
        middle = advance_observed(middle_command)
        final_command = carry.prepare("x2-final", "graph-source-x2", RemoveRuntimeResource(RuntimeTarget("runtime-b")),
            expected_claims=self.member.protective_claims())
        final = advance_observed(final_command)
        self.assertEqual((bundle(middle), bundle(final)), (second_bytes, second_bytes))
        # #2007 exposes only ingress connectors. A genuine accepted API X2
        # replacement remains valid shared-source proof, never a connector.
        with self.member.fixture.unit_of_work() as uow:
            current = uow.stores.workspaces.get("workspace-a")
            reader = getattr(uow.stores.configuration_acceptance, "read_current_connector_installation", None)
            self.assertTrue(callable(reader), "current connector installation read is missing")
            observed = reader("workspace-a", expected_current_graph_id=current.current_graph_id,
                expected_current_realized_projection_id=current.current_realized_projection_id,
                runtime_id=graph.node("api").runtime_id, node_id="api")
            self.assertEqual(observed.state, "unavailable")
            self.assertIsNone(observed.installation)
        connection.execute("UPDATE cpk_configuration_acceptances SET installation_sources_preimage='{}' "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (middle.desired_graph_revision,))
        self.assertTrue(self.replay_service().execute(final_command).replayed)
        connection.execute("UPDATE cpk_configuration_acceptances SET installation_sources_preimage=%s "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (second_bytes, middle.desired_graph_revision))

        # Remove X2's binding coherently, keeping X1 and the X2 root commitment
        # valid. This is test-owned evidence loss, never a production repair.
        empty = _InstallationSources(())
        event_payload = connection.execute("SELECT payload FROM cpk_activity_events WHERE event_id=%s",
            (second.event.event_id,)).fetchone()[0]
        action_payload = connection.execute("SELECT payload FROM cpk_operation_actions WHERE action_id=%s",
            (second.action.action_id,)).fetchone()[0]
        for absent in (True, False):
            with self.subTest(original_binding_absent=absent):
                with connection.transaction():
                    connection.execute("UPDATE cpk_configuration_acceptances SET installation_sources_preimage=%s,"
                        "installation_source_count=%s,installation_sources_digest=%s "
                        "WHERE workspace_id='workspace-a' AND pinned_revision=%s",
                        (empty.canonical_bytes().decode() if absent else '{}', 0 if absent else 1,
                            empty.digest if absent else second.event.evidence.descriptor()["installation_sources_digest"],
                            second.desired_graph_revision))
                    connection.execute("UPDATE cpk_activity_events SET payload=%s WHERE event_id=%s",
                        (Jsonb({**event_payload, "evidence": {**event_payload["evidence"], **empty.commitment}}
                            if absent else event_payload), second.event.event_id))
                    connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE action_id=%s",
                        (Jsonb({**action_payload, **empty.commitment} if absent else action_payload), second.action.action_id))
                before = self.member.fixture.retained_snapshot()
                with self.assertRaises(CurrentGraphAdvancementConflict):
                    self.replay_service().execute(final_command)
                self.assertEqual(self.member.fixture.retained_snapshot(), before)
                self.assertEqual(bundle(first), first_bytes)


class _TwoIngressRuntime(ProfiledRecordingRuntime):
    def execute_with_authority(self, request, authority, *, invocations=None):
        if type(request.operation) is StartNode:
            ingresses = tuple(value for value in self.test.graph.public_ingresses
                if value.connector_node_id == request.operation.target.node_id)
            if ingresses:
                ingress, = ingresses
                selection, = (value for material in request.products for value in material.secret_selections
                    if value.intent is SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN)
                with self.test.unit_of_work() as uow:
                    generated, = (value for value in uow.stores.generated_ingress_secrets.list_for_workspace(
                        request.source.workspace_id) if value.secret_ref == selection.reference)
                    event = uow.stores.execution.get_event(generated.source_event_id)
                self.test.assertEqual(selection.version_id, generated.provider_version_id)
                self.test.assertEqual(generated.source_run_id, request.source.run_id.value)
                self.test.assertIs(event.kind, ActivityEventKind.STEP_STARTED)
                allocation = self.test.plan.activity(ActivityId(generated.source_activity_id)).operation
                self.test.assertIs(type(allocation), AllocatePublicIngress)
                self.test.assertEqual(allocation.target.ingress_id, ingress.ingress_id)
                self._require_connector_grant(request, selection.reference.reference_id)
        # Preserve both the profiled consumer/receipt checks and base port behavior.
        if invocations is None:
            return super().execute_with_authority(request, authority)
        grant, = request.secret_resolution_grants
        permit = invocations.admit("secret-resolution", authorization_id=grant.authorization_id)
        invocations.record_outcome(permit, "returned")
        final = invocations.admit("prepared-runtime-dispatch")
        result = super().execute_with_authority(request, authority)
        invocations.record_outcome(final, "returned")
        return result


class _TwoIngressProvider(RecordingIngressInterpreter):
    def create(self, ingress, **kwargs):
        allocated = super().create(ingress, **kwargs)
        name = kwargs["allocation_name"]
        return replace(allocated, tunnel_id="tunnel-" + name, dns_record_id="dns-" + name,
            endpoint_url="https://" + ingress.hostname)


class PostgresTwoIngressAcceptanceTests(ManagedApplicationFixture):
    """#2076 actual owner composition; only external effects are recorded."""

    def setUp(self):
        super().setUp()
        self.runtime = _TwoIngressRuntime(self)
        self.ingress = _TwoIngressProvider(self.tracker)
        self.health.responses = [NativeConnectionOutcome.CONNECTED]

    def native_before_path_graph(self, graph):
        # This positive case needs no overlap-specific sibling ordering.
        return replace(graph, public_ingresses=(replace(graph.public_ingresses[0],
            hostname="cpk-gateway-management.openj92.dev"),))

    def authored_graph(self):
        graph, documents, registry = super().authored_graph()
        api = graph.node("api")
        declaration = WorkloadNodeControlSurfaceDeclaration(api.block_spec.control_surfaces[0],
            WorkloadNodeControlSurfaceDeclarationProfile.V2)
        nodes = dict(graph.nodes)
        for name, receiver_id in (("connector", "c" * 32), ("application-connector", "d" * 32)):
            control = artifact("workload", declaration, node=name, receiver=True,
                receiver_id=receiver_id, revision=self.authored_revision)
            environment = wrapper_environment((control,))
            product = ContainerServerProduct(ProductIdentity("test", "chain-" + name, 1),
                OciImageReference("ghcr.io", "test/chain-" + name, "sha256:" + "a" * 64),
                ProductRuntimeContract(sockets=api.sockets, provider_ports=(ProviderRuntimePort("http", 8000),),
                    capabilities=api.block_spec.capabilities, control_surfaces=api.block_spec.control_surfaces,
                    configuration_artifacts=(control,), public_environment=environment))
            document = ProductDescriptorCodec().encode_document(product)
            documents[name] = document
            reference = ProductReference.from_document(document)
            endpoint = api.endpoints["http"]
            nodes[name] = replace(api, node_id=name, configuration_artifacts=(control,),
                block_spec=replace(api.block_spec, role_id=name),
                public_environment=environment,
                endpoints={"http": Endpoint(LiteralAddress(f"http://{name}:8000"), endpoint.protocol)},
                metadata={"product_identity": reference.identity.key,
                    "product_descriptor_digest": reference.descriptor_sha256.value})
        gateway = nodes["gateway"]
        routes = ReceiverManagementRoutesCodec().encode_bytes(management_routes(nodes)).decode("utf-8")
        nodes["gateway"] = replace(gateway, configuration_artifacts=tuple(
            replace(value, content=routes) if value.artifact_id == "gateway-health-targets" else value
            for value in gateway.configuration_artifacts))
        management, = graph.public_ingresses
        application = replace(management, ingress_id="application", target=PublicIngressTarget("api", "http"),
            connector_node_id="application-connector", hostname="cpk-gateway-application.openj92.dev")
        runtime = graph.runtimes["docker"]
        graph = replace(graph, nodes=nodes, public_ingresses=(management, application),
            runtimes={"docker": replace(runtime, children=(*runtime.children, "application-connector"))})
        graph = DEFAULT_GRAPH_CODEC.decode(DEFAULT_GRAPH_CODEC.encode(graph))
        validate_graph(graph).require_valid()
        return graph, documents, registry

    def assert_initial_dependencies(self):
        starts = {item.operation.target.node_id: item for item in self.plan.activities
            if type(item.operation) is StartNode}
        allocations = {item.operation.target.ingress_id: item for item in self.plan.activities
            if type(item.operation) is AllocatePublicIngress}
        bootstrap = {item.operation.stage: item for item in self.plan.activities
            if type(item.operation) is ObserveManagementBootstrap}
        health = {item.operation.node_id: item for item in self.plan.activities
            if type(item.operation) is ObserveNodeHealth}
        self.assertEqual(set(starts), {"gateway", "api", "connector", "application-connector"})
        self.assertEqual(set(allocations), {"management", "application"})
        self.assertEqual(set(bootstrap), {ManagementBootstrapStage.CONNECTOR_CONNECTED,
            ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH, ManagementBootstrapStage.GATEWAY_INGRESS_READY})
        self.assertEqual(set(health), {"api", "connector", "application-connector"})
        native = bootstrap[ManagementBootstrapStage.CONNECTOR_CONNECTED]
        path = bootstrap[ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH]
        ready = bootstrap[ManagementBootstrapStage.GATEWAY_INGRESS_READY]
        dependencies = [(allocations["management"], starts["gateway"]),
            (starts["connector"], allocations["management"]),
            (native, starts["connector"]), (path, starts["connector"]), (ready, path),
            (ready, starts["gateway"]), (allocations["application"], health["api"]),
            (starts["application-connector"], allocations["application"])]
        for name, observation in health.items():
            dependencies.extend(((observation, native), (observation, ready), (observation, starts[name])))
        for successor, predecessor in dependencies:
            self.assertIn(predecessor.activity_id,
                {dependency.predecessor for dependency in successor.dependencies})

    def acceptance_snapshot(self):
        return tuple(self.rows(name) for name in ("cpk_workspaces", "cpk_configuration_acceptances",
            "cpk_operation_actions", "cpk_graph_receiver_introductions", "cpk_activity_events"))

    async def execute_registered_connector(self, claim):
        import control_plane_kit_operations as ops
        from tests import test_managed_application_chain as managed
        from tests.test_postgres_coordinator_permissions import _CurrentCoordinatorPermission
        from control_plane_kit_operations.workflows import IdempotencyKey
        from control_plane_kit_core.types import RuntimeKind

        _CurrentCoordinatorPermission.configure_permissions(self, self.worker.identity, installation="coordinator-permission")
        factory = self.unit_of_work
        fold = managed.EffectAttemptFoldService(factory, id_factory=self.ids["effect-fold"])
        registration = RuntimeAdapterRegistration(self.runtime, exact_version_execute_with_authority=True,
            invocation_profile="cpk.semantic-invocations.v1")
        owner = managed.ExecutionCoordinator(factory,
            lifecycle=managed.RunLifecycleCommandService(factory, clock=managed.now, id_factory=self.ids["lifecycle"]),
            adapter=managed.ActivityExecutionDispatcher(runtime=managed.RuntimeInterpreterDispatcher(
                {RuntimeKind.DOCKER: registration}, managed.SecretUseAuthorizationService(factory))),
            start_service=managed.EffectAttemptStartService(factory, id_factory=self.ids["effect-start"],
                permission_policy=self.policy, health_receiver_decoders=self.registry), fold_service=fold,
            reconciliation_service=managed.EffectAttemptReconciliationService(factory, managed.ForbiddenRecovery(), fold),
            clock=managed.now, id_factory=self.ids["execution"], permission_policy=self.policy,
            managed_health=self.health, health_signing_authority=managed.HealthSigningAuthorityReloadService(
                factory, health_receiver_decoders=self.registry),
            invocation_service=ops.ExecutionInvocationService(factory, permission_policy=self.policy, clock=managed.now))
        execution = ops.ExecuteActivityRun(self.run_id, ops.ExecutionWorkerAuthority(claim.worker_id,
            self.original.granted_scopes), claim.fence, IdempotencyKey("registered-connector-start"))
        return (await owner.execute_managed(ops.ExecuteManagedActivityRun(execution, self.original))).descriptor()

    async def prepare_worker_connector_read(self, *, registered=True):
        from control_plane_kit_operations.execution_leases import ExecutionLeaseFence

        await self.prepare_and_start()
        runtime = self.graph.runtimes["docker"]
        with self.unit_of_work() as uow:
            claim = uow.stores.execution.get_request(self.request_id).claim
        scope = dict(workspace_id="workspace-a", request_id=self.request_id, run_id=self.run_id,
            plan_id=self.plan_id, activity_id=self.native_id, node_id="connector", authority_ref=runtime.authority_ref,
            fence=ExecutionLeaseFence(claim.worker_id, claim.generation))
        def read(**overrides):
            unit = PostgresUnitOfWork(lambda: psycopg.connect(self.tracker.database_url))
            return unit.read_run_connector_installation(**(scope | overrides))

        self.assertIsNone(read())
        start, = (step for step in self.plan.activities if type(step.operation) is StartNode
            and step.operation.target.node_id == "connector")
        from control_plane_kit_core.planning import derive_schedule
        for _ in self.plan.activities:
            journal = project_activity_journal(self.plan, activity_journal_events(self.history()))
            ready = derive_schedule(self.plan, journal.state).ready
            step = (await self.execute_registered_connector(claim) if registered and ready and ready[0].activity_id == start.activity_id
                else await self.execute_one())
            self.assertEqual(step["run_status"], "running")
            if any(e.activity_id == start.activity_id.value and e.kind is ActivityEventKind.STEP_SUCCEEDED
                    for e in self.history()):
                break
        else:
            self.fail("connector start did not succeed")
        return read, runtime, start, claim

    def configure_retained_bootstrap(self):
        """Keep the ordinary API running and add two managed connector roles."""
        from control_plane_kit_core.public_ingress import IngressAuthorityReference

        api = self.graph.node("api")
        original = self.documents["api"].product
        contract = replace(original.runtime_contract, configuration_artifacts=(), public_environment=(),
            capabilities=(), control_surfaces=())
        ordinary = ProductDescriptorCodec().encode_document(replace(original,
            identity=replace(original.identity, contract_revision=2), runtime_contract=contract))
        self.documents["api"] = ordinary
        # Both connector roles use the same registered image with distinct receiver configurations.
        self.documents.pop("application-connector")
        nodes = dict(self.graph.nodes)
        nodes["api"] = replace(api, configuration_artifacts=(), public_environment=(),
            block_spec=replace(api.block_spec, capabilities=(), control_surfaces=()))
        for key, node in tuple(nodes.items()):
            doc = self.documents["connector" if key == "application-connector" else key]
            ref = ProductReference.from_document(doc)
            nodes[key] = replace(node, metadata={**node.metadata, "product_identity": ref.identity.key,
                "product_descriptor_digest": ref.descriptor_sha256.value, "oci_image": doc.product.image.execution_reference})
        gateway = nodes["gateway"]
        routes = ReceiverManagementRoutesCodec().encode_bytes(management_routes(nodes)).decode()
        nodes["gateway"] = replace(gateway, configuration_artifacts=tuple(
            replace(item, content=routes, source_digest=None) if item.artifact_id == "gateway-health-targets" else item
            for item in gateway.configuration_artifacts))
        management, public = self.graph.public_ingresses
        self.graph = replace(self.graph, nodes=nodes, public_ingresses=(management,
            replace(public, authority_ref=IngressAuthorityReference("application-authority"))))
        self.author_initial_desired = self.accept_retained_workload

    async def accept_retained_workload(self):
        from control_plane_kit_core.policies import PolicyScope
        from control_plane_kit_core.planning import compile_graph_activity_plan
        from control_plane_kit_core.topology import DeploymentGraph
        from control_plane_kit_operations.records import GraphVersionRecord
        from tests.accepted_graph_origin_fixture import accept_selected_fixture_origin
        from tests.test_managed_application_chain import now

        runtime = replace(self.graph.runtimes["docker"], children=("api",), management=None)
        base = replace(self.graph, public_ingresses=(), runtimes={"docker": runtime},
            nodes={"api": self.graph.node("api")})
        with self.unit_of_work() as uow:
            stores = uow.stores
            graph = GraphVersionRecord.from_graph(graph_id="retained-app", workspace_id="workspace-a",
                version=stores.graphs.next_version_for_workspace("workspace-a"), graph=base, created_by="operator-a", created_at=now())
            stores.graphs.save(graph)
            stores.workspaces.set_desired_graph("workspace-a", graph.graph_id)
            management, public = self.graph.public_ingresses
            registration = stores.ingress_authorities.get("workspace-a", management.authority_ref)
            stores.ingress_authorities.register(workspace_id="workspace-a", authority_ref=public.authority_ref,
                authority=registration.authority, admitted_by="operator-a", admitted_at=now())
            uow.commit()
        plan = compile_graph_activity_plan(validate_graph(DeploymentGraph("empty")), validate_graph(base))
        self.database_url = self.tracker.database_url
        accept_selected_fixture_origin(self, plan, prefix="accepted-retained", timestamp=now(),
            actor_scopes=(PolicyScope.PLAN_EXECUTE, PolicyScope.RUNTIME_AUTHORITY_USE))
        with self.unit_of_work() as uow:
            ws = uow.stores.workspaces.get("workspace-a")
        self.assertEqual(ws.current_graph_id, graph.graph_id)
        self.workspace.update(current_graph_id=ws.current_graph_id,
            current_realized_projection_id=ws.current_realized_projection_id,
            desired_graph_revision=ws.desired_graph_revision)
        return {"authored_graph_id": ws.desired_graph_id,
            "realized_projection_id": ws.desired_realized_projection_id}

    def configure_retained_reader(self):
        from tests.test_managed_application_chain import RecordingManagedHealth

        self.configure_retained_bootstrap()
        captured = []

        class CaptureHealth(RecordingManagedHealth):
            def select(inner, *, plan, current, desired, registered_products, runtime_authorities):
                self.assertEqual(set(current.nodes), {"api"})
                self.assertEqual(desired, self.graph)
                selected = tuple(item.activity_id for item in plan.activities
                    if type(item.operation) in (ObserveManagementBootstrap, ObserveNodeHealth))
                inner.selections.append(selected)
                return selected

            async def observe_connection(inner, realization, request, authority):
                captured.append(realization.run_connector_installation)
                return await super().observe_connection(realization, request, authority)

        self.health = CaptureHealth(self)
        self.health.responses = [NativeConnectionOutcome.CONNECTED]
        return captured

    async def execute_through_native_attempt(self):
        for _ in self.plan.activities:
            await self.execute_one()
            if any(event.activity_id == self.native_id for event in self.history()
                    if event.kind is not ActivityEventKind.STEP_STARTED):
                return self.native_attempt(1)
        self.fail("the native attempt never produced an outcome")

    async def test_retained_bootstrap_delivers_original_connector_receipt_to_native_reader(self):
        from control_plane_kit_operations.plan_derivation import PlanDerivationProfile

        captured = self.configure_retained_reader()
        read, *_ = await self.prepare_worker_connector_read()
        proof = read()
        self.assertIsNotNone(proof)
        self.assertIsNone(read(node_id="application-connector"))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.activity_history.get_plan(self.plan_id).derivation_profile,
                PlanDerivationProfile.EXISTING_RUNTIME_BOOTSTRAP_V1)
        _, retained = await self.execute_through_native_attempt()
        self.assertEqual(captured, [proof])
        self.assertEqual(len(self.health.native_reads), 1)
        self.assertIs(retained.outcome.observation.outcome, NativeConnectionOutcome.CONNECTED)

    async def test_retained_bootstrap_missing_dispatch_receipt_refuses_native_read(self):
        captured = self.configure_retained_reader()
        read, *_ = await self.prepare_worker_connector_read(registered=False)
        self.assertIsNone(read())
        _, retained = await self.execute_through_native_attempt()
        self.assertEqual(captured, [])
        self.assertEqual(self.health.native_reads, [])
        self.assertEqual(retained.outcome.result.failure.code, "native-read-result-unknown")

    async def test_retained_bootstrap_foreign_allocation_refuses_native_read(self):
        captured = self.configure_retained_reader()
        read, *_ = await self.prepare_worker_connector_read()
        proof = read()
        self.assertIsNotNone(proof)
        with self.connection.transaction():
            changed = self.connection.execute(
                "UPDATE cpk_generated_ingress_secret_references SET source_run_id=%s "
                "WHERE source_run_id=%s AND source_event_id=%s",
                ("unrelated-run", self.run_id, proof.allocation_start_event_id))
            self.assertEqual(changed.rowcount, 1)
        self.assertIsNone(read())
        _, retained = await self.execute_through_native_attempt()
        self.assertEqual(captured, [])
        self.assertEqual(self.health.native_reads, [])
        self.assertEqual(retained.outcome.result.failure.code, "native-read-result-unknown")

    async def test_retained_bootstrap_substituted_signed_receiver_refuses_before_dispatch(self):
        from unittest.mock import patch
        from control_plane_kit_operations import coordinator
        from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartConflict
        from control_plane_kit_operations.managed_effect_plan import (
            ManagedEffectPlanConflict, require_managed_effect_plan)
        from control_plane_kit_operations.runtime_management_targets import is_signed_management_health_operation

        self.configure_retained_reader()
        read, *_ = await self.prepare_worker_connector_read()
        self.assertIsNotNone(read())
        alternate, = (item for item in self.plan.activities
            if type(item.operation) is ObserveNodeHealth and item.operation.node_id == "application-connector")
        project = coordinator._runtime_effect_intent_for_context
        substitutions = []
        signed_requests = []

        def substitute(context, activity):
            original = project(context, activity)
            if not is_signed_management_health_operation(original.operation):
                return original
            other = project(context, alternate)
            self.assertNotEqual(activity.activity_id, alternate.activity_id)
            forged = replace(other, activity_id=activity.activity_id)
            with self.unit_of_work() as uow:
                request = uow.stores.execution.get_request(self.request_id)
                self.assertEqual(require_managed_effect_plan(uow.stores, original, request),
                    require_managed_effect_plan(uow.stores, other, request))
                with self.assertRaises(ManagedEffectPlanConflict):
                    require_managed_effect_plan(uow.stores, forged, request)
            substitutions.append(activity.activity_id)
            return forged

        async def record_signed(*args, **kwargs):
            signed_requests.append(args[1])
            self.fail("a substituted receiver reached the signed reader")

        # Inject only the alternate intent. The worker, plan reader and start service stay real.
        with patch.object(coordinator, "_runtime_effect_intent_for_context", substitute), \
                patch.object(self.health, "observe_signed", record_signed):
            with self.assertRaises(EffectAttemptStartConflict):
                for _ in self.plan.activities:
                    await self.execute_one()
        self.assertTrue(substitutions)
        self.assertEqual(signed_requests, [])
        self.assertFalse(any(event.activity_id == substitutions[-1].value for event in self.history()))

    async def test_worker_reads_new_connector_from_its_active_run(self):
        from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
        read, runtime, start, claim = await self.prepare_worker_connector_read()
        before = self.effects()
        proof = read()
        self.assertIsNotNone(proof)
        self.assertEqual((proof.source.run_id.value, proof.source.plan_id, proof.source.request_id,
            proof.start_activity_id, proof.wrapper.node_id, proof.ingress_id, proof.authority_ref),
            (self.run_id, self.plan_id, self.request_id, start.activity_id.value, "connector",
             "management", runtime.authority_ref))
        self.assertNotEqual(proof.allocation_start_event_id, proof.allocation_success_event_id)
        self.assertRegex(proof.receipt.current_realization_fingerprint, "^[0-9a-f]{64}$")
        self.assertEqual(proof.receipt.disposition.value, "created")
        self.assertNotIn(proof.token.version_id, repr(proof))
        for changes in (dict(run_id="other-run"), dict(node_id="application-connector"),
                dict(activity_id=start.activity_id.value), dict(plan_id="other-plan"),
                dict(authority_ref=None), dict(request_id="other-request"),
                dict(fence=ExecutionLeaseFence(claim.worker_id, claim.generation+1))):
            with self.subTest(fields=tuple(changes)):
                self.assertIsNone(read(**changes))
        # Replace the origin of the real committed token receipt in the test database.
        # The run, its start, its successful outcome and the active allocation still match.
        with self.connection.transaction():
            changed = self.connection.execute("UPDATE cpk_generated_ingress_secret_references SET source_run_id=%s "
                "WHERE source_run_id=%s AND source_event_id=%s",
                ("unrelated-run", self.run_id, proof.allocation_start_event_id))
            self.assertEqual(changed.rowcount, 1)
        self.assertIsNone(read())
        with self.connection.transaction():
            self.connection.execute("UPDATE cpk_generated_ingress_secret_references SET source_run_id=%s "
                "WHERE source_run_id=%s AND source_event_id=%s", (self.run_id, "unrelated-run", proof.allocation_start_event_id))
        self.assertIsNotNone(read())
        # A real replacement registration reuses the logical Docker name.
        from control_plane_kit_operations.runtime_authorities import RemoteDockerTlsAuthority
        from control_plane_kit_core.secrets import SecretReference
        from tests.test_managed_application_chain import now
        with self.unit_of_work() as uow:
            registered = uow.stores.runtime_authorities
            old = registered.revoke("workspace-a", runtime.authority_ref)
            new = registered.register(workspace_id="workspace-a", authority_ref=runtime.authority_ref,
                runtime_kind=runtime.kind, authority=RemoteDockerTlsAuthority("tcp://replacement.example.test:2376",
                    *(SecretReference("secret://runtime/" + key) for key in ("ca", "cert", "key"))),
                admitted_by="operator-a", admitted_at=now())
            self.assertNotEqual(old.registration_id, new.registration_id)
            uow.commit()
        self.assertIsNone(read(), "a re-registered Docker name cannot relabel a previous start")
        self.assertEqual(self.effects(), before)
        self.assertEqual(self.health.native_reads, [])

    def test_absent_original_dispatch_charges_the_shared_read_budget(self):
        from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
        from control_plane_kit_operations.execution_invocations import ExecutionInvocationKind
        from control_plane_kit_operations.postgres.configuration_evidence import _joined_read
        from control_plane_kit_operations.postgres.execution import PostgresExecutionStore

        store = PostgresExecutionStore(self.connection)
        with _joined_read(self.connection) as read:
            before = read.used
            self.assertIsNone(store.original_final_invocation(EffectAttemptIdentity(RunId("missing"), "start", 1),
                original_event_id="missing-start", request_fingerprint="a" * 64,
                kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH))
            self.assertGreater(read.used.statements, before.statements)
            self.assertGreater(read.used.accounted_bytes, before.accounted_bytes)

    async def test_worker_read_refuses_connector_without_original_dispatch_registration(self):
        read, *_ = await self.prepare_worker_connector_read(registered=False)
        self.assertIsNone(read())

    async def test_active_connector_proof_is_only_carried_by_the_native_observation(self):
        from tests.test_managed_application_chain import RecordingManagedHealth
        from control_plane_kit_operations.plan_derivation import PlanDerivationProfile
        from control_plane_kit_operations.workflows import InvalidOperationCommand

        contexts = {}
        class CaptureHealth(RecordingManagedHealth):
            async def observe_connection(self, realization, request, authority):
                contexts["native"] = realization
                return await super().observe_connection(realization, request, authority)

            async def observe_signed(self, realization, request, authority, **kwargs):
                contexts["signed"] = realization
                return await super().observe_signed(realization, request, authority, **kwargs)

        self.health = CaptureHealth(self)
        self.health.responses = [NativeConnectionOutcome.CONNECTED]
        read, *_ = await self.prepare_worker_connector_read()
        proof = read()
        self.assertIsNotNone(proof)
        for _ in self.plan.activities:
            await self.execute_one()
            if set(contexts) == {"native", "signed"}:
                break
        self.assertEqual(set(contexts), {"native", "signed"})
        # Exercise the receiving value independently of the execution hold.
        # Only the in-memory profile is varied; neither value is dispatched.
        def attach(context):
            record = replace(context.plan_record, derivation_profile=PlanDerivationProfile.EXISTING_RUNTIME_BOOTSTRAP_V1)
            return replace(context, plan_record=record, run_connector_installation=proof)
        self.assertIs(attach(contexts["native"]).run_connector_installation, proof)
        with self.assertRaises(InvalidOperationCommand):
            attach(contexts["signed"])

    async def test_two_managed_ingress_connectors_advance_with_original_sources(self):
        await self.prepare_and_start()
        self.assert_initial_dependencies()
        for _ in range(len(self.plan.activities) + 1):
            result = await self.execute_one()
            if result["coordinator_status"] == "completed":
                break
            self.assertEqual(result["coordinator_status"], "progressed")
        else:
            self.fail("bounded initial execution did not complete")
        self.assertEqual(result["run_status"], "succeeded")
        self.assertEqual(len(self.health.native_reads), 1)
        self.assertEqual(self.health.responses, [])
        events = self.history()
        self.assertIs(events[-1].kind, ActivityEventKind.RUN_SUCCEEDED)
        self.assertEqual(Counter(event.activity_id for event in events
            if event.kind is ActivityEventKind.STEP_SUCCEEDED),
            Counter(item.activity_id.value for item in self.plan.activities))
        plan = self.plan_descriptor
        with self.unit_of_work() as uow:
            receivers = uow.stores.graphs.receiver_bindings("workspace-a",
                plan["desired_graph_id"], plan["desired_realized_projection_id"])
            self.assertEqual(len({value.receiver_id for value in receivers}), 4)
            self.assertTrue(all(uow.stores.graphs.receiver_introduction("workspace-a", value.receiver_id)
                .first_accepted_action_id is None for value in receivers))
        claims = self.connection.execute("SELECT run_id,activity_id,attempt,artifact_id,workspace_id,"
            "allocation_id,runtime_id,node_id FROM cpk_configuration_claims ORDER BY 1,2,3,4").fetchall()
        effects, before = self.effects(), self.acceptance_snapshot()
        try:
            advanced = await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
                path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker,
                payload={"plan_id": self.plan_id, "expected_current_graph_id": self.workspace["current_graph_id"],
                    "expected_current_realized_projection_id": self.workspace["current_realized_projection_id"],
                    "desired_graph_id": plan["desired_graph_id"],
                    "desired_realized_projection_id": plan["desired_realized_projection_id"],
                    "expected_desired_graph_revision": plan["desired_graph_revision"],
                    "claim_generation": self.generation, "idempotency_key": "two-ingress-advance"})
        except Exception as error:
            allowed_classes = {"CurrentGraphAdvancementConflict", "CurrentGraphAdvancementIncomplete",
                "CurrentGraphAdvancementDenied", "CurrentGraphAdvancementNotFound",
                "CurrentGraphAdvancementIdempotencyConflict", "CurrentGraphAdvancementError",
                "_Unavailable", "_Capacity", "CheckViolation", "ForeignKeyViolation", "UniqueViolation",
                "SerializationFailure", "DeadlockDetected", "AssertionError", "TypeError", "ValueError",
                "AttributeError", "KeyError", "InvalidOperationCommand"}
            allowed_files = {"advancement.py", "configuration_acceptance_store.py", "receiver_lifecycle.py",
                "receiver_execution_scopes.py", "configuration_evidence.py", "unit_of_work.py"}
            frames = [(frame.filename.rsplit("/", 1)[-1], frame.name, frame.lineno)
                for frame in traceback.extract_tb(error.__traceback__)
                if frame.filename.rsplit("/", 1)[-1] in allowed_files
                and frame.name.isidentifier() and len(frame.name) <= 96][-8:]
            print("two-ingress-acceptance-diagnostic", json.dumps({"stage": "advance-current",
                "exception": type(error).__name__ if type(error).__name__ in allowed_classes else "other",
                "cause": (type(error.__cause__).__name__
                    if type(error.__cause__).__name__ in allowed_classes else None), "frames": frames}))
            # No second invocation: a failed owner transaction must preserve its inputs.
            self.assertTrue(self.acceptance_snapshot() == before, "failed advancement published acceptance")
            self.assertTrue(self.effects() == effects, "advancement dispatched an external effect")
            raise
        self.assertEqual(self.effects(), effects)
        self.assertEqual(self.connection.execute("SELECT run_id,activity_id,attempt,artifact_id,workspace_id,"
            "allocation_id,runtime_id,node_id FROM cpk_configuration_claims ORDER BY 1,2,3,4").fetchall(), claims)
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
            self.assertEqual((workspace.current_graph_id, workspace.current_realized_projection_id),
                (plan["desired_graph_id"], plan["desired_realized_projection_id"]))
            for receiver in receivers:
                origin = uow.stores.graphs.receiver_introduction("workspace-a", receiver.receiver_id)
                self.assertEqual((origin.first_accepted_action_id, origin.first_accepted_session_id),
                    (advanced["action_id"], plan["session_id"]))
            generated = uow.stores.generated_ingress_secrets.list_for_workspace("workspace-a")
        preimage, = self.connection.execute("SELECT installation_sources_preimage FROM cpk_configuration_acceptances "
            "WHERE workspace_id=%s AND pinned_revision=%s", ("workspace-a", plan["desired_graph_revision"])).fetchone()
        accepted = {value["node_id"]: value for value in json.loads(preimage)["bindings"]}
        self.assertEqual(set(accepted), {"connector", "application-connector"})
        installed = tuple(request for request in self.runtime.calls if type(request.operation) is StartNode
            and request.operation.target.node_id in accepted)
        originals = self.mutation_records(installed)
        allocation_starts = set()
        for request, (original, attempt, outcome) in zip(installed, originals, strict=True):
            binding = accepted[request.operation.target.node_id]
            self.assertEqual(binding["source"], {"run_id": self.run_id,
                "activity_id": request.activity_id.value, "attempt": attempt.state.identity.attempt})
            self.assertEqual(binding["original_request_fingerprint"], original.request_fingerprint)
            self.assertEqual(binding["outcome_fingerprint"], outcome.outcome.outcome_fingerprint)
            selection, = (value for material in request.products for value in material.secret_selections
                if value.intent is SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN)
            allocation, = (value for value in generated if value.secret_ref == selection.reference)
            provenance, = binding["generated_allocations"]
            self.assertEqual(provenance["source_event_id"], allocation.source_event_id)
            start, = (event for event in events if event.event_id == provenance["source_event_id"])
            terminal, = (event for event in events if event.event_id == provenance["terminal_event_id"])
            self.assertIs(start.kind, ActivityEventKind.STEP_STARTED)
            self.assertIs(terminal.kind, ActivityEventKind.STEP_SUCCEEDED)
            self.assertEqual(start.activity_id, terminal.activity_id)
            self.assertLess(start.ordinal, terminal.ordinal)
            self.assertLess(terminal.ordinal, original.original_start_event.ordinal)
            allocation_starts.add(start.event_id)
        self.assertEqual(len(allocation_starts), 2)


class PostgresAcceptedInstallationSourcesTests(ManagedApplicationFixture):
    def setUp(self):
        super().setUp()
        self.runtime = ProfiledRecordingRuntime(self)

    def application(self):
        return super().application(runtime_registration=RuntimeAdapterRegistration(
            self.runtime, exact_version_execute_with_authority=True))

    async def test_generated_acceptance_binds_actual_terminal_and_full_prefix(self):
        from dataclasses import replace
        from tests.test_managed_application_chain import ManagedUpdateExecutionTests
        from control_plane_kit_operations.postgres.configuration_evidence import _Unavailable
        from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
        from control_plane_kit_operations.records import BoundedEvidence
        await ManagedUpdateExecutionTests.complete_initial(self)
        revision = self.plan_descriptor["desired_graph_revision"]
        preimage = self.connection.execute("SELECT installation_sources_preimage FROM cpk_configuration_acceptances "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (revision,)).fetchone()[0]
        binding, = json.loads(preimage)["bindings"]
        self.assertEqual(binding["node_id"], "connector")
        allocation, = binding["generated_allocations"]
        with self.unit_of_work() as uow:
            generated, = uow.stores.generated_ingress_secrets.list_for_workspace("workspace-a")
            start = uow.stores.execution.get_event(allocation["source_event_id"])
            terminal = uow.stores.execution.get_event(allocation["terminal_event_id"])
        self.assertEqual(allocation["source_event_id"], generated.source_event_id)
        self.assertIs(start.kind, ActivityEventKind.STEP_STARTED)
        self.assertIs(terminal.kind, ActivityEventKind.STEP_SUCCEEDED)
        self.assertEqual(start.activity_id, terminal.activity_id)
        self.assertLess(start.ordinal, terminal.ordinal)
        prefix = tuple(event for event in self.history() if event.ordinal <= terminal.ordinal)
        uncertain = replace(terminal, event_id="source-test-uncertain", kind=ActivityEventKind.STEP_UNCERTAIN)
        delayed_terminal = replace(terminal, ordinal=terminal.ordinal + 1)
        malformed = (*prefix[:-1], uncertain, delayed_terminal)
        self.assertTrue(project_activity_journal(self.plan, activity_journal_events(malformed)).uncertain)
        with self.assertRaises(_Unavailable):
            ConfigurationAcceptanceStore._direct_allocation_projection(self.plan, malformed, start, delayed_terminal)
        for first, last in ((1, True), (-1, -1), (0, 0)):
            with self.subTest(start_generation=first, terminal_generation=last):
                changed_start = replace(start, evidence=BoundedEvidence.from_mapping(
                    {**start.evidence.descriptor(), "claim_generation": first}))
                changed_terminal = replace(terminal, evidence=BoundedEvidence.from_mapping(
                    {**terminal.evidence.descriptor(), "claim_generation": last}))
                changed = tuple(changed_start if event.event_id == start.event_id else changed_terminal
                    if event.event_id == terminal.event_id else event for event in prefix)
                with self.assertRaises(_Unavailable):
                    ConfigurationAcceptanceStore._direct_allocation_projection(self.plan, changed, changed_start, changed_terminal)
        with _configuration_accounting(("1987-generated-cold", revision)), self.unit_of_work() as uow:
            uow.stores.configuration_acceptance._receipt("workspace-a", revision)
        effects = self.effects()
        original_payload = self.connection.execute("SELECT payload FROM cpk_activity_events WHERE event_id=%s",
            (terminal.event_id,)).fetchone()[0]
        self.addCleanup(self.connection.execute, "UPDATE cpk_activity_events SET payload=%s WHERE event_id=%s",
            (Jsonb(original_payload), terminal.event_id))
        # This root member is deliberately ignored by typed event decoding.
        # Only commitment to the full durable payload can catch its alteration.
        self.connection.execute("UPDATE cpk_activity_events SET payload=payload || "
            "jsonb_build_object('uninterpreted-member','changed') WHERE event_id=%s", (terminal.event_id,))
        before = self.durable_snapshot()
        with self.assertRaises(_Unavailable), _configuration_accounting(("1987-generated-corrupt", revision)), \
                self.unit_of_work() as uow:
            uow.stores.configuration_acceptance._receipt("workspace-a", revision)
        self.assertEqual(self.durable_snapshot(), before)
        self.assertEqual(self.effects(), effects)

    async def test_generated_allocation_prefix_capacity_inputs(self):
        try:
            await self.reach_native_wait()
        except AssertionError:
            if hasattr(self, "run_id"):
                # Failure-only diagnostic before owned cleanup: no payloads,
                # messages, references, credentials or query parameters emitted.
                rows = self.connection.execute("SELECT event_type, "
                    "CASE WHEN payload->'failure'->>'category' ~ '^[a-zA-Z0-9_.-]{1,128}$' "
                    "THEN payload->'failure'->>'category' END, "
                    "CASE WHEN payload->'failure'->>'code' ~ '^[a-zA-Z0-9_.-]{1,128}$' "
                    "THEN payload->'failure'->>'code' END FROM cpk_activity_events "
                    "WHERE run_id=%s ORDER BY ordinal DESC LIMIT 8", (self.run_id,)).fetchall()
                print("generated-setup-failure-codes", rows)
            raise
        before, effects = self.durable_snapshot(), self.effects()
        # Existing fixture establishes semantic coordinates outside the measured
        # reader. These are inputs, not free proof claimed for a future command.
        with self.unit_of_work() as uow:
            generated, = uow.stores.generated_ingress_secrets.list_for_workspace("workspace-a")
        journal = self.history()
        start, = (event for event in journal if event.event_id == generated.source_event_id)
        self.assertIs(start.kind, ActivityEventKind.STEP_STARTED)
        operation, = (activity.operation for activity in self.plan.activities
            if activity.activity_id.value == generated.source_activity_id)
        self.assertIs(type(operation), AllocatePublicIngress)
        terminal, = (event for event in journal if event.activity_id == start.activity_id
            and event.kind is ActivityEventKind.STEP_SUCCEEDED)
        self.assertEqual(terminal.evidence.descriptor()["claim_generation"],
            start.evidence.descriptor()["claim_generation"])
        self.assertTrue(terminal.evidence.descriptor()["details"]["connector_material_recorded"])
        expected = tuple(event for event in journal if event.ordinal <= terminal.ordinal)
        self.assertLess(start.ordinal, terminal.ordinal)
        self.assertEqual(sum(event.kind is ActivityEventKind.STEP_STARTED
            and event.activity_id == start.activity_id for event in expected), 1)
        self.assertEqual(project_activity_journal(self.plan, activity_journal_events(expected))
            .state.step(SagaStepId(start.activity_id)).status.value, "succeeded")
        consumer, = (request for request in self.runtime.calls
            if any(product.node_id == "connector" for product in request.products))
        consumer_identity = EffectAttemptIdentity(consumer.source.run_id, consumer.activity_id.value, 1)
        columns = (("event_id", "text", 2048), ("run_id", "text", 2048),
            ("ordinal", "int", 32), ("event_type", "text", 128),
            ("occurred_at", "time", 64), ("payload", "json", 65536))
        custody_columns = (("workspace_id", "text", 2048), ("purpose", "text", 128),
            ("secret_ref", "text", 2048), ("recorded_at", "time", 64),
            ("source_run_id", "text", 2048), ("source_activity_id", "text", 2048),
            ("source_event_id", "text", 2048), ("metadata", "json", 16384))
        with _configuration_accounting(("1987-measurement", "generated-prefix")) as accounting:
            observed = dict(bytes=0, rows=0, largest_cell=0, statements=0, queries=[],
                accounting=accounting, role_label=lambda _sql, _params: "generated-prefix")
            with PostgresUnitOfWork(lambda: _PhaseConnection(psycopg.connect(
                    os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]), observed)) as uow:
                stores, read = uow.stores, _EvidenceRead(uow.stores.connection)
                custody = read.bounded_rows("cpk_generated_ingress_secret_references", custody_columns,
                    "workspace_id=%s AND secret_ref=%s", ("workspace-a", generated.secret_ref.reference_id))
                self.assertEqual(len(custody), 1)
                source = stores.execution.get_event(start.event_id)
                end = stores.execution.get_event(terminal.event_id)
                self.assertEqual((source, end), (start, terminal))
                run = stores.execution.get_run(generated.source_run_id)
                request = stores.execution.get_request(run.admission.request_id)
                plan = stores.activity_history.get_plan(run.plan_id)
                self.assertEqual(request.identity.workspace_id, generated.workspace_id)
                self.assertEqual(plan.plan, self.plan)
                stores.graphs.get(plan.desired_graph_id)
                stores.realized_graphs.get(plan.desired_realized_projection_id)
                # Bounded independent count/identity capture, followed by the
                # existing measured native transport; no replacement SQL results.
                count, = read.query("SELECT count(*) FROM (SELECT ordinal FROM cpk_activity_events "
                    "WHERE run_id=%s AND ordinal<=%s ORDER BY ordinal LIMIT 8193) AS prefix",
                    (run.run_id, end.ordinal), records=1, octets=20, cells=1)[0]
                self.assertEqual(count, len(expected))
                self.assertLessEqual(count, 8192)
                keys = read.bounded_rows("cpk_activity_events", columns[:1] + columns[2:3],
                    "run_id=%s AND ordinal<=%s", (run.run_id, end.ordinal),
                    maximum=count, point=False, order="ordinal")
                self.assertEqual(keys, tuple((event.event_id, event.ordinal) for event in expected))
                for _ in range(2):
                    rows = read.bounded_rows("cpk_activity_events", columns,
                        "run_id=%s AND ordinal<=%s", (run.run_id, end.ordinal),
                        maximum=count, point=False, order="ordinal")
                    self.assertEqual(tuple(_activity_event(row) for row in rows), expected)
                original = stores.effect_attempt_intents.get(consumer_identity)
                attempt = stores.effect_attempts.get(consumer_identity)
                outcome = stores.effect_outcomes.get(consumer_identity, attempt.latest_transition_event.event_id)
                self.assertEqual(outcome.outcome.request_fingerprint, original.request_fingerprint)
                self.assertEqual(outcome.attempt, attempt)
                self.assertTrue(any(selection.reference == generated.secret_ref
                    for product in original.intent.products for selection in product.secret_selections))
                self.assertEqual(accounting.used.statements, observed["statements"])
                measurement_wire_report("generated-prefix-two-reads", observed, accounting.used)
        self.assertEqual(self.durable_snapshot(), before)
        self.assertEqual(self.effects(), effects)


class PostgresObservedInstallationSourcePublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_observed_replacement_advances_without_direct_completion_or_transfer(self):
        from tests.test_managed_observed_installation import ManagedObservedInstallationTests
        from tests.test_managed_configuration_update_execution import ManagedConfigurationUpdateExecutionTests
        from tests.execution_lease_recovery_fixture import Sequence
        from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
        from control_plane_kit_operations.effect_outcome_evidence import ObservedEffectOutcome
        from control_plane_kit_core.operations import ControlPlaneServiceRole
        async with ManagedObservedInstallationTests().started() as fixture:
            folded = EffectAttemptFoldService(fixture.unit_of_work,
                id_factory=Sequence("source-observed-success")).execute_observed(fixture.guarded(fixture.observation()))
            self.assertIs(type(folded.outcome_record.outcome), ObservedEffectOutcome)
            completed = await ManagedConfigurationUpdateExecutionTests.execute_content(fixture)
            self.assertEqual((completed["coordinator_status"], completed["run_status"]), ("completed", "succeeded"))
            claims = fixture.connection.execute("SELECT * FROM cpk_configuration_claims WHERE run_id=%s "
                "ORDER BY activity_id,attempt,artifact_id", (fixture.update_run,)).fetchall()
            effects = fixture.effects()
            plan = fixture.update_plan
            result = await fixture.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
                path={"workspace_id": "workspace-a", "run_id": fixture.update_run}, principal=fixture.worker,
                payload={"plan_id": fixture.record.plan_id,
                    "expected_current_graph_id": fixture.before_update.current_graph_id,
                    "expected_current_realized_projection_id": fixture.before_update.current_realized_projection_id,
                    "desired_graph_id": plan["desired_graph_id"], "desired_realized_projection_id": plan["desired_realized_projection_id"],
                    "expected_desired_graph_revision": plan["desired_graph_revision"],
                    "claim_generation": fixture.update_generation, "idempotency_key": "advance-observed-source"})
            self.assertEqual(result["to_graph_id"], plan["desired_graph_id"])
            self.assertEqual(fixture.connection.execute("SELECT * FROM cpk_configuration_claims WHERE run_id=%s "
                "ORDER BY activity_id,attempt,artifact_id", (fixture.update_run,)).fetchall(), claims)
            for table in ("cpk_configuration_invocation_completions", "cpk_configuration_claim_transfers"):
                self.assertEqual(fixture.connection.execute("SELECT count(*) FROM " + table + " WHERE run_id=%s",
                    (fixture.update_run,)).fetchone(), (0,))
            self.assertEqual(fixture.effects(), effects)
