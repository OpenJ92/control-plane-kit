# Control Plane Kit Language

Status: Living reference
Last updated: 2026-07-24

This document is the dictionary for the current Control Plane Kit language. It
names the values, interpreters, durable facts, and package boundaries that now
exist after the extraction work.

For a paper-map learning plan, see
[Control Plane Kit Language Study Guide](CONTROL_PLANE_LANGUAGE_STUDY_GUIDE.md).

It is not a tutorial and it is not a replacement for the code. The purpose is to
make the algebra claimable: when a new feature says "graph", "product",
"runtime", "approval", "interpreter", or "pull authority", this document should
make clear which object owns that word and which package may interpret it.

## Package Rings

```text
control-plane-kit-core
  pure language, contracts, descriptors, validation, planning values

control-plane-kit-operations
  durable application services, stores, UnitOfWork, sessions, approvals,
  admission, lifecycle, coordinator, observations, read models

control-plane-kit-interpreters
  concrete RuntimeEffectRequest -> IO RuntimeEffectResult implementations
  such as DockerRuntimeInterpreter

control-plane-kit-servers
  package-owned server products, descriptors, Dockerfiles, OCI publication,
  cpk-server FastAPI/MCP process wrapper
```

The north-star dependency direction is:

```text
core <- operations <- cpk-server
core <- interpreters
core <- server product descriptors
operations -> interpreter protocol only
cpk-server -> operations + selected interpreters at process composition
```

`control-plane-kit-core` must remain importable without Docker, FastAPI, HTTPX,
Postgres drivers, server product code, or concrete runtime packages.

## Whole Pipeline

The operator-facing program is a composition of values and durable commands:

```text
DeploymentTopology
  -> compile_topology
    -> DeploymentGraph
      -> validate_graph
        -> ValidatedGraph
          -> diff_graphs(current, desired)
            -> GraphDiff
              -> compile_activity_plan
                -> ActivityPlan
                  -> ApprovalRequest
                    -> AdmittedRun
                      -> ActivityRun
                        -> RuntimeEffectRequest
                          -> RuntimeEffectResult
                            -> Observation
                              -> CurrentGraph advancement
```

The application program shape is:

```text
Plan -> Approve -> Admit -> Claim -> Start -> Execute -> Advance
```

and the four common graph transitions are:

```text
initial deployment = Deploy(EmptyGraph, desired)
update             = Deploy(current, desired)
teardown           = Deploy(current, EmptyGraph)
no-op              = Deploy(graph, graph)
```

`Deploy` and `DeploymentProgram` belong to operations/application composition,
not to core. Core owns the pure transition, command, route, and contract
language that such a program uses.

## Entry Format

Each dictionary entry uses this shape:

```text
Name
  meaning:
  owned by:
  durable:
  may contain secrets:
  interpreted by:
  laws:
```

## Core Topology Language

### DeploymentTopology

meaning:
  A named declarative source tree for a deployment. It is the authored topology
  expression before compilation into a graph.

owned by:
  `control-plane-kit-core`.

durable:
  Pure value. Operations may persist descriptors or graph versions derived from
  it, but the topology object itself is not operational truth.

may contain secrets:
  No.

interpreted by:
  The topology compiler.

laws:
  It describes structure only. It does not perform Docker, HTTP, filesystem,
  database, approval, or runtime effects.

### DeploymentGraph

meaning:
  The compiled graph language: nodes, edges, endpoints, runtime identity, socket
  bindings, descriptors, and graph validation input.

owned by:
  `control-plane-kit-core`.

durable:
  Pure value in core. Operations persists workspace graph truth and current or
  desired graph pointers.

may contain secrets:
  No.

interpreted by:
  Validation, diffing, planning, read projections, and operations graph stores.

laws:
  Duplicate graph identities fail closed. Observed runtime state never rewrites
  graph truth. Graph drift must not retarget already admitted work.

### RuntimeContext

meaning:
  A grouping context saying which runtime kind should interpret child blocks.
  Today the core language includes `DockerRuntime` and `ExternalRuntime`
  contexts.

owned by:
  `control-plane-kit-core`.

