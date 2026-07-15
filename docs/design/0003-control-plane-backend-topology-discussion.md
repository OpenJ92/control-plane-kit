# Design Discussion 0003: Control Plane Backend Topology

Status: Draft discussion document

This document is a working notebook for the design discussion around the
backend structure of `control-plane-kit`.

It is intentionally not an ADR yet.  The purpose is to preserve the current
shape of the conversation, ask explicit questions, and edit the document as the
model becomes sharper.

## Why This Exists

Roadmaps 0005 and 0006 are the point where `control-plane-kit` stops being
mostly algebra and starts becoming operational.

The package already has a growing pure model:

```text
DeploymentRecipe
  -> DeploymentGraph
  -> validation
  -> current / desired graph versions
  -> diff
  -> ActivityPlan
  -> executor
  -> observed runtime state
```

The next design question is where that model lives when there are real
operators, real control-plane servers, real deployments, and real mutation.

The fragile boundary is:

```text
desired topology
  !=
observed running system
```

The control plane must make that boundary explicit.  It should not collapse
operator intent, stored graph state, runtime reality, and activity history into
one vague object.

## Prior Prototype: pottery-factory-deploy

The branch `codex/deploy-algebraic-control-plane-refactor` in
`pottery-factory-deploy` is a useful prototype.

It is not the design we should copy verbatim, because `control-plane-kit` now
has cleaner vocabulary around blocks, sockets, runtime contexts, and typed graph
descriptors.

It is still valuable because it explored:

- an outer control-plane registry,
- wakeable control-plane instances,
- two authorization layers,
- current and desired graph storage,
- read-only control-plane query routes,
- FastAPI and ASGI adapters,
- activity events,
- block catalogs,
- capabilities,
- and graph-builder workspace payloads.

The strongest prototype shape was:

```text
control-plane-kit UI
  -> registry / outer auth server
      -> selected remote ControlPlane server
          -> graph store
          -> runtime adapters
          -> block control routes
```

That shape feels directionally correct, but incomplete.

## Pottery Factory Ownership Analogy

Pottery Factory has a useful ownership pattern that should inform this project.

Some repositories are basis truth modules:

```text
Product
  owns product catalog truth

Inventory
  owns shelf, balance, and movement truth
```

Other repositories are activity/session modules:

```text
Transfer
  owns transfer sessions and lines
  submits inventory decrements through an Inventory provider

Audit
  owns audit sessions and counted lines
  submits balance truth through an Inventory provider

Receiving / Restock / Worklist / Production
  similarly own workflow/session intent
  and compose against basis truth modules
```

The key separation is:

```text
basis module
  owns source-of-truth mutation

activity module
  owns grouped operator intent
  records session/action state
  asks basis module to perform truth mutation through a narrow capability
```

For example, a transfer session does not own inventory truth.  It owns the
operator's transfer intent and, at completion, calls an inventory capability
such as `adjust_balance`.

An audit session does not own inventory truth.  It owns observations and, at
completion, calls an inventory capability such as `set_balance`.

This is the same shape as the control-plane design:

```text
Graph / workspace store
  owns topology truth

Runtime / block control surfaces
  own live runtime mutation truth

OperationSession
  owns grouped operator intent

ActivityPlan / ActivityRun
  owns interpreted and executed operational workflow
```

The control plane should not blur these.

An operation session should not directly mutate runtime truth.  It records
intent.  Planning interprets that intent into activities.  Execution asks the
appropriate truth-owning capability to perform the mutation.

This gives the control plane the same design law:

```text
Requests and approvals are not the same thing as source-of-truth mutation.
```

That law should guide CRUD-like parts of this package and future packages.

## Construction Law: Basis Before Workflow

This separation should determine roadmap and PR order.

The package should be built from the ground up:

```text
1. Basis truth modules
   Own durable facts and valid mutations.

2. Workflow/session modules
   Own grouped operator intent and approval/request state.

3. Planner/interpreter modules
   Interpret truth + intent into activity/data structures.

4. Effect/capability modules
   Execute approved activities by calling runtime or block capabilities.

5. Projection/interface modules
   Present read models to API, CLI, MCP, or UI.
```

This means PR topology should usually follow:

```text
truth ownership
  -> session/workflow ownership
    -> planning/interpreting
      -> execution/capability calls
        -> API/UI/MCP projections
```

The reason is practical.  If workflow modules are created before truth ownership
is clear, they will accidentally start owning truth.  If API routes are created
before the ownership model is clear, the route surface will freeze confusion
into the public contract.

Before executing Roadmaps 0005 and 0006, the project should classify the backend
modules by responsibility:

```text
Source-of-truth module
Workflow/session module
Interpreter/planner module
Effect/capability module
Projection/read-model module
```

Every roadmap issue should then state which category it affects and what
ownership boundary it must preserve.

Review question for every non-trivial PR:

```text
What truth does this own?
What intent does this record?
What capability does this call?
What projection does this expose?
Is it accidentally owning another module's truth?
```

## Module Taxonomy

This taxonomy is provisional, but it should guide the roadmap split and PR
topology.  The categories are ordered from basis truth toward outward
interfaces.

The point is not to create unnecessary layers.  The point is to prevent modules
that record intent, expose routes, or execute effects from accidentally becoming
the owner of durable truth.

