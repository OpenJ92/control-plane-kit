# Control Plane Kit Language

Status: Living reference
Last updated: 2026-08-03

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

control-plane-kit-secrets
  encrypted durable secret custody, scoped resolution, versions, rotation,
  revocation, and provider-local audit

control-plane-kit-servers
  package-owned server products, descriptors, Dockerfiles, OCI publication,
  cpk-server FastAPI/MCP process wrapper
```

The north-star dependency direction is:

```text
core <- operations <- cpk-server
core <- interpreters
core <- server product descriptors
operations -> interpreter protocols and injected effect capabilities only
interpreters -> configured secret-provider clients at IO boundaries
cpk-server -> operations + selected interpreter composition
```

`control-plane-kit-core` must remain importable without Docker, FastAPI, HTTPX,
Postgres drivers, secret-provider clients, server product code, or concrete
runtime packages. Operations must not import concrete provider SDKs.

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
  A requirement may declare reference-only secret deliveries needed when that
  socket is connected. Those declarations are configured at product
  instantiation but remain inactive until the explicit `SocketConnection`
  exists. An unconnected optional requirement grants no secret use.

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

### VerificationPolicy

meaning:
  Finite execution, retry-cadence, and evidence bounds for one semantic product
  verification check:

```text
VerificationPolicy
  = timeout_seconds
  x interval_seconds
  x maximum_attempts
  x maximum_evidence_bytes
```

owned by:
  `control-plane-kit-core`.

durable:
  Pure product descriptor language.

may contain secrets:
  No.

interpreted by:
  Concrete verification interpreters and bounded live acceptance drivers.

laws:
  `timeout_seconds` bounds one attempt. `interval_seconds` is the delay between
  completed failed attempts; it does not delay the first attempt or follow the
  final attempt. `maximum_attempts` is exact and finite. A container or process
  lifecycle state is not semantic readiness.

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

### SecretDelivery

meaning:
  Closed instructions for making secret material or a secret identity available
  to one runtime process:

```text
SecretEnvironmentDelivery
  = environment_name
  x SecretReference
  x SecretUseIntent

SecretFileDelivery
  = target_path
  x SecretReference
  x SecretUseIntent
  x file_mode
  x optional path_binding

SecretReferenceEnvironmentDelivery
  = environment_name
  x SecretReference
```

owned by:
  `control-plane-kit-core`.

durable:
  Delivery instructions and opaque references may be durable graph/product
  language. Resolved values are runtime-only.

may contain secrets:
  No.

interpreted by:
  Concrete runtime interpreters after operations has authorized the exact
  reference and use intent.

laws:
  Every value-resolving delivery states a closed `SecretUseIntent`. Intent is
  never inferred from an environment name, file path, product identity, or
  interpreter kind. A reference-environment delivery exposes only the opaque
  identity and therefore carries no resolving intent and invokes no resolver.
  A delivery bound to a requirement socket is configured reference material,
  not an active runtime delivery. Topology compilation activates it only from
  the exact provider-to-requirement edge. Removing that edge removes the active
  delivery from the newly compiled desired graph.

### RegisteredSecretProvider

meaning:
  Operations-owned admission that says a workspace may use a named secret
  provider for specific reference prefixes and intents. It is not the secret
  value and it is not the provider implementation.

owned by:
  `control-plane-kit-operations`.

durable:
  Yes. The admission record, provider id, endpoint identity, allowed prefixes,
  use intents, revocation state, and bounded metadata are durable operational
  truth.

may contain secrets:
  No.

interpreted by:
  Operations authorization, cpk-server provider-client composition, and
  interpreter-side secret resolvers.

laws:
  Provider authentication is distinct from secret-use authorization. Registering
  a provider does not grant every runtime or ingress authority permission to use
  every secret. Raw plaintext and ciphertext do not belong in operations stores.

### control-plane-kit-secrets

meaning:
  Sibling distribution/product that owns encrypted durable secret
  custody, authenticated resolve/write/revoke APIs, provider-local audit, and
  secret version metadata.

owned by:
  `OpenJ92/control-plane-kit-secrets`.

durable:
  Yes. It owns encrypted secret material and provider-local audit. It does not
  own deployment graphs, runtime effects, operations approvals, or cpk-server
  routes.

may contain secrets:
  Internally yes, as encrypted persistence and process-memory plaintext at the
  moment of authorized resolution. Public descriptors, logs, errors, and read
  models must not expose raw material.

interpreted by:
  Provider clients/resolvers composed by cpk-server and concrete interpreters.

laws:
  The provider is not a public raw-secret read API. It resolves only through
  authenticated provider access plus authorized `SecretUseIntent` context. The
  first implementation uses a master key supplied outside the provider database,
  preferably by mounted file, to avoid circular bootstrapping.

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

### RuntimeAuthorityAccessDelivery

meaning:
  Secret-free statement that a specific process should receive access material
  for an admitted runtime authority:

```text
RuntimeAuthorityAccessDelivery
  = RuntimeAuthorityReference
  x RuntimeAuthorityAccessDeliveryKind
  x labeled SecretReference*