durable:
  Pure graph language; operations can persist graph descriptors that include
  runtime identity.

may contain secrets:
  No.

interpreted by:
  Runtime-specific interpreters selected by operations through a dispatcher.

laws:
  A runtime context does not execute itself. It selects the interpreter family
  for child materialization.

### DeployBlock

meaning:
  A block that may become a graph node. The closed shape is:

```text
DeployBlock
  = ApplicationBlock
  | DataBlock
  | ProxyBlock
```

owned by:
  `control-plane-kit-core`.

durable:
  Pure graph language.

may contain secrets:
  No.

interpreted by:
  Topology compiler, graph validation, planning, runtime-effect translation.

laws:
  The block carries identity, runtime implementation material, and socket
  surface. It does not own process state, container state, database state, or
  observed health.

### BlockSpec

meaning:
  Shared identity and display metadata for a block: role id, display name,
  health path, capabilities, verification, and bounded metadata.

owned by:
  `control-plane-kit-core`.

durable:
  Pure graph descriptor material.

may contain secrets:
  No.

interpreted by:
  Graph compiler, validators, UI/read projections, product instantiation.

laws:
  Capability claims must map to executable behavior or explicit unsupported
  outcomes. Unsupported claims fail closed.

## Socket And Protocol Language

### Protocol

meaning:
  A closed pair of transport and application protocol semantics:

```text
Protocol = Transport x ApplicationProtocol
```

owned by:
  `control-plane-kit-core`.

durable:
  Pure descriptor value.

may contain secrets:
  No.

interpreted by:
  Socket compatibility checks, endpoint codecs, runtime publication, probes,
  product descriptors.

laws:
  Compatibility is semantic, not textual. UDP reachability is never inferred
  from a TCP connection. Transport reachability does not imply application
  health.

### RequirementSocket

meaning:
  A named need of a block, such as `DATABASE_URL` or `UPSTREAM_BASE_URL`.

owned by:
  `control-plane-kit-core`.

durable:
  Pure graph/product descriptor material.

may contain secrets:
  No.

interpreted by:
  Graph validation, dependency binding, runtime-effect translation.

laws:
  Environment-bound requirements require explicit environment binding names.
  Runtime-control requirements must not smuggle environment bindings.

### ProviderSocket

meaning:
  A named endpoint provided by a block.

owned by:
  `control-plane-kit-core`.

durable:
  Pure graph/product descriptor material.

may contain secrets:
  No.

interpreted by:
  Graph validation, dependency binding, runtime publication.

laws:
  It advertises protocol semantics only. It does not prove reachability,
  readiness, or health.

### SocketConnection

meaning:
  A graph edge connecting one provider socket to one consumer requirement
  socket.

owned by:
  `control-plane-kit-core`.

durable:
  Pure graph edge descriptor.

may contain secrets:
  No.

interpreted by:
  Validation, diffing, planning, and runtime dependency binding.

laws:
  Consumer and provider protocols must be compatible. Edge structure drives
  runtime parameter binding; free-form metadata must not infer dependencies.

## Product Language

### ProductIdentity

meaning:
  Language-neutral identity for an externally supplied product contract:

```text
ProductIdentity = namespace x name x contract_revision
```

owned by:
  `control-plane-kit-core`.

durable:
  Pure descriptor value.

may contain secrets:
  No.

interpreted by:
  Product descriptors, catalogues, operations product registration, graph
  authoring.

laws:
  Identity is not image identity. One product identity points to one product
  contract revision.

### ProductDescriptorDocument

meaning:
  The canonical `product.cpk.json` document for an externally supplied product.
  It describes sockets, runtime contract, OCI image identity, configuration,
  lifecycle, verification, and product family.

owned by:
  `control-plane-kit-core` owns the descriptor language.
  `control-plane-kit-servers` owns package product descriptor files.

durable:
  Pure descriptor document. Operations may persist admitted descriptor documents
  as registered product truth.

may contain secrets:
  No.

interpreted by:
  Product catalogue, product registration service, graph authoring, runtime
  effect translation.

