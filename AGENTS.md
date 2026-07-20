# control-plane-kit Agent Guide

`control-plane-kit` is a generic Python package for describing deployable
systems as topology values. It must remain independent from any one application
or product repository.

Read `docs/OPERATING_MODEL.md` for the compressed working loop. Use the longer
roadmap, ADR, and design documents as references for the current vertical.

## Branch Flow

Use this flow for normal feature work:

```text
develop
  -> codex/<issue-or-feature>
      -> PR into develop
```

Promote `develop` to `main` only after a coherent vertical is reviewed. Do not
open feature PRs directly into `main` unless the user explicitly asks for a
one-off patch.

For roadmap-driven work, prefer a roadmap integration branch:

```text
main
  develop
    roadmap/<roadmap-id>-<slug>
      codex/<issue-id>-<slug>
      codex/<issue-id>-<slug>
      codex/<issue-id>-<slug>
```

Child issue branches should target the roadmap branch when they are part of a
dependent roadmap vertical:

```text
codex/<issue-id>-<slug>
  -> roadmap/<roadmap-id>-<slug>
```

The roadmap branch should then target `develop`:

```text
roadmap/<roadmap-id>-<slug>
  -> develop
```

This gives three review levels:

```text
child PR
  small implementation slice

roadmap PR
  whole vertical coherence

develop
  completed roadmap nodes only
```

Open the roadmap PR as a draft early so it becomes the visible container for
the vertical. Merge child PRs into the roadmap branch in topological order. Do
not merge the roadmap branch into `develop` until the roadmap parent acceptance
criteria pass.

If a child issue is genuinely independent and useful on its own, it may target
`develop` directly. Otherwise, preserve the roadmap topology.

## Collaboration Model

This repository is intended to support architecture-led development. The user
may primarily engage with roadmap, design, issue topology, PR summaries, and
decision logs rather than line-by-line implementation.

The agent is expected to preserve that interface. Do not force the user to
review implementation detail as the only way to understand progress. Every
meaningful implementation slice should produce a small human-readable account
of:

- what changed,
- why that shape was chosen,
- what alternatives were rejected,
- which code snippets reveal the implementation shape,
- what tests protect it,
- what remains risky,
- and what the next issue needs to know.

The code is still the truth, but the roadmap, issues, PRs, and handoffs are the
user-facing control surface.

## Recursive Roadmap Loop

Roadmap documents are the outer topology. Issue topologies are the inner
topology.

When the user asks to execute a roadmap item, use this outer loop:

```text
1. Select one roadmap node.
2. Read the roadmap node, architecture doc, relevant ADRs, and current source.
3. Produce or update a parent GitHub issue for that roadmap node.
4. Break the roadmap node into ordered child issues.
5. Ensure each child issue is reviewable and independently meaningful.
6. Execute child issues in topological order.
7. Create one PR per child issue unless the user explicitly asks for a larger
   batch.
8. Review the PR before moving on.
9. Run explicit hardening passes when the roadmap surface is broad enough to
   warrant them.
10. Leave handoff comments from completed child issues to dependent child issues.
11. Summarize the roadmap node on the parent issue when complete.
12. Move to the next roadmap node only after the current node is coherent on
    develop.
```

This is recursive. If a child issue is still too large, split it into a smaller
issue topology before implementing. Do not push through an issue that has become
too broad to review.

### Milestone Review Gates

A broad roadmap that crosses qualitatively different architectural boundaries
must define mandatory milestone review gates in its roadmap learning document.
Examples include moving from pure values to persistence, from persistence to
external effects, from fake effects to real runtime adapters, or from happy-path
execution to compensation and destructive behavior.

Milestone gates are internal stops on the existing roadmap branch. They are not
releases, new roadmap branches, or substitutes for per-issue review. At each
declared gate, stop the issue chain and:

1. run the gate's full validation;
2. perform the specified architecture, security, data-engineering, and test-
   integrity reviews;
3. update the roadmap learning document with implementation truth;
4. report the gate result to the user; and
5. proceed only when the next milestone is genuinely unblocked.

