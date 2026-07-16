# Roadmap 0009: Control Plane Instance Block, Recursive Spawning, And Direct Navigation

Status: Draft
Depends on: Roadmap 0001 through Roadmap 0008

## Motivation

The package already has one deployment language:

```text
DeploymentRecipe
  -> DeploymentGraph[DeployBlock]
  -> graph diff
  -> ActivityPlan
  -> Executor
  -> live deployment
```

A running control-plane instance is application code served over HTTP. It has a
runtime implementation, provider sockets, requirement sockets, health behavior,
and control capabilities. Therefore it is not a second kind of graph node:

```text
ControlPlaneInstanceBlock : DeployBlock
```

More concretely, it is a package-provided `ApplicationBlock` whose application
is the control-plane instance server:

```text
ControlPlaneInstanceBlock
  = ApplicationBlock
      ControlPlaneInstanceSpec
      RuntimeImplementation
      BlockSockets
```

This removes the need for a recursive `ManagedNode` sum, a Hub-specific graph
grammar, or a distinct Hub implementation. Recursion emerges because an
ordinary deployment graph can contain a server block whose application owns
and interprets another ordinary deployment graph.

```text
parent DeploymentGraph
  contains ControlPlaneInstanceBlock("child-a")

child-a ControlPlaneInstance application
  owns another DeploymentGraph
    containing API, Postgres, routers, or another ControlPlaneInstanceBlock
```

"Hub" is only the positional name for the first externally bootstrapped
control-plane instance through which a user enters. It is not a domain type.
Every running instance exposes the same server application and may discover
selectable child instance blocks in its own graph.

## Goal

Make the control-plane instance server available as an ordinary package server
block and make recursive child discovery plus direct navigation a projection
over existing graph, observed-state, authorization, and control-route
machinery.

This roadmap should provide:

- one composed FastAPI control-plane instance application rather than only the
  existing read-only adapter and seeded demo server,
- a package-provided `ControlPlaneInstanceBlock` constructor,
- a real Docker image/runtime implementation for the instance server,
- an ordinary instance deployment recipe fragment containing public entry,
  Auth, CPI, stores, runtime authority, and connections,
- declared HTTP, Postgres, and runtime-authority sockets,
- declared health, read, planning, execution, and child-navigation
  capabilities,
- graph compilation and execution with no special node case,
- child discovery derived from ordinary graph topology,
- endpoint and health lookup derived from observed state,
- access filtering derived from authorization records,
- observed public entry URLs and direct per-instance authentication,
- bootstrap and recovery recipes,
- nested child-discovery projections for UI, API, CLI, and MCP,
- and a live Docker demonstration of an instance deploying another instance.

## Current Implementation Reality

The repository already contains meaningful pieces, but it does not yet contain
the final deployable CPI server:

```text
create_instance_read_app
  = real FastAPI read adapter over InstanceReadService

current Dockerfile entry point
  = seeded read-interface demo server

Roadmap 0007
  = command/session/graph-edit/planning APIs

Roadmap 0008
  = approved execution and runtime mutation
```

Roadmap 0009 must first compose the completed read, command, planning,
execution, health, capability, and control surfaces into one application
factory. The detailed composition appears below. The services remain imported
modules with their own truth and workflow boundaries; the FastAPI application
only composes them and owns the HTTP transaction boundary.

### Prerequisite Contract From Roadmaps 0007 And 0008

Roadmap 0009 must consume completed lower-level boundaries rather than quietly
finishing them inside the CPI server.

Roadmap 0007 must hand off transport-neutral, injectable services for:

```text
operation sessions and ordered actions
desired graph editing and optimistic concurrency
pure validation and diffing
activity planning
approval request and decision
session/plan read projections
Postgres UnitOfWork construction
stable request/result/error descriptors
```

Roadmap 0008 must hand off generic execution machinery capable of:

```text
claiming one approved plan idempotently
starting Docker-backed DataBlock and ApplicationBlock values
injecting socket-derived environment values
publishing explicitly requested host endpoints
waiting for declared provider readiness and HTTP health
recording ActivityRun, ActivityEvent, and ObservedState truth
retaining Postgres according to cleanup policy
compensating completed external steps where safe
```

If either handoff is absent, Roadmap 0009 stops and repairs the prerequisite
roadmap. It must not add a CPI-specific transaction manager, activity executor,
Docker startup script, or optimistic `started == healthy` shortcut.

### Server Package Boundary

The CPI's server composition belongs under `control_plane_kit/servers/`, beside
the existing package-provided server blocks. Its durable and workflow modules
do not move there.

```text
control_plane_kit/
  servers/
    control_plane_instance/
      __init__.py       # curated public server exports
      app.py            # compose the complete FastAPI application
      block.py          # control_plane_instance_block(...)
      environment.py    # ControlPlaneInstanceEnvironment
      entrypoint.py     # create_app_from_environment() for Uvicorn
      routes/
        __init__.py
        reads.py        # transport adapter over read services
        commands.py     # transport adapter over Roadmap 0007 services
        execution.py    # transport adapter over Roadmap 0008 services
        control.py      # CPI control/capability routes
        health.py       # bounded liveness/readiness projections

  stores/               # durable truth and Postgres adapters remain here
  read_services/        # read semantics remain here
  workflows/            # command/session semantics remain here
  policies/             # authz/approval/destructive policy remains here
  projections/          # operator-facing projections remain here
```

