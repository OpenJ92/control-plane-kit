# control-plane-kit-core

`control-plane-kit-core` is the extracted pure deployment kernel for
`control-plane-kit`.

This package is built from the frozen reference laws recorded by EXTRACT.A. It
does not import the frozen `control_plane_kit` package. The initial milestone
owns only the pure planning pipeline:

```text
DeploymentTopology
  -> DeploymentGraph
  -> ValidatedGraph
  -> GraphDiff
  -> ActivityPlan
```

The package deliberately excludes:

- Docker and other runtime interpreters;
- Postgres stores and UnitOfWork implementations;
- FastAPI, HTTP clients, MCP transports, and process entrypoints;
- package-owned server products;
- live runtime effects;
- Hello and other acceptance products.

EXTRACT.D adds the pure control-plane service composition boundary. It names
the generic service roles a future `DeploymentProgram` composes, but still does
not implement stores, process entrypoints, hosted MCP, Docker images, or
server-product descriptors:

```text
DeploymentProgramBoundary
  = planning
  x approval
  x admission
  x lifecycle
  x execution
  x recovery
  x observation
  x reads
  x authorization
```

It also names the transaction boundary each role must obey:

```text
UnitOfWorkBoundary
  = DeploymentProgramBoundary
  x ServiceTransactionBoundary*

one operator command = one explicit transaction
stores never commit
external effects happen only after commit
```

MCP Streamable HTTP is represented as a typed contract, not as a hosted server:

```text
Protocol.MCP_STREAMABLE_HTTP
  = tcp x mcp-streamable-http

McpStreamableHttpContract
  = endpoint path
  x POST/GET method contract
  x required media/header policy
  x authentication/origin-validation requirements
```

The future `cpk-server` process implements this contract. Core only describes
and validates it.

HTTP API routes are also values:

```text
HttpApiRouteContract
  = route id
  x method
  x path template
  x ControlPlaneServiceRole
  x auth scope
  x safety classification
  x bounded request schema
  x bounded response schema
  x bounded error contract
```

This lets future HTTP adapters bind routes to services without inventing route
local workflow semantics.

## Extraction Law

Every migrated behavior must be justified by a frozen law card from the
EXTRACT.A parity artifacts:

```text
inspect frozen law
  -> dry-run target boundary
    -> write focused successor test
      -> prove red
        -> implement green
```

Scaffold files do not claim parity. A frozen law is migrated only when this
package has passing successor evidence.