| Category | Motivation | Owns | Does Not Own | Larger Topology Use |
| --- | --- | --- | --- | --- |
| Source-of-truth modules | Durable facts need one authoritative home. | Graph versions, workspace records, activity records, observed-state records, instance registry records, secret references. | User workflow intent, runtime side effects, UI projections. | Basis layer. Everything else asks these modules for facts or asks them to record accepted facts. |
| Workflow/session modules | Operator work is grouped and meaningful over time. | Operation sessions, operation actions, approval requests, workflow status. | Graph truth, runtime truth, block-local state. | Records what the operator is trying to do before planning or execution mutates anything. |
| Policy/authorization modules | Permission and approval need explicit logic, not scattered `if` statements. | Scope checks, approval rules, destructive-action classification, who may propose/approve/execute. | Graph persistence, runtime execution, UI behavior. | Guards transitions between workflow stages and prevents direct execution without authority. |
| Planner/interpreter modules | Pure data should explain what a change means before effects happen. | Graph diff interpretation, activity plan construction, recovery-plan construction, validation results. | Runtime mutation, durable history ownership, credentials. | Converts current/desired/workflow state into explicit activity structures. |
| Saga/compensation modules | Multi-step execution needs a grammar for compensation when later steps fail. | Pure saga program values, compensation ordering, interpretation result shape. | Domain truth, approval policy, graph semantics. | Gives activity execution a reusable compensation structure without making every executor ad hoc. |
| Effect/capability modules | Real systems are changed through narrow, explicit capability boundaries. | Runtime executor calls, block control-route calls, start/stop/switch/patch operations. | Operator intent, graph ownership, approval decisions. | Performs approved effects and returns observations/events. |
| Projection/read-model modules | Interfaces need stable query shapes without owning domain truth. | Operator graph views, workspace payloads, capability summaries, bounded event/log views. | Source-of-truth mutation, planning, execution. | Feeds API, CLI, MCP, and UI with curated read models. |
| Interface adapter modules | External transports should be thin. | HTTP route binding, CLI command parsing, MCP tool binding, request/response adaptation. | Core workflow rules, graph semantics, runtime effects. | Lets users and tools interact with the same underlying services without inventing separate models. |

### Source-of-Truth Modules

Source-of-truth modules are the basis modules.

They exist because durable data must have a single owner.  If graph versions,
activity events, observed runtime state, and instance lifecycle records can be
written from arbitrary workflow or route code, the system will become
impossible to reason about.

Likely source-of-truth modules:

```text
WorkspaceStore / GraphStore
  owns deployment workspaces and graph versions

ActivityHistoryStore
  owns operation sessions, actions, plans, runs, events, approvals

ObservedStateStore
  owns the latest observed runtime evidence and historical observations

InstanceRegistryStore
  owns hub-visible control-plane instance records and lifecycle metadata

SecretReferenceStore
  owns secret references and write-only secret assignment metadata
```

These modules are allowed to validate and mutate their own facts.  They should
not decide workflow policy by themselves.  For example, `GraphStore` can reject
an invalid graph version, but it should not decide whether Jacob has permission
to execute a destructive migration.

Motivation in the larger topology:

```text
Basis truth makes every later interpretation inspectable.
```

### Workflow/Session Modules

Workflow/session modules own grouped human intent.

They exist because an operator task is not reducible to a final graph.  The
session tells the story:

```text
who started the task
what they were trying to do
which actions belonged together
what plan was requested
who approved it
whether execution happened
how the task closed
```

Likely workflow/session modules:

```text
OperationSessionService
  starts/closes operator task sessions

OperationActionService
  records intentional edits or requests inside a session

ApprovalWorkflowService
  records requested/approved/rejected plan approvals

ActivityRunService
  opens and closes execution attempts
```

These modules should not mutate deployed runtime state.  They may ask
source-of-truth modules to persist session/action/approval records, but they do
not own graph truth or runtime truth.

Motivation in the larger topology:

```text
Workflow modules preserve operator intent before interpretation.
```

### Policy/Authorization Modules

Policy/authorization modules decide what is allowed.

They exist because permission, approval, and destructive-risk classification are
security-sensitive and should not be scattered across route handlers,
executors, or UI code.

Likely policy/authorization modules:

```text
HubAccessPolicy
  decides which instances an operator may see or wake

InstanceAccessPolicy
  decides what an operator may do inside one workspace

ApprovalPolicy
  decides whether a plan requires approval and which scope can approve it

DestructiveActivityPolicy
  classifies activities that require stronger confirmation
```

These modules should be mostly pure.  They can read facts and return decisions.
They should not write graph versions, execute runtime effects, or silently alter
plans.

Motivation in the larger topology:

```text
Policy modules make danger and authority explicit.
```

### Planner/Interpreter Modules

Planner/interpreter modules are the algebraic middle.

They exist so the system can explain what a desired change means before it
touches the real world.

Likely planner/interpreter modules:

```text
GraphValidator
  validates graph structure and socket compatibility

GraphDiff
  compares current and desired graph versions

ActivityPlanner
  turns graph diffs into activity plans

RecoveryPlanner
  constructs recovery or compensation plans from graph/history state
```

These modules should be pure or nearly pure.  They should not call Docker,
Cloudflare, block control routes, or databases except through explicitly passed
read models.

Motivation in the larger topology:

```text
Planner modules turn structure into executable meaning without effects.
```

### Saga/Compensation Modules

Saga/compensation modules provide a small grammar for multi-step execution with
compensation semantics.

They exist because activity execution is not only a list of commands.  If a
later step fails, successful earlier steps may need compensation in reverse
completion order.

The Pottery Factory `saga` repository gives the right law:

```text
Saga describes work; Saga does not decide domain truth.
```

That law should carry into `control-plane-kit`.

Likely saga/compensation modules:

```text
SagaProgram
  immutable activity/parallel composition values

SagaActivity
  protocol for compute + compensate

SagaInterpreter
  executes a saga and compensates completed activities on failure

CompensationRecord
  records compensation attempts and failures
```

