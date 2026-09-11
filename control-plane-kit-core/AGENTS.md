# control-plane-kit-core Agent Guide

Canonical contract: `cpk-agent-contract/v1`

This package inherits the repository root `AGENTS.md`,
`docs/OPERATING_MODEL.md`, and `docs/TESTING.md`. These rules tighten that
contract for the pure deployment kernel and may not weaken it.

## Ownership

`control-plane-kit-core` owns pure deployment language and transformations:

```text
DeploymentTopology
  -> DeploymentGraph
  -> ValidatedGraph
  -> GraphDiff
  -> ActivityPlan
```

It owns public algebra, graph validation and diff, planning values, policies
that are pure over those values, and stable descriptors.

It does not own Postgres stores, durable execution, HTTP/MCP servers, Docker or
cloud interpreters, package-owned products, provider truth, or live effects.
It must not import `control_plane_kit_operations` or external CPK repositories.

## Implementation companions

Core companions live at
`docs/implementation/<repository-relative-source-path>.md`, preserving the
source suffix. Before design or implementation, read the relevant companion,
verify its owner source and inspect contract-bearing imports. The notes explain
pure values, transformations, rejection laws and consumer obligations; they do
not substitute for source or grant authority to execute effects.

Create, move or remove companions with covered source files, including concise
test navigation. Update changed meaning in the same PR. When a selected
dependency changes, verify the contract actually adopted and search affected
consumers; leave cross-repository handoffs rather than claiming atomic adoption.
Before handoff, inspect `git diff --name-status` against the PR base and record
companion updates or review/no semantic change in the existing decision log.
Reviewers compare the actual diff and check consequential claims against source.

The initial [Core coverage inventory](../docs/implementation/control-plane-kit-core/coverage.md)
distinguishes pending, authored, reviewed and excluded files; it is not a
permanent freshness ledger or a complete behavioral audit. Missing initial
coverage is backlog, not a global development stop. Bring newly touched files
and their notes current. Do not add hash/timestamp churn, generic placeholders
or a new correspondence framework.

## Tests

Run executable validation only through:

```bash
./control-plane-kit-core/test.sh
```

Use standard-library `unittest` through that Docker-backed suite. Tests prove
the public pure boundary and algebraic laws; they do not inspect helper names or
source layout. Migration parity/law-card work applies only when the governing
issue explicitly requires it.

Stop if the suite or its documented image prerequisite is unavailable. Do not
create a host Python environment or custom replacement harness.

## Package Stop Conditions

Stop when a proposed Core change would persist state, perform I/O, call a
provider, import Operations, encode an application/product name, or invent
durable execution semantics. Move that decision to its owning package issue.