```

owned by:
  `control-plane-kit-core` owns the pure descriptor language.
  Operations owns workspace admission and readback.
  Interpreters will own concrete materialization.

durable:
  The delivery contract may be durable as operational truth. The delivered
  socket, TLS key, token, cloud session, kubeconfig, or other capability material
  must not be durable in descriptors or read models.

may contain secrets:
  No. It may contain `SecretReference` identities when the delivery kind needs
  referenced secret material, such as remote Docker TLS files.

interpreted by:
  Runtime interpreters and process/bootstrap composition.

laws:
  Runtime authority admission does not imply access delivery. Interpreter
  availability does not imply access delivery. Local Docker socket delivery is a
  closed capability intent, not a stored `/var/run/docker.sock` host path.

### NamedPublicIngress

meaning:
  Provider-neutral request to expose one declared provider socket at one stable
  public hostname:

```text
NamedPublicIngress
  = ingress_id
  x IngressAuthorityReference
  x PublicIngressTarget(node_id, provider_socket)
  x hostname
  x PublicIngressExposure
  x PublicIngressLifecycle
```

owned by:
  `control-plane-kit-core` owns the pure provider-neutral value, strict codec,
  and bounded observation language. Operations owns authority admission,
  activity ordering, and durable evidence. Interpreters own concrete provider
  effects such as Cloudflare tunnel/DNS mutation.

durable:
  The desired hostname and target socket may be graph/control-plane truth.
  Provider-generated tunnel ids, DNS record ids, and readiness checks are
  effect evidence and observations.

may contain secrets:
  No. The authority is referenced by `IngressAuthorityReference`; provider API
  tokens and generated connector tokens stay behind secret references and IO
  boundaries.

interpreted by:
  The first concrete interpreter will be a Cloudflare named-ingress
  interpreter. Future cloud/native ingress interpreters may consume the same
  provider-neutral request language.

laws:
  Named public ingress is socket-adjacent exposure, not a replacement for
  sockets. Core does not define `CloudflareNamedIngress`. Cloudflare appears as
  provider data and interpreter implementation, not as the name of the core
  graph language.

### GatewayProbeRequest / DelegatedGatewayProbeGrant

meaning:
  Provider-neutral language for delegating one exact, read-only private target
  probe from an authorized cpk-server operation to a local runtime-island
  gateway:

```text
GatewayProbeRequest
  = GatewayProbeCommandKind
  x GatewayTargetId
  x bounded HTTP path?

DelegatedGatewayProbeGrant
  = issuer
  x key_id
  x runtime-island audience
  x workspace / operation / request correlation
  x exact gateway node
  x exact probe kind / target
  x canonical request digest
  x issued_at / expires_at
  x jti
```

owned by:
  `control-plane-kit-core` owns the unsigned request, grant, strict codecs,
  health-disclosure policy, and bounded verification result. Operations owns
  authorization and durable intent/result evidence. Outer interpreters own
  signing, dispatch, verification-key materialization, and transport.

durable:
  The unsigned grant is pure command material. Operations may retain bounded
  issuer, key id, correlation, digest, and `jti` evidence. Compact signed
  envelopes and key material are never durable control-plane truth.

may contain secrets:
  No.

interpreted by:
  An injected signer/dispatcher and the cpk-local-gateway verifier. Core does
  not define a token format, signing algorithm, HTTP header, transport, or key
  store.

laws:
  The operator credential is never forwarded. The grant binds the exact
  gateway, runtime island, workspace, kind, target, and complete request
  including HTTP path. Only HTTP status and Postgres select-one are currently
  delegable. Minimal liveness may be public; readiness and target metadata
  require delegated authority. Replay evidence is bounded and process-local,
  so no cross-restart replay guarantee is claimed.

### DelegationAuthorityBinding / DelegationVerifierProjection

meaning:
  A stable authored declaration that one exact graph node receives bounded
  delegated authority, plus the generated public verifier material for one
  exact lifecycle projection:

```text
DelegationAuthorityBinding
  = delegate_node_id
  x DelegationKeyPurpose
  x issuer

DelegationVerifierProjection
  = exact binding identity
  x audience
  x projection_id
  x ordered DelegationPublicKey set
