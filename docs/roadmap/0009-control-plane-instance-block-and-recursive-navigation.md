# Roadmap 0009: Control Plane Instance Block And Recursive Navigation

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
block and make recursive instance navigation a projection over existing graph,
observed-state, authorization, and control-route machinery.

This roadmap should provide:

- a package-provided `ControlPlaneInstanceBlock` constructor,
- a Docker image/runtime implementation for the instance server,
- declared HTTP and Postgres sockets,
- declared health, read, planning, execution, and child-navigation
  capabilities,
- graph compilation and execution with no special node case,
- child discovery derived from ordinary graph topology,
- endpoint and health lookup derived from observed state,
- access filtering derived from authorization records,
- authenticated navigation/proxying between instances,
- bootstrap and recovery recipes,
- recursive UI/API/CLI/MCP projections,
- and a live Docker demonstration of an instance deploying another instance.

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
from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DockerImageImplementation,
    Protocol,
    ProviderSocket,
    RequirementSocket,
)


def control_plane_instance_block(
    block_id: str,
    *,
    image: str = "control-plane-kit-instance:latest",
) -> ApplicationBlock:
    """Describe one deployable control-plane instance server."""

    return ApplicationBlock(
        spec=BlockSpec(
            role_id=block_id,
            display_name="Control Plane Instance",
            health_path="/health",
            metadata={"server_kind": "control-plane-instance"},
        ),
        implementation=DockerImageImplementation(
            image=image,
            command=("python", "-m", "control_plane_kit.servers.instance"),
            ports={"instance-api": 8000, "control-api": 8001},
        ),
        sockets=BlockSockets(
            requirements=(
                RequirementSocket(
                    "database",
                    Protocol.POSTGRES,
                    ("CONTROL_PLANE_DATABASE_URL",),
                ),
            ),
            providers=(
                ProviderSocket("instance-api", Protocol.HTTP),
                ProviderSocket("control-api", Protocol.HTTP),
            ),
        ),
    )
```

The exact image, command, ports, and capability representation may change
during implementation. The law may not:

```text
The instance is constructed through the existing block product form.
```

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
    children = (
        node
        for node in graph.nodes
        if node.metadata.get("server_kind") == "control-plane-instance"
    )
    return tuple(
        project_selectable_instance(node, observed, grants)
        for node in children
        if grants.may_enter(actor, node.node_id)
    )
```

The implementation should replace metadata string inspection with the package's
eventual typed block/specification form. The snippet demonstrates the
projection boundary, not the final discriminator.

Relational data may still be needed for operator grants, delegated sessions,
endpoint history, lifecycle events, and recovery metadata. Those records do not
constitute a second graph or a Hub-owned child topology registry.

## Parent And Child Truth

The parent owns facts about its deployment of the child block:

```text
parent current/desired graph
parent observation of child endpoint and health
parent activity history for starting/stopping/replacing the child block
parent authorization grants for traversing to that block
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

## Navigation And Authentication

The user enters through the externally bootstrapped instance and selects child
instances recursively:

```text
UI -> bootstrapped instance
        GET selectable child instances
        select child-a
        proxy or delegate authenticated request
          -> child-a instance
               GET selectable child instances
               select child-a-1
               ...
```

The stable reference is the block/instance identity, not its current URL. The
parent resolves that identity through observed runtime endpoints.

The first implementation should prefer a parent proxy:

```text
https://control.example/instances/child-a/workspace
```

The parent resolves `child-a` to its current endpoint and forwards the request.
This keeps runtime URL changes out of frontend navigation. A later direct mode
may return a public child URL plus a short-lived delegated session.

Every boundary authorizes independently:

```text
operator -> parent
  parent authorizes traversal to child block
  parent creates audience- and scope-bound delegation
  parent -> child
    child authorizes the requested workspace operation
```

Credentials must be short-lived, audience-bound, scope-bound, attributable,
redacted, and unavailable as raw control secrets to browser code.

## Lifecycle Uses The Existing Pipeline

Adding a child is an ordinary graph edit:

```text
add ControlPlaneInstanceBlock + database + socket connection
  -> validate DeploymentRecipe
  -> compile DeploymentGraph
  -> diff current and desired graphs
  -> create ActivityPlan
  -> approve
  -> execute StartNode / Connect / WaitForHealth activities
  -> record observed endpoint and health
