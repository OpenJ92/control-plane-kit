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


## #614 Pure Kernel Migration

### Capability

The extracted package now contains the first real pure deployment kernel:

```text
control-plane-kit-core/src/control_plane_kit_core/
  algebra.py
  capabilities.py
  configuration.py
  control_routes.py
  environment.py
  lifecycle.py
  secrets.py
  types.py
  verification.py
  topology/
    graph.py
    codec.py
    validation.py
    changes.py
    diff.py
    compiler.py
  planning/
    activity_plan.py
    codec.py
    compiler.py
```

The executable kernel is still the intended pure pipeline:

```text
DeploymentRecipe
  -> compile_recipe
    -> DeploymentGraph
      -> validate_graph
        -> ValidatedGraph
          -> diff_graphs
            -> GraphDiff
              -> compile_activity_plan
                -> ActivityPlan
```

No Docker interpreter, Postgres store, FastAPI app, HTTP client, MCP transport,
Hello server, CoreDNS product, webhook service, or other server product was
migrated into `control-plane-kit-core`.

### Law Cards Migrated

#614 migrated representative laws from these frozen families:

```text
connection protocol:
  closed Transport x ApplicationProtocol values
  invalid transport/application combinations fail at construction
  descriptors are exact and fail closed

graph construction and compilation:
  provider/requirement vocabulary survives descriptors
  socket-derived environment wiring is pure graph material
  protocol mismatch fails at compile time

graph codec:
  generic BlockSpec identity round-trips
  unknown variants and unknown fields fail closed
  literal credentials are rejected before durable graph entry

graph validation:
  missing required connections are structured findings
  require_valid raises with the complete validation result

graph diff and activity planning:
  initial deployment compiles to runtime/node/health work
  environment connections are startup material, not socket effects
  runtime-control edge switches become typed switch activities

activity plan algebra:
  plans sort topologically, not by input order
  fan-out/fan-in order is deterministic across permutations
  structural violations are deterministic
  review work blocks execution unless explicitly reviewed
  invalid target shapes cannot enter the plan
```

### Architectural Correction

The mechanical migration initially brought `PackageServerProduct`,
`PackageServerSpec`, and `ProductMaturity` into extracted core. That would have
made core name Hello, CoreDNS, webhook delivery, routers, and other package-owned
servers. This contradicts the rollout law:

```text
core consumes registered product descriptors;
core does not own the product catalogue.
```

The correction was to remove package-owned server identities from core and keep
`BlockSpecVariantCodec` as the extension point. Future server packages can
provide product-specific specs and codecs without teaching core product names.
An architecture unittest now proves those names are absent from extracted core
source.

### Dependency Boundary

The copied configuration language imported PyYAML eagerly. Since extracted core
currently has no dependencies, YAML validation is now lazy at the exact YAML
configuration boundary:

```python
elif media_type is ConfigurationMediaType.YAML:
    try:
        import yaml
    except ModuleNotFoundError as error:
        raise ConfigurationArtifactError(
            "YAML configuration validation requires PyYAML"
        ) from error
```

This keeps `import control_plane_kit_core` and non-YAML kernel use dependency-free
without pretending YAML content was validated when the parser is unavailable.

### Public Boundary

The root package remains intentionally lightweight:

```python
import control_plane_kit_core
control_plane_kit_core.__version__
```

Kernel use imports from explicit language homes:

```python
from control_plane_kit_core.algebra import DeploymentRecipe
from control_plane_kit_core.topology import compile_recipe, validate_graph, diff_graphs
from control_plane_kit_core.planning import ActivityPlan, compile_activity_plan
```

### Evidence

Focused package validation:

```text
./control-plane-kit-core/test.sh
Ran 21 tests
OK
control-plane-kit-core import ok
```

Parity foundation validation:

```text
policy=foundation valid=true migration_complete=false entries=1107 required=880
deferred=227 incomplete_required=856 findings=0
```

Successor evidence bundle:

```text
extract-b-614.pure-kernel.unittest
sha256:a10e898280dcbb8360dcde26de49f605f42b5472c8a8ceeaf03f982865e00074
```

### Handoff

#619 should treat #614 as a representative pure-kernel migration, not as full
migration completion. The zero-unmapped manifest still reports 856 required laws
without successor evidence. #619 should decide whether EXTRACT.B closeout is a
kernel-foundation milestone or whether additional law families must move before
opening the milestone PR to main.

#619 should also preserve these decisions:

- only stdlib `unittest` in `control-plane-kit-core`;
- no package-owned server names in extracted core;
- no old/current `control_plane_kit` imports in successor tests;
- no PyYAML dependency for base package import;
- no parity claim without successor evidence.

## #619 Pure Core Kernel Closeout

### Capability

EXTRACT.B now has a reviewable kernel-floor package:

```text
control-plane-kit-core
  = pure deployment language
  + descriptor/codecs
  + validation
  + diff
  + planning
  + contract values needed before effects
```