laws:
  The document is immutable and digest-addressed. Descriptor changes produce a
  new digest. Host paths, raw credentials, tokens, and password values are not
  descriptor language.

### ProductReference

meaning:
  A pure graph/planning reference to a pinned product descriptor:

```text
ProductReference = ProductIdentity x ProductDescriptorDigest
```

owned by:
  `control-plane-kit-core`.

durable:
  Pure descriptor value. Operations persists product references in graph truth
  and registered product records.

may contain secrets:
  No.

interpreted by:
  Operations product registration and runtime-effect translation.

laws:
  A graph that references a product must reference a registered descriptor
  digest. The reference is not enough to pull or run an image by itself.

### RegisteredProduct

meaning:
  Workspace-scoped operational truth that a product descriptor has been admitted
  for use.

owned by:
  `control-plane-kit-operations`.

durable:
  Yes. It lives in the operations store.

may contain secrets:
  No.

interpreted by:
  Graph authoring, planning context loading, runtime-effect translation.

laws:
  Registration records provenance and trust. Core does not own the fact that a
  workspace accepted a descriptor.

### ProductFamily

meaning:
  Closed graph-visible product classification. Current families include
  `server` and `data-service`.

owned by:
  `control-plane-kit-core`.

durable:
  Pure descriptor value.

may contain secrets:
  No.

interpreted by:
  Graph authoring, product catalogue, runtime realization, future UI grouping.

laws:
  Family is a classification, not a behavior escape hatch. A data service can
  still be OCI-backed without pretending to be an application server.

### OciImageReference

meaning:
  Immutable OCI image identity: registry, repository, digest, optional tag,
  platform, and bounded provenance.

owned by:
  `control-plane-kit-core`.

durable:
  Pure descriptor value.

may contain secrets:
  No.

interpreted by:
  Runtime interpreters such as Docker, and future ECS/Kubernetes/Lambda
  interpreters.

laws:
  Image identity is distinct from pull authority. Acceptance must use digest
  identity, not local mutable tags.

## Configuration And Secret Language

### ConfigurationArtifact

meaning:
  Immutable bounded configuration file material:

```text
ConfigurationArtifact
  = artifact_id
  x target_path
  x media_type
  x bounded_content
  x content_digest
  x file_mode
```

owned by:
  `control-plane-kit-core`.

durable:
  Pure graph/product descriptor material.

may contain secrets:
  No.

interpreted by:
  Product renderers and runtime interpreters.

laws:
  Target paths are safe absolute container paths. Host paths are not graph data.
  Digests are derived and verified. Configuration material is distinct from
  retained data and secret material.

### SecretReference / CredentialReference

meaning:
  Opaque references to secret or credential material held outside graph and
  descriptor language.

owned by:
  `control-plane-kit-core` owns the reference value.
  Operations/interpreters own admission and resolution boundaries.

durable:
  The opaque reference may be durable. The secret value must not be durable in
  CPK graph/product/runtime descriptors.

may contain secrets:
  No. The reference is not the secret.

interpreted by:
  Secret delivery and credential resolver adapters.

laws:
  Raw secrets never enter product descriptors, graphs, plans, runtime requests,
  activity events, observations, logs, route responses, or issue evidence.

### ImagePullAuthority

meaning:
  Secret-free authority reference for pulling OCI images:

```text
ImagePullAuthority
  = registry
  x optional repository scope
  x CredentialReference
```

owned by:
  `control-plane-kit-core` owns the pure value.
  `control-plane-kit-operations` owns workspace admission.
  `control-plane-kit-interpreters` owns concrete credential resolution.

durable:
  The admitted authority record is durable in operations. The resolved
  credential value is not.

may contain secrets:
  No.

interpreted by:
  `DockerRuntimeInterpreter` today. Future OCI-capable runtimes can interpret
  the same authority into ECR, Kubernetes imagePullSecrets, Lambda image
  permissions, or another runtime-specific mechanism.

laws:
  Missing or denied authority fails closed before image pull, network,
  configuration, volume, or container mutation.

## Planning Language

### DeploymentTransition

meaning:
  The pure relation between a current graph and a desired graph.

owned by:
  `control-plane-kit-core`.

