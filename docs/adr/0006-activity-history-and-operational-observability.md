# ADR 0006: Activity History And Operational Observability

## Status

Accepted.

## Context

`control-plane-kit` will have multiple faces:

- Python API,
- CLI,
- MCP adapter,
- web or iPad UI,
- and future automation.

All of those faces should communicate with a control-plane/home server that owns
the realized deployment graph, accepted user actions, activity plans, execution
results, events, and operational history.

This mirrors a lesson from workflow-style systems: a service should not only
perform actions. It should preserve the activity that led to those actions.
Sessions, lines, submitted actions, completion events, and results create a
history that users and operators can inspect later.

For this package, logs are not enough. Logs are low-level observations. The
control plane needs a structured activity history that can answer:

```text
What did the user intend?
What graph was current at the time?
What graph was requested?
What plan was produced?
Who approved it?
What activities were executed?
Which activities succeeded, failed, retried, or were skipped?
What state was observed afterward?
What can be safely resumed, retried, or rolled back?
```

## Decision

The control-plane/home server should model operational activity as durable,
queryable data.

The core shape is:

```text
OperationSession
  captures a user/system intent over time.

OperationAction
  captures an explicit requested action inside a session.

ActivityPlan
  captures the interpreted plan before execution.

ActivityRun
  captures one execution attempt of a plan.

ActivityEvent
  captures bounded, structured observations during execution.

ObservedState
  captures what the system saw after execution.
```

The UI, MCP adapter, and CLI should query this history from the control-plane
server rather than reconstructing it from process logs.

## Relationship To The Algebra

This ADR extends the same interpreter-oriented pattern:

```text
user intent
  -> OperationSession
  -> desired DeploymentGraph
  -> GraphDiff
  -> ActivityPlan
  -> ActivityRun
  -> ActivityEvent*
  -> ObservedState
```

The history is not incidental persistence. It is part of the structure of the
program. It is the record of interpretations that happened in the real world.

## Example Shape

```python
@dataclass(frozen=True)
class OperationSession:
    session_id: str
    title: str
    status: OperationStatus
    created_by: ActorRef
    created_at: datetime
    closed_at: datetime | None = None


@dataclass(frozen=True)
class OperationAction:
    action_id: str
    session_id: str
    action_type: str
    payload: Mapping[str, object]
    created_by: ActorRef
    created_at: datetime


@dataclass(frozen=True)
class ActivityRun:
    run_id: str
    session_id: str
    plan_id: str
    status: ActivityRunStatus
    started_at: datetime
    completed_at: datetime | None = None
```

The exact implementation may evolve, but the concept should remain: control
plane work has a durable session/action/plan/run/event shape.

## What Belongs In Activity History

Activity history should record:

- graph creation,
- graph edits,
- socket connections,
- validation attempts,
- generated plans,
- approvals,
- execution starts,
- activity-level success/failure,
- retries,
- cancellations,
- rollbacks or compensations,
- observed health checks,
- target switches,
- variable patches,
- runtime resource creation/deletion,
- and final session closeout.

## What Does Not Belong In Activity History

Do not store:

- raw secrets,
- unbounded request/response bodies,
- full process logs,
- private keys or tokens,
- unredacted environment dumps,
- or large binary artifacts.

The activity history may link to bounded logs or artifacts, but it should remain
structured and safe to query.

## Consequences

- Control-plane interfaces can show a user's work as sessions rather than only
  current state.
- MCP tools can explain what happened without scraping logs.
- The UI can show a timeline of graph edits, plans, approvals, and execution.
- Failed operations can be resumed or reasoned about from structured state.
- Activity planning and execution need stable IDs and event emission.
- Data safety and security reviews must include activity-history behavior.

## Non-Goals

- This ADR does not require a specific database yet.
- This ADR does not require event sourcing for every internal object.
- This ADR does not make logs obsolete.
- This ADR does not require every tiny example to persist history.

## Open Questions

- Should the first persistence backend be SQLite, Postgres, or an abstract
  repository with in-memory tests?
- Should graph descriptors be snapshotted by value, referenced by content hash,
  or both?
- How long should activity history be retained?
- Which events are user-facing and which are diagnostic only?
- How should MCP mutation approval attach to an operation session?
- Should a session be required for every mutation, or can simple mutations
  create implicit sessions?

## Review Checklist

For PRs that affect control-plane server behavior, activity planning, execution,
MCP mutation, UI state, or runtime mutation, answer:

- What operation session or action records are created?
- What plan or graph snapshot is persisted?
- What events are emitted?
- What values are redacted?
- Can the history explain partial failure?
- Can retries attach to the same session/run?
- What can UI/MCP/CLI query afterward?
- What is the cleanup or retention story?

