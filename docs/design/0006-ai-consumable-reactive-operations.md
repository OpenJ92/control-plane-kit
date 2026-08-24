# Design 0006: AI-Consumable Reactive Operations

Status: Draft direction
Last updated: 2026-08-24

## Purpose

`control-plane-kit` should be designed for an AI agent to consume its language
as a first-class operator.

That means more than exposing Python functions or placing JSON behind an MCP
server. The language must be sufficiently explicit, stable, bounded, and
composable that an authorized AI can:

- read desired and observed server topologies;
- explain their structure and current operational state;
- construct a proposed topology or topology delta;
- adjust a proposal in response to validation or operator feedback;
- react to health, capacity, interruption, and dependency observations;
- produce an inspectable execution and compensation plan;
- request the powers and approvals required by that plan;
- and verify whether an interpreted plan produced the intended result.

The AI is a consumer and author of language values. It is not the source of
runtime truth, the holder of ambient provider credentials, or an unrestricted
runtime shell.

## Intended Control Loop

The intended reactive loop is:

```text
runtime observation
  -> durable operational event
    -> bounded notification
      -> AI reads authoritative state through MCP
        -> AI constructs a plan in the CPK language
          -> validation and policy
            -> operator or policy approval
              -> Gateway interpretation
                -> verification and durable history
```

A callback, webhook, queue message, or connected-session notification is only
a wake-up hint. It identifies durable evidence; it does not carry the complete
truth required to plan recovery or scaling.

The AI must re-read current graph versions, observed state, activity history,
capabilities, leases, and approvals before proposing or executing a change.

## Runtime And Gateway Shape

Each runtime environment may have a Gateway that provides the local operational
boundary:

```text
RuntimeEnvironment
  -> Gateway
       local operational store
       runtime observer
       provider interpreters
       capability advertisement
       event and activity journal
       MCP-facing query and command projections
```

The Gateway may host a local database, such as SQLite, so it can preserve
observations and activity while disconnected. Operations still defines the
durable meaning of sessions, events, approvals, attempts, and observed state.
Gateway hosts or interprets that model for one runtime environment; it must not
invent a competing operational language.

When connectivity returns, synchronization should use stable event identities,
cursors, content hashes, and idempotency keys. The design must not assume that
webhooks are delivered exactly once or in order.

## Observation And Notification Boundary

CPK-owned servers, ingress blocks, routers, load balancers, and Gateways should
be able to produce bounded operational observations without requiring each
application author to invent webhook contracts.

Candidate observation families include:

```text
HealthObservation
ReadinessObservation
RequestRateObservation
ConcurrencyObservation
LatencyWindowObservation
ErrorRateObservation
QueueDepthObservation
CapacityObservation
DependencyObservation
RuntimeInterruptionObservation
TargetHealthObservation
```

Provider adapters translate AWS, Cloudflare, Docker, Kubernetes, or server-local
telemetry into this provider-neutral language. A load balancer does not need to
understand CPK objects directly.

Webhooks are one projection of observations. Polling, provider event streams,
and local probes are other projections. They must converge on the same durable
observation model.

Observation collection should be off the application request path whenever
possible. Request bodies, credentials, client identities, and unbounded provider
payloads do not belong in operational events. Request volume can usually be
represented as aggregate counts and bounded time windows.

## Language Objects

The reactive language should grow from concrete topology consumers and is
expected to include objects in these families:

```text
DeploymentGraph
ObservedTopology
OperationalObservation
OperationalCondition
OperationalIntent
TopologyDelta
ActivityPlan
ApprovalRequirement
CapabilityRequirement
ExecutionLease
CompensationPlan
VerificationPlan
ActivityEvent
ObservedResult
```

The exact names may change. The important ownership relationships are:

```text
Core
  owns topology, intent, delta, plan, compensation, and verification values.

Operations
  owns durable sessions, actions, approvals, runs, events, and observations.

Gateway and provider interpreters
  observe runtime truth and perform approved effects.

MCP, HTTP, CLI, and UI adapters
  project the same services without inventing semantics.
```

## Transformations

The useful structure is visible in transformations rather than broad imperative
commands:

```text
DeploymentGraph x ObservedTopology
  -> GraphDiff

GraphDiff x OperationalObservation x Policy
  -> OperationalIntent

DeploymentGraph x OperationalIntent x CapabilitySet
  -> ActivityPlan

ActivityPlan x Approval x ExecutionLease
  -> InterpretableProgram

InterpretableProgram
  -> ActivityEvent* x ObservedResult

DeploymentGraph x ObservedResult
  -> VerificationResult
```

