"""Genuine simulated D1/acceptance prefix; recorded B1 reader-defense suffix."""
from hashlib import sha256

from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCompletion, configuration_invocation_correlation_for_request,
    configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.postgres.configuration_evidence import _joined_read
from tests import test_postgres_configuration_acceptance_membership as membership


TRANSFER_TABLE = "cpk_configuration_claim_transfers"


def profiled_configuration_result(request):
    correlated = configuration_invocation_correlation_for_request(request)
    completion = ConfigurationInvocationCompletion(correlated.request_fingerprint,
        configuration_invocation_selection_fingerprint(correlated.selection))
    return RuntimeEffectResult.succeeded(request.effect_id, evidence={
        "adapter": "simulated-total-configuration-invocation",
        "configuration_invocation_completion": completion.descriptor()})


class ConfigurationTransferFixture:
    def setUp(self):
        self.membership = membership.PostgresConfigurationAcceptanceMembershipTests()
        self.membership.configuration_result_for_request = profiled_configuration_result
        self.addCleanup(self.cleanup_membership)
        self.membership.setUp()
        self.base = self.membership.fixture
        self.connection = self.base.connection
        self.original = self.membership.original
        self.refs = self.membership.refs
        with self.base.unit_of_work() as uow:
            self.completion = uow.stores.configuration_completions.get(self.original.identity)
            self.assertIsNotNone(self.completion, "genuine original fold must admit D1 before recording a transfer")
        self.acceptance = self.membership.advance()
        self.revision = self.acceptance.desired_graph_revision

    def cleanup_membership(self):
        self.assertTrue(self.membership.doCleanups(), "nested transfer fixture cleanup failed")

    def require_transfer_schema(self):
        self.assertEqual(self.connection.execute("SELECT to_regclass(%s)", (TRANSFER_TABLE,)).fetchone(),
            (TRANSFER_TABLE,), "missing exact original-claim transfer relation")

    def key(self, ref):
        identity = self.original.identity
        return identity.run_id.value, identity.activity_id, identity.attempt, ref.artifact_id

    def record_transfer(self, refs=None):
        """Below-owner recorded premise only. B1 exposes no transfer writer."""
        self.require_transfer_schema()
        with self.base.unit_of_work() as uow:
            for ref in self.refs if refs is None else refs:
                key = self.key(ref)
                digest = sha256(ConfigurationInstanceRefCodec().encode_canonical_bytes(ref)).hexdigest()
                self.assertEqual(uow.stores.connection.execute(
                    "SELECT source_run_id,source_activity_id,source_attempt,source_artifact_id,full_ref_digest "
                    "FROM cpk_configuration_accepted_slots WHERE "
                    "(workspace_id,pinned_revision,runtime_id,node_id,artifact_id)=(%s,%s,%s,%s,%s)",
                    (ref.workspace_id, self.revision, ref.runtime_id, ref.node_id, ref.artifact_id)).fetchone(),
                    (*key, digest))
                uow.stores.connection.execute(f"INSERT INTO {TRANSFER_TABLE} "
                    "(run_id,activity_id,attempt,artifact_id,workspace_id,allocation_id,runtime_id,node_id,"
                    "ref_digest,request_fingerprint,selection_fingerprint,outcome_fingerprint,acceptance_revision) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (*key, ref.workspace_id, ref.allocation_id, ref.runtime_id, ref.node_id, digest,
                     self.completion.request_fingerprint, self.completion.selection_fingerprint,
                     self.completion.outcome_fingerprint, self.revision))
                for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
                    changed = uow.stores.connection.execute(f"UPDATE {table} SET accepted_revision=%s "
                        "WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s) AND protective",
                        (self.revision, *key))
                    self.assertEqual(changed.rowcount, 1)
            uow.commit()

    def prove_transfer(self, uow, ref=None):
        ref = ref or self.refs[0]
        proof = getattr(uow.stores.configuration_acceptance, "_accepted_transfer", None)
        self.assertTrue(callable(proof), "missing acceptance-owned exact transfer point proof")
        with _joined_read(uow.stores.connection) as read:
            return proof(read, self.key(ref), ref, self.revision)

    def transfer_snapshot(self):
        return tuple(self.connection.execute(f"SELECT * FROM {table} ORDER BY run_id,activity_id,attempt,artifact_id").fetchall()
            for table in (TRANSFER_TABLE, "cpk_effect_configuration_refs", "cpk_configuration_claims"))
