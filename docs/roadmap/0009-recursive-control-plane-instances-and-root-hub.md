# Roadmap 0009: Recursive Control Plane Instances And Root Hub

Status: Draft
Depends on: Roadmap 0001 through Roadmap 0008

## Motivation

The package does not need one implementation for a Hub and another for an
ordinary control-plane instance. It needs one recursive control-plane object.

A `ControlPlaneInstance` owns a workspace, plans changes to a managed graph,
executes approved activities, observes results, and exposes authenticated
control and read interfaces. The nodes in that managed graph may include both
ordinary deployment blocks and child control-plane instances:

```text
ManagedNode
  = DeployBlockNode
  | ControlPlaneInstanceNode

ControlPlaneInstance
  = control plane over Graph[ManagedNode]
```

"Hub" is therefore the user-facing name for a root `ControlPlaneInstance`
configured to admit and manage child instances. It is not a second server
species or an unrelated object hierarchy. Any instance may receive the same
child-management capabilities when its admission policy permits them.

This matters beyond conceptual neatness. A deployment workspace may itself
need subordinate workspaces. A team may begin with a leaf instance that manages
application blocks, then later attach a separately governed child instance
without replacing the parent implementation. The frontend may hide this
recursion behind Hub and workspace screens, but the backend should preserve the
recursive law.

## Goal

Implement the recursive instance composition layer that sits between runtime
execution and the operator UI.

This roadmap should provide:

- one `ControlPlaneInstance` server/application shape,
- a typed `ControlPlaneInstanceNode` managed-node descriptor,
- explicit child-admission policy,
- parent/child instance registry and lifecycle truth,
- delegated authorization between parent and child,
- an authenticated parent proxy to child instance APIs,
- child capability and protocol discovery,
- recursive child lifecycle planning through the activity pipeline,
- recursive read projections suitable for UI, CLI, and MCP,
- root bootstrap and recovery documentation,
- and a live Docker demonstration with a root instance and child instance.

## Core Product Form

The intended shape is:

```text
ChildAdmission
  = DeployBlocksOnly
  | InstancesOnly
  | Mixed

ControlPlaneInstance
  = InstanceIdentity
  x Workspace
  x ManagedGraph[ManagedNode]
  x ChildAdmission
  x StoreBundle
  x WorkflowServices
  x Policies
  x Planner
  x Executor
  x ReadCommandAPI
```

Conventional profiles remain useful as constructors and UI labels:

```text
rootHub
  = ControlPlaneInstance(admission = InstancesOnly, root = True)

deploymentWorkspace
  = ControlPlaneInstance(admission = DeployBlocksOnly)

compositeWorkspace
  = ControlPlaneInstance(admission = Mixed)
```

These profiles configure the same object. They must not produce divergent Hub
and Instance implementations.

## Ownership Laws

### Parent Knowledge Is Not Child Truth

The parent stores a registry record describing how it knows and controls a
child. The child owns its workspace and operational history:

```text
parent InstanceRecord(child)
  = parent-side identity, endpoint, lifecycle, ownership, and recovery metadata

child WorkspaceRecord
  = child-owned graph, sessions, plans, approvals, runs, events, and observations
```

The parent must not read or mutate the child's database directly. It reaches
child truth through the child's authenticated API. Proxying does not transfer
ownership.

### Recursive Ownership Is A Rooted Tree

The first implementation should enforce:

```text
parent(child) is unique
root.parent is absent
no instance is an ancestor of itself
```

This prevents two active parents from believing they control the same child.
Multi-controller coordination would require leader election or another
explicit protocol and is outside this roadmap.

### A Child Is Opaque At Its Boundary

The parent may know a child's identity, endpoint, lifecycle state, health,
capabilities, protocol version, and bounded summary. Detailed graph and activity
data are queried through the child API. The UI may render the child as a box and
navigate into it without flattening its graph into the parent.

### Lifecycle Is Planned Work

Creating, waking, stopping, archiving, deconstructing, or reconstructing a
child is not an incidental registry update. It is an approved activity plan
interpreted by the Roadmap 0008 executor and recorded as activity history.

### Recursion Has A Bootstrap Boundary

The first root instance must be started by an external bootstrap recipe or an
already-running parent. The model must say this plainly rather than pretending
that recursion creates its own first process.

## Security Model

The browser authenticates to the root instance. Traversing to a child requires
two authorization decisions:

```text
operator -> parent instance
  parent authorizes ownership and traversal
  parent obtains short-lived child-scoped credentials
  parent proxies the request
    -> child instance
       child authorizes the requested operation
```

Delegated credentials must be:

- short-lived,
- audience-bound to one child,
- scope-bound to the requested capabilities,
- attributable to the original operator and parent,
- redacted from descriptors, events, and logs,
- and never returned as raw child control secrets to the browser.

The root instance's public authentication responsibilities are additional
configured capabilities around the same instance object. They do not justify a
separate Hub domain model.

## Suggested Issue Topology

1. Record the recursive instance ADR.
   - Define `ManagedNode` as the closed sum of deploy blocks and instance nodes.
   - Define `ChildAdmission` as a closed policy type.
   - Record the one-parent, acyclic ownership law.
   - Record the parent-registry versus child-workspace truth boundary.
   - Record root bootstrap as an explicit external boundary.

2. Add the control-plane instance managed-node descriptor.
   - Add stable instance identity and protocol version.
   - Advertise provider/requirement sockets needed for runtime placement and
     control access.
   - Advertise lifecycle, health, read, command, and proxy capabilities.
   - Keep secret material out of the descriptor.

