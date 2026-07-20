# Domain Language Admission And Migration Manifest

## Purpose

`control_plane_kit.domains` is not a home for feature-specific helpers. It owns
independent closed languages whose values can be interpreted by products,
operations, and entrypoints without knowing those implementations exist.

A language qualifies when its meaning is substantially defined by:

- typed identities;
- bounded commands, intents, or policies;
- closed results, outcomes, events, or states;
- pure validation, evolution, replay, or scheduling laws;
- strict adjacent descriptors when values cross durable boundaries; and
- abstract protocols, when needed, that do not select an implementation.

No single bullet is sufficient. In particular, typed product configuration is
not automatically a domain language.

The import law is:

```text
domains.<name>.language -> core

operations.<name> -> domains.<name> + persistence/application services
products.servers.<product> -> domains.<name> when interpreting that language
entrypoints.<product> -> product + operations + interpreters
```

Domain languages never import stores, UnitOfWork implementations, FastAPI
applications, HTTP or Docker clients, product declarations, or process
bootstrap. Domains do not import one another unless a later ADR justifies an
explicit algebraic composition.

## Admission Decisions

| Candidate | Decision | Independent language evidence |
| --- | --- | --- |
| Discovery | Admit | Identity, registration and lease values; bounded commands; closed outcomes; strict command/result codecs. |
| Webhook delivery | Admit | Delivery identity and intent; claims and attempt events; closed state evolution and replay; strict event/intent codecs. |
| Idempotency | Admit | Route/gateway policy, request identity, record state, closed method/status/outcome values, deterministic identity derivation, and strict policy descriptors. |
| Load generation | Admit | Policy and run command identities; closed request/run outcomes; pure validation and deterministic request scheduling; strict descriptors. |

Admission does not replace product identity. Service discovery, webhook
delivery, idempotency gateway, and load generator remain uniformly deployable
server products under `products.servers`. Their domain packages describe only
their pure interior languages.

## Rejected Categories

| Category | Why it is not a domain |
| --- | --- |
| Configuration artifacts | Part of the deployment graph language and therefore core. |
| Verification contracts | Graph-visible desired verification truth and therefore core; probe execution is an interpreter. |
| Resource lifecycle | Graph-visible ownership and retention semantics and therefore core. |
| Docker | An external-effect interpreter for supplied graph and effect values. |
| Products | Graph-visible deployable declarations that interpret core/domain values. |
| Policies | Durable/application decision services over supplied plan and authority facts; operations, not an independent product language. |
| Generic workflows | Durable control-plane orchestration and transaction ownership; operations. |
| Router, proxy, multiplexer, balancer | Server products with typed configuration, but no independent identity/command/event/replay language at present. |

## CoreDNS Direction

Discovery never knows CoreDNS exists. CoreDNS owns the pure product projection:

```text
domains.discovery.DiscoveryRegistrationRecord
  -> products.servers.coredns.CoreDnsConfiguration
    -> core.ConfigurationArtifact
```

The edge is `products.servers.coredns -> domains.discovery`. It grants no
permission to import a discovery registry store, service, UnitOfWork, or
entrypoint. A/AAAA projection intentionally loses endpoint port information;
future SRV support requires an explicit port-bearing contract.

## Exact Migration Manifest For #555

| Current source | Canonical destination | Must remain outside |
| --- | --- | --- |
| `control_plane_kit/discovery.py` | `domains/discovery/language.py` | `discovery_registry` stores/services and `discovery_server` app/bootstrap |
| `control_plane_kit/webhook/language.py` | `domains/webhook/language.py` | webhook HTTP interpreter, Postgres store/UoW, application service, FastAPI app |
| `control_plane_kit/idempotency.py` | `domains/idempotency/language.py` | gateway adapters, Postgres store/service, server process and product block |
| `control_plane_kit/load_generation.py` | `domains/load_generation/language.py` | request adapter, runnable server, process composition and product block |

For each move, #555 must preserve public value equality, strict descriptor
behavior, pure laws, and product behavior; update every consumer to the new
canonical import; prove the former module is absent; and add no compatibility
alias because the package is unreleased.

