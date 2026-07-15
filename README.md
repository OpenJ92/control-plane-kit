# control-plane-kit

`control-plane-kit` is a small Python algebra for describing deployable systems
as values: blocks, runtimes, sockets, and connections. It is designed for tools
that want to let a user build deployment topology visually, diff that topology,
and hand the result to a runtime interpreter.

The central equation is:

```text
DeployBlock = BlockSpec x RuntimeImplementation x BlockSockets
```

The block variant carries the domain distinction:

```text
Block
  = ApplicationBlock(BlockSpec, RuntimeImplementation, BlockSockets)
  | DataBlock(BlockSpec, RuntimeImplementation, BlockSockets)
  | ProxyBlock(BlockSpec, RuntimeImplementation, BlockSockets)
```

A developer brings ordinary server code. The code listens on a port and reads
unknown addresses from environment variables. `control-plane-kit` gives those
unknown addresses meaning by wiring provider sockets into consumer requirement
sockets.

## Developer Contract

Application code does not import this package. It only externalizes topology:

```python
DATABASE_URL = os.environ["DATABASE_URL"]
PAYMENTS_BASE_URL = os.environ["PAYMENTS_BASE_URL"]
```

The deployment graph declares how those values are fulfilled:

```python
from control_plane_kit import (
    ApplicationBlock,
    BlockSpec,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerPostgresImplementation,
    DockerRuntime,
    Protocol,
    RequirementSocket,
    ProviderSocket,
    BlockSockets,
    SocketConnection,
    compile_recipe,
)

api = ApplicationBlock(
    spec=BlockSpec(role_id="orders-api", display_name="Orders API"),
    implementation=DockerImageImplementation(
        image="orders-api:latest",
        command=("java", "-jar", "orders.jar"),
        ports={"internal": 8080},
    ),
    sockets=BlockSockets(
        requirements=(
            RequirementSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),
            RequirementSocket("PAYMENTS_BASE_URL", Protocol.HTTP, ("PAYMENTS_BASE_URL",)),
        ),
        providers=(ProviderSocket("internal", Protocol.HTTP),),
    ),
)

postgres = ApplicationBlock(
    spec=BlockSpec(role_id="postgres"),
    implementation=DockerPostgresImplementation(database="orders"),
    sockets=BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
)

recipe = DeploymentRecipe(
    name="orders-local",
    root=DockerRuntime(
        children=(
            api,
            postgres,
            SocketConnection(
                provider_role="postgres",
                provider_socket="internal",
                consumer_role="orders-api",
                requirement_socket="DATABASE_URL",
            ),
        ),
    ),
)

graph = compile_recipe(recipe)
print(graph.node("orders-api").environment["DATABASE_URL"])
```

## Mental Model

```text
RuntimeContext
  interprets child blocks

DeployBlock
  spec: identity and domain metadata
  implementation: how the thing exists in a runtime
  sockets: what it needs and what it exposes

SocketConnection
  provider socket -> consumer requirement socket
```

A runtime is ambient. A Docker image implementation does not create Docker; it
means "when this block is interpreted under a Docker runtime, run this image as
a container in that runtime." The same block shape can later be interpreted by
Kubernetes, ECS, or a dry-run runtime.

## Why Sockets?

Sockets are the UI/editor boundary.

- Provider sockets are provided endpoints: HTTP base URLs, Postgres connection
  strings, TCP addresses.
- Requirement sockets are environment expectations: env vars that need to be
  filled from a compatible provider.

A visual editor can render provider sockets on one side of a block and
requirement sockets on the other. Dragging from a provider to a requirement
creates a `SocketConnection`.

## Capabilities

Blocks may advertise operator capabilities independently from their application
traffic sockets. A capability says what the control plane or UI may ask a
running block to do. When a capability is backed by protocol routes, it points
at a route set such as `common-status`, `logs`, `targets`, or `observers`.

```python
BlockSpec(
    role_id="api-router",
    capabilities=(
        CapabilityName.HEALTH_CHECKABLE,
        CapabilityName.TARGET_MUTABLE,
        CapabilityName.SWITCHABLE,
    ),
)
```

The compiled node descriptor then exposes JSON-friendly capability descriptors
for inspectors and graph editors.

## Included Blocks and Implementations

The first implementation is deliberately small:

- `ApplicationBlock`
- `DataBlock`
- `ProxyBlock`
- `DockerImageImplementation`
- `LocalSourceImplementation`
- `ExternalHttpImplementation`
- `ExternalTcpImplementation`
- `ExternalPostgresImplementation`
- `DockerPostgresImplementation`
- `PlanOnlyImplementation`

