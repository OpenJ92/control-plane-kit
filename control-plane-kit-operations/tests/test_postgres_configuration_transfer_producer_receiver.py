"""Genuine configuration transfers and receiver FINISH in one managed run.

Operations, admission, starts, folds, receipts and history are real. Runtime,
ingress and health ports remain the existing explicit recording interpreters.
No predecessor journal, configuration completion, or transfer row is seeded.
"""
from dataclasses import replace
from hashlib import sha256
from unittest import mock

from control_plane_kit_core.configuration_installation import (
    ConfigurationInstallationDisposition, ConfigurationInstallationReceipt, ConfigurationInstallationReceiptCodec,
)
from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCompletion, configuration_invocation_correlation_for_request,
    configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.operations import ControlPlaneServiceRole, EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import StartNode, ReconcileNode
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_fingerprint, runtime_effect_intent_for_request,
)
from control_plane_kit_core.runtime_effects import EffectResultKind, RuntimeEffectKind
from control_plane_kit_core.secret_installation import (
    SecretResolutionReceipt, SecretInstallationReceipt,
    secret_installation_receipt_for_result, secret_material_selection_fingerprint,
)
from control_plane_kit_core.secret_version_grants import SecretVersionResolutionGrant, validate_resolution_grant
from control_plane_kit_core.secrets import SecretReference, SecretUseIntent
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
from control_plane_kit_operations import advancement as advancement_module
from control_plane_kit_operations.effect_outcome_evidence import NativeConnectionOutcome
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.runtime_adapter_registration import RuntimeAdapterRegistration
from tests import receiver_health_execution_fixture as receiver
from tests import test_managed_application_chain as managed
from tests import test_postgres_configuration_transfer_publication as physical


class ProfiledRecordingRuntime(managed.RecordingRuntime):
    """Explicit simulated exact-version recipient; no provider conformance claim."""

    def _require_connector_grant(self, request, token_reference):
        grants = tuple(grant for grant in request.secret_resolution_grants
            if type(grant) is SecretVersionResolutionGrant
            and grant.selection.reference == SecretReference(token_reference)
            and grant.selection.intent is SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN)
        self.test.assertEqual(len(grants), 1, "connector requires its generated, selected version grant")

    def execute_with_authority(self, request, authority):
        # Validate complete consumer coverage before the base port mutates its
        # simulated containers. Operational grants remain a separate role.
        for grant in request.secret_resolution_grants:
            validate_resolution_grant(grant)
        selected = tuple(selection for material in request.products for selection in material.secret_selections
            if type(request.operation) in (StartNode, ReconcileNode))
        consumers = tuple(grant for grant in request.secret_resolution_grants
            if type(grant) is SecretVersionResolutionGrant)
        self.test.assertCountEqual(tuple(grant.selection for grant in consumers), selected)
        fingerprint = runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(request))
        selection_fingerprint = secret_material_selection_fingerprint(request) if selected else None
        for grant in consumers:
            self.test.assertEqual((grant.workspace_id, grant.operation_id, grant.run_id,
                grant.activity_id, grant.effect_id, grant.request_fingerprint, grant.selection_fingerprint),
                (request.source.workspace_id, request.source.request_id, request.source.run_id.value,
                 request.activity_id.value, request.effect_id, fingerprint, selection_fingerprint))
        result = super().execute_with_authority(request, authority)
        if result.kind is not EffectResultKind.SUCCEEDED:
            return result
        evidence = dict(result.evidence)
        configuration = None
        if request.kind is RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1:
            context = configuration_invocation_correlation_for_request(request)
            configuration_selection = configuration_invocation_selection_fingerprint(context.selection)
            evidence["configuration_invocation_completion"] = ConfigurationInvocationCompletion(
                context.request_fingerprint, configuration_selection).descriptor()
            if "configuration_installation_receipt" in evidence:
                configuration = ConfigurationInstallationReceiptCodec().decode(evidence["configuration_installation_receipt"])
            elif selected:
                self.test.assertIs(type(request.operation), StartNode)
                configuration = ConfigurationInstallationReceipt(fingerprint, configuration_selection,
                    ConfigurationInstallationDisposition.CREATED, None, sha256(request.effect_id.encode()).hexdigest())
                evidence["configuration_installation_receipt"] = configuration.descriptor()
        if selected:
            disposition, prior, current = (ConfigurationInstallationDisposition.CREATED, None,
                sha256(request.effect_id.encode()).hexdigest()) if configuration is None else (
                configuration.disposition, configuration.prior_realization_fingerprint,
                configuration.current_realization_fingerprint)
            evidence["secret_resolution_receipt"] = SecretResolutionReceipt(fingerprint, selection_fingerprint).descriptor()
            evidence["secret_installation_receipt"] = SecretInstallationReceipt(fingerprint, selection_fingerprint,
                disposition, prior, current).descriptor()
        result = replace(result, evidence=evidence)
        secret_installation_receipt_for_result(request, result)
        return result


