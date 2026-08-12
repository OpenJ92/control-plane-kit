# Node-Control Canonical Wire

Status: public contract for `jcs-rfc8785.v1`.

Issues: OpenJ92/control-plane-kit#1452, #1547, and #1548.

## Purpose

Workload node-control grants bind the digest of one exact semantic request.
Python, Java, C++, and future SDKs must therefore derive identical bytes from
the same request value.

Surface discovery additionally binds one exact static declaration identity and
one exact capabilities or status request digest. Those values use the same
cross-language canonicalization law without sharing the command request type.

## Canonicalization Identity

Every `NodeControlCommandRequest` descriptor carries:

```json
{"canonicalization": "jcs-rfc8785.v1"}
```

That identity means:

```text
request descriptor
  -> RFC 8785 JSON Canonicalization Scheme
    -> UTF-8 canonical bytes
      -> SHA-256 lowercase hexadecimal request digest
```

The canonicalization identity is part of the descriptor being canonicalized.
Missing or unknown identities fail strict decoding.

## Numeric Domain

Node-control JSON numbers use the RFC 8785/I-JSON interoperable domain:

- integers are between `-(2**53 - 1)` and `2**53 - 1` inclusive;
- floating-point values are finite IEEE-754 binary64 values;
- negative zero is rejected before canonicalization;
- NaN, infinities, oversized integers, and values outside this domain fail as
  `NodeControlContractError`.

Versions and epoch seconds are nonnegative safe integers. Weighted-routing
integer inputs are safe-bounded before conversion to binary64, so conversion
cannot leak an overflow exception or silently lose integer precision.

## Size Law

Public node-control payload and result bounds are measured from the same RFC
8785 UTF-8 representation used by request digests. Size validation and grant
binding therefore cannot disagree because they used different serializers.

Surface-read results have distinct reachable global ceilings:

```text
capabilities result  16,902 bytes
status result         4,811 bytes
```

The capabilities ceiling contains the exact 16,453-byte maximum declaration.
The status ceiling is the reachable maximum under the accepted 16,384-byte
surface, variable-count, identifier, and descriptor laws; it is not the loose
and unreachable product of independent name-count and name-length limits.
Result codecs also derive a smaller ceiling from the exact expected request and
declaration before interpreting nested values. Raw HTTP admission before JSON
parsing remains an SDK/adapter responsibility.

## Golden Vectors

The language-neutral fixture is:

```text
tests/fixtures/node_control_canonical_wire_v1.json
```

It contains request descriptors, exact canonical UTF-8 text and hexadecimal
bytes, SHA-256 digests, and selected RFC 8785 Appendix B number vectors. Python
tests consume it in this distribution. Java and C++ SDKs must consume the same
fixture before claiming compatibility; they must not regenerate expected bytes
from their own serializer and call that interoperability evidence.

The surface-read fixture is:

```text
tests/fixtures/node_control_surface_read_canonical_wire_v1.json
```

It pins the complete
`workload-node-control-surface-declaration.v1` envelope, its exact canonical
UTF-8 text and SHA-256 identity, and one complete
`workload-node-control-surface-read-request.v1` descriptor with exact canonical
UTF-8 text and SHA-256 request digest. It also pins the common
`workload-node-control-surface-read-result.v1` profile, an exact capability
result, the separate status declaration and request preimages, and exact
`none|partial|complete` status vectors. SDK implementations must consume these
vectors before claiming surface-read compatibility.

## Scope

This contract covers workload node-control command requests, static surface
declarations, surface-read requests, and their request-bound capability/status
results. Status coverage proves only that canonical installed names form the
claimed subset of the expected declaration. It does not prove that those names
came from a maintained live registry. This contract does not change
gateway-probe digests or another package's existing canonical material. It does
not sign requests, verify signatures, store replay state, log canonical bytes,
inspect a registry, or perform network or runtime effects.
