# ADR 0010: Node-Control Route Prefix And Server SDK Boundary

## Status

Accepted for AUTH.NODE-CONTROL.0.

## Context

The live core control-route descriptor language still uses the historical
default prefix:

```text
/__deploy
```

That prefix is already present in `control_routes.py` and in current successor
tests. It is existing descriptor truth and should not be renamed inside
AUTH.NODE-CONTROL.0.

Newer rollout and architecture documents use the clearer workload-control
prefix:

```text
/__control
```

AUTH.NODE-CONTROL introduces a different public surface: CPK-enabled workload
servers that accrue authenticated control routes without becoming `cpk-server`.
This lane also introduces a reusable server SDK boundary and one
`ControlPlaneVariable[State, Command, Result]` extension model.

## Decision

Canonical newly published CPK-enabled workload control routes use:

```text
/__control
```

The existing `/__deploy` descriptors remain bounded legacy compatibility until
a dedicated migration issue changes the executable route descriptor surface.
AUTH.NODE-CONTROL.0 does not rename live routes, publish compatibility aliases,
or claim that both route families are active.

Compatibility is allowed only when all of these are true:

- the compatibility surface is explicitly authenticated;
- both prefixes dispatch to the same semantic command;
- route descriptors and docs identify one canonical route family;
- duplicate publication cannot create an unauthenticated bypass;
- removal is assigned to a named migration issue.

The reusable workload-facing SDK is a separately installable package:

```text
control-plane-kit-server-sdk
```

The expected repository is:

```text
OpenJ92/control-plane-kit-server-sdk
```

The exact repository creation and scaffolding remain the implementation work of
the later SDK issue. This ADR fixes the ownership boundary: application authors
must not need `control-plane-kit-operations`, `cpk-server`, Docker
interpreters, Postgres stores, Cloudflare clients, or server-product packages
to expose CPK workload routes.

The SDK owns:

- framework-neutral `ControlPlaneVariable` protocols and request context;
- verification and dispatch helpers for workload end-to-end authority;
- atomic process-local variable implementations;
- optional FastAPI route accrual through an optional dependency extra.

Core owns only provider-neutral pure contracts. Operations owns authorization,
durable command intent, durable replay where applicable, and result evidence.
The gateway owns only a closed relay after verifying its transit grant. A
workload owns its own process-local or durable state.

## Grant Boundary

Gateway transit authority and workload end-to-end authority are distinct.

```text
gateway transit grant
  authorizes one gateway relay to one graph-declared node/socket target

workload end-to-end grant
  authorizes one semantic ControlPlaneVariable read or command
```

The two grants may share durable key lifecycle machinery, but they must not
share an audience, command identity, or authority meaning by accident.
`DelegationKeyPurpose.GATEWAY_PROBE` remains the read-only probe transit
purpose. A later contract issue may add a workload-control purpose or reuse a
more general purpose only through explicit tests.

## ControlPlaneVariable Boundary

The extension model is exactly one public design:

```text
ControlPlaneVariable[State, Command, Result]
  descriptor() -> closed variable contract
  read(context) -> versioned bounded State
  apply(Command, context) -> bounded Result
```

Process-local router and balancer variables use SDK-provided atomic
implementations. Durable discovery implements the same protocol by delegating
to its domain service, UnitOfWork, revisions, leases, and command ledger. There
is no second arbitrary domain-handler, plugin, reflection, method-call, or
free-form mutation system.

## Consequences

- `/__control` is the public direction for CPK-aware workload SDK routes.
- `/__deploy` remains live descriptor history until a focused migration changes
  it.
- AUTH.NODE-CONTROL.0 is documentation and guard evidence, not runtime
  implementation.
- AUTH.NODE-CONTROL.1 starts from the existing core route, capability, gateway
  grant, trusted identity, and delegation-key vocabulary.
- Later issues must keep graph-declared node/socket/variable/command authority,
  replay, bounds, secret exclusion, and transaction boundaries explicit.
