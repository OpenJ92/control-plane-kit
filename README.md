# control-plane-kit

`control-plane-kit` is a deployment language and durable control-plane backend.
It describes applications as graphs of products, runtimes, sockets, authorities,
secret references, and public ingress. Operations can compare current and
desired graph truth, produce a reviewable plan, require approval, realize the
accepted effects, record observations, and advance current truth only after
successful realization.

This repository is the coordination, pure-language, and durable-operations
repository. Concrete interpreters, packaged server products, and durable secret
custody live in sibling repositories.

## Start with one deployment

Suppose Hello serves a greeting and a multiplexer forwards requests to it.
The desired graph places both nodes on one Docker runtime and connects the
multiplexer's HTTP requirement to Hello's HTTP provider. A draft can say
that the nodes belong together before the operator chooses an engine.
Registering a Docker authority makes an engine available in the workspace.
Selecting that authority on the graph tells the worker which engine to use.

Saving the draft records a revision. Selecting it as **Desired** gives the
planner a target. CPK compares that target with **Current**, records a plan
and its approval, and admits the approved work for a worker to execute.
The operator can then compare the recorded run and Current graph with an
observation: does a request through the multiplexer actually return the
configured greeting? Adding public ingress is a second graph revision with
its own plan. The application ingress goes to the multiplexer. A separately
declared management ingress goes to the runtime gateway.

