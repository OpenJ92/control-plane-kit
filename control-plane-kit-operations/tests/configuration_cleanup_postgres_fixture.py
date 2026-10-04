"""C2 real owner fixture; the adapter simulates completion, never provider proof."""
from dataclasses import replace
from importlib import import_module
from itertools import count

import psycopg
import rfc8785

from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCompletion, configuration_invocation_correlation_for_request,
    configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind, WorkspaceGrant
from control_plane_kit_core.planning import NodeTarget, ReconcileNode
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.products import ProductDescriptorCodec
from control_plane_kit_core.probe_intents import EndpointContext, LiteralEndpointMaterial, RuntimeEndpointObservation
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_core.types import Protocol
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.effect_attempts import effect_attempt_state_fingerprint
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.products import RegisteredProduct
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.configuration_cleanup_contract_fixture import require_cleanup
from tests import test_postgres_configuration_acceptance_membership as membership
from tests import test_postgres_configuration_carry as carry
from tests import test_execution_coordinator as coordinator
from tests.test_runtime_effect_translation import _configuration_product


NOW = "2026-10-03T12:00:00Z"
READ_PLAN = (PolicyScope.INSTANCE_WORKSPACE_READ, PolicyScope.PLAN_REQUEST)


def completion_result(request):
    source = configuration_invocation_correlation_for_request(request)
    completion = ConfigurationInvocationCompletion(source.request_fingerprint,
        configuration_invocation_selection_fingerprint(source.selection))
    return RuntimeEffectResult.succeeded(request.effect_id, evidence={
        "adapter": "simulated-total-configuration-invocation",
        "configuration_invocation_completion": completion.descriptor()}, observations=(RuntimeEndpointObservation(
            subject_id="api", socket_name="http", graph_id=source.source.desired_graph_id,
            protocol=Protocol.HTTP, context=EndpointContext.RUNTIME_PRIVATE,
            address=LiteralEndpointMaterial("http://configuration-test.invalid:8080")),))


def command_context(*, scopes=READ_PLAN, workspace="workspace-a", kind=PrincipalKind.OPERATOR, actor="planner"):
    return AuthenticatedPrincipal(PrincipalIdentity("urn:test:cleanup", actor, kind),
        (WorkspaceGrant(workspace, scopes),)).command_context(workspace)


