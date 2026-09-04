# control-plane-kit-operations Agent Guide

Canonical contract: `cpk-agent-contract/v1`

This package inherits the repository root `AGENTS.md`,
`docs/OPERATING_MODEL.md`, and `docs/TESTING.md`. These rules tighten that
contract for durable control-plane application services and may not weaken it.

## Ownership

`control-plane-kit-operations` owns current durable control-plane truth:

```text
DeploymentProgram and operator commands
Postgres current-schema installation and contract
Postgres UnitOfWork, stores, and store bundles
authorization, approval, planning, and execution services
durable sessions, plans, requests, runs, attempts, events, and observations
read projections and cpk-server adapter contracts
```

It depends on `control-plane-kit-core` for pure values and contracts. It must
not depend on package-owned servers, FastAPI/MCP process bootstraps, Docker or
cloud interpreters, product implementations, or external provider clients.

Server routes and MCP tools compose Operations services; they do not own or
reimplement Operations semantics.

## Transaction And Effect Laws

- one operator command has an explicit caller-owned transaction boundary;
- stores never commit independently;
- current-schema installation is deterministic, non-destructive, and accepts
  only an object-free namespace or the exact current contract;
- idempotency and replay are durable Operations truth;
- no database transaction spans an external effect;
- incomplete or ambiguous effects remain uncertain until authoritative evidence
  resolves them;
- secrets and provider response bodies are not durable Operations records.

Schema/reset policy changes require their own reviewed data decision; do not
smuggle migration or repair behavior into feature work.

## Tests And Prerequisites

Run executable validation only through:

```bash
./control-plane-kit-operations/test.sh
```

The suite owns Python, PostgreSQL, dependencies, installation, and cleanup in
Docker. It requires the exact clean sibling
`control-plane-kit-architecture-testing` checkout declared by the harness and
used by CI. Establish that checkout before invocation; mount it read-only as the
suite does. Do not install it into host Python or substitute a custom runner or
database.

Operations tests prove durable records, transaction boundaries, policy,
planning/execution, idempotency, history, and projections. They should not
duplicate Core algebra or server route/authentication behavior.

If the suite or sibling prerequisite is missing, Docker cannot start, or setup
fails before behavioral collection, stop and report apparatus without retry or
workaround.

## Package Stop Conditions

Stop when a proposed Operations change would:

- alter Core deployment algebra instead of consuming it;
- move durable truth into a server route, cache, or provider adapter;
- add an external provider call inside a transaction;
- imply automatic retry, compensation, cleanup, or adoption without explicit
  product authority;
- change schema/reset policy outside an issue that owns that data decision.
