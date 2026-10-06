"""Genuine simulated D1/acceptance prefix; recorded B1 reader-defense suffix."""
from dataclasses import replace
from hashlib import sha256

from psycopg.types.json import Jsonb

from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCompletion, configuration_invocation_correlation_for_request,
    configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.operations import ActivityEventKind, EffectAttemptStatus, fold_effect_attempt
from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
from control_plane_kit_operations.effect_attempt_fold_interpreter import _event_kind
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptEventEvidence, EffectAttemptRecord, effect_attempt_state_fingerprint,
)
from control_plane_kit_operations.effect_outcome_evidence import (
    ExecutionEffectOutcome, effect_outcome_failure, effect_outcome_transition,
)
from control_plane_kit_operations.postgres.configuration_evidence import _joined_read
from control_plane_kit_operations.postgres.effect_outcome_store import _encode_preimage
from control_plane_kit_operations.records import BoundedEvidence
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
        self.membership.node_ids = getattr(self, "transfer_node_ids", ("api",))
        self.membership.configuration_result_for_request = profiled_configuration_result
        self.addCleanup(self.cleanup_membership)
        self.membership.setUp()
        self.base = self.membership.fixture
        self.connection = self.base.connection
        self.original = self.membership.original
        self.refs = self.original.intent.configuration_instances.instances
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

    def proof_snapshot(self):
        return self.base.retained_snapshot(), self.transfer_snapshot(), tuple(
            self.connection.execute(f"SELECT * FROM {table} ORDER BY run_id,activity_id,attempt").fetchall()
            for table in ("cpk_effect_attempt_outcomes", "cpk_effect_attempts", "cpk_configuration_invocation_completions"))

    def corrupt_same_terminal(self, uow, *, failed):
        """Impossible retained history, only in a caller's rollback-only UoW.

        Preserve source/selection/receipt; change one correlated direct terminal.
        This does not simulate a lawful failed or unprofiled transfer writer.
        """
        original = uow.stores.effect_outcomes.get(self.original.identity, self.membership.direct_event_id)
        self.assertIs(type(original.outcome), ExecutionEffectOutcome)
        self.assertIs(original.attempt.state.status, EffectAttemptStatus.SUCCEEDED)
        self.assertIs(original.attempt.original_start_event.kind, ActivityEventKind.STEP_STARTED)
        self.assertIsNone(original.attempt.state.recovery_decision)
        self.assertEqual(original.endpoint_observations, ())
        self.assertEqual(original.outcome.result.observations, ())
        evidence = dict(original.outcome.result.evidence)
        self.assertIn("configuration_invocation_completion", evidence)
        if failed:
            result = replace(RuntimeEffectResult.failed(original.outcome.effect_id,
                RuntimeEffectFailure("configuration.recorded-failure", "Recorded history corruption.")), evidence=evidence)
        else:
            del evidence["configuration_invocation_completion"]
            result = RuntimeEffectResult.succeeded(original.outcome.effect_id, evidence=evidence)
        outcome = ExecutionEffectOutcome(self.original.identity, original.outcome.request_fingerprint, result)
        started = replace(original.attempt.state, status=EffectAttemptStatus.STARTED, outcome_fingerprint=None)
        state = fold_effect_attempt(started, effect_outcome_transition(outcome), fence=started.fence)
        event = replace(original.attempt.latest_transition_event, kind=_event_kind(original.attempt, state),
            evidence=BoundedEvidence.from_mapping({"effect_attempt": EffectAttemptEventEvidence(
                state.identity.attempt, effect_attempt_state_fingerprint(state)).descriptor()}),
            failure=effect_outcome_failure(outcome))
        attempt = EffectAttemptRecord(state, original.attempt.original_start_event, event)
        record = replace(original, outcome=outcome, attempt=attempt)
        connection = uow.stores.connection
        connection.execute("ALTER TABLE cpk_configuration_invocation_completions "
            "DROP CONSTRAINT cpk_configuration_completions_outcome_fk")
        connection.execute(f"ALTER TABLE {TRANSFER_TABLE} DROP CONSTRAINT cpk_claim_transfers_completion_fk")
        failure = event.failure
        payload = {"activity_id": event.activity_id, "evidence": event.evidence.descriptor(),
            "failure": None if failure is None else {"category": failure.category.value, "code": failure.code,
                "message": failure.message, "details": failure.details.descriptor()}, "recovery": None}
        connection.execute("UPDATE cpk_activity_events SET event_type=%s,payload=%s WHERE event_id=%s",
            (event.kind.value, Jsonb(payload), event.event_id))
        key = self.key(self.refs[0])[:3]
        connection.execute("UPDATE cpk_effect_attempt_outcomes SET preimage=%s,status=%s,outcome_fingerprint=%s "
            "WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", (_encode_preimage(record), state.status.value,
             state.outcome_fingerprint, *key))
        connection.execute("UPDATE cpk_effect_attempts SET status=%s,outcome_fingerprint=%s "
            "WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", (state.status.value, state.outcome_fingerprint, *key))
        if failed:
            connection.execute("UPDATE cpk_configuration_invocation_completions SET outcome_fingerprint=%s "
                "WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", (state.outcome_fingerprint, *key))
        else:
            connection.execute("DELETE FROM cpk_configuration_invocation_completions "
                "WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", key)
        connection.execute(f"UPDATE {TRANSFER_TABLE} SET outcome_fingerprint=%s "
            "WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", (state.outcome_fingerprint, *key))
        return record


