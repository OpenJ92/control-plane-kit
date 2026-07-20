# ADR 0003: Capability Descriptors

## Status

Accepted.

## Context

Not every block can do every operator action. A request logger can expose logs;
an active router can switch targets; a multiplexer can mutate observers; a
runtime-owned application node may be restartable. The control plane and future
UI need to know these powers without guessing from class names or implementation
kinds.

## Decision

Represent operator powers as typed capabilities in
`control_plane_kit.capabilities`. Capabilities are advertised by specs and are
projected into compiled node metadata.

Initial capabilities are:

```text
health-checkable -> common-status
log-readable     -> logs
target-mutable   -> targets
switchable       -> targets
drainable        -> targets
observer-mutable -> observers
metrics-readable -> no route set yet
restartable      -> no route set yet
```

Route-backed capabilities point at `ControlRouteSetName` values. Capabilities
without route sets still have meaning: for example, `restartable` may be
implemented by a runtime interpreter rather than by an in-process block route.

## Consequences

- The graph descriptor can tell an inspector or UI what controls to show.
- Blocks advertise powers explicitly.
- Capability data remains independent from any one web framework.
- Future block servers can serve `/__deploy/capabilities` from the same source
  of truth.

## Non-Goals

- Capabilities do not prove that a running service is healthy or reachable.
- Capabilities do not grant authorization by themselves. Authorization belongs
  to the control-plane/control-route layer.
