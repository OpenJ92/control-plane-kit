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

### Capability

`control-plane-kit-core` now has package-local operating notes:

```text
control-plane-kit-core/AGENTS.md
control-plane-kit-core/docs/EXTRACTION.md
```

They defer to the repository root process, then narrow the extracted package to
the pure kernel. The package-local policy explicitly requires stdlib
`unittest`, law cards before dry run, target tests before implementation, and
no import of the frozen/current `control_plane_kit`.

### Handoff

#612 proves that the package metadata and root import stay lightweight.

## #612 Package Metadata

### Capability

The package boundary now has executable metadata/import laws:

```python
project = metadata["project"]
self.assertEqual(project["name"], "control-plane-kit-core")
self.assertEqual(project["dependencies"], [])
self.assertNotIn("optional-dependencies", project)
self.assertNotIn("scripts", project)
```

The root import is AST-checked against forbidden runtime/package imports:

```python
forbidden = {
    "control_plane_kit",
    "docker",
    "fastapi",
    "httpx",
    "mcp",
    "psycopg",
    "uvicorn",
}
```

### Evidence

```text
python -m unittest discover -s tests
python -m pip install . && python -c "import control_plane_kit_core"
git diff --check
```

All validation ran in Docker. The package still claims no migrated parity laws.

### Handoff

#613 should turn these focused checks into a reusable Docker-first package test
harness. It should not add Postgres, runtime-effect Docker tests, product
images, or pytest.
