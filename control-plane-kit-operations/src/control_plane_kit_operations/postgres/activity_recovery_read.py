"""Read-only, bounded joins of original effect owners and current run pins."""
from datetime import datetime

from control_plane_kit_core.configuration_invocation import configuration_invocation_selection_fingerprint
from control_plane_kit_core.operations import EffectAttemptStatus
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_operations.read_services.activity_recovery import (
    ActivityRecoveryError, ActivityRecoveryProposal, _bytes, _REQUIRED_BLOCKERS,
)
from .configuration_evidence import _Capacity, _Unavailable, _composed_read
from .temporal import decode_postgres_timestamp


class ActivityRecoverySnapshot:
    """No mutation capability, provider client or secret resolver is exposed."""
    def __init__(self, stores):
        self._stores = stores

    def inspect(self, query):
        category = "unavailable"
        try:
            with _composed_read(self._stores.connection) as read:
                return _inspect(self._stores, read, query)
        except _Capacity:
            category = "capacity"
        except KeyError:
            category = "missing"
        except ActivityRecoveryError as error:
            category = error.category
        except (ValueError, TypeError, AttributeError, OverflowError):
            pass
        raise ActivityRecoveryError(category)


def _require(condition):
    if not condition:
        raise _Unavailable


def _inspect(stores, read, query):
    run = stores.execution.get_run(query.identity.run_id.value)
    request = stores.execution.get_request(run.admission.request_id)
    if request.identity.workspace_id != query.workspace_id:
        raise ActivityRecoveryError("missing")
    workspace = stores.workspaces.get(query.workspace_id)
    session = stores.activity_history.get_session(request.identity.session_id)
    plan = stores.activity_history.get_plan(request.identity.plan_id)
    _require(session.workspace_id == query.workspace_id and plan.session_id == session.session_id
             and run.plan_id == plan.plan_id)
    attempt = stores.effect_attempts.get(query.identity)
    if (attempt.state.status is not EffectAttemptStatus.UNCERTAIN
            or attempt.state.recovery_decision is not None
            or attempt.state.request_fingerprint != query.expected_request_fingerprint
            or attempt.state.outcome_fingerprint != query.expected_outcome_fingerprint):
        raise ActivityRecoveryError("stale-evidence")
    original = stores.effect_attempt_intents.get(query.identity)
    outcome = stores.effect_outcomes.get(query.identity, attempt.latest_transition_event.event_id)
    source = original.intent.source
    _require(original.identity == query.identity and original.workspace_id == query.workspace_id
        and original.request_id == request.identity.request_id
        and original.request_fingerprint == attempt.state.request_fingerprint
        and original.original_start_event == attempt.original_start_event
        and source.plan_id == plan.plan_id and source.base_graph_id == plan.base_graph_id
        and source.desired_graph_id == plan.desired_graph_id
        and plan.plan.activity(original.intent.activity_id).operation == original.intent.operation
        and outcome.workspace_id == query.workspace_id and outcome.attempt == attempt
        and outcome.outcome.outcome_fingerprint == query.expected_outcome_fingerprint
        and outcome.outcome.request_fingerprint == query.expected_request_fingerprint)
    selection = original.intent.configuration_instances
    count, selection_fingerprint, protection = 0, None, "not-applicable"
    if original.intent.kind is RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1:
        _require(selection is not None)
        stores.configuration_preparation._require_original(original, read=read)
        count = len(selection.instances)
        selection_fingerprint = configuration_invocation_selection_fingerprint(selection)
        protection = "validated"
    latest = stores.execution.get_latest_run_for_request(request.identity.request_id)
    rows = read.query("WITH snapshot AS (SELECT statement_timestamp() AS at) "
        "SELECT snapshot.at::text, request.lease_expires_at <= snapshot.at "
        "FROM cpk_execution_requests request CROSS JOIN snapshot WHERE request.request_id=%s",
        (request.identity.request_id,), records=1, octets=65, cells=2)
    _require(len(rows) == 1 and type(rows[0][0]) is str)
    timestamp = decode_postgres_timestamp(datetime.fromisoformat(rows[0][0]))
    claim = request.claim
    claim_view = None
    if claim is not None:
        _require(type(rows[0][1]) is bool)
        claim_view = {"worker_id": claim.worker_id, "generation": claim.generation,
                      "claimed_at": claim.claimed_at, "lease_expires_at": claim.lease_expires_at,
                      "expired": rows[0][1]}
    blockers = list(_REQUIRED_BLOCKERS)
    if latest.run_id != run.run_id:
        blockers.append("run-not-current")
    if run.status is not ActivityRunStatus.RUNNING:
        blockers.append("run-not-running")
    pins = (workspace.current_graph_id, workspace.current_realized_projection_id,
            workspace.desired_graph_id, workspace.desired_realized_projection_id, workspace.desired_graph_revision)
    expected = (plan.base_graph_id, plan.base_realized_projection_id,
                plan.desired_graph_id, plan.desired_realized_projection_id, plan.desired_graph_revision)
    if pins != expected:
        blockers.append("plan-stale")
    if claim_view is None or claim_view["expired"]:
        blockers.append("claim-unavailable-or-expired")
    return ActivityRecoveryProposal(_bytes({
        "profile": "activity-recovery-proposal.v1", "status": "blocked", "workspace_id": query.workspace_id,
        "identity": query.identity.descriptor(), "historical_outcome": "uncertain",
        "provider_observation": "not-collected", "blockers": blockers,
        "source": {"request_id": request.identity.request_id, "session_id": session.session_id,
            "plan_id": plan.plan_id, "base_graph_id": plan.base_graph_id, "desired_graph_id": plan.desired_graph_id,
            "base_projection_id": plan.base_realized_projection_id,
            "desired_projection_id": plan.desired_realized_projection_id, "desired_revision": plan.desired_graph_revision},
        "evidence": {"request_fingerprint": original.request_fingerprint,
            "outcome_fingerprint": outcome.outcome.outcome_fingerprint, "profile": outcome.outcome.profile.value,
            "original_event_id": original.original_start_event.event_id,
            "original_recorded_at": original.original_start_event.occurred_at,
            "outcome_event_id": attempt.latest_transition_event.event_id,
            "outcome_recorded_at": attempt.latest_transition_event.occurred_at,
            "configuration_count": count, "configuration_selection_fingerprint": selection_fingerprint,
            "configuration_protection": protection},
        "current": {"snapshot_at": timestamp, "latest_run_id": latest.run_id, "run_status": run.status.value,
            "base_graph_id": workspace.current_graph_id, "base_projection_id": workspace.current_realized_projection_id,
            "desired_graph_id": workspace.desired_graph_id, "desired_projection_id": workspace.desired_realized_projection_id,
            "desired_revision": workspace.desired_graph_revision, "claim": claim_view},
    }))
