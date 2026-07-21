# control-plane-kit-core

`control-plane-kit-core` is the extracted pure deployment kernel for
`control-plane-kit`.

This package is built from the frozen reference laws recorded by EXTRACT.A. It
does not import the frozen `control_plane_kit` package. The initial milestone
owns only the pure planning pipeline:

```text
DeploymentRecipe
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

