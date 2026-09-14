Source: [control-plane-kit-core/tests/test_probe_intents.py](../../../../control-plane-kit-core/tests/test_probe_intents.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Distinct observation layers and endpoint construction

The [probe owner](../src/control_plane_kit_core/probe_intents.py.md) is exercised
with pure HTTP/TCP endpoint fixtures. Tests distinguish process, transport,
health and readiness objects and outcomes, preserve private/host/public context,
derive health intent from matching HTTP material, and return typed failures for
missing health declarations and protocol mismatch.

Literal endpoint cases reject credentials, query strings, fragments and
selected protocol/scheme mismatches. The protocol loop covers every allowed
transport/application combination and constructs endpoints for its advertised
schemes. It does not contact an endpoint or establish an egress policy.

Secret-reference descriptor tests require the opaque reference to remain,
without resolving its value. The test title mentioning redaction uses this
selected reference fixture and a token-string absence assertion; it is not a
universal secret detector or a promise that literal endpoint addresses are
hidden. Policy checks cover a valid configuration, HTTP status normalization
and selected attempt/response bounds, not all timeout values or NaN.

Observation tests accept representative layer-coherent outcomes and reject
selected cross-layer claims and invalid context presence. They do not exhaust
every kind/outcome pair, overridden intent kinds, all malformed endpoints or
all size/encoding limits. Full 358-line test file and full 653-line owner read
for this note. No provider observation, freshness, readiness aggregation,
credential use or executable validation was performed.
