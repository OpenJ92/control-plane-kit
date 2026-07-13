# Design

`control-plane-kit` is organized around one product form:

```text
DeployBlock = Spec x RuntimeImplementation x RoleSockets
```

## Spec

A spec identifies the thing being deployed. Different block families can use
different specs: `AppSpec`, `DataSpec`, `ProxySpec`. Specs should contain stable
identity and metadata, not runtime effects.

## RuntimeImplementation

An implementation says how the block exists under a runtime. Examples:

- Docker image
- local source command
- external HTTP URL
- external Postgres URL
- Docker Postgres
- plan-only router

Implementations do not own the runtime. They are interpreted by the enclosing
runtime context.

## RoleSockets

Sockets are the communication contract.

- `RoleOutputSocket`: an endpoint the node provides.
- `RoleInputSocket`: an env-backed requirement the node needs fulfilled.

A socket connection wires provider output to consumer input.

## Compiler

The compiler walks the recipe tree, materializes blocks into graph nodes, then
applies socket connections. Connections validate protocol compatibility and
write environment assignments into the consumer node.

## Graph

The compiled graph is pure data. It has nodes, edges, environment assignments,
runtime context records, and descriptors. Runtime interpreters can act on this
later.