durable:
  Pure planning language. Operations persists operation/session/plan records
  that name graph versions.

may contain secrets:
  No.

interpreted by:
  Diffing and activity planning.

laws:
  Initial deployment, update, teardown, and no-op are all graph-pair
  transitions.

### GraphDiff

meaning:
  The structural difference between current and desired graph truth.

owned by:
  `control-plane-kit-core`.

durable:
  Pure planning value. Operations may persist plan descriptors derived from it.

may contain secrets:
  No.

interpreted by:
  Activity planning and review surfaces.

laws:
  Diffing compares pinned graph values. It does not inspect live runtime state
  and does not retarget admitted work after graph drift.

### ActivityPlan

meaning:
  The ordered, reviewable plan compiled from a graph diff.

owned by:
  `control-plane-kit-core`.

durable:
  Pure planning value; operations records approved/admitted plan evidence.

may contain secrets:
  No.

interpreted by:
  Approval queues, admission, lifecycle, coordinator, read models.

laws:
  Effects are materialized from the exact desired graph pinned by the approved
  plan.

### Activity

meaning:
  One planned operation such as realizing, mutating, verifying, compensating, or
  tearing down graph material.

owned by:
  `control-plane-kit-core`.

durable:
  Pure planning value. Operations records activity events and run state.

may contain secrets:
  No.

interpreted by:
  Coordinator and runtime-effect translator.

laws:
  Activity identity is durable evidence. Activity execution must preserve
  original failures separately from compensation or recovery evidence.

## Operations Language

### Workspace

meaning:
  Operational boundary for graph truth, product registration, image pull
  authority, sessions, approvals, runs, observations, and read models.

owned by:
  `control-plane-kit-operations`.

durable:
  Yes.

may contain secrets:
  No.

interpreted by:
  Operations services and cpk-server routes.

laws:
  Workspace ownership scopes mutable operational truth. Runtime observations do
  not rewrite workspace desired graph truth.

### OperationSession

meaning:
  Durable record of an operator's attempt to move from one graph state toward
  another.

owned by:
  `control-plane-kit-operations`.

durable:
  Yes.

may contain secrets:
  No.

interpreted by:
  Planning, approval, admission, read projections.

laws:
  Session history is append-only evidence. A later failure does not erase
  earlier operator intent.

### ApprovalRequest

meaning:
  Durable suspension point asking an authorized reviewer to accept or reject a
  compiled plan.

owned by:
  `control-plane-kit-operations` for durable behavior.
  `control-plane-kit-core` for pure command and route contract names.

durable:
  Yes.

may contain secrets:
  No.

interpreted by:
  Approval command service, approval queue read model, cpk-server HTTP/MCP
  routes.

laws:
  Admission rejects missing, rejected, stale, wrong-plan, or insufficient-scope
  approval. Execution must not bypass approval.

### AdmittedRun

meaning:
  Durable admission of an approved plan into execution.

owned by:
  `control-plane-kit-operations`.

durable:
  Yes.

may contain secrets:
  No.

interpreted by:
  Run lifecycle service and coordinator.

laws:
  Every activity run is owned by an admitted run. Admission records execution
  request identity; it does not execute effects.

### ActivityRun

meaning:
  Durable execution instance opened by claim/start lifecycle commands.

owned by:
  `control-plane-kit-operations`.

durable:
  Yes.

may contain secrets:
  No.

interpreted by:
  Lifecycle, coordinator, recovery, advancement, read projections.

laws:
  The durable sequence is:

```text
admit -> execution request id
claim -> opens activity run and returns run id
start -> records RUN_STARTED
execute -> dispatches activities
advance -> uses completed run evidence
```

### Observation

meaning:
  Durable evidence of runtime state, result, health, reachability, or endpoint
  material observed during execution.

owned by:
  `control-plane-kit-operations`.

durable:
  Yes.

may contain secrets:
  No.

interpreted by:
  Read projections, advancement checks, operator review.

laws:
  Observations extend canonical operational history. They never rewrite graph
  truth.

### CurrentGraph

meaning:
  Workspace pointer to the accepted current graph version.

