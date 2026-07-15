# ADR 0008: Transactional Data Engineering Policy

## Status

Accepted.

## Context

`control-plane-kit` is moving from pure topology algebra toward durable
operational systems:

```text
DeploymentGraph
  -> OperationSession
  -> ActivityPlan
  -> ActivityRun
  -> Runtime effects
  -> ObservedState
```

This introduces relational databases, graph topology stores, activity history,
approval records, runtime mutation, control-route calls, and eventually
multi-process services.

Those operations cannot be treated like ordinary in-memory transformations.
Some transitions are safely atomic inside one database. Others cross process,
runtime, network, or infrastructure boundaries and cannot be made ACID by wish.

The package therefore needs a concrete data-engineering policy for atomicity,
idempotency, concurrency, partial failure, migrations, and auditability.

## Decision

Every durable state transition must be one of two things:

```text
atomic inside one store transaction
```

or:

```text
explicitly modeled as a multi-step workflow with durable events,
retries, compensation, and visible partial failure
```

The package must not hide distributed partial failure behind a helper that
pretends a multi-system operation is a single transaction.

## Store-Local Atomicity

When a transition is contained inside one relational store, it must use a real
transaction.

Examples:

```text
create OperationSession + first OperationAction
save desired graph version + graph metadata
record PlanApproval
open ActivityRun + initial ActivityEvent
claim a queued execution request
```

Rules:

- Related writes must share an explicit transaction boundary.
- Route handlers must not write several tables independently.
- Services must not mutate durable state through hidden side effects outside
  their repository/store boundary.
- Transaction boundaries should be visible in service or unit-of-work code.
- Failed transactions must leave no half-written workflow state.

## Cross-Boundary Effects

Operations that cross stores, runtimes, block control routes, secret stores, or
external services are not one ACID transaction.

Examples:

```text
write ActivityRun row
start Docker container
call router control route
patch runtime variable
write secret value
query health
update ObservedState
append ActivityEvent
```

These operations must be modeled as activity/saga workflows:

```text
record intent
execute one step
record event
execute next step
record event
on failure:
  compensate where possible
  record compensation events
  leave run in failed/partial state
```

Partial failure is a first-class state.

The package should prefer recoverable workflow records over pretending that
external effects can be rolled back automatically.

## Idempotency

Mutation APIs must accept or derive idempotency keys when retry or duplicate
submission is plausible.

This applies to:

- creating operation sessions,
- saving graph edits,
- requesting activity plans,
- approving plans,
- executing plans,
- starting runtime nodes,
- stopping runtime nodes,
- calling block control routes,
- writing secrets,
- and recording externally triggered events.

Repeated requests should return the original result, report that the operation
was already applied, or fail with a clear conflict. They must not duplicate
dangerous work.

## Optimistic Concurrency

Graph edits must name the graph version they were built from.

Example:

```text
operator edits desired_graph_version = 7
current desired_graph_version is now 8
=> reject, rebase, or explicitly merge
```

The system must not silently overwrite a newer graph version.

This protects:

- UI edits,
- CLI edits,
- MCP edits,
- concurrent operator sessions,
- and automatic planning tools.

## Execution Locking

Execution must prevent multiple workers from running the same approved plan
unless an explicit retry/resume model says otherwise.

For Postgres-backed execution state, this can be implemented with:

- guarded status transitions,
- row-level locks,
- `SELECT ... FOR UPDATE`,
- `SKIP LOCKED` queues,
- unique constraints on active runs,
- or equivalent mechanisms.

The required invariant is:

```text
one approved plan cannot accidentally produce two concurrent ActivityRuns
```

unless the plan explicitly allows that behavior.

## Durable Requests Before Effects

External effects should be requested durably before they happen.

The outbox pattern is recommended where appropriate:

```text
transaction:
  save approval
  enqueue execution_requested event

worker:
  claim execution_requested event
  execute activity
  append ActivityEvent
```

This prevents the system from committing an approval or graph edit, crashing,
and losing the fact that execution still needed to happen.

## Approval And Destructive Operations

Consequential activities require explicit approval records before execution.

Some activities require stronger approval than ordinary graph edits:

- destroying runtime resources,
- deleting retained state,
- destructive database migrations,
- secret rotation with old-value discard,
- external irreversible messages,
- production traffic switches,
- and history deletion.

The approval record must capture:

- who approved,
- what plan or run was approved,
- when approval happened,
- what scope authorized it,
- whether destructive activities were included,
- and any required confirmation text or comment.

The backend must enforce approval boundaries. A scary UI is not sufficient.

## Auditability

Every meaningful durable transition should record:

- actor,
- session,
- action,
- base version,
- resulting version,
- approval if required,
- activity/run if execution happened,
- timestamp,
- result,
- and failure or compensation details.

Activity history is not raw logging. It is structured operational memory.

Logs may supplement history, but they do not replace it.

## Secrets

Secrets must not appear in:

- graph descriptors,
- activity plans,
- activity events,
- observed-state payloads,
- read models,
- logs,
- PR decision logs,
- or MCP responses.

Secret values are write-only or represented by stable secret references.

It is acceptable to record:

```text
secret ref exists
secret ref was assigned
secret ref was rotated
secret ref failed validation
```

It is not acceptable to return the secret value.

## Migration Discipline

Once relational persistence exists, schema changes require migrations.

Migration work must specify:

- forward migration,
- rollback or compensation story,
- behavior for empty databases,
- behavior for existing databases,
- expected locks or downtime,
- and tests or smoke checks.

Manual schema drift is not acceptable.

## Store Interfaces And Future Extraction

Modules should depend on store/service protocols rather than concrete database
files or tables.

This preserves the future path:

```text
in-process repository today
  -> HTTP/gRPC/MCP/service boundary tomorrow
```

For example, an `OperationSessionService` should not reach directly into graph
storage internals. It should call the appropriate store or service contract.

## Review Checklist

For any PR that touches durable data, persistence, graph versions, activity
history, approval, execution, observed state, runtime mutation, or secrets,
answer:

- What store owns this truth?
- What transaction protects related writes?
- What idempotency key protects retries?
- What version or lock protects concurrency?
- What happens if the process crashes halfway through?
- Is this single-store atomic or cross-boundary saga work?
- What partial failure state is visible?
- What compensation is possible?
- What approval is required?
- What audit record remains?
- Are secrets redacted?
- Is a migration required?

## Consequences

- The first implementation may feel heavier than a simple CRUD server.
- Activity history, approvals, idempotency, and locks become part of the design
  early.
- Runtime effects must be represented as workflows rather than hidden side
  effects.
- Data loss and partial failure become easier to reason about.
- Future service extraction is easier because store and service boundaries are
  explicit from the start.

## Non-Goals

- This ADR does not require a graph database in the first implementation.
- This ADR does not require distributed ACID transactions.
- This ADR does not require perfect rollback.
- This ADR does not require every demo block to implement production-grade
  durability.
- This ADR does not choose Postgres migrations tooling yet.
