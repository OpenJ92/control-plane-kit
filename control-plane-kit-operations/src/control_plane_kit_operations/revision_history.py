"""Pure attribution of bounded revision history; association never grants authority."""
from __future__ import annotations

from typing import Protocol

from control_plane_kit_operations.advancement import CurrentGraphAdvancementError, CurrentGraphAdvancementResult
from control_plane_kit_operations.workflows import InvalidOperationCommand
from control_plane_kit_operations.read_pages import (
    ReadPage, ReadPageRequest, RevisionReadScope, _canonical_instant, _general_identifier,
)
from control_plane_kit_operations.saved_deployment_preparation import (
    validate_saved_preparation_source, validate_saved_preparation_start,
)


HISTORY_SCOPE = "source-or-target-sessions-and-exact-target-attempts"


class RevisionHistoryStore(Protocol):
    def page(self, request: ReadPageRequest) -> ReadPage[dict]: ...
    def presence(self, scope: RevisionReadScope) -> dict: ...


def unavailable_source():
    return {"state": "unavailable", "draft_id": None, "revision": None, "graph_id": None}


def saved_revision_source(scope, source, session, revision, start, *, plan=None):
    """Reuse immutable admission laws; a target match alone proves no origin."""
    if any(value is None for value in (source, session, revision, start)):
        return unavailable_source()
    try:
        metadata = validate_saved_preparation_source(source, session, revision)
        validate_saved_preparation_start(session, start)
        if (source.workspace_id, source.draft_id, source.revision) != (
                scope.workspace_id, scope.draft_id, scope.revision):
            return unavailable_source()
        if plan is not None:
            expected = (
                metadata["deployment_prepare_saved_current_graph_id"],
                metadata["deployment_prepare_saved_current_projection_id"],
                metadata["deployment_prepare_saved_graph_id"],
                metadata["deployment_prepare_saved_desired_projection_id"],
                int(metadata["deployment_prepare_saved_desired_generation"]),
            )
            actual = tuple(plan[key] for key in ("base_graph_id", "base_realized_projection_id",
                "desired_graph_id", "desired_realized_projection_id", "desired_graph_revision"))
            if actual != expected:
                return unavailable_source()
        for value in (source.draft_id, revision.graph_id):
            _general_identifier(value)
    except (ValueError, TypeError, KeyError, AttributeError):
        return unavailable_source()
    return {"state": "saved", "draft_id": source.draft_id, "revision": source.revision,
            "graph_id": revision.graph_id}


def historical_advancement(*, workspace_id, session_id, plan_id, plan, request_id,
                           run_id, projection_digest, events, actions):
    """Interpret one retained pair, never a command replay or today's lease."""
    if not events and not actions:
        return {"state": "none-recorded", "receipt": None}
    unavailable = {"state": "unavailable", "receipt": None}
    if len(events) != 1 or len(actions) != 1 or events[0] is None or actions[0] is None:
        return unavailable
    event, action = events[0], actions[0]
    try:
        if (action.session_id != session_id or action.payload.get("execution_request_id") != request_id
                or event.occurred_at != action.created_at):
            return unavailable
        result = CurrentGraphAdvancementResult(
            workspace_id=workspace_id, from_authored_graph_id=plan["base_graph_id"],
            from_realized_projection_id=plan["base_realized_projection_id"],
            to_authored_graph_id=plan["desired_graph_id"],
            to_realized_projection_id=plan["desired_realized_projection_id"],
            to_realized_projection_digest=projection_digest,
            desired_graph_revision=plan["desired_graph_revision"], run_id=run_id,
            plan_id=plan_id, event=event, action=action,
        )
        _general_identifier(result.event.event_id)
        _general_identifier(result.action.action_id)
        # SQL evidence codecs produce the same closed microsecond UTC language.
        _canonical_instant(event.occurred_at)
    except (CurrentGraphAdvancementError, InvalidOperationCommand, ValueError, TypeError, KeyError, AttributeError):
        return unavailable
    return {"state": "accepted", "receipt": {"event_id": event.event_id,
        "action_id": action.action_id, "occurred_at": event.occurred_at,
        "to_realized_projection_digest": projection_digest}}
