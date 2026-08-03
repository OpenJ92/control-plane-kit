# control-plane-kit-operations Agent Guide

`control-plane-kit-operations` is the durable control-plane application
services distribution for CPK.

It depends on `control-plane-kit-core` for pure values and contracts. It must
not depend on `control-plane-kit-servers`, package-owned product
implementations, FastAPI/MCP process bootstraps, Docker runtimes, or cloud
runtime interpreters.

The package owns the future operations layer:

```text
DeploymentProgram / Deploy
Postgres schema installation
Postgres UnitOfWork
stores and store bundles
command services
read projections
durable product registration
```

The package does not own runnable process packaging. `cpk-server` composes this
package later from `control-plane-kit-servers`.

Preserve the data-engineering laws from the repository root `AGENTS.md`,
especially:

```text
one operator command = one explicit Postgres transaction
stores never commit independently
schema installation is idempotent and non-destructive
no transaction spans an external effect
```
