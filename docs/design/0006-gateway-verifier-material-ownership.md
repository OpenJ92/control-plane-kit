# Gateway Verifier Material Ownership

Status: Accepted for `HARDEN.AUTH.GATEWAY.ROTATION`
Last updated: 2026-08-04

## Decision

An authored gateway node declares stable delegation intent. It does not declare
the generated verifier environment used by a particular realization.

```text
authored graph
  DelegationAuthorityBinding(delegate, purpose, issuer)
    -> operations locks admitted delegation-key truth
      -> operations compiles DelegationVerifierProjection
        -> runtime-effect translation carries the projection
          -> interpreter materializes bounded public verifier environment
            -> cpk-local-gateway verifies delegated capabilities
```

This is the sole authoritative production path. A product descriptor describes
the gateway process, sockets, target-map input, secret deliveries, health
contract, and lifecycle. It must not contain placeholder verifier identity or
key material.

The following environment names are reserved for realized projection
materialization and must be absent from the canonical gateway descriptor and
authored product-instance public environment:

- `CPK_GATEWAY_PROBE_AUDIENCE`
- `CPK_GATEWAY_PROBE_ISSUER`
- `CPK_GATEWAY_PROBE_NODE_ID`
- `CPK_GATEWAY_PROBE_PROJECTION_ID`
- `CPK_GATEWAY_PROBE_VERIFICATION_KEYS_JSON`
- `CPK_GATEWAY_PROBE_VERIFIER`

`CPK_GATEWAY_TARGETS_JSON` remains graph-derived runtime target material. It is
not delegation authority and remains part of gateway realization. Socket-bound
secret deliveries, including the Postgres password delivery, also remain
independent of verifier projection.

## Custody Boundary

The public verifier projection contains only bounded public material: issuer,
audience, delegate node, projection identity, verifier kind, key ids, and
public keys. Private keys remain behind `SecretReference` and are resolved only
by the configured signer at the IO boundary.

Two source-live compositions are permitted:

1. Durable-provider custody. The real `control-plane-kit-secrets` process owns
   the private key and operations admits its reference and public key. This is
   required for gateway rotation and durable Cloudflare custody acceptance.
2. Explicit ephemeral test-only composition. A source-built test may generate
   a key at fixture startup and inject its signer/provider composition into the
   cpk-server process. It must be labelled test-only, must register and activate
   the corresponding public delegation key through authenticated cpk-server
   routes, and must not claim durable provider custody or become a production
   default.

In both cases, an authoritative graph contains the same stable
`DelegationAuthorityBinding`. Graph execution permission does not imply key
registration or activation permission.

## Gateway Path Matrix

