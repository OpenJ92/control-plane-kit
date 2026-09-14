Source: [control-plane-kit-core/src/control_plane_kit_core/node_control_surface_reads.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_reads.py).
Maintain this document alongside its source file. When declaration/request identities, grant claims, verification order, bounds or consumer obligations change, verify and update this companion in the same change.

This module defines the pure language for reading a workload's declared control
surface: a versioned declaration, an exact capabilities/status request, unsigned
read-grant claims and a claim comparator. A surface read is distinct from reading
or changing a control variable. The module does not parse HTTP, sign or verify
signatures, retain replay state, inspect a registry or perform workload I/O.

```text
WorkloadNodeControlSurfaceDescriptor
  -> versioned declaration -> canonical declaration identity
    -> target + kind + declaration identity + request ID
      -> exact read request -> canonical request digest

unsigned read grant + request + expected issuer/key/audience/time
  -> accepted | first bounded mismatch
```

## Identity domains and public shapes

WorkloadNodeControlSurfaceDeclaration wraps the existing static surface with its
versioned profile. Its two-field descriptor contains profile and surface; the
profile therefore participates in the canonical SHA-256 identity. There is no
separate canonicalization field inside that declaration envelope. Its
WorkloadNodeControlSurfaceDeclarationIdentity is a distinct frozen nominal value,
validated as 64 lowercase hex characters. Constructing that identity from text
does not demonstrate possession of the declaration preimage.

NodeControlSurfaceReadRequest is a frozen ordered value containing target, kind,
declaration identity, request ID, profile and canonicalization. Kind is the closed
capabilities/status enum. Request ID uses the bounded identifier law; request
profile must be its nominal enum and canonicalization the exact JCS V1 member.
The complete six-field descriptor is hashed into NodeControlSurfaceReadRequestDigest,
a separate nominal identity type. There is no variable name, payload, operation,
expected version or idempotency key in this request language.

The request constructor has no declaration body or graph store to consult. It
checks nominal shape and size, not that the identity resolves, the target exists,
or the declaration belongs to the target. The
[result-language consumer](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_read_results.py)
later compares the request's identity with the actual expected declaration and
checks its provider socket. That still requires trustworthy graph/declaration
selection by the calling service.

## Unsigned read-grant claims

DelegatedWorkloadNodeControlSurfaceReadGrant is frozen and ordered, with fifteen
descriptor fields: profile/canonicalization/purpose, issuer/key/audience, target,
read kind, declaration identity, request ID/digest, issued-at/not-before/expiry
and JTI. It has neither the declaration body nor the request preimage. Nominal
identity/digest checks do not by themselves establish a matching request.

Grant profile must be its enum, canonicalization the exact JCS V1 member, and
purpose a recognized DelegationKeyPurpose. A recognized purpose for another
authority family remains constructible; the comparator rejects it for surface
reads. The constructor requires the right target, kind and identity/digest types.
Audience is supplied public reference text, not derived from target by this
module. It may be well-formed yet disagree with a workload's expected audience.

The shared [public-wire owner](../../../../../control-plane-kit-core/src/control_plane_kit_core/_node_control_public_wire.py)
provides identifier, reference, digest, epoch and finite credential/endpoint laws.
Request ID, key ID and JTI use the 128-character identifier grammar; issuer and
audience use the 256-character reference grammar. Graph references already carry
their own role and identifier constraints. Epochs must be exact integers in
0..2**53-1, excluding bool. The grant requires issued-at <= not-before < expiry
and expiry minus issued-at <= 300 seconds. These conditions establish local time
shape, not clock trust, revocation or replay protection.

## Canonical bounds and mapping codecs

| Public object | Exact top-level keys | Canonical byte limit |
| --- | --- | --- |
| Declaration | profile, surface | 16,453 |
| Request | profile, canonicalization, target, kind, declaration_identity, request_id | 951 |
| Grant | fifteen fields described above | 1,984 |

