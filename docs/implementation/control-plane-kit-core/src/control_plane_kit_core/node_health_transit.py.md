Source: [node_health_transit.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_health_transit.py).
Maintain this companion with source and imported contract changes.

# Exact health gateway transit

This owner defines unsigned gateway authority over the existing
NodeHealthReadRequest and its nominal digest. It adds no second health request,
result or opaque authorization-context digest. The profile is
gateway-node-health-read-transit-grant.v1 and purpose is the separate
GATEWAY_NODE_HEALTH_READ_TRANSIT. Old variable transit (including READ_STATE
with command_codec=None), static reads, variable workload, native probes and
health workload grants retain separate authority languages.

The exact18 wire fields bind issuer/key, attempt, gateway, target, runtime,
health kind, declaration identity, request ID/digest, validity and JTI.
Workspace/revision are owned by target, with no redundant top-level fields.
Audience is derived as gateway:{target.workspace_id}:{gateway_node_id}, not
caller-selected. Identifier grammar excludes colon, so fields are unambiguous.
The codec rejects a supplied audience that disagrees with those coordinates.

The verifier requires independent expected gateway, target, runtime, V2
surface declaration, actual requested health kind and admitted attempt, plus
issuer/key/time. Local gateway/runtime come from composition; target/declaration
come from admitted mapping. Actual kind is an action comparison, not permission.
Do not reconstruct these expected values from a grant. Neither nominal equality
nor same-runtime membership proves current graph membership or authorization.

Rejection order: grant type/purpose/issuer/key/time/attempt/gateway; request
versus local workspace/revision/node/socket/runtime/declaration/kind; then grant
versus exact request workspace/revision/node/socket/runtime/kind/declaration
and request ID/digest. Derived audience requires no separate unreachable
verifier code: successful workspace/gateway comparisons prove it. Malformed
expected arguments raise bounded contract errors, not raw submitted values.

Safe integer epochs exclude bool. issued_at <= not_before < expires_at and
expires_at - issued_at <=300; not_before inclusive and expires_at exclusive.
This is not immediate revocation. attempt_id correlates to a particular
independently admitted execution attempt; it is not request identity, evidence
of current approval, freshness or Core attempt state.

Canonical audience/grant maxima are265/2423 bytes. The fixed18-field descriptor
counts issuer256, graph/runtime/gateway and action identifiers128, audience265,
two digests64, readiness9, valid16-digit epochs and fixed JSON/profile overhead.
Constructors and whole-envelope bounds apply before nested interpretation.
Mapping decoding delegates its request fields to the accepted health request
codec. Raw-byte decoding rejects duplicate keys, malformed UTF-8/JSON,
nonfinite values, noncanonical aliases and bounded recursion failure.
Grant digest is SHA-256 of complete RFC8785 bytes, distinct from the workload
request digest. Errors have no cause/context or submitted material; repr hides
issuer/key/request/attempt/JTI and request digest.

The fixed public vector and test_node_health_transit laws cover every binding,
independent locality, time/field/aggregate limits, canonical raw admission,
family substitution and preserved old READ_STATE behavior. They prove pure
contracts only, not signatures, network suppression or callback suppression.

Operations #1821 / Interpreters #149 must explicitly adopt both health purposes,
current schema validation, appropriate SecretUseIntents and retained key/grant
resolution with their Core/Secrets owners. The existing signer's binary
old-transit-versus-workload fallback is not a generic health resolver; do not
pass new purposes through it or alias old keys/intents. This enum provisions
nothing. Actual crypto, HTTP, replay and secret materialization stay downstream.

Operations allocates fresh request IDs/digests for new observations and
correlates retries/attempts/deadlines. Changing gateway, attempt or signature
alone does not create a new observation. Materialization and relay must bind
both credentials to the same exact request before relay; the public grant
contains neither credential. Servers #180 must deny gateway/structural-pairing
failures with zero outbound target HTTP. A paired credential with bad workload
signature may already reach the target: SDK #20 then guarantees zero protected
health callback invocation. Gateway need not acquire workload signature
verification authority. Interpreters #147/#149 preserve pairing/materialization
and these distinct denial boundaries. No provider, schema or SDK code changes
belong to this Core slice.