This module should be small and generic.  It should not know about Docker,
Cloudflare, graph stores, approval scopes, or deployment policy.  Activity
execution can compile approved `ActivityPlan` nodes into saga activities when a
plan has meaningful compensation behavior.

Motivation in the larger topology:

```text
Saga modules provide compensation grammar without owning the deployment domain.
```

### Effect/Capability Modules

Effect/capability modules perform real-world operations.

They exist because effects should happen only through narrow capability
boundaries.

Likely effect/capability modules:

```text
RuntimeExecutor
  starts/stops/restarts runtime resources

BlockControlClient
  calls block-local control routes

RuntimeProvider
  adapts Docker, AWS, Kubernetes, host processes, or external references

SecretWriter
  writes secret values without exposing them through descriptors
```

These modules should not decide whether an action is approved.  They receive an
approved activity and attempt the effect.  Their return values should become
structured observations or activity events.

Motivation in the larger topology:

```text
Effect modules keep mutation explicit and attributable.
```

### Projection/Read-Model Modules

Projection/read-model modules curate data for humans and tools.

They exist because internal basis data is rarely the right shape for UI, CLI, or
MCP.  The UI wants an operator graph.  MCP wants bounded descriptors.  CLI wants
human-readable summaries.

Likely projection/read-model modules:

```text
OperatorGraphProjection
  turns internal graphs into UI-facing node/edge payloads

WorkspaceReadModel
  combines graph, catalog, capability, and status summaries

CapabilityReadModel
  exposes what nodes can do

ActivityTimelineReadModel
  exposes bounded workflow and execution history
```

These modules should not mutate truth.  They are allowed to aggregate and
redact.

Motivation in the larger topology:

```text
Projection modules make truth usable without making routes own truth.
```

### Interface Adapter Modules

Interface adapter modules are transport boundaries.

They exist so FastAPI, CLI, MCP, and future UI-facing adapters do not each grow
their own control-plane model.

Likely interface adapter modules:

```text
HubFastAPI
InstanceFastAPI
ControlPlaneCLI
ControlPlaneMCP
future UI bridge
```

They should translate external requests into calls on workflow, policy,
planning, projection, or effect services.  They should not contain the core
rules.

Motivation in the larger topology:

```text
Adapters expose the system; they do not define the system.
```

## Candidate Concrete Module Map

This is not final implementation structure.  It is the first attempt to map the
taxonomy into concrete package modules.

```text
control_plane_kit/stores/
  graph.py
  workspace.py
  activity_history.py
  observed_state.py
  instance_registry.py
  secrets.py

control_plane_kit/workflows/
  operation_sessions.py
  operation_actions.py
  approvals.py
  activity_runs.py

control_plane_kit/policies/
  hub_access.py
  instance_access.py
  approval.py
  destructive_activity.py

control_plane_kit/planning/
  validation.py
  diff.py
  activity_planner.py
  recovery_planner.py

control_plane_kit/saga/
  activity.py
  program.py
  interpreter.py

control_plane_kit/effects/
  runtime_executor.py
  block_control_client.py
  runtime_provider.py
  secret_writer.py

control_plane_kit/projections/
  operator_graph.py
  workspace_read_model.py
  capabilities.py
  activity_timeline.py

control_plane_kit/interfaces/
  hub_fastapi.py
  instance_fastapi.py
  cli.py
  mcp.py
```

The exact folder names may change.  The ownership boundaries should not change
casually.

## Persistence Direction

The current persistence direction is:

```text
Hub
  owns its own database
  likely Postgres
  stores operators, grants, instance registry, lifecycle metadata, and hub audit

ControlPlaneInstance
  owns its own persistence
  likely Postgres for local durable relational/session/history data
  uses graph-store adapter for topology graph data
  may later use Neo4j or Memgraph for graph topology
```

The motivation is practical:

- hub data is shared and long-lived across many instances;
- instance data is scoped to one deployment workspace;
- instances should be easy to create, pause, archive, move, or delete;
- Postgres containers make local durability and lifecycle handling explicit;
- graph storage should be behind an adapter so Neo4j/Memgraph can arrive later.

Important operational constraint:

```text
Local durable state must live outside ephemeral application containers.
```

Therefore, instance lifecycle design must specify where instance state lives.
For local deployments, the current preference is:

```text
instance Postgres container
  retained named volume
  lifecycle managed as part of the instance workspace

graph topology adapter
  JSON/descriptor-backed first if needed
  Neo4j/Memgraph-backed later
```

The first implementation should use Docker-backed Postgres for tests, but local
durable development should prefer Postgres-backed adapters over baking the
application model around SQLite.  Application code should depend on store
protocols, not on a particular database.

Real relational normalization is allowed and expected.  Session, action,
approval, plan, run, event, observation, and graph-version metadata should be
modeled as proper relations where that is the natural shape.

## Store Boundary Discussion

`WorkspaceStore` and `ActivityHistoryStore` are conceptual names, not settled
implementation names.

One possible split:

```text
WorkspaceStore
  owns deployment workspace identity
  owns current graph pointer
  owns desired graph pointer
  owns graph version metadata
  owns workspace lifecycle status

GraphStore
  owns graph-shaped topology values
  stores nodes, socket connections, runtime contexts, graph versions

ActivityHistoryStore
  owns operation sessions
  owns operation actions
  owns approvals
  owns activity plans
  owns activity runs
  owns activity events

ObservedStateStore
  owns latest and historical runtime observations
```

Another possible implementation is one physical instance relational database
that contains repositories for all relational instance tables, plus a separate
graph adapter:

```text
InstanceRelationalStore
  sessions table
  actions table
  approvals table
  plans table
  runs table
  events table
  observations table
  graph version metadata table

GraphTopologyStore
  graph nodes / edges / runtime contexts
```