The existing `servers/instance_read.py` is an early transport adapter. During
this roadmap it should become the read-router portion of the composed server,
with imports and tests moved deliberately rather than retained as a second CPI
application. No compatibility wrapper is required merely to preserve an
unreleased internal module path.

The root `Dockerfile` should gain a named CPI image target whose command points
at the real package entrypoint. The server package should not receive a second,
independent Docker build system unless runtime evidence demonstrates a need.

```dockerfile
FROM package AS control-plane-instance

EXPOSE 8010

CMD ["uvicorn", \
     "control_plane_kit.servers.control_plane_instance.entrypoint:create_app_from_environment", \
     "--factory", "--host", "0.0.0.0", "--port", "8010"]
```

### Composition, Not Ownership Collapse

The FastAPI layer assembles services and owns HTTP transaction boundaries; it
does not absorb their semantics:

```python
def create_control_plane_instance_app(
    services: ControlPlaneInstanceServices,
    security: InstanceSecurity,
) -> FastAPI:
    app = FastAPI(title="Control Plane Instance")
    app.include_router(read_router(services.reads, security.read_policy))
    app.include_router(command_router(services.commands, security.command_policy))
    app.include_router(execution_router(services.execution, security.execution_policy))
    app.include_router(instance_control_router(services.control, security.control_policy))
    app.include_router(health_router(services.health))
    return app
```

Each mutating route calls one application-service command. That command opens
one Postgres UnitOfWork. Repositories may add or flush, but only the UnitOfWork
commits. Docker, Cloudflare, runtime-agent, or child HTTP effects occur after
durable intent and outside the database transaction through Roadmap 0008.

### CPI Environment Contract

The CPI declares startup requirements using the existing typed contract system.
Access remains lookup so a future runtime patch or derived-resource rebuild can
be represented honestly.

```python
from control_plane_kit.contracts import (
    EnvironmentContract,
    HttpVariable,
    PostgresVariable,
    ReloadPolicy,
    SecretVariable,
    TextVariable,
)


class ControlPlaneInstanceEnvironment(EnvironmentContract):
    instance_id = TextVariable(
        "instance_id",
        mutable=False,
        reload_policy=ReloadPolicy.IMMUTABLE,
        metadata={"env": "CONTROL_PLANE_INSTANCE_ID"},
    )
    workspace_id = TextVariable(
        "workspace_id",
        mutable=False,
        reload_policy=ReloadPolicy.IMMUTABLE,
        metadata={"env": "CONTROL_PLANE_WORKSPACE_ID"},
    )
    database_url = PostgresVariable(
        "database_url",
        reload_policy=ReloadPolicy.DRAIN_REQUIRED,
        metadata={"env": "CONTROL_PLANE_DATABASE_URL"},
    )
    runtime_authority_url = HttpVariable(
        "runtime_authority_url",
        required=False,
        reload_policy=ReloadPolicy.RESTART_REQUIRED,
        metadata={"env": "CONTROL_PLANE_RUNTIME_URL"},
    )
    control_token = SecretVariable(
        "control_token",
        reload_policy=ReloadPolicy.RESTART_REQUIRED,
        metadata={"env": "CONTROL_PLANE_CONTROL_TOKEN"},
    )
```

Database clients derived from `database_url` must either be rebuilt under an
allowed policy or be reported stale. Secret-bearing settings use secret
contracts/references and are never emitted in graph, activity, log, or MCP
descriptors.

The first Roadmap 0009 implementation slice is therefore:

```text
compose real CPI FastAPI application
  -> package executable Docker image
  -> describe image as ApplicationBlock
  -> run through DockerRuntime
  -> prove real health/read behavior
```

That slice ends in a required reassessment before Auth, public entry, or child
spawning proceeds. The review must compare the live implementation against this
roadmap and record any necessary issue-topology changes.

## Central Algebra

The graph remains unchanged:

```text
DeployBlock
  = ApplicationBlock
  | DataBlock
  | ProxyBlock

DeploymentGraph
  = Graph[DeployBlock]
```

The instance is one inhabitant of `ApplicationBlock`:

```python
from dataclasses import dataclass

from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    CapabilityName,
    DockerImageImplementation,
    Protocol,
    ProviderSocket,
    RequirementSocket,
)


@dataclass(frozen=True)
class ControlPlaneInstanceSpec(BlockSpec):
    """Closed specification type identifying a CPI application block."""


def control_plane_instance_block(
    block_id: str,
    *,
    workspace_id: str | None = None,
    image: str = "control-plane-kit-instance:latest",
) -> ApplicationBlock:
    """Describe one deployable control-plane instance server."""

    return ApplicationBlock(
        spec=ControlPlaneInstanceSpec(
            role_id=block_id,
            display_name="Control Plane Instance",
            health_path="/health",
            capabilities=(CapabilityName.HEALTH_CHECKABLE,),
        ),
        implementation=DockerImageImplementation(
            image=image,
            command=(
                "uvicorn",
                "control_plane_kit.servers.control_plane_instance.entrypoint:create_app_from_environment",
                "--factory",
                "--host",
                "0.0.0.0",
                "--port",
                "8010",
            ),
            ports={"internal": 8010},
            environment={
                "CONTROL_PLANE_INSTANCE_ID": block_id,
                "CONTROL_PLANE_WORKSPACE_ID": workspace_id or block_id,
            },
        ),
        sockets=BlockSockets(
            requirements=(
                RequirementSocket(
                    "database",
                    Protocol.POSTGRES,
                    ("CONTROL_PLANE_DATABASE_URL",),
                ),
                RequirementSocket(
                    "runtime-authority",
                    Protocol.HTTP,
                    ("CONTROL_PLANE_RUNTIME_URL",),
                    required=False,
                ),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
```

