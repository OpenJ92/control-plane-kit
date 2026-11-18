"""Real Operations transfer production; the terminal adapter is simulated."""
from hashlib import sha256
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCompletion, configuration_invocation_correlation_for_request,
    configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from tests import test_postgres_configuration_acceptance_membership as membership
from tests import test_postgres_configuration_transfer_publication as physical
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead


class PostgresConfigurationTransferProducerTests(unittest.TestCase):
    def configured_member(self, *, node_ids=("api",), profiled_nodes=None):
        def simulated_completion(request):
            context = configuration_invocation_correlation_for_request(request)
            if profiled_nodes is not None and context.operation.target.node_id not in profiled_nodes:
                return RuntimeEffectResult.succeeded(request.effect_id,
                    evidence={"adapter": "simulated-unprofiled-configuration-invocation"})
            completion = ConfigurationInvocationCompletion(context.request_fingerprint,
                configuration_invocation_selection_fingerprint(context.selection))
            return RuntimeEffectResult.succeeded(request.effect_id, evidence={
                "adapter": "simulated-total-configuration-invocation",
                "configuration_invocation_completion": completion.descriptor()})

        member = membership.PostgresConfigurationAcceptanceMembershipTests()
        member.node_ids = node_ids
        member.configuration_result_for_request = simulated_completion
        self.addCleanup(lambda: self.assertTrue(member.doCleanups(), "membership fixture cleanup failed"))
        member.setUp()
        return member

    def test_fresh_own_success_transfers_exact_accepted_claims(self):
        member = self.configured_member()
        original, refs, connection = member.original, member.refs, member.connection
        identity = original.identity
        key = identity.run_id.value, identity.activity_id, identity.attempt
        with member.fixture.unit_of_work() as uow:
            completion = uow.stores.configuration_completions.get(identity)
        self.assertIsNotNone(completion, "the original real fold must admit its own completion")
        self.assertEqual(completion.request_fingerprint, original.request_fingerprint)
        self.assertEqual(completion.original_event_id, original.original_start_event.event_id)
        self.assertEqual(completion.direct_event_id, member.direct_event_id)

        def dispositions(table):
            return connection.execute("SELECT artifact_id,protective,accepted_revision,disposition_kind "
                "FROM " + table + " WHERE (run_id,activity_id,attempt)=(%s,%s,%s) ORDER BY artifact_id",
                key).fetchall()

        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(dispositions(table), [(ref.artifact_id, True, None, "outstanding") for ref in refs])
        self.assertEqual(connection.execute("SELECT count(*) FROM cpk_configuration_claim_transfers "
            "WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", key).fetchone(), (0,))

        accepted = member.advance()
        self.assertFalse(accepted.replayed)
        revision = accepted.desired_graph_revision
        self.assertEqual(connection.execute("SELECT run_id,request_id,plan_id,action_id,event_id,slot_count "
            "FROM cpk_configuration_acceptances WHERE workspace_id=%s AND pinned_revision=%s",
            (original.intent.source.workspace_id, revision)).fetchone(),
            (key[0], original.intent.source.request_id, original.intent.source.plan_id,
             accepted.action.action_id, accepted.event.event_id, len(refs)))
        slots = connection.execute("SELECT runtime_id,node_id,artifact_id,source_run_id,source_activity_id,"
            "source_attempt,source_artifact_id,birth_run_id,birth_activity_id,birth_attempt,birth_artifact_id,"
            "full_ref_digest FROM cpk_configuration_accepted_slots WHERE workspace_id=%s AND pinned_revision=%s "
            "ORDER BY runtime_id,node_id,artifact_id", (original.intent.source.workspace_id, revision)).fetchall()
        self.assertEqual(tuple(slots), member.expected_slots)

        transfers = connection.execute("SELECT run_id,activity_id,attempt,artifact_id,workspace_id,allocation_id,"
            "runtime_id,node_id,ref_digest,request_fingerprint,selection_fingerprint,outcome_fingerprint,"
            "acceptance_revision FROM cpk_configuration_claim_transfers "
            "WHERE (run_id,activity_id,attempt)=(%s,%s,%s) ORDER BY artifact_id", key).fetchall()
        expected = [(*key, ref.artifact_id, ref.workspace_id, ref.allocation_id, ref.runtime_id, ref.node_id,
            sha256(ConfigurationInstanceRefCodec().encode_canonical_bytes(ref)).hexdigest(),
            completion.request_fingerprint, completion.selection_fingerprint,
            completion.outcome_fingerprint, revision) for ref in refs]
        self.assertEqual(transfers, expected,
            "fresh own successful acceptance must produce every exact eligible transfer")
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(dispositions(table),
                [(ref.artifact_id, False, revision, "accepted-current") for ref in refs])
        with member.fixture.unit_of_work() as uow:
            current = uow.stores.configuration_acceptance.read_current_configuration(
                original.intent.source.workspace_id, node_id=refs[0].node_id)
        self.assertEqual(current.state, "complete")
        self.assertEqual(tuple(binding.ref for binding in current.bindings), refs)
        self.assertTrue(all(binding.source.identity == identity for binding in current.bindings))

    def test_writer_suffix_and_fresh_consumer_reconcile_physical_accounting(self):
        member = self.configured_member()
        observed, state = physical.observation(), {}
        preflight, commit = ConfigurationAcceptanceStore._preflight, PostgresUnitOfWork.commit

        def admit(store, prepared):
            before = prepared.evidence_read.used
            with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError("forecast issued SQL")):
                snapshot, future, publication = store._publication_budgets(prepared)
            self.assertEqual(prepared.evidence_read.used, before)
            result = preflight(store, prepared)
            phase = "pre_id" if prepared.event is None else "bound"
            self.assertNotIn(phase, state)
            state[phase] = (before, snapshot, future, publication, len(observed["queries"]))
            observed["accounting"] = _ACCOUNTING.get()
            self.assertEqual(len(prepared.transfers), 2)
            invocations = [entry for role, entry in prepared.read_bounds.collections if role == "invocation-refs"]
            self.assertEqual(len(invocations), 1)
            self.assertEqual(invocations[0].keys, tuple((ref.artifact_id,) for ref in member.refs))
            self.assertGreaterEqual(invocations[0].widths[16], len("true"))
            return result

        def committed(uow):
            if _ACCOUNTING.get() is observed["accounting"]:
                state["end"] = observed["accounting"].used
            return commit(uow)

        factory = lambda: PostgresUnitOfWork(lambda: physical.PublicationWire(
            psycopg.connect(member.fixture.database_url), observed))
        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", admit), \
                mock.patch.object(PostgresUnitOfWork, "commit", committed):
            accepted = member.advance(factory)
        self.assertFalse(accepted.replayed)
        self.assertEqual(set(state), {"pre_id", "bound", "end"})
        for phase in ("pre_id", "bound"):
            prior, _, _, declaration, offset = state[phase]
            used = physical.difference(state["end"], prior)
            print("producer-accounting", phase, "used", used, "settled", declaration.settled,
                "peak", declaration.peak)
            physical.within(self, used, declaration.settled)
            selected = observed["queries"][offset:]
            for entry in selected:
                physical.within(self, physical.difference(physical.Footprint(*entry["peak"]), prior), declaration.peak)
            physical.reconciles(self, used, {"queries": selected})
        self.assertEqual(sum(entry["sql"].startswith("INSERT INTO cpk_configuration_claim_transfers")
            for entry in observed["queries"]), 2)
        self.assertTrue(any("FROM (VALUES " in entry["sql"] for entry in observed["queries"]),
            "actual generated width probes must remain in the pre-ID suffix")

        cold, snapshots = physical.observation(), []
        manifest = ConfigurationAcceptanceStore._receipt_manifest
        def read_manifest(store, workspace, revision, read):
            before = read.used
            result = manifest(store, workspace, revision, read)
            snapshots.append(physical.difference(read.used, before))
            return result
        with mock.patch.object(ConfigurationAcceptanceStore, "_receipt_manifest", read_manifest):
            with PostgresUnitOfWork(lambda: physical.PublicationWire(
                    psycopg.connect(member.fixture.database_url), cold, cold=True)) as uow:
                result = uow.stores.configuration_acceptance.read_current_configuration("workspace-a")
        self.assertEqual(result.state, "complete")
        self.assertEqual(tuple(binding.ref for binding in result.bindings), member.refs)
        self.assertEqual(len(snapshots), 1)
        print("producer-accounting cold", cold["accounting"].used, "snapshot", snapshots[0])
        for phase in ("pre_id", "bound"):
            _, snapshot, future, _, _ = state[phase]
            physical.within(self, snapshots[0], snapshot)
            physical.within(self, cold["accounting"].used, future.settled)
            for entry in cold["queries"]:
                physical.within(self, physical.Footprint(*entry["peak"]), future.peak)
        physical.reconciles(self, cold["accounting"].used, cold)
