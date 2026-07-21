# EXTRACT.D Core Services And cpk-server Boundary - Run 0001

## Scope

EXTRACT.D defines the extracted core control-plane application service surface
and the exact handoff contract for the future
`control-plane-kit-servers/cpk-server` product.

It does not package `cpk-server` inside core.

```text
control-plane-kit-core
  owns generic control-plane application services
  owns HTTP/MCP contract language
  owns service-level parity laws

control-plane-kit-servers/cpk-server
  owns FastAPI/MCP process composition
  owns Dockerfile and OCI image
  owns product descriptor
  owns live process publication evidence
```

## D.0 Result

#630 confirms that the refreshed D.0-D.5 topology is coherent and executable.
The remaining children each have one phase owner and no remaining child
requires core to build the canonical `cpk-server` process, Dockerfile, OCI
image, hosted MCP process, FastAPI app, or product descriptor.

## Phase Topology

```text
D.0 Topology Refresh
  #630

D.1 Core Application Service Composition
  #631 -> #632

D.2 Core HTTP/MCP Contract Language
  #633 -> #634 -> #635

D.3 Core Parity Laws
  #632 + #634 -> #636 -> #637 -> #638

D.4 cpk-server Product Handoff Contract
  #636 + #637 + #638 -> #639 -> #640 -> #641

D.5 Mandatory Stop And Closeout
  #641 -> #642
```

## Child Classification

| Issue | Classification | Why |
| --- | --- | --- |
| #631 | Core application service composition | Frozen workflows show generic planning, approval, admission, lifecycle, execution, recovery, observation, and read-service boundaries. These are reusable control-plane semantics. |
| #632 | Core application service composition | Frozen stores and command services prove that UnitOfWork, store participation, workers, runtime authority, and transaction laws must be explicit before transport contracts use them. |
| #633 | Core HTTP/MCP contract language | MCP Streamable HTTP belongs in the typed protocol/endpoint language, but the hosted MCP server is process packaging. |
| #634 | Core HTTP/MCP contract language | HTTP routes, request/response shapes, auth scopes, and errors are contracts over services, not FastAPI process ownership. |
| #635 | Core HTTP/MCP contract language | Readiness, liveness, verification, observation, shutdown, and retained-data behavior are contracts the future process must satisfy. |
| #636 | Core parity law | HTTP and MCP must share command vocabulary and projections before either transport wrapper is implemented. |
| #637 | Core parity law | Transaction, idempotency, and approval behavior must be service laws, not transport-specific behavior. |
| #638 | Core parity law | Authorization, destructive-command classification, and activity history must be identical across transports. |
| #639 | cpk-server handoff contract | Process entrypoint composition belongs to `control-plane-kit-servers/cpk-server`; core only defines what must be composed. |
| #640 | cpk-server handoff contract | Environment, secret, configuration, and descriptor obligations are the product wrapper contract. |
| #641 | cpk-server handoff contract | OCI image, publication, health, live smoke, cleanup, and retained-data evidence belong to the process wrapper. |
| #642 | Mandatory stop and closeout | Close EXTRACT.D and stop before process packaging starts. |

## Frozen Source Classification

| Frozen source | Future owner | Classification |
| --- | --- | --- |
| `control_plane_kit/workflows/*.py` | core | Generic application command services and service commands. |
| `control_plane_kit/stores/*.py` | core | Durable records, store protocols, Postgres UnitOfWork, and transaction boundaries. |
| `control_plane_kit/read_services/*.py` | core | Canonical redacted read projections. |
| `control_plane_kit/execution/*.py` | core | Typed execution values, records, observations, recovery, and codecs. |
| `control_plane_kit/mcp_read.py` | core contract vocabulary | Transport-neutral MCP-shaped tool vocabulary; not a hosted MCP server. |
| `control_plane_kit/servers/instance_read.py` | cpk-server handoff input | FastAPI read adapter pattern; useful as frozen evidence, but process adapter implementation belongs to `cpk-server`. |
| `control_plane_kit/servers/_fastapi.py` | cpk-server handoff input | Optional FastAPI import boundary; do not put it in core. |
| `control_plane_kit/servers/block_control.py` | cpk-server/product helper handoff input | Control-route adapter evidence; process hosting belongs outside core. |
| `control_plane_kit/servers/catalog.py` and product servers | control-plane-kit-servers | Product catalogue and reusable server products. |
| Docker runtimes/effects/live demos | later interpreters or cpk-server/server packages | Not part of D core service implementation except as handoff obligations. |

