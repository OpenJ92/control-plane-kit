# AUTH.NODE-CONTROL Topology Freeze

Status: accepted architecture note for AUTH.NODE-CONTROL.0.

Issue: OpenJ92/control-plane-kit#1147.

## Purpose

This document freezes the laws, threat model, route prefix decision, SDK
package boundary, and adopter handoff for authenticated CPK-enabled workload
control. It is intentionally pre-implementation. Public pure contracts begin in
#1148, server SDK implementation begins later, and gateway/runtime acceptance
belongs to later issues.

## Source Dry Run

Current source facts:

- `control_routes.py` still defines `DEFAULT_CONTROL_PREFIX = "/__deploy"`.
- `SERVER_PRODUCT_ROLLOUT.md` and current architecture notes use `/__control`
  for CPK-aware workload control routes.
- `gateway_delegation.py` already defines the closed gateway access paths
  `runtime-private` and `named-public-ingress`.
- `delegation_keys.py` currently defines `DelegationKeyPurpose.GATEWAY_PROBE`.
- AUTH.0 through AUTH.2 closed the operator identity boundary.
- AUTH.GATEWAY.0 through AUTH.GATEWAY.4 closed read-only gateway probe
  language, authorization, signing, verification, and durable key lifecycle.
- AUTH.GATEWAY.5 remains open for published private/public parity and is owned
  outside #1147.

Determination: #1147 is a documentation/ADR issue with an artifact guard. It
must not implement runtime behavior, mutate Cloudflare, publish images, change
server-product coordinates, or begin #1148.

## Route Prefix Decision

Canonical future workload-control prefix:

```text
/__control
```

Bounded legacy descriptor compatibility:

```text
/__deploy
```

Compatibility law:

```text
/__deploy may remain as existing descriptor truth.
/__control is the canonical route family for newly published workload SDK routes.
No issue may silently publish two unauthenticated or divergent route families.
```

If a later migration exposes both prefixes, it must prove:

- both prefixes require the same authentication and authorization;
- both dispatch to the same semantic command;
- descriptors identify one canonical route family;
- compatibility has a named removal owner;
- route publication does not bypass workload end-to-end grants.

## Package Boundary Decision

The workload-facing SDK is a separately installable distribution:

```text
control-plane-kit-server-sdk
```

Expected repository ownership:

```text
OpenJ92/control-plane-kit-server-sdk
```

The SDK boundary:

```text
control-plane-kit-core
  pure node-control grants, commands, descriptors, codecs, and result language

control-plane-kit-operations
  trusted intent authorization, durable command evidence, replay where durable,
  and bounded result folding

control-plane-kit-server-sdk
  ControlPlaneVariable protocol, workload verification context, dispatch, atomic
  process-local implementations, and optional framework adapters

control-plane-kit-server-sdk[fastapi]
  explicit FastAPI route accrual adapter

cpk-local-gateway
  closed relay after verifying gateway transit authority

CPK-enabled workload
  end-to-end verifier and owner of mutable process-local or durable state
```

The SDK must not depend on operations stores, cpk-server process code,
interpreters, Docker, Cloudflare, server-product packages, or Postgres unless a
durable adopter explicitly owns that dependency behind its own implementation.

## Threat Model

| Boundary | Authority produced | Verifier | Must not trust | Durable owner |
| --- | --- | --- | --- | --- |
| operator or agent -> cpk-server | `AuthenticatedPrincipal` and `TrustedCommandContext` | cpk-server then operations | request payload identity, caller scopes, bearer presence | operations records actor evidence only |
| cpk-server -> gateway | gateway transit grant | cpk-local-gateway | operator bearer token, Cloudflare, Docker network locality, caller URL | operations records intent/result metadata |
| gateway -> workload | workload end-to-end grant | workload SDK verifier | successful gateway verification alone | operations records command/result evidence |
| SDK -> variable | typed transition context | `ControlPlaneVariable` implementation | arbitrary reflection, arbitrary methods, unvalidated payloads | variable owner |

Cloudflare identity, a public URL, Docker private networking, source IP, tunnel
reachability, and gateway process liveness are not workload authority.

## Grant Separation

Gateway transit grant:

- audience is a gateway or runtime-island audience;
- target is one graph-declared node/socket relay destination;
- verifier is the gateway;
- result is relay admission or denial;
- replay guarantee is bounded by gateway relay policy.

Workload end-to-end grant:

- audience is one workload node/control surface;
- target is one graph-declared node, provider socket, variable, and command;
- verifier is the workload SDK;
- result is one typed read or `ControlPlaneVariable` command outcome;
- replay guarantee belongs to the variable durability class.

Both grants bind issuer, key id, workspace, request identity, issued time,
expiry, and command identity. They must not share authority meaning merely
because they use the same signing algorithm or secret-provider custody.

## ControlPlaneVariable Extension Model

There is one public extension model:

```text
ControlPlaneVariable[State, Command, Result]
  descriptor() -> closed variable contract
  read(context) -> versioned bounded State
  apply(Command, context) -> bounded Result
```

