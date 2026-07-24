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

### Fourth Pass: Examples

Draw these concrete flows:

```text
Deploy EmptyGraph -> hello/router graph

Deploy graph A -> graph B

Parent cpk-server -> child cpk-server

Private OCI image pull
```

For each example, mark:

```text
where the graph truth is;
where approval happens;
where the transaction stops;
where the external effect happens;
where observation is recorded;
when CurrentGraph advances.
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