```

Stopping, replacing, or deleting a child uses the same graph-diff and activity
machinery. Roadmap 0009 must not create a child-specific executor.

The child becomes selectable only after the block is healthy and the parent can
query its instance protocol. Partial startup remains visible through Roadmap
0008 activity events and saga compensation.

## Suggested Issue Topology

1. Record the `ControlPlaneInstanceBlock : DeployBlock` ADR.
   - Reject the `ManagedNode` sum and Hub-specific graph grammar.
   - Define recursion as one application block owning another deployment graph.
   - Define the root as externally bootstrapped position, not a new type.
   - Preserve child opacity and independent truth ownership.

2. Define the control-plane instance server block specification.
   - Implement it as an `ApplicationBlock` constructor in `servers/`.
   - Declare instance API, control API, health, and Postgres sockets.
   - Advertise typed capabilities without relying on display metadata.
   - Keep secrets out of descriptors.

3. Package the instance server runtime implementation.
   - Build a Docker image for the existing FastAPI instance server.
   - Accept database and runtime configuration through requirement sockets.
   - Expose health, read, command, and control routes on declared providers.
   - Run with ordinary Docker runtime interpretation.

4. Prove ordinary graph compilation and execution.
   - Place the instance block and its Postgres block in a `DockerRuntime`.
   - Connect their sockets.
   - Compile without a new graph node case.
   - Start and observe them through Roadmap 0008 execution machinery.
   - Preserve all existing deployment examples.

5. Reconcile the existing instance registry concepts.
   - Identify which records are duplicated topology and remove that role.
   - Retain normalized grants, delegated sessions, lifecycle history, and
     recovery metadata where they are independently authoritative.
   - Derive immediate child topology from graph nodes.
   - Derive endpoint and health from observed state.
   - Add migration notes for existing Postgres schemas and APIs.

6. Add typed recursive child discovery projections.
   - Identify instance blocks through a typed specification/capability.
   - Join graph, observed state, and grants without mutating any source.
   - Return stable identity, display name, health, endpoint status, and bounded
     capabilities.
   - Expose the same projection to FastAPI, CLI, and read-only MCP.

7. Add authenticated recursive navigation.
   - Resolve stable child identity to current observed endpoint.
   - Add strict parent proxying with timeout, body, method, path, and header
     policies.
   - Add audience- and scope-bound delegation.
   - Require authorization at parent and child.
   - Preserve original actor attribution in child activity history.

8. Add bootstrap and recovery recipes.
   - Bootstrap the first instance externally.
   - Recover it from retained Postgres and graph descriptor state.
   - Treat every descendant as an ordinary block deployment.
   - Document stop, remove, archive, and retained-data behavior.

9. Add a recursive Docker demonstration.
   - Bootstrap a root instance.
   - Use its graph workflow to add a child instance and child Postgres.
   - Execute the approved plan.
   - Discover the child from graph plus observed state.
   - Navigate through the root proxy to the child workspace.
   - Let the child own a small application graph.

10. Perform security, data, design, and reliability hardening.
    - Concurrent child block creation and idempotency.
    - Duplicate stable identities.
    - Endpoint staleness and child unavailability.
    - Delegation audience, scope, expiry, replay, and redaction.
    - Proxy path confusion, header injection, request smuggling, and size limits.
    - Partial startup, failed compensation, retained Postgres, and recovery.
    - Verify no second topology registry remains.

11. Document and hand off recursive projections to Roadmap 0010.
    - Include root, child, grandchild, stopped child, and failed-start fixtures.
    - Curate block-construction and navigation snippets.
    - Make the UI derive navigation from selectable instance projections.
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
  -> start child database
  -> start child instance block
  -> wait for health
  -> observe provider endpoints
  -> expose child in selectable-instance projection
```

## Non-Goals

- Do not implement a separate Hub server or Hub domain model.
- Do not add a `ManagedNode` sum solely for control-plane instances.
- Do not add a special child-instance compiler or executor path.
- Do not create a second registry as the source of child topology.
- Do not inline a child's graph into its parent's graph.
- Do not let a parent read or mutate a child's database directly.
- Do not expose raw delegated or control credentials to the browser.
- Do not require direct browser-to-child communication initially.

## Validation

- `ControlPlaneInstanceBlock` is an ordinary `ApplicationBlock` and therefore a
  `DeployBlock`.
- Existing recipe, graph, diff, and execution code accepts it without a new node
  alternative.
- Socket connections supply its Postgres and HTTP requirements normally.
- A parent discovers immediate child instances from graph plus observed state.
- Authorization filters selectable children independently of topology truth.
- No parallel instance registry claims to own child topology.
- Parent and child both authorize proxied operations.
- Delegated credentials are scoped, expiring, and redacted.
- A child owns its own workspace, plans, runs, events, and observations.
- Root, child, and grandchild navigation works in the Docker demonstration.
- Existing deployment recipes remain live-runnable.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Definition Of Done

Roadmap 0009 is complete when:

- the package provides a deployable control-plane instance server block,
- no Hub-specific server implementation is needed,
- no special recursive graph node or compiler case is needed,
- the bootstrapped instance can deploy a child through the ordinary graph edit,
  planning, approval, and execution pipeline,
- the parent derives selectable children from existing graph, observation, and
  authorization truths,
- the child remains opaque and authoritative for its own workspace,
- recursive navigation and authorization work through at least three levels in
  a live Docker example,
- all previous deployments continue to run,
- and Roadmap 0010 can build its UI entirely from ordinary graph projections,
  selectable-instance projections, and capability descriptors.

## Handoff

Roadmap 0010 should present the externally bootstrapped instance as the user's
entry screen and recursively display selectable `ControlPlaneInstanceBlock`
children. "Hub" may remain UI vocabulary, but the UI must not require a Hub
backend type. Selecting a child changes the current instance focus; it does not
enter a different application species.
