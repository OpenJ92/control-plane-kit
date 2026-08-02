# Control Plane Kit Language Study Guide

Status: Living guide
Last updated: 2026-07-24

Use this guide with
[Control Plane Kit Language](CONTROL_PLANE_LANGUAGE.md). The language document
is the dictionary. This document is the way to absorb it on paper.

The goal is not to memorize every class. The goal is to understand the map well
enough that new features have an obvious place to land.

## The Paper Map

Draw the system in bands from left to right:

```text
AUTHORING
  DeploymentTopology
  ProductDescriptor
  Socket contracts

PURE CORE
  DeploymentGraph
  ValidatedGraph
  GraphDiff
  ActivityPlan
  RuntimeEffectRequest

DURABLE OPERATIONS
  Workspace
  RegisteredProduct
  OperationSession
  ApprovalRequest
  AdmittedRun
  ActivityRun
  Observation
  CurrentGraph

INTERPRETERS
  RuntimeInterpreterDispatcher
  DockerRuntimeInterpreter
  RuntimeEffectResult

SERVER SURFACES
  cpk-server HTTP
  cpk-server MCP
```

Then draw the main river through those bands:

```text
Topology
  -> Graph
  -> Diff
  -> Plan
  -> Approval
  -> Admission
  -> Claim/Start
  -> RuntimeEffectRequest
  -> RuntimeEffectResult
  -> Observation
  -> Advance CurrentGraph
```

## Color Code

Mark every noun with one of three colors:

```text
Blue  = pure value
Green = durable Postgres truth
Red   = external effect / IO
```

Examples:

```text
ProductDescriptor       blue
RegisteredProduct       green
OciImageReference       blue
ImagePullAuthority      blue/green boundary
Resolved Docker auth    red, never durable
RuntimeEffectRequest    blue
ActivityRun             green
Docker container        red
Observation             green
```

If a term is hard to color, that is usually a sign it is a boundary term. Write
both sides next to it instead of forcing it into one box.

## Three Laws To Put At The Top

```text
1. Values first, interpreters second.

2. One operator command = one explicit Postgres transaction.

3. Durable intent -> commit -> external effect -> durable result.
```

Most design questions reduce to one of these laws.

## Do Not Confuse

Keep this section visible on the page:

```text
ProductDescriptor != RegisteredProduct

OciImageReference != ImagePullAuthority != resolved credential

RuntimeAuthorityReference != RuntimeAuthorityAccessDelivery != delivered socket/TLS/session

DesiredGraph != CurrentGraph != Observation

ActivityPlan != AdmittedRun != ActivityRun

cpk-server process != operations truth

RuntimeContext != RuntimeInterpreter
```

These pairs look similar because they sit on opposite sides of important
boundaries.

## Study Order

### First Pass: Pipeline

Trace only the main transformation:

```text
DeploymentTopology
  -> DeploymentGraph
    -> GraphDiff
      -> ActivityPlan
        -> ApprovalRequest
          -> AdmittedRun
            -> ActivityRun
              -> RuntimeEffectRequest
                -> RuntimeEffectResult
                  -> Observation
                    -> CurrentGraph advancement
```

Do not stop on every term. Get the river into your head first.

### Second Pass: Ownership

For each noun, ask:

```text
Who owns this?

core?
operations?
interpreters?
cpk-server?
server-products?
```

Write the owner beside the noun. This prevents the most common architectural
mistake: putting behavior in the package where the noun merely appears.

### Third Pass: Secrets And Effects

For each noun, ask:

```text
Can this contain secrets?
Can this perform IO?
Can this be persisted?
```

The usual answers should feel sharp:

```text
core value:
  no secrets, no IO

operations durable fact:
  no raw secrets, Postgres truth

interpreter:
  IO allowed, resolved secrets allowed in memory only

cpk-server:
  process wrapper, routes into operations
```

The durable secrets direction adds one more ring:

```text
control-plane-kit-secrets:
  encrypted custody, scoped resolution, audit
```

Study it as a custody service, not a new graph language. Core still carries
`SecretReference`; operations admits and authorizes use; interpreters resolve at
the IO edge.

### Fourth Pass: Examples

Draw these concrete flows:

```text
Deploy EmptyGraph -> hello/router graph

Deploy graph A -> graph B

Parent cpk-server -> child cpk-server

Private OCI image pull

Gateway delegation-key rotation:

  operator has delegation-key:rotate
    -> operations derives GatewayKeyRotationApprovalSubject
      -> reviewer has delegation-key:rotate-approve
        -> immutable review digest is approved
          -> operations prepares a reference-only provider grant
            -> secrets provider generates and retains private key B
              -> operations atomically admits B reference + public identity
                -> one authored graph binding
                  -> realized verifier A
                    -> realized verifier A+B
                      -> realized verifier B
```

Then draw the durable operations program beside the realized projections:

```text
GatewayKeyRotation(status, version)
  x GatewayKeyRotationTransition(transition_id, from, to)
  x ApprovalSubject(kind, review_digest)
  x overlap DeploymentCheckpoint
  x drain_deadline
  x retirement DeploymentCheckpoint
```

Mark each state change as one Postgres compare-and-set plus one transition-ledger
insert in the same transaction. Draw provider, Docker, gateway, and health IO
outside that transaction. Replace any imagined `sleep()` with a durable deadline
and an injected trusted clock. An uncertain child effect points to `blocked`,
not back to the effect and not forward to success.

Keep the three authorities visually separate: requesting rotation, reviewing
the rotation subject, and executing the accepted deployment are not equivalent
permissions. The reviewer sees bounded rotation intent, never a secret
reference, provider version, private key, or generated verifier material.

For each example, mark:

```text
where the graph truth is;
where approval happens;
where the transaction stops;
where the external effect happens;
where observation is recorded;
when CurrentGraph advances.

For the rotation example, use different colors for authored intent and realized
projection. The authored graph must not change merely because public verifier
keys rotate. Each realized projection must still have an immutable identity and
accepted execution evidence. Draw the advancement guard as one atomic check over
current authored/projection identity, desired authored/projection identity, and
the monotonic desired revision.
```

## Five-Band Summary

If the full map gets too dense, compress it to this:

```text
PURE LANGUAGE
  topology, products, sockets, protocols, plans, runtime requests

DURABLE TRUTH
  workspaces, registrations, sessions, approvals, runs, observations

EFFECT BOUNDARY
  dispatcher, interpreter request/result, transaction break

RUNTIME WORLD
  Docker containers, networks, volumes, ports, health checks

SERVER/API WORLD
  cpk-server HTTP/MCP routes over operations services
```

## One-Sentence Model

Write this at the bottom of the page:

```text
CPK does not deploy code directly; it transforms approved graph truth into
runtime effect requests, interprets them, records evidence, and only then
advances current graph truth.
```

That sentence is the whole control plane in miniature.