```

owned by:
  `control-plane-kit-core` owns both pure values, canonical graph encoding, and
  deterministic materialization. Operations will own durable projection
  lineage and lifecycle selection. Secret providers retain private keys.

durable:
  The binding is immutable authored graph truth. A verifier projection is an
  immutable realized-graph value linked to the authored binding and exact key
  lifecycle version. A, A+B, and B are distinct realized projections over one
  unchanged authored graph.

may contain secrets:
  No. The projection may contain bounded public PEM material. It never contains
  a private key, private-key `SecretReference`, provider credential, compact
  signed grant, or resolved secret value.

interpreted by:
  A pure materializer attaches the projection to the bound realized node.
  Runtime translation derives the gateway verifier environment at the effect
  boundary. Generated environment is never fed back into authored product
  instance configuration.

laws:
  Binding identities are exact and unambiguous. Projections must cover the
  exact authored bindings and match purpose and issuer. Materialization is
  deterministic and idempotent for the same projection. Missing nodes,
  duplicate key ids, changed issuers, raw authored verifier environment, and
  private material fail closed before runtime effects.

  Publishing a desired realized projection is distinct from authoring a desired
  graph. `desired-realized-projection.publish` keeps the authored graph id and
  graph-version row unchanged, advances only the desired projection pointer and
  monotonic desired revision, and records ordered operation-action evidence in
  the same UnitOfWork. The generic command carries immutable projection identity
  and digest evidence; focused programs must derive its material from durable
  approved truth rather than accept verifier keys or environment from callers.

  Gateway-key overlap publication applies that command as:

  ```text
  key-generated rotation
    x settled G[A]
    x exact ACTIVE key A
    x exact VERIFY_ONLY key B
      -> immutable G[A+B]
        -> desired projection CAS
  ```

  The target binding is selected by workspace, gateway node, purpose, and
  issuer. Its audience is `gateway:{workspace_id}:{gateway_node_id}`. Other
  authored delegation bindings preserve their exact current public projections.
  Missing or extra verification keys, stale pointers or revisions, changed
  projection material under a deterministic identity, and a current target
  other than exact A fail before pointer mutation. No provider or runtime IO is
  performed by projection publication.

  A gateway-key rotation approval may authorize an ordinary child deployment
  plan without creating a synthetic plan approval, but only through the closed
  rotation-child bridge. For the overlap phase, admission jointly verifies the
  original approved `GatewayKeyRotationApprovalSubject`, a `KEY_GENERATED`
  rotation, the exact publication action and rotation version, settled
  `G[A] -> G[A+B]` workspace pointers, and the canonical activity plan compiled
  from that realized projection diff. The execution request retains the
  original rotation approval request and decision ids. A different session is
  permitted only for this explicit subject branch; ordinary activity-plan
  approvals retain exact plan/session/risk matching.

  The bridge does not convert rotation approval into reusable plan authority.
  Another workspace, plan, projection, revision, phase, key set, publication
  provenance, or compiled activity set fails before execution admission. The
  operator still requires `plan:execute` and every runtime/ingress authority-use
  scope implied by the exact child graphs.

  Overlap preparation is a bounded operations program over the existing
  transactional services:

  ```text
  KEY_GENERATED
    -> start child session
    -> publish desired G[A+B]
    -> plan canonical G[A] -> G[A+B]
    -> admit under the exact rotation approval
    -> claim and start the ordinary activity run
    -> persist OVERLAP_DEPLOYING(prepared checkpoint)
    -> stop before runtime dispatch
  ```

  Every step retains its own explicit UnitOfWork. Deterministic idempotency
  identities allow restart before any committed boundary without duplicating
  session, projection, plan, request, or run truth. The checkpoint is built
  only from committed service results and contains the exact approval, plan,
  run, authored graph, realized projection, and desired-revision lineage.
  Callers provide expected starting lineage, never checkpoint identities. A
  prepared replay performs no child command; a later rotation phase is reported
  without mutation. Runtime dispatch, waiting, health checks, and current-graph
  advancement are deliberately outside this preparation step.

  Overlap execution resumes only from that exact prepared checkpoint and
  advances by at most one coordinator effect per invocation:

  ```text
  OVERLAP_DEPLOYING(prepared checkpoint)
    -> classify durable run + activity journal + current projection
      -> no effect evidence: dispatch one ExecutionCoordinator step
      -> terminal success: guarded CurrentGraph advancement to G[A+B]
      -> accepted advancement: OVERLAP_READY(accepted checkpoint)
      -> known failure: BLOCKED(bounded failure code)
      -> started without terminal evidence: BLOCKED(uncertain)
  ```

  The checkpoint, not the caller, supplies workspace, plan, request, run,
  approval, projection, and revision identities. The coordinator records
  `STEP_STARTED` before external IO and terminal evidence afterward. A restart
  that finds an in-flight step never redispatches it; generic reconciliation
  remains owned by the recovery/fencing program. A restart after terminal step
  evidence may complete the run, a restart after current advancement replays
  the exact advancement action, and a restart after the rotation fold returns
  accepted replay. The authored graph remains unchanged throughout.

  Preparation, each coordinator event, run completion, current advancement,
  and rotation folding remain separate short transactions. No transaction
  spans adapter IO. Failure transition time comes from the injected trusted
  application clock, not runtime evidence or a caller-provided timestamp.

  Accepted overlap activates B through a focused operations program:

  ```text
  OVERLAP_READY with accepted current G[A+B]
    -> exact ACTIVE A + VERIFY_ONLY B
      -> activate B atomically, demoting A to VERIFY_ONLY
        -> NEW_KEY_ACTIVE(trusted activation evidence + drain deadline)
          -> DRAINING_OLD_GRANTS
            -> WAITING before deadline
            -> READY_FOR_RETIREMENT at or after deadline
  ```

  Activation and each aggregate fold retain separate explicit transactions.
  If the process is lost after activation but before the aggregate fold, the
  program recognizes exact ACTIVE B plus VERIFY_ONLY A and resumes without
  activating another key. The deadline is computed only by the rotation
  aggregate from the configured maximum grant lifetime and clock skew. The
  caller supplies neither timestamps nor a deadline. Waiting is a typed,
  mutation-free result from an injected trusted epoch clock; it is never a
  sleep or polling loop. `READY_FOR_RETIREMENT` is a program result while the
  aggregate remains `DRAINING_OLD_GRANTS`, leaving retirement projection
  preparation to the next explicit phase.

### DelegationKeyGenerationGrant / DelegationKeyGenerationEvidence

meaning:
  Reference-only authority for one admitted provider to generate an asymmetric
  delegation key, plus bounded public evidence returned after encrypted
  provider custody succeeds:

```text
GenerateDelegationSigningKey
  -> DelegationKeyGenerationGrant
    -> provider IO
      -> DelegationKeyGenerationEvidence
        -> RegisteredSecretReference x RegisteredDelegationSigningKey
