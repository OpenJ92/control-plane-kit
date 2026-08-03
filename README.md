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
specific runtime target. Graph nodes reference admitted authorities; endpoints,
TLS private material, tokens, and local socket paths do not become graph truth.

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
project the same operations command/read services and authorization laws. The
server process composes dependencies; it does not own runtime, ingress, graph,
approval, or secret semantics.

## Documentation

- [Operating Model](docs/OPERATING_MODEL.md)
- [Control Plane Language](docs/CONTROL_PLANE_LANGUAGE.md)
- [Language Study Guide](docs/CONTROL_PLANE_LANGUAGE_STUDY_GUIDE.md)
- [Postgres Unit Of Work](docs/POSTGRES_UNIT_OF_WORK.md)
- [Server Product Rollout](SERVER_PRODUCT_ROLLOUT.md)
- [Current Backend Validation](current_backend/README.md)

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