class ConfigurationTransferredConsumerFixture(ConfigurationTransferFixture):
    """Existing real reuse/carry owners with a mixed recorded transfer prefix."""
    def setUp(self):
        from tests import test_postgres_configuration_reuse as reuse
        self.reuse = reuse.PostgresConfigurationReuseTests()
        self.reuse.configuration_result_for_request = profiled_configuration_result
        self.addCleanup(self.cleanup_reuse)
        self.reuse.setUp()
        self.carry, self.membership, self.base = self.reuse.carry, self.reuse.fixture, self.reuse.base
        self.connection, self.original = self.base.connection, self.membership.original
        self.refs = self.original.intent.configuration_instances.instances
        with self.base.unit_of_work() as uow:
            self.completion = uow.stores.configuration_completions.get(self.original.identity)
            self.assertIsNotNone(self.completion)
        self.acceptance = self.carry.original_acceptance
        self.revision = self.acceptance.desired_graph_revision
        self.record_transfer()
        self.membership.claims = self.membership.protective_claims()
        proposed = [row for row in self.reuse.expected_claims if row[0] == self.reuse.identity.run_id.value]
        self.reuse.expected_claims = sorted(self.membership.claims + proposed)
        self.assert_zero_active_api()
        with self.base.unit_of_work() as uow:
            for ref in self.refs:
                self.prove_transfer(uow, ref)

    def cleanup_reuse(self):
        self.assertTrue(self.reuse.doCleanups(), "nested transferred consumer cleanup failed")

    def assert_zero_active_api(self):
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {table} "
                "WHERE node_id='api' AND protective").fetchone(), (0,))
            self.assertEqual(self.connection.execute(f"SELECT accepted_revision,disposition_kind FROM {table} "
                "WHERE node_id='api' ORDER BY artifact_id").fetchall(),
                [(self.revision, "accepted-current")] * len(self.refs))
            self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {table} "
                "WHERE node_id='worker' AND protective AND accepted_revision IS NULL "
                "AND disposition_kind='outstanding'").fetchone(), (2,))

    def assert_original_rows_preserved(self, before):
        after = self.transfer_snapshot()
        self.assertEqual(after[0], before[0], "ordinary owner must not produce or rewrite transfers")
        for old_rows, new_rows in zip(before[1:], after[1:], strict=True):
            by_key = {row[:4]: row for row in new_rows}
            for row in old_rows:
                self.assertEqual(by_key[row[:4]], row)

    def assert_missing_birth_admission_refuses_reads(self):
        before = self.proof_snapshot()
        with self.base.unit_of_work() as uow:
            store = uow.stores.configuration_acceptance
            self.assertEqual(store.read_current_configuration("workspace-a", node_id="api").state, "complete")
            uow.stores.connection.execute(f"ALTER TABLE {TRANSFER_TABLE} DROP CONSTRAINT cpk_claim_transfers_completion_fk")
            uow.stores.connection.execute("DELETE FROM cpk_configuration_invocation_completions "
                "WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", self.key(self.refs[0])[:3])
            current = store.read_current_configuration("workspace-a", node_id="api")
            inverse = store.read_configuration_use("workspace-a", self.refs)
            self.assertEqual((current.state, current.bindings), ("unavailable", ()))
            self.assertEqual((inverse.state, inverse.bindings), ("unavailable", ()))
        self.assertEqual(self.proof_snapshot(), before)