`ControlPlaneVariable` means a named typed control-plane-visible state surface.
It is not necessarily a scalar and not necessarily process-local memory.

Responsibilities:

- SDK authenticates, decodes, bounds payloads, verifies workload grants, and
  dispatches to the named variable.
- Variable implementations validate their own commands and own their state
  transition.
- Process-local variables publish only complete validated snapshots.
- Durable variables own their UnitOfWork, revisions, leases, command ledger,
  idempotency, and restart replay.
- Core contracts do not import locks, FastAPI, Postgres, operations, or product
  code.

Rejected extension shapes:

- arbitrary object reflection;
- arbitrary method invocation;
- free-form command registration;
- a second durable domain-handler plugin interface;
- gateway-owned graph truth;
- caller URLs or endpoints inside variable state.

## Behavioral Law Cards

| Law id | Classification | Observable law | Negative cases | Future owner |
| --- | --- | --- | --- | --- |
| node-control.prefix | new-law | Newly published workload routes are canonical under `/__control`; `/__deploy` is legacy descriptor compatibility. | duplicate unauthenticated route families; silent divergent aliases | #1148 then route SDK issues |
| node-control.three-boundaries | new-law | operator, gateway transit, and workload end-to-end authority are separate. | gateway reachability grants workload command authority | #1148, #1150, #1151 |
| node-control.transit-vs-workload-grants | new-law | transit and workload grants have distinct audiences, command identities, and verifier responsibilities. | substituting a gateway probe grant for a workload command grant | #1148, #1151 |
| node-control.graph-declared-authority | strengthened | commands address only graph-declared node, socket, variable, command, and target identities. | caller URLs, arbitrary endpoints, graph-edge insertion | #1148, #1096 |
| node-control.one-variable-protocol | strengthened | one `ControlPlaneVariable[State, Command, Result]` protocol covers atomic and durable implementations. | second handler/plugin system, reflection, free-form mutation | #1148, #1149 |
| node-control.replay | strengthened | same idempotency key plus same command converges; changed command conflicts. | replay with changed intent, cross-restart claim for process-local memory | #1148, #1149, #1153 |
| node-control.bounds-redaction | strengthened | descriptors, grants, commands, results, logs, and readbacks are bounded and secret-free. | raw bearer, compact grant, signature, private key, token, private endpoint, unbounded body | #1148 through #1157 |
| node-control.atomic-publication | strengthened | process-local transition validates before publication and exposes old or new complete state only. | partial target/weight snapshot, NaN probability, unknown target | #1149, #1155, #1156 |
| node-control.durable-variable | strengthened | durable discovery remains domain-owned and transactional while implementing the same protocol. | SDK memory mirror replaces registry truth | #1152, #1153, #1154 |
| node-control.ordinary-route-isolation | isomorphic/strengthened | CPK control auth does not protect ordinary app/data routes. | application route unexpectedly requires CPK grant | #1150 |

## Adopter Matrix

| Adopter | State | Commands | Storage owner | Key laws |
| --- | --- | --- | --- | --- |
| http-active-router | active target and declared target set selected from graph identities | set active target; replace control state within graph-declared identities | SDK atomic in-process variable | no restart for switch; unknown target rejected; ordinary data route unchanged |
| weighted HTTP load balancer | one validated target plus weight snapshot | replace weights; drain; add/remove only graph-declared capacity | SDK atomic in-process variable | finite nonnegative weights; at least one positive target; deterministic normalization; no partial snapshot |
| service discovery | bounded registry projection with revisions and leases | register; heartbeat; deregister; expire; resolve | durable discovery service and UnitOfWork | one command transaction; durable replay; one-winner concurrency; restart safety |

## #1148 Handoff

#1148 should add pure provider-neutral contracts only:

- node-control command identity and closed operation;
- workload grant claims and strict codecs;
- bounded result and redacted evidence language;
- `ControlPlaneVariableDescriptor`;
- state, command, result codec identities;
- version/precondition fields;
- structural weighted snapshot validation;
- route-set and capability linkage.

#1148 should not:

- rename live `/__deploy` descriptors;
- create FastAPI route accrual;
- implement the server SDK;
- implement gateway relay;
- publish images;
- mutate Cloudflare;
- duplicate gateway key lifecycle;
- introduce product names into core.

## Security Note

New surfaces: future authenticated workload control routes.

Auth/authz: mutation requires the operator cpk-server authority, gateway
transit grant, and workload end-to-end grant to all match. None substitutes for
the others.

Secrets/redaction: private keys, bearer credentials, compact grants,
signatures, secret values, private endpoints, and unbounded bodies are excluded
from durable and public evidence.

Network exposure: public ingress is only a transport overlay. It grants no
semantic authority.

Mutation/destructive behavior: this note does not implement mutation. Later
mutations must preserve idempotency, replay, atomicity, and approval laws.

Residual risk: exact workload key-purpose naming and repository creation are
left to #1148/#1149 implementation decisions, constrained by this boundary.