```

owned by:
  Core owns the distinct `delegation-key:generate` permission and public key
  language. Operations owns preparation, the provider-neutral protocol, exact
  result validation, and atomic durable folding. `control-plane-kit-secrets`
  generates and retains private bytes. An outer interpreter client performs
  provider IO.

durable:
  Operations stores the admitted `SecretReference`, provider version identity,
  correlation, and public verification identity. For rotation, operations first
  stores the exact active provider-registration identity and a secret-free
  custody-grant fingerprint in `generation-prepared`; provider IO begins only
  after that checkpoint commits.

may contain secrets:
  No. The grant contains only provider, endpoint, credential, and generated
  secret references. Evidence contains bounded public PEM and version metadata.
  Private bytes never cross the provider boundary.

interpreted by:
  A provider implementation composed outside operations. The operations
  package imports neither the concrete provider client nor cryptography.

laws:
  Generation permission is distinct from provider use, key registration, and
  key use. Provider IO occurs between short operations transactions. Workspace,
  reference, purpose, issuer, and correlation must match exactly. The fold
  rechecks active provider identity and atomically admits both the generated
  reference and public key; a second-write conflict rolls both back. Exact
  provider replay is idempotent.

  Rotation generation is a prepare/effect/fold program. Restart reconstructs
  the exact action from the committed transition timestamp and custody
  fingerprint. A definite pre-mutation failure leaves that same action
  retryable. An uncertain post-mutation outcome blocks the rotation. Successful
  replay must match workspace, reference, purpose, issuer, correlation, public
  key identity, and provider version exactly.

### SecretVersionRevocationGrant / SecretVersionRevocationReceipt

meaning:
  Reference-only authority and evidence for revoking one exact durable provider
  version without revoking sibling versions under the same `SecretReference`:

```text
SecretVersionRevocationGrant
  -> provider IO
    -> SecretVersionRevocationReceipt
```

owned by:
  Core owns the provider-neutral grant and receipt. Operations will prepare and
  fold the grant as part of an approved lifecycle program. An interpreter owns
  provider transport. `control-plane-kit-secrets` owns the exact encrypted
  mutation, replay correlation, workspace authorization, and provider-local
  audit.

durable:
  The provider durably binds one workspace and correlation to one secret
  identity, provider version id and version number, and actor. Operations may
  retain the reference and version evidence, never the represented value.

may contain secrets:
  No. Endpoint, credential, and secret identities are opaque references. The
  grant and receipt contain no plaintext, ciphertext, private key, or provider
  credential bytes.

laws:
  Exact replay returns the same revoked version identity. Reusing a correlation
  for a different reference, version, number, or actor fails closed. Reusing an
  already-revoked target under a different correlation also fails closed.
  Revocation state, replay binding, and audit append share one provider
  transaction. Other active versions remain active and resolvable. Existing
  reference-wide revocation remains a distinct operation and cannot substitute
  for version retirement.

### GatewayKeyRotation / GatewayKeyRotationTransition

meaning:
  Durable operations truth for one approved gateway delegation-key lifecycle
  program. The aggregate records the current closed phase and bounded evidence;
  the transition ledger records each accepted state change exactly once:

```text
requested -> awaiting-approval -> approved -> generation-prepared
  -> key-generated
  -> overlap-deploying -> overlap-ready -> new-key-active
    -> draining-old-grants -> retirement-deploying -> retirement-ready
      -> old-key-retired -> revocation-prepared -> completed
