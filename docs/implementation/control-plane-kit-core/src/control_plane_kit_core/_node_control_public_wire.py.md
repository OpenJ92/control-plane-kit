Source: [control-plane-kit-core/src/control_plane_kit_core/_node_control_public_wire.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/_node_control_public_wire.py).
Maintain this document alongside its source file. When admissibility, canonicalization, dependency or error contracts change, verify and update this companion in the same change.

This private 163-line module owns shared representation rules for public
node-control values. It returns one of three violation values, or `None`, and
delegates canonical JSON serialization to `rfc8785.dumps`. It does not own a
command language, descriptor codec, authorization policy, signature verifier or
external effect. Its three language consumers retain their own contracts and
translate these shared results into their own errors.

## Shape and public-material rules

Identifiers are strings of 1–128 characters, beginning with an ASCII letter or
digit and continuing with letters, digits, dot, underscore or hyphen. References
allow 1–256 characters with colon and slash added after the initial character.
Both use `isinstance(value, str)`, so this is not an exact-built-in-string
boundary. A shape violation takes precedence over public-material classification:
`http://router` is an invalid identifier shape but an endpoint-envelope reference.
Digests require exactly 64 lowercase hexadecimal characters; this only checks
representation, not correspondence to content. Epoch values require exact `int`
type in `0..2**53-1`, excluding booleans; this does not check current time or grant
validity.

`public_material_violation(text)` examines the literal text and one ASCII percent
projection. `%xx` escapes decode only when their byte value is at most `0x7f`;
non-ASCII escapes remain unchanged, and decoding is not recursive. Credential
classification across both projections takes precedence over endpoint
classification across both. The classifier itself supplies no type, length,
emptiness or aggregate-size gate; callers own those boundaries.

Credential patterns recognize selected authorization/bearer envelopes,
credential/password/secret/signature/token assignments, private-key armor and
compact `sk-`/`sg.` forms. Endpoint patterns recognize scheme URLs,
protocol-relative forms, syntactically matching host/port pairs with ports
1–65535, and separated IP-address or localhost atoms. IP recognition delegates
to `ipaddress.ip_address`; token processing strips brackets and trailing dots.
These are finite lexical rejection rules, not discovery of every credential or
network destination. Ordinary names such as `router.internal` and `secret-agent`
are admitted. The fixture also admits `router.internal:0`,
`router.internal:65536` and `localhost:70000`; admission means these rules found
no prohibited envelope, not that the value denotes a valid or safe endpoint.

## Canonicalization and composition

`canonical_json_bytes` returns the dependency's encoded bytes. It catches only
`rfc8785.CanonicalizationError` and raises a fixed
`NodeControlCanonicalDomainError("outside the canonical JSON domain")` after
leaving the handler. The selected NaN test checks bounded outward text and no
cause, context or instance attributes. Other exception classes are not normalized
here; the helper adds no depth, byte, work or time limit. Dependency internals
were not audited for this note.

Selected actual consumers demonstrate the division of ownership:

- [node_control.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py)
  uses the classifiers in its field guards and maps canonical-domain failure to
  `NodeControlContractError`. Its public-text guard checks a caller-supplied
  length bound before classification; its descriptor helpers check encoded size.
- [node_control_surface_reads.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_reads.py)
  maps the same shape/material violations to `NodeControlSurfaceReadContractError`
  and supplies its own canonical byte bound.
- [node_control_surface_read_results.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_read_results.py)
  also uses shared canonicalization while retaining its aggregate-size check and
  surface-read error vocabulary.

The [ownership test companion](../../tests/test_node_control_public_wire_ownership.py.md)
records the exact fixture and static-check limits. In particular, exclusion of
the module name from `core.__all__` is not an import-access restriction, and the
finite import scan is not a proof about dynamic or transitive execution.

Review depth: full owner and ownership test, both JSON fixture inputs, and the
selected consumer guard/canonicalization paths above. The consumers and
third-party package were not reviewed in full for this packet. No imports,
tests, canonicalizer execution or provider actions were performed. Changing this
module can change admissibility and canonical wire bytes across several
languages; preserve each consumer's semantic and authority boundary when doing so.
