# control-plane-kit-operations

`control-plane-kit-operations` is the durable application layer of Control Plane
Kit. It interprets the pure contracts from `control-plane-kit-core` through
Postgres-backed command services, read models, approval and lifecycle workflows,
and an execution coordinator.

It is not a hosted API process and it does not contain concrete Docker,
Cloudflare, or secret-provider SDKs. `cpk-server` composes these services behind
HTTP and MCP; external interpreters perform runtime IO.

The package is pre-alpha. Its durable boundaries and tests are substantial, but
recovery, fencing, and executable-program hardening remain active roadmap work.

## Operator Program

Operations owns the durable spine:

```text
Deploy(current, desired)
  -> plan
    -> request and decide approval
      -> admit
        -> claim
          -> start
            -> execute
              -> observe
                -> advance current graph
```

This one program shape covers:

```text
initial deployment = Deploy(EmptyGraph, desired)
update             = Deploy(current, desired)
teardown           = Deploy(current, EmptyGraph)
no-op              = Deploy(graph, graph)
```

Current graph advancement is explicit. A desired graph or runtime observation
cannot silently become current truth; advancement requires accepted completed
realization evidence.

## Durable Truth

The Postgres store bundle owns operational records for:

- workspaces and current/desired graph pointers;
- authored and realized graph versions;
- registered products and image-pull authorities;
- registered runtime, ingress, and secret-provider authorities;
- operation sessions and ordered actions;
- activity plans, approvals, execution requests, runs, and journals;
- observations and bounded runtime evidence;
- generated ingress resources and secret references;
- gateway probe and delegation-key lifecycle state.

Stores expose persistence operations but never commit independently.

## Transaction Law

```text
one operator command = one explicit Postgres transaction
```

Application command services own commit and rollback. Every participating store
uses the same `PostgresUnitOfWork` connection. External effects are deliberately
split from those transactions:

```text
short transaction: record durable intent
  -> commit
    -> bounded Docker / Cloudflare / HTTP / secret-provider IO
      -> short transaction: record result, event, and observation
```

No Postgres transaction or lock may span Docker, filesystem, provider, network,
health, or other external IO.

## Execution Boundary

The coordinator receives an operations-owned realization context and emits a
pure runtime request:

```text
ExecutionCoordinator
  -> RuntimeInterpreterDispatcher protocol
    -> RuntimeEffectRequest
      -> external interpreter
        -> RuntimeEffectResult
          -> operations folds durable result and observation
```

Operations defines the dispatcher protocols and application sequencing. The
Python Docker SDK and Cloudflare client remain in
`control-plane-kit-interpreters`.

## Registrations And Authorities

Workspace-scoped registration services admit bounded metadata for:

- product descriptors;
- OCI image-pull authority;
- runtime authority and explicit authority delivery;
- named-ingress authority;
- secret-provider authority;
- delegation signing keys.

Registration permission, authority use, graph execution, approval, and
destructive operations remain distinct scopes. Operations persists references
and audit correlation, never raw credentials or secret ciphertext.

## cpk-server Boundary

`control_plane_kit_operations.cpk_server` projects the same command and read
services to HTTP- and MCP-shaped requests. It is framework-neutral application
composition, not a FastAPI or MCP server implementation.

```text
authenticated HTTP or MCP request
  -> trusted principal and workspace grant
    -> CpkServerOperationsApplication
      -> operations command/read service
        -> Postgres UnitOfWork
```

HTTP and MCP must enforce the same operation identity, scopes, approval policy,
idempotency policy, bounded errors, and transaction behavior.

## Package Map

Important modules include:

```text
workspaces.py              workspace lifecycle
graph_authoring.py         desired graph commands
planning.py                plan and desired-graph services
approvals.py               approval request/decision workflow
admission.py               execution admission
lifecycle.py               claim/start/pause/resume/complete/fail
coordinator.py             external-effect dispatch and result folding
advancement.py             guarded current graph advancement
read_services.py           bounded read projections
products.py                product and image-pull registration
runtime_authorities.py     runtime authority registration/delivery
ingress_authorities.py     ingress authority and owned-resource evidence
secret_providers.py        provider metadata and use authorization
gateway_probes.py          authenticated local-island probe workflow
postgres/                  schema, stores, and PostgresUnitOfWork
cpk_server.py              shared HTTP/MCP-shaped application boundary
```

## Installation

From this repository checkout:

```bash
python -m pip install ./control-plane-kit-core ./control-plane-kit-operations
```

## Validation

Project validation is Docker-first and uses `unittest`:

```bash
./control-plane-kit-operations/test.sh
```

The package gate starts an owned Postgres test dependency, runs the complete
current operations suite, verifies imports and compilation, and cleans only its
owned resources.

For exact cross-repository composition and authenticated cpk-server HTTP/MCP
source-live acceptance, run from the repository root:

```bash
./current-backend-test.sh --report /tmp/current-backend-report.json
```

That gate is source-built and non-provider-mutating. Published-image and
provider-mutating acceptance remain separate evidence classes.

## Documentation

- [Repository overview](../README.md)
- [Control Plane Language](../docs/CONTROL_PLANE_LANGUAGE.md)
- [Operating Model](../docs/OPERATING_MODEL.md)
- [Postgres Unit Of Work](../docs/POSTGRES_UNIT_OF_WORK.md)
- [Test Evidence And Acceptance](../docs/TESTING.md)