```

owned by:
  Operations owns the aggregate, transition law, Postgres store, approval and
  child-deployment identities, and public read projection. Core owns only the
  pure rotation approval subject plus the focused `delegation-key:rotate` and
  `delegation-key:rotate-approve` permissions shared by public contracts.

durable:
  Yes. Rotation state, version, operator correlation, deterministic child
  deployment identities, accepted current-graph evidence, phase timestamps,
  generation provider-registration identity, generation action digest, and the
  grant-drain deadline survive process restart. One nonterminal rotation owns a
  `(workspace, gateway, purpose, issuer)` binding.

may contain secrets:
  Internal operations state may retain a `SecretReference` and provider version
  identity, never plaintext, ciphertext, or private key bytes. Public rotation
  readback omits the reference and provider version metadata.

interpreted by:
  A later operations-owned rotation program. This state layer performs no
  provider, Docker, gateway, HTTP, filesystem, or other external IO.

laws:
  Request and advancement require `delegation-key:rotate`; accepting or
  rejecting the immutable rotation review subject requires the distinct
  `delegation-key:rotate-approve` scope. The approval subject is derived from
  persisted rotation intent and contains no secret reference or private
  material. Every advancement also requires an exact expected status and
  version and a unique transition id. Exact transition replay is
  idempotent; semantic reuse conflicts. The aggregate CAS and transition record
  share one UnitOfWork transaction. Child operation identities are persisted
  before their effects begin. Accepted overlap and retirement evidence must
  preserve those exact identities. Activating replacement B requires both
  `delegation-key:rotate` and `delegation-key:activate`; graph execution alone
  grants neither authority. Activation atomically makes B the sole signer
  while A remains verification-capable during the drain. The old-grant drain
  deadline is computed
  from the maximum issued capability lifetime plus bounded clock skew when the
  new key becomes active; advancement consults an injected trusted clock and
  never sleeps. Uncertain child effects move to `blocked` with retained evidence
  for recovery/fencing work; absence of a folded result never licenses a retry.
  Retirement deployment acceptance advances the aggregate only to
  `retirement-ready`: current graph G[B] is then accepted, but old key A and its
  secret version remain intact until a separate retirement program records
  their exact successful lifecycle evidence. The program first joins A's
  immutable signing-key reference to admitted provider-version metadata before
  retiring A publicly. It then commits `old-key-retired`, commits an exact
  reference/version/correlation/action-digest checkpoint at
  `revocation-prepared`, performs provider IO outside every operations
  transaction, accepts only a matching `SecretVersionRevocationReceipt`,
  revokes A's public identity, and advances to `completed`. A definite
  pre-mutation provider failure leaves the same prepared action retryable;
  uncertain or malformed mutating evidence moves the rotation to `blocked` for
  recovery/fencing. Exact provider replay uses the same durable correlation and
  cannot mutate a sibling secret version.
  Approved rotation does not call a provider directly: it first commits
  `generation-prepared`, performs provider IO outside transactions, and then
  folds bounded evidence. Exact success and uncertainty replay are idempotent;
  changed action lineage, provider evidence, or failure identity conflicts.

### GatewayProbeAttempt

meaning:
  Durable operations evidence for one authorized request to delegate an exact
  probe to a graph-declared runtime-island gateway.

owned by:
  `control-plane-kit-operations`.

durable:
  Operations records authorized intent before dispatch, including workspace,
  current graph, gateway, runtime, target, probe kind, canonical request
  digest, issuer, key id, `jti`, and bounded validity times. After the external
  dispatch it records only a closed terminal status and bounded result or error
  evidence.

may contain secrets:
  No. The signed capability, signature, verification key, private gateway
  endpoint, operator credential, and target credentials remain transient at
  the outer IO boundary.

interpreted by:
  `GatewayProbeCommandService` derives the request from current graph truth and
  calls an injected `GatewayProbeDispatcher` after the intent transaction
  commits. HTTP and MCP adapters call the same service.

laws:
  Authorization requires `gateway-probe:use`. The target must be present in the
  graph-derived gateway target map and support the requested closed probe kind.
  One workspace/request id identifies one immutable intent; exact retries
  return existing evidence without redispatch, while changed intent conflicts.
  No Postgres transaction spans signing, gateway transport, or target IO.

### Authenticated Workload Node Control

meaning:
  AUTH.NODE-CONTROL language for invoking one declared workload control surface
  through a gateway relay and a workload-local SDK verifier. The architectural
  boundary is frozen by `control-plane-kit-core/docs/NODE_CONTROL_TOPOLOGY.md`;
  executable pure contracts live in
  `control-plane-kit-core/src/control_plane_kit_core/node_control.py`.

owned by:
  `control-plane-kit-core` owns pure provider-neutral command, grant,
  descriptor, and result contracts. `control-plane-kit-operations` owns
  authorization, durable intent, replay/evidence, and result folding. The
  separately installable `control-plane-kit-server-sdk` owns workload verifier,
  dispatch, `ControlPlaneVariable[State, Command, Result]`, and optional
  FastAPI route accrual.

durable:
  Commands and results may become durable operations evidence. SDK process-local
  state is not durable. Durable adopters such as service discovery own their own
  UnitOfWork, command ledger, revisions, and restart replay.

may contain secrets:
  No. This is a producer contract: core enforces bounded syntax and the exact
  credential/endpoint envelopes in
  `control-plane-kit-core/docs/NODE_CONTROL_PUBLIC_MATERIAL.md`; it cannot prove
  arbitrary text is semantically public. Operator bearer credentials, compact
  grants, signatures, private keys, secret values, private endpoint details,
  and unbounded payloads remain forbidden from graph truth, descriptors,
  grants, commands, results, events, observations, read models, logs, route
  responses, and issue evidence.

interpreted by:
  cpk-server operations services issue bounded intent, cpk-local-gateway relays
  only after transit authority, and a workload SDK verifies end-to-end workload
  authority before one `ControlPlaneVariable` transition.

laws:
  Canonical newly published workload SDK routes use `/__control`; existing
  `/__deploy` control-route descriptors remain bounded legacy compatibility
  until a focused migration changes them. Gateway transit authority and
  workload end-to-end authority have distinct audiences, command identities,
  verifier responsibilities, and replay scopes. Variables may activate, drain,
  weight, or select only graph-declared identities; they must not create graph
  edges, accept caller URLs, expose arbitrary object reflection, or introduce a
  second handler/plugin mechanism.

public contract shape:
  `NodeControlGraphReference` gives workspace, graph revision, node, provider
  socket, variable, and weighted target identities one closed nominal role.
  The wire retains ordinary strings whose role comes from the enclosing field;
  codec construction proves bounded role syntax, not graph membership. An
  authenticated producer must derive those references from the admitted graph,
  and the SDK/operations boundary verifies that provenance before dispatch.
  Resolved endpoint and secret material objects cannot substitute for graph
  references. A bare DNS-looking string remains semantically ambiguous until
  that producer-owned graph join rather than being guessed from bytes.
  Other open text and scalar material follows
  `cpk.node-control.public-material.v1`: exact lexical exclusions are applied
  literally and after one ASCII percent projection, while semantic publicness
  remains producer-attested. Python object representations and contract errors
  omit open/rejected material; canonical descriptors retain admitted public
  values required by the language-neutral wire.

  `NodeControlCommandRequest` binds one workspace, graph revision, node,
  provider socket, variable, closed operation, request id, idempotency key,
  command codec, transition precondition, and bounded typed payload. The
  request declares `jcs-rfc8785.v1`; its RFC 8785 UTF-8 canonical bytes and
  SHA-256 digest are frozen by the language-neutral vectors in
  `control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md`.
  `DelegatedWorkloadNodeControlGrant` binds that canonical request digest under
  a distinct `workload-node-control` key purpose. Its complete canonical value
  has an exact reachable 2,111-byte ceiling and a nominal
  `WorkloadNodeControlGrantDigest`. Strict raw request and workload-grant
  decoders bound input before parsing, reject duplicate keys recursively, and
  require byte-for-byte RFC 8785 reconstruction. Gateway probe grants are a
  different type and are rejected at this boundary.
  `DelegatedGatewayNodeControlTransitGrant` is the separate, unsigned authority
  for one selected graph gateway to relay that exact request. Its audience is
  derived as `gateway:{workspace_id}:{gateway_node_id}` from nominal graph
  references, not accepted as caller-selected truth. The complete 21-field RFC
  8785 value is bounded to 2,834 bytes and is the transit signing payload; its
  verifier compares purpose, issuer/key, attempt, graph coordinates, gateway,
  request, and time without performing signature verification or IO. Gateway
  transit, gateway probe, workload command, and workload surface-read grants
  are nominally non-substitutable. Transit authority does not authorize the
  workload command: the workload still requires its distinct end-to-end grant.

  #1555 owns durable intended request, workload-grant, and transit bytes plus
  their nominal digests. #1556 owns graph-authorized construction plus a
  reference-only deferred signing request. Post-commit signing consumes that
  deferred request; #1243 consumes the signed transit grant and owns gateway
  admission, replay protection, relay, and workload response handling. No
  canonical field is re-encoded or separately treated as a generic reference
  at those handoffs.
  `ControlPlaneVariableDescriptor` names one scalar, map, or atomic
  weighted-routing state codec and an exact operation index. `read-state` has
  no command codec and returns `control.state.v1`; `apply-command` names the
  kind's matching replacement codec and returns `control.transition.v1`.
  Descriptors declare both entries once in canonical order and retain the
  canonical `node-control` route/capability pair. Strict codecs reject legacy
  flat command/result fields, unknown keys and kinds, partial indexes, and
  reordered entries. Results expose only bounded state and closed evidence
  codes, never provider errors, signatures, credentials, endpoints, or secret
  values.

  `WorkloadNodeControlSurfaceDescriptor` groups one or more variable
  descriptors under the exact HTTP provider socket through which they are
  reached. Product runtime contracts and compiled block specifications retain
  a canonical `control_surfaces` tuple, so accepted graph truth can answer:

  ```text
  node x provider socket -> exact declared variable contracts
  ```

  Surfaces and variables are finite, canonically ordered, and unique in their
  local identity domains. The same variable name may occur on distinct
  provider sockets because the full identity is `(provider socket, variable)`.
  `NODE_CONTROLLABLE` is present exactly when at least one surface is declared.
  A surface carries no endpoint, credential, current state, version, health,
  or runtime status. Legacy product and graph descriptors omit the field and
  retain their canonical bytes; a present empty field is noncanonical.

  `WorkloadNodeControlSurfaceDeclaration` wraps that static descriptor in the
  versioned `workload-node-control-surface-declaration.v1` identity domain.
  `NodeControlSurfaceReadRequest` binds one exact graph target, declaration
  identity, request id, and the closed `capabilities|status` read kind under
  `jcs-rfc8785.v1`. `DelegatedWorkloadNodeControlSurfaceReadGrant` binds the
  request digest plus issuer, key, audience, bounded time window, JTI, and the
  distinct `workload-node-control-surface-read` key purpose. Its pure verifier
  compares claims only; this unsigned value is not authentication or authority
  until the server SDK verifies its signed envelope and durable key policy.

  The workload route set declares `GET /__control/capabilities` and
  `GET /__control/status` under the least-privilege
  `node-control-surface:read` scope. Core does not implement HTTP or inspect a
  registry.

  `NodeControlSurfaceCapabilitiesResult` and
  `NodeControlSurfaceStatusResult` form a disjoint nominal result sum under the
  common `workload-node-control-surface-read-result.v1` profile. Both retain the
  exact request and declaration context while their wire carries only the
  common profile and canonicalization, request ID/digest, read kind,
  declaration identity, and variant data. Capabilities echoes the exact
  declaration. Status carries a canonical installed-variable subset and
  derives `NodeControlSurfaceRegistryCoverage` as exactly
  `none|partial|complete`; wire coverage is checked against that derivation and
  is never trusted as live registry truth. The strict codec admits mappings no
  larger than the reachable 16,902-byte capability or 4,811-byte status ceiling
  and applies the smaller exact context ceiling before nested interpretation.

  Signed-envelope admission belongs to #1542. The FastAPI adapter, raw HTTP
  byte admission, and proof that reported identities came from a maintained
  live registry without variable invocation belong to #1507. Language-neutral
  declaration, request, and result vectors live in
  `control-plane-kit-core/tests/fixtures/node_control_surface_read_canonical_wire_v1.json`
  and are described by `control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md`.

  ```json
  {
    "operation_contracts": [
      {
        "operation": "read-state",
        "command_codec": null,
        "result_codec": "control.state.v1"
      },
      {
        "operation": "apply-command",
        "command_codec": "control.replace-weighted-routing.v1",
        "result_codec": "control.transition.v1"
      }
    ]
  }
  ```

  Results are a closed nominal sum rather than one optional-field record.
  Successful reads carry one versioned state whose state codec matches the
  variable. Successful transitions carry one version and exactly `applied` or
  `no-change` evidence. Rejected and failed results carry one closed evidence
  code but no state or version; read and apply have separate rejection laws,
  and failure is always `internal-failure`. `NodeControlResultCodec` is bound to
  the selected variable descriptor and rejects legacy, cross-variant, and
  variable-mismatched material.

  ```json
  {
    "request_id": "request-read-1",
    "operation": "read-state",
    "status": "succeeded",
    "codec": "control.state.v1",
    "state_codec": "control.scalar.v1",
    "version": 4,
    "state": {"kind": "scalar", "value": "target-a"}
  }
  ```

  ```json
  {
    "request_id": "request-apply-1",
    "operation": "apply-command",
    "status": "succeeded",
    "codec": "control.transition.v1",
    "version": 5,
    "evidence": {"code": "applied"}
  }
  ```

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

### AuthenticatedPrincipal

meaning:
  Credential-free identity and workspace grants produced after successful
  authentication.

owned by:
  `control-plane-kit-core` for the pure value. cpk-server owns credential
  verification; operations owns authorization.

durable:
  No. Durable history may retain the authenticated subject id as actor
  provenance, never the credential.

may contain secrets:
  No.

interpreted by:
  cpk-server authentication composition and operations authorization.

laws:
  Request payloads cannot construct authority. Workspace grants contain only
  closed `PolicyScope` values. Operator, service, and worker identities remain
  distinct. Raw credentials are absent from equality, hashing, descriptors,
  representations, logs, errors, and durable evidence.

### TrustedCommandContext

meaning:
  One authenticated principal's exact authority for one workspace command.

owned by:
  `control-plane-kit-core` for the pure value and
  `control-plane-kit-operations` for authorization/derivation.

durable:
  No. Its actor identity may be copied into durable history.

may contain secrets:
  No.

interpreted by:
  Operations adapters and command services.

laws:
  Its workspace must be present in the principal's grants and its scopes must
  exactly equal that workspace grant. A command body supplies intent, never
  identity or authority. Public operations routes derive actor provenance,
  scopes, and worker identity only from this context before store access.
  Operator principals cannot perform worker lifecycle commands. Runtime and
  ingress authority registration, reading, use, and revocation remain distinct
  permissions.

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

### ApprovalSubject / ApprovalRequest

meaning:
  A closed, immutable description of the exact operational intent presented to
  a reviewer, plus the durable suspension point asking that reviewer to accept
  or reject it:

```text
ApprovalSubject
  = ActivityPlanApprovalSubject
  | GatewayKeyRotationApprovalSubject
