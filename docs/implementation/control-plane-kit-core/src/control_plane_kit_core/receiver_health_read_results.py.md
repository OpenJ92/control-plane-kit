Source: [receiver_health_read_results.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/receiver_health_read_results.py).
Maintain this companion with source and imported contract changes.

`ReceiverHealthReadResult(request, declaration, outcome)` uses the explicit
`workload-node-health-read-result.v2` profile and existing health outcomes.
Its codec requires an independently supplied exact successor request and
health-bearing declaration. Result context validation is stronger than the
verifier's initial structural check: request identity/socket/kind must match
the declaration before a result can be constructed or decoded.

The wire keeps only profile, canonicalization, request ID/digest, declaration
identity, health kind and outcome. Target/runtime/authority context are bound
through the complete request digest, not repeated as mutable result facts.
Every correlation field is derived from the receiving context. Old results
cannot satisfy a fresh request ID, changed scope/receiver, changed authority
context, kind or declaration. Matching correlation does not establish durable
freshness or successful physical installation.

Dict and canonical-byte codecs reject extra/missing fields, old/foreign profiles,
noncanonical or malformed JSON and unknown outcomes. Encode and decode revalidate
both nominal members and the codec's frozen context. The historical 446-byte cap
is retained and tightened by actual request-ID and kind lengths. Tests reach
446 exactly with the longest outcome and reject 447, as well as wrong context,
forged members and old/new profile substitution in both directions.

No timeout, authentication, transport or exception-detail outcome is added.
There is no I/O, persistence, signing, event emission or cleanup behavior.
