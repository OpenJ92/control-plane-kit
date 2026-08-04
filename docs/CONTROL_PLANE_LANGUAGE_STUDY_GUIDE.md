# Control Plane Kit Language Study Guide

Status: Living guide
Last updated: 2026-08-03

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

SECRET CUSTODY
  RegisteredSecretProvider
  SecretReference
  SecretUseIntent
  control-plane-kit-secrets

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

For versioned credentials, distinguish custody-wide revocation from exact
retirement. `SecretVersionRevocationGrant` names one reference and one provider
version; `SecretVersionRevocationReceipt` proves only that version was revoked.
This allows key A to retire while key B under the same reference remains usable.

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
            -> commits provider registration + action digest
              -> enters generation-prepared
            -> secrets provider generates and retains private key B
              -> operations atomically admits B reference + public identity
                -> one authored graph binding
                  -> realized verifier A
                    -> publish desired realized projection A+B
                      -> realized verifier B
```

The first `realized verifier A` is not harness configuration. When the operator
publishes an authored graph containing a stable delegation binding, operations
locks that admitted key scope and compiles exactly one settled active public
key into desired projection A in the same transaction. Ordinary authoring
rejects A+B; the approved rotation program alone owns overlap and retirement.

Then draw the durable operations program beside the realized projections:

```text
GatewayKeyRotation(status, version)
  x GatewayKeyRotationTransition(transition_id, from, to)
  x ApprovalSubject(kind, review_digest)
  x GenerationCheckpoint(provider_registration_id, action_digest)
  x overlap DeploymentCheckpoint
  x drain_deadline
  x retirement DeploymentCheckpoint
```

Mark each state change as one Postgres compare-and-set plus one transition-ledger
insert in the same transaction. Draw provider, Docker, gateway, and health IO
outside that transaction. Replace any imagined `sleep()` with a durable deadline
and an injected trusted clock. An uncertain child effect points to `blocked`,
not back to the effect and not forward to success.

For A -> A+B, draw two graph identities, not two authored graphs:

```text
authored graph:     G ---------------------------- G
current projection: G[A] ------------------------- G[A]
desired projection: G[A] -- publish + revision --> G[A+B]
```

The publication transaction stores immutable G[A+B], compare-and-sets the
desired projection pointer, increments the desired revision, and appends one
operation action. Planning and execution happen afterward. The operator does
not provide public-key PEM, verifier environment, audience, or projection ids;
operations derives them from the approved rotation and durable key lifecycle.

Then draw the approval bridge as an intersection, not inheritance:

```text
approved rotation subject
  x key-generated rotation
  x exact overlap publication evidence
  x canonical plan(G[A], G[A+B])
    -> one ordinary execution request
```

The rotation approval does not become a general plan approval. It authorizes
only the exact phase child whose workspace, projections, revision, public key
set, compiled activities, and publication provenance all agree. The resulting
execution request points to the child plan while retaining the original
rotation approval request and decision ids.

Then draw preparation as committed boxes, with the effect boundary after the
last box:

```text
[session] -> [G[A+B] desired] -> [plan] -> [admit]
  -> [claim] -> [start] -> [rotation checkpoint] || runtime effect
```

Each box is its own operations transaction. A restart repeats the same command
identity and recovers the original row. The checkpoint's session, plan,
approval, request, run, graph, projection, revision, and preparation time come
from those results rather than operator input. The `||` matters: preparation
creates a resumable handoff but does not call Docker, a gateway, or the
execution coordinator.

Then continue the drawing one bounded invocation at a time:

```text
[prepared checkpoint]
  -> [journal classification]
    -> [at most one effect]
      -> [run complete]
        -> [current G[A+B]]
          -> [OVERLAP_READY]
```

Put a process-loss mark after `STEP_STARTED`, `STEP_SUCCEEDED`, run completion,
current advancement, and the rotation fold. After `STEP_STARTED` alone, draw a
hard arrow to `BLOCKED(uncertain)`, never back to the adapter. At every later
mark, draw recovery from committed evidence with no duplicate effect. This is
the distinction between durable history and safe effect replay.

Draw verifier projections as maps keyed by public key id. Their serialized
order is canonical but carries no old/new meaning. Label A and B from rotation
identity and lifecycle truth, then require exact material and status for each
id before drawing A+B -> B.

Continue from accepted overlap with three separate committed boxes:

```text
[activate B; A becomes verify-only]
  -> [NEW_KEY_ACTIVE; deadline = trusted now + lifetime + skew]
    -> [DRAINING_OLD_GRANTS]
      -> WAITING(now, deadline)
      -> READY_FOR_RETIREMENT(now, deadline)
```

Draw a process-loss mark after every box. Recovery reads key and rotation truth:
if B is already active while the rotation still says `OVERLAP_READY`, it folds
the committed activation rather than activating again. Circle the deadline and
write "not caller input". `WAITING` performs no write and contains no sleep.
`READY_FOR_RETIREMENT` is an application-program outcome, not another durable
aggregate state; the next phase will prepare the exact B-only projection.

Finish the drawing through B-only acceptance and exact A-version revocation:

```text
[READY_FOR_RETIREMENT]
  -> [publish desired G[B]] -> [plan] -> [admit] -> [claim/start]
    -> [retirement checkpoint] || runtime effect
      -> [current G[B]] -> [RETIREMENT_READY]
        -> [retire public key A] -> [OLD_KEY_RETIRED]
          -> [exact A-version revocation checkpoint]
            || secrets-provider effect
              -> [matching receipt] -> [COMPLETED]
```

The second `||` is as important as the first. Operations commits only an exact
provider registration, secret reference, version, correlation, and action
digest before provider IO. A definite pre-mutation failure returns that same
action as retryable. An uncertain result points to `BLOCKED`, never to a guessed
retry or success. Completion revokes only A's exact private version; B remains
the active signer. The public rotation view and diagnostic history contain
neither provider-version evidence nor generated verifier material.

Write the bounded caller outcomes beside the diagram:

```text
deployment: dispatched | progressed | accepted | accepted-replay
            | already-advanced | blocked
drain:      waiting | ready-for-retirement
completion: completed | completed-replay | retryable | blocked
```

These are observations of one invocation. They are not a harness-owned policy
loop. A future public caller asks the operations service to progress and reads
durable state; cpk-server does not invent phase transitions.

Keep the three authorities visually separate: requesting rotation, reviewing
the rotation subject, and executing the accepted deployment are not equivalent
permissions. The reviewer sees bounded rotation intent, never a secret
reference, provider version, private key, or generated verifier material.
Inside the execution box, draw an additional intersection with the current
`runtime-authority:use` grant. The focused rotation permission supplies program
intent, while runtime use supplies access to the selected runtime. Neither
implies the other, and the program never draws that runtime-use arrow itself.
When a child runtime effect needs a secret delivery, draw a second narrow
intersection with the current `secret-provider:use` grant. The internal worker
always receives `execution:operate`, receives secret use only from trusted
operator authority, and still passes the exact provider/reference/intent check
before any plaintext is resolved at interpreter IO.

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
