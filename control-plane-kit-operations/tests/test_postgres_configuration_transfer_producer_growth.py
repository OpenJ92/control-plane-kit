"""More than 64 real accepted uses; history grows without active-claim growth."""
from dataclasses import replace
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCompletion, configuration_invocation_correlation_for_request,
    configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import NodeTarget, ReconcileNode
from control_plane_kit_core.products import ProductInstanceConfiguration, instantiate_product
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_core.topology import compile_topology
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from tests import test_execution_coordinator as coordinator_fixture
from tests import test_postgres_configuration_carry as carry_fixture
from tests import test_postgres_configuration_transfer_producer as producer
from tests import test_postgres_configuration_transfer_publication as physical
from tests.test_runtime_effect_translation import _configuration_product


class PostgresConfigurationTransferProducerGrowthTests(unittest.TestCase):
    # Reuse the existing approval/admission/lifecycle helper unchanged. It
    # requires only this case's base, graph_version and unittest assertions.
    admit = carry_fixture.PostgresConfigurationCarryTests.admit

    def setUp(self):
        fixture = producer.PostgresConfigurationTransferProducerTests()
        self.addCleanup(lambda: self.assertTrue(fixture.doCleanups(), "producer fixture cleanup failed"))
        self.member = fixture.configured_member()
        self.base, self.graph_version = self.member.fixture, 3
        self.refs, self.birth = self.member.refs, self.member.original.identity
        self.product = _configuration_product().descriptor_document.product
        self.samples = []

    def changed_graph(self, number):
        configuration = replace(ProductInstanceConfiguration.from_contract(self.product.runtime_contract),
            public_environment=(PublicStaticEnvironmentBinding("HELLO_MESSAGE", "accepted-use-" + str(number)),))
        return compile_topology(DeploymentTopology("configured", DockerRuntime(runtime_id="runtime-a", children=(
            instantiate_product(self.product, "api", configuration),))))

    def profiled_installation(self, _context, request):
        context = configuration_invocation_correlation_for_request(request)
        completion = ConfigurationInvocationCompletion(context.request_fingerprint,
            configuration_invocation_selection_fingerprint(context.selection))
        return RuntimeEffectResult.succeeded(request.effect_id, evidence={
            "adapter": "simulated-total-configuration-invocation",
            "configuration_invocation_completion": completion.descriptor()})

    def assert_claim_counts(self, uses, active):
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(self.member.connection.execute("SELECT allocation_id,count(*),count(*) FILTER (WHERE protective) "
                "FROM " + table + " GROUP BY allocation_id ORDER BY allocation_id").fetchall(),
                [(ref.allocation_id, uses, active) for ref in sorted(self.refs, key=lambda ref: ref.allocation_id)])

    def accept(self, command, number, identity, *, sample):
        observed, state = physical.observation(), {}
        preflight, commit = ConfigurationAcceptanceStore._preflight, PostgresUnitOfWork.commit
        def admit(store, prepared):
            result = preflight(store, prepared)
            self.assertEqual({item.identity for item in prepared.transfers}, {identity})
            self.assertEqual({item.ref for item in prepared.transfers}, set(self.refs))
            if sample:
                phase = "pre_id" if prepared.event is None else "bound"
                self.assertNotIn(phase, state)
                state[phase] = (prepared.evidence_read.used, *store._publication_budgets(prepared), len(observed["queries"]))
                observed["accounting"] = _ACCOUNTING.get()
            return result
        def committed(uow):
            if sample and _ACCOUNTING.get() is observed["accounting"]:
                state["end"] = observed["accounting"].used
            return commit(uow)
        factory = (lambda: PostgresUnitOfWork(lambda: physical.PublicationWire(
            psycopg.connect(self.base.database_url), observed))) if sample else self.base.unit_of_work
        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", admit), \
                mock.patch.object(PostgresUnitOfWork, "commit", committed):
            accepted = CurrentGraphAdvancementCommandService(factory,
                clock=lambda: "2026-07-22T13:05:00Z",
                id_factory=iter(("growth-event-" + str(number), "growth-action-" + str(number))).__next__).execute(command)
        self.assertFalse(accepted.replayed)
        self.assertEqual(self.member.connection.execute("SELECT run_id,activity_id,attempt,artifact_id,acceptance_revision "
            "FROM cpk_configuration_claim_transfers WHERE run_id=%s ORDER BY artifact_id", (identity.run_id.value,)).fetchall(),
            [(identity.run_id.value, identity.activity_id, identity.attempt, ref.artifact_id, accepted.desired_graph_revision)
                for ref in self.refs])
        self.assertEqual(self.member.connection.execute("SELECT count(*) FROM cpk_configuration_claim_transfers").fetchone(),
            (number * len(self.refs),))
        self.assert_claim_counts(number, 0)
        if not sample:
            return
        self.assertEqual(set(state), {"pre_id", "bound", "end"})
        for phase in ("pre_id", "bound"):
            prior, _, _, declaration, offset = state[phase]
            used = physical.difference(state["end"], prior)
            physical.within(self, used, declaration.settled)
            selected = observed["queries"][offset:]
            for entry in selected:
                physical.within(self, physical.difference(physical.Footprint(*entry["peak"]), prior), declaration.peak)
            physical.reconciles(self, used, {"queries": selected})
            print("producer-growth", number, phase, "used", used, "settled", declaration.settled, "peak", declaration.peak)
        cold = physical.observation()
        with PostgresUnitOfWork(lambda: physical.PublicationWire(
                psycopg.connect(self.base.database_url), cold, cold=True)) as uow:
            current = uow.stores.configuration_acceptance.read_current_configuration("workspace-a")
        self.assertEqual((current.state, current.pinned_revision, current.manifest_slot_count),
            ("complete", accepted.desired_graph_revision, len(self.refs)))
        self.assertEqual(tuple(binding.ref for binding in current.bindings), self.refs)
        self.assertTrue(all(binding.source.identity == identity and binding.birth.identity == self.birth
            for binding in current.bindings))
        for phase in ("pre_id", "bound"):
            future = state[phase][2]
            physical.within(self, cold["accounting"].used, future.settled)
            for entry in cold["queries"]:
                physical.within(self, physical.Footprint(*entry["peak"]), future.peak)
        physical.reconciles(self, cold["accounting"].used, cold)
        self.samples.append(number)
        print("producer-growth", number, "cold", cold["accounting"].used)

    def test_sixty_five_genuine_accepted_uses_preserve_birth_and_bounded_active_claims(self):
        self.assert_claim_counts(1, 1)
        self.accept(self.member.command(), 1, self.birth, sample=True)
        for number in range(2, 66):
            label = "accepted-" + str(number)
            command = self.admit(label, "graph-" + label, ReconcileNode(NodeTarget("api")), graph=self.changed_graph(number))
            identity = EffectAttemptIdentity(RunId(command.run_id), "activity-" + label, 1)
            engine = self.base.engine
            adapter = coordinator_fixture.RecordingAdapter(engine.tracker, self.profiled_installation)
            result = engine.coordinator(adapter).execute(replace(engine.command(
                generation=command.fence.generation, idempotency_key="execute-" + label), run_id=command.run_id))
            self.assertIs(result.status, CoordinatorStatus.COMPLETED)
            self.assertEqual(adapter.calls, [identity.activity_id])
            self.assertEqual(adapter.active_during_calls, [0])
            with self.base.unit_of_work() as uow:
                original = uow.stores.effect_attempt_intents.get(identity)
                completion = uow.stores.configuration_completions.get(identity)
            self.assertEqual(original.intent.configuration_instances.instances, self.refs)
            self.assertIsNotNone(completion)
            self.assertEqual(completion.identity, identity)
            self.assertEqual(completion.request_fingerprint, original.request_fingerprint)
            self.assertEqual(self.member.connection.execute("SELECT birth_run_id,birth_activity_id,birth_attempt,birth_artifact_id,is_birth "
                "FROM cpk_effect_configuration_refs WHERE run_id=%s ORDER BY artifact_id", (command.run_id,)).fetchall(),
                [(self.birth.run_id.value, self.birth.activity_id, self.birth.attempt, ref.artifact_id, False) for ref in self.refs])
            self.assert_claim_counts(number, 1)
            self.accept(command, number, identity, sample=number in (2, 65))
        self.assertEqual(self.samples, [1, 2, 65])
        self.assertEqual(self.member.connection.execute("SELECT count(*) FROM cpk_configuration_invocation_completions").fetchone(), (65,))
        self.assert_claim_counts(65, 0)
