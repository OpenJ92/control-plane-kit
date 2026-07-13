# ADR 0001: Product-Form Block Algebra

## Status

Accepted.

## Context

The package needs to describe deployable systems without hardcoding one runtime,
one application stack, or one project. The same logical topology should be able
to describe local Docker, external services, future AWS/ECS resources, and
future Kubernetes resources.

A class hierarchy such as `HttpRouterRole`, `TcpRouterRole`,
`PostgresRouterRole`, `DockerApiRole`, and `AwsApiRole` would grow quickly and
would mix unrelated axes: what a node is, how it runs, and how it communicates.

## Decision

Represent deployable blocks as product values:

```text
DeployBlock = Spec x RuntimeImplementation x BlockSockets
```

Current concrete families are:

```text
ApplicationBlock = AppSpec x RuntimeImplementation x RoleSockets
DataBlock        = DataSpec x RuntimeImplementation x RoleSockets
ProxyBlock       = ProxySpec x RuntimeImplementation x RoleSockets
```

`RoleSockets` is the current public name. The intended semantic name is
`BlockSockets`; see `AGENTS.md` for the planned vocabulary cleanup.

## Consequences

- New runtime strategies can be added by writing new implementations and
  interpreters instead of changing every block family.
- User application code remains ordinary server code. It reads env vars and
  exposes ports; it does not import `control-plane-kit`.
- Built-in blocks and user-defined applications share the same core shape.
- Validation and graph compilation can reason over pure values before any Docker,
  cloud, or process side effect occurs.

## Non-Goals

- This package is not a replacement for Docker, Kubernetes, Terraform, or a
  secret manager.
- This package does not own application business logic.
- This package does not know application domain services.
