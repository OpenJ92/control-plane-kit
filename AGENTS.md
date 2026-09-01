# control-plane-kit Agent Guide

Canonical contract: `cpk-agent-contract/v1`

Source: [CPK #1741](https://github.com/OpenJ92/control-plane-kit/issues/1741).
This file is the shared daily contract for this repository. Nested `AGENTS.md`
files may add package-specific constraints, but they may not weaken this file's
authorization, Docker-only validation, truthful-uncertainty, test-ownership, or
GitHub-memory laws. When instructions conflict, follow the more protective law
and record the conflict on the governing issue.

Read `docs/OPERATING_MODEL.md` for the working loop and `docs/TESTING.md` before
executable validation. Read only the design docs and ADRs relevant to the
current issue.

`control-plane-kit` is a generic Python package for describing deployable
systems as topology values. It must remain independent from any one application
or product repository.

## Product Boundary

`control-plane-kit` is an AI-assisted infrastructure control plane. It lets a
user define server topologies, deploy and edit them across multiple runtimes,
inspect their health, remove resources, and receive bounded reports derived
from the providers that own the external runtime truth.

The governing interaction is:

```text
define topology
  -> inspect current providers
  -> produce and explain an explicit plan
  -> request permission for consequential actions
  -> execute through provider interpreters
  -> inspect the result
  -> report what happened and what remains uncertain
```

The AI may read structured provider observations, explain drift or failure, and
recommend recovery, cleanup, replacement, or failover. It must not silently
turn those recommendations into authority. The user owns permission decisions;
CPK owns translation, validation, execution of approved plans, durable history,
and truthful reporting.

Use these conservative defaults:

- provider reads, health inspection, and report generation may be automatic;
- mutations require an explicit inspectable plan and appropriate authorization;
- destructive cleanup, public exposure, increased cost or capacity, credential
  changes, cross-provider movement, adoption, and ambiguous retries require
  explicit user approval;
- an interrupted or ambiguous external mutation must never be blindly
  redispatched;
- provider state is authoritative for external resources, while CPK records its
  own intent, attempts, ownership, observations, and approved actions;
- uncertain outcomes remain uncertain until authoritative evidence resolves
  them; never fabricate success, graph advancement, cleanup, or ownership;
- reports summarize provider evidence in bounded, redacted form and retain
  enough durable correlation to explain what was requested, attempted, changed,
  failed, and recommended next.

Do not assume that autonomous compensation, failover, adoption, or generalized
recovery is a product requirement. Add such behavior only when a concrete user
workflow and an explicitly approved roadmap issue require it. The default
recovery shape is to observe, explain, propose an exact action, request
permission, execute if approved, and report the result.

Tests must stay at their ownership boundary. Core and Operations tests own CPK
algebra and durable semantics. Server, interpreter, and live tests prove only
their composition and externally observable behavior; they must not recreate
CPK's state machines or become a parallel implementation.

## Durable Project Memory

GitHub is the project's durable user-facing control surface:

- issue bodies own the current capability contract, owner, topology and
  dependencies, accepted base and destination, public acceptance, authority
  class, owning Docker suite and prerequisites, exclusions, stop conditions,
  and downstream handoff;
- material issue comments record decisions, supersessions, causal evidence,
  stops, releases, and handoffs;
- PR bodies explain implementation shape, ownership, alternatives, security,
  data and history consequences, validation, risks, and handoff;
- review comments are findings-first and conclude `PASS` or `HOLD`, with the
  exact next permitted boundary.

Commits, trees, hashes, local logs, `/tmp` artifacts, inventories, task
messages, and chat transcripts are supporting coordinates. They never replace
the governing GitHub record. Do not leave a material decision, validation
result, review disposition, or dependency handoff only in local state.

Before a long Docker gate, push a candid checkpoint and record that it is
unvalidated. After a material gate or stop, update the issue or PR with the
result and its meaning, not a dump of every local artifact.

## Branch And Issue Flow

The normal feature flow is:

```text
current develop
  -> codex/<issue-id>-<slug>
    -> PR into develop
```

Use a roadmap integration branch only when a parent issue explicitly defines a
dependent multi-PR vertical and its merge order. Do not create roadmap branches,
draft roadmap PRs, migration manifests, or separate hardening PRs by default.

Keep issues small enough for one coherent decision log. Split when a change
introduces unrelated public concepts, owners, security decisions, durable-data
boundaries, or independently useful merge order.

## Assigned Roles And Handoffs

When roles are assigned:

- **North / coordinator** owns issue topology, releases, shared/live/destructive
  authority, merge disposition, and handoff routing;
- **Vale / implementer** owns the bounded source and test change and invokes the
  owning package suite when released;
- **Meridian / reviewer** remains independently read-only unless reassigned and
  reports concrete findings, the smallest correction, residual risk, and
  `PASS` or `HOLD`.

Every assignment links its governing issue or PR and states the role, allowed
action, repository, branch/base/destination, scope ceiling, owning suite and
prerequisites, authority limits, stop conditions, expected return artifact,
and next reviewer.

Every completed handoff names the stage and links the durable GitHub artifact.
Silence is not approval. The coordinator actively polls or waits for assigned
work, acknowledges receipt, and routes the next boundary; agents do not infer a
release from an idle task.

## Proportional Implementation And Review

Start from the current public contract, relevant source, and the smallest
ownership-local behavioral proof. Use law cards, frozen parity translation, and
focused target-red evidence only when an issue is explicitly migration/parity
governed or when causality for genuinely missing behavior matters.

Tests prove the changed owner's public boundary. Do not make application tests
police helper names, AST layout, source organization, exhaustive mutation
matrices, assertion-count arithmetic, or wrapper bookkeeping. Do not recreate
another package's state machine in server or live tests. Fixture exemplars are
not runtime invariants.

Review blocks only concrete correctness, ownership, public-contract,
authority/security, durable-data, destructive-operation, or evidence defects.
Use `PASS` with residual notes or `HOLD` with the smallest correction; do not
use speculative exhaustive hardening as a condition of progress.

A separate hardening issue or PR is justified only by a concrete coherent risk
surface found in review. Implementation should carry the behavior; tests should
remain proportional evidence rather than a parallel implementation.

## Module Ownership

Read `docs/adr/0009-package-boundary-topology.md` before changing package
ownership or dependency direction. Preserve this vocabulary:

```text
core         owns the deployment language
domains      own independent closed languages
operations   own durable control-plane truth
interpreters perform representation and external effects
products     are graph-visible deployable values
entrypoints  compose dependencies and run processes
```

The package graph must remain acyclic. Stores own durable facts and valid
mutations; workflows own grouped intent; policies decide authority and
approval; planners interpret current and desired truth; effects call providers
only after authorization; projections and interfaces expose semantics without
inventing them.

Route handlers, CLI commands, MCP tools, and UI payloads must not become hidden
owners of durable semantics.

## Data, Security, And History

Read ADR 0004 for persistence, ADR 0005 for security, ADR 0006 for operational
history, and ADR 0007 for runtime reliability when those surfaces are in scope.

For durable mutation:

- make the plan and transaction boundary explicit;
- define idempotency, concurrency, verification, and uncertain-outcome behavior;
- never hold a database transaction across an external effect;
- do not imply compensation or retry when safe stop and explicit follow-up are
  the truthful contract.

For security-sensitive work:

- mutation requires authentication and authorization;
- destructive activity and public exposure require explicit approval;
- secrets are never returned or written to logs, reports, descriptors, or test
  artifacts;
- reports and errors are bounded and redacted;
- private Docker networking is not a security boundary;
- read-only and mutation MCP tools remain distinct.

User/system intent becomes structured durable history: sessions/actions, plans,
approvals, runs, attempts, events, observations, and bounded results as owned by
the relevant package. Logs supplement that truth; they do not replace it.

## Validation And Acceptance

All executable validation uses the owning package's established Docker-backed
suite. Host work is limited to source, Git/GitHub, and invoking repository
commands. Do not use host Python or PostgreSQL, venvs, host `pip`, alternate
databases, shims, or custom wrappers when an owning suite exists.

Follow `docs/TESTING.md`. If the documented suite or prerequisite is missing,
cannot start, or fails for apparatus, stop and ask rather than improvising or
silently retrying. Normal package suites are repeatable. One-shot wrappers,
leases, hash ledgers, and full inventory sealing apply only to an explicitly
authorized shared/provider/destructive gate.

After bootstrap, topology-producing capstone actions must enter through
authenticated cpk-server HTTP or MCP. Direct Docker, database, provider,
private-service, or source-live orchestration may diagnose a failure but cannot
earn capstone acceptance.

## Stop Conditions

Stop and return to the governing issue or coordinator when:

- required authority, base, destination, ownership, suite, or prerequisite is
  ambiguous;
- current source contradicts the issue's public contract;
- a change would cross the approved owner or product boundary;
- an external effect is ambiguous or ownership is incomplete;
- an owning suite encounters apparatus failure;
- review exposes a new security, destructive, durable-data, or public-contract
  decision;
- local and GitHub state disagree about the active decision or dependency.

Do not repair, rebaseline, broaden scope, or infer approval. Record the stop
durably and wait for an explicit next boundary.
