# Deployment Program Direction

The current extracted backend implements the durable deployment spine as
operations-owned services:

```text
set desired graph
  -> request plan
    -> request and decide approval
      -> admit execution
        -> claim and start run
          -> execute activities
            -> advance current graph
```

These are not one in-memory transaction. Plans, approvals, execution requests,
runs, events, observations, and graph pointers are durable Postgres facts. Each
operator command owns one short UnitOfWork. Runtime, provider, filesystem,
network, and health effects occur outside those transactions.

## Current Ownership

```text
control_plane_kit_core
  pure graph, plan, approval subject, lifecycle, and runtime-effect language

control_plane_kit_operations
  command services, stores, UnitOfWork, coordinator, read models, and cpk-server
  application adapters

cpk-server
  authenticated HTTP/MCP process wrapper over the operations application

control_plane_kit_interpreters
  concrete RuntimeEffectRequest -> IO RuntimeEffectResult implementations
```

The public process proves the full command sequence through cpk-server. HTTP and
MCP translate into the same `CpkServerOperationsApplication`; neither transport
owns deployment policy.

## One Transition Model

Every deployment remains movement between two graph values:

```text
initial deployment = Deploy(empty, desired)
update             = Deploy(current, desired)
teardown           = Deploy(current, empty)
no-op              = Deploy(current, current)
```

The graph diff compiles activities. Successful accepted realization is required
before current graph advancement. Observed runtime state never rewrites desired
graph truth.

## Executable Program Handoff

Issue #1096 owns the remaining general composition: an operations-owned,
resumable `DeploymentProgram` that invokes the existing services as one durable
operator workflow. Until that issue closes, hosted controllers may invoke the
public commands, but documentation must not claim that the former aggregate
`control_plane_kit.application.deploy.DeploymentProgram` remains a current API.

Specialized operations programs, such as gateway delegation-key rotation, are
current examples of the intended composition style. They do not replace the
future generic deployment program.

## Governing Laws

- approval cannot be bypassed;
- admission uses current approval and current graph evidence;
- one operator command owns one explicit Postgres transaction;
- no transaction spans an external effect;
- identical idempotent commands replay exact durable results;
- terminal sessions publish no later nonterminal actions;
- current graph advances only after accepted completed realization;
- uncertain external effects block and hand off to recovery/fencing work.
