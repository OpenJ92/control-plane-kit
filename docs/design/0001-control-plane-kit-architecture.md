# Control Plane Kit Architecture Design

Status: Draft
Audience: maintainers, contributors, early adopters, and future UI/MCP authors
Last updated: 2026-07-13

## Table Of Contents

- [1. Executive Summary](#1-executive-summary)
- [2. Problem Statement](#2-problem-statement)
- [3. Goals](#3-goals)
- [4. Non-Goals](#4-non-goals)
- [5. Design Principles](#5-design-principles)
- [6. Current Implementation](#6-current-implementation)
- [7. Proposed Architecture](#7-proposed-architecture)
- [8. EnvironmentContract Design](#8-environmentcontract-design)
- [9. Control Protocol](#9-control-protocol)
- [10. Relationship Between Sockets And Control Variables](#10-relationship-between-sockets-and-control-variables)
- [11. Runtime Interpreters](#11-runtime-interpreters)
- [12. MCP Adapter](#12-mcp-adapter)
- [13. Visual UI Relationship](#13-visual-ui-relationship)
- [14. Example Servers](#14-example-servers)
- [15. Security Model](#15-security-model)
- [16. Professional Product Positioning](#16-professional-product-positioning)
- [17. Roadmap](#17-roadmap)
- [17.1 Future Maintainer Checklist](#171-future-maintainer-checklist)
- [18. Risks](#18-risks)
- [19. Open Questions](#19-open-questions)
- [19.1 EnvironmentContract Declaration Shape](#191-environmentcontract-declaration-shape)
- [19.2 Variable Validation Boundary](#192-variable-validation-boundary)
- [19.3 RuntimeContract Versus EnvironmentContract](#193-runtimecontract-versus-environmentcontract)
- [19.4 Event And Watch Transport](#194-event-and-watch-transport)
- [19.5 First Non-Python Contract Format](#195-first-non-python-contract-format)
- [19.6 Route-Mounting Ergonomics](#196-route-mounting-ergonomics)
- [20. Acceptance For The Design Direction](#20-acceptance-for-the-design-direction)
- [21. Glossary](#21-glossary)
- [22. Related Issues](#22-related-issues)

## 1. Executive Summary

`control-plane-kit` is a Python-first toolkit for describing, inspecting,
validating, and eventually changing software deployment topology through typed
values.

The core idea is that deployment infrastructure should be represented as data
before it becomes effects. A system should be expressible as a graph of runtime
contexts, deployable blocks, provider sockets, requirement sockets, and socket
connections. That graph can then be inspected by humans, rendered by visual
editors, exposed to AI agents through MCP, diffed against another graph, and
interpreted by runtime providers such as Docker, Kubernetes, ECS, EC2, RDS, or
externally managed services.

The second major idea is that each node in the graph should also expose a typed
configurable surface. This is the proposed `EnvironmentContract` and
`ControlVariable` design. A service can declare the values it needs, the values
it can safely expose, the values that are secrets, and the values that can be
changed while the process is running. If application code reads through the
contract object, then the control plane can safely mutate those values through a
standard control protocol.

The result is a two-layer algebra:

```text
Deployment topology algebra
  describes runtimes, blocks, sockets, and edges.

Node control-variable algebra
  describes what each node can inspect, validate, expose, and mutate.
```

Together, these form the foundation for:

- a Python package for topology authoring,
- runtime interpreters,
- a visual graph editor,
- a CLI,
- an MCP adapter for AI agents,
- reusable controllable network blocks,
- and eventually safe activity plans for live topology changes.

## 2. Problem Statement

Modern software deployments often have many sources of truth:

- source code knows environment variable names,
- Docker or Kubernetes manifests know process wiring,
- Terraform knows some cloud resources,
- CI/CD knows release order,
- dashboards know live health,
- logs know symptoms,
- human operators remember the intended shape,
- and AI agents currently inspect all of this indirectly.

This makes topology difficult to reason about. It is easy to ask simple
questions and receive uncertain answers:

```text
What is running?
Which runtime owns this service?
What does this service require?
Which upstreams can it connect to?
Which values are missing?
Which secrets are present?
What must restart if this value changes?
What plan safely moves traffic from service-v1 to service-v2?
```

The current state of agentic development makes this sharper. If AI agents are
going to help with operational work, they need a typed, bounded, inspectable
interface. They should not need to infer topology from shell commands, process
names, unstructured logs, and dashboard state.

`control-plane-kit` aims to provide that interface.

## 3. Goals

### 3.1 Core Goals

- Represent deployment topology as algebraic data.
- Keep application code ordinary. A normal server can still listen on a port and
  read URLs, TCP addresses, database strings, or secrets from environment
  variables.
- Let deployable nodes declare provider sockets and requirement sockets.
- Compile recipes into pure deployment graphs.
- Validate socket compatibility before runtime effects occur.
- Represent runtime contexts as topology, not hidden implementation details.
- Support multiple runtime contexts in one graph.
- Make Docker the first runtime interpreter without making Docker the model.
- Provide package-owned controllable server blocks such as routers, load
  balancers, loggers, multiplexers, and rate limiters.
- Provide a standard control protocol for node configuration and runtime state.
- Provide a future MCP adapter so AI agents can inspect and propose graph
  changes through a safe interface.

### 3.2 Developer Experience Goals

- A developer should be able to declare a server contract in Python with a
  small amount of code.
- A visual editor should be able to render provider and requirement sockets
  from that contract.
- A control plane should be able to detect missing environment values and
  secrets before runtime.
- A developer should be able to opt into live reconfiguration by reading
  through an `EnvironmentContract` instance.
- Built-in example servers should teach the pattern by using the same contracts
  users are expected to use.

### 3.3 AI-Agent Goals

- An AI agent should be able to ask for the current graph.
- An AI agent should be able to ask what a service requires and provides.
- An AI agent should be able to propose wiring between services.
- An AI agent should be able to validate a candidate graph.
- An AI agent should be able to explain missing requirements.
- Mutation tools should be separated from read-only tools and require explicit
  approval.

## 4. Non-Goals

`control-plane-kit` is not:

- Terraform,
- Kubernetes,
- Docker Compose,
- a general cloud provisioning system,
- a secret manager,
- an observability platform,
- a CI/CD platform,
- or an application framework.

It may integrate with these systems later. The kit itself should remain a
generic topology and control-contract package.

The package should not become specific to any one product. Product-specific
hostnames, deployment descriptors, operational logs, and secrets belong in
private downstream repositories or ignored local files.

## 5. Design Principles

### 5.1 Topology Before Effects

Describe the system as values before starting processes or touching cloud
resources.

```text
DeploymentRecipe -> compile_recipe -> DeploymentGraph -> runtime interpreter
```

### 5.2 Runtime Context Is Topology

Runtime contexts are part of the graph.

```text
RuntimeContext is topology.
Docker is only one interpreter of one runtime kind.
```

A graph may contain several runtime contexts:

```text
DeploymentRecipe
  root:
    DockerRuntime("local-services")
      auth
      api
      router

    DockerRuntime("local-data")
      postgres

    ExternalRuntime("managed-public-edge")
      public-edge

    AwsEcsRuntime("prod-ecs")
      api-v2

    AwsRdsRuntime("prod-rds")
      postgres

    cross-runtime SocketConnection(...)
```

Socket connections should remain runtime-agnostic. The runtime interpreter
decides how a connection becomes a URL, connection string, service discovery
record, environment assignment, control-route call, security group rule, or
observe-only reference.

### 5.3 Product Values Over Class Explosion

Prefer product forms and small interpreters over deep inheritance trees.

The central block equation is:

```text
DeployBlock = BlockSpec x RuntimeImplementation x BlockSockets
```

The block variant is the meaningful distinction:

```text
Block
  = ApplicationBlock(BlockSpec, RuntimeImplementation, BlockSockets)
  | DataBlock(BlockSpec, RuntimeImplementation, BlockSockets)
  | ProxyBlock(BlockSpec, RuntimeImplementation, BlockSockets)
```

`BlockSpec` carries shared identity and descriptive metadata. Specialized specs
should only exist later if a block family earns distinct metadata that does not
belong in the shared shape.

### 5.4 Access Is Always Lookup

The core law for control variables:

```text
Access is always lookup.
```

If application code calls:

```python
env.get("storage_base_url")
```

it is asking for the current value. The value may have been bootstrapped from
the process environment, but the `EnvironmentContract` instance is now the
runtime holder and control boundary.

If code copies a value out and stores it forever, that copy is a snapshot. The
developer then owns invalidation.

### 5.5 Secrets Can Be Set And Checked, Not Read

Secret variables must never expose their values through descriptors, logs,
resources, or read APIs.

Safe secret status:

```json
{
  "sendgrid_key": {
    "secret": true,
    "present": true
  }
}
```

Unsafe:

```json
{
  "sendgrid_key": "real-secret-value"
}
```

### 5.6 Package Servers Must Eat Their Own Cooking

If `control-plane-kit` ships a server with configurable state, that server
should declare the state through `EnvironmentContract` / `ControlVariable` and
mount the standard control protocol.

Examples:

```text
Hello server
  world: TextVariable

Active router
  targets: RuntimeMap
  active_target: RuntimeValue

Weighted load balancer
  targets: RuntimeMap
  weights: RuntimeMap
  policy: RuntimeValue

Rate limiter
  limit: IntegerVariable
  window: DurationVariable
  mode: RuntimeValue
```

This keeps the abstraction honest and gives users readable examples.

## 6. Current Implementation

This section describes what exists today.

### 6.1 Repository State

The current package contains:

- core algebra values,
- graph compiler,
- compiled graph descriptors,
- several runtime implementation descriptors,
- block capability descriptors,
- control route descriptors,
- a reusable FastAPI block control adapter,
- a draft HTTP active router block,
- a tiny hello application block,
- examples,
- tests.

The current verified test suite has 44 tests passing.

### 6.2 Core Algebra

Implemented in `control_plane_kit/algebra.py`.

Key types:

```text
DeploymentRecipe
RuntimeContext
DockerRuntime
ExternalRuntime

ApplicationBlock
DataBlock
ProxyBlock

ProviderSocket
EnvironmentRequirementSocket
RuntimeRequirementSocket
BlockSockets
SocketConnection
```

Example shape:

```python
recipe = DeploymentRecipe(
    name="hello-router",
    root=DockerRuntime(
        children=(
            hello_application_block("hello-earth", world="earth"),
            hello_application_block("hello-mars", world="mars"),
            http_active_router_block("hello-router"),
            SocketConnection("hello-earth", "internal", "hello-router", "targets"),
            SocketConnection("hello-mars", "internal", "hello-router", "targets"),
        )
    ),
)
```

More complete shape, showing the product form directly:

```python
from control_plane_kit import (
    ApplicationBlock,
    BlockSpec,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerPostgresImplementation,
    DockerRuntime,
    EnvironmentRequirementSocket,
    Protocol,
    ProviderSocket,
    BlockSockets,
    SocketConnection,
)

api = ApplicationBlock(
    spec=BlockSpec(
        role_id="api",
        display_name="Orders API",
    ),
    implementation=DockerImageImplementation(
        image="orders-api:latest",
        command=("uvicorn", "orders.main:app", "--host", "0.0.0.0"),
        ports={"internal": 8000},
    ),
    sockets=BlockSockets(
        providers=(
            ProviderSocket("internal", Protocol.HTTP),
        ),
        requirements=(
            EnvironmentRequirementSocket(
                name="database",
                protocol=Protocol.POSTGRES,
                env_vars=("DATABASE_URL",),
            ),
            EnvironmentRequirementSocket(
                name="payments",
                protocol=Protocol.HTTP,
                env_vars=("PAYMENTS_BASE_URL",),
            ),
        ),
    ),
)

postgres = ApplicationBlock(
    spec=BlockSpec(role_id="postgres", display_name="Postgres"),
    implementation=DockerPostgresImplementation(database="orders"),
    sockets=BlockSockets(
        providers=(
            ProviderSocket("internal", Protocol.POSTGRES),
        ),
    ),
)

recipe = DeploymentRecipe(
    name="orders-local",
    root=DockerRuntime(
        runtime_id="local-docker",
        children=(
            api,
            postgres,
            SocketConnection(
                provider_role="postgres",
                provider_socket="internal",
                consumer_role="api",
                requirement_socket="database",
            ),
        ),
    ),
)
```

The UI equivalent is:

```text
drag Orders API onto canvas
drag Postgres onto canvas
connect Postgres.internal -> Orders API.database
compile recipe
```

### 6.3 Compiler

Implemented in `control_plane_kit/compiler.py`.

The compiler walks the recipe tree and emits a pure `DeploymentGraph`.

Responsibilities:

- materialize blocks under their enclosing runtime context,
- collect runtime records,
- collect socket connections,
- validate provider and requirement protocol compatibility,
- turn environment requirements into environment assignments,
- turn runtime requirements into runtime assignments and control route
  references.

### 6.4 Graph Model

Implemented in `control_plane_kit/graph.py`.

Key values:

```text
DeploymentGraph
RuntimeRecord
Node
Edge
Endpoint
```

The graph is pure data. It has no side effects. It can be rendered as a
descriptor for inspection, UI, MCP, CLI, or runtime interpreters.

### 6.5 Runtime Implementations

Implemented in `control_plane_kit/implementations.py`.

Current implementations materialize nodes into graph metadata and endpoints.
They do not yet start processes generically.

Implemented:

```text
DockerImageImplementation
DockerPostgresImplementation
LocalSourceImplementation
ExternalHttpImplementation
ExternalTcpImplementation
ExternalPostgresImplementation
PlanOnlyImplementation
```

Important distinction:

```text
RuntimeImplementation materializes a node into the graph.
RuntimeInterpreter starts/stops/reconciles effects.
```

The second part is not yet implemented as a first-class system.

Example implementation descriptors:

```python
api_image = DockerImageImplementation(
    image="orders-api:2026-07-14",
    command=("uvicorn", "orders.main:app", "--host", "0.0.0.0"),
    ports={"internal": 8000},
)

local_source = LocalSourceImplementation(
    path="./examples/hello",
    command=("python", "-m", "hello"),
    ports={"internal": 8080},
)

external_payments = ExternalHttpImplementation(
    base_url="https://payments.example.com",
)

external_database = ExternalPostgresImplementation(
    connection_url_ref="secret://prod/orders/DATABASE_URL",
)
```

Important rule:

```text
Implementation describes how a node can exist.
Runtime interpreter decides how to realize it in a specific substrate.
```

### 6.6 Built-In Blocks

Implemented in `control_plane_kit/blocks.py`.

Current block factories:

```text
hello_application_block
http_active_router_block
```

The active router currently exposes:

```text
ProviderSocket("internal", HTTP)
RuntimeRequirementSocket("targets", HTTP, route_set=TARGETS)
```

and capabilities:

```text
health-checkable
target-mutable
switchable
drainable
```

### 6.7 Server Adapters

Implemented in `control_plane_kit/servers`.

Current server modules:

```text
block_control.py
http_active_router.py
hello.py
```

The active router server currently exposes route-specific control operations
for target mutation and switching. This works, but it should eventually be
re-expressed through the more general `EnvironmentContract` / `ControlVariable`
model.

### 6.8 Planner

Implemented in `control_plane_kit/planner.py`.

Current planner:

```text
diff_graphs(current, desired)
plan_migration(current, desired)
```

It emits a conservative linear `ActivityPlan` with activities such as:

```text
StartNode
HealthCheck
RegisterRuntimeTarget
SwitchRuntimeTarget
RemoveRuntimeTarget
ReconcileNode
StopNode
```

This is a useful beginning, but not yet a complete activity AST or executor.

Target activity-planning shape:

```python
current_graph = compile_recipe(current_recipe)
desired_graph = compile_recipe(desired_recipe)

diff = diff_graphs(current_graph, desired_graph)
plan = plan_migration(diff)

for activity in plan.activities:
    print(activity.describe())
```

Example output:

```text
StartNode(api-v2)
WaitForHealthy(api-v2)
RegisterRuntimeTarget(auth-router, api-v2)
SwitchRuntimeTarget(auth-router, api-v2)
DrainNode(api-v1)
StopNode(api-v1)
```

Later executor shape:

```python
executor = ActivityExecutor(
    interpreters={
        "local-docker": DockerRuntimeInterpreter(),
        "external-cloudflare": ExternalRuntimeInterpreter(),
    },
    approvals=ConsoleApprovalGate(),
)

result = executor.execute(plan)
```

### 6.9 Examples

Current examples:

```text
examples/app_with_postgres.py
examples/split_service.py
examples/router_swap.py
examples/hello_router_graph_demo.py
examples/hello_router_switch_demo.py
```

The canonical current example is graph-only:

```bash
python3 -m examples.hello_router_graph_demo
```

It demonstrates:

```text
DeploymentRecipe -> compile_recipe -> DeploymentGraph
```

The Docker hello/router switch demo is a smoke test, not the final runtime
interpreter.

## 7. Proposed Architecture

The intended full architecture has five layers.

```text
Layer 1: Algebra
  DeploymentRecipe, RuntimeContext, DeployBlock, BlockSockets, SocketConnection

Layer 2: Graph
  DeploymentGraph, RuntimeRecord, Node, Edge, Endpoint, descriptors

Layer 3: Node Control Contracts
  EnvironmentContract, ControlVariable, RuntimeEnvironmentRef, derived resources

Layer 4: Runtime Interpreters
  Docker, external, Kubernetes, ECS, EC2, RDS, Cloudflare, etc.

Layer 5: Interfaces
  CLI, HTTP control plane, visual UI, MCP adapter
```

These layers should remain separable. For example, MCP should not know how to
run Docker directly. It should ask the control plane. Docker should not own the
graph model. It should interpret the graph model.

### 7.1 Algebra Package Boundaries

The graph and activity-plan layers are cohesive packages rather than a flat
collection of similarly named modules:

```text
control_plane_kit/topology/
  graph.py       # desired deployment graph values
  codec.py       # stable graph descriptor boundary
  validation.py  # typed graph validity evidence
  changes.py     # typed graph-change algebra
  diff.py        # old graph x new graph -> GraphDiff
  compiler.py    # DeploymentRecipe -> DeploymentGraph

control_plane_kit/planning/
  activity_plan.py  # typed effect-free activity plan algebra
  codec.py          # stable activity-plan descriptor boundary
  compiler.py       # GraphDiff -> ActivityPlan
  recovery.py       # explicit recovery candidates and limitations
```

Their dependency direction is a law:

```text
algebra + typed block specifications
  -> topology
    -> planning
      -> workflows
        -> runtime execution
```

`topology` must not import `planning`, workflow persistence, or stores.
`planning` may consume topology values but must not import workflow persistence
or stores. Workflow command services own Postgres transaction boundaries and
compose the pure topology and planning languages. Roadmap 0008 execution code
consumes durable plans; it does not belong inside either pure package.

The root `control_plane_kit` package re-exports the intentionally public values
for user convenience. Internal code should import from the canonical package
that owns the concept so dependency direction remains visible during review.

## 8. EnvironmentContract Design

### 8.1 Motivation

The graph tells us how nodes connect. It does not fully describe the internal
configurable surface of a node.

Each node may have:

- startup environment variables,
- mutable runtime values,
- secrets,
- upstream service URLs,
- database URLs,
- feature flags,
- router targets,
- load balancer weights,
- rate limits,
- derived resources such as connection pools.

We need a standard model for these.

### 8.2 Environment Variables As Bootstrap Values

Traditional application code reads:

```python
DATABASE_URL = os.environ["DATABASE_URL"]
```

That is only the bootstrap value. In this design:

```python
env = ApiEnvironment.from_process()
```

loads from `os.environ`, but the `env` object becomes the live holder.

Application code should read:

```python
env.get("database_url")
```

If the control plane changes `database_url`, later calls to `env.get(...)` see
the new value.

### 8.3 Contract Declaration

Proposed Python API:

```python
class ApiEnvironment(EnvironmentContract):
    database_url = PostgresVariable("DATABASE_URL", mutable=True)
    storage_base_url = HttpVariable("STORAGE_BASE_URL", mutable=True)
    email_provider = TextVariable("EMAIL_PROVIDER", mutable=True)
    sendgrid_key = SecretVariable("SENDGRID_API_KEY", mutable=True)
```

Expanded form with reload policy:

```python
class ApiEnvironment(EnvironmentContract):
    database_url = PostgresVariable(
        env="DATABASE_URL",
        mutable=True,
        reload_policy=ReloadPolicy.DRAIN_REQUIRED,
        description="Primary transactional database URL.",
    )

    inventory_database_url = PostgresVariable(
        env="INVENTORY_DATABASE_URL",
        mutable=True,
        reload_policy=ReloadPolicy.DRAIN_REQUIRED,
    )

    storage_base_url = HttpVariable(
        env="STORAGE_BASE_URL",
        mutable=True,
        reload_policy=ReloadPolicy.LIVE,
    )

    sendgrid_key = SecretVariable(
        env="SENDGRID_API_KEY",
        mutable=True,
        reload_policy=ReloadPolicy.LIVE,
    )
```

The class is not "the environment." It is the contract declaration. The runtime
instance is created later.

### 8.4 Runtime Instance

Proposed usage:

```python
env = ApiEnvironment.from_process()

def storage_url() -> str:
    return env.get("storage_base_url")
```

Mutation:

```python
env.set("storage_base_url", "https://storage-v2.internal")
```

Validation:

```python
env.validate_patch({"storage_base_url": "https://storage-v2.internal"})
```

Descriptor:

```python
env.descriptor()
```

Expected descriptor shape:

```python
{
    "name": "ApiEnvironment",
    "variables": {
        "database_url": {
            "kind": "postgres",
            "env": "DATABASE_URL",
            "required": True,
            "mutable": True,
            "secret": False,
            "reload_policy": "drain-required",
            "present": True,
        },
        "sendgrid_key": {
            "kind": "secret",
            "env": "SENDGRID_API_KEY",
            "required": True,
            "mutable": True,
            "secret": True,
            "reload_policy": "live",
            "present": True,
            "value": None,
        },
    },
}
```

The raw secret value is intentionally absent. The descriptor says enough for a
human, UI, MCP adapter, or planner to reason about readiness without leaking the
credential.

### 8.5 ControlVariable Metadata

Each variable should be able to describe:

```text
public name
source env var
protocol/type
required flag
mutable flag
secret flag
reload policy
validation rules
visibility
current status
```

Potential reload policies:

```text
live
restart-required
drain-required
immutable
custom-handler
```

### 8.6 Derived Resources

Some values produce long-lived objects:

- SQLAlchemy engines,
- session factories,
- HTTP clients,
- connection pools,
- caches.

Changing the variable does not automatically update the derived object unless
the derived object is declared.

Proposed API:

```python
engine_ref = env.derived(
    "database_engine",
    from_var="database_url",
    build=lambda url: create_engine(url),
    dispose=lambda engine: engine.dispose(),
)
```

Per-use lookup example:

```python
def list_orders() -> list[Order]:
    database_url = env.get("database_url")
    with connect(database_url) as connection:
        return connection.fetch_all("select * from orders")
```

Derived resource example:

```python
engine_ref = env.derived(
    name="database_engine",
    from_var="database_url",
    build=lambda url: create_engine(url),
    dispose=lambda engine: engine.dispose(),
)

def list_orders() -> list[Order]:
    with Session(engine_ref.current()) as session:
        return session.query(Order).all()
```

If `database_url` changes, `engine_ref` is the place where rebuild/dispose
semantics live. The application should not keep its own hidden engine global if
it wants live mutation to work.

Design law:

```text
If you cache a value, you own invalidation.
```

### 8.7 Static And Live Modes

The same contract can support multiple adoption levels.

Static mode:

- contract validates startup config,
- graph compiler can inspect requirements,
- changes require restart.

Live mode:

- application reads through contract object,
- mounted routes can mutate values,
- changes can apply without restart according to variable policy.

## 9. Control Protocol

The control protocol is the semantic interface for inspecting and mutating
contracts. HTTP is the first transport. MCP can later expose the same semantics.

### 9.1 Core Operations

```text
describe contract
list variables
get public value or secret presence
validate proposed patch
set variable
list derived resources
refresh/rebuild derived resource
watch changes/events
```

### 9.2 HTTP Route Shape

Proposed:

```text
GET   /__control/contract
GET   /__control/variables
GET   /__control/variables/{name}
PATCH /__control/variables/{name}
POST  /__control/variables/{name}/validate
GET   /__control/derived
POST  /__control/derived/{name}/refresh
GET   /__control/events
```

### 9.3 FastAPI Mounting

Python-first adoption should be simple:

```python
from control_plane_kit.fastapi import mount_control_routes

env = ApiEnvironment.from_process()
app = FastAPI()

mount_control_routes(
    app,
    env,
    prefix="/__control",
    auth=BearerControlAuth.from_env("CONTROL_PLANE_TOKEN"),
)
```

The mounted routes should be ordinary FastAPI routes generated from the
contract:

```python
@router.get("/contract")
def get_contract() -> ContractDescriptor:
    return env.descriptor()

@router.get("/variables/{name}")
def get_variable(name: str) -> VariableDescriptor:
    return env.describe_variable(name)

@router.patch("/variables/{name}")
def set_variable(name: str, patch: VariablePatch) -> VariableDescriptor:
    env.validate_patch({name: patch.value})
    env.set(name, patch.value)
    return env.describe_variable(name)
```

The real implementation should centralize auth and redaction, but the semantic
shape should stay this small.

### 9.4 Other Transports

MCP tools over the same semantics:

```python
get_contract()
list_variables()
get_variable(name)
set_variable(name, value)
validate_variable_patch(name, value)
list_derived_resources()
refresh_derived_resource(name)
```

Future transports:

- raw ASGI,
- Flask,
- WSGI,
- TCP,
- language-specific SDKs.

## 10. Relationship Between Sockets And Control Variables

Provider and requirement sockets describe topology between nodes.

Control variables describe configurable values inside nodes.

They meet when a requirement variable is fulfilled by a provider socket.

Example:

```python
class ApiEnvironment(EnvironmentContract):
    database_url = PostgresVariable("DATABASE_URL", mutable=True)
```

This can generate or inform:

```text
EnvironmentRequirementSocket("database_url", POSTGRES, ("DATABASE_URL",))
```

Then the graph can connect:

```text
postgres.internal -> api.database_url
```

Python shape:

```python
api = ApplicationBlock(
    spec=BlockSpec(role_id="api"),
    implementation=DockerImageImplementation(...),
    sockets=BlockSockets(
        requirements=(
            EnvironmentRequirementSocket(
                name="database_url",
                protocol=Protocol.POSTGRES,
                env_vars=("DATABASE_URL",),
            ),
        ),
        providers=(
            ProviderSocket("internal", Protocol.HTTP),
        ),
    ),
)

postgres = DataBlock(
    spec=BlockSpec(role_id="postgres"),
    implementation=DockerPostgresImplementation(database="app"),
    sockets=BlockSockets(
        providers=(
            ProviderSocket("internal", Protocol.POSTGRES),
        ),
    ),
)

connection = SocketConnection(
    provider_role="postgres",
    provider_socket="internal",
    consumer_role="api",
    requirement_socket="database_url",
)
```

Compile-time result:

```text
api.environment["DATABASE_URL"] = postgres.endpoint("internal").url
```

Runtime result, if the app reads through `env`:

```text
env.get("database_url") returns current live value
```

If the graph later changes to point at `postgres-v2`:

```python
connection = SocketConnection(
    provider_role="postgres-v2",
    provider_socket="internal",
    consumer_role="api",
    requirement_socket="database_url",
)
```

the planner can decide whether this becomes a live variable mutation:

```text
SetVariable(api.database_url)
```

or a safer blue/green activity plan:

```text
StartNode(api-v2)
HealthCheck(api-v2)
SwitchTraffic(api-router, api-v2)
DrainNode(api-v1)
```

That decision comes from reload policy, runtime capabilities, and graph shape.

## 11. Runtime Interpreters

### 11.1 Why Runtime Interpreters Exist

The graph is pure topology. Runtime interpreters perform effects.

Examples:

```text
DockerRuntimeInterpreter
  creates networks
  starts containers
  injects env vars
  registers control targets
  stops containers

ExternalRuntimeInterpreter
  records observe-only endpoints
  validates known URLs

AwsEcsRuntimeInterpreter
  maps nodes to services/tasks
  writes task env
  uses service discovery

AwsRdsRuntimeInterpreter
  exposes existing database endpoints
  does not own lifecycle unless explicitly configured
```

### 11.2 First Runtime Target

The first real interpreter should be Docker.

Important constraint:

```text
Docker proves the runtime pattern.
Docker must not become the topology model.
```

### 11.3 Interpreter Shape

Potential protocol:

```python
class RuntimeInterpreter(Protocol):
    def plan_start(self, graph: DeploymentGraph, runtime_id: str) -> tuple[Activity, ...]:
        ...

    def apply(self, activities: tuple[Activity, ...]) -> RuntimeState:
        ...
```

The interpreter should receive the whole graph plus one runtime id. It needs the
whole graph because cross-runtime edges are first-class.

## 12. MCP Adapter

MCP should be an adapter over the control plane, not the control plane itself.

Shape:

```text
Codex / AI host
  -> MCP server
    -> control plane
      -> graph
      -> runtime descriptors
      -> control protocol
      -> logs/status/health
      -> runtime interpreters
```

### 12.1 Read-Only First

Initial tools/resources should be read-only:

```python
get_current_graph()
get_deploy_status()
list_runtime_contexts()
list_registered_servers()
list_available_blocks()
get_server_contract(server_id)
get_role_logs(role_id, lines=80)
check_health(role_id=None)
validate_graph(candidate_graph)
explain_missing_requirements(candidate_graph)
```

### 12.2 Mutations Later

Mutation tools should be separate and require approval:

```python
plan_transition(source_graph_id, target_graph_id)
execute_activity_plan(plan_id)
switch_router_target(router_id, target_id)
restart_node(node_id)
start_deployment(recipe_id)
stop_deployment(deployment_id)
```

Possible MCP server skeleton:

```python
class ControlPlaneMcpAdapter:
    def __init__(self, client: ControlPlaneClient) -> None:
        self.client = client

    async def get_current_graph(self) -> dict:
        graph = await self.client.get_graph()
        return graph.to_descriptor()

    async def validate_graph(self, candidate: dict) -> dict:
        graph = DeploymentGraph.from_descriptor(candidate)
        result = await self.client.validate_graph(graph)
        return result.to_descriptor()

    async def get_server_contract(self, server_id: str) -> dict:
        contract = await self.client.get_contract(server_id)
        return contract.redacted_descriptor()
```

Important boundary:

```text
MCP adapter does not run Docker.
MCP adapter does not shell out.
MCP adapter does not own graph state.
MCP adapter asks the control plane.
```

### 12.3 Why MCP Matters

MCP gives AI agents a stable operational interface. The agent no longer needs to
reconstruct topology from shell output. It can query graph state, contracts,
missing requirements, logs, health, and candidate plans through typed tools.

This is a key product direction.

## 13. Visual UI Relationship

The visual UI and MCP adapter are peer interpreters over the same control plane.

```text
Visual UI
  human visual graph editor

MCP
  AI semantic graph editor

CLI
  human terminal/script interface
```

All three must consume the same graph model, contract descriptors, validators,
and runtime descriptors. There should not be separate topology models for each
interface.

## 14. Example Servers

Examples are critical. They prove the design is understandable.

### 14.1 Hello Server

Demonstrates `Access is always lookup`.

```python
class HelloEnvironment(EnvironmentContract):
    world = TextVariable("HELLO_WORLD", mutable=True)

env = HelloEnvironment.from_process()

@app.get("/hello")
def hello():
    return {"message": f"Hello {env.get('world')}"}
```

If the control plane changes `world`, the same route returns a new response.

### 14.2 Proxy Server

Demonstrates a mutable upstream HTTP URL.

```python
class ProxyEnvironment(EnvironmentContract):
    target_base_url = HttpVariable("TARGET_BASE_URL", mutable=True)

env = ProxyEnvironment.from_process()

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request) -> Response:
    target = env.get("target_base_url")
    return await forward_request(request, base_url=target, path=path)
```

### 14.3 Active Router

Demonstrates runtime maps and active selection.

```python
class ActiveRouterEnvironment(EnvironmentContract):
    targets = RuntimeMap("targets", mutable=True)
    active_target = RuntimeValue("active_target", mutable=True)
```

Traffic route:

```python
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def route(path: str, request: Request) -> Response:
    target_id = env.get("active_target")
    targets = env.get("targets")
    target = targets[target_id]
    return await forward_request(request, base_url=target.base_url, path=path)
```

Control operation:

```python
@control.patch("/targets/{target_id}/activate")
def activate_target(target_id: str) -> dict:
    targets = env.get("targets")
    if target_id not in targets:
        raise HTTPException(status_code=404, detail="unknown target")
    env.set("active_target", target_id)
    return {"active_target": target_id}
```

### 14.4 Weighted Balancer

Demonstrates mutable probability weights.

```python
class WeightedBalancerEnvironment(EnvironmentContract):
    targets = RuntimeMap("targets", mutable=True)
    weights = RuntimeMap("weights", mutable=True)
    policy = RuntimeValue("policy", mutable=True)
```

Possible selection shape:

```python
def choose_target() -> Target:
    targets = env.get("targets")
    weights = env.get("weights")
    selected_id = weighted_choice(weights)
    return targets[selected_id]
```

### 14.5 External Provider Server

Demonstrates secret presence and redaction.

```python
class ExternalProviderEnvironment(EnvironmentContract):
    provider = TextVariable("PROVIDER", mutable=True)
    api_key = SecretVariable("PROVIDER_API_KEY", mutable=True)
```

### 14.6 Database Reader

Demonstrates derived resource invalidation.

```python
class DbEnvironment(EnvironmentContract):
    database_url = PostgresVariable("DATABASE_URL", mutable=True)
```

The example should show both per-use lookup and derived engine rebuild.

```python
env = DbEnvironment.from_process()

engine = env.derived(
    name="engine",
    from_var="database_url",
    build=lambda url: create_engine(url),
    dispose=lambda engine: engine.dispose(),
)

@app.get("/rows")
def rows() -> list[dict]:
    with engine.current().connect() as connection:
        return [dict(row) for row in connection.execute(text("select 1"))]
```

## 15. Security Model

### 15.1 Principles

- Private network access is not sufficient protection.
- Mutation requires authentication.
- Read-only development mode must be explicit.
- Secrets are never returned.
- Secret presence can be returned.
- Logs should be bounded.
- MCP mutation tools require explicit approval.
- Runtime interpreters should avoid dumping raw environment values.

### 15.2 Control Route Auth

Mounted routes should require a control-plane token or stronger mechanism.

```python
mount_control_routes(
    app,
    env,
    auth=BearerControlAuth.from_env("CONTROL_PLANE_TOKEN"),
)
```

### 15.3 Secret Handling

Allowed:

```text
present
missing
last_rotated_at, if known
fingerprint, if intentionally supported
```

Not allowed:

```text
raw value
raw env dump
unredacted logs
descriptor containing token strings
```

## 16. Professional Product Positioning

This is not best framed as another deployment tool.

Better framing:

```text
An AI-native control plane SDK for describing, inspecting, validating, and
safely changing service topologies.
```

The differentiator is the semantic layer:

```text
Service declares:
  providers
  requirements
  environment/control variables
  runtime state
  capabilities
  control routes

Control plane can:
  validate graph
  explain missing pieces
  plan transitions
  expose safe AI tools
```

The strongest demo:

```text
1. Register a few services.
2. Render them as graph nodes.
3. Connect provider sockets to requirement sockets.
4. Validate missing env/secrets.
5. Add service-v2.
6. Generate a safe rollout plan.
7. Execute with approval.
8. Observe traffic switch.
```

## 17. Roadmap

This roadmap is intentionally written as a handoff to a future maintainer or
future Codex session. Each phase should leave the repository in a state where
the examples still explain the package and the tests still protect the core
algebra.

The execution roadmap lives in [docs/roadmap](../roadmap/README.md). This
section summarizes the same direction at architecture level; the roadmap folder
breaks it into issue-ready verticals.

The safest loop for each phase is:

```text
1. Read this design document.
2. Read the related issue.
3. Inspect the current implementation before planning.
4. Make the smallest coherent implementation.
5. Add or update examples that demonstrate the behavior.
6. Add tests at the algebra, graph, and runtime boundary as appropriate.
7. Run the full test suite.
8. Leave a handoff note on the next issue if the phase affects it.
```

The most important constraint is that examples remain understandable. If a
feature cannot be shown with a small hello/router/database example, the design
is probably becoming too clever.

### Phase 1: Finish Current Router Vertical

- Merge active router block.
- Keep graph-only demo canonical.
- Keep Docker live demo as smoke only.
- Document that router target routes are a precursor to control variables.

Implementation notes:

- The active router should remain a small package-provided server, not the
  architectural center of the package.
- The router's target registry is the first visible example of mutable runtime
  state. Treat it as a stepping stone toward `EnvironmentContract`, not as a
  special one-off mechanism.
- The graph-only demo should be the canonical explanation because the package
  is topology-first. The live demo exists to prove behavior, not to define the
  model.

Tests to preserve:

- The router forwards application traffic to the active target.
- The router can switch targets through its control route.
- The router can register or hydrate targets before traffic is switched.
- Control routes do not accidentally proxy to downstream application targets.

Likely traps:

- Do not let router-specific terms leak into generic socket or graph APIs.
- Do not make all mutable runtime state look like router targets.
- Do not solve Docker orchestration inside this phase.

### Phase 2: Docker Runtime Interpreter

- Add runtime interpreter protocol.
- Add activity model for runtime effects.
- Implement Docker runtime interpreter.
- Move hello/router live demo onto interpreter.
- Prove multiple runtime records are preserved.
- Reject unsupported cross-runtime effects loudly but preserve graph topology.

Motivation:

The current package can describe deployable topology. Docker interpretation is
the first proof that descriptions can become live systems without collapsing the
model into Docker-specific assumptions.

Design target:

```text
DeploymentRecipe
  -> compile_recipe
  -> DeploymentGraph
  -> plan runtime activities
  -> DockerRuntimeInterpreter.apply(...)
  -> live containers/network
```

Target API:

```python
recipe = quick_cloudflare_auth_router_demo()
graph = compile_recipe(recipe)

interpreter = DockerRuntimeInterpreter(
    project_name="control-plane-kit-demo",
    cleanup_policy=CleanupPolicy.ON_STOP,
)

state = interpreter.up(graph, runtime_id="local-docker")

try:
    assert state.node("hello").healthy
finally:
    interpreter.down(state)
```

Implementation notes:

- Add a runtime interpreter protocol before adding Docker specifics.
- Treat Docker networks, containers, environment bindings, and published ports
  as Docker effects derived from the graph.
- Runtime context records must survive compilation. The interpreter should know
  which blocks belong to which runtime context.
- A block with `DockerImageImplementation` means "this can run under a Docker
  runtime." It must not mean "this block owns Docker."
- Cross-runtime socket connections should compile even when the first Docker
  interpreter cannot realize them. The interpreter may reject execution with a
  precise error, but the graph should still preserve the topology.

Tests to add:

- A graph with one Docker runtime can start a hello server.
- A graph with hello -> router -> hello can be realized and queried.
- A graph with two Docker runtime contexts preserves both contexts in the
  compiled graph.
- Unsupported runtime combinations fail before partially starting containers.
- Repeated runs either reuse or cleanly reject existing resources according to
  explicit policy.

Likely traps:

- Avoid shell-script-shaped orchestration hidden inside tests.
- Avoid making container names part of core graph identity.
- Avoid Docker private DNS assumptions in host-side health checks.
- Keep cleanup explicit. Tests that leak containers make the package feel
  unsafe immediately.

### Phase 3: EnvironmentContract Core

- Add `EnvironmentContract`.
- Add `ControlVariable`.
- Add concrete variables:
  - text,
  - HTTP,
  - TCP,
  - Postgres,
  - secret,
  - runtime value,
  - runtime map.
- Add descriptors.
- Add `from_mapping` / `from_process`.
- Add `get`, `set`, `validate_patch`.
- Add secret redaction.
- Add derived resources.

Motivation:

Sockets describe wiring between nodes. They do not describe the live
configuration surface inside a node. `EnvironmentContract` is the bridge between
startup configuration, runtime configuration, and control-plane visibility.

Core law:

```text
Access is always lookup.
```

Target package API:

```python
env = ApiEnvironment.from_process()

missing = env.missing_required()
if missing:
    raise StartupConfigError(missing)

descriptor = env.descriptor(redact=True)
patch = {"storage_base_url": "https://storage-v2.internal"}

validation = env.validate_patch(patch)
if validation.ok:
    env.apply_patch(patch)
```

This means application code that wants live mutation must read through the
contract at the point of use:

```python
env = ApiEnvironment.from_process()

def storage_client() -> StorageClient:
    return StorageClient(base_url=env.get("storage_base_url"))
```

If application code does this:

```python
STORAGE_BASE_URL = env.get("storage_base_url")
```

then the value is a snapshot. That can still be valid, but it must be described
as `restart-required`, `drain-required`, or otherwise non-live.

Implementation notes:

- Prefer a declarative class form for Python:

  ```python
  class ApiEnvironment(EnvironmentContract):
      database_url = PostgresVariable("DATABASE_URL", mutable=True)
      storage_base_url = HttpVariable("STORAGE_BASE_URL", mutable=True)
      sendgrid_key = SecretVariable("SENDGRID_API_KEY", mutable=True)
  ```

- The class declaration is the static contract.
- The instance is the runtime holder.
- `from_process()` reads `os.environ` once to bootstrap the holder.
- `get()` and `set()` interact with the holder, not with `os.environ`.
- Secret variables can report `present`, `missing`, and safe metadata. They
  cannot return raw values through descriptor or control APIs.
- Validation must happen before mutation.
- Derived resources must declare dependency variables and disposal behavior.

Tests to add:

- `from_mapping` loads expected values.
- missing required values produce structured errors.
- `get` reflects `set` for mutable variables.
- immutable variables reject mutation.
- secret descriptors redact values.
- protocol-specific variables validate shape.
- derived resources rebuild when their source variable changes, only when the
  reload policy permits it.

Likely traps:

- Do not make the contract only a descriptor generator. It must also be able to
  act as a runtime holder.
- Do not mutate `os.environ` as the live behavior.
- Do not build a secret manager. This package should know whether a secret is
  present and whether it can be updated, not become the source of long-term
  secret truth.
- Do not hide reload semantics. Every variable must say whether live mutation is
  allowed.

### Phase 4: Mounted Control Protocol

- Add FastAPI route mounting.
- Add read-only route mode.
- Add authenticated mutation mode.
- Add tests for descriptor, get, patch, validate, secret redaction.

Motivation:

The control plane, UI, and MCP adapter need a standard way to inspect and
mutate a running node. Mounted routes are the first Python-friendly transport.

Implementation notes:

- FastAPI should be the primary ergonomic adapter because it is readable and
  common in Python services.
- A raw ASGI adapter can remain an internal implementation detail if useful, but
  users should not have to read pure ASGI to understand the package.
- Route mounting should be explicit:

  ```python
  mount_control_routes(
      app,
      env,
      prefix="/__control",
      auth=BearerControlAuth.from_env("CONTROL_PLANE_TOKEN"),
  )
  ```

- Read-only mode should be available without exposing mutation routes.
- Mutation routes must require auth.
- The mounted protocol should use the same operation names that MCP will later
  expose, so transport does not change semantics.

Tests to add:

- `GET /__control/contract` returns descriptors.
- `GET /__control/variables` redacts secrets.
- `PATCH /__control/variables/{name}` validates and mutates allowed values.
- unauthenticated mutation is rejected.
- read-only mounts do not expose mutation behavior.
- malformed variable names produce structured errors.

Likely traps:

- Do not treat Docker private networking as security.
- Do not expose raw environment dumps.
- Do not make route names package-product-specific. This package should be
  extractable and generic.

### Phase 5: Rebuild Package Servers On Contracts

- Convert hello server to contract variables.
- Convert active router to contract variables.
- Add proxy server.
- Add weighted balancer.
- Add rate limiter.
- Add request logger/multiplexer.

Motivation:

The package-provided servers are the examples users will copy. They must use
the same contract patterns the package asks application developers to adopt.

Server guidance:

- Hello server:
  - exposes one HTTP provider socket,
  - has a mutable `world` variable,
  - proves "same route, different runtime value."
- Active router:
  - exposes one HTTP provider socket,
  - requires target registry runtime variables,
  - supports `register_target`, `list_targets`, `switch_target`,
  - proves topology mutation can update live process behavior.
- Weighted balancer:
  - exposes one HTTP provider socket,
  - has target and weight variables,
  - proves runtime policy can change without changing application code.
- Request logger/multiplexer:
  - observes traffic while forwarding to the application target,
  - proves blocks can be placed "around" an app without the app knowing.
- Rate limiter:
  - proves generic infrastructure policy can be a reusable block.

Implementation notes:

- Keep each package server in an obvious folder.
- Keep traffic routes separate from control routes.
- Keep examples tiny and runnable.
- Avoid a single giant generic proxy file. It may be mathematically elegant, but
  coherent concrete server modules are easier for users to understand.
- Shared machinery belongs in small helpers only after at least two servers
  genuinely need it.

Tests to add:

- Each server has descriptor tests.
- Each server has traffic behavior tests.
- Each server has control-route tests.
- Each mutable server proves state can change at runtime.
- Each server proves secrets or sensitive config are not emitted.

Likely traps:

- Do not marry routers/load balancers to one application node. Some blocks are
  shared infrastructure nodes and should stand alone.
- Do not put application traffic through the control plane. The control plane
  configures traffic blocks; it is not the data path.

### Phase 6: MCP Adapter

- Add read-only MCP server.
- Expose graph, status, runtime contexts, contracts, logs, health.
- Add graph validation tools.
- Keep mutation tools out of the first version.

Motivation:

MCP lets AI agents interact with the topology directly instead of inferring
state from shells, dashboards, or logs. This is not a toy integration; it is one
of the clearest reasons the package exists.

Design stance:

```text
MCP adapter -> control plane API -> graph/contracts/runtime state
```

The MCP adapter should not itself become the control plane. It should be an
adapter over the same semantics available to the UI and CLI.

Read-only first tools:

```text
get_graph
list_nodes
get_node
list_runtime_contexts
list_socket_connections
validate_graph
list_contracts
get_contract
list_capabilities
get_health
get_recent_events
```

Target adapter registration:

```python
server = McpServer("control-plane-kit")
control_plane = ControlPlaneClient.from_env()
adapter = ControlPlaneMcpAdapter(control_plane)

server.tool("get_graph")(adapter.get_current_graph)
server.tool("list_nodes")(adapter.list_nodes)
server.tool("get_contract")(adapter.get_contract)
server.tool("validate_graph")(adapter.validate_graph)
```

Mutation later:

```text
propose_connection
apply_connection
set_control_variable
start_node
stop_node
switch_target
execute_activity_plan
```

Mutation tools must be separated, loudly named, and approval-aware.

Tests to add:

- read-only tools return bounded data.
- secret values are redacted.
- invalid node IDs return structured errors.
- mutation tools are absent or disabled in the first adapter.

Likely traps:

- Do not let MCP tools shell out to discover topology.
- Do not make the MCP adapter Docker-specific.
- Do not expose write tools before the control protocol and auth model are
  boringly solid.

### Phase 7: Mutation And Activity Planning

- Expand activity AST.
- Add plan validation.
- Add approval boundary.
- Add execution through runtime interpreters.
- Add rollback/pause primitives where possible.

Motivation:

The graph diff can explain what changed. The activity planner turns that
explanation into a sequence or DAG of safe operations.

Design target:

```text
old DeploymentGraph
new DeploymentGraph
  -> graph diff
  -> ActivityPlan
  -> validation
  -> approval
  -> interpreter execution
  -> observed result
```

Activities should be explicit values:

```text
StartNode
StopNode
RegisterTarget
SwitchTarget
SetVariable
HealthCheck
DrainTraffic
WaitForHealthy
RemoveTarget
```

Implementation notes:

- Start with conservative linear plans.
- Allow future fan-out once dependencies are represented clearly.
- Distinguish "can plan" from "can execute." A runtime may be able to explain a
  change it cannot safely apply yet.
- Prefer blue/green style transitions where possible:
  - start new node,
  - health check,
  - register target,
  - switch traffic,
  - drain old target,
  - stop old node.
- Avoid mutating application code. Traffic blocks and environment contracts are
  the controllable boundary.

Tests to add:

- initial empty graph -> target graph produces start activities.
- api-v1 -> api-v2 behind router produces start/register/switch/drain/stop.
- changing only a variable produces `SetVariable` when reload policy allows it.
- changing an immutable variable produces restart/drain activities or rejects.
- activity plans refuse missing providers or incompatible sockets.

Likely traps:

- Do not assume all changes are restarts.
- Do not assume all changes can be live.
- Do not hide destructive activity inside a generic "apply" step.

### Phase 8: UI And Cross-Language Contracts

- Visual graph editor can consume descriptors.
- Python remains first-class.
- Other languages can use JSON contract descriptors and small SDKs.

Motivation:

The UI should feel like dragging servers and infrastructure blocks onto a
workspace, then connecting provider sockets to requirement sockets. The
underlying data model must stay close to that mental model.

UI shape:

```text
Workspace
  graph canvas
  node palette
  runtime context boxes
  node inspector
  socket inspector
  activity timeline
  health/event panel
```

Important UI concepts:

- Runtime contexts should appear as boxes or regions containing child blocks.
- Application blocks should show provider sockets and requirement sockets.
- Generic infrastructure blocks should be visually distinct but still use the
  same socket model.
- Some infrastructure blocks are internal to an application boundary; others,
  such as shared routers or load balancers, are independent graph nodes.
- Socket connections are the edge model. The UI should not invent a second
  connection concept.
- Capabilities determine which controls appear for a selected node.

Cross-language direction:

- Python gets the first native contract API.
- Other languages can start with static JSON contract descriptors.
- Later SDKs can provide runtime holders and mounted control routes.
- A Java or Go server should be able to participate if it can:
  - listen on declared provider ports,
  - receive required URLs/connection strings through env vars,
  - optionally expose the control protocol.

Likely traps:

- Do not make the UI depend on Python reflection.
- Do not require live route mounting for static topology authoring.
- Do not make application developers learn the entire control plane to declare
  a few env-backed requirements.

## 17.1 Future Maintainer Checklist

Before changing this package, answer these questions:

- Is this change part of topology, runtime interpretation, node control, or UI?
- Does this belong in algebra, graph, interpreter, server adapter, or example?
- Can the change be shown in a tiny example?
- Does the change preserve `DeploymentRecipe -> DeploymentGraph -> ActivityPlan`
  as a mostly pure pipeline?
- Does the change keep Docker out of the core model?
- Does the change reveal secrets, process env dumps, or private hostnames?
- Does the change make application code aware of orchestration when it does not
  need to be?
- Does the change introduce a new class where a product value would do?
- Does the change introduce a generic abstraction before two concrete examples
  need it?
- Does the change keep graph construction close to the future UI gesture:
  choose nodes, connect sockets?

If the answer is unclear, prefer writing an example first. This package should
be pulled forward by examples, not by abstract completion.

## 18. Risks

### 18.1 Abstraction Risk

The design can become too abstract. We must keep examples concrete.

Mitigation:

- build small servers,
- test each idea through examples,
- avoid inventing APIs that examples do not need.

Warning signs:

- The README cannot explain the new concept with one small code snippet.
- A user must understand activity planning before declaring one service.
- A generic type exists but only one implementation uses it.
- An example becomes mostly framework glue.
- The graph no longer resembles what the UI would draw.

Response:

When abstraction risk appears, stop adding features and write the smallest
possible example that should justify the abstraction. If the example is not
convincing, delete or shrink the abstraction.

### 18.2 Security Risk

Control routes are powerful.

Mitigation:

- default read-only where possible,
- require auth for mutation,
- never reveal secrets,
- separate MCP read-only and mutation surfaces.

Warning signs:

- A route returns raw environment variables.
- A descriptor returns a token, password, private key, or database URL with
  credentials.
- A mutation route works without explicit auth.
- MCP exposes mutation tools under friendly names that hide the danger.
- Logs include full request bodies without size limits or redaction.

Response:

Security failures should block merges. This package is about control, so
control boundaries must be boring, explicit, and tested.

### 18.3 Runtime Complexity Risk

Many runtimes can make the model muddy.

Mitigation:

- keep runtime contexts in graph,
- keep runtime interpreters separate,
- prove with Docker first,
- do not hardcode Docker assumptions into topology.

Warning signs:

- `DeploymentGraph` contains Docker-only fields.
- A socket connection assumes Docker DNS.
- Runtime context IDs become container names.
- Tests pass only when Docker is installed even though they claim to be graph
  tests.
- Cross-runtime edges disappear because the first interpreter cannot execute
  them.

Response:

Move runtime-specific detail behind interpreter outputs. The graph should retain
the user's topology even when a given interpreter cannot apply it.

### 18.4 Live Mutation Risk

Not every value can safely change live.

Mitigation:

- every variable has a reload policy,
- derived resources declare dependencies,
- immutable/restart-required values reject live patches,
- activity planner handles restart/drain when needed.

Warning signs:

- A value is marked mutable because `set()` can update it, but application code
  only read it once at startup.
- Database URLs are changed while existing pools continue using old
  connections.
- Target registries update but in-flight requests are dropped unexpectedly.
- A route can patch a value without declaring reload policy.

Response:

Prefer explicit reload policy over optimism. If live mutation is not proven,
model the change as restart-required or drain-required.

### 18.5 Adoption Risk

Developers may not want to import a control-plane package.

Mitigation:

- support static JSON contracts,
- support Python contracts without route mounting,
- make route mounting optional,
- keep app code ordinary.

Warning signs:

- A user must rewrite their app around the control plane.
- A static deployment cannot be described without importing runtime classes into
  the app server itself.
- The first tutorial starts with control routes instead of environment
  variables and sockets.

Response:

Keep a ladder of adoption:

```text
Level 1: ordinary app + static env vars + graph descriptor
Level 2: Python contract validates env at startup
Level 3: mounted read-only control routes
Level 4: mutable variables with explicit reload policy
Level 5: activity planner changes live topology through approved operations
```

No level should invalidate the levels before it.

## 19. Open Questions

- Should `EnvironmentContract` use descriptors, metaclass collection, or plain
  dataclass metadata?
- How much validation belongs in variables versus the contract instance?
- What is the minimum useful derived resource API?
- What is the correct event/watch transport?
- Should runtime maps be part of `EnvironmentContract`, or should there be a
  separate `RuntimeContract` that shares the same variable protocol?
- How do we express reload policies in graph descriptors?
- How should MCP mutation approval map to host approvals?
- What is the first non-Python contract format?
- How do we prevent route-mounting from making user servers feel invaded?

### 19.1 EnvironmentContract Declaration Shape

Candidate approaches:

```text
descriptor fields
  class ApiEnvironment(EnvironmentContract):
      database_url = PostgresVariable("DATABASE_URL")

dataclass metadata
  @dataclass
  class ApiEnvironment(EnvironmentContract):
      database_url: str = variable(Postgres, env="DATABASE_URL")

plain builder
  ApiEnvironment = EnvironmentContract.define(...)
```

Current preference:

Use descriptor fields first. They are readable, Pythonic for declaration, easy
to inspect through class metadata, and close to how ORMs and validation
libraries teach users to think. Keep the implementation simple enough that a
future dataclass front end could compile to the same internal model.

Decision criteria:

- Can we collect variables deterministically?
- Can type checkers give useful hints?
- Can we represent secret and reload metadata clearly?
- Can examples be read without explaining metaclass machinery?

### 19.2 Variable Validation Boundary

Open question:

```text
Does validation live on the variable, the contract, or both?
```

Preferred split:

- variable validates local shape:
  - URL parse,
  - protocol type,
  - required/present,
  - secret redaction rule.
- contract validates cross-variable invariants:
  - if provider is sendgrid, sendgrid key must be present,
  - if mode is read-replica, replica URL must be present,
  - if active target is set, it must exist in target registry.

This split keeps individual variables reusable while allowing domain-specific
contracts to enforce relationships.

### 19.3 RuntimeContract Versus EnvironmentContract

The router target registry and load balancer weights are not exactly
environment variables. They are mutable process state. We need to decide whether
these belong inside `EnvironmentContract` or a sibling `RuntimeContract`.

Current preference:

Use one underlying `ControlVariable` protocol and allow two semantic groupings:

```text
EnvironmentContract
  bootstrap/runtime configuration values, often env-backed.

RuntimeContract
  process-owned mutable operational values, rarely env-backed.
```

Both should expose the same descriptor and control protocol shape. The UI can
then render "environment requirements" and "runtime controls" differently
without needing two unrelated systems.

### 19.4 Event And Watch Transport

Candidate transports:

- server-sent events,
- WebSocket,
- polling,
- MCP resources/tools.

Current preference:

Start with polling and bounded event reads. Add streaming only when examples
require it. Streaming feels attractive, but it creates failure modes around
disconnects, replay, backpressure, and auth renewal.

### 19.5 First Non-Python Contract Format

The first non-Python format should probably be JSON, not a language SDK.

Example:

```json
{
  "role_id": "orders-api",
  "providers": [
    {"name": "internal", "protocol": "http", "port": 8080}
  ],
  "requirements": [
    {"name": "DATABASE_URL", "protocol": "postgres", "env": "DATABASE_URL"}
  ]
}
```

This lets Java, Go, Node, or any other server participate statically. Live
mutation can come later through language-specific runtime holders.

### 19.6 Route-Mounting Ergonomics

Concern:

Route mounting can feel invasive. Developers may not want deployment control
routes inside their application server.

Response:

Make it optional and explicit. The package should support:

- no mounted routes,
- read-only mounted routes,
- mounted routes on a sidecar,
- full mutable routes inside the app process.

The user chooses how much control surface to expose.

## 20. Acceptance For The Design Direction

This design direction is acceptable when:

- current graph examples remain simple,
- a hello server can change behavior through `env.get(...)`,
- secret descriptors prove redaction,
- an active router can be expressed through runtime variables,
- Docker runtime can run the graph without owning the graph model,
- MCP can inspect the graph without shelling out,
- and package-provided servers use the same contract model users are asked to
  adopt.

Concrete acceptance scenarios:

1. Static graph authoring:

   A user can define an app, a database, and an HTTP dependency as blocks; connect
   sockets; compile the recipe; and inspect the graph without running anything.

2. Docker realization:

   A user can take the same graph and run it under a Docker runtime interpreter
   without changing the graph model.

3. Runtime state mutation:

   A hello server can expose a `world` variable through a contract, serve
   `Hello, earth!`, accept a control mutation, and later serve
   `Hello, mars!` from the same application route.

4. Router swap:

   A router can start with target A, register target B while running, switch to
   target B through a control route, and continue serving the same application
   path.

5. Secret safety:

   A secret variable can be present and usable by the process while descriptors,
   logs, MCP tools, and control APIs never reveal its raw value.

6. AI inspection:

   An MCP client can ask for graph topology, runtime contexts, node contracts,
   and validation errors without shell access.

7. UI readiness:

   The compiled descriptors contain enough information for a future UI to draw
   runtime boxes, blocks, provider sockets, requirement sockets, connections,
   capabilities, and warnings.

If these scenarios pass, the package has crossed from interesting prototype to
credible SDK foundation.

## 21. Glossary

Activity:
  A planned operation such as start, stop, health check, register target, or
  switch target.

Block:
  A deployable node source value. It combines spec, implementation, and sockets.

Control plane:
  The system that understands graph topology, contracts, runtime state, and
  safe operations.

ControlVariable:
  A typed configurable value exposed by an environment/runtime contract.

DeploymentGraph:
  Pure compiled topology data.

DeploymentRecipe:
  Source language value used to build a deployment graph.

EnvironmentContract:
  A typed declaration and runtime holder for node-level configurable values.

ProviderSocket:
  A socket that exposes an endpoint or value for other nodes to consume.

RequirementSocket:
  A socket that needs a compatible provider value.

RuntimeContext:
  A graph node grouping that represents where child blocks are interpreted.

RuntimeInterpreter:
  A component that turns graph/runtime records into effects.

SocketConnection:
  A typed edge from provider socket to requirement socket.

## 22. Related Issues

- #17: Add first-class Docker runtime interpreter without making Docker the
  topology model.
- #18: Add MCP adapter for control-plane topology access.
- #19: Add EnvironmentContract control variables and mounted control protocol.
