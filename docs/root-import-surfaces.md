# Public Import Surfaces

`control_plane_kit.core` is the minimal deployment kernel. The package root is
a stable, lightweight facade over that kernel and selected pure operational
value languages. It must remain importable with the base installation and
without FastAPI, HTTPX, psycopg, Uvicorn, Docker, or a running service.

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
| Deployment kernel | `core.algebra`, `core.capabilities`, `core.configuration`, `core.control_routes`, `core.environment`, `core.implementations`, `core.lifecycle`, `core.secrets`, `core.types`, `core.verification` |
| Pure topology and planning pipeline | `core.topology`, `core.planning` |
| Pure operational value languages | `contracts`, `effects`, `execution`, `saga`, `scheduling` |
| Independent domain languages | `domains.discovery`, `domains.idempotency`, `domains.load_generation`, `domains.webhook` |
| Optional HTTP and process adapters | `adapters`, `entrypoints`, transitional `servers`, `discovery_server`, `idempotency_gateway` |
| Optional Postgres operations | `discovery_registry`, `stores`, operational portions of `webhook` |
| Runtime interpreters | `docker_runtime`, `runtimes` |
| Durable control-plane operations | `workflows`, `read_services` |

Only deployment-kernel and pure operational value names may be re-exported by
`control_plane_kit`. Domains, products, concrete interpreters, durable services,
and process entrypoints use their owning package entrances. The architecture
policy treats the root as this explicit facade; it does not redefine
operational values as core.

Examples:

```python
from control_plane_kit import DeploymentRecipe, compile_recipe, diff_graphs
from control_plane_kit.docker_runtime import DockerRuntimeInterpreter
from control_plane_kit.discovery_registry import DiscoveryRegistryService
from control_plane_kit.products.servers import coredns_block
from control_plane_kit.operations.webhook import WebhookDeliveryService
```

## Dependency Diagnostics

Operational packages fail immediately with an actionable installation message
when their optional dependencies are absent. HTTP adapters name the focused
`[http]` extra, Postgres process composition names `[postgres]`, and `[server]`
remains the broad runnable-server bundle. They do not use lazy imports,
silently swallow missing dependencies, or make those dependencies mandatory for
the pure package root.

Representative product boundaries now prove the split for webhook delivery,
the test-only auth gateway, and CoreDNS. Broader teaching-server and operational
module relocations remain explicit inventory work; they do not receive duplicate
canonical implementations or compatibility facades.