An AI should construct and revise these values. It should not receive a generic
`recover()`, `scale()`, or `fix_everything()` capability that hides planning,
authority, effects, and verification inside one call.

## AI-Consumable Language Requirements

A language is AI-consumable when an agent can reliably inspect, author,
validate, revise, and explain it.

Required properties include:

### Stable Identity

Graphs, nodes, sockets, observations, sessions, plans, approvals, runs, and
events have stable opaque identities. References do not depend on display names
or list positions.

### Closed And Versioned Schemas

Public values use closed, versioned descriptor schemas. Unknown variants fail
clearly. Schema evolution has an explicit compatibility story.

### Canonical Serialization

Semantically identical values have a canonical representation and content
digest where association matters. Descriptors round-trip without losing
identity or meaning.

### Explicit Composition

Topology connections, runtime membership, dependencies, capability
requirements, ordering, and compensation are represented as data rather than
inferred from prose or process logs.

### Bounded Observations And Errors

Every event and error has a closed classification, bounded fields, stable
correlation identity, and redacted detail. Raw logs and provider responses may
support diagnosis but are not the planning language.

### Preconditions And Concurrency

Plans name the graph version, observation window, leases, capabilities, and
other facts they depend on. An interpreter rejects stale plans rather than
silently applying them to a changed runtime.

### Powers As Values

Plans declare the capabilities they require. Reading observations, provisioning
a runtime, creating public DNS, binding a secret reference, changing load
balancer membership, and destroying a resource are separate powers.

### Explainable Diffs

An AI and an operator can inspect the proposed topology delta, execution order,
risks, required approvals, compensation, and verification before effects occur.

### Machine And Human Legibility

The same plan should support strict machine validation and a useful operator
explanation. Human approval must not require reading provider-specific command
logs or trusting an AI summary that is not associated with the plan value.

## Example: Cross-Environment Capacity Expansion

Suppose one public ingress and load balancer in AWS routes to two opaque public
ingress endpoints:

```text
AWS ingress and load balancer
  -> local Docker battery-factory server
  -> AWS battery-factory server
```

A sustained capacity observation may lead an AI to propose:

```text
1. Provision a third AWS runtime.
2. Register its Gateway and advertised capabilities.
3. Realize the battery-factory server topology.
4. Bind its database requirement through an opaque secret reference.
5. Create a Cloudflare ingress with a unique domain.
6. Verify server and ingress readiness.
7. Add the new endpoint to the load balancer at weight zero.
8. Verify target health.
9. Increase weight through declared steps.
10. Verify capacity, latency, and error-rate bounds.
11. Commit the desired graph version.
```

The associated compensation program may remove load-balancer membership,
remove the ingress, and terminate the new runtime in reverse dependency order.
It must not destroy a pre-existing database or foreign runtime.

Scale-in has stronger laws than scale-out. It should drain traffic, verify no
new admissions, account for in-flight work and durable state, remove membership,
and only then destroy capacity when authorized.

## MCP And Agent Interaction

MCP is the intended AI-facing query and command projection, but durable
notification delivery may use a separate transport.

Illustrative MCP capabilities are:

```text
list_operational_events
read_operation_session
read_activity_timeline
read_desired_topology
read_observed_topology
read_capabilities
validate_plan
submit_plan
request_plan_approval
execute_approved_plan
read_execution_status
```

Read, propose, approve, execute, and destroy are distinct authority classes.
An agent that can inspect and propose does not thereby gain authority to
execute. The actor proposing a plan need not be allowed to approve it.

The notification path should contain only a stable event or operation identity
and a bounded classification. The agent uses that identity to retrieve current
authoritative state through MCP.

## Approval And Operator Collaboration

The language is shared by the AI and the operator.

The expected interaction is:

```text
AI
  Here is the observation and current topology.
  Here is the proposed topology delta.
  Here are the required capabilities and risks.
  Here is the execution, compensation, and verification program.
  May this plan execute?

Operator
  approve
  reject
  or amend constraints and request a revised plan
```

Policies may pre-authorize narrow, idempotent actions within explicit bounds.
Creating public exposure, attaching production data, reducing capacity,
changing destructive resources, or exceeding cost limits should normally
require stronger approval.

Approval associates with an exact plan digest and graph version. Editing a plan
invalidates its prior approval.

