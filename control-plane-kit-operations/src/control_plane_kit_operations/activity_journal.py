"""Projection from durable activity events into the pure saga journal language."""

from __future__ import annotations

from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.planning.saga import (
    ActivityJournalEvent,
    ActivityJournalEventKind,
)
from control_plane_kit_operations.records import ActivityEventRecord


EVENT_KIND_TO_JOURNAL_KIND = {
    ActivityEventKind.STEP_STARTED: ActivityJournalEventKind.STEP_STARTED,
    ActivityEventKind.STEP_SUCCEEDED: ActivityJournalEventKind.STEP_SUCCEEDED,
    ActivityEventKind.STEP_FAILED: ActivityJournalEventKind.STEP_FAILED,
    ActivityEventKind.STEP_UNSUPPORTED: ActivityJournalEventKind.STEP_UNSUPPORTED,
    ActivityEventKind.STEP_UNCERTAIN: ActivityJournalEventKind.STEP_UNCERTAIN,
    ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED: (
        ActivityJournalEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED
    ),
    ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED: (
        ActivityJournalEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED
    ),
    ActivityEventKind.RUN_COMPENSATION_STARTED: (
        ActivityJournalEventKind.RUN_COMPENSATION_STARTED
    ),
    ActivityEventKind.STEP_COMPENSATION_STARTED: (
        ActivityJournalEventKind.STEP_COMPENSATION_STARTED
    ),
    ActivityEventKind.STEP_COMPENSATION_SUCCEEDED: (
        ActivityJournalEventKind.STEP_COMPENSATION_SUCCEEDED
    ),
    ActivityEventKind.STEP_COMPENSATION_FAILED: (
        ActivityJournalEventKind.STEP_COMPENSATION_FAILED
    ),
    ActivityEventKind.STEP_COMPENSATION_UNSUPPORTED: (
        ActivityJournalEventKind.STEP_COMPENSATION_UNSUPPORTED
    ),
    ActivityEventKind.STEP_COMPENSATION_UNCERTAIN: (
        ActivityJournalEventKind.STEP_COMPENSATION_UNCERTAIN
    ),
    ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED: (
        ActivityJournalEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED
    ),
    ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED: (
        ActivityJournalEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED
    ),
}


def activity_journal_events(
    events: tuple[ActivityEventRecord, ...],
) -> tuple[ActivityJournalEvent, ...]:
    """Interpret durable events as pure saga journal events."""

    journal: list[ActivityJournalEvent] = []
    for event in events:
        kind = EVENT_KIND_TO_JOURNAL_KIND.get(event.kind)
        if kind is None:
            continue
        journal.append(
            ActivityJournalEvent(
                event_id=event.event_id,
                run_id=event.run_id,
                ordinal=event.ordinal,
                kind=kind,
                activity_id=event.activity_id,
            )
        )
    return tuple(journal)
