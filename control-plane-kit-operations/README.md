# control-plane-kit-operations

Durable control-plane application services for `control-plane-kit`.

This package is a sibling distribution beside `control-plane-kit-core` in the
same repository. Core owns pure deployment language and contracts. Operations
owns the durable application-service interpretation of those contracts:
workspace truth, graph versions, product registration, command services,
Postgres UnitOfWork, read projections, and the `DeploymentProgram` / `Deploy`
composition.

The initial package foundation intentionally contains only importable boundary
metadata. Postgres schema, stores, UnitOfWork, command services, and cpk-server
adapters are introduced in later EXTRACT.OPERATIONS issues.

The intended public spine remains:

```text
plan -> approve -> admit -> claim -> execute -> advance
```

with:

```text
initial deployment = Deploy(EmptyGraph, desired)
update             = Deploy(current, desired)
teardown           = Deploy(current, EmptyGraph)
no-op              = Deploy(graph, graph)
```
