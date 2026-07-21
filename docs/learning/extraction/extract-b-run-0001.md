# EXTRACT.B Pure Core Kernel - Run 0001

## Scope

EXTRACT.B creates the first in-repository `control-plane-kit-core` package and
migrates only the pure deployment planning kernel:

```text
DeploymentRecipe
  -> DeploymentGraph
  -> ValidatedGraph
  -> GraphDiff
  -> ActivityPlan
```

Durable operations, Postgres stores, Docker/runtime interpreters, HTTP/FastAPI,
MCP, package-owned servers, live runners, service discovery, webhook delivery,
idempotency gateway, and Hello are explicitly out of scope.

## Topology

```text
#610 -> #611 -> #612 -> #613 -> #677 -> #614 -> #619
```

## #610 Scaffold

### Capability

The repository now has an in-repository package boundary:

```text
control-plane-kit-core/
  pyproject.toml
  README.md
  src/control_plane_kit_core/
  tests/
```

The package is intentionally minimal. It claims no migrated parity laws yet.

### Decision

Start in-repository rather than creating `OpenJ92/control-plane-kit-core`
immediately. This keeps the frozen reference artifacts, successor tests, and
new package boundary reviewable together until the kernel boundary is proven.

### Evidence

```text
python -m compileall src tests
python -m unittest discover -s tests
git diff --check
```

All focused validation ran in Docker. The scaffold uses `unittest`, not pytest.

### Handoff

#611 installs the package-local operating notes without widening scope.

## #611 Operating Scaffolds

Pending.