The exact image, command, ports, and capability representation may change
during implementation. The law may not:

```text
The instance is constructed through the existing block product form.
```

The first implementation intentionally advertises one HTTP provider. Reads,
commands, execution, health, and `__control` routes are route families on one
FastAPI listener, not separate network services. A second provider socket is
added only if the implementation later creates a genuinely separate listener
with an independently meaningful address.

This provider is the CPI's runtime-local service address. It is not the public
URL shown to users. Auth consumes the CPI provider; a public-entry block fronts
Auth and advertises the user-facing address.

The runtime-authority socket is optional only so the same server can run in an
explicit read/planning-only mode. Mutation endpoints must report authority as
unavailable and may not fall back to host Docker access. A CPI that advertises
or accepts execution capability must have this requirement connected.

Discovery must pattern-match the closed `ControlPlaneInstanceSpec` type. It may
not infer CPI identity from display names, image names, route strings, or a
free-form metadata discriminator.

The compiler must therefore preserve authored specification identity in the
compiled node rather than flattening it entirely into metadata:

```python
@dataclass(frozen=True)
class Node:
    node_id: str
    spec: BlockSpec
    implementation: MaterializedImplementation
    runtime_id: str
    sockets: BlockSockets
    # endpoints, environment, and observations remain compiled/runtime values
```

This sketch is a design requirement, not a demand for those exact field names.
The important laws are:

```text
authoring spec type survives compilation in memory
descriptor boundary encodes a stable closed variant tag
descriptor decoder reconstructs the same spec variant
core graph/compiler does not import CPI server implementation code
projection may match ControlPlaneInstanceSpec without reading metadata strings
```

Roadmap 0007 must leave graph descriptor reconstruction extensible enough for
this variant. Roadmap 0009 owns adding and round-tripping the CPI variant.

The initial snippet advertises only health because `/health` is part of the
first slice. Logging, restart, planning, execution, and child-management
capabilities are added only with their real route sets and authorization
policies. A capability may not be advertised merely because the roadmap intends
to implement it later.

## Ordinary Graph Construction

A parent instance and its database are authored with the same node-and-socket
language as every other deployment:

```python
from control_plane_kit import (
    BlockSockets,
    BlockSpec,
    DataBlock,
    DeploymentRecipe,
    DockerPostgresImplementation,
    DockerRuntime,
    Protocol,
    ProviderSocket,
    SocketConnection,
)

child = control_plane_instance_block("child-a")

child_database = DataBlock(
    spec=BlockSpec("child-a-postgres", "Child A Postgres"),
    implementation=DockerPostgresImplementation(database="child_a"),
    sockets=BlockSockets(
        providers=(ProviderSocket("internal", Protocol.POSTGRES),),
    ),
)

recipe = DeploymentRecipe(
    name="root-with-child-instance",
    root=DockerRuntime(
        children=(
            child,
            child_database,
            SocketConnection(
                provider_role="child-a-postgres",
                provider_socket="internal",
                consumer_role="child-a",
                requirement_socket="database",
            ),
        ),
    ),
)
```

## Deployment Recipe Ladder And Reassessment Gate

Roadmap 0009 should not attempt the recursive demonstration in one jump. It
must accrue executable examples in three stages.

### Stage A: CPI Packaging Smoke

The first recipe contains only CPI and Postgres. The runtime-authority
requirement is intentionally unconnected, so the server runs in
read/planning-only mode:

```python
def control_plane_instance_core_recipe() -> DeploymentRecipe:
    postgres = control_plane_postgres_block("cpi-postgres", database="control_plane")
    cpi = control_plane_instance_block("cpi")

    return DeploymentRecipe(
        name="control-plane-instance-core",
        root=DockerRuntime(
            runtime_id="cpi-core",
            children=(
                postgres,
                cpi,
                SocketConnection(
                    provider_role="cpi-postgres",
                    provider_socket="internal",
                    consumer_role="cpi",
                    requirement_socket="database",
                ),
            ),
        ),
    )
```

This recipe must compile through the ordinary graph compiler, start through the
ordinary Docker interpreter, publish an explicitly requested development port,
answer `/health`, and answer at least one real read route against Postgres.
Execution routes must report runtime authority unavailable rather than reaching
Docker implicitly.

Until the operational bootstrap issue is complete, Stage A may use a clearly
named test provisioning fixture to install the Postgres schema and seed one
workspace. The fixture must use the real Postgres adapters and migrations, be
visible in the example/test setup, and never be presented as the production
bootstrap mechanism. The later bootstrap issue replaces it with the idempotent
`BootstrapInstance` program used by real deployments.

### Stage B: Operational CPI Core

The second recipe adds the first reviewed runtime-authority block:

```text
Postgres.internal -> CPI.database
DockerRuntimeAgent.control -> CPI.runtime-authority
CPI.internal -> host-published development observation
```

It proves that an approved harmless plan can pass from CPI to the runtime
authority, produce activity events, and update observed state. It still omits
Auth, public entry, and recursive spawning so failures remain attributable.

### Mandatory Reassessment Gate

After Stages A and B pass live Docker validation, stop and review Roadmap 0009
before implementing the selectable-instance stack. Record findings in the
Roadmap 0009 learning document and update child issues where needed.

