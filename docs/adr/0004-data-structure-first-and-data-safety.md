# ADR 0004: Data-Structure-First Engineering And Data Safety

## Status

Accepted.

## Context

The package is intentionally shaped by an interpreter-oriented programming
style. Inspired by the tradition of SICP and related systems-design work, the
preferred pattern is:

```text
accumulate typed data structures
  -> inspect
  -> validate
  -> transform
  -> interpret
  -> observe
```

This pattern is powerful because it keeps programs understandable before they
perform effects. A deployment recipe can become a graph. A graph can become a
diff. A diff can become an activity plan. An activity plan can be reviewed,
approved, executed, and observed.

However, real systems also contain persistent data, transactions, migrations,
secrets, and live runtime state. These are not ordinary refactors. Mistakes in
data engineering can corrupt user data, lose history, break idempotency, leak
secrets, or make rollback impossible.

The package therefore needs an explicit safety stance for data work.

## Decision

Treat data engineering and persistence-affecting work as a higher-care class of
change.

The package should continue to prefer typed intermediate values:

```text
Descriptor
ValidationResult
GraphDiff
ActivityPlan
MigrationPlan
RuntimeState
EventLog
```

but any interpreter that touches durable state must expose its plan before it
performs the effect.

For data-affecting work, the required pipeline is:

```text
desired state
  -> explicit plan
  -> validation
  -> dry run where possible
  -> approval boundary
  -> transactional execution where possible
  -> verification
  -> rollback or compensation story
  -> durable event/audit record
```

The package must not hide durable data mutation inside an opaque helper,
unreviewed runtime interpreter, or generic "apply" function.

## Data Safety Rules

### 1. Plans Before Effects

Any operation that can create, delete, migrate, rewrite, or re-point durable
data must produce an inspectable plan first.

Examples:

```text
DatabaseMigrationPlan
SecretRotationPlan
PersistentVolumeChangePlan
RuntimeVariablePatchPlan
TrafficCutoverPlan
```

### 2. Explicit Transaction Boundaries

If a data operation is transactional, the transaction boundary must be visible in
the code and tests.

If the operation is not transactional, the compensation or rollback story must
be documented.

### 3. Idempotency Is A Design Requirement

Repeated execution should be safe whenever possible. If an operation is not
idempotent, it must say so explicitly.

Good:

```text
Create database if missing.
Apply migration version 17 only if version 17 is not present.
Register target only if target ID is not already registered.
```

Risky:

```text
Insert row every time this activity retries.
Delete old resource before the new resource is verified.
Blindly patch runtime state without checking expected current value.
```

### 4. Preserve Identity

Stable identities must not be derived from mutable position, display order, or
human labels when durable history depends on them.

Human-readable labels can change. Durable IDs should survive movement,
renaming, grouping, and topology refactors.

### 5. Migrations Need Verification

A migration is not complete when it runs. It is complete when verification
proves the expected state.

Verification may include:

- row counts,
- version markers,
- checksums,
- foreign key consistency,
- expected graph descriptors,
- expected reachable health endpoints,
- or domain-specific invariants.

### 6. Secrets Are Not Data To Display

Secrets may be present, missing, rotated, or fingerprinted. They must not be
dumped into descriptors, logs, event records, PR comments, MCP responses, or UI
payloads.

### 7. Runtime Configuration Changes Need Concurrency Semantics

Changing a live route target, database URL, or runtime variable must say what
happens during concurrent traffic.

Valid answers include:

- live atomic swap,
- drain old target,
- reject until restart,
- restart-required,
- lock and patch,
- optimistic compare-and-set,
- or not supported.

Silence is not acceptable.

## Consequences

- Data-affecting PRs must include an explicit decision log section for data
  safety.
- Runtime interpreters should return structured plans and results.
- Activity plans should distinguish safe preview from execution.
- Tests must cover retry, duplicate execution, rollback/compensation, or
  verification behavior when relevant.
- The architecture remains interpreter-oriented, but effects are treated with
  appropriate operational seriousness.

## Non-Goals

- This ADR does not require every simple in-memory example to implement
  production-grade transactions.
- This ADR does not make the package a database migration framework.
- This ADR does not prescribe one storage engine, ORM, queue, or transaction
  manager.

## Review Checklist For Data-Affecting Work

Before merging a data-affecting PR, answer:

- What durable state can this change touch?
- What is the inspectable plan?
- What validates the plan before execution?
- What makes execution idempotent?
- What is the transaction boundary?
- If not transactional, what is the compensation story?
- What verifies success after execution?
- What happens on retry?
- What happens under concurrent use?
- What secrets or sensitive values are intentionally redacted?
- What durable event or audit record remains?

