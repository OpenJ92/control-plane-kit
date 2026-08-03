# Control Plane Kit Operating Model

Status: Draft
Last updated: 2026-07-14

This is the short operating model for `control-plane-kit`.

The longer ADRs, roadmap docs, and design docs explain why. This file explains
how to work.

## Core Loop

```text
roadmap node
  -> roadmap parent issue
    -> ordered child issues
      -> roadmap integration branch
        -> per-child test context
          -> test-conditioned dry run
            -> child PRs
              -> decision logs
              -> review
              -> handoff
    -> roadmap PR
      -> closeout
      -> develop
```

Roadmap documents are the outer topology. Child issues are the inner topology.

## Branch Shape

```text
main
  develop
    roadmap/<roadmap-id>-<slug>
      codex/<issue-id>-<slug>
      codex/<issue-id>-<slug>
      codex/<issue-id>-<slug>
```

Child PRs target the roadmap branch. The roadmap branch targets `develop`.

## Before Implementing A Child Issue

```text
governing frozen/new laws
  -> behavioral law cards
    -> source dry run with those laws in view
      -> target public-interface design
        -> focused target tests
          -> focused target-red evidence
            -> implementation
              -> target green
```

Classify tests as `isomorphic`, `strengthened`, `new-law`, or
`non-executable-scaffold`. Preserve semantic assertions rather than obsolete
file layout. Do not use skips, `xfail`, weakened assertions, or imports of the
frozen implementation to manufacture a passing migration.

Before the dry run, inspect the governing frozen tests and extract compact law
cards: test identity, observable law, negative cases, old structural
assumptions to discard, and future owner. Do not copy their imports, fixtures,
constructors, or file layout into the target package yet.

The dry run uses those laws as context and records the affected boundaries,
risks, target public interface, and any child-child decomposition before target
tests or application code are written. After that design exists, write the
focused target tests and prove that they fail because behavior is missing, not
because collection, imports, fixtures, or Docker setup are broken.

The frozen parity foundation supplies the reference-green baseline. The mutable
frozen package and root gate have been retired. Do not run
`./reference-test.sh` before every issue dry run unless that baseline is
missing, stale, or disputed. Run broader target package, current-backend,
parity, and issue-owned live suites after implementation and at PR or milestone
gates.

## Before Starting A Roadmap Node

Read:

1. `AGENTS.md`
2. the selected roadmap document
3. the architecture design doc
4. relevant ADRs only
5. relevant source

Then create or update:

- a roadmap parent issue,
- ordered child issues,
- a draft roadmap PR,
- and the roadmap integration branch.

## Every Non-Trivial PR Includes

```text
Decision log

- Chosen shape:
- Important snippets:
- Why:
- Alternatives considered:
- Tests:
- Security:
- Risks:
- Handoff:
```

Include additional sections when relevant:

```text
Data safety
Mathematical design note
Operational history
Operational reliability
```

## Mathematical Frame

For structural work, explain:

```text
Objects
Transformations
Laws/invariants
Valid compositions
Interpreter boundary
```

This is how the user reasons about the package.

## Safety Frame

For data-affecting work, explain:

```text
durable state
transaction boundary
idempotency
retry behavior
verification
rollback or compensation
redaction
```

For security-affecting work, explain:

```text
new surfaces
auth/authz
secrets/redaction
network exposure
mutation/destructive behavior
tests
residual risk
```

## Operational Frame

For runtime/control-plane work, explain:

```text
session/action records
plans or graph snapshots
events emitted
query surfaces
partial failure story
retry/resume story
retention/cleanup
```

## Narrow Resumable Programs

When one operator workflow crosses an external-effect boundary, compose it as
small durable phases:

```text
short transaction: prepare exact intent and action identity
  -> commit
    -> bounded external effect
      -> short transaction: fold bounded result and evidence
```

Each command service owns its UnitOfWork. Stores never commit independently,
and no Postgres transaction remains open across provider, Docker, gateway,
health, HTTP, filesystem, clock waiting, or other external IO.

Restart and retry semantics must be explicit. Exact committed intent replays
the same result or action. A definite failure known to precede mutation may be
retryable. An uncertain effect blocks; absence of a folded result is not proof
that the effect did not happen. Waiting is a typed result derived from durable
time evidence and an injected trusted clock, never a policy `sleep()`.

Prefer issue-specific narrow programs while the domain law is still being
learned. Do not invent a second generic workflow engine. #1092 owns reusable
effect recovery, leases, and fencing. #1096 owns the eventual reusable
`DeploymentProgram` composition after concrete programs have supplied enough
evidence for the common abstraction.

At the end of every roadmap node, check:

```text
health/status
logs/events
failure modes
cleanup
retry/resume
examples added or updated
examples still missing
```

## Example Rule

Every durable abstraction should have a teaching example.

Prefer an example ladder:

```text
tiny example
composition example
runtime smoke example
roadmap capstone example
```

## Stop And Split When

- a PR needs more than one independent decision log,
- a concept rename mixes with behavior,
- a change touches multiple public concepts,
- tests fall into unrelated groups,
- security/data/operational questions are unclear,
- or review requires holding too much state in memory.

Prefer smaller topology over heroic PRs.
