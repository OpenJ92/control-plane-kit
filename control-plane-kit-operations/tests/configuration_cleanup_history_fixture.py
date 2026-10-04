"""Deliberate retained cleanup history, never a supported cleanup writer.

Ordinary source/D1 and proposal/approval come from real existing owners. Only
cleanup execution history and its relational evidence are recorded here. Core
constructs all state/result values; no admission, dispatch or provider is faked.
"""
from dataclasses import replace
from hashlib import sha256

import rfc8785

from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceRefCodec, ConfigurationCleanupOutcome,
    ConfigurationCleanupOutcomeSet, ConfigurationCleanupStatus, ConfigurationCleanupReason,
)
from control_plane_kit_core.operations import (
    ActivityEventKind, EffectAttemptFence, EffectAttemptIdentity, EffectAttemptState,
    EffectAttemptStatus, RunId, fold_effect_attempt,
)
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntent, RuntimeEffectIntentSource, runtime_effect_intent_fingerprint,
    runtime_effect_request_for_intent,
    RuntimeEffectObservedIndeterminate, RuntimeEffectObservationEvidence, RuntimeEffectObservationFailure,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectKind, configuration_cleanup_result
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.approvals import ApprovalCommandService, RequestApproval, DecideApproval
from control_plane_kit_operations.configuration_cleanup import configuration_cleanup_proposal_fingerprint
from control_plane_kit_operations.effect_attempt_intent_evidence import EffectAttemptIntentRecord
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord, effect_attempt_state_fingerprint
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord, ExecutionEffectOutcome, ObservedEffectOutcome,
    effect_outcome_transition, effect_outcome_failure,
)
from control_plane_kit_operations.postgres.effect_attempt_store import _COLUMN_NAMES, _record_values
from control_plane_kit_operations.postgres import effect_outcome_store
from control_plane_kit_operations.records import (
    ActivityEventRecord, ActivityRunRecord, AdmittedRun, ApprovalDecisionKind,
    BoundedEvidence, RetryIdentity,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.configuration_cleanup_postgres_fixture import ConfigurationCleanupPostgresFixture, NOW
from tests.receiver_scope_history_fixture import insert_recorded_request


TABLES = (
    "cpk_configuration_cleanup_reservations", "cpk_configuration_cleanup_members",
    "cpk_configuration_invocation_closures", "cpk_configuration_claim_closures",
    "cpk_configuration_cleanup_member_outcomes",
)


def key(identity):
    return identity.run_id.value, identity.activity_id, identity.attempt


def insert(connection, table, columns, values):
    connection.execute("INSERT INTO " + table + " (" + ",".join(columns)
        + ") VALUES (" + ",".join("%s" for _ in columns) + ")", values)


def event_for(state, *, event_id, ordinal, kind, failure=None):
    return ActivityEventRecord(event_id, state.identity.run_id.value, ordinal, kind, NOW,
        activity_id=state.identity.activity_id, failure=failure,
        evidence=BoundedEvidence.from_mapping({"effect_attempt": {
            "attempt": state.identity.attempt, "state_fingerprint": effect_attempt_state_fingerprint(state)}}))


class ConfigurationCleanupHistoryFixture(ConfigurationCleanupPostgresFixture):
    runtime_authority_ref = RuntimeAuthorityReference("recorded-cleanup-docker")

    def ownership(self, stores):
        owner = getattr(stores, "configuration_cleanup_ownership", None)
        self.assertIsNotNone(owner, "missing #1935 retained-cleanup read owner")
        self.assertTrue(callable(getattr(owner, "get", None)), "missing #1935 retained reservation reader")
        return owner

    def prepare_recorded_cleanup(self, *, label=None):
        # Capability assertion comes before future-schema SQL, not in setUp.
        with self.unit_of_work() as uow:
            self.ownership(uow.stores)
        suffix = "" if label is None else "-" + label
        self.retained_suffix = suffix
        plan = self.publish(self.request(key="publish-cleanup" + suffix)).plan_record
        approvals = ApprovalCommandService(self.unit_of_work, clock=lambda: NOW, id_factory=self.sample_id)
        requested = approvals.execute(RequestApproval(plan.session_id, plan.plan_id, "requester",
            (PolicyScope.PLAN_REQUEST,), IdempotencyKey("retained-ask" + suffix))).request
        decision = approvals.execute(DecideApproval(plan.session_id, requested.request_id, "approver",
            (PolicyScope.PLAN_APPROVE_DESTRUCTIVE,), ApprovalDecisionKind.APPROVED,
            IdempotencyKey("retained-decide" + suffix))).decision
        activity = plan.plan.activities[0]
        identity = EffectAttemptIdentity(RunId("recorded-cleanup-run" + suffix), activity.activity_id.value, 1)
        intent = RuntimeEffectIntent(RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1, RuntimeKind.DOCKER,
            RuntimeEffectIntentSource("workspace-a", "recorded-cleanup-request" + suffix, identity.run_id,
                plan.plan_id, plan.base_graph_id, plan.desired_graph_id), activity.activity_id,
            activity.operation, self.runtime_authority_ref, (), ())
        state = EffectAttemptState(identity, runtime_effect_intent_fingerprint(intent),
            EffectAttemptFence("recorded-worker", 1), EffectAttemptStatus.STARTED)
        event = event_for(state, event_id="recorded-cleanup-start" + suffix, ordinal=1, kind=ActivityEventKind.STEP_STARTED)
        attempt = EffectAttemptRecord(state, event, event)
        self.retained_plan, self.retained_approval, self.retained_decision = plan, requested, decision
        self.retained_attempt = attempt
        self.retained_intent = EffectAttemptIntentRecord(identity, event, intent)
        with self.unit_of_work() as uow:
            self.retained_completion = uow.stores.configuration_completions.get(self.original.identity)
            identities = {EffectAttemptIdentity(RunId(value["run_id"]), value["activity_id"], value["attempt"])
                for candidate in plan.cleanup_proposal.descriptor()["candidates"]
                for value in candidate["protecting_uses"]}
            self.retained_completions = tuple(uow.stores.configuration_completions.get(value)
                for value in sorted(identities, key=key))
            self.retained_claims = tuple((value, ref) for value in sorted(identities, key=key)
                for ref in uow.stores.effect_attempt_intents.get(value).intent.configuration_instances.instances)
        self.assertIsNotNone(self.retained_completion, "ordinary source must have actual D1 admission")
        self.assertTrue(all(value is not None for value in self.retained_completions))
        self.assertEqual(self.base.runtime_registration.authority_ref, self.runtime_authority_ref)
        return identity

    def insert_recorded_cleanup(self, stores):
        """Record fixture history in caller's transaction; no issued authority."""
        connection = stores.connection
        plan, original = self.retained_plan, self.retained_intent
        identity, attempt = original.identity, self.retained_attempt
        insert_recorded_request(connection, request_id=original.intent.source.request_id,
            workspace_id="workspace-a", session_id=plan.session_id, plan_id=plan.plan_id,
            approval_request_id=self.retained_approval.request_id,
            approval_decision_id=self.retained_decision.decision_id, idempotency_key=original.intent.source.request_id,
            intent_fingerprint="recorded-history-only", requested_at=NOW, status="claimed",
            claim_worker_id="recorded-worker", claim_generation=1, claimed_at=NOW,
            lease_expires_at="2026-10-03T13:00:00Z")
        stores.execution._add_run(ActivityRunRecord(identity.run_id.value, plan.plan_id,
            AdmittedRun(original.intent.source.request_id), RetryIdentity(1), ActivityRunStatus.RUNNING,
            NOW, started_at=NOW))
        stores.execution.add_event(attempt.original_start_event)
        event = attempt.original_start_event
        insert(connection, "cpk_effect_attempt_intents",
            ("run_id", "activity_id", "attempt", "workspace_id", "request_id", "request_fingerprint",
             "original_event_id", "original_event_run_id", "original_event_ordinal", "preimage"),
            (*key(identity), "workspace-a", original.intent.source.request_id, original.request_fingerprint,
             event.event_id, event.run_id, event.ordinal, rfc8785.dumps(original.intent.descriptor())))
        insert(connection, "cpk_effect_attempts", _COLUMN_NAMES, _record_values(attempt))
        completion = self.retained_completion
        insert(connection, TABLES[0],
            ("cleanup_run_id", "cleanup_activity_id", "cleanup_attempt", "workspace_id", "request_id",
             "request_fingerprint", "original_event_id", "plan_id", "approval_request_id", "approval_decision_id",
             "proposal_fingerprint", "runtime_id", "runtime_kind", "authority_ref", "registration_id",
             "candidate_count", "invocation_count", "claim_count"),
            (*key(identity), "workspace-a", original.intent.source.request_id, original.request_fingerprint,
             event.event_id, plan.plan_id, self.retained_approval.request_id, self.retained_decision.decision_id,
             configuration_cleanup_proposal_fingerprint(plan.cleanup_proposal), self.refs[0].runtime_id,
             "docker", self.runtime_authority_ref.reference_id, self.base.runtime_registration.registration_id,
             len(self.refs), len(self.retained_completions), len(self.retained_claims)))
        for ref in self.refs:
            insert(connection, TABLES[1],
                ("cleanup_run_id", "cleanup_activity_id", "cleanup_attempt", "workspace_id", "allocation_id",
                 "birth_run_id", "birth_activity_id", "birth_attempt", "birth_artifact_id", "full_ref_digest"),
                (*key(identity), ref.workspace_id, ref.allocation_id, *key(self.original.identity), ref.artifact_id,
                 sha256(ConfigurationInstanceRefCodec().encode_canonical_bytes(ref)).hexdigest()))
        for completion in self.retained_completions:
            insert(connection, TABLES[2],
                ("run_id", "activity_id", "attempt", "cleanup_run_id", "cleanup_activity_id", "cleanup_attempt",
                 "workspace_id", "request_fingerprint", "selection_fingerprint", "outcome_fingerprint"),
                (*key(completion.identity), *key(identity), completion.workspace_id, completion.request_fingerprint,
                 completion.selection_fingerprint, completion.outcome_fingerprint))
        for source_identity, ref in self.retained_claims:
            insert(connection, TABLES[3],
                ("run_id", "activity_id", "attempt", "artifact_id", "cleanup_run_id", "cleanup_activity_id",
                 "cleanup_attempt", "workspace_id", "allocation_id"),
                (*key(source_identity), ref.artifact_id, *key(identity), ref.workspace_id, ref.allocation_id))
            for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
                rows = connection.execute("UPDATE " + table + " SET cleanup_run_id=%s,cleanup_activity_id=%s,"
                    "cleanup_attempt=%s WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s) RETURNING 1",
                    (*key(identity), *key(source_identity), ref.artifact_id)).fetchall()
                self.assertEqual(rows, [(1,)])

    def retain_cleanup(self, *, label=None):
        identity = self.prepare_recorded_cleanup(label=label)
        with self.unit_of_work() as uow:
            self.insert_recorded_cleanup(uow.stores)
            uow.commit()
        return identity

    def retain_cleanup_result(self, outcomes):
        original, current = self.retained_intent, self.retained_attempt
        request = runtime_effect_request_for_intent(original.intent,
            effect_id=original.original_start_event.event_id)
        result = configuration_cleanup_result(request, outcomes)
        outcome = ExecutionEffectOutcome(original.identity, original.request_fingerprint, result)
        return self._retain_outcome(outcome, outcomes.outcomes)

    def retain_generic_observation(self):
        original = self.retained_intent
        observation = RuntimeEffectObservedIndeterminate(original.original_start_event.event_id,
            original.request_fingerprint, RuntimeEffectObservationEvidence({"fixture": "recorded-observation"}),
            RuntimeEffectObservationFailure("fixture.indeterminate", "Recorded indeterminate history."))
        return self._retain_outcome(ObservedEffectOutcome(original.identity, observation), ())

    def _retain_outcome(self, outcome, members):
        original, current = self.retained_intent, self.retained_attempt
        state = fold_effect_attempt(current.state, effect_outcome_transition(outcome), fence=current.state.fence)
        event = event_for(state, event_id="recorded-cleanup-terminal" + self.retained_suffix, ordinal=2,
            kind={EffectAttemptStatus.SUCCEEDED: ActivityEventKind.STEP_SUCCEEDED,
                  EffectAttemptStatus.FAILED: ActivityEventKind.STEP_FAILED,
                  EffectAttemptStatus.UNCERTAIN: ActivityEventKind.STEP_UNCERTAIN}[state.status],
            failure=effect_outcome_failure(outcome))
        attempt = EffectAttemptRecord(state, current.original_start_event, event)
        record = EffectAttemptOutcomeRecord("workspace-a", outcome, attempt, ())
        with self.unit_of_work() as uow:
            connection = uow.stores.connection
            uow.stores.execution.add_event(event)
            connection.execute("UPDATE cpk_effect_attempts SET "
                + ",".join(column + "=%s" for column in _COLUMN_NAMES)
                + " WHERE (run_id,activity_id,attempt)=(%s,%s,%s)", (*_record_values(attempt), *key(original.identity)))
            insert(connection, "cpk_effect_attempt_outcomes", effect_outcome_store._COLUMN_NAMES,
                effect_outcome_store._record_values(record, original.intent.source.request_id))
            for row in members:
                insert(connection, TABLES[4],
                    ("cleanup_run_id", "cleanup_activity_id", "cleanup_attempt", "workspace_id", "allocation_id",
                     "request_fingerprint", "outcome_fingerprint", "status", "reason"),
                    (*key(original.identity), row.ref.workspace_id, row.ref.allocation_id,
                     original.request_fingerprint, state.outcome_fingerprint, row.status.value,
                     None if row.reason is None else row.reason.value))
            uow.commit()
        return record

    def cleanup_snapshot(self):
        with self.unit_of_work() as uow:
            self.ownership(uow.stores)
        return self.truth(), tuple((table, tuple(self.connection.execute(
            "SELECT row_to_json(t)::text FROM " + table + " t ORDER BY row_to_json(t)::text").fetchall()))
            for table in TABLES + ("cpk_execution_requests", "cpk_activity_runs", "cpk_execution_receiver_scopes"))

    def read_retained(self):
        with self.unit_of_work() as uow:
            return self.ownership(uow.stores).get(self.retained_intent.identity)