The review must answer:

```text
Does one CPI HTTP provider correctly represent the running server?
Do command and execution routes preserve API-owned UnitOfWork boundaries?
Are database and runtime URLs supplied only through RequirementSockets?
Does Docker distinguish started, ready, healthy, unhealthy, and unknown?
Are private Docker endpoints distinct from host-observed endpoints?
Can Postgres survive CPI replacement under retained-state policy?
Are activity events sufficient to explain partial startup or execution?
Does the image target package the real server without demo-only wiring?
Did any CPI-specific behavior leak into compiler or runtime interpreters?
```

Only after this review may the roadmap proceed to the full selectable stack.

### Stage C: Selectable CPI Stack

The complete fragment adds Auth and public entry around the proven operational
core:

```text
Postgres.internal -> CPI.database
DockerRuntimeAgent.control -> CPI.runtime-authority
CPI.internal -> Auth.upstream
Auth.internal -> PublicEntry.upstream
PublicEntry.public -> Auth.public-entry-url
```

The last two connections express different facts:

```text
Auth.internal -> PublicEntry.upstream
  traffic flow: public entry forwards requests to Auth

PublicEntry.public -> Auth.public-entry-url
  configuration flow: Auth learns its externally observed issuer/base URL
```

This is a communication/configuration cycle but not an arbitrary startup cycle.
Roadmap 0008 readiness policy and Roadmap 0009 phased execution must allow the
public-entry provider to establish or reveal its URL before Auth becomes ready,
then verify end-to-end public traffic after Auth starts.

The two-node example proves the algebra, but it is not the complete selectable
instance deployment. The operational unit presented by authoring helpers and
the future UI is an ordinary recipe fragment:

```text
InstanceDeploymentFragment
  = PublicEntryBlock
  + AuthBlock
  + ControlPlaneInstanceBlock
  + StoreBundle
  + RuntimeAuthority
  + SocketConnections
```

This fragment is not a new graph node or persistence type. It is a convenience
constructor that returns ordinary deployment expressions:

```python
def control_plane_instance_deployment(
    instance_id: str,
) -> tuple[DeploymentExpr, ...]:
    public_entry = public_entry_block(f"{instance_id}-entry")
    auth = control_plane_auth_block(f"{instance_id}-auth")
    instance = control_plane_instance_block(instance_id)
    postgres = control_plane_postgres_block(f"{instance_id}-postgres")
    runtime_authority = docker_runtime_agent_block(f"{instance_id}-runtime")

    children: tuple[DeploymentExpr, ...] = (
        public_entry,
        auth,
        instance,
        postgres,
        runtime_authority,
        SocketConnection(postgres.block_id, "internal", instance.block_id, "database"),
        SocketConnection(
            runtime_authority.block_id,
            "control",
            instance.block_id,
            "runtime-authority",
        ),
        SocketConnection(instance.block_id, "internal", auth.block_id, "upstream"),
        SocketConnection(auth.block_id, "internal", public_entry.block_id, "upstream"),
        SocketConnection(
            public_entry.block_id,
            "public",
            auth.block_id,
            "public-entry-url",
        ),
    )
    return children
```

The exact public-entry implementation may be Cloudflare, an AWS load balancer,
Kubernetes ingress, a private VPN hostname, or a host-published development
endpoint. The socket follows the package's provider-to-requirement
configuration-flow convention. The important law is that every selectable
instance advertises a reachable Auth URL and the fragment expands into ordinary
blocks and edges.

One workspace may contain several disconnected fragments:

```text
bootstrapped instance graph
  Auth A -> CPI A -> Stores A
  Auth B -> CPI B -> Stores B
  Auth C -> CPI C -> Stores C
```

They need not exchange application traffic. The graph itself registers the
selectable instance deployments.

The compiler should not contain logic equivalent to:

```python
if isinstance(node, ControlPlaneInstanceNode):
    compile_child_instance_specially(node)
```

It should continue interpreting the block product:

```python
match block:
    case ApplicationBlock(spec, implementation, sockets):
        return implementation.materialize(spec.role_id, sockets, runtime)
    case DataBlock(spec, implementation, sockets):
        return implementation.materialize(spec.role_id, sockets, runtime)
    case ProxyBlock(spec, implementation, sockets):
        return implementation.materialize(spec.role_id, sockets, runtime)
```

The instance's special behavior belongs inside its application server and
advertised capabilities, not in the deployment graph algebra.

## Recursion Law

The recursion is operational rather than structural within one graph value:

```text
root instance application
  owns graph G0
    node child-a is a ControlPlaneInstanceBlock

child-a instance application
  owns graph G1
    node child-a-1 is a ControlPlaneInstanceBlock

child-a-1 instance application
  owns graph G2
```

`G1` is not inlined into `G0`. From `G0`, `child-a` is an opaque application
server block. This preserves independent workspace truth, transaction
boundaries, authorization, activity history, and runtime ownership.

The externally bootstrapped root is special only by position:

```text
root = the ControlPlaneInstanceBlock whose public endpoint was bootstrapped
```

No `Hub` class, `HubServer`, `HubGraph`, or `HubAdmission` type is required.

## Discovery Is A Projection, Not Another Registry

The current code contains instance-registry concepts introduced while Hub was
thought to be a separate boundary. Roadmap 0009 must review and narrow or retire
those concepts. There must not be two competing sources of child topology.

Immediate child instances are derived from existing truths:

