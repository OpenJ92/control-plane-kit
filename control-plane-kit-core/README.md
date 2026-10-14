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

An instance can explicitly request process authority through
`ProductInstanceConfiguration.runtime_authority_deliveries`. The tuple defaults
to empty and compiles into the same named node; sibling nodes sharing its runtime
receive no declaration. It uses `RuntimeAuthorityAccessDelivery` references, never
credentials or host socket paths. This instance setting does not alter the
reusable `ProductRuntimeContract`.

Adding or removing a declaration changes the graph and creates a node
reconciliation plan. Empty declarations retain the existing canonical graph
format. Runtime product material carries the declaration into intent commitments.
Core #1793 supplies this language; Operations #1794 owns atomic recipient matching
and active workspace admission. This Core slice alone does not correct existing
runtime delivery, authorize replay of historical effects, or migrate stored graphs.

## Language Families

### Deployment-instance Secret Versions

Each node records one `SecretVersionSelection(reference, intent, version_id)`
per effective value-resolving reference/use, shared by all its delivery slots.
Compilation explicitly records `UnselectedSecretUse` when a version is not yet
known. Current graph descriptors require the selection array; a missing field
does not imply permission to resolve the latest version. Reusable product
descriptors contain delivery contracts, not instance version choices.

The pure `carry_forward_secret_selections(previous_graph, desired_graph,
explicit_choices, *, product_documents=())` transformation preserves exact known
choices across compatible image/configuration edits. Explicit choices use a
mapping from node ID to a tuple of `SecretVersionSelection` values. For example:

```python
successor = carry_forward_secret_selections(
    previous_graph, authored_graph,
    {"api": (SecretVersionSelection(reference, intent, "version-2"),)},
    product_documents=(previous_product_document, successor_product_document),
)
```

Changed registered product coordinates require both exact canonical documents.
They prove pure contract compatibility, not registry admission or authority.
An incompatible delivery remains explicitly unresolved until an explicit valid
choice is supplied. Version-only changes produce a real graph difference and
node reconciliation. Stops/removals need not invent historical versions.

Every current StartNode/ReconcileNode request has complete target material and
exact versions for all effective delivered secrets before dispatch. Resolution
and installation receipts are separate bounded assertions correlated with the
original request, complete selected slots and resource incarnations. A known
desired version or prior container identity is never proof of its installed
secret version. Coexisting configuration/secret receipts must agree on the
replacement incarnations and disposition.

Core supplies syntax and validation only. Operations and Interpreters must
enforce current authorization, exact resolution, recipient verification and
result/recovery admission before adopting this contract. Existing downstream
production remains pinned until that compatible chain is ready; generic success
or an older resolver cannot stand in for the required version evidence.

The structural `compile_activity_plan` recognizes a version-only difference as
ReconcileNode. Managed-update policy admission remains closed for this new
shape; Operations #1975 and the Servers #252 composition must enable it only
with the compatible resolution, authorization and receipt checks in place.

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

Configuration-aware `StartNode` and `ReconcileNode` invocations have an optional
closed `configuration-invocation-completion.v1` result profile. It binds the
original intent fingerprint and the canonical **whole** selection fingerprint:

```python
from control_plane_kit_core.configuration_invocation import (
    configuration_invocation_correlation_for_request,
    configuration_invocation_completion_for_result,
)

# request is the actual validated invocation; result is its unchanged result.
context = configuration_invocation_correlation_for_request(request)
completion = configuration_invocation_completion_for_result(context, result)
```

The reader validates the entire result, including ordinary evidence, failure and
observations, within the existing 8192-byte limit. An absent profile returns
`None`; a present malformed or miscorrelated profile refuses. A valid profile
requires a succeeded or failed terminal result. A producer may assert completion
only when all invoked mutations have finished, with no outstanding calls,
background work or delayed retries. These pure values do not prove that condition.
Failed invocations may leave attached resources, and completion never grants
cleanup permission. Operations must construct context from verified original
history; partial selections and fabricated requests cannot replace that proof.

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

## Secret authorization roles

`SecretResolutionGrant` authorizes operational use such as an image pull,
health probe, or remote TLS connection. The independent
`SecretVersionResolutionGrant` authorizes one `SecretVersionSelection` for
one consuming effect. Its required request and complete material fingerprints
bind the original intent, including every selected delivery slot. Neither
grant contains secret bytes or proves recipient installation.

The closed `SecretResolutionGrantCodec` encodes both roles. Request validation
admits exact nominal values, permits the same reference/use once in each role,
and rejects duplicates or consumer grants with mismatched commitments.
Transient grants remain excluded from the original intent fingerprint.
For an executable composition example, see
[`test_secret_version_grants.py`](tests/test_secret_version_grants.py).

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