## Law Cards

### D.1 Service Composition Law

- Evidence source: frozen `workflows/*`, `stores/*`, `read_services/*`,
  `execution/*`, and Roadmap 0008 learning.
- Observable law: entrypoints compose services; they do not own graph, activity,
  execution, observation, recovery, or read truth.
- Negative cases: a route function opening its own store, a tool function
  bypassing approval, a worker committing independently, a process global
  becoming durable truth.
- Future owner: core.

### D.2 Contract Language Law

- Evidence source: frozen `servers/instance_read.py`, `mcp_read.py`, protocol
  extraction, and EXTRACT.C product descriptor language.
- Observable law: HTTP and MCP are typed contracts over shared services before
  they are hosted processes.
- Negative cases: free-form metadata protocols, FastAPI imports in core, hosted
  MCP runtime imports in core, route-only command semantics.
- Future owner: core for values/contracts; `cpk-server` for process hosting.

### D.3 Parity Law

- Evidence source: frozen `ReadOnlyMcpAdapter`, read service descriptors, route
  adapter shape, command services, and UnitOfWork tests.
- Observable law: equivalent HTTP and MCP requests reach the same services,
  produce the same durable intent/result, and expose equivalent redacted
  projections.
- Negative cases: duplicate tool table, duplicate projection table, transport
  bypass of idempotency, approval, authorization, or activity history.
- Future owner: core.

### D.4 Handoff Law

- Evidence source: rollout server-product split and EXTRACT.C
  `ContainerServerProduct` descriptor language.
- Observable law: the `cpk-server` wrapper is an ordinary external server
  product that imports core and satisfies core contracts.
- Negative cases: core importing `cpk-server`, core owning canonical Dockerfile,
  core owning canonical product descriptor, automatic self-registration,
  recursive proxying as a requirement.
- Future owner: `control-plane-kit-servers/cpk-server`.

## Stale Assumptions Not Migrated

- "Core-owned CPI image" is stale. Core owns services and contracts.
- "Core self descriptor" is stale. The canonical descriptor belongs to the
  future `cpk-server` product package.
- A FastAPI app in the frozen reference is adapter evidence, not a core module.
- A hosted MCP server is process packaging, not core protocol language.
- The server product catalogue must not be imported by core.
- Direct navigation uses the child `cpk-server` public endpoint; recursive
  proxying is not required.

## Security And Data Notes

- Every mutating command must require authorization and the same approval laws
  regardless of transport.
- One operator command owns one explicit transaction.
- Stores never commit independently.
- HTTP/MCP/Docker/filesystem/health/cloud effects never occur inside an open
  transaction.
- Tokens, secret values, private endpoints, and unbounded payloads stay out of
  descriptors, events, logs, errors, and product data.

## Test Evidence

#630 adds a core-local topology guardrail:

```text
control-plane-kit-core/docs/EXTRACT_D_TOPOLOGY.md
  -> control-plane-kit-core/tests/test_extract_d_topology.py
```

The focused red run failed because the topology document did not exist. That
proved the new tests were collected and failing for missing behavior, not for a
broken test harness or frozen implementation import.

## Handoff To #631

#631 may now inventory and design the generic control-plane application service
composition without worrying that it must build the server process. It should
start from frozen `workflows/*`, `stores/*`, `read_services/*`, and
`execution/*`, then design the target service boundary in core.

#631 must not import or implement FastAPI, hosted MCP, Dockerfile, OCI image,
product descriptor, or `cpk-server`.