class PostgresConfigurationTransferProducerReceiverTests(receiver.ReceiverHealthExecutionFixture):
    def application(self):
        return super().application(runtime_registration=RuntimeAdapterRegistration(
            self.runtime, exact_version_execute_with_authority=True))

    def setUp(self):
        super().setUp()
        self.runtime = ProfiledRecordingRuntime(self)

    async def test_genuine_transfer_receiver_finish_is_atomic_replayable_and_within_physical_bounds(self):
        self.health.responses = [NativeConnectionOutcome.CONNECTED]
        await self.prepare_and_start()
        for _ in range(len(self.plan.activities) + 2):
            result = await self.execute_one()
            if result["run_status"] == "succeeded":
                break
            self.assertEqual(result["coordinator_status"], "progressed")
        self.assertEqual(result["run_status"], "succeeded")
        self.assertEqual(len(self.health.signed_reads), 3)
        self.assertEqual(self.tracker.active, 0)
        refs = self.connection.execute("SELECT run_id,activity_id,attempt,artifact_id FROM cpk_effect_configuration_refs "
            "WHERE run_id=%s ORDER BY activity_id,attempt,artifact_id", (self.run_id,)).fetchall()
        self.assertGreater(len(refs), 0)
        with self.unit_of_work() as uow:
            for run, activity, attempt in dict.fromkeys(tuple(row[:3]) for row in refs):
                self.assertIsNotNone(uow.stores.configuration_completions.get(
                    EffectAttemptIdentity(RunId(run), activity, attempt)))
            for target in self.targets.values():
                self.assertIsNone(uow.stores.graphs.receiver_introduction(
                    "workspace-a", target.receiver_id).first_accepted_action_id)

        observed, state = physical.observation(), {}
        preflight, commit, connect = ConfigurationAcceptanceStore._preflight, PostgresUnitOfWork.commit, self.tracker._connect
        def admit(store, prepared):
            self.assertIsNotNone(prepared.receiver_truth)
            self.assertEqual(len(prepared.transfers), len(refs))
            self.assertGreater(len(prepared.receiver_truth[1]), 0)
            prior = prepared.evidence_read.used
            snapshot, future, publication = store._publication_budgets(prepared)
            phase = "pre_id" if prepared.event is None else "bound"
            self.assertNotIn(phase, state)
            state[phase] = (prior, snapshot, future, publication, len(observed["queries"]))
            observed["accounting"] = _ACCOUNTING.get()
            return preflight(store, prepared)
        def committed(uow):
            if observed["accounting"] is not None and _ACCOUNTING.get() is observed["accounting"]:
                state["end"] = observed["accounting"].used
            return commit(uow)
        plan = self.plan_descriptor
        payload = {"plan_id": self.plan_id, "expected_current_graph_id": self.workspace["current_graph_id"],
            "expected_current_realized_projection_id": self.workspace["current_realized_projection_id"],
            "desired_graph_id": plan["desired_graph_id"],
            "desired_realized_projection_id": plan["desired_realized_projection_id"],
            "expected_desired_graph_revision": plan["desired_graph_revision"],
            "claim_generation": self.generation, "idempotency_key": "advance-profiled-receiver"}
        def compound_truth():
            tables = ("cpk_workspaces", "cpk_operation_actions", "cpk_activity_events",
                "cpk_configuration_acceptances", "cpk_configuration_accepted_slots",
                "cpk_configuration_claim_transfers", "cpk_effect_configuration_refs", "cpk_configuration_claims",
                "cpk_graph_receiver_introductions", "cpk_graph_receiver_bindings")
            return tuple((table, self.connection.execute("SELECT row_to_json(t)::text FROM " + table
                + " t ORDER BY row_to_json(t)::text").fetchall()) for table in tables)
        before, finished = compound_truth(), []
        finish = advancement_module._finish_receiver_advancement
        def fail_after_finish(stores, request, run, guard, prepared, action, advanced):
            finish(stores, request, run, guard, prepared, action, advanced)
            self.assertIsNotNone(prepared)
            self.assertEqual(stores.connection.execute("SELECT count(*) FROM cpk_configuration_claim_transfers "
                "WHERE run_id=%s", (self.run_id,)).fetchone(), (len(refs),))
            for target in self.targets.values():
                origin = stores.graphs.receiver_introduction("workspace-a", target.receiver_id)
                self.assertEqual(origin.first_accepted_action_id, action.action_id)
            finished.append(True)
            raise RuntimeError("injected after real receiver finish")
        with mock.patch.object(advancement_module, "_finish_receiver_advancement", fail_after_finish), \
                self.assertRaisesRegex(RuntimeError, "^injected after real receiver finish$"):
            await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
                path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker, payload=payload)
        self.assertEqual(finished, [True])
        self.assertEqual(compound_truth(), before)
        self.assertEqual(self.tracker.active, 0)
        with mock.patch.object(self.tracker, "_connect", side_effect=lambda: physical.PublicationWire(connect(), observed)), \
                mock.patch.object(ConfigurationAcceptanceStore, "_preflight", admit), \
                mock.patch.object(PostgresUnitOfWork, "commit", committed):
            advanced = await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
                path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker, payload=payload)
        self.assertEqual(advanced["to_graph_id"], plan["desired_graph_id"])
        self.assertEqual(set(state), {"pre_id", "bound", "end"})
        for phase in ("pre_id", "bound"):
            prior, _, _, declaration, offset = state[phase]
            used = physical.difference(state["end"], prior)
            print("producer-receiver-accounting", phase, "used", used,
                "settled", declaration.settled, "peak", declaration.peak)
            physical.within(self, used, declaration.settled)
            selected = observed["queries"][offset:]
            for entry in selected:
                physical.within(self, physical.difference(physical.Footprint(*entry["peak"]), prior), declaration.peak)
            physical.reconciles(self, used, {"queries": selected})
        revision = plan["desired_graph_revision"]
        self.assertEqual(self.connection.execute("SELECT run_id,activity_id,attempt,artifact_id,acceptance_revision "
            "FROM cpk_configuration_claim_transfers WHERE run_id=%s ORDER BY activity_id,attempt,artifact_id",
            (self.run_id,)).fetchall(), [(*row, revision) for row in refs])
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(self.connection.execute("SELECT run_id,activity_id,attempt,artifact_id,protective,accepted_revision "
                "FROM " + table + " WHERE run_id=%s ORDER BY activity_id,attempt,artifact_id", (self.run_id,)).fetchall(),
                [(*row, False, revision) for row in refs])
        action, session = self.connection.execute("SELECT a.action_id,a.session_id FROM cpk_configuration_acceptances h "
            "JOIN cpk_operation_actions a ON a.action_id=h.action_id WHERE h.workspace_id='workspace-a' "
            "AND h.pinned_revision=%s", (revision,)).fetchone()
        with self.unit_of_work() as uow:
            for target in self.targets.values():
                origin = uow.stores.graphs.receiver_introduction("workspace-a", target.receiver_id)
                self.assertEqual((origin.first_accepted_action_id, origin.first_accepted_session_id), (action, session))

        cold = physical.observation()
        with mock.patch.object(self.tracker, "_connect", side_effect=lambda: physical.PublicationWire(connect(), cold, cold=True)):
            with self.unit_of_work() as uow:
                current = uow.stores.configuration_acceptance.read_current_configuration("workspace-a")
        self.assertEqual((current.state, current.manifest_slot_count), ("complete", len(refs)))
        for phase in ("pre_id", "bound"):
            future = state[phase][2]
            physical.within(self, cold["accounting"].used, future.settled)
            for entry in cold["queries"]:
                physical.within(self, physical.Footprint(*entry["peak"]), future.peak)
        physical.reconciles(self, cold["accounting"].used, cold)
        self.assertEqual(self.tracker.active, 0)
        committed_truth = compound_truth()
        advance_ids = self.ids["advance"].next
        runtime_calls = tuple(self.runtime.calls)
        replay = await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker, payload=payload)
        self.assertTrue(replay["replayed"])
        self.assertEqual((replay["action_id"], replay["event_id"]), (advanced["action_id"], advanced["event_id"]))
        self.assertEqual(self.ids["advance"].next, advance_ids)
        self.assertEqual(tuple(self.runtime.calls), runtime_calls)
        self.assertEqual(compound_truth(), committed_truth)
