"""#2007: current connector provenance through genuine accepted owner records.

New-law public projection of the existing accepted-source laws. Recording ports
simulate providers; PostgreSQL and admission/execution/advancement are real.
"""
from dataclasses import FrozenInstanceError, replace
import importlib
import json
import os
import re

import psycopg

from control_plane_kit_core.operations import ControlPlaneServiceRole, EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import StartNode
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_core.secret_installation import secret_installation_receipt_for_result
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.runtime_adapter_registration import RuntimeAdapterRegistration
from tests import test_managed_application_chain as chain
from tests import test_managed_configuration_update_execution as content
from tests import test_postgres_configuration_transfer_producer_receiver as recipient


class CurrentConnectorInstallationFixture(chain.ManagedApplicationFixture):
    complete_initial = chain.ManagedUpdateExecutionTests.complete_initial

    def setUp(self):
        super().setUp()
        self.runtime = recipient.ProfiledRecordingRuntime(self)

    def application(self):
        return super().application(runtime_registration=RuntimeAdapterRegistration(
            self.runtime, exact_version_execute_with_authority=True))

    def current(self):
        with self.unit_of_work() as uow:
            return uow.stores.workspaces.get("workspace-a")

    def read_installation(self, *, workspace_id="workspace-a", statements=None, pins=None, **overrides):
        current = self.current() if pins is None else pins
        arguments = dict(expected_current_graph_id=current.current_graph_id,
            expected_current_realized_projection_id=current.current_realized_projection_id,
            runtime_id="docker", node_id="connector")
        arguments.update(overrides)
        queries = [] if statements is None else statements

        class ReadConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, query, parameters=None):
                queries.append(str(query))
                return self.connection.execute(query, parameters)

        with PostgresUnitOfWork(lambda: ReadConnection(psycopg.connect(
                os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]))) as uow:
            reader = getattr(uow.stores.configuration_acceptance,
                "read_current_connector_installation", None)
            self.assertTrue(callable(reader), "current connector installation read is missing")
            result = reader(workspace_id, **arguments)
        self.assertFalse(any(re.search(
            r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|ALTER|CREATE|DROP)\b", query, re.I)
            for query in queries), "read must not mutate")
        self.assertFalse(any(re.search(
            r"\bFOR\s+(?:(?:NO\s+)?KEY\s+)?(?:UPDATE|SHARE)\b|\bLOCK\b|\bpg_\w*advisory\w*\s*\(",
            query, re.I) for query in queries), "read must not acquire locks")
        return result

    def assert_closed(self, value, state="unavailable"):
        self.assertEqual(type(value).__name__, "CurrentConnectorInstallationRead")
        self.assertEqual(value.state, state)
        self.assertIsNone(value.installation)

    def assert_original(self, value):
        self.assertEqual(type(value).__name__, "CurrentConnectorInstallationRead")
        self.assertEqual(value.state, "complete")
        installation = value.installation
        current = self.current()
        self.assertEqual((installation.workspace_id, installation.current_graph_id,
            installation.current_realized_projection_id, installation.runtime_id, installation.node_id),
            ("workspace-a", current.current_graph_id, current.current_realized_projection_id,
                "docker", "connector"))
        original, = (request for request in self.runtime.calls
            if type(request.operation) is StartNode and request.operation.target.node_id == "connector")
        self.assertEqual(installation.original_request, replace(original, secret_resolution_grants=()))
        self.assertEqual(installation.original_request.effect_id, original.effect_id)
        self.assertEqual(installation.original_request.secret_resolution_grants, ())
        with self.unit_of_work() as uow:
            identity = EffectAttemptIdentity(RunId(original.source.run_id.value),
                original.activity_id.value, 1)
            attempt = uow.stores.effect_attempts.get(identity)
            outcome = uow.stores.effect_outcomes.get(identity, attempt.latest_transition_event.event_id)
        self.assertEqual(installation.receipt, secret_installation_receipt_for_result(original, outcome.outcome.result))
        self.assertNotIn("RuntimeEffectRequest", repr(value))
        self.assertNotIn("SecretInstallationReceipt", repr(value))
        self.assertNotIn(installation.receipt.request_fingerprint, repr(value))
        self.assertNotIn(installation.receipt.current_realization_fingerprint, repr(value))
        for selection in original.products[0].secret_selections:
            self.assertNotIn(selection.reference.reference_id, repr(value))
        return installation

    def assert_outcome_corruption_has_no_dependents(self, request):
        """Admit test-only corruption without violating or disabling any FK."""
        self.assertIs(request.kind, RuntimeEffectKind.REALIZE_ACTIVITY)
        identity = request.source.run_id.value, request.activity_id.value, 1
        for relation, prefix in (("cpk_effect_attempt_outcome_observations", ""),
                ("cpk_configuration_accepted_slots", "source_"),
                ("cpk_configuration_invocation_completions", ""),
                ("cpk_failed_run_compensation_steps", "source_"),
                ("cpk_configuration_cleanup_member_outcomes", "cleanup_")):
            count, = self.connection.execute("SELECT count(*) FROM " + relation
                + " WHERE " + prefix + "run_id=%s AND " + prefix + "activity_id=%s AND "
                + prefix + "attempt=%s", identity).fetchone()
            self.assertEqual(count, 0, "negative corruption requires no FK dependents: " + relation)