Every milestone report must state:

- what capability now exists;
- which objects and transformations were introduced;
- which laws are executable as tests;
- what review found and what was fixed;
- what remains provisional;
- which security and data risks remain;
- whether implementation deviated from the roadmap; and
- whether the next milestone is safe to begin.

Do not accumulate work across a declared gate merely because later issues are
already specified. If a gate exposes an unresolved architectural, security,
data, destructive-operation, or test-integrity decision, stop for the user.

Hardening is part of the roadmap loop, not an afterthought. A hardening pass
should become its own issue and PR when an implementation creates a broad
interface, persistence, security, runtime, or adapter surface. Prefer
per-issue or per-module hardening PRs over one vague final cleanup. The normal
shape is:

```text
child issue implementation PR
  -> review pass
    -> hardening pass PR
      -> final roadmap integration validation
```

Hardening passes should look for:

- boundary cases and negative cases that happy-path tests missed;
- inconsistent behavior across adapters over the same service;
- missing source-of-truth validation;
- security leaks, secret leaks, address leaks, or unbounded payloads;
- transaction or unit-of-work drift;
- runtime cleanup/retry/idempotency gaps;
- descriptor instability;
- and documentation or handoff claims that are no longer true.

Record learning from substantial roadmap runs under:

```text
docs/learning/<roadmap-id>/run-<nnnn>.md
```

Those documents are the memory layer for future roadmap executions. Read the
relevant learning documents before retrying or extending a roadmap.

## Issue Execution Loop

For each child issue, use this inner loop:

```text
1. Re-read the child issue and parent roadmap issue.
2. Inspect the relevant source before planning.
3. State the implementation plan in the issue or PR when useful.
4. Create a feature branch from develop.
5. Implement the smallest coherent vertical.
6. Add or update tests.
7. Update examples/docs when public behavior changes.
8. Run validation.
9. Open a PR into develop.
10. Perform a code-review pass on the PR.
11. Add a hardening PR when review shows the implementation surface needs
    additional edge tests, security checks, data checks, or adapter consistency
    checks.
12. Leave a PR decision log.
13. Merge only when checks pass and the result is coherent.
14. Leave handoff comments for dependent issues.
```

The issue loop should preserve topological order. If issue B depends on a
concept, descriptor, public name, or runtime behavior from issue A, issue A must
be merged first unless the user explicitly asks for stacked branches.

## PR Decision Log

Every non-trivial PR should include a concise decision log for the user. This is
the primary way the user can engage without reading every line of code.

Use this shape in the PR body or a PR comment:

```text
Decision log

- Chosen shape:
  ...
- Important snippets:
  ...
- Why:
  ...
- Alternatives considered:
  ...
- Tests:
  ...
- Risks:
  ...
- Handoff:
  ...
```

Keep the log honest. If an implementation is provisional, say so. If an
abstraction is intentionally small, say what would force it to grow.

Important snippets should be curated, not exhaustive. Include the smallest code
fragments that let the user understand the new shape:

- public type definitions,
- constructor or factory examples,
- interpreter entry points,
- descriptor shapes,
- control route handlers,
- activity plan examples,
- or the key test that locks behavior.

Do not paste entire files. Link to files and line numbers when possible, then
show only the snippet that carries the architectural idea.

## Mathematical Design Notes

Read `docs/design/0002-mathematical-design-preference.md` before work that
changes public algebra, graph shape, descriptors, contracts, interpreters,
validators, activity planning, or examples that teach the model.

The user's preferred frame is mathematical and interpreter-oriented. When a PR
touches that frame, add a short optional section to the PR decision log:

```text
Mathematical design note

- Objects:
  ...
- Transformations:
  ...
- Laws/invariants:
  ...
- Valid compositions:
  ...
- Interpreter boundary:
  ...
```

Use this section to curate the structure, not to perform ceremony. Skip it for
trivial mechanical changes. Include it when it helps the user understand what
the code means as a structure.

## Review Pass

Before merging a PR, do a review pass from a code-review stance:

- correctness,
- public API clarity,
- consistency with roadmap/design/ADRs,
- tests,
- security,
- secret redaction,
- runtime cleanup,
- descriptor stability,
- and future issue handoff.

## Module Ownership Law

Read `docs/adr/0009-package-boundary-topology.md` before changing package
ownership, import direction, root exports, product declarations, domain
languages, interpreters, or process entrypoints. Keep
`docs/architecture/package-module-inventory.json` exhaustive as modules move.

Preserve this package ownership vocabulary:

```text
core         owns the deployment language
domains      own independent closed languages
operations   own durable control-plane truth
interpreters perform representation and external effects
products     are graph-visible deployable values
entrypoints  compose dependencies and run processes
```

The observed package graph must remain acyclic without migration allowances.
An inventory destination records canonical ownership; it does not authorize a
duplicate implementation or compatibility facade. Package-owned servers share
the `products.servers` exterior even when their domain, operation, interpreter,
or entrypoint interiors differ.

Backend modules should be separable enough that a future service boundary is
obvious.  Use this source-of-truth order when adding backend behavior:

```text
stores
  own durable facts and valid mutations

workflows
  own grouped operator intent and session state

policies
  return authorization/approval/destructive-action decisions

planners
  interpret current truth plus desired intent into activity values

effects
  call runtime providers or block control routes after approval

projections/interfaces
  expose read or command surfaces without defining core semantics
```

Review every backend PR with these questions:

- What truth does this own?
- What intent does this record?
- What capability does this call?
- What projection does this expose?
- Is it accidentally owning another module's truth?

If a module might become an HTTP, MCP, or worker boundary later, keep its
service contract visible now.  Do not let route handlers, CLI commands, or UI
payloads become the place where durable semantics are invented.

If the PR is documentation-only, review for:

- architectural consistency,
- stale terminology,
- broken links,
- issue-readiness,
- and whether the text gives future work enough traction.

## Splitting Criteria

Split an issue when any of these become true:

- it changes multiple public concepts at once,
- it mixes vocabulary refactor with runtime behavior,
- it needs more than one independent PR decision log,
- tests naturally fall into unrelated groups,
- a later issue cannot receive a clean handoff,
- the implementation plan cannot be summarized in a few bullets,
- or review would require holding too much state in memory.

Prefer smaller issue topology over heroic PRs.

## User-Facing Progress Fragments

When moving through roadmap work, report progress at the level the user can use:

- roadmap node selected,
- issue topology created or updated,
- current child issue,
- PR opened,
- decision log summary,
- validation result,
- handoff left,
- roadmap node complete.

Avoid flooding the user with low-level implementation narration unless they ask
for a walkthrough. The durable artifacts should carry the detail.

## Validation

Run the narrowest useful validation before opening a PR. For code changes, use:

```bash
./test.sh
python3 -m compileall control_plane_kit tests
git diff --check
```

For documentation-only changes, `git diff --check` is sufficient unless the docs
include executable examples that should be run.

## Design Constraints

- Keep the package generic. Do not import application repositories or encode
  application-specific service names in core modules.
- Application code must not import this package. Applications expose ports and
  read URLs, connection strings, or TCP addresses from environment variables.
- The core model is algebraic data. Prefer product values and interpreters over
  deep inheritance trees.
- Blocks describe topology. Runtime implementations and runtime contexts
  interpret that topology into effects.
- The graph owns nodes, sockets, edges, environment assignments, runtime records,
  descriptors, and activity planning inputs. Runtime interpreters own processes,
  containers, cloud resources, and side effects.
- Control routes are protocol data first. FastAPI, ASGI, Docker, Kubernetes, or
  cloud interpreters may implement them later.
- Capabilities are advertised powers. They should be explicit and optional, not
  inferred from block class names.

## Data Engineering Safety

Read ADR 0004 before any work that touches persistence, durable runtime state,
transactions, migrations, secrets, or live data movement.

Data-affecting work is a higher-care class of change. Preserve the package's
interpreter-oriented style:

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