owned by:
  `control-plane-kit-operations`.

durable:
  Yes.

may contain secrets:
  No.

interpreted by:
  Planning and graph advancement service.

laws:
  Current graph advancement is explicit and guarded by accepted run evidence.
  Runtime success alone does not silently mutate the pointer.

## Runtime Effect Language

### RuntimeEffectRequest

meaning:
  Pure request from operations to an external runtime interpreter.

owned by:
  `control-plane-kit-core`.

durable:
  Pure boundary value. Operations may record intent/event evidence that produces
  it.

may contain secrets:
  No.

interpreted by:
  Runtime interpreters.

laws:
  It contains pinned source identities and product material selected from
  durable truth. It does not contain Docker clients, HTTP clients, stores,
  credentials, or process handles.

### RuntimeProductMaterial

meaning:
  Exact product material selected for one runtime effect: node id, runtime id,
  product reference, product descriptor material, socket-derived environment,
  and optional image pull authority.

owned by:
  `control-plane-kit-core`.

durable:
  Pure boundary value selected from durable operations truth.

may contain secrets:
  No.

interpreted by:
  Runtime interpreters.

laws:
  It carries product material, not arbitrary runtime decisions. Docker-specific
  network, mount, and port choices belong to Docker interpretation.

### RuntimeEffectResult

meaning:
  Pure result returned by a runtime interpreter after an external effect.

owned by:
  `control-plane-kit-core`.

durable:
  Operations records result, event, and observation derived from it.

may contain secrets:
  No.

interpreted by:
  Operations coordinator and read projections.

laws:
  Result folding must not erase historical evidence. Failure and uncertainty
  remain visible.

### RuntimeInterpreterDispatcher

meaning:
  Operations-side dependency that selects the configured runtime interpreter for
  a runtime request.

owned by:
  `control-plane-kit-operations` as an application boundary/protocol.

durable:
  No.

may contain secrets:
  No.

interpreted by:
  cpk-server/bootstrap composition supplies concrete interpreters.

laws:
  Operations may depend on the dispatcher protocol, not on Docker SDK or a
  concrete interpreter package.

### DockerRuntimeInterpreter

meaning:
  Concrete interpreter:

```text
RuntimeEffectRequest -> IO RuntimeEffectResult
```

for local Docker.

owned by:
  `control-plane-kit-interpreters`.

durable:
  No.

may contain secrets:
  It may resolve credentials in memory at the Docker boundary. It must not
  persist or return raw credential values.

interpreted by:
  Python Docker SDK and Docker Engine.

laws:
  Resolve pull authority before network/config/volume/container mutation. Never
  hold a Postgres transaction across Docker effects. Inspect and prove
  ownership before mutation or cleanup.

## cpk-server And Server Products

### cpk-server

meaning:
  Package-owned server product and process wrapper around operations. It exposes
  HTTP and MCP process surfaces backed by the same operations application
  services.

owned by:
  `control-plane-kit-servers`.

durable:
  The process is not durable truth. Its operations database is durable truth.

may contain secrets:
  Process configuration may reference secret locations, but product descriptors
  and route responses must remain secret-free.

interpreted by:
  OCI runtimes such as Docker today, and future runtimes later.

laws:
  cpk-server composes dependencies. It does not own graph truth, stores,
  runtime semantics, Docker auth semantics, or child cpk-server history.

### Package-Owned Server Product

meaning:
  A deployable server product shipped by `control-plane-kit-servers`, such as
  cpk-server, hello-server, router, or multiplexer.

owned by:
  `control-plane-kit-servers`.

durable:
  Descriptor files are immutable product inputs. Running instances are runtime
  state.

may contain secrets:
  No descriptor may contain secrets.

interpreted by:
  Core product codecs, operations registration, runtime interpreters.

laws:
  Products are values. Entrypoints are processes. Interpreters perform effects.

### Data-Service Product

meaning:
  A graph-visible data-bearing product, such as a Postgres container descriptor
  or future managed data service descriptor.

owned by:
  Core owns the family and descriptor language.
  Product packages own concrete descriptors.
  Operations owns registration and graph admission.
  Runtime interpreters own realization.

