"""Assumed completion inputs for advancement; no upstream health/provider proof.

This composer uses existing typed records/folds in the canonical plan's own
order. It does not implement scheduling, authorization, health preparation,
dispatch, acceptance, or retirement. Its records are synthetic premises for
the advancement/C1 consumer contracts, per #1904 comment 5904298267.
"""

from control_plane_kit_core.operations import (
    ActivityEventKind, EffectAttemptFence, EffectAttemptIdentity,
    EffectAttemptTransition, EffectAttemptTransitionKind, fold_effect_attempt,
)
from control_plane_kit_core.planning import (
    AddSocketConnection, AllocatePublicIngress, RemovePublicIngress,
    RemoveSocketConnection, SwitchSocketConnection,
)
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.effect_attempt_intent_evidence import EffectAttemptIntentRecord
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptEventEvidence, EffectAttemptRecord, effect_attempt_state_fingerprint,
)
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord, ExecutionEffectOutcome, NativeConnectionEffectOutcome,
    NativeConnectionObservation, NativeConnectionOutcome, effect_outcome_transition,
)
from control_plane_kit_operations.postgres import effect_attempt_store, effect_attempt_intent_store
from control_plane_kit_operations.records import ActivityEventRecord, BoundedEvidence
from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
from control_plane_kit_operations.runtime_management_targets import is_native_connection_operation


LEGACY_EVENT_ONLY = (AddSocketConnection, SwitchSocketConnection, RemoveSocketConnection,
                     AllocatePublicIngress, RemovePublicIngress)


def retain_completion_inputs(case, context, *, activities=None):
    """Retain only the selected prefix/all of the plan, never target current truth."""
    selected = context.plan.activities if activities is None else activities
    with case.unit_of_work() as uow:
        stores = uow.stores
        ordinal = stores.execution.next_event_ordinal(context.run.run_id)
        for activity in selected:
            started_at = case.now()
            event_id = context.run.run_id + "-premise-" + str(ordinal)
            if type(activity.operation) in LEGACY_EVENT_ONLY:
                start = ActivityEventRecord(event_id, context.run.run_id, ordinal,
                    ActivityEventKind.STEP_STARTED, started_at, activity_id=activity.activity_id.value,
                    evidence=BoundedEvidence.from_mapping({"fixture_premise": "assumed-completion"}))
                end = ActivityEventRecord(event_id + "-success", context.run.run_id, ordinal + 1,
                    ActivityEventKind.STEP_SUCCEEDED, case.now(), activity_id=activity.activity_id.value,
                    evidence=BoundedEvidence.from_mapping({"fixture_premise": "assumed-completion"}))
                stores.execution.add_event(start)
                stores.execution.add_event(end)
            else:
                # This is the same pure pinned-material translator used by the
                # coordinator, including Observe* intent construction. It does
                # not establish signed-health preparation or authority.
                intent = _runtime_effect_intent_for_context(context, activity)
                identity = EffectAttemptIdentity(intent.source.run_id, activity.activity_id.value, 1)
                fingerprint = runtime_effect_intent_fingerprint(intent)
                fence = EffectAttemptFence(context.fence.worker_id, context.fence.generation)
                state = fold_effect_attempt(None, EffectAttemptTransition(
                    EffectAttemptTransitionKind.STARTED, identity, request_fingerprint=fingerprint), fence=fence)
                start = state_event(state, event_id, ordinal, ActivityEventKind.STEP_STARTED, started_at)
                intent_record = EffectAttemptIntentRecord(identity, start, intent)
                ended_at = case.now()
                if is_native_connection_operation(activity.operation):
                    outcome = NativeConnectionEffectOutcome.from_observation(identity=identity,
                        request_fingerprint=fingerprint, accepted_at=ended_at,
                        observation=NativeConnectionObservation(NativeConnectionOutcome.CONNECTED,
                            event_id, activity.activity_id.value, container_id="a" * 64,
                            sample_start=started_at, sample_end=ended_at, ready_connections=1,
                            connector_id="11111111-2222-3333-4444-555555555555"))
                else:
                    outcome = ExecutionEffectOutcome(identity, fingerprint,
                        RuntimeEffectResult.succeeded(event_id,
                            evidence={"fixture_premise": "assumed-completion"}))
                terminal = fold_effect_attempt(state, effect_outcome_transition(outcome), fence=fence)
                end = state_event(terminal, event_id + "-success", ordinal + 1,
                    ActivityEventKind.STEP_SUCCEEDED, ended_at)
                attempt = EffectAttemptRecord(terminal, start, end)
                stores.execution.add_event(start)
                stores.execution.add_event(end)
                # Explicit retained-history premises use the existing owner
                # codecs. They do not call the fresh public writers being
                # closed by C3 or mint an admission/acceptance bypass.
                _, preimage = effect_attempt_intent_store._require_record(intent_record)
                names = effect_attempt_intent_store._COLUMN_NAMES
                values = (identity.run_id.value, identity.activity_id, identity.attempt,
                    intent_record.workspace_id, intent_record.request_id, intent_record.request_fingerprint,
                    start.event_id, start.run_id, start.ordinal, preimage)
                stores.connection.execute("INSERT INTO cpk_effect_attempt_intents (" + ",".join(names)
                    + ") VALUES (" + ",".join("%s" for _ in names) + ")", values)
                names = effect_attempt_store._COLUMN_NAMES
                stores.connection.execute("INSERT INTO cpk_effect_attempts (" + ",".join(names)
                    + ") VALUES (" + ",".join("%s" for _ in names) + ")",
                    effect_attempt_store._record_values(attempt))
                record = EffectAttemptOutcomeRecord(context.request.identity.workspace_id, outcome, attempt, ())
                stores.effect_outcomes.insert(record)
                case.assertEqual(stores.effect_attempts.get(identity), attempt)
                case.assertEqual(stores.effect_attempt_intents.get(identity), intent_record)
                case.assertEqual(stores.effect_outcomes.get(identity, end.event_id), record)
            ordinal += 2
        uow.commit()


def state_event(state, event_id, ordinal, kind, occurred_at):
    return ActivityEventRecord(event_id, state.identity.run_id.value, ordinal, kind, occurred_at,
        activity_id=state.identity.activity_id, evidence=BoundedEvidence.from_mapping({
            "effect_attempt": EffectAttemptEventEvidence(state.identity.attempt,
                effect_attempt_state_fingerprint(state)).descriptor()}))