The design question is conceptual ownership, not necessarily one database file
per concept.

The current preference is:

```text
one instance-owned persistence boundary
  with clearly separated repositories inside it
```

This lets the first local implementation use Postgres without losing the
ownership distinction between workspace truth, activity history, observed
state, and topology graph data.

## Module Service And Adapter Law

Each module should expose a service boundary.

Those services are the future seams for extracting pieces into separate
processes or microservices if that ever becomes useful.

The current package may compose modules in-process:

```text
OperationSessionService
  calls ActivityHistoryStore

ActivityPlannerService
  calls WorkspaceStore / GraphStore

ActivityExecutorService
  calls RuntimeExecutor and BlockControlClient
```

But the dependencies should pass through explicit service/provider/adaptor
interfaces rather than hidden imports of another module's persistence internals.

This preserves the future option:

```text
in-process service call today
  -> HTTP/gRPC/MCP/service route tomorrow
```

The rule:

```text
If a module boundary would become a microservice boundary later,
make the service contract visible now.
```

This does not mean over-engineer every method.  It means source-of-truth modules
advertise capabilities, and activity/workflow modules request those
capabilities through adapters.

## Directional Answers To Remaining Questions

- Approval records should exist from day one.
- The saga grammar from `pottery-factory-saga` appears generic enough to adapt
  closely, because it already avoids Pottery-specific domain truth.
- Local durable instance persistence should prefer Postgres over SQLite.
- A single local Postgres server/database setup is acceptable for early
  implementation, as long as instance ownership boundaries remain explicit in
  schema and service contracts.
- Graph topology should not be modeled as relational Postgres tables in the
  first pass.  Start with descriptor/blob storage behind a graph-store adapter,
  and design that adapter so Neo4j/Memgraph can replace or supplement it later.
- Saga should receive roadmap attention.  The Pottery Factory saga grammar is
  likely generic enough to adapt closely, but it should still be reviewed
  against control-plane activity execution needs before being copied.
- The first meaningful server should be the control-plane instance.
- The hub can arrive later as a registry/session/spawner around instances.
- Read interfaces should follow implementation order: instance first, then hub.
- Control-route protocol can begin with the generic capabilities already needed
  by package blocks and expand as new block types require it.

## Data Engineering Policy

The data engineering policy must be explicit before durable stores are built.

The central law:

```text
Every durable state transition is either:
  atomic inside one store transaction,
  or explicitly modeled as a multi-step workflow with durable events,
  retries, compensation, and visible partial failure.
```

### Store-Local Atomicity

When a transition is contained inside one relational store, it should use a real
database transaction.

Examples:

```text
create OperationSession + first OperationAction
save desired graph version + graph metadata
record PlanApproval
open ActivityRun + initial ActivityEvent
```

No route handler or service should write related tables without an explicit
transaction boundary.

### Cross-Boundary Effects

Operations that cross into runtimes, block control routes, graph stores,
external services, or secrets cannot pretend to be one ACID transaction.

Examples:

```text
write ActivityRun row
start Docker container
call router control route
update ObservedState
write ActivityEvent
```

These should be modeled as activity/saga workflows:

```text
record intent
execute step
record event
execute next step
record event
on failure:
  compensate where possible
  record compensation events
  leave run in failed/partial state
```

Partial failure is a first-class state, not an exception to hide.

### Idempotency

Mutation APIs should accept or derive idempotency keys.

Repeated requests should return the original result or safely report that the
operation was already applied.

This applies to:

- creating sessions,
- saving graph edits,
- approving plans,
- executing plans,
- starting runtime nodes,
- calling block control routes.

### Optimistic Concurrency

Graph edits should name the base graph version they were built from.

If another graph version has been accepted since then, the edit must reject or
require an explicit rebase.

```text
edit based on desired_graph_version = 7
current desired_graph_version = 8
=> reject / rebase
```

### Execution Locking

Execution must prevent two workers from running the same approved plan unless an
explicit retry/resume model says otherwise.

For Postgres-backed execution state, this likely means guarded status
transitions or row-level locks.

### Durable Requests Before Effects

External effects should be requested durably before they happen.

The outbox pattern may be useful:

```text
commit approval / execution request
  -> executor consumes durable request
  -> executor records activity events
```

This protects against process crashes between approval and execution.

### Auditability

Every meaningful transition should record:

- who requested it,
- which session it belongs to,
- what changed,
- when it happened,
- what graph/version it was based on,
- what result occurred,
- and whether any compensation happened.

### Secrets

Secrets must not appear in graph descriptors, activity events, plans, logs, or
read models.

Secret values are write-only or represented by stable secret references.

### Migration Discipline

Once relational persistence exists, schema changes require migrations.

Migrations should be tested against empty databases and existing databases.

## Current Working Shape

The backend likely has at least two major server concepts.

## Current Agreed Direction

The current shared direction is:

```text
Hub
  -> authenticates operators
  -> records which control-plane instances they own or can access
  -> can create, wake, pause, stop, archive, or deconstruct instances
  -> grants short-lived access into one selected instance

ControlPlaneInstance
  -> owns one deployment workspace
  -> maintains current / desired / observed graph state for that workspace
  -> owns activity sessions, plans, runs, and events
  -> holds the authority needed to call that deployment's control routes
  -> executes approved topology mutations against runtimes and blocks
```

### Resolved Recursive Identity And Capability Law

The Hub and a user-selected control plane are not fundamentally different
objects. Both are control-plane instances, and child-management functionality
is not intrinsically Hub-only. Their distinction is configuration, position in
the ownership tree, admission policy, and enabled capabilities.

In type-like notation:

```text
ManagedNode
  = DeployBlockNode
  | ControlPlaneInstanceNode

ChildAdmission
  = DeployBlocksOnly
  | InstancesOnly
  | Mixed

ControlPlaneInstance
  = control plane over Graph[ManagedNode]
```

An ordinary leaf deployment instance commonly uses `DeployBlocksOnly`. A root
Hub commonly uses `InstancesOnly`. A composite instance may use `Mixed` and
manage both its own deployment blocks and subordinate control-plane instances.
These are policies over the same object, not separate implementations. A parent
must not directly mutate arbitrary application blocks belonging to a child's
managed deployment.

Reusable instance modules and capabilities include:

- user identity and ownership,
- the child-instance registry,
- child-instance lifecycle planning and execution,
- discovery of child instance API endpoints,
- delegated credentials or sessions,
- and authenticated proxying from the frontend to a selected child instance.

Any instance may receive those capabilities when its policy permits child
management. The root Hub adds public entry-point and root identity concerns,
but not a new control-plane species.

Each child instance exposes a typed control API in the same broad sense that a
router or load balancer exposes a typed control API. The parent instance can query
health and capabilities, start or stop the child through its runtime, and proxy
authorized instance-specific requests. The child still owns its workspace,
plans, approvals, execution, and history; proxying does not transfer that truth
to the parent.

```text
Frontend
  -> Root instance API
      authenticate user
      select owned child instance
      proxy authorized request
        -> Child ControlPlaneInstance API
            -> child workspace / graph / activity authority
```

This recursion is intentionally hidden in the ordinary user experience.  The
UI presents a Hub screen followed by a selected deployment workspace.  The
algebra and runtime interpreters retain the recursive identity so the Hub can
deploy, wake, stop, archive, reconstruct, and observe child instances through
the same planning machinery used elsewhere.

There is one unavoidable bootstrap boundary: the first root Hub must be
started by an external bootstrap recipe or an already-running parent.  After
bootstrap, its child-instance lifecycle is ordinary control-plane work.

The hub is expected to be long-lived.  It is the registry and entry point.  It
may itself be deployable by the same kind of graph machinery, but conceptually
it is the outer home server.

A control-plane instance is expected to be recoverable.  It may be running,
sleeping, paused, stopped, archived, or deconstructed.  Deconstructing the
server process should not necessarily mean losing the deployment workspace
forever.

The tentative recovery model is:

```text
instance runtime can disappear
instance durable record should remain
```

At minimum, the hub should retain enough instance metadata to reconstruct or
reconnect to the workspace later.  There are two possible levels:

1. Final-state recovery:
   the hub or backing store keeps the latest graph snapshot and enough metadata
   to recreate the instance.
2. Full-history recovery:
   the hub or backing store preserves graph versions, operation sessions,
   activity plans, activity runs, events, and observed-state snapshots.

The stronger design preference is full-history recovery, but it may not be the
first implementation.  The important rule is that lifecycle cleanup must be
explicit about what is retained and what is discarded.

One control-plane instance should own one deployment workspace.  Multiple
instances pointing at the same realized deployment is treated as unsafe unless a
future design introduces explicit locking or leader election.

An instance acting as a parent is intentionally lighter with respect to each
child's internal deployment semantics. The responsibility distinction is:

```text
parent capabilities = instance registry + ownership + lifecycle + authenticated proxy
child self authority = child workspace + graph/activity authority
```

The parent should not absorb a child's deployment graph semantics merely because
it is itself a control-plane instance. It may hold an instance ID,
owner/grant records, lifecycle state, endpoint/wake metadata, and enough
retained recovery metadata to recreate or reconnect a child.  The child owns
its operational graph and activity machinery.

In graph-language terms, a control-plane instance can itself be thought of as a
small deployment graph:

```text
instance auth boundary
  -> instance application code
  -> graph/activity store
  -> runtime/control-route credentials
```

This is not necessarily how the first implementation is physically split, but
it is useful for understanding that an instance is not a magical singleton.  It
is a deployable service with authority over one workspace.

The first real server should probably be the control-plane instance server,
because it contains the meaningful graph/workspace/activity semantics.  The hub
can begin as a small registry/login shell.

The graph store should be behind a protocol/interface.  The first adapters may
be descriptor/JSON-backed, but the API should be shaped so a future Neo4j or
Memgraph adapter does not force application-level rewrites.

The desired store law is:

```text
ControlPlaneInstance depends on GraphStore behavior,
not on JSON files, process memory, or a particular graph database.
```

## Boundary Map

There are three different places in the system:

```text
1. Hub
2. ControlPlaneInstance
3. Deployed application graph
```

### Hub Boundary

The hub is the front door.

It owns:

- users,
- login,
- which control-plane instances exist,
- who can access each instance,
- whether an instance is running, stopped, paused, archived, or deconstructed,
- wake/start/stop/deconstruct commands for instances,
- and hub-level audit history.

The hub does not own the deployment graph's internal operational truth.

The hub can decide:

```text
Jacob is allowed to open the Pottery Factory production control plane.
Jacob is allowed to wake that control plane.
Jacob is allowed to receive a session token for it.
```

The hub should not directly decide:

```text
switch auth-router from auth-v1 to auth-v2
start api-v2
patch a runtime variable
query a deployment node's logs
```

Those belong to the selected control-plane instance.

### ControlPlaneInstance Boundary

A control-plane instance is the brain for one deployment workspace.

It owns:

- current graph,
- desired graph,
- observed runtime state,
- activity sessions,
- activity plans,
- activity runs,
- activity events,
- control-route credentials,
- runtime executor credentials,
- workspace-local authorization,
- and execution state.

It can decide:

