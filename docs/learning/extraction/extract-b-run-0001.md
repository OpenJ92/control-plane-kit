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

## #613 Test Harness

### Capability

`control-plane-kit-core/test.sh` now runs the package-local checks in Docker:

```text
python -m unittest discover -s tests
python -m compileall src tests
python -m pip install . && import control_plane_kit_core
```

The unittest lane uses a read-only package mount. The compile and install lanes
copy the package into `/tmp/pkg` inside the container because those operations
legitimately create build or bytecode artifacts.

The repository root `./test.sh` invokes the core package harness before the
existing Docker/Postgres suite, so a single validation command now covers the
frozen/reference package and the extracted core scaffold.

### Handoff

#677 can rely on a reusable core package harness while rehearsing the
law-context-before-dry-run loop. It should still avoid claiming parity from
scaffold or harness tests alone.

## #677 Law-Context Rehearsal

### Law Card

```text
frozen reference:
  tests.test_graph_construction.GraphConstructionTests.test_add_operations_reject_duplicates_without_erasing_first_values

stable law:
  behavior.add-operations-reject-duplicates-without-erasing-first-values

observable behavior:
  DeploymentGraph.add_node, add_edge, and add_runtime reject duplicate
  identities with a closed GraphConstructionError.

negative cases:
  duplicate node identity
  duplicate edge identity
  duplicate runtime identity
  replacement value must not overwrite the first value
  error string must not leak replacement body text

obsolete assumptions:
  frozen examples, Docker implementations, recipe fixtures, and root
  control_plane_kit imports are not part of the successor law

successor owner:
  control-plane-kit-core

successor test:
  control-plane-kit-core/tests/test_topology_graph.py

evidence status:
  passing
```

### Target Boundary

The rehearsal introduced the first real pure value surface:

```python
from control_plane_kit_core.topology import (
    DeploymentGraph,
    Edge,
    GraphConstructionCode,
    GraphConstructionError,
    GraphIdentityKind,
    Node,
    RuntimeRecord,
)
```

This is intentionally smaller than the frozen graph language. It proves the
process with one central identity law before #614 migrates the complete kernel.

### Red-To-Green Evidence

Red failed for the expected reason:

```text
ModuleNotFoundError: No module named 'control_plane_kit_core.topology'
```

Green passed through the package harness:

```text
Ran 4 tests
OK
control-plane-kit-core import ok
```

### Parity Evidence

The manifest now points the frozen law to one successor proof:

```text
extract-b-677.graph-duplicate-identity.unittest
sha256:4bff6481ba3871f90aeae2735b6b6085f24413506a4bae9a0aaae61384af99ae
```

Foundation validation remains green:

```text
policy=foundation valid=true migration_complete=false entries=1107 required=880
deferred=227 incomplete_required=879 findings=0
```

### Handoff

#614 should migrate the full pure kernel using this exact loop, but it should
not treat the minimal #677 topology module as final structure. It may extend,
rename, or split the surface where the broader law set demands it.