```

owned by:
  `control-plane-kit-core` owns the pure closed subject language and review
  digest. `control-plane-kit-operations` owns durable requests, decisions,
  authorization, stores, and read models.

durable:
  Yes.

may contain secrets:
  No.

interpreted by:
  Approval command service, approval queue read model, cpk-server HTTP/MCP
  routes.

laws:
  Subject kind, bounded subject descriptor, and deterministic review digest are
  persisted together. Plan approvals retain their existing public shape.
  Rotation approvals expose only secret-free rotation intent. Request,
  decision, and execution permissions are distinct. Admission rejects missing,
  rejected, stale, wrong-plan, or insufficient-scope plan approval. Rotation
  advancement similarly rejects missing, rejected, stale, wrong-subject, or
  insufficient-scope evidence. Neither execution nor lifecycle advancement may
  bypass approval.

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
  Workspace lineage pairing the accepted operator-authored graph version with
  the exact realized graph projection that successfully executed.

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
  Runtime success alone does not silently mutate the pointer. Advancement
  atomically compares current authored and realized identity, desired authored
  and realized identity, and desired revision. Its durable evidence names the
  accepted realized projection and digest while public current/desired graph
  descriptors remain authored truth.

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
  durable truth. When an authority has an admitted access delivery, it may carry
  the matching secret-free `RuntimeAuthorityAccessDelivery` contract. It does
  not contain Docker clients, HTTP clients, stores, credentials, socket paths,
  host paths, or process handles.

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

### EffectAttemptState

meaning:
  Pure durable-coordination contract for one possible performance of an
  external effect. Its identity is `(run_id, activity_id, attempt)` and its
  active ownership fence is `(worker_id, generation)`.

owned by:
  The pure contract is exported by `control-plane-kit-core.operations`.
  `control-plane-kit-operations` owns persistence and transactional enforcement.

durable:
  Yes, when #1105 provides the operations-store projection. The pure value does
  not itself perform persistence.

may contain secrets:
  No. Request, result, and recovery evidence are lowercase SHA-256 fingerprints,
  not raw requests, provider results, credentials, addresses, or exception text.

interpreted by:
  Operations lease/fence stores, the execution coordinator, recovery services,
  and operator projections.

laws:
  The closed attempt states are `started`, `succeeded`, `failed`, `unsupported`,
  `uncertain`, and `abandoned`. A stale or foreign fence cannot fold a result.
  An exact duplicate terminal fold is idempotent; an incompatible duplicate is
  rejected. Uncertainty is neither success nor failure and can become success,
  failure, or abandonment only through an explicit `EffectRecoveryDecision`
  that retains the exact attempt identity, the uncertainty fingerprint being
  resolved, and the public reconciliation evidence fingerprint.

  Retry creates a new positive attempt number and names the immediately prior
  attempt; it never rewrites the previous attempt. `ActivityEvent` remains the
  canonical append-only operational history. `EffectAttemptState` is the current
  coordination/fence projection, not a second journal.

  The persistence transaction introduced by #1105 must validate the active
  lease and fence under row lock, fold the attempt state, and append its bounded
  lifecycle event atomically. External interpreter IO never occurs inside that
  Postgres transaction. Fence takeover and lease-expiry authority belong to that
  operations transaction, not to the pure fold.

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