| Path | Authoritative caller | Final classification | Key custody and migration |
| --- | --- | --- | --- |
| `authenticated-gateway-private` | cpk-server HTTP/MCP -> operations -> interpreters | Authoritative cpk-server workflow | Use explicit ephemeral test-only signer composition. Register and activate the public key before publishing the bound graph. Remove `_configured_gateway_instance` verifier replacements. |
| `public-gateway-ingress` | cpk-server deployment workflow | Authoritative cpk-server workflow | Use explicit ephemeral test-only signer composition and a stable gateway binding. Named ingress changes reachability, not verifier ownership. |
| `public-gateway-toggle` | cpk-server `G0 -> G1 -> G2` deployment workflow | Authoritative cpk-server workflow | Use the same admitted test key across overlay removal/recreation. Each gateway-bearing graph carries the stable binding; the workload-only graph does not. |
| `workspace-a-router-transition` / `router-transition` | cpk-server deployment workflow | Authoritative cpk-server workflow | Use explicit ephemeral test-only signer composition. Blue and green graphs bind the same gateway authority; no verifier values are authored. |
| `workspace-b-multiplexer-observer` | cpk-server deployment workflow | Authoritative cpk-server workflow | Use explicit ephemeral test-only signer composition and stable binding. |
| `workspace-c-postgres-retained-data` | cpk-server deployment workflow | Authoritative cpk-server workflow | Use explicit ephemeral test-only signer composition and stable binding. Postgres password remains a separate socket-bound secret delivery. |
| `workspace-d-negative-cleanup` | cpk-server deployment workflow | Authoritative cpk-server workflow | Use explicit ephemeral test-only signer composition and stable binding. Cleanup must not turn verifier configuration back into authored graph data. |
| `seeded-stress-public-ingress` | Aggregate of the four cpk-server workspace workflows above | Authoritative cpk-server workflow | Reuse each workspace's admitted test key. It is aggregate evidence, not a separate custody mechanism. |
| `cloudflare-tunnel-custody` in `cpk_server_secret_provider_source_live.py` | Authenticated cpk-server workflow with real Cloudflare and secrets-provider adapters | Authoritative cpk-server workflow | Use real `control-plane-kit-secrets` custody for the gateway signing key. Register/activate admitted public key truth before publishing `_public_gateway_ingress_graph`. |
| `gateway-key-rotation` in `cpk_server_secret_provider_source_live.py` | Authenticated cpk-server rotation and deployment workflows | Authoritative cpk-server workflow | Use real provider custody for A and provider-generated B. `_gateway_rotation_graph` must accept no verifier configuration; its stable binding compiles A, A+B, and B through operations. Later verifier reads are corroborative assertions only. |
| `cpk_server_secret_consumers_published_live_smoke.sh` | Published-digest wrapper over the authoritative provider-custody workflows | Authoritative published acceptance wrapper | Inherits real provider custody from the Cloudflare and rotation scenarios. It must not add a second verifier source. |
| `cpk_local_gateway_private_probe_smoke.sh` | Direct host -> gateway image | Explicit diagnostic | Keep only as a low-level process/network diagnostic. Inject an ephemeral public verifier environment and signed requests explicitly, or narrow it to liveness/target behavior. It cannot count as cpk-server authorization acceptance. |
| `cpk_local_gateway_structural_grant_image_smoke.sh` and `cpk_local_gateway_structural_grant_check.py` | Direct code/image boundary | Explicit diagnostic | Continue generating an ephemeral key entirely inside the diagnostic. This proves grant/verifier image compatibility, not admitted key lifecycle or graph realization. |
| `products/cpk_local_gateway/tests/test_cpk_local_gateway_product.py` process tests | In-process gateway unit tests | Explicit diagnostic/unit evidence | Construct verifier objects or explicit ephemeral process environment in the test. Descriptor assertions must instead prove reserved verifier names are absent. |
| `cpk_cloudflare_two_gateway_smoke.py` | Host-side Docker and Cloudflare provisioning | Obsolete; remove | It explicitly predates cpk-server acceptance and bypasses operations ownership. Current multi-workspace public ingress scenarios supersede its behavioral proof. |

Static tests in `products/cpk_server/tests/test_image_bootstrap.py` follow the
classification of the scenario they inspect. They are structural evidence and
never replace the named live workflow.

## Migration Order

1. `#1383` gives every authoritative hosted workspace admitted delegation-key
   truth before desired-graph publication. It preserves durable-provider
   custody for the rotation and Cloudflare custody paths and labels the other
   signer composition as ephemeral source-test infrastructure.
2. `#1384` removes the reserved verifier names from the canonical descriptor,
   adds stable bindings to every authoritative gateway graph, removes
   graph-construction verifier lookups, updates diagnostics, regenerates
   catalogue surfaces, and removes the obsolete host-side Cloudflare smoke.
3. `#1272` reruns the provider-generated A -> A+B -> B source-live program as a
   thin cpk-server client and treats verifier readback only as corroboration.

## Invariants

- Product instantiation remains exact. No permissive descriptor keys,
  post-instantiation field stripping, or placeholder substitution is allowed.
- Operations owns admitted key lifecycle and realized projection truth.
- Core contains stable intent and pure projection language, not provider or
  product-specific behavior.
- Interpreters materialize public verifier environment but never receive
  private key bytes for gateway verification.
- cpk-server composes signer/provider dependencies and routes operations; it
  does not own key lifecycle policy.
- A direct diagnostic cannot become authoritative merely because it uses a
  published image or reaches a live target.
- Private key bytes never enter graph truth, runtime request descriptors,
  operations persistence, events, observations, logs, errors, or public
  responses.