3. Add child-admission validation.
   - Support `DeployBlocksOnly`, `InstancesOnly`, and `Mixed`.
   - Reject disallowed child node types before graph persistence.
   - Reject self-parenting, ancestry cycles, and duplicate active ownership.
   - Keep admission policy independent of UI labels such as "Hub."

4. Generalize control-plane authorization policy.
   - Replace Hub-versus-Instance policy species with scoped control-plane
     authorization and delegation policies.
   - Preserve root login, ownership, workspace, planning, approval, execution,
     lifecycle, and proxy scopes.
   - Prove parent authorization does not bypass child authorization.

5. Generalize the instance registry to parent/child relationships.
   - Store parent ID, child ID, endpoint, lifecycle state, ownership grants,
     protocol/capability summary, runtime locator, and recovery metadata.
   - Use normalized Postgres records for relational lifecycle truth.
   - Keep child workspace state out of the parent registry.
   - Enforce uniqueness and guarded lifecycle transitions transactionally.

6. Define the typed child control protocol.
   - Health and readiness.
   - Protocol version and capability discovery.
   - Lifecycle status and bounded workspace summary.
   - Read/command API discovery.
   - Explicit unsupported-capability responses.

7. Add delegated sessions and authenticated parent proxying.
   - Exchange parent authority for audience- and scope-bound child authority.
   - Preserve original operator attribution.
   - Apply strict forwarding rules, timeout/body limits, and header filtering.
   - Never expose child control credentials to frontend code.

8. Compile child lifecycle operations into activity plans.
   - Create/provision child runtime and durable stores.
   - Start child API and wait for readiness.
   - Register the child endpoint and attach its node to the parent graph.
   - Pause, stop, wake, archive, deconstruct, and reconstruct.
   - Use saga compensation for partial cross-boundary failure.
   - Keep registry writes inside explicit Postgres units of work.

9. Add recursive read projections.
   - List visible children.
   - Navigate parent and child relationships.
   - Report lifecycle, capability, health, and bounded activity summaries.
   - Represent children as opaque/boxed subgraphs.
   - Expose equivalent semantics through API, CLI, and read-only MCP adapters.

10. Add root bootstrap and recovery recipes.
    - Bootstrap one root instance externally.
    - Recover a root from retained durable stores.
    - Document which resources survive stop, archive, and deconstruction.
    - Do not imply that the root can recursively create itself.

11. Add a live Docker recursive-instance demonstration.
    - Start one root instance and one child instance.
    - Give each instance separate workspace authority and durable state.
    - Register the child through an approved lifecycle activity.
    - Send a frontend-like request to the root and proxy it to the child.
    - Let the child manage a small application deployment graph.
    - Demonstrate that stopping the child does not destroy retained truth.

12. Perform security, data, design, and reliability hardening.
    - Concurrent child creation and idempotency.
    - Cycle and duplicate-parent rejection.
    - Token audience, scope, expiry, replay, and redaction.
    - Child unavailability and stale registry observations.
    - Partial provisioning and failed compensation.
    - Root restart and registry recovery.
    - Bounded proxy traffic and audit events.

13. Document and hand off recursive projections to Roadmap 0010.
    - Provide UI fixtures for root, child, stopped child, and failed creation.
    - Curate code snippets showing the managed-node sum and admission policy.
    - Record which capabilities are stable and which remain experimental.

## Transaction And Saga Boundaries

Store-local transitions follow ADR 0008:

```text
API/application service
  owns Postgres UnitOfWork
    registry repository
    relationship repository
    operation/activity repositories
```

Repositories may add and flush. Only the unit of work commits.

Cross-boundary work follows the saga/activity pipeline:

```text
persist approved child-creation intent
  -> provision runtime/store
  -> start child
  -> wait for health
  -> register relationship
  -> attach desired graph node
  -> observe result
```

No database transaction may remain open while waiting on Docker, cloud APIs, or
child HTTP calls. Durable events surround each external effect.

## Validation

- The same instance application can run as root, leaf, or composite by policy.
- Admission validation rejects forbidden node types and recursive cycles.
- A child has at most one active parent.
- Parent registry records do not duplicate child workspace truth.
- Parent and child both authorize proxied operations.
- Delegated credentials are scoped, expiring, and redacted.
- Child lifecycle requests are idempotent and activity-backed.
- Partial provisioning leaves visible events and compensation state.
- Root recovery reconstructs child registry without reading child databases.
- Existing deployment recipes continue to execute unchanged or through a
  documented functionally identical descriptor migration.
- The Docker recursive-instance demonstration passes.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Definition Of Done

Roadmap 0009 is complete when:

- there is no bespoke Hub server/domain object required to manage children,
- any `ControlPlaneInstance` admitted to manage instance nodes can create,
  observe, proxy to, stop, and recover child instances,
- the root Hub is the same instance application configured with root identity,
  public authentication, and an instance-oriented admission policy,
- child creation and lifecycle changes pass through approved, inspectable
  activity plans,
- each child remains authoritative for its workspace and operational history,
- parent/child security and data ownership laws are enforced by tests,
- all existing live deployment examples still work,
- and Roadmap 0010 can build nested UI/MCP navigation entirely from stable
  descriptors and read/command interfaces.

## Handoff

Roadmap 0010 should treat Hub and Instance as presentation contexts over one
recursive object. The UI may show a Hub registry first and box child instances,
but it must derive controls from capabilities and admission policy rather than
hard-coded server species.