The earlier Hello deployment exercised create, update, and teardown on a real
installation. Its final run reached the empty graph. Docker and Cloudflare
confirmed removal of the workload containers, network, two tunnels and their
DNS records. Eight named volumes remained at that inspection. That distinction
matters whenever reporting what an empty graph means. [Teardown receipt](https://github.com/OpenJ92/control-plane-kit-servers/issues/225#issuecomment-6653018702)

## Where the project is headed

Follow the [product roadmap](https://github.com/users/OpenJ92/projects/5) and
the owning issue for current status. The installation milestones in order are:

1. Operate a fresh CPK installation through its own Codex MCP connection.
   Verify its workspace independently in the website, then deploy private
   Hello and mux and read the greeting through mux. [Fresh sibling #431](https://github.com/OpenJ92/control-plane-kit-servers/issues/431)
2. Add the wrapped Cloudflare connector to that running topology. Verify the
   public HTTPS greeting and the signed management checks on their respective
   routes. [Named ingress #421](https://github.com/OpenJ92/control-plane-kit-servers/issues/421)
3. Use the grandparent to deploy a child CPK installation as an ordinary
   graph. Verify the child's readiness from the grandparent, connect Codex to
   the child's own MCP endpoint, read its workspace and operate an application
   there. The two installations keep separate identities and histories.
   [Recursive deployment #163](https://github.com/OpenJ92/control-plane-kit-servers/issues/163)

Later work includes making gateway-to-workload relationships explicit in the
graph and separating compute provisioning from deployment. A local, owned, or
cloud host could supply a registered Docker endpoint and protected credential
references for the same interpreter. [Management relationships #2112](https://github.com/OpenJ92/control-plane-kit/issues/2112) · [Provisioning and Docker over mutual TLS #2083](https://github.com/OpenJ92/control-plane-kit/issues/2083)

## Repository Shape

```text
control-plane-kit-core
  pure graph, product, socket, planning, policy, authorization,
  runtime-effect, ingress, verification, and secret-reference language

control-plane-kit-operations
  Postgres stores, UnitOfWork, workspace and graph truth, registrations,
  planning, approval, lifecycle, coordinator, observations, and read models

control-plane-kit-interpreters
  concrete RuntimeEffectRequest -> IO RuntimeEffectResult implementations
  Docker SDK, Cloudflare API, runtime verification, and secret-provider clients

control-plane-kit-secrets
  encrypted durable secret custody, scoped resolution, rotation, revocation,
  and provider-local audit

control-plane-kit-servers
  cpk-server, cpk-local-gateway, seeded product processes, descriptors,
  Dockerfiles, OCI coordinates, and catalogue metadata

control-plane-kit-server-sdk
  application wrappers and receivers for declared management and health checks

control-plane-kit-web
  operator website, graph inspection and human review of deployment plans
```

The governing dependency direction is:

```text
core
  <- operations
  <- composition boundaries

core
  <- interpreters

core + operations + interpreters + secrets clients
  <- cpk-server process composition

descriptors and images
  <- control-plane-kit-servers
```

Core does not import Postgres, Docker, Cloudflare, FastAPI, HTTPX, MCP, or
package-owned server code. Operations does not import concrete interpreter SDKs.

## Operator Program

The intended deployment program is:

```text
Deploy(current, desired)
  -> plan
    -> review and approve
      -> admit
        -> claim
          -> start
            -> execute external effects
              -> observe
                -> advance current graph
```

The same shape covers:

```text
initial deployment = Deploy(EmptyGraph, desired)
update             = Deploy(current, desired)
teardown           = Deploy(current, EmptyGraph)
no-op              = Deploy(graph, graph)
```

Every operator command owns one explicit Postgres transaction. Stores share the
UnitOfWork connection and never commit independently. Docker, Cloudflare, HTTP,
filesystem, probe, and secret-provider effects occur outside those transactions.

## Runtime Effects

Operations and interpreters meet at a provider-neutral boundary:

```text
core:
  RuntimeEffectRequest

interpreter:
  RuntimeEffectRequest -> IO RuntimeEffectResult

operations:
  ActivityJournal x RuntimeEffectResult -> ActivityJournal'
```

The Docker composition currently follows:

```text
cpk-server
  -> configured operations application
    -> ExecutionCoordinator
      -> RuntimeInterpreterDispatcher
        -> DockerRuntimeInterpreter
          -> Python Docker SDK
```

Interpreter availability is process capability. A
`RegisteredRuntimeAuthority` is workspace-scoped permission and material for a
specific runtime target. For a named Docker target, the graph's runtime record
selects the authority. Access to the Docker socket or TLS credential is
delivered to the interpreter; the workloads remain on their declared runtime.

## Products And Sockets

Products are immutable, digest-pinned descriptors. A graph instantiates
registered products and connects typed provider sockets to compatible
requirement sockets:

```text
provider node.socket
  -> SocketConnection
    -> consumer node.requirement
      -> compiled runtime material
```

Socket edges drive dependency binding. Product identity must not trigger hidden
Docker or operations branches. Removing an edge removes the authority and
material compiled from that edge.

The current server-product catalogue includes cpk-server variants, the local
gateway, Hello, router, multiplexer, Postgres data-service material, and
Cloudflare connector material. Product descriptors and OCI coordinates are
owned by `control-plane-kit-servers`.

## Runtime Islands And Ingress

A runtime island can contain private workload nodes and a `cpk-local-gateway`.
The gateway is an ordinary product node: it receives a graph-derived target map,
accepts only closed authenticated commands, and probes declared private targets.
It does not own graph truth and does not spawn workloads.

Named public ingress is socket-adjacent exposure:

```text
NamedPublicIngress
  -> provider-specific ingress interpreter
    -> generated connector authority
      -> cloudflared connector
        -> cpk-local-gateway.control
```

Runtime authority is the power to realize or change the island. Gateway plus
public ingress is a removable access overlay.

## Secrets

The pure language carries references and explicit delivery intent, never secret
values:

```text
SecretReference
  + SecretUseIntent
    + SecretDelivery
      -> interpreter resolves plaintext at IO
```

Operations admits provider metadata, authorizes use, and records bounded audit
correlation. `control-plane-kit-secrets` owns encrypted custody. Interpreters
resolve and deliver values only at the external-effect boundary. Plaintext and
ciphertext must not enter graph data, operations persistence, runtime request
descriptors, events, observations, logs, errors, or public responses.

## Public Boundary

`cpk-server` is the FastAPI/MCP process wrapper around operations. HTTP and MCP
share Operations services and authorization laws. The installed MCP profile
determines which of those operations a client can discover and call. The
server process composes dependencies; it does not own runtime, ingress, graph,
approval, or secret semantics.

For agents, the installed server supplies instructions and a tool catalogue.
Tool discovery, workspace access and a verified application response answer
three different questions. The [operating model](docs/OPERATING_MODEL.md#operating-an-installed-cpk)
walks through each. A future ACP service would connect the website to an agent
that operates CPK through MCP. [ACP roadmap](https://github.com/OpenJ92/control-plane-kit/issues/2090)

## Documentation

- [Operating Model](docs/OPERATING_MODEL.md)
- [Control Plane Language](docs/CONTROL_PLANE_LANGUAGE.md)
- [Language Study Guide](docs/CONTROL_PLANE_LANGUAGE_STUDY_GUIDE.md)
- [Postgres Unit Of Work](docs/POSTGRES_UNIT_OF_WORK.md)
- [Server Product Rollout](SERVER_PRODUCT_ROLLOUT.md)
- [Current Backend Validation](current_backend/README.md)
- [Test Evidence And Acceptance](docs/TESTING.md)

Historical roadmap, review, and learning documents remain as design evidence.
They may name retired source paths and old commands, but they are not current
execution instructions.

## Validation

Run current package gates independently:

```bash
./control-plane-kit-core/test.sh
./control-plane-kit-operations/test.sh
```

Run the exact multi-repository, non-provider-mutating backend gate:

```bash
./current-backend-test.sh --report /tmp/current-backend-report.json
```

That gate materializes exact Git objects, validates cross-repository contracts,
runs all five package suites, exercises authenticated cpk-server HTTP/MCP
source-live acceptance, and audits Docker residue. It does not claim published
OCI or provider-mutating acceptance.

The mutable aggregate package and mixed root test suite have been retired.
Historical reproducibility remains available only through the immutable tag:

```bash
./reference-test.sh
```

`reference-test.sh` verifies
`pre-server-product-extraction-2026-07-20` resolves to
`20129959d3b0f8e8bd5dbdafdf51c0a5d592a9ec`, archives that tag into a temporary
directory, runs its own historical suite, records bounded evidence, and removes
only exact-owned resources.
