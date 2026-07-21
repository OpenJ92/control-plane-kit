# EXTRACT.D Topology Guardrail

EXTRACT.D defines the core control-plane service and contract surface required
by a future `cpk-server` product. It does not package that server process in
core.

## Ownership Law

```text
control-plane-kit-core
  owns generic control-plane services and contracts

control-plane-kit-servers/cpk-server
  owns FastAPI/MCP process composition, Dockerfile, OCI image, product
  descriptor, health/readiness bootstrap, and live process publication evidence
```

`cpk-server` imports core. core never imports `cpk-server`.

The canonical `cpk-server` process, Dockerfile, OCI image, and product
descriptor are not core artifacts.

process packaging is deferred to `control-plane-kit-servers/cpk-server`.

The EXTRACT.D stop law is: do not build the canonical cpk-server process inside
core.

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

D.5 Mandatory Stop
  #641 -> #642
```

## Child Classification

| Issue | Classification | Output |
| --- | --- | --- |
| #631 | Core application service composition | DeploymentProgram and generic service boundary inventory. |
| #632 | Core application service composition | UnitOfWork, store, worker, runtime-authority, and transaction boundary contract. |
| #633 | Core HTTP/MCP contract language | MCP Streamable HTTP protocol identity and endpoint contract. |
| #634 | Core HTTP/MCP contract language | HTTP route, schema, auth-scope, request, response, and error contracts. |
| #635 | Core HTTP/MCP contract language | Readiness, liveness, verification, observation, shutdown, and retained-data contracts. |
| #636 | Core parity law | Shared HTTP/MCP service vocabulary and projection parity. |
| #637 | Core parity law | Transaction, idempotency, and approval parity at the service boundary. |
| #638 | Core parity law | Authorization, destructive-command, and activity-history parity. |
| #639 | cpk-server handoff contract | Process entrypoint and composition obligations for `control-plane-kit-servers/cpk-server`. |
| #640 | cpk-server handoff contract | Environment, secret, configuration, and descriptor obligations. |
| #641 | cpk-server handoff contract | OCI, publication, health, live smoke, cleanup, and retained-data obligations. |
| #642 | Mandatory stop and closeout | EXTRACT.D closeout, learning update, and handoff to EXTRACT.E/EXTRACT.F. |

## Stale Frozen Assumptions Not Migrated

- A FastAPI app is not a core object.
- A hosted MCP server is not a core object.
- A Dockerfile or OCI image is not a core object.
- A canonical `control-plane-instance.product.cpk.json` descriptor is not a
  core object.
- Server process startup does not define control-plane truth.
- HTTP and MCP adapters must not own private command vocabularies, projections,
  UnitOfWork conventions, approval paths, or idempotency rules.

## Valid Core Work

- Generic command and read service boundaries.
- `DeploymentProgram` composition boundaries.
- Authorization vocabulary and destructive-command classification.
- UnitOfWork and transaction laws.
- Store participation, worker, runtime-authority, and external-effect timing as
  pure contracts.
- MCP Streamable HTTP protocol and endpoint contracts as typed values, including
  path, method, media, header, authentication, and origin-validation policy.
- HTTP route, schema, auth-scope, request, response, safety, and error
  contracts as typed values.
- Parity laws proving transport adapters will call the same services.

## Deferred cpk-server Work

- FastAPI process composition.
- Hosted MCP Streamable HTTP server.
- Dockerfile and OCI image.
- Canonical `control-plane-instance.product.cpk.json` descriptor.
- Live process publication evidence.
- Health/readiness process bootstrap.
- Docker/Postgres live cleanup proof.
