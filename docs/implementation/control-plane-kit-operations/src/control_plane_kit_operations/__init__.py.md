Source: [src/control_plane_kit_operations/__init__.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/__init__.py).
Maintain this document alongside its source file. When public bindings, wildcard exports, import behavior or relevant imported contracts change, verify and update this companion in the same change.

# Operations root export surface

## Responsibility and shape

This 1,128-line module is the eager public import surface for durable Operations
values, commands, services, interpreters, results, and errors. It imports their
canonical definitions rather than wrapping or reimplementing them. It declares
`__version__ = "0.1.0"` and an explicit `__all__`; there are no local functions,
classes, lazy import hooks, transaction blocks, or service instances here.

The surface spans deployment preparation/progression, sessions and workspaces,
authoring/planning, approval/admission, run lifecycle and execution, effect
attempts and observation, graph advancement, read pages/projections, product
and authority registration, node control, gateway probes, and the gateway-key
rotation phases. Retry, lease-recovery, and compensation names describe exported
capabilities, not permission to invoke autonomous recovery. `DeploymentProgramStage`
is re-exported directly from Core; Operations does not redefine that algebra.

The `CpkServer*` exports are Operations application/service contracts. Their
presence does not make this module an HTTP/MCP server, authentication boundary,
process entrypoint, or concrete runtime/provider composition.

## Import and ownership consequences

Imports execute eagerly, including imports needed by the imported modules.
This is not a lightweight dependency-free root. In particular, the direct
`NodeControlAttemptStore` import enters the
[Postgres package](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/__init__.py),
whose initializer imports schema, stores, and UnitOfWork machinery; the
[attempt store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/node_control_attempt_store.py)
itself imports `psycopg` exceptions. Package metadata declares psycopg as a
normal dependency. Importing a definition is not opening a connection or
performing its database operations; the root contains no such calls. This note
does not certify every transitive module's initialization behavior or import
cost.

Durable validation, authorization, locking, commit boundaries, redaction, and
provider calls remain with the defining services/stores and supplied adapters.
A root export neither bypasses nor establishes those laws. Most Postgres
construction APIs remain under the Postgres package; the explicit root attempt
store is a concrete exception, not evidence that all stores are root exports.

## Bound names versus wildcard exports

At this source, the literal `__all__` has 496 unique names, each matched by an
explicit import binding in the file. A static source comparison is not an
executed import or an exhaustive compatibility test.

Twenty imported retirement names are absent from `__all__`: the six exports
imported from `gateway_key_rotation_retirement`, the seven from
`gateway_key_rotation_retirement_program`, and the seven from
`gateway_key_rotation_retirement_execution`. For example,
`PublishGatewayKeyRotationRetirementProjection`,
`PrepareGatewayKeyRotationRetirement`, and
`ProgressGatewayKeyRotationRetirement` are root bindings but are not included
in wildcard import. The corresponding errors, results, services/programs, and
outcomes follow the same difference. This is current source behavior, not a
claim that the omission is intentional or an authorization to repair it.
`__version__` is also bound separately and is not in `__all__`.

Consequently, explicit root attribute/import availability and membership of
the documented wildcard surface are different contracts. Do not describe the
root as an exhaustive mirror of every submodule or infer privacy solely from
absence in `__all__`.

## Governing evidence and its limits

[test_package_boundary.py](../../../../../control-plane-kit-operations/tests/test_package_boundary.py)
contains six tests covering package metadata/dependencies and absence of
entrypoints, the foundation descriptor's deployment spine, source import
restrictions, runtime-bootstrap descriptor limits, and five selected secret-use
names in `__all__`. Its AST import scan excludes named concrete runtime/process
dependencies and confines explicit psycopg imports to Postgres sources. It is
not a dynamic-import or all-transitive-effects proof, nor a 496-name export
identity/coverage test.

[test_read_services_package.py](../../../../../control-plane-kit-operations/tests/test_read_services_package.py)
checks ten selected read-service public objects have canonical identities at
their leaf, read-services facade, and Operations root. It also checks twelve
internal protocols are absent from both facades, the exact read-services file
set, selected ownership edges, parser forms, and the inspected local dependency
graph. These are read-services-local boundaries, not proof of acyclicity of the
entire Operations import graph or identity of every root export.

Review depth: full root source, both complete boundary-test files, the Postgres
initializer, and the attempt-store import/constructor boundary were read.
Existing owner companions supply separate semantic context; listing an owner
here does not confer a new full-source review on it. No imports, tests, Docker,
database, or provider operations were executed for this documentation change.

## Maintenance

When adding, moving, or removing a public definition, inspect both its eager
binding and intended `__all__` membership, preserve canonical ownership, and
check package dependency direction. Keep protocol/internal names internal where
the owning contract requires it. A public API adjustment, import-cost change,
or cleanup of the retirement export discrepancy requires its own authorized
source change and owning validation; this companion changes none of those.
