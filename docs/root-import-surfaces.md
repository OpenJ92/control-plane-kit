# Public Import Surfaces

The mutable aggregate `control_plane_kit` facade was retired after extraction.
Current code imports from the package that owns the language or behavior.

## Coordination Repository

Pure deployment language belongs to `control_plane_kit_core`:

```python
from control_plane_kit_core import DeploymentGraph, ProductDescriptor
from control_plane_kit_core.planning import ActivityPlan
```

Durable application behavior belongs to `control_plane_kit_operations`:

```python
from control_plane_kit_operations import RuntimeInterpreterDispatcher
```

The operations package owns stores, Unit of Work boundaries, application
services, activity history, read models, and dispatcher protocols. It does not
import concrete runtime or provider SDKs.

## External Packages

Concrete effect implementations are imported from
`control_plane_kit_interpreters` at a composition boundary. Encrypted secret
custody is provided by `control_plane_kit_secrets`. Product descriptors,
processes, Dockerfiles, and OCI publication metadata live in
`control-plane-kit-servers`.

The intended dependency direction is:

```text
control-plane-kit-core
  <- control-plane-kit-operations
  <- cpk-server composition
       -> control-plane-kit-interpreters
       -> control-plane-kit-secrets client

control-plane-kit-servers
  -> package-owned product descriptors and processes
```

## Dependency Diagnostics

Each live package has an independent `./test.sh` gate and clean-import check.
The repository-level current backend gate validates exact source coordinates,
cross-package protocols, package gates, source-live cpk-server HTTP/MCP
acceptance, and Docker residue:

```bash
./current-backend-test.sh
```

Historical imports from `control_plane_kit` remain only in frozen evidence and
historical design records. They are not compatibility surfaces and current
source must not import them.
