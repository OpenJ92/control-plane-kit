Source: [control-plane-kit-core/tests/fixtures/node_control_canonical_wire_v1.json](../../../../../control-plane-kit-core/tests/fixtures/node_control_canonical_wire_v1.json).
Maintain this document alongside its source file. When canonical descriptors, bytes, digests, number vectors or consumer assumptions change, verify and update this companion in the same change.

This 151-line fixture pins selected wire examples for
`cpk.node-control.canonical-wire.v1`, with canonicalization `jcs-rfc8785.v1` and
consumer labels `python`, `java`, `cpp`. Those labels state intended consumers;
they do not record executions by three SDKs. Expected text, hexadecimal bytes
and SHA-256 strings are literals in this file, separate from the implementation
computations tested against them. Their independent provenance was not verified
in this documentation review.

## Represented values

Three request descriptors cover a read-state request with null command material,
a scalar replacement containing `1e20`, and weighted routing containing `1e-7`
and `1.0`. Each includes the canonicalization identity in the request itself.
The expected scalar output spells `1e20` as `100000000000000000000`; the weighted
output spells `1.0` as `1`. Each request has `canonical_utf8`,
`canonical_utf8_hex` and `sha256` expectations.

One unsigned workload-grant descriptor binds the weighted request's stored
digest and carries issuer/key/audience, target, command/request identities and
epoch fields. Its canonical text, hex and digest describe the whole grant
descriptor. These are synthetic public claims; no signature, admitted issuer,
current-time validity, replay record or provider observation accompanies them.

Five number vectors store binary64 bit patterns as hexadecimal alongside expected
JSON spellings: zero, the smallest positive subnormal, the largest finite value,
and the selected `1e23`/`1e21` cases. The test constructs values with big-endian
`struct.unpack(">d", ...)`, avoiding a decimal input spelling as the initial
number source. This is a finite sample, not the complete numeric domain or a
complete standards conformance suite.

## Actual consumption and limits

- [test_node_control_canonical_wire.py](../test_node_control_canonical_wire.py.md)
  asserts schema/consumer labels, decodes all three request mappings and compares
  exact canonical text, hex and computed digest with these literals. It embeds
  each number vector in a scalar request and checks an expected value substring.
  It does not consume the workload-grant section.
- [test_node_control_public_wire_ownership.py](../test_node_control_public_wire_ownership.py.md)
  passes request descriptors directly to the shared canonicalizer and compares
  UTF-8 bytes only, without checking this fixture's other sections or metadata.
- The selected golden-vector test in
  [test_node_control_graph_references.py](../../../../../control-plane-kit-core/tests/test_node_control_graph_references.py)
  adds request descriptor equality after nominal-reference decoding and compares
  canonical text/digest. Its helper asserts required types exist; it does not
  silently skip absent implementations.
- Selected cases in
  [test_node_control_workload_wire.py](../../../../../control-plane-kit-core/tests/test_node_control_workload_wire.py)
  compare the first grant against direct `rfc8785.dumps` and SHA-256 computations,
  check its stored request-digest relation to the weighted vector, and exercise
  request/grant strict raw-byte round trips. Another selected case round-trips
  the number vectors and rejects chosen ambiguous spellings. The fixture also
  seeds selected rejection cases across distinct request/grant contracts; those
  broader tests are not fully reviewed by this companion.

Actual request/grant codecs in
[node_control.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py)
rebuild typed values from mappings. Raw decoders separately bound bytes before
parsing, reject duplicate keys, and compare canonical re-encoding with the input.
The numeric parser observes integer tokens outside the safe range as floats;
exact-integer fields reject those, while accepted scalar values must still
reproduce canonical bytes. Direct Python integer construction has a different
admission path. The [canonical-wire contract](../../../../../control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md)
explains the intended interoperability boundary. Regenerating expectations from
one implementation alone would not establish another implementation's agreement.

Review depth: full fixture and canonical-wire test; retained full shared
canonicalizer and request/grant construction paths, actual grant/raw/numeric
decoder paths and selected fixture consumers above. No whole large-owner,
third-party canonicalizer or non-Python SDK audit is claimed. No imports, tests,
hash recomputation, database or provider execution ran for this note. Fixture
agreement establishes selected representation evidence, not execution authority.