Do not hide durable mutation inside an opaque helper, runtime interpreter, or
generic `apply` function. If an operation can create, delete, migrate, rewrite,
or re-point durable data, produce an inspectable plan first.

For data-affecting PRs, the decision log must additionally answer:

- what durable state can change,
- where the transaction boundary is,
- how retries behave,
- how idempotency is enforced,
- what verifies success,
- what rollback or compensation exists,
- what happens under concurrent use,
- and what sensitive values are redacted.

If these answers are unclear, stop and split the issue or write the plan before
touching implementation.

## Security Review In Every Loop

Read ADR 0005 before work that touches routes, auth, secrets, descriptors, logs,
network exposure, MCP tools, runtime mutation, activity execution, dependencies,
or request forwarding.

Every roadmap node, child issue, PR, and handoff must include an explicit
security note. If there is no new security surface, say so plainly.

For PR decision logs, include:

```text
Security

- New surfaces:
  ...
- Auth/authz:
  ...
- Secrets/redaction:
  ...
- Network exposure:
  ...
- Mutation/destructive behavior:
  ...
- Tests:
  ...
- Residual risk:
  ...
```

Default posture:

- mutation requires authentication,
- read-only mode is explicit,
- secrets are never returned,
- logs are bounded,
- descriptors are redacted,
- private Docker networking is not security,
- MCP mutation tools are separated from read-only tools,
- destructive activity requires approval,
- external network exposure must be named in docs/PRs,
- and package examples should model safe defaults.

Do not make security implicit. Surface assumptions, even when they feel obvious.

## Activity History And Operational Observability

Read ADR 0006 before work that touches the control-plane/home server, graph
mutation, activity planning, execution, MCP mutation, UI-facing state, runtime
mutation, logs, events, or operational status.

Control-plane work should preserve structured activity history. Do not rely on
process logs as the only explanation of what happened.

When relevant, PR decision logs should include:

```text
Operational history

- Session/action records:
  ...
- Plans or graph snapshots:
  ...
- Events emitted:
  ...
- Query surfaces:
  ...
- Partial failure story:
  ...
- Retry/resume story:
  ...
- Retention/cleanup:
  ...
```

Default posture:

- user/system intent becomes an operation session or action,
- generated plans are inspectable before execution,
- execution attempts produce activity runs,
- execution emits bounded structured events,
- observed state is queryable afterward,
- logs supplement structured history rather than replacing it,
- and secrets are redacted from all history.

## Operational Reliability And Examples

Read ADR 0007 before completing any roadmap vertical and before work that
affects runtime behavior, health/status, logs, events, cleanup, retries, or
examples.

At the end of each roadmap branch, perform an examples and operational
reliability checkpoint:

```text
Operational reliability

- Health/status:
  ...
- Logs/events:
  ...
- Failure modes:
  ...
- Cleanup:
  ...
- Retry/resume:
  ...
- Examples added or updated:
  ...
- Examples still missing:
  ...
```

Examples should accrue as the package grows. Prefer a ladder of examples:

```text
tiny example
  teaches one object or law

composition example
  shows multiple concepts together

runtime smoke example
  proves interpretation works

roadmap capstone example
  demonstrates the vertical coherently
```

If a new abstraction is difficult to explain, write or update an example before
expanding the abstraction.

## Vocabulary

Current endpoint socket names use the intended provider/requirement vocabulary:

```text
RequirementSocket
ProviderSocket
```

The container still uses the older `BlockSockets` name until Roadmap 0001.3:

```text
BlockSockets
```

The intended semantic vocabulary is:

```text
RequirementSocket: an env-backed requirement needing a provider value.
ProviderSocket: an endpoint/value exposed for other blocks to consume.
BlockSockets: the communication boundary of one block.
```

Do not perform the `BlockSockets` -> `BlockSockets` rename opportunistically
inside unrelated issues. If we rename it, do it as the dedicated Roadmap 0001.3
refactor before more server block APIs depend on the current name.

## Issue Handoff

For issue topology work, leave a short handoff comment when a child issue changes
what the next child should know. Keep handoffs concrete: files touched, decisions
made, tests added, and remaining risks.