```text
child instance listing
  = current graph nodes identified as control-plane instance server blocks
  + observed endpoint / health / runtime state
  + authorization grants visible to the operator
```

Conceptually:

```python
def selectable_instances(
    graph: DeploymentGraph,
    observed: ObservedState,
    grants: AccessGrants,
    actor: Actor,
) -> tuple[SelectableInstance, ...]:
    children = []
    for node in graph.nodes:
        match node.spec:
            case ControlPlaneInstanceSpec():
                children.append(node)
            case _:
                pass
    return tuple(
        project_selectable_instance(node, observed, grants)
        for node in children
        if grants.may_enter(actor, node.node_id)
    )
```

The projection consumes the preserved specification value. JSON persistence may
encode the closed variant with a textual descriptor tag, but that string belongs
only at the serialization boundary and must be decoded before domain matching.

Relational data may still be needed for operator visibility grants, endpoint
history, lifecycle events, and recovery metadata. Those records do not
constitute a second graph or a Hub-owned child topology registry.

## Parent And Child Truth

The parent owns facts about its deployment of the child block:

```text
parent current/desired graph
parent observation of child endpoint and health
parent activity history for starting/stopping/replacing the child block
parent authorization grants for listing or managing that child deployment
```

The child owns facts inside its application boundary:

```text
child workspace
child current/desired/observed graph
child operation sessions and approvals
child activity plans, runs, and events
child control credentials
```

The parent must not query the child's Postgres database directly. It talks to
the child through the child's advertised and authenticated HTTP APIs.

## Runtime Authority Is An Explicit Dependency

A CPI cannot mutate Docker, ECS, Kubernetes, or another runtime merely because
it is running inside that runtime. Runtime authority must be supplied explicitly.

For the first Docker implementation, prefer a narrow authenticated runtime-agent
server over mounting the Docker socket directly into every CPI:

```text
Docker socket
  -> DockerRuntimeAgentBlock
       authenticated runtime-control provider
         -> CPI runtime-authority requirement
```

The agent is privileged and must be treated accordingly. It translates narrow
typed activities such as start, stop, inspect, and wait-for-health into Docker
operations. The CPI submits approved activities; it does not receive arbitrary
shell access.

```python
RequirementSocket(
    "runtime",
    Protocol.HTTP,
    ("CONTROL_PLANE_RUNTIME_URL",),
)
```

Future AWS, Kubernetes, or external runtimes may satisfy the same logical
requirement through different provider blocks or credential-backed adapters.
The roadmap must document the security difference between:

```text
remote runtime-agent capability
direct Docker socket mount
cloud API credentials
observe-only external runtime
```

The Docker demonstration must use one explicit, reviewed authority path. It may
not rely on an unexplained host-side test process to perform the child's work.

## Navigation And Authentication

Deployment recursion does not imply recursive request routing. Every selectable
instance advertises its own public Auth URL and owns its own login session.

```text
UI -> bootstrapped instance Auth
        GET selectable child instances
        select child-a with entry_url

UI -> child-a entry_url
        authenticate directly with child-a Auth
        enter child-a workspace
        GET child-a selectable children
        select child-a-1 with entry_url

UI -> child-a-1 entry_url
        authenticate directly with child-a-1 Auth
```

The parent does not proxy ordinary UI traffic to the child and does not mint a
recursive delegation chain. The parent-child relation records deployment
provenance, lifecycle authority, observation, recovery responsibility, and
visibility. It is not the network path for normal use.

The selectable-instance projection includes both stable identity and the
currently observed public entry URL:

```python
SelectableInstance(
    instance_id="child-a",
    display_name="Child A",
    entry_url="https://child-a.control.example",
    health="healthy",
)
```

The UI treats `instance_id` as durable identity and `entry_url` as replaceable
observed addressing. Selecting an instance changes the client's active base URL
and authentication session. A native client may retain separate sessions for
several instances, but credentials must remain scoped to the Auth server that
issued them.

### Public Entry URL Contract

The public entry URL is a typed contract supplied by the public-entry provider
and consumed by Auth:

```text
PublicEntryBlock
  provides PublicEntryUrl

AuthBlock
  requires PublicEntryUrl
    -> CONTROL_PLANE_PUBLIC_URL
    -> AUTH_PUBLIC_BASE_URL
    -> AUTH_ISSUER_URL where required
```

Using the existing contract vocabulary, Auth declares the environment values it
reads and its public-entry requirement fills them from one provider URL:

```python
class ControlPlaneAuthEnvironment(EnvironmentContract):
    public_url = HttpVariable(
        "public_url",
        reload_policy=ReloadPolicy.RESTART_REQUIRED,
        metadata={"env": "CONTROL_PLANE_PUBLIC_URL"},
    )
    auth_base_url = HttpVariable(
        "auth_base_url",
        reload_policy=ReloadPolicy.RESTART_REQUIRED,
        metadata={"env": "AUTH_PUBLIC_BASE_URL"},
    )
    issuer_url = HttpVariable(
        "issuer_url",
        reload_policy=ReloadPolicy.RESTART_REQUIRED,
        metadata={"env": "AUTH_ISSUER_URL"},
    )


public_entry_requirement = RequirementSocket(
    "public-entry-url",
    Protocol.HTTP,
    (
        "CONTROL_PLANE_PUBLIC_URL",
        "AUTH_PUBLIC_BASE_URL",
        "AUTH_ISSUER_URL",
    ),
)
```