The closeout deliberately does not claim full migration. It claims that the
first coherent extracted package exists, has a Docker-first `unittest` harness,
has package-boundary tests, has successor parity evidence for the laws migrated
so far, and is ready to be treated as the destination for later core-owned law
migration.

### Objects

The objects of this milestone are the pure values that can be built, inspected,
serialized, validated, compared, and planned without talking to Docker,
Postgres, FastAPI, MCP, HTTP clients, package-owned servers, or external
systems:

```text
DeploymentRecipe
DeploymentGraph
ValidatedGraph
GraphDiff
ActivityPlan
ProviderSocket
RequirementSocket
SocketConnection
ConfigurationArtifact
SecretReference
EnvironmentContract
VerificationContract
```

### Morphisms

The intended morphisms remain the pure deployment pipeline:

```text
DeploymentRecipe
  -> compile_recipe
    -> DeploymentGraph
      -> validate_graph
        -> ValidatedGraph
          -> diff_graphs
            -> GraphDiff
              -> compile_activity_plan
                -> ActivityPlan
```

Those arrows are the kernel. Anything that performs an effect, owns durable
workflow truth, starts a process, serves HTTP, or declares a package-owned
server product stays outside this package.

### Module Inventory

The closeout artifact records the exact package inventory:

```text
artifacts/extraction/extract-b-closeout-report.json
```

The executable inventory is also guarded by `unittest`:

```python
EXPECTED_MODULES = {
    "algebra",
    "capabilities",
    "configuration",
    "control_routes",
    "environment",
    "lifecycle",
    "planning.activity_plan",
    "planning.codec",
    "planning.compiler",
    "secrets",
    "topology.changes",
    "topology.codec",
    "topology.compiler",
    "topology.diff",
    "topology.graph",
    "topology.validation",
    "types",
    "verification",
}
```

The test includes package `__init__` modules too, but the snippet above shows
the meaningful language modules.

### Laws

The closeout law is intentionally conservative:

```text
migrated law
  = frozen reference law
  + focused successor unittest evidence
  + parity manifest mapping
  + passing validation
```

Anything without successor evidence remains incomplete. It is not counted as
migrated just because a similar module now exists.

Current parity state:

```text
entries=1107
required=880
deferred=227
passing_successors=24
failed_successors=0
incomplete_required=856
findings=0
migration_complete=false
```

The two successor evidence records are:

```text
extract-b-677.graph-duplicate-identity.unittest
extract-b-614.pure-kernel.unittest
```

### Boundary Enforcement

#619 adds a package-local closeout test:

```text
control-plane-kit-core/tests/test_milestone_closeout.py
```

It proves:

- the source module inventory is exact;
- core modules do not import forbidden runtime, product, or optional transport
  dependency roots;
- successor tests do not import pytest.

The forbidden roots include:

```python
FORBIDDEN_IMPORT_ROOTS = {
    "control_plane_kit",
    "docker",
    "fastapi",
    "httpx",
    "mcp",
    "psycopg",
    "pytest",
    "uvicorn",
}
```

### Validation

Validation for the closeout run:

```text
./control-plane-kit-core/test.sh
  Ran 24 tests
  OK
  control-plane-kit-core import ok

./validate-parity.sh foundation
  policy=foundation valid=true migration_complete=false entries=1107
  required=880 deferred=227 incomplete_required=856 findings=0

git diff --check
  passed

./test.sh
  Ran 1158 tests in 195.316s
  OK
```

The first full `./test.sh` attempt failed from Docker/Postgres storage pressure,
not application behavior:

```text
psycopg.errors.DiskFull: No space left on device
```

Docker cleanup preserved the running Pottery Factory containers and removed
only unused build cache, unused images, and anonymous unused test volumes. The
full suite was then rerun from the beginning and passed.

### Security And Data Review

Security posture:

- root import remains dependency-light;
- secret values remain outside descriptors and durable graph data;
- product-owned server identities are absent from core;
- optional effect and transport dependencies remain outside core;
- YAML parsing is lazy and fails closed when PyYAML is unavailable.

Data-engineering posture:

- no store, UnitOfWork, Postgres schema, transaction boundary, or durable
  operation service was added to core;
- parity evidence is deterministic JSON;
- unmigrated laws remain explicit instead of being silently normalized away.

Test-integrity posture:

- no pytest was introduced;
- no skips or xfails were added;
- successor counts only move when evidence exists;
- the full reference suite still passes.

### Deviations

The implementation made one important correction during #614: product-server
catalogue identities were removed from the extracted kernel. That was not a
retreat from the product system; it is the boundary that lets products become
external packages later.

### Handoff

EXTRACT.B should close as a kernel-floor milestone. The next milestone PR into
main should say plainly:

```text
control-plane-kit-core now exists as a pure package, but the complete law
migration is not done.
```

Future extraction work should migrate remaining law families through the same
process:

```text
inspect frozen law
  -> write focused unittest successor
    -> prove meaningful red where possible
      -> implement green
        -> record parity evidence
```

Do not reintroduce product-owned server names, operations, interpreters,
stores, entrypoints, or optional runtime dependencies into the core package.
