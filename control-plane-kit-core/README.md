# control-plane-kit-core

`control-plane-kit-core` is the pure language kernel of Control Plane Kit. It
describes deployable systems, proposed transitions, authority references, and
runtime effects as immutable Python values. It performs no database, Docker,
Cloudflare, HTTP, filesystem, or secret-provider IO.

The package is pre-alpha. Its contracts are executable and extensively tested,
but the public language continues to evolve while backend hardening proceeds.

## Place In The System

```text
operator-authored topology
  -> DeploymentGraph
    -> validate
      -> diff(current, desired)
        -> ActivityPlan
          -> RuntimeEffectRequest

RuntimeEffectRequest
  -> external interpreter IO
    -> RuntimeEffectResult
```

Core owns the values and pure transformations in that diagram. Durable command
execution belongs to `control-plane-kit-operations`; concrete effects belong to
`control-plane-kit-interpreters`; process and OCI packaging belong to
`control-plane-kit-servers`.

## Language Families

### Topology And Products

- deployment topologies, runtimes, blocks, nodes, and graph descriptors;
- typed provider and requirement sockets;
- socket connections and graph-derived dependency binding;
- immutable external product descriptors and digest-pinned OCI references;
- product instance configuration, public environment, configuration artifacts,
  retained data, and secret-delivery intent;
- deterministic graph codecs, validation, and structural diffs.

The principal pure pipeline is:

```text
DeploymentTopology
  -> compile_topology
    -> DeploymentGraph
      -> validate_graph
        -> diff_graphs
          -> GraphDiff
            -> compile_activity_plan
              -> ActivityPlan
```

Socket connections carry dependency meaning:

```text
provider node.socket
  -> SocketConnection
    -> consumer node.requirement
      -> compiled endpoint, configuration, and delivery material
```

No product identity triggers hidden runtime behavior. Removing an edge removes
the material derived from that edge.

### Planning And Recovery Values

Core defines closed activity variants, activity dependencies, schedules, saga
journals, compensation plans, recovery decisions, and deterministic codecs.
These values describe what should happen; they do not perform the work.

### Runtime Effects And Authorities

`RuntimeEffectRequest` is the provider-neutral handoff to an interpreter. It can
carry pinned product material and references to admitted runtime, image-pull,
ingress, delegation, and secret authorities. It never carries raw credentials.

```text
core:
  RuntimeEffectRequest

interpreter:
  RuntimeEffectRequest -> IO RuntimeEffectResult

operations:
  ActivityJournal x RuntimeEffectResult -> ActivityJournal'
```

### Policy And Public Contracts

Core also owns pure contracts for:

- authenticated principals, workspace grants, and focused policy scopes;
- approval subjects and command idempotency;
- HTTP/MCP command and read parity;
- process liveness, readiness, shutdown, and publication obligations;
- runtime verification and bounded probe intents;
- named public ingress and observed public endpoints;
- gateway delegation grants and verifier projections;
- secret references, use intents, and environment/file deliveries.

These contracts let adapters share one vocabulary without importing FastAPI,
MCP, Postgres, Docker, or provider SDKs into core.

## Non-Negotiable Laws

- Graph truth is desired operator intent, not observed runtime state.
- Product descriptors are immutable and secret-free.
- Accepted OCI references are digest-pinned.
- Socket edges drive dependency binding.
- Raw secret values never enter graphs, runtime request descriptors, events,
  observations, logs, errors, or public projections.
- Unknown variants and extra fields fail closed.
- Core remains deterministic and free of external effects.

## Package Map

Important modules include:

```text
control_plane_kit_core.algebra          topology authoring values
control_plane_kit_core.topology         compilation, validation, codecs, diffs
control_plane_kit_core.products         product and OCI descriptor language
control_plane_kit_core.planning         plans, scheduling, saga, recovery
control_plane_kit_core.runtime_effects  interpreter request/result boundary
control_plane_kit_core.operations       command/read/process contracts
control_plane_kit_core.public_ingress   provider-neutral ingress language
control_plane_kit_core.secrets          secret references and delivery intent
control_plane_kit_core.verification     closed health/readiness contracts
control_plane_kit_core.gateway_delegation
control_plane_kit_core.identity
control_plane_kit_core.policies
```

The package root re-exports established public values. New code may import from
the focused modules when module ownership is useful to the reader.

## Installation

From this repository checkout:

```bash
python -m pip install ./control-plane-kit-core
```

Host Python dependencies are not assumed for project validation. The supported
development gate is Docker-first:

```bash
./control-plane-kit-core/test.sh
```

The gate runs the current `unittest` suite, compile checks, import checks, and
package-integrity policy. It does not execute the retired aggregate package.

## Documentation

- [Repository overview](../README.md)
- [Control Plane Language](../docs/CONTROL_PLANE_LANGUAGE.md)
- [Language Study Guide](../docs/CONTROL_PLANE_LANGUAGE_STUDY_GUIDE.md)
- [Operating Model](../docs/OPERATING_MODEL.md)
- [Test Evidence And Acceptance](../docs/TESTING.md)
