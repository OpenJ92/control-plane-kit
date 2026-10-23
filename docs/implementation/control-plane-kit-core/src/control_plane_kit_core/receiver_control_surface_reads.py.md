Source: [receiver_control_surface_reads.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/receiver_control_surface_reads.py).
Maintain this companion with source and imported contract changes.

V2 surface-description requests and workload grants carry the complete logical
receiver target and a separate authority context. The target owns runtime;
context participates in the complete canonical request digest. Existing
declarations V1/V2, capabilities/status kinds, purposes and historical codecs
retain their meanings. A surface description does not read variable state.

For constructed values, the composition law is:

```python
request_b = replace(request_a, authority_context=context_b)
assert request_b.target == request_a.target
assert request_b.canonical_digest() != request_a.canonical_digest()
```

The verifier requires independent expected target, declaration, admitted kind,
issuer, key, audience and time. Self-consistent incoming claims cannot choose
these inputs. Context congruence is correlation, not current controller permission;
there is no local graph-currentness store. Signature verification and transport
admission remain external.

Structural caller validation raises a fixed detached family error. Valid but
different declarations/kinds reach semantic refusal codes. Incoming wrong nominal
type refuses first; absent purpose yields GRANT_INVALID, present wrong purpose
PURPOSE_MISMATCH, other malformed grant fields GRANT_INVALID. Validated claims
then check issuer/key/audience/time, request-local scope/declaration/kind, and
grant-request scope/context/kind/declaration/request. Scope is ordered workspace,
runtime, node, socket, receiver. Both declaration versions are admissible.

Closed dict and strict canonical-byte codecs reconstruct nominal values and
reject unknown/missing fields, legacy or foreign profiles, sibling runtime,
duplicates, nonfinite numbers, invalid UTF8, recursion and noncanonical JSON.
Raw input is bounded before parsing. No input material is retained in public
errors or value repr. No permissive legacy decoder or fabricated old target is
used, and there is no coupling to health-family private helpers.

Historical owners retain the request951/grant1984 byte caps and300-second
lifetime. Intervals use safe integers and half-open expiry. The larger shape
does not fit every per-field maximum simultaneously; tests independently build
reachable maxima and first overflow. Narrow family-local helpers support the
V3 result companion without a general schema framework.

No signatures, routes, gateway surface transit, credentials, persistence,
provider or live effects are added. Whole #1881 acceptance still gates adopters.
