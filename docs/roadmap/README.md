# Control Plane Kit Roadmap

Status: Draft
Last updated: 2026-07-14

This folder is the execution roadmap for `control-plane-kit`.

The architecture document explains what the package wants to become. The
roadmap explains how to get there in reviewable, testable verticals.

## How To Use This Roadmap

Each roadmap document is a source for GitHub issue topology. A roadmap item
should usually become:

```text
roadmap section
  -> parent GitHub issue
    -> ordered child issues
      -> feature branches
        -> pull requests into develop
```

Every child issue should be small enough to review, but large enough to leave
the repository in a coherent state. Avoid issue slices that only rename half a
concept or add an unused abstraction.

## Roadmap Order

The intended order is:

1. [Foundation And Naming](0001-foundation-and-naming.md)
2. [Runtime Interpreter Foundation](0002-runtime-interpreter-foundation.md)
3. [Environment And Runtime Contracts](0003-environment-and-runtime-contracts.md)
4. [Package Server Blocks](0004-package-server-blocks.md)
5. [Control Plane Backend Topology](0005-control-plane-backend-topology.md)
6. [Control Plane Read Interfaces](0006-control-plane-read-interfaces.md)
7. [Activity Sessions And Planning](0007-activity-sessions-and-planning.md)
8. [Activity Execution And Runtime Mutation](0008-activity-execution-and-runtime-mutation.md)
9. [Control Plane Instance Block And Recursive Navigation](0009-control-plane-instance-block-and-recursive-navigation.md)
10. [Visual UI, MCP, And Cross-Language Contracts](0010-visual-ui-mcp-and-cross-language-contracts.md)

This order is deliberately conservative. The package is easier to reason about
when its pure algebra is stable before runtime effects, runtime effects are
behind explicit source-of-truth and workflow boundaries, and live mutation is
planned, approved, executed, and observed as durable activity. The control-plane
instance server is then packaged as an ordinary deployable application block;
visual interfaces consume recursive navigation projections without requiring a
second Hub model.

## Definition Of Done For A Roadmap Vertical

A vertical is done when:

- the code implements the promised behavior,
- examples demonstrate the behavior in a small way,
- tests cover the important failure modes,
- documentation reflects the new public shape,
- `./test.sh` passes for code changes,
- `python3 -m compileall control_plane_kit tests` passes for code changes,
- `git diff --check` passes,
- all child issues have a handoff comment when they affect later work,
- and the parent issue has a final summary of what changed and what remains.

For documentation-only verticals, `git diff --check` is sufficient unless the
document includes executable examples that should be validated.

## Planning Rules

Use these rules when converting roadmap items into issues:

- Prefer issue titles that state the deliverable, not the activity.
- Put dependency order in the parent issue.
- Do not mix unrelated refactors with feature work.
- If a concept changes vocabulary, make that a dedicated issue.
- If a public example changes, update tests and docs in the same issue.
- If a later roadmap item depends on a decision, leave a handoff comment on the
  later issue.

## Architectural Guardrails

These constraints should survive every roadmap vertical:

- `DeploymentRecipe -> DeploymentGraph -> ActivityPlan -> Executor` remains the
  pipeline.
- Source-of-truth modules are built before workflow/session modules.
- Workflow/session modules are built before planning and execution.
- Interface adapters expose the model; they do not define the model.
- Graph construction stays close to the future UI gesture: choose nodes, connect
  sockets.
- Docker is an interpreter target, not the topology model.
- Runtime contexts are graph topology.
- Application code can remain ordinary application code.
- Live mutation is opt-in through contracts and reload policies.
- Secrets can be set and checked, not read.
- Store-local transitions use explicit Postgres unit-of-work transactions owned
  by API/application-service/use-case code; cross-boundary transitions use
  activity/saga workflows with visible partial failure.
- MCP, UI, and CLI are peer interfaces over the same control plane semantics.

## Relationship To Design Documents And ADRs

Read these before implementing a roadmap vertical:

- [Operating Model](../OPERATING_MODEL.md)
- [Architecture Design](../design/0001-control-plane-kit-architecture.md)
- [Mathematical Design Preference](../design/0002-mathematical-design-preference.md)
- [Control Plane Backend Topology Discussion](../design/0003-control-plane-backend-topology-discussion.md)
- [ADR 0001: Product Form Block Algebra](../adr/0001-product-form-block-algebra.md)
- [ADR 0002: Control Route Protocol](../adr/0002-control-route-protocol.md)
- [ADR 0003: Capability Descriptors](../adr/0003-capability-descriptors.md)
- [ADR 0004: Data-Structure-First Engineering And Data Safety](../adr/0004-data-structure-first-and-data-safety.md)
- [ADR 0005: Security Review In Every Loop](../adr/0005-security-review-in-every-loop.md)
- [ADR 0006: Activity History And Operational Observability](../adr/0006-activity-history-and-operational-observability.md)
- [ADR 0007: Operational Reliability And Examples In Every Roadmap](../adr/0007-operational-reliability-and-examples.md)
- [ADR 0008: Transactional Data Engineering Policy](../adr/0008-transactional-data-engineering-policy.md)

When a roadmap vertical changes a durable architectural decision, write or
update an ADR. Do not bury durable decisions only in pull request text.

Use these templates when creating roadmap and PR artifacts:

- [Roadmap parent issue template](../templates/roadmap-parent-issue.md)
- [Child issue template](../templates/child-issue.md)
- [PR decision log template](../templates/pr-decision-log.md)
- [Roadmap closeout template](../templates/roadmap-closeout.md)

## Roadmap Maintenance

The roadmap is allowed to change. When reality teaches us something, update the
relevant roadmap document before or during the issue vertical. The purpose is
not prediction theater. The purpose is to keep future work aligned, bounded, and
reviewable.
