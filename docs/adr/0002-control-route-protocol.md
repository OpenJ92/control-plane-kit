# ADR 0002: Control Route Protocol

## Status

Accepted.

## Context

Some deployable blocks are not passive topology descriptions. Routers,
multiplexers, request loggers, load balancers, and future TCP/Postgres blocks
need a private control surface so the control plane can inspect or mutate their
runtime state.

This control surface must be separate from application traffic. Application code
should not learn that deploy exists, and control-plane requests should not be
smuggled through ordinary domain APIs.

## Decision

Represent control-plane routes as typed data in `control_plane_kit.control_routes`.
The default protocol prefix is:

```text
/__deploy
```

The initial route sets are:

```text
common-status
  GET /__deploy/capabilities
  GET /__deploy/health
  GET /__deploy/status

logs
  GET /__deploy/logs

targets
  GET  /__deploy/targets
  POST /__deploy/targets
  POST /__deploy/active-target
  POST /__deploy/drain-target

observers
  GET  /__deploy/observers
  POST /__deploy/observers
```

Each route carries a method, path, authorization scope, name, and description.

## Consequences

- FastAPI server adapters can implement the route sets without owning the route
  contract.
- UI surfaces can render available actions from descriptors.
- Authorization can reason over route scopes consistently.
- Blocks can expose live adjacency concepts such as targets and observers while
  the compiled graph remains the desired topology.

## Open Questions

- Whether the protocol prefix should become a package-level configuration object
  instead of a constant.
- Whether future metrics/configuration routes should be separate route sets or
  extensions of common status.