Three facts remain distinct:

```text
desired contract
  Auth requires PublicEntryUrl

observed endpoint
  PublicEntryBlock advertises the URL currently reachable by clients

injected value
  Auth currently reads that URL from its EnvironmentContract
```

A stable named URL may be supplied before startup. An ephemeral tunnel URL is
available only after the ingress starts; the executor must observe it, satisfy
the contract, then start or reconfigure Auth according to the contract's reload
policy. The parent projection reads the observed provider endpoint, not the
child process environment.

Direct authentication is the first design. Future identity federation or SSO
may reduce repeated logins, but it must remain optional and must not reintroduce
ancestor-by-ancestor request proxying.

## Lifecycle Uses The Existing Pipeline

Adding a child is an ordinary graph edit:

```text
add PublicEntryBlock + AuthBlock + ControlPlaneInstanceBlock + stores
    + runtime authority + socket connections
  -> validate DeploymentRecipe
  -> compile DeploymentGraph
  -> diff current and desired graphs
  -> create ActivityPlan
  -> approve
  -> execute dependency-ordered StartNode / WaitForHealth activities
  -> record observed endpoint and health
```

Startup ordering must be compiled from explicit startup dependencies. A socket
connection does not automatically imply a hard startup dependency for every
protocol, so the graph language or block specification needs a typed readiness
policy. For the instance fragment, the required order is:

```text
stores ready
  -> runtime authority ready
    -> CPI ready
      -> public entry endpoint available
        -> Auth configured and ready
          -> public entry traffic healthy
```

Roadmap 0008 must provide the dependency-aware activity planner/executor. This
roadmap supplies the instance fragment's readiness declarations and proves that
the generic executor honors them. A hand-authored child startup script does not
satisfy the roadmap.

Stopping, replacing, or deleting a child uses the same graph-diff and activity
machinery. Roadmap 0009 must not create a child-specific executor.

The child becomes selectable only after the block is healthy and the parent can
query its instance protocol through child Auth. Partial startup remains visible
through Roadmap 0008 activity events and saga compensation.

## Bootstrap, Archive, And Recovery

Starting a CPI process is not sufficient to create an operable instance. The
bootstrap program must idempotently establish:

```text
Postgres schema
workspace identity
empty initial current/desired graph
initial owner or trusted-parent grant
instance signing/trust identity
secret references
runtime-authority connection
```

The bootstrap command is an interpreter over explicit bootstrap data, not
hidden container-entrypoint mutation:

```python
BootstrapInstance(
    instance_id="root",
    workspace_id="root-workspace",
    initial_owner="jacob",
    runtime_requirement="docker-agent",
)
```

It must be retry-safe and distinguish create, reconnect, and incompatible-store
states.

Current graph topology determines active child deployments. Removing a child
from the current graph must not make retained deployments impossible to find or
recover. Archived/deconstructed projections should be derived from:

```text
historical graph version containing the instance fragment
+ lifecycle activity/events
+ retained store/runtime locators
+ access grants
```

Recovery records may retain locators and secret references, but may not become a
second desired-topology registry. Reconstruction produces a new desired graph
edit from the retained historical descriptor and passes through the normal
approval/execution pipeline.

## Suggested Issue Topology

1. Record the `ControlPlaneInstanceBlock : DeployBlock` ADR.
   - Reject the `ManagedNode` sum and Hub-specific graph grammar.
   - Define recursion as one application block owning another deployment graph.
   - Define the root as externally bootstrapped position, not a new type.
   - Preserve child opacity and independent truth ownership.
   - Define `ControlPlaneInstanceSpec` as a closed authored specification type
     and require that compilation plus descriptor round-trip preserve it.
   - Reject metadata-string, display-name, image-name, and route-name inference
     for CPI discovery.

2. Compose the real control-plane instance FastAPI application.
   - Create the `servers/control_plane_instance/` package and route subpackage
     exactly as described in the server-package boundary above.
   - Move the existing read adapter into that composition deliberately; do not
     leave a second seeded CPI application behind.
   - Combine Roadmap 0006 reads, Roadmap 0007 commands/planning, Roadmap 0008
     execution, health, capabilities, and instance control routes.
   - Keep route handlers thin and preserve the API-owned UnitOfWork boundary.
   - Add `ControlPlaneInstanceEnvironment` and construct services from typed
     startup requirements and secret references.
   - Replace the seeded demo entry point as the definition of the CPI process.
   - Add app-factory tests for read, command, execution-policy, and health
     composition.

3. Package the CPI as a real Docker application block.
   - Build an executable CPI image rather than a generated toy server command.
   - Add `control_plane_instance_block()` in
     `servers/control_plane_instance/block.py` following the router and
     package-server factory pattern.
   - Declare one internal HTTP provider, required Postgres, optional explicit
     runtime authority for read/planning-only mode, health, and capabilities.
   - Carry `ControlPlaneInstanceSpec` through compiled nodes and descriptor
     reconstruction without coupling the core compiler to server behavior.
   - Advertise typed capabilities without relying on display metadata.
   - Add the Stage A `control-plane-instance-core` recipe.
   - Prove ordinary `DockerRuntime` compilation, explicit host publication, real
     health, and a Postgres-backed read without adding a new graph node case.

