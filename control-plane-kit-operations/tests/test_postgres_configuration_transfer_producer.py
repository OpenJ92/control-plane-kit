"""Real Operations transfer production; the terminal adapter is simulated."""
from hashlib import sha256
import unittest

from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCompletion, configuration_invocation_correlation_for_request,
    configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from tests import test_postgres_configuration_acceptance_membership as membership


class PostgresConfigurationTransferProducerTests(unittest.TestCase):
    def test_fresh_own_success_transfers_exact_accepted_claims(self):
        def simulated_completion(request):
            context = configuration_invocation_correlation_for_request(request)
            completion = ConfigurationInvocationCompletion(context.request_fingerprint,
                configuration_invocation_selection_fingerprint(context.selection))
            return RuntimeEffectResult.succeeded(request.effect_id, evidence={
                "adapter": "simulated-total-configuration-invocation",
                "configuration_invocation_completion": completion.descriptor()})

        member = membership.PostgresConfigurationAcceptanceMembershipTests()
        member.configuration_result_for_request = simulated_completion
        self.addCleanup(lambda: self.assertTrue(member.doCleanups(), "membership fixture cleanup failed"))
        member.setUp()
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
