# control-plane-kit

`control-plane-kit` is a small Python algebra for describing deployable systems
as values: blocks, runtimes, sockets, and connections. It is designed for tools
that want to let a user build deployment topology visually, diff that topology,
and hand the result to a runtime interpreter.

The central equation is:

```text
DeployBlock = Spec x RuntimeImplementation x RoleSockets
```

For application code this becomes:

```text
ApplicationBlock = AppSpec x RuntimeImplementation x RoleSockets
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
    AppSpec,
    ApplicationBlock,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerPostgresImplementation,
    DockerRuntime,
    Protocol,
    EnvironmentRequirementSocket,
    ProviderSocket,
    RoleSockets,
    SocketConnection,
    compile_recipe,
)

api = ApplicationBlock(
    spec=AppSpec(role_id="orders-api", display_name="Orders API"),
    implementation=DockerImageImplementation(
        image="orders-api:latest",
        command=("java", "-jar", "orders.jar"),
        ports={"internal": 8080},
    ),
    sockets=RoleSockets(
        requirements=(
            EnvironmentRequirementSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),
            EnvironmentRequirementSocket("PAYMENTS_BASE_URL", Protocol.HTTP, ("PAYMENTS_BASE_URL",)),
        ),
        providers=(ProviderSocket("internal", Protocol.HTTP),),
    ),
)

postgres = ApplicationBlock(
    spec=AppSpec(role_id="postgres"),
    implementation=DockerPostgresImplementation(database="orders"),
    sockets=RoleSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
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
- Requirement sockets are expectations that need to be filled from a compatible
  provider. Environment requirements become env vars; runtime requirements
  become control-route mutations.

A visual editor can render provider sockets on one side of a block and requirement
sockets on the other. Dragging from a provider socket to a consumer requirement creates
a `SocketConnection`.

## Capabilities

Blocks may advertise operator capabilities independently from their application
traffic sockets. A capability says what the control plane or UI may ask a
running block to do. When a capability is backed by protocol routes, it points
at a route set such as `common-status`, `logs`, `targets`, or `observers`.

```python
ProxySpec(
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

The optional server adapters require FastAPI:

```bash
pip install control-plane-kit[server]
```

They expose control protocol routes for package-provided block servers while
leaving application traffic to concrete block implementations.

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