The declaration limit is the underlying 16,384-byte surface cap plus 69 bytes of
envelope. Constructors enforce aggregate canonical bounds. Declaration and
request expose canonical bytes and identity/digest methods. The grant exposes a
descriptor; its constructor canonicalizes for admission, but it has no public
canonical-bytes or grant-digest method here.

Each codec checks nominal input on encode. Decode accepts Mapping/string-key
shape, canonicalizes under the relevant aggregate cap, requires exact keys and
then reconstructs the typed value. Targets have exactly four keys and are rebuilt
with WORKSPACE, GRAPH_REVISION, NODE and PROVIDER_SOCKET references. Declaration
decode delegates the surface body to the existing surface codec.

Size/canonical-domain admission precedes exact-key and nested-type checks. These
are mapping codecs, not raw parsers: duplicate JSON keys, original whitespace and
raw transport byte limits must be handled before a mapping loses that information.
Canonicalized output limits do not bound every arbitrary Mapping callback or
serializer recursion path. Selected canonical-domain and nested surface/target
errors are translated after their handlers into categorical surface-read errors;
other exceptions are not universally caught.

Request repr suppresses request ID. Grant repr suppresses issuer, key ID,
audience, request ID and JTI; nested graph references suppress their text. Digest
and identity values are still ordinary public values, and descriptors disclose
their complete public claims. These choices do not constitute a universal secret
filter or make descriptors safe for every presentation surface.

## Ordered unsigned verification

verify_workload_node_control_surface_read_grant first validates the request and
caller-supplied expected issuer, key ID, audience and current epoch. Malformed
expectations raise a contract error before a grant verdict. With valid inputs,
the first failed check determines one of thirteen codes:

```text
grant type -> surface-read purpose -> issuer -> key -> audience -> time
  -> workspace -> revision -> node -> socket -> read kind
    -> declaration identity -> request ID / canonical request digest
```

Time is half-open: not-before <= now < expiry. The final two comparisons share
REQUEST_MISMATCH. WorkloadNodeControlSurfaceReadGrantVerificationResult requires
an exact bool: accepted has no code; rejected has a bounded enum code. Its
descriptor contains only accepted and code, with no provider message.

The comparator verifies equality of unsigned claims, not signatures or the origin
of the expected values. It neither derives expected audience from target nor
checks active keys, JTI replay, current graph revision, actual declaration contents
or registry state. The caller owns trusted expectations and authentication; the
result-language owner separately binds a supplied declaration. Agreement is not
permission to invoke a command or proof that a surface read occurred.

## Governing evidence and maintenance

The [nine-test authority suite](../../../../../control-plane-kit-core/tests/test_node_control_surface_read_authority.py)
uses fixed declaration/request vectors, seven request-digest changes, reachable
declaration/request/grant maxima and one-byte oversize mapping witnesses. It
checks thirteen ordered mismatch cases and exact temporal endpoints, selected
foreign-grant rejection, public-material fixtures, missing/None fields, selected
identity/string/time failures and finite diagnostic/repr guards. Several cases
deliberately combine mismatches to test precedence; the final request case changes
both ID and digest. These examples do not exhaust invalid expected arguments or
cryptographic/replay behavior.

The route check fixes four method/path/scope triples, with surface capabilities
and status assigned their own read scope. Thirteen selected root exports must
exist. The import guard examines only from-imports against five prefixes; it is
not a complete dependency graph or a guard on every import form. Protocol data
and these tests do not instantiate authenticated route handlers.

The [wire contract](../../../../../control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md)
requires fixed vectors for compatibility and keeps registry provenance outside
pure data. Authoring read the full 790-line owner and full 721-line test/helpers,
retained canonical/public-material fixtures, selected shared-wire laws and the
actual result consumer's context checks. Only this owner receives coverage here.
No imports, executable tests, hashing tools, signing, persistence, HTTP or provider
effects were run. Changes must preserve identity preimages, bounded error/order
contracts and the downstream distinction between claims, authority and observation.