durable:
  Descriptor is pure. Data produced by the running service is retained data and
  belongs to lifecycle/retention policy.

may contain secrets:
  Descriptor no. Runtime credentials must use secret or credential references.

interpreted by:
  Docker today for local data products; future RDS/cloud interpreters later.

laws:
  Data resources, retained data, ephemeral configuration, and secrets remain
  distinct.

## Transactions And External Effects

### Postgres UnitOfWork

meaning:
  Explicit transaction boundary for one operator command.

owned by:
  `control-plane-kit-operations`.

durable:
  It governs durable changes; it is not itself durable business truth.

may contain secrets:
  No.

interpreted by:
  Operations command services and stores.

laws:
  One operator command equals one explicit Postgres transaction. Application
  command services own commit and rollback. Stores share the UnitOfWork
  connection and never commit independently.

### External Effect Law

meaning:
  The invariant separating durable intent from Docker, filesystem, HTTP,
  network, health, or other external effects.

owned by:
  Operations and interpreters together.

durable:
  Intent, result, events, and observations are durable. The external effect is
  not a transaction.

may contain secrets:
  No durable record may contain raw secrets.

interpreted by:
  Coordinator and runtime interpreters.

laws:

```text
short transaction: record durable intent
  -> commit
    -> bounded external effect
      -> short transaction: record result, event, and observation
```

Never hold a Postgres transaction or lock across an external effect.

## HTTP And MCP Contract Language

### OperatorCommandContract

meaning:
  Pure public command vocabulary: command identity, family, stage, service role,
  payload policy, idempotency policy, and approval relation.

owned by:
  `control-plane-kit-core`.

durable:
  Pure contract value.

may contain secrets:
  No.

interpreted by:
  cpk-server HTTP and MCP adapters, operations service adapters, parity tests.

laws:
  HTTP and MCP routes must use the same command vocabulary and the same
  operations services.

### ReadProjectionContract

meaning:
  Pure public read vocabulary for operator-facing projections.

owned by:
  `control-plane-kit-core`.

durable:
  Pure contract value. Operations owns the actual read models.

may contain secrets:
  No.

interpreted by:
  cpk-server HTTP/MCP adapters and operations read services.

laws:
  Read projections expose canonical operational truth. They do not become
  duplicate mutable stores.

## Common Compositions

### Initial Deployment

```text
current = EmptyGraph
desired = graph
Deploy(current, desired)
```

Creates resources required by the desired graph after approval and admitted
execution.

### Update

```text
current = graph_a
desired = graph_b
Deploy(current, desired)
```

Diffs pinned graph values and executes only the approved transition.

### Teardown

```text
current = graph
desired = EmptyGraph
Deploy(current, desired)
```

Removes only resources proven owned and removable. Retained data is not removed
as ordinary ephemeral cleanup.

### Recursive cpk-server

```text
parent cpk-server
  -> registered cpk-server product descriptor
    -> DockerRuntimeInterpreter
      -> child cpk-server container
```

The child cpk-server is opaque to the parent. The parent may spawn it and
observe readiness/liveness, but it must not own the child's workspace graph
truth, operation sessions, approvals, activity history, or current graph.

## Hard Boundaries

- Do not put Docker SDK imports in core or operations.
- Do not put Postgres stores in core.
- Do not put FastAPI/MCP process code in core or operations services.
- Do not put server product implementation code in core.
- Do not put raw secrets in descriptors, graphs, plans, requests, events,
  observations, logs, or route responses.
- Do not use free-form strings where a closed value already exists.
- Do not treat a local image tag as acceptance identity.
- Do not infer unsupported runtime behavior from metadata.

## When Adding A New Term

Add a dictionary entry before or with the implementation when a change adds:

- a new graph-visible value;
- a new durable operation fact;
- a new runtime effect request/result field;
- a new product descriptor field;
- a new interpreter boundary;
- a new public HTTP/MCP command or read projection;
- a new source of authority, ownership, cleanup, retention, or secret handling.

The entry should state ownership, durability, secret policy, interpreter, and
laws. If those cannot be stated clearly, the issue is not ready to implement.