4. Define and implement the first Docker runtime-authority path.
   - Record the runtime-authority contract and threat model.
   - Prefer an authenticated `DockerRuntimeAgentBlock` that alone holds the
     Docker socket over mounting it into every CPI.
   - Restrict the agent to typed runtime activities; do not expose arbitrary
     shell execution.
   - Connect the agent provider to the CPI runtime requirement normally.
   - Test authorization, scope restrictions, and unavailable-agent behavior.
   - Add the Stage B operational CPI recipe and execute one approved harmless
     activity through the agent.
   - Stop for the mandatory live reassessment, record a learning document, and
     update the remaining Roadmap 0009 issue topology before proceeding.

5. Package the control-plane Auth and public-entry boundary.
   - Provide an ordinary Auth application block in front of CPI.
   - Connect CPI's `internal` provider to Auth's upstream requirement.
   - Define `PublicEntryUrl` as a typed EnvironmentContract supplied by the
     public-entry provider and consumed by Auth.
   - Model `CPI.internal -> Auth.upstream`, `Auth.internal ->
     PublicEntry.upstream`, and `PublicEntry.public -> Auth.public-entry-url` as
     distinct socket connections.
   - Keep traffic forwarding distinct from public issuer/base-URL
     configuration even though both concern the same Auth boundary.
   - Support stable preconfigured URLs and observed ephemeral URLs through
     explicit reload/restart policy.
   - Keep Auth signing/session secrets in secret providers and descriptors
     redacted.
   - Prove direct login against the advertised public entry URL.

6. Add the ordinary instance deployment recipe fragment.
   - Expand to public entry, Auth, CPI, stores, runtime authority, and socket
     connections.
   - Return ordinary `DeploymentExpr` values rather than introducing a capsule
     graph node or new persistence aggregate.
   - Support several disconnected instance fragments in one workspace.
   - Add the Stage C selectable CPI stack recipe using only ordinary blocks and
     connections.
   - Add compilation fixtures for named Cloudflare, ephemeral Cloudflare, and
     host-published development entry providers.

7. Prove dependency-ordered generic execution.
   - Consume the readiness/dependency semantics delivered by Roadmap 0008.
   - Start stores, runtime authority, CPI, public-entry endpoint, and Auth in the
     declared phased order with endpoint and health gates.
   - Resolve the deliberate Auth/public-entry communication/configuration cycle
     through typed readiness phases rather than arbitrary graph order.
   - Compensate partial startup in reverse completed order where safe.
   - Reject any implementation that uses a hand-authored instance startup
     script or a child-specific executor.
   - Preserve all existing deployment examples.

8. Reconcile the existing instance registry concepts and access schema.
   - Identify which records are duplicated topology and remove that role.
   - Normalize operator visibility grants, lifecycle history, and recovery
     metadata where they are independently authoritative.
   - Derive immediate child topology from graph nodes.
   - Derive endpoint and health from observed state.
   - Migrate or retire `cpk_instances` fields explicitly; do not leave a shadow
     catalog beside graph truth.
   - Keep all multi-repository commands inside one API-owned Postgres UnitOfWork.

9. Add typed recursive child discovery projections.
   - Identify instance blocks through a typed specification/capability.
   - Join graph, observed state, and grants without mutating any source.
   - Return stable identity, display name, health, endpoint status, and bounded
     capabilities.
   - Project archived/deconstructed children from historical graph versions,
     lifecycle events, retained locators, and grants.
   - Expose the same projection to FastAPI, CLI, and read-only MCP.

10. Add direct selectable-instance navigation.
   - Project stable child identity and observed public Auth entry URL.
   - Let clients switch their active base URL and authenticate directly against
     the selected child.
   - Keep sessions scoped by instance origin and prevent credential reuse across
     unrelated Auth servers.
   - Handle changed, stale, unavailable, and malformed advertised URLs.
   - Test root login -> child selection -> child login and child login ->
     grandchild selection -> grandchild login without parent proxying.

11. Add idempotent bootstrap, archive, and recovery interpreters.
   - Install schema, workspace, empty graph, initial grants, trust identity,
     secret references, and runtime requirement explicitly.
   - Distinguish create, reconnect, and incompatible-store outcomes.
   - Recover the root from retained Postgres and graph descriptor state.
   - Treat every descendant as an ordinary block deployment.
   - Reconstruct archived descendants by proposing ordinary desired-graph edits
     from historical descriptors and recovery references.
   - Document stop, remove, archive, deconstruct, and retained-data behavior.

12. Add a recursive Docker demonstration.
   - Bootstrap a root instance.
   - Use its graph workflow to add a full child instance fragment.
   - Execute the approved plan.
   - Discover the child from graph plus observed state.
   - Navigate directly to the child's advertised URL and log in there.
   - Let the child deploy a grandchild through its own runtime authority.
   - Navigate directly to the grandchild's advertised URL and log in there.
   - Exercise both a host-published development entry and a Cloudflare entry
     implementation of the same public-entry contract.

13. Perform security, data, design, and reliability hardening.
    - Concurrent child block creation and idempotency.
    - Duplicate stable identities.
    - Runtime-agent privilege and command-surface review.
    - Endpoint staleness and child unavailability.
    - Public URL validation, issuer mismatch, open redirects, DNS rebinding,
      stale endpoints, session-origin isolation, and redaction.
    - Partial startup, failed compensation, retained Postgres, and recovery.
    - Verify no second topology registry remains.

14. Document and hand off recursive spawning and direct navigation to Roadmap 0010.
    - Include root, child, grandchild, stopped child, and failed-start fixtures.
    - Curate block-construction and navigation snippets.
    - Make the UI derive instance switching from selectable-instance IDs and
      observed public URLs.
    - Avoid hard-coded Hub/Instance species in UI models.

