# Node-Control Public Material

Status: public contract for `cpk.node-control.public-material.v1`.

Issue: OpenJ92/control-plane-kit#1468.

## Boundary

Node-control graph references, request and replay identities, authority
references, scalar/map strings, and descriptions use ordinary JSON strings.
Their enclosing nominal value or descriptor field gives them meaning. No
wrapper or self-attested provenance field is added to the wire.

Core enforces bounded field syntax and the exact lexical exclusions below. An
authenticated producer remains responsible for proving that admitted material
is public and that graph references came from the admitted graph. Bytes alone
cannot prove either semantic fact.

## Field Classification

| Material | Classification | Core enforcement | Producer/consumer obligation |
| --- | --- | --- | --- |
| operation, kind, status, evidence, codec, route, capability | closed code | exact membership | none |
| workspace, revision, node, socket, variable, target | nominal bounded graph reference | role, identifier grammar, lexical exclusions | derive from and verify against the admitted graph |
| request, idempotency, key, JTI, result identities | bounded identity | identifier grammar, lexical exclusions | assign stable public identities |
| issuer, audience, verifier expectations | bounded authority reference | reference grammar, lexical exclusions | establish trusted authority meaning |
| scalar/map strings and description | producer-attested public material | canonical type/size domain, lexical exclusions | attest semantic publicness |
| request digest | digest | 64 lowercase hexadecimal characters | establish request binding |
| versions, epochs, weights | bounded numeric material | canonical numeric domain | establish semantic ownership |

## Projection

Each string is inspected literally and after one ASCII percent projection.
The projection replaces every valid `%HH` escape whose octet is in `00..7f`
with that ASCII code point. It does not recursively decode, decode non-ASCII
UTF-8, parse a URL, resolve DNS, decode base64, or inspect product vocabulary.

## Credential Envelopes

The following case-insensitive lexical envelopes are rejected:

- an `Authorization:` header or standalone `Bearer` scheme carrying nonempty
  material, including horizontal spacing variants;
- a `credential`, `password`, `secret`, `signature`, or `token` assignment
  carrying a nonempty value;
- PEM private-key BEGIN armor;
- compact material beginning with `sk-` or `sg.` at a lexical boundary.

Words and phrases such as `secret-agent`, `bearer-capacity`, `token-count`,
`authorization policy`, and `private key rotation` are not credential
envelopes.

## Endpoint Envelopes

The following lexical envelopes are rejected:

- a URI scheme followed by `://`;
- a protocol-relative `//authority`;
- a hostname or bracketed IP authority with a decimal port in `1..65535`;
- IPv4 or IPv6 literals;
- `localhost`, `localhost.`, or a `*.localhost` name, with an optional valid
  port.

A bare DNS-looking value such as `router.internal` is not rejected. It remains
semantically ambiguous until an authenticated producer derives it from graph
truth and the consuming boundary verifies that provenance.

## Errors And Representations

Contract failures identify a stable field and law. They do not include rejected
values, producer map keys, unknown attacker-controlled field names, underlying
parser exceptions, or provider diagnostics.

Python object representations omit all open text/reference material as a
defense-in-depth diagnostic rule. Descriptors still carry admitted public
material because descriptors are the canonical interoperable wire. Equality,
ordering, descriptor round trips, RFC 8785 bytes, and request digests are not
changed by representation redaction.

## Vectors

The language-neutral fixture is:

```text
tests/fixtures/node_control_public_material_v1.json
```

Python tests consume it here. Future Java and C++ SDKs must consume the same
accepted and rejected vectors before claiming this public-material contract.
