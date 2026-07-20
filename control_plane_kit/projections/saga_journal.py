"""Project canonical activity events into the pure saga evidence model."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.execution import ActivityEventKind, ActivityEventRecord
from control_plane_kit.core.planning import ActivityPlan, Compensate
from control_plane_kit.saga import (
    SagaCompensationFailed,
    SagaCompensationRequested,
    SagaCompensationStarted,
    SagaCompensationSucceeded,
    SagaEvent,
    SagaState,
    SagaStepFailed,
    SagaStepId,
    SagaStepState,
    SagaStepStarted,
    SagaStepStatus,
    SagaStepSucceeded,
    evolve_all,
)


class SagaJournalError(ValueError):
    """Raised when durable execution history is not coherent saga evidence."""


@dataclass(frozen=True)
class SagaJournalProjection:
    """Pure saga state plus operational conditions requiring coordination."""

    state: SagaState
    in_flight: tuple[ActivityEventRecord, ...] = ()
    uncertain: tuple[ActivityEventRecord, ...] = ()
    compensation_in_flight: tuple[ActivityEventRecord, ...] = ()
    compensation_uncertain: tuple[ActivityEventRecord, ...] = ()


def project_activity_journal(
    plan: ActivityPlan,
    events: tuple[ActivityEventRecord, ...],
) -> SagaJournalProjection:
    """Fold canonical ActivityEvents without creating a second journal."""

    if not isinstance(plan, ActivityPlan):
        raise TypeError("saga journal projection requires ActivityPlan")
    if tuple(event.ordinal for event in events) != tuple(
        sorted(event.ordinal for event in events)
    ) or len({event.ordinal for event in events}) != len(events):
        raise SagaJournalError("activity journal ordinals must be unique and increasing")
    if len({event.run_id for event in events}) > 1:
        raise SagaJournalError("activity journal cannot mix run identities")
    plan_ids = {activity.activity_id.value for activity in plan.activities}
    saga_events: list[SagaEvent] = []
    uncertain_by_step: dict[str, ActivityEventRecord] = {}
    event_by_step: dict[str, ActivityEventRecord] = {}
    compensation_uncertain_by_step: dict[str, ActivityEventRecord] = {}
    compensation_event_by_step: dict[str, ActivityEventRecord] = {}
    started_steps: set[str] = set()

    for event in events:
        if event.activity_id is not None and event.activity_id not in plan_ids:
            raise SagaJournalError(
                f"activity event references foreign step {event.activity_id!r}"
            )
        if event.kind is ActivityEventKind.RUN_COMPENSATION_STARTED:
            saga_events.append(SagaCompensationRequested())
            continue
        if event.activity_id is None:
            continue
        step_id = SagaStepId(event.activity_id)
        match event.kind:
            case ActivityEventKind.STEP_STARTED:
                saga_events.append(SagaStepStarted(step_id))
                event_by_step[event.activity_id] = event
                started_steps.add(event.activity_id)
            case ActivityEventKind.STEP_SUCCEEDED:
                saga_events.append(SagaStepSucceeded(step_id))
                event_by_step.pop(event.activity_id, None)
            case ActivityEventKind.STEP_FAILED:
                saga_events.append(SagaStepFailed(step_id))
                event_by_step.pop(event.activity_id, None)
            case ActivityEventKind.STEP_UNSUPPORTED:
                if event.activity_id not in started_steps:
                    saga_events.append(SagaStepStarted(step_id))
                saga_events.append(SagaStepFailed(step_id))
                event_by_step.pop(event.activity_id, None)
            case ActivityEventKind.STEP_UNCERTAIN:
                uncertain_by_step[event.activity_id] = event
                event_by_step.pop(event.activity_id, None)
            case ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED:
                if event.activity_id not in uncertain_by_step:
                    raise SagaJournalError(
                        "success resolution requires prior uncertain evidence"
                    )
                saga_events.append(SagaStepSucceeded(step_id))
                uncertain_by_step.pop(event.activity_id)
            case ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED:
                if event.activity_id not in uncertain_by_step:
                    raise SagaJournalError(
                        "failure resolution requires prior uncertain evidence"
                    )
                saga_events.append(SagaStepFailed(step_id))
                uncertain_by_step.pop(event.activity_id)
            case ActivityEventKind.STEP_COMPENSATION_STARTED:
                saga_events.append(SagaCompensationStarted(step_id))
                compensation_event_by_step[event.activity_id] = event
            case ActivityEventKind.STEP_COMPENSATION_SUCCEEDED:
                saga_events.append(SagaCompensationSucceeded(step_id))
                compensation_event_by_step.pop(event.activity_id, None)
            case ActivityEventKind.STEP_COMPENSATION_FAILED:
                saga_events.append(SagaCompensationFailed(step_id))
                compensation_event_by_step.pop(event.activity_id, None)
            case ActivityEventKind.STEP_COMPENSATION_UNSUPPORTED:
                if event.activity_id not in compensation_event_by_step:
                    saga_events.append(SagaCompensationStarted(step_id))
                saga_events.append(SagaCompensationFailed(step_id))
                compensation_event_by_step.pop(event.activity_id, None)
            case ActivityEventKind.STEP_COMPENSATION_UNCERTAIN:
                compensation_uncertain_by_step[event.activity_id] = event
                compensation_event_by_step.pop(event.activity_id, None)
            case ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED:
                if event.activity_id not in compensation_uncertain_by_step:
                    raise SagaJournalError(
                        "compensation success resolution requires prior uncertain evidence"
                    )
                saga_events.append(SagaCompensationSucceeded(step_id))
                compensation_uncertain_by_step.pop(event.activity_id)
            case ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED:
                if event.activity_id not in compensation_uncertain_by_step:
                    raise SagaJournalError(
                        "compensation failure resolution requires prior uncertain evidence"
                    )
                saga_events.append(SagaCompensationFailed(step_id))
                compensation_uncertain_by_step.pop(event.activity_id)
            case _:
                continue

    state = evolve_all(
        initial_state_for_plan(plan),
        tuple(saga_events),
    )
    running_ids = {
        value.step_id.value
        for value in state.steps
        if value.status is SagaStepStatus.RUNNING
    }
    return SagaJournalProjection(
        state,
        tuple(
            event_by_step[value]
            for value in sorted(running_ids)
            if value in event_by_step
        ),
        tuple(uncertain_by_step[key] for key in sorted(uncertain_by_step)),
        tuple(
            compensation_event_by_step[value]
            for value in sorted(compensation_event_by_step)
        ),
        tuple(
            compensation_uncertain_by_step[key]
            for key in sorted(compensation_uncertain_by_step)
        ),
    )


def initial_state_for_plan(plan: ActivityPlan) -> SagaState:
    """Create pure initial evidence using ActivityPlan as dependency authority."""

    if not isinstance(plan, ActivityPlan):
        raise TypeError("initial saga evidence requires ActivityPlan")
    return SagaState(
        tuple(
            SagaStepState(
                SagaStepId(activity.activity_id.value),
                compensation_available=isinstance(activity.compensation, Compensate),
            )
            for activity in plan.activities
        )
    )