## Transaction And Saga Boundaries

Graph edits, operation sessions, approvals, and relational access records obey
ADR 0008:

```text
API/application service
  owns Postgres UnitOfWork
    graph/workspace repositories
    authorization repositories
    operation/activity repositories
```

Repositories may add and flush. Only the unit of work commits.

No database transaction remains open during Docker, cloud, or child HTTP
effects. Those effects use the activity/saga pipeline:

```text
persist approved desired graph
  -> start child stores and wait for readiness
  -> start or connect runtime authority
  -> start child CPI and wait for health
  -> start public-entry provider and observe PublicEntryUrl
  -> inject PublicEntryUrl into child Auth
  -> start or reconfigure child Auth
  -> wait for public-entry traffic health
  -> expose child with entry_url in selectable-instance projection
```

## Non-Goals

- Do not implement a separate Hub server or Hub domain model.
- Do not add a `ManagedNode` sum solely for control-plane instances.
- Do not add a special child-instance compiler or executor path.
- Do not create a second registry as the source of child topology.
- Do not inline a child's graph into its parent's graph.
- Do not let a parent read or mutate a child's database directly.
- Do not give CPI arbitrary shell access through its runtime authority.
- Do not proxy ordinary UI or API traffic through ancestor instances.
- Do not create recursive delegation chains between instance Auth servers.
- Do not treat deployment ownership as a network route.
- Do not expose raw control credentials to the browser.
- Do not make Cloudflare the only implementation of the public-entry contract.

## Validation

- `ControlPlaneInstanceBlock` is an ordinary `ApplicationBlock` and therefore a
  `DeployBlock`.
- `ControlPlaneInstanceSpec` survives recipe compilation, descriptor
  persistence, and reconstruction as a closed specification variant.
- Child discovery pattern-matches that type and does not inspect free-form
  metadata strings.
- Existing recipe, graph, diff, and execution code accepts it without a new node
  alternative.
- Socket connections supply its Postgres and HTTP requirements normally.
- The Stage A core recipe runs the real CPI image with Postgres, an intentionally
  published development endpoint, truthful health, and a real read route.
- With runtime authority absent, Stage A remains explicitly read/planning-only
  and execution fails closed.
- The Stage B operational recipe executes one approved harmless activity through
  the reviewed runtime authority and records events plus observed state.
- The mandatory Stage A/B reassessment is recorded before Auth/public-entry
  implementation begins.
- One ordinary recipe fragment expands into public entry, Auth, CPI, stores,
  runtime authority, and connections.
- Dependency-aware execution starts that fragment in readiness order without a
  special instance executor.
- A running CPI can execute an approved child deployment through its explicit
  runtime authority.
- A parent discovers immediate child instances from graph plus observed state.
- Authorization filters selectable children independently of topology truth.
- No parallel instance registry claims to own child topology.
- Every selectable child advertises its observed public Auth entry URL.
- The desired public URL contract, observed provider endpoint, and injected
  Auth environment value remain distinct and traceable.
- Root, child, and grandchild each authenticate directly and maintain
  origin-scoped sessions.
- Named and ephemeral public-entry providers satisfy the same typed contract.
- Public URL changes obey the declared reload or restart policy.
- A child owns its own workspace, plans, runs, events, and observations.
- Bootstrap is idempotent and recovery does not require hidden entrypoint
  mutation.
- Archived/deconstructed instances remain recoverable without becoming active
  current-graph nodes.
- Root, child, and grandchild navigation works in the Docker demonstration.
- Existing deployment recipes remain live-runnable.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Definition Of Done

Roadmap 0009 is complete when:

- the package provides a deployable control-plane instance server block,
- the CPI block runs the composed read/command/planning/execution FastAPI
  application rather than the seeded read demo,
- the CPI server composition lives under
  `control_plane_kit/servers/control_plane_instance/` while truth, workflow,
  policy, and projection ownership remains in their existing modules,
- the package has live-validated Stage A and Stage B recipes and a documented
  reassessment of the remaining roadmap,
- CPI identity is represented and reconstructed as a closed specification type,
  not inferred from strings,
- no Hub-specific server implementation is needed,
- no special recursive graph node or compiler case is needed,
- the bootstrapped instance can deploy a child through the ordinary graph edit,
  planning, approval, and execution pipeline,
- the deployed child consists of ordinary public-entry, Auth, CPI, store,
  runtime-authority, and connection blocks,
- the parent derives selectable children from existing graph, observation, and
  authorization truths,
- the child remains opaque and authoritative for its own workspace,
- the root can spawn a child, the UI can open the child's advertised URL and
  authenticate there, and the child can repeat that process for a grandchild,
- no ancestor instance is required in the request path after a child is
  selected,
- every CPI uses an explicit runtime-authority capability,
- all previous deployments continue to run,
- and Roadmap 0010 can build its UI entirely from ordinary graph projections,
  selectable-instance projections, and capability descriptors.

## Handoff

Roadmap 0010 should present the externally bootstrapped instance as the user's
entry screen and recursively display selectable `ControlPlaneInstanceBlock`
children. "Hub" may remain UI vocabulary, but the UI must not require a Hub
backend type. Selecting a child changes the active server/base URL to the
child's observed public Auth entry and begins a direct authentication session
there. Breadcrumbs may preserve spawning provenance, but they must not route
requests through ancestors.