class ConfigurationCleanupPostgresFixture:
    def setUp(self):
        self.values = require_cleanup(self)
        self.commands = import_module("control_plane_kit_operations.configuration_cleanup_planning")
        self.member = membership.PostgresConfigurationAcceptanceMembershipTests()
        self.member.configuration_result_for_request = completion_result
        if getattr(self, "single_artifact", False):
            registered = _configuration_product()
            product = registered.descriptor_document.product
            product = replace(product, runtime_contract=replace(product.runtime_contract,
                configuration_artifacts=product.runtime_contract.configuration_artifacts[:1]))
            self.member.registered_product = RegisteredProduct.from_document(workspace_id="workspace-a",
                descriptor_document=ProductDescriptorCodec().encode_document(product), source=registered.source,
                imported_by=registered.imported_by, imported_at=registered.imported_at)
        self.addCleanup(self.cleanup_member)
        self.member.setUp()
        self.base = self.member.fixture
        self.connection, self.database_url = self.base.connection, self.base.database_url
        self.refs, self.original = self.member.refs, self.member.original
        self.sampled = []
        self.serial = count()

    def cleanup_member(self):
        self.assertTrue(self.member.doCleanups(), "cleanup owner fixture cleanup failed")

    def sample_clock(self):
        self.sampled.append("clock")
        return NOW

    def sample_id(self):
        self.sampled.append("id")
        return "cleanup-" + str(next(self.serial))

    def unit_of_work(self):
        return self.base.unit_of_work()

    def service(self, *, factory=None):
        return self.commands.ConfigurationCleanupPlanningService(factory or self.unit_of_work,
            clock=self.sample_clock, id_factory=self.sample_id)

    def pins(self):
        with self.unit_of_work() as uow:
            row = uow.stores.workspaces.get("workspace-a")
        return self.values.ConfigurationCleanupExpectedContext(row.current_graph_id,
            row.current_realized_projection_id, row.desired_graph_id,
            row.desired_realized_projection_id, row.desired_graph_revision)

    def query(self, refs=None, *, identity=None, pins=None, session="session-config"):
        refs = self.refs if refs is None else refs
        identity = self.original.identity if identity is None else identity
        selectors = tuple(self.values.ConfigurationCleanupSourceSelector(identity, ref.artifact_id, ref) for ref in refs)
        return self.commands.InspectConfigurationCleanup(session, "workspace-a", pins or self.pins(), selectors)

    def inspect(self, query=None, *, factory=None, context=None):
        return self.service(factory=factory).inspect(query or self.query(), context=context or command_context())

    def request(self, *, key="publish-cleanup", query=None, inspection=None):
        query = query or self.query()
        inspection = inspection or self.inspect(query)
        self.assertEqual(inspection.state, "complete")
        return self.commands.RequestConfigurationCleanupPlan(query.session_id, query.workspace_id,
            IdempotencyKey(key), query.expected_context, query.selectors, inspection.inspection.evidence_digest)

    def publish(self, command=None, *, factory=None, context=None):
        return self.service(factory=factory).request_plan(command or self.request(), context=context or command_context())

    def truth(self):
        tables = ("cpk_workspaces", "cpk_activity_plans", "cpk_operation_actions", "cpk_approval_requests",
            "cpk_approval_decisions", "cpk_configuration_claims", "cpk_effect_configuration_refs",
            "cpk_effect_attempts", "cpk_effect_attempt_intents", "cpk_effect_attempt_outcomes",
            "cpk_configuration_invocation_completions",
            "cpk_activity_events", "cpk_configuration_acceptances", "cpk_configuration_accepted_slots")
        return tuple((table, self.connection.execute("SELECT row_to_json(t)::text FROM " + table +
            " t ORDER BY row_to_json(t)::text").fetchall()) for table in tables)

    def assert_unavailable(self, result, state="unavailable"):
        self.assertEqual(result.state, state)
        self.assertIsNone(result.inspection)
        self.assertNotIn("CANARY", repr(result))

    def later_use(self, label, *, producer=completion_result, expected_status=CoordinatorStatus.COMPLETED):
        """Reuse an accepted exact allocation via real admission/start/fold owners."""
        operator = self.carry_operator()
        command = operator.admit(label, "graph-" + label, ReconcileNode(NodeTarget("api")), graph=operator.graph)
        result = self.execute_later(command, label, producer=producer, expected_status=expected_status)
        return command, result

    def retain_historical_malformed_completion(self, identity):
        """Build archived no-link evidence, never admit an invalid fresh result.

        The real seed already owns its terminal state, source and event IDs.
        Only retained evidence and its existing fingerprint commitments change.
        """
        key = (identity.run_id.value, identity.activity_id, identity.attempt)
        with self.unit_of_work() as uow:
            stores = uow.stores
            attempt = stores.effect_attempts.get(identity)
            direct = attempt.latest_transition_event
            retained = stores.effect_outcomes.get(identity, direct.event_id)
            self.assertEqual(retained.endpoint_observations, ())
            self.assertIsNotNone(stores.configuration_completions.get(identity))
            malformed = replace(retained.outcome.result, evidence={
                **retained.outcome.result.evidence,
                "configuration_invocation_completion": {"profile": "CANARY"}})
            outcome = replace(retained.outcome, result=malformed)
            state = replace(attempt.state, outcome_fingerprint=outcome.outcome_fingerprint)
            connection = stores.connection
            self.assertEqual(connection.execute(
                "DELETE FROM cpk_configuration_invocation_completions "
                "WHERE (run_id,activity_id,attempt)=(%s,%s,%s) RETURNING 1", key).fetchone(), (1,))
            self.assertEqual(connection.execute(
                "UPDATE cpk_effect_attempt_outcomes SET preimage=%s,outcome_fingerprint=%s "
                "WHERE (run_id,activity_id,attempt)=(%s,%s,%s) RETURNING 1",
                (rfc8785.dumps(malformed.descriptor()), outcome.outcome_fingerprint, *key)).fetchone(), (1,))
            self.assertEqual(connection.execute(
                "UPDATE cpk_effect_attempts SET outcome_fingerprint=%s "
                "WHERE (run_id,activity_id,attempt)=(%s,%s,%s) RETURNING 1",
                (outcome.outcome_fingerprint, *key)).fetchone(), (1,))
            self.assertEqual(connection.execute(
                "UPDATE cpk_activity_events SET payload=jsonb_set(payload, "
                "'{evidence,effect_attempt,state_fingerprint}',to_jsonb(%s::text)) "
                "WHERE (event_id,run_id,ordinal)=(%s,%s,%s) RETURNING 1",
                (effect_attempt_state_fingerprint(state), direct.event_id, direct.run_id, direct.ordinal)).fetchone(), (1,))
            uow.commit()
        with self.unit_of_work() as uow:
            self.assertIsNone(uow.stores.configuration_completions.get(identity))
            self.assertEqual(uow.stores.effect_attempts.get(identity).state, state)
            self.assertEqual(uow.stores.effect_outcomes.get(identity, direct.event_id).outcome.result, malformed)
        return malformed

    def carry_operator(self):
        operator = carry.PostgresConfigurationCarryTests()
        operator.base, operator.fixture = self.base, self.member
        operator.graph_version = 3 + next(self.serial)
        with self.unit_of_work() as uow:
            operator.graph = DEFAULT_GRAPH_CODEC.decode(uow.stores.graphs.get("graph-configured").graph_descriptor)
        return operator

    def execute_later(self, command, label, *, producer=completion_result, expected_status=CoordinatorStatus.COMPLETED):
        adapter = coordinator.RecordingAdapter(self.base.engine.tracker, lambda _context, request: producer(request))
        result = self.base.engine.coordinator(adapter).execute(replace(self.base.engine.command(
            generation=command.fence.generation, idempotency_key="execute-" + label), run_id=command.run_id))
        self.assertEqual(adapter.active_during_calls, [0])
        self.assertEqual(adapter.calls, ["activity-" + label])
        self.assertIs(result.status, expected_status)
        return result

    def failure_factory(self, *, commit=False):
        owner = self

        class FailingConnection:
            def __init__(self):
                self.connection = psycopg.connect(owner.database_url)

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, query, parameters=None):
                if not commit and str(query).lstrip().upper().startswith("INSERT INTO CPK_OPERATION_ACTIONS"):
                    raise RuntimeError("injected action failure")
                return self.connection.execute(query, parameters)

            def commit(self):
                raise RuntimeError("injected commit failure")

        return lambda: PostgresUnitOfWork(FailingConnection)

    def assert_accounted_command(self, call):
        """Observe actual command SQL without replacing any evidence result."""
        from unittest import mock
        from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead
        from tests.test_postgres_configuration_evidence import _ObservedConnection
        observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)
        seen = []
        actual = _EvidenceRead.query

        def record(reader, sql, params, **kwargs):
            result = actual(reader, sql, params, **kwargs)
            seen.append((id(reader), id(reader.accounting), reader.used))
            return result

        factory = lambda: PostgresUnitOfWork(lambda: _ObservedConnection(psycopg.connect(self.database_url), observed))
        with mock.patch.object(_EvidenceRead, "query", record):
            result = call(factory)
        self.assertTrue(seen)
        self.assertEqual(len({(row[0], row[1]) for row in seen}), 1)
        footprint = seen[-1][2]
        self.assertEqual(footprint.statements, observed["statements"])
        self.assertGreaterEqual(footprint.records, observed["rows"])
        self.assertGreaterEqual(footprint.accounted_bytes, observed["bytes"])
        self.assertLessEqual(footprint.records, 4096)
        self.assertLessEqual(footprint.accounted_bytes, 16 * 1024 * 1024)
        return result
