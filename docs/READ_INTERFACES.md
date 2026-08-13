# Control Plane Read Interfaces

Current reads are operations-owned projections exposed through cpk-server.

## Projection Ownership

```text
Postgres stores
  -> private projection families
    - workspace_graph
    - operations_history
    - observations
    - authority_secrets
    - gateway_security
  -> InstanceReadService (explicit composition only)
    -> CpkServerReadService
      -> shared CpkServerOperationsApplication
        -> HTTP routes
        -> MCP resources/tools
```

The five private families own projection behavior according to the durable
truth they read. `workspace_graph` owns workspace, graph, operator, and control
surface views. `operations_history` owns sessions, actions, plans, approvals,
runs, events, risk, and recovery views. `observations` owns freshness
interpretation against an injected trusted clock. `authority_secrets` owns
runtime authority, delivery, ingress authority, secret-provider, and
secret-reference metadata. `gateway_security` owns gateway probes, delegation
signing-key inventory, and verifier configuration.

`InstanceReadService` is the single public composition facade. Its methods make
explicit one-hop calls to those owners; it does not implement projection
behavior, dispatch through reflection, or provide an alternate read API.

## Interface Law

The law is:

```text
interfaces expose the model; they do not define the model
```

## Read Models

`control_plane_kit_operations.read_services.InstanceReadService` composes
bounded models for:

- workspace summary and graph pointers;
- current and desired graph descriptors;
- operator graph projection;
- activity timeline, sessions, plans, and approvals;
- observed state and control surface;
- runtime and ingress authorities;
- runtime authority deliveries;
- secret-provider and secret-reference metadata;
- gateway probe history and verifier configuration;
- delegation signing-key metadata.

Read models contain no raw secret values. Authority and secret-provider reads
expose bounded metadata and references only.

## Public Material Disclosure

```text
delegation signing-key inventory
  includes: bounded public metadata
  omits: public_key_pem, private_key_reference
gateway verifier configuration
  includes: bounded public_key_pem, derived public environment
  disclosure: purpose-limited public verification material, not a secret
all read surfaces
  forbid: private-key bytes, private-key references, provider credentials, resolved secret values
```

Public verification material is disclosed only by the verifier-configuration
projection that needs it. Calling public PEM non-secret does not make it part of
general signing-key inventory or permit disclosure of any private or resolved
credential material.

## Shared HTTP And MCP Boundary

Core defines pure route vocabulary. Operations maps route ids such as
`read.workspace`, `read.current-graph`, `read.activity`, and
`read.runtime-authorities` to one `CpkServerReadService`. The cpk-server process
then exposes that same application through FastAPI and MCP.

Transport authentication establishes a trusted principal. Operations enforces
workspace and focused read permissions. HTTP and MCP do not receive independent
authorization semantics or direct store access.

## Transaction Boundary

One read request opens one short read UnitOfWork, constructs its projection,
and closes the transaction before returning through the process adapter. Reads
do not perform Docker, Cloudflare, secret-provider, filesystem, or health IO.

## Package Ownership Evidence

`control-plane-kit-operations/tests/test_read_services_package.py` is the
executable current inventory for the installed read-services subtree. It fixes
the canonical public object identities, private protocol boundary, exact module
set, and acyclic local import graph.

`docs/architecture/package-module-inventory.json` is deliberately different:
it is retained evidence for the historical pre-extraction aggregate package.
It is not rewritten to describe current external packages.

## Current Validation

Current operations tests prove projection behavior, missing-workspace failure,
bounded errors, secret redaction, permission separation, and HTTP/MCP adapter
parity. The source-live cpk-server image smoke proves authenticated HTTP and MCP
requests traverse the same operations application against real Postgres.

The retired aggregate CLI, `create_instance_read_app`, and
`ReadOnlyMcpAdapter` imports are historical APIs. They are not current
compatibility surfaces.
