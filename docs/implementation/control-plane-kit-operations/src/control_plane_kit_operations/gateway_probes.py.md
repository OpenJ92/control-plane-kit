Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_probes.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_probes.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner turns a trusted gateway-probe command into an intended receipt,
an external dispatch and a terminal receipt. GatewayProbeAttempt records the
workspace/request/actor, graph and gateway coordinates, access path, target and
request digest, unsigned grant metadata, status, times, result code and bounded
evidence. Its statuses are intended, succeeded, rejected and failed. A receipt
describes one attempt; it is neither current health nor evidence that a gateway
has installed the corresponding verification configuration.

The command path requires GATEWAY_PROBE_USE and DELEGATION_KEY_USE before even
looking up a replay. Within a caller-created unit of work, it locks the
workspace/request identity and compares the stored intent fingerprint. The
fingerprint hashes workspace, expected graph, gateway node, access path and the
complete request descriptor. Actor/scopes are not fingerprint fields; request ID
is the separate workspace-scoped lookup coordinate. A matching receipt returns
unchanged, including its original attribution and any intended status, without
new clocks, IDs, secret authorization or dispatch. It does not revalidate graph,
key or provider freshness. A changed request or access path conflicts.

For a fresh request, the service locks the workspace, requires the exact current
graph pointer and graph ownership, decodes the gateway node, and selects active
registered products. The imported
[endpoint and target helpers](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_effects.py)
derive targets from graph edges whose providers share the gateway runtime;
they do not restrict the set to edges consumed by that gateway. HTTP-status and
Postgres-select-one must match their target type. Private control uses the
declared HTTP control socket and registered product port. Public control requires
one graph ingress targeting that node's control socket with HTTPS exposure.
These are graph-derived endpoint values, not observations from a live connection,
DNS lookup, ingress controller or health check.

An unambiguous active GATEWAY_PROBE signing key supplies issuer and key ID. The
[Core request and grant values](../../../../../control-plane-kit-core/src/control_plane_kit_core/gateway_delegation.py)
bind the exact request digest, workspace, probe operation ID, request ID, gateway,
audience, issued/expiry epochs and a new JTI. The default lifetime is 60 seconds;
Core limits a grant to 300 seconds. The intended attempt is committed before
secret-use authorization and dispatch, both outside this transaction. The
[signing-key lifecycle](delegation_signing_keys.py.md) remains a separate owner;
this service does not lock graph/key state through the external effect or perform
a second freshness check immediately before it.

Secret authorization uses the signing-key intent and deterministic correlation
for this probe. The returned SecretResolutionGrant must have the right type,
workspace, probe ID and permission for the reference/intent. This local check
does not compare every returned correlation, actor or operation field. The
transient GatewayProbeDispatch carries the unsigned grant, request, endpoint,
private reference, public key and resolution grant. Its own type checks and
key-ID equality are not a complete cross-field coherence proof. Actual secret
resolution, signing, transport and gateway JTI replay prevention belong to the
dispatcher/provider/gateway interpreters. Operations request deduplication does
not establish gateway token replay prevention.

SecretProviderRegistrationError folds to rejected with a fixed authorization
code; GatewayProbeError during authorization folds to a fixed invalid-grant code.
GatewayProbeDispatchError folds to failed with only its exception type in
evidence. Other exceptions, invalid dispatcher results, completion-clock failure
or persistence failure may escape after intent has committed. The attempt can
remain intended even if external work occurred. A matching command replay does
not resume that work. Completion uses a new unit of work and the
[probe store](postgres/gateway_probe_store.py.md); there is no cross-provider
transaction or automatic compensation in this owner.

GatewayProbeVerifierConfigurationService selects the unambiguous active signer
and that issuer's active/verify-only verification set. The configuration sorts
unique key IDs, accepts 1 through 16 public keys, and emits five public environment
bindings for Ed25519 verification, issuer, audience, node ID and compact key JSON.
Public PEM is intentionally exposed here. The supplied gateway node ID is a
configuration coordinate: this service checks workspace/key availability but
does not resolve that node in the graph or authorize a caller itself. See the
[gateway security projection](read_services/gateway_security.py.md) for the read
surface's separate behavior.

Attempt descriptors contain unsigned grant metadata and bounded evidence, not
the compact signed grant, signature or secret-resolution grant. BoundedEvidence
limits canonical JSON to 4096 bytes, depth 4, 32 fields/items per container and
512 characters per string value; its secret-shaped-key rejection is not a secret-value
scanner. Result codes and several local coordinate checks are only nonblank-text
checks. The attempt constructor checks terminal status against the conjunction
of completed_at and result_code presence; an intended value with only one of
those fields populated can pass this local condition. PostgreSQL enforces the
stronger completion shape. Canonical timestamp admission also belongs to the
store, rather than these local nonblank checks. Selected service exceptions retain
their causes, so this module does not promise universal exception redaction.

Read depth: full 657-line owner, full
[1,075-line command tests](../../tests/test_gateway_probes.py.md), full
[520-line paging tests](../../tests/test_gateway_probe_read_pages.py.md) and full
252-line store; selected Core request/grant, endpoint helpers, evidence, paging
and schema contracts, plus the previously read signing-key/UoW/temporal contracts.
This documentation changes no security or runtime behavior. No tests, probes,
database operations, credential reads or key generation were executed.