```text
Here is the current graph.
Here is the desired graph.
This edit is valid.
This edit produces this diff.
This diff produces this ActivityPlan.
This plan requires approval.
Now I will call Docker / AWS / router control routes / block control routes.
Here are the events from that execution.
Here is the observed state afterward.
```

The instance is more than a proxy because it remembers, validates, plans,
executes, observes, and records.

It should still remain an orchestration boundary, not a dumping ground for
logic.  Integrating modules into a `ControlPlaneInstance` should mostly mean
importing and composing the separated modules:

```text
ControlPlaneInstance
  imports stores
  imports workflow/session services
  imports policy services
  imports planners
  imports saga/compensation grammar
  imports effect/capability clients
  exposes API routes / application service methods
  orchestrates transactions between them
```

The instance API should coordinate transactions.  It should not absorb the
truth ownership, workflow ownership, policy logic, planning logic, compensation
grammar, or effect implementation into one large class.

### Deployed Application Graph Boundary

The deployed graph is the actual running system.

It contains nodes such as:

- application servers,
- databases,
- storage services,
- tunnels,
- routers,
- load balancers,
- rate limiters,
- multiplexers,
- and future protocol-specific blocks.

Some nodes are ordinary application code.  Some nodes are controllable blocks.

The deployed graph does not own its own topology history.  It runs.  A
controllable block may expose local control routes:

```text
GET  /__control/health
GET  /__control/capabilities
POST /__control/router/targets
POST /__control/router/switch
```

Those routes are local capabilities, not the full control plane.

The control-plane instance calls those routes while executing an approved
activity.

### Boundary Law

The short law is:

```text
Hub grants access.
Instance interprets topology.
Runtime/block nodes perform local effects.
```

The normal request path is:

```text
User / UI / CLI / MCP
  -> Hub
      login, list instances, wake instance, issue session

User / UI / CLI / MCP
  -> ControlPlaneInstance
      edit graph, plan, approve, execute, inspect history

ControlPlaneInstance
  -> Deployed graph nodes
      start/stop runtimes, call control routes, observe health/logs
```

### Control Plane Hub

The hub is the durable home server.

It likely owns:

- operator identity,
- which control planes an operator can see,
- outer authorization,
- control-plane wake/sleep lifecycle,
- high-level audit history,
- perhaps global templates or saved deployment workspaces,
- and the list of reachable control-plane instances.

The hub should not directly mutate arbitrary application blocks.  It should
select, authorize, and coordinate access to a control-plane instance.

### Control Plane Instance

A control-plane instance is attached to one deployment workspace or one realized
deployment.

It likely owns:

- current graph versions,
- desired graph versions,
- observed runtime state,
- block catalog projection,
- runtime contexts,
- runtime adapters,
- block control-route clients,
- local activity sessions,
- activity plans,
- activity runs,
- activity events,
- bounded logs,
- node capabilities,
- and execution state.

The instance is the thing that knows how to speak to the deployment's runtime
surfaces.

### Runtime Executors

Runtime executors know how to perform effects in a runtime environment.

Examples:

- Docker executor,
- external endpoint/reference executor,
- future ECS/Fargate executor,
- future EC2 executor,
- future Kubernetes executor,
- future RDS/S3 reference interpreters.

They should not be the topology model.  They are interpreters for a portion of
the topology.

### Block Control Routes

Some blocks expose control routes.

Examples:

- router target registry routes,
- load balancer weight routes,
- rate limiter configuration routes,
- multiplexer observer routes,
- runtime variable contract routes,
- health/status routes.

These routes mutate or inspect block-local runtime state.  They are not the same
as user traffic routes.

The control plane should call these routes only through explicit authorization
and activity execution.

## Desired Mutation Flow

The mutation flow should look like this:

```text
operator intent
  -> OperationSession
  -> desired DeploymentGraph edit
  -> validation
  -> compare against current / observed state
  -> GraphDiff
  -> ActivityPlan
  -> approval
  -> ActivityRun
  -> runtime executor calls
  -> block control-route calls
  -> ActivityEvent*
  -> ObservedState
  -> persisted history
```

The control plane does not directly "edit a running system."  It records desired
topology, compares it to accepted and observed topology, builds an activity plan,
gets approval, executes through interpreters, and records what happened.

## Core Distinctions

### Desired Graph

The graph the operator wants.

It is intention, not proof.

### Current Graph

The graph the control plane has accepted as the current recorded topology.

It may still diverge from actual runtime reality if the world changed outside
the control plane.

### Observed State

The runtime evidence most recently observed by health checks, runtime adapters,
or control routes.

Observed state should not silently rewrite desired topology.

### Activity Plan

The interpreted transition from current/observed state toward desired state.

It is the morphism between graph states.

### Activity Run

One execution attempt of an activity plan.

### Activity Events

Structured events emitted during execution.

These are not raw logs.  They should be bounded and safe to query.

## Early Ownership Laws

- The graph is not the runtime.
- Runtime contexts are topology.
- Runtime executors are interpreters.
- Block control routes are capability surfaces.
- Application code remains ordinary unless it opts into runtime contracts.
- Mutation requires explicit authorization.
- Secrets can be set, referenced, or checked, but not read back.
- Observed state must be recorded separately from desired graph state.
- Activity history is part of the program, not incidental logging.

## Open Questions For Discussion

### Hub And Instance: Resolved Direction And Remaining Questions

Resolved:

- `ControlPlaneHub` is a user-facing root profile/name for the same
  `ControlPlaneInstance` object used at every level.
- `ManagedNode` is the closed sum of deploy blocks and child instance nodes.
- Child admission is explicit: deploy blocks only, child instances only, or a
  mixed graph.
- Access, registry, child lifecycle, delegation, and proxying are reusable
  instance capabilities rather than Hub-only functionality.
