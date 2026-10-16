"""#1987 original-source publication laws and retained scalar input measurements."""
import os
import json
import unittest
from unittest import mock
from hashlib import sha256

import psycopg
from psycopg.types.json import Jsonb
import rfc8785

from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_core.planning import AllocatePublicIngress
from control_plane_kit_core.planning.saga import SagaStepId, project_activity_journal
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead
from control_plane_kit_operations.postgres.execution import _activity_event
from control_plane_kit_operations.runtime_adapter_registration import RuntimeAdapterRegistration
from tests.configuration_cleanup_phase_read_bounds_fixture import _PhaseConnection
from tests.test_managed_application_chain import ManagedApplicationFixture
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
