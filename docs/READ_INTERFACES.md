# Control Plane Read Interfaces

Current reads are operations-owned projections exposed through cpk-server.

```text
Postgres stores
  -> InstanceReadService
    -> CpkServerReadService
      -> shared CpkServerOperationsApplication
        -> HTTP routes
        -> MCP resources/tools
```

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

## Current Validation

Current operations tests prove projection behavior, missing-workspace failure,
bounded errors, secret redaction, permission separation, and HTTP/MCP adapter
parity. The source-live cpk-server image smoke proves authenticated HTTP and MCP
requests traverse the same operations application against real Postgres.

The retired aggregate CLI, `create_instance_read_app`, and
`ReadOnlyMcpAdapter` imports are historical APIs. They are not current
compatibility surfaces.