## Distributed Execution And Recovery

Cross-Gateway execution is a saga, not an assumed distributed transaction.

Required execution properties are:

- one durable operation and plan identity;
- an execution lease preventing competing agents from applying the same plan;
- idempotency keys for every effect;
- bounded structured events for each attempt;
- exact ownership and cleanup coordinates;
- compensation values established before effects;
- verification after every traffic or durability boundary;
- and explicit terminal classifications for passed, failed, interrupted,
  compensated, and operator-required states.

A callback cannot authorize a retry. Recovery begins from durable activity and
observed state. The AI may propose `resume`, `reconcile`, `restart`, `recreate`,
`rollback`, or `abandon` only when those actions exist in the language and the
target advertises the required capabilities.

## Security And Privacy

The reactive loop crosses important trust boundaries.

It must preserve these laws:

- Gateway reachability is not authority.
- Public ingress is not a management channel.
- Private Docker or cloud networking is not authorization.
- Webhooks and event streams are authenticated, replay-aware, and deduplicated.
- Notification payloads contain no secrets or ambient provider authority.
- Plans use opaque secret references and capability grants, not secret values.
- AI-visible history is bounded and redacted.
- Provider credentials remain inside the authorized interpreter boundary.
- Public exposure and destructive effects are explicit plan elements.
- Every mutation is attributable to an actor, approval, plan, and run.

An AI must not receive a raw shell, Docker socket, cloud credential, or arbitrary
HTTP forwarding capability merely because it can speak MCP.

## Laws

The architecture should eventually make these executable:

```text
Notification is not operational truth.
Observation does not imply authority.
Proposal does not imply approval.
Approval binds one exact plan and graph version.
Execution requires an unexpired lease and declared capabilities.
No traffic reaches a target before readiness and admission verification.
Scale-in drains before removal.
Interrupted execution cannot be reclassified as passed.
Foreign resources are never inferred as owned from daemon-wide differences.
Secret values never enter descriptors, plans, events, or MCP responses.
Every terminal run has verification or an explicit unresolved classification.
```

## Relationship To Current Hardening

Current candidate lifecycle hardening supplies one small precursor:

```text
declared ownership
  -> observed resource identities
    -> interruption
      -> exact containment
        -> durable terminal classification
```

That is not yet Gateway activity history or agent notification. It demonstrates
that interruption can become trustworthy language rather than an ambiguous log.

The next candidate blue/green topology should add another concrete consumer:
traffic admission, old/new target observation, gradual cutover, verification,
rollback, and cleanup. Shared lifecycle, event, or runner abstractions should be
extracted only after those concrete consumers reveal the common structure.

## Roadmap Direction

This design should inform, but not bypass, the existing roadmap topology.

Near-term order:

```text
1. Complete candidate/live hardening with total topologies.
2. Add blue/green traffic admission and rollback as a concrete topology.
3. Project lifecycle and traffic observations into durable activity history.
4. Define provider-neutral reactive observation descriptors.
5. Stabilize AI-consumable graph, plan, event, and approval schemas.
6. Expose bounded MCP read and proposal capabilities.
7. Add approval-bound execution through Gateway interpreters.
8. Demonstrate one complete observation -> proposal -> approval -> execution
   -> verification control loop.
```

Every implementation slice must end with focused laws, package validation, and
a complete user-visible smoke or demonstration. Interface-only green evidence
does not substitute for using the product.

## Non-Goals

This design does not currently authorize:

- autonomous production mutation;
- an unrestricted recovery or autoscaling tool;
- direct AI possession of runtime or provider credentials;
- request-body interception for operational telemetry;
- a new generic lifecycle framework before concrete consumers exist;
- replacement of deterministic autoscaling policies with an AI;
- or representing every provider detail in Core.

Common bounded scaling may remain deterministic. AI consumption is especially
valuable for explaining and composing cross-runtime changes, handling novel or
partial failures, and collaborating with an operator on inspectable plans.

## Open Questions

- Which observation families belong in Core and which belong in domains?
- Is the first Gateway operational store SQLite, and how is it synchronized?
- Which notification transport wakes a disconnected agent host?
- Which MCP resources and tools provide enough language without leaking power?
- How are plan amendments represented while preserving approval association?
- Which actions may policy pre-authorize without a human?
- How are costs and capacity constraints represented provider-neutrally?
- What is the smallest blue/green topology that exercises traffic admission,
  rollback, and agent-readable activity honestly?
