# Public Import Surfaces

The package root is the stable, pure entry surface for the deployment language.
It must remain importable with the base installation and without FastAPI,
HTTPX, psycopg, Uvicorn, Docker, or a running service.

The root pipeline is:

```text
DeploymentRecipe
  -> DeploymentGraph
  -> ValidatedGraph
  -> GraphDiff
  -> ActivityPlan
```

## Export Inventory

Every name in `control_plane_kit.__all__` is classified by its originating
module below. The source import blocks and `__all__` together are the
machine-checkable symbol inventory; this table records the ownership class of
every originating module.

| Classification | Import surfaces |
| --- | --- |
| Pure deployment language | `algebra`, `capabilities`, `configuration`, `configuration_rendering`, `contracts`, `control_routes`, `environment`, `implementations`, `lifecycle`, `secrets`, `types`, `verification` |
| Pure topology pipeline | `topology.compiler`, `topology.graph`, `topology.codec`, `topology.changes`, `topology.diff`, `topology.validation` |
| Pure planning and execution languages | `planning.activity_plan`, `planning.codec`, `planning.compiler`, `planning.recovery`, `effects`, `execution`, `saga`, `scheduling` |
| Independent pure domain languages | `discovery`, `idempotency`, `load_generation` |
| Optional HTTP and process adapters | `adapters`, `mcp_read`, `webhook`, `servers`, `discovery_server`, `idempotency_gateway` |
| Optional Postgres operations | `discovery_registry`, `stores`, operational portions of `webhook` |
| Runtime interpreters | `docker_runtime`, `runtimes` |
| Durable control-plane operations | `workflows`, `read_services` |

Only the first four rows may be re-exported by `control_plane_kit`. The other
rows are explicit subpackage entrances and must be imported from their owning
package.

Examples:

```python
from control_plane_kit import DeploymentRecipe, compile_recipe, diff_graphs
from control_plane_kit.docker_runtime import DockerRuntimeInterpreter
from control_plane_kit.discovery_registry import DiscoveryRegistryService
from control_plane_kit.servers import coredns_block
from control_plane_kit.webhook import WebhookDeliveryService
```

## Dependency Diagnostics

Operational packages fail immediately with an actionable installation message
when their optional dependencies are absent. HTTP adapters name the focused
`[http]` extra, Postgres process composition names `[postgres]`, and `[server]`
remains the broad runnable-server bundle. They do not use lazy imports,
silently swallow missing dependencies, or make those dependencies mandatory for
the pure package root.

The temporary broad `servers` and `webhook` entrances still combine product
declarations with runnable application code. The package-consolidation vertical
will separate those concerns into products, domains, operations, interpreters,
and entrypoints without changing the graph-visible product model.