The package also includes graph diffing and a conservative activity planner.

## Control Plane Reads

The first control-plane instance read surfaces are available through one shared
service boundary:

```text
Postgres-backed stores
  -> InstanceReadService
    -> FastAPI read routes
    -> CLI read commands
    -> MCP-shaped read adapter
```

Read interfaces are intentionally non-mutating. They expose workspace summaries,
current/desired graph descriptors, operator graph projections, activity
timelines, observed state, and declared control surfaces.

See [Control Plane Read Interfaces](docs/READ_INTERFACES.md) for route, CLI,
and MCP-shaped examples.

Run the local read demo with:

```bash
docker compose -f docker-compose.read-demo.yml up --build
```

Then query `http://localhost:8010/workspaces/demo-workspace` with bearer token
`demo-token`.

If `8010` is busy, set `CPK_DEMO_HOST_PORT=8011` before running compose.

The optional server adapters require FastAPI:

```bash
pip install control-plane-kit[server]
```

They expose control protocol routes for package-provided block servers while
leaving application traffic to concrete block implementations.

## Testing

The test suite is Docker-first because the control-plane stores use real
Postgres.  Run:

```bash
./test.sh
```

The script builds the test image, starts a Postgres container, installs the
control-plane schema, runs the tests, and removes the containers/volumes on
exit.


## Runtime Interpreters

A compiled graph is still only topology. A runtime interpreter is the boundary
where topology becomes effects:

```python
from control_plane_kit import CleanupPolicy, DockerRuntimeInterpreter, compile_recipe
from examples.hello_runtime import hello_recipe

graph = compile_recipe(hello_recipe("Hello, runtime!"))
interpreter = DockerRuntimeInterpreter(
    project_name="hello-demo",
    cleanup_policy=CleanupPolicy.REMOVE_ON_STOP,
)

state = interpreter.up(graph, runtime_id="docker")
try:
    assert state.node("hello").healthy
finally:
    interpreter.down(state)
```

The Docker interpreter operates on one `RuntimeRecord` at a time. It consumes
`DeploymentGraph` values, produces inspectable `RuntimePlan` values, and records
live facts in `RuntimeState`. Container names, cleanup metadata, and health
belong to runtime state; they do not belong to the graph.

Current Docker support is intentionally narrow:

- supported: one Docker runtime at a time,
- supported: Docker image blocks and Docker Postgres blocks,
- supported: fake-client tests that do not require Docker,
- supported: default cleanup that removes owned containers and network,
- supported: preserve cleanup that stops containers but keeps resources,
- unsupported: cross-runtime Docker realization,
- unsupported: host port publishing and host health checks,
- unsupported: Kubernetes, ECS, EC2, RDS, and Cloudflare interpreters.

Activity descriptors redact environment values. The executor still receives the
real environment map because containers need those values to start.

### Runtime Examples

The example ladder is:

- `examples/hello_runtime.py`: one HTTP application block through Docker.
- `examples/postgres_runtime.py`: an application wired to Docker Postgres.
- `examples/router_runtime.py`: two HTTP backends behind a Docker-backed active
  router.
- `examples/http_block_compositions.py`: graph-level compositions of the
  package-provided HTTP proxy, router, weighted balancer, multiplexer, and rate
  limiter blocks.

Graph-only examples such as `examples/app_with_postgres.py` and
`examples/router_swap.py` remain useful when you want to stop at topology.

## Docker

The project Docker image uses Python 3.14 by default:

```bash
docker build -t control-plane-kit:local .
```

Run the container smoke check:

```bash
docker run --rm control-plane-kit:local
```

Run the Docker test target, including optional FastAPI adapter tests:

```bash
docker build --target test -t control-plane-kit:test .
```

## Design Boundary

This package is not Terraform, Kubernetes, Docker Compose, or a secret manager.
It is an algebra and compiler for topology. Runtime interpreters can later use
Docker, Kubernetes, AWS, Cloudflare, or any other substrate.

The graph owns topology. Runtime interpreters own effects. Application code
stays ordinary application code.

## Design Documents

- [Operating Model](docs/OPERATING_MODEL.md)
- [HTTP Block Compositions](docs/HTTP_BLOCK_COMPOSITIONS.md)
- [Control Plane Kit Architecture Design](docs/design/0001-control-plane-kit-architecture.md)
- [Mathematical Design Preference](docs/design/0002-mathematical-design-preference.md)
- [Control Plane Kit Roadmap](docs/roadmap/README.md)