class CurrentConnectorInstallationTests(CurrentConnectorInstallationFixture):
    async def test_artifactless_connector_returns_exact_original_without_effects_or_writes(self):
        await self.complete_initial()
        self.assertEqual(self.graph.node("connector").configuration_artifacts, ())
        before = self.durable_snapshot(), self.effects(), self.rows("cpk_configuration_acceptances")
        installation = self.assert_original(self.read_installation())
        self.assertEqual(installation.current_revision, self.plan_descriptor["desired_graph_revision"])
        self.assertEqual((self.durable_snapshot(), self.effects(), self.rows("cpk_configuration_acceptances")), before)

    async def test_wrong_pins_workspace_runtime_and_nonconnector_are_closed(self):
        await self.complete_initial()
        self.assert_original(self.read_installation())
        before = self.durable_snapshot(), self.effects()
        for change in ({"workspace_id": "foreign"}, {"expected_current_graph_id": "foreign"},
                {"expected_current_realized_projection_id": "foreign"}, {"runtime_id": "foreign"},
                {"node_id": "missing"}, {"node_id": "api"}, {"node_id": "gateway"},
                {"node_id": True}, {"runtime_id": "x" * 2049}, {"expected_current_graph_id": "\n"}):
            with self.subTest(change=change):
                self.assert_closed(self.read_installation(**change))
        self.assertEqual((self.durable_snapshot(), self.effects()), before)

    async def test_missing_selected_connector_outcome_is_unavailable(self):
        await self.complete_initial()
        installation = self.assert_original(self.read_installation())
        source = installation.original_request
        self.assert_outcome_corruption_has_no_dependents(source)
        self.connection.execute("DELETE FROM cpk_effect_attempt_outcomes "
            "WHERE run_id=%s AND activity_id=%s AND attempt=1",
            (source.source.run_id.value, source.activity_id.value))
        before = self.durable_snapshot(), self.effects(), self.rows("cpk_configuration_acceptances")
        self.assert_closed(self.read_installation())
        self.assertEqual((self.durable_snapshot(), self.effects(), self.rows("cpk_configuration_acceptances")), before)

    async def test_corrupt_bundle_or_original_outcome_is_unavailable_without_fallback(self):
        await self.complete_initial()
        installation = self.assert_original(self.read_installation())
        self.assert_outcome_corruption_has_no_dependents(installation.original_request)
        revision = installation.current_revision
        preimage, = self.connection.execute("SELECT installation_sources_preimage FROM cpk_configuration_acceptances "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (revision,)).fetchone()
        binding, = json.loads(preimage)["bindings"]
        source = binding["source"]
        identity = source["run_id"], source["activity_id"], source["attempt"]
        fingerprint, status = self.connection.execute("SELECT outcome_fingerprint, status FROM cpk_effect_attempt_outcomes "
            "WHERE run_id=%s AND activity_id=%s AND attempt=%s", identity).fetchone()
        changes = (
            ("UPDATE cpk_configuration_acceptances SET installation_sources_preimage=%s "
                "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (revision,), "{}", preimage),
            ("UPDATE cpk_effect_attempt_outcomes SET outcome_fingerprint=%s "
                "WHERE run_id=%s AND activity_id=%s AND attempt=%s", identity, "0" * 64, fingerprint),
            ("UPDATE cpk_effect_attempt_outcomes SET status=%s "
                "WHERE run_id=%s AND activity_id=%s AND attempt=%s", identity, "uncertain", status),
        )
        for statement, key, corrupt, original in changes:
            with self.subTest(statement=statement):
                self.connection.execute(statement, (corrupt, *key))
                try:
                    before = self.durable_snapshot(), self.effects()
                    self.assert_closed(self.read_installation())
                    self.assertEqual((self.durable_snapshot(), self.effects()), before)
                finally:
                    self.connection.execute(statement, (original, *key))
                self.assert_original(self.read_installation())

    async def test_nested_reads_share_capacity_and_fresh_read_recovers(self):
        await self.complete_initial()
        before = self.durable_snapshot(), self.effects()
        pins = self.current()
        with _configuration_accounting(("2007-current-connector", "workspace-a")) as accounting:
            states = []
            for _ in range(256):
                result = self.read_installation(pins=pins)
                states.append(result.state)
                if result.state != "complete":
                    self.assert_closed(result, "capacity")
                    break
            self.assertEqual(states[0], "complete")
            self.assertEqual(states[-1], "capacity")
            self.assertGreater(len(states), 1)
            self.assertLessEqual(accounting.used.records, 8192)
            self.assertLessEqual(accounting.used.accounted_bytes, 16 * 1024 * 1024)
        # Exact boundary probe in addition to the real cumulative reads above:
        # a caller with no remaining record allowance must dispatch no SQL.
        with _configuration_accounting(("2007-exhausted", "workspace-a")) as accounting:
            accounting.used = ConfigurationEvidenceFootprint(8192, 0, 0, 0)
            statements = []
            self.assert_closed(self.read_installation(pins=pins, statements=statements), "capacity")
            self.assertEqual(statements, [])
            self.assertEqual(accounting.used, ConfigurationEvidenceFootprint(8192, 0, 0, 0))
        self.assert_original(self.read_installation())
        self.assertEqual((self.durable_snapshot(), self.effects()), before)

    async def test_values_are_strict_frozen_correlated_and_redacted(self):
        await self.complete_initial()
        installation = self.assert_original(self.read_installation())
        module = importlib.import_module("control_plane_kit_operations.connector_installation")
        read_type = module.CurrentConnectorInstallationRead
        self.assertIs(type(installation), module.CurrentConnectorInstallation)
        nonconnector, = (request for request in self.runtime.calls
            if type(request.operation) is StartNode and request.operation.target.node_id == "api")
        connector, = (request for request in self.runtime.calls
            if type(request.operation) is StartNode and request.operation.target.node_id == "connector")
        self.assertTrue(connector.secret_resolution_grants)
        for state, value in (("complete", None), ("unavailable", installation),
                ("capacity", installation), ("unknown", None), (True, None), ("complete", object())):
            with self.subTest(state=state), self.assertRaises((TypeError, ValueError)):
                read_type(state, value)
        for changes in ({"current_revision": True}, {"current_revision": -1}, {"current_revision": 2 ** 53},
                {"runtime_id": "foreign"}, {"node_id": "api"}, {"workspace_id": "foreign"},
                {"node_id": "x" * 2049}, {"current_graph_id": "\n"},
                {"receipt": replace(installation.receipt, request_fingerprint="0" * 64)},
                {"original_request": nonconnector}, {"original_request": connector}):
            with self.subTest(fields=tuple(changes)), self.assertRaises((TypeError, ValueError)):
                replace(installation, **changes)
        with self.assertRaises(FrozenInstanceError):
            installation.node_id = "other"


class CurrentConnectorInstallationContentUpdateTests(CurrentConnectorInstallationFixture):
    # Reuse owner-driving helpers without collecting their inherited test cases.
    application_artifacts = (content.APPLICATION,)
    desired_update = content.ManagedConfigurationUpdateExecutionTests.desired_update
    prepare_content = content.ManagedConfigurationUpdateExecutionTests.prepare_content
    execute_content = content.ManagedConfigurationUpdateExecutionTests.execute_content

    async def test_workload_content_update_carries_original_connector_at_new_current_revision(self):
        await self.prepare_content()
        initial = self.assert_original(self.read_installation())
        result = await self.execute_content()
        self.assertEqual((result["coordinator_status"], result["run_status"]), ("completed", "succeeded"))
        plan = self.update_plan
        await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
            payload={"plan_id": self.record.plan_id,
                "expected_current_graph_id": self.before_update.current_graph_id,
                "expected_current_realized_projection_id": self.before_update.current_realized_projection_id,
                "desired_graph_id": plan["desired_graph_id"],
                "desired_realized_projection_id": plan["desired_realized_projection_id"],
                "expected_desired_graph_revision": plan["desired_graph_revision"],
                "claim_generation": self.update_generation, "idempotency_key": "advance-content"})
        carried = self.assert_original(self.read_installation())
        self.assertEqual(carried.original_request, initial.original_request)
        self.assertEqual(carried.receipt, initial.receipt)
        self.assertEqual(carried.current_revision, plan["desired_graph_revision"])
        self.assertGreater(carried.current_revision, initial.current_revision)
        self.assertNotEqual(carried.current_graph_id, initial.current_graph_id)
        self.assert_closed(self.read_installation(expected_current_graph_id=initial.current_graph_id,
            expected_current_realized_projection_id=initial.current_realized_projection_id))
        # The original A acceptance stays valid. Loss of B's exact selected
        # commitment must not be repaired by falling back to A's valid success.
        original_rows = self.connection.execute("SELECT installation_sources_preimage FROM cpk_configuration_acceptances "
            "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (initial.current_revision,)).fetchall()
        self.assertEqual(len(original_rows), 1)
        for damaged in ("{}", ""):
            self.connection.execute("UPDATE cpk_configuration_acceptances SET installation_sources_preimage=%s "
                "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (damaged, carried.current_revision))
            before = self.durable_snapshot(), self.effects(), self.rows("cpk_configuration_acceptances")
            self.assert_closed(self.read_installation())
            self.assertEqual((self.durable_snapshot(), self.effects(), self.rows("cpk_configuration_acceptances")), before)
            self.assertEqual(self.connection.execute("SELECT installation_sources_preimage FROM cpk_configuration_acceptances "
                "WHERE workspace_id='workspace-a' AND pinned_revision=%s", (initial.current_revision,)).fetchall(), original_rows)
