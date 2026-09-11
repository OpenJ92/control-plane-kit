Source: [control-plane-kit-core/src/control_plane_kit_core/delegation_authority.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/delegation_authority.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Authored bindings and realized public verifier material

`DelegationAuthorityBinding` records a delegate node, a closed key purpose and
an issuer. Its identity is node plus purpose; the issuer is additional matched
content. Reference strings use a 256-character ASCII grammar allowing mixed
case, digits and selected punctuation. This is authored intent, not registration,
possession of signing material or permission to issue credentials.

`DelegationVerifierProjection` adds audience, projection ID and one to sixteen
typed Ed25519-labelled public keys. Keys are sorted by ID and duplicate IDs are
rejected. The imported [key owner](./delegation_keys.py.md) validates PEM text
shape and fingerprints normalized text; this constructor does not parse or
cryptographically validate an Ed25519 key. Audience and projection ID are
caller-supplied references, not values derived here from registry state or keys.

Descriptors deliberately include public PEM alongside each key's ID, algorithm
and fingerprint. Decoding requires exact key sets, reconstructs keys and rejects
fingerprint disagreement before normalizing the projection. This public-material
representation differs from the [graph-diff value](./topology/changes.py.md),
which includes key descriptors without PEM. Neither is an authority token or
evidence that a receiver has installed the material.

`public_environment()` builds six sorted `CPK_GATEWAY_PROBE_*` public bindings,
including a deterministic JSON key map and the `ed25519` verifier label. It uses
that gateway vocabulary even though the purpose enum is broader. The imported
[environment binding](./environment.py.md) enforces a 16-KiB UTF-8 limit per
value; projection construction does not precheck the combined key-map size, so
an admitted projection can fail when rendered to environment. No process
environment is mutated by this function.

`materialize_delegation_verifiers` requires a graph and typed projection tuple,
matches the exact authored binding identity set, rejects duplicate projections
and mismatched issuers, and resolves each delegate node. Reserved authored
public-environment names conflict with generated material. An identical attached
projection is preserved; a different existing projection is rejected. Since a
node has one projection slot, multiple distinct purposes on the same node do
not become independent verifier installations through this materializer.

The result is a graph value with attached projections; the authored input is
not mutated. With no bindings or projections it returns the same graph object.
It does not call `public_environment()`, install keys, sign/verify a request,
write history or reconcile a running node. The selected graph codec separately
requires projections to match an authored binding and issuer. Provider and
durable registration/adoption checks belong to their consumers.

Full 320-line owner and full 331-line
[governing test](../../tests/test_delegation_authority_projection.py.md) read,
with previously read public-key/graph/planning owners and selected actual
environment, graph identity/reference and diff-projection implementations.
Exact-key decoding is not a general redaction or safe-error boundary; wrapped
errors retain causes. No executable validation, credentials, runtime effects or
key rotation were performed.