- One child instance owns one deployment workspace.
- A parent instance can create, wake, pause, stop, archive, deconstruct, and reconstruct
  child instances through activity planning and execution.
- The root instance is the authenticated frontend proxy to child instance APIs.
- A parent does not directly manage application blocks inside a child's opaque
  workspace.

Remaining questions:

1. Does the parent retain full child graph/activity snapshots, recovery metadata,
   or only references to child-owned stores?
2. Which child lifecycle states physically remove runtime resources, and which
   retain durable stores?
3. What is the delegated credential/session format between Hub and child?
4. Which protocol advertises child capabilities and endpoints to the Hub?
5. What bootstrap recipe starts the first root instance?

A control-plane instance is not merely a bearer-token holder, although
credential custody is one of its responsibilities.  It is the authority and
execution boundary for one managed graph:

```text
ControlPlaneInstance =
  deployment workspace store
  + runtime/control-route credential custody
  + activity planner/executor boundary
  + observed-state collector
  + local authorization enforcement
```

The instance may be thin at first, but if it owns activity plans, execution,
events, observed state, and control-route calls, it is more than an auth proxy.
The hub grants access to it; the instance performs or refuses the workspace
operation.

### Persistence

1. What is the first persistence backend?
2. Should graph versions be stored in SQLite, Postgres, JSON, or a graph
   database adapter?
3. Should graph descriptors be snapshotted by value, referenced by content
   hash, or both?
4. What activity history is required before mutation routes exist?
5. How much of the deploy prototype's `GraphStore` should influence the kit
   shape?

### Authorization

1. What scopes does the outer hub own?
2. What scopes does the local control-plane instance enforce?
3. How does a hub-issued session become valid at a selected instance?
4. Are read-only MCP tools allowed before mutation tools?
5. How are mutation tools separated from read-only tools?
6. What is the minimum acceptable local-development auth model?

### Control Routes

1. What is the standard route protocol for controllable blocks?
2. Which routes are read-only?
3. Which routes mutate runtime state?
4. Do all package-provided blocks need to implement the same mounted contract?
5. How does the control plane discover a block's routes and capabilities?
6. How do we keep user traffic routes separate from control routes?

### Activity Planning

1. Does Roadmap 0006 own operation sessions, or should sessions begin in
   Roadmap 0005?
2. Is an activity plan always generated from current graph and desired graph?
3. Does observed state participate in planning directly, or only as validation
   evidence?
4. What does safe replay mean?
5. What does pause/resume mean?
6. What activities must be durable before execution starts?

Discussion note:

`OperationSession` and `OperationAction` are not yet obvious names.  They are
trying to capture user/operator intent before it becomes a graph diff or
activity plan.

One possible shape:

```text
OperationSession
  = "Jacob is editing Pottery Factory production deployment at 2:15 PM"

OperationAction
  = "add api-v2 node"
  = "connect auth-router.active to api-v2.internal"
  = "request backend swap plan"
  = "approve plan-123"
```

Then:

```text
OperationAction*
  -> desired graph version
  -> diff current desired
  -> ActivityPlan
  -> ActivityRun
  -> ActivityEvent*
```

Decision:

The package should model explicit sessions/actions.  This follows the same
lesson as workflow repositories such as restock, transfer, receiving, and audit
in Pottery Factory: the grouping of actions is part of the domain truth.  A
deployment edit is not only a final graph.  It is an operator session in which
related actions are collected, interpreted, approved, executed, and later
understood.

Graph versions and activity plans are necessary, but not sufficient.  Without
operation sessions/actions, the package can answer "what graph changed?" but not
"what was the operator doing?"

The design should therefore include:

```text
OperationSession
  groups one coherent operator task

OperationAction
  records one intentional step inside that task

ActivityPlan
  records the interpreted operational plan produced from the session/graph state

ActivityRun
  records one execution attempt

ActivityEvent
  records bounded observations during that attempt
```

This makes the user-facing history session-shaped rather than log-shaped.

For undo, one candidate design is graph-history based:

```text
graph A -> graph B
undo = plan transition from graph B -> graph A
```

This is not commutative.  It is a reverse transition.  It will be valid only
when the reverse transition can be planned and executed safely.  Some activities
may be exactly reversible, some may require compensation, and some may be
irreversible.

The simplest durable basis for undo is therefore graph snapshots:

```text
graph version at time T1
graph version at time T2
plan(T2, T1)
```

Later, richer persistent data structures could preserve structural sharing or
edit history more elegantly, but the first design should probably not require
that sophistication.

Decision:

Undo/recovery is saga-coded.  A reverse graph plan is not automatically a true
undo of the real-world effects.

There are at least three different cases:

```text
Reversible
  Switch router target from A to B.
  Recovery can switch B back to A if A still exists and is healthy.

Compensating
  Start a new service, migrate traffic, then discover a failure.
  Recovery may start a replacement service, drain traffic, or restore from
  snapshot rather than literally undoing each prior command.

Irreversible
  Delete a database volume, send an external message, destroy an unmanaged
  resource, or discard a secret.
  Recovery cannot be inferred from graph diff alone.
```

So the system should not promise:

```text
plan(A, B) inverse = plan(B, A)
```

The safer law is:

```text
plan(B, A)
  is a requested recovery transition,
  not proof that every prior effect is reversible.
```

Some recoveries may even look like:

```text
plan(null, A)
```

That means reconstructing a desired known-good graph from scratch rather than
trying to reverse each activity that produced the current state.

Because many execution activities are consequential, execution should require
explicit approval gates.  Some activity kinds should require stronger approval
than ordinary graph edits.

Potential approval levels:

```text
graph-edit approval
  save desired topology

plan approval
  accept the interpreted activity plan

execution approval
  start runtime mutation

destructive approval
  confirm actions such as delete, destroy, irreversible migration, or secret
  discard
```

This may feel like two-factor approval for serious operations, and that is
probably correct.  The control plane is allowed to be careful because it is
operating real systems.

This is not only a UI concern.  The backend model should preserve the workflow
truth:

```text
session author
  may propose topology changes

activity planner
  produces a concrete plan with consequences

approver
  may be a different operator with stronger scope

executor
  only runs an approved plan
```

This supports a professional deployment workflow:

```text
network engineer proposes change
manager/admin reviews ActivityPlan
manager/admin approves execution
control plane executes and records events
```

The UI should make the danger visible, but the backend must enforce the
approval boundary.  A dangerous activity is not safe merely because the UI made
it look scary.

Potential approval records:

```text
PlanApproval
  plan_id
  approved_by
  approved_at
  approval_scope
  approval_level
  comment

ExecutionApproval
  run_id or plan_id
  approved_by
  approved_at
  destructive_activity_ids
  confirmation_text
```

Potential scopes:

```text
graph:edit
plan:request
plan:approve
plan:execute
plan:approve-destructive
runtime:destroy
secret:rotate
history:delete
```

The guiding workflow law:

```text
The person who proposes a consequential change does not necessarily have the
authority to execute it.
```

### User Interface Shape

1. Does the UI talk first to the hub, then to a selected instance?
2. Does the UI ever talk directly to block control routes?
3. Is the graph-builder workspace an instance route or a hub route?
4. Where does the block catalog live?
5. How should the UI display boxed subgraphs, runtime contexts, and application
   blocks?

### Relationship To Existing Package Blocks

1. Are package-provided HTTP blocks currently "teaching/local demo" blocks only?
2. Which parts of their control route shape should become durable protocol?
3. Which implementation details should remain explicitly non-production?
4. How do future nginx, HAProxy, Envoy, Cloudflare, AWS, and Kubernetes
   implementations satisfy the same block/control contracts?

## Roadmap Impact

Roadmap 0005 should likely become:

```text
read/query interfaces over hub + instance + graph store
```

This includes:

- graph queries,
- workspace payloads,
- block catalogs,
- contracts,
- capabilities,
- bounded logs/events,
- read-only MCP tools,
- read-only CLI commands,
- and a FastAPI control-plane server adapter.

Roadmap 0006 should likely become:

```text
mutation/session/diff/activity/execution interfaces
```

This includes:

- operation sessions,
- desired graph edits,
- validation,
- graph diff,
- activity planning,
- approval gates,
- executor interfaces,
- runtime executor calls,
- block control-route calls,
- events,
- observed state updates,
- pause/resume/replay behavior.

## Provisional Roadmap Partition

This is not finalized.  It is a candidate partition that came out of the
discussion and should be revisited after the module taxonomy is defined.

The current Roadmaps 0005 and 0006 may be too compressed.  A safer partition
may be:

```text
0005 Control Plane Backend Topology
  Define Hub / Instance / DeployedGraph boundaries.
  Define persistence ownership.
  Define lifecycle states.
  Define auth/session shape.
  Mostly design + core types.

0006 Control Plane Read Interfaces
  Hub read routes.
  Instance read routes.
  Graph/workspace/capability/status/event query surfaces.
  CLI/MCP read-only adapters.

0007 Activity Sessions And Planning
  OperationSession.
  Desired graph edits.
  Graph diff.
  ActivityPlan generation.
  Approval boundary.
  No runtime execution yet.

0008 Activity Execution And Runtime Mutation
  Executor interface.
  Docker/runtime executor.
  Block control-route clients.
  ActivityRun / ActivityEvent recording.
  Pause/resume/failure behavior.

0009 Recursive Control Plane Instances And Root Hub
  Represent child instances as managed nodes.
  Generalize registry, lifecycle, authorization, and proxy capabilities.
  Compile child lifecycle through the approved activity executor.
  Preserve parent registry truth separately from child workspace truth.

0010 Operator UI / MCP / Cross-Language Contracts
  Existing visual UI and cross-language concerns consume the recursive model.
```

This provisional split should be tested against the construction law:

```text
truth ownership
  -> workflow/session ownership
    -> planning/interpreting
      -> execution/capability calls
        -> API/UI/MCP projections
```

If the module taxonomy reveals a better topological order, this partition
should change.

## Working Mathematical Frame

The tentative objects are:

- `ControlPlaneHub`
- `ControlPlaneInstance`
- `ManagedNode`
- `ChildAdmission`
- `InstanceRelationship`
- `DeploymentWorkspace`
- `DeploymentGraph`
- `GraphVersion`
- `ObservedState`
- `OperationSession`
- `OperationAction`
- `ActivityPlan`
- `ActivityRun`
- `ActivityEvent`
- `RuntimeExecutor`
- `BlockControlRouteClient`

The tentative transformations are:

```text
operator intent -> desired graph edit
current graph x desired graph -> graph diff
graph diff -> activity plan
activity plan x approval -> activity run
activity run -> observed state + events
observed state + accepted result -> current graph version
```

The tentative laws are:

- Pure planning does not perform effects.
- Effectful execution records events.
- Runtime mutation requires an activity.
- Activity execution must be attributable to an operator/session.
- Secrets are never returned in descriptors.
- Observed state does not silently become desired state.
- A control route mutation is not a user traffic request.

## Next Discussion Prompt

The first design question to resolve:

```text
What exactly does the hub own, and what exactly does one control-plane instance own?
```

Once that ownership boundary is clear, the API routes and persistence model
should become much easier to reason about.
