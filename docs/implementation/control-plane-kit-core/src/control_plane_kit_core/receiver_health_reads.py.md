Source: [receiver_health_reads.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/receiver_health_reads.py).
Maintain this companion with source and imported contract changes.

The explicit V2 request and workload grant use the complete logical receiver
target plus a separate authority context. Runtime lives only inside target.
The context is included in the complete canonical request digest and copied
exactly into grant claims; it is not installed current-graph state. Historical
health values, profiles, codecs and golden bytes remain unchanged.

For already constructed request/context values, the tested composition is:

```python
request_b = replace(request_a, authority_context=context_b)
assert request_b.target == request_a.target
assert request_b.canonical_digest() != request_a.canonical_digest()
```

`verify_workload_receiver_health_read_grant` receives independent expected
target, declaration, route kind, issuer, key, audience and time. The target
includes workspace/runtime/node/socket/receiver. Congruent incoming claims
cannot choose those local expectations. There is no expected local graph context:
grant/request context agreement establishes correlation, not current permission.
Signatures, actual route admission and durable authority remain external.

Caller inputs are structurally revalidated. Malformed values raise a fixed,
detached error; a valid but mismatched declaration or undeclared kind reaches
the ordered semantic refusal code. Incoming wrong nominal grant type refuses;
missing purpose returns GRANT_INVALID, present wrong purpose returns
PURPOSE_MISMATCH, and other malformed or missing grant fields return GRANT_INVALID.
Validated grants then check issuer/key/audience/time, request-local scope and
declaration/kind, grant-request scope, authority context, kind, declaration and
request ID/digest, in that order. New scope order is workspace/runtime/node/
socket/receiver. No historical revision field is reinterpreted.

Both codecs expose closed dict and strict canonical-byte methods. Raw bytes are
bounded before parsing; duplicates, nonfinite values, invalid UTF-8, noncanonical
formatting, unknown fields and foreign profiles refuse. Encode reconstructs
frozen nominal values. Public references and errors exclude private material.
The new nominal digest prevents accidental reuse of a historical request digest.

Existing semantic owners retain health kinds, declaration identities, purposes,
canonicalization, 300-second lifetime and aggregate caps: request 1083 and
workload grant 2107. The larger shape means not every per-field maximum fits
together. Independent target fixtures exercise reachable exact caps and first
overflow. Time is half-open at expiry and bounded by exact safe integers.

Narrow private health field/wire/local-binding helpers are shared only by the
successor health transit/result modules. This is not a general schema engine or
a legacy profile adapter. Tests preserve historical contracts and cover local
scope, A/B/C correlation, trust/time, forged input, precedence, bounds, purpose
separation and root exports. No network, credential, storage or provider effect
is introduced; parent #1881 still gates all downstream adoption.
