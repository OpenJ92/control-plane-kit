Source: [control-plane-kit-core/tests/test_node_control_workload_wire.py](../../../../control-plane-kit-core/tests/test_node_control_workload_wire.py).
Maintain this document alongside its source file. When byte bounds, canonical decoding, fixture construction, authority separation or evidence limits change, verify and update this companion in the same change.

This 544-line suite has eight tests for workload node-control wire contracts.
Helpers construct nominal targets, scalar requests and unsigned grants. Two
fixtures supply command/workload and transit examples. The tests call codecs and
constructors, not signature verifiers, dispatchers, stores or providers.

## Fixed bytes and reachable bounds

The grant-vector test uses the first workload grant in the
[canonical-wire fixture](fixtures/node_control_canonical_wire_v1.json.md). It
compares direct `rfc8785.dumps` output, fixed UTF-8/hex expectations and a direct
SHA-256 computation against the fixture, checks its request-digest field equals
the weighted-request vector's stored digest, then compares the decoded grant's
bytes and nominal digest with those expectations. It also checks the 2,111-byte
constant and root digest-type identity. Direct dependency/hash computations
are separate test paths, not evidence of another language implementation or
independently verified fixture provenance.

The maximum test builds a grant with maximum-length identifiers/references,
the longest selected command codec and large valid epochs. Its encoded length
must equal 2,111 and direct dependency serialization must agree. It then exceeds
individual identity/reference/digest/variable/target and epoch bounds through
mapping decode. This is a concrete reachable-size witness plus selected
constituent failures, not an exhaustive grammar/type matrix. The helper's
default request is overridden with synthetic grant claims and a fabricated
digest; this tests grant representation, not correspondence to an authenticated
request. Actual constructor bounds and aggregate checking remain in
[node_control.py](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py).

## Strict raw decoding

The round-trip test decodes all three stored request byte strings and the first
workload-grant byte string, re-encodes them exactly and checks their fixed
digests. A separate numeric test round-trips five binary64 vectors through
scalar requests and rejects six chosen changes: a rounded integer-looking
value, an alternate exponent spelling, negative zero, overflow, an unsafe
precondition integer and `1.0` replacing canonical `1`.

Actual request/workload raw decoders require exact bytes and impose their
16,384/2,111-byte limits before parsing. Their shared parser rejects duplicate
keys and nonstandard constants. Integer tokens outside the safe range are
observed as floats; field validation and exact canonical re-encoding then decide
admission. A canonical spelling for `1e20` can therefore round-trip without
admitting the same oversized value as an exact Python integer or epoch.

The malformed-input matrix has eighteen selected cases across the two decoders:
oversized input, wrong input types, invalid UTF-8/JSON/root kinds/constants,
duplicate keys at request and nested-grant levels, whitespace/trailing content,
missing or unknown fields and reordered request keys. Each requires the contract
error, message length at most 128, selected fragments absent from str/repr, and
no cause/context. Canaries are not present in every candidate; the
`http://attacker` fragment is absent from these constructed inputs altogether.
These are selected diagnostic assertions, not universal sanitization evidence.
Three further cases replace each raw grant epoch independently with an unsafe
integer token, assert the replacement changed bytes, and check bounded errors
with no cause/context.

## Distinct contracts and runtime state

One fixture value each represents a command request, workload command grant,
gateway transit grant, workload surface-read grant and gateway probe grant.
For every ordered pair of different types, object encoding and mapping decoding
must fail with the destination contract's error: twenty pairs on each surface.
A smaller matrix checks six ordered raw-byte substitutions among request,
workload and transit only. It does not provide a five-language raw matrix or
compare multiple examples of each language. These rejection assertions do not
check the diagnostic length/cause/context properties of the malformed matrix.

Actual type and field-shape admission was checked in the
[transit codec](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_transit.py),
[surface-read codec](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_reads.py)
and [gateway-probe codec](../../../../control-plane-kit-core/src/control_plane_kit_core/gateway_delegation.py),
alongside request/workload codecs. The
[transit fixture](../../../../control-plane-kit-core/tests/fixtures/node_control_transit_canonical_wire_v1.json)
is an input to the matrix, not newly granted fixture coverage. The surface
fixture uses a fabricated declaration identity; the probe fixture constructs a
target and `/health` request without making a network call. Structural
non-substitution is distinct from issuer trust, signature validity, admitted
graph membership and live authorization.

The recursion test lowers Python's process-wide recursion limit to 200 and feeds
300 nested arrays to the request decoder. It requires the exact malformed-byte
error and no cause/context, restores the old limit in `finally`, then checks
restoration. This is one process-global-state test, not a per-request depth
budget, universal resource bound or proof of safe concurrent changes to that
interpreter setting.

## Exports and review limits

The last test checks two root attributes and their `__all__` membership. An AST
scan of ordinary and from-import roots excludes seven named dependencies; local
import aliases retain their original module names in this scan. It does not
inspect dynamic or transitive imports. Four exact substrings must occur in the
[canonical-wire document](../../../../control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md);
their presence does not establish current issue completion or durable storage.
There are no conditional skips substituting another serializer in this suite.

Review depth: full test/helpers, retained full canonical fixture/shared wire
owner, full transit fixture, and actual selected request/grant/raw/parser,
transit constructor/codec, surface-read constructor/codec, probe constructor/
codec, root export and target-type paths. The governing canonical-wire document
and declared dependency pin were retained from the preceding review. No whole
large-owner or installed-dependency audit is claimed. No imports, tests, global
recursion changes, canonicalizer/hash execution, database or provider actions
ran during this documentation review.
