Source: [control-plane-kit-core/tests/test_node_control_transit.py](../../../../control-plane-kit-core/tests/test_node_control_transit.py).
Maintain this document alongside its source file. When transit laws, canonical vectors, bounds, verification order or assertion limits change, verify and update this companion in the same change.

This 898-line suite has nine tests for the pure gateway node-control transit
language. Helpers require the module and requested symbols to exist, then build
a mode/scalar-green request with expected version 7 and matching unsigned transit
claims. Missing symbols fail assertions rather than skipping tests. Issuer/key,
attempt and gateway expectations are synthetic; no signing, forwarding, provider
inspection or workload command execution occurs in these tests.

## Canonical identity and reachable bounds

The first test consumes the fixed
[canonical fixture](../../../../control-plane-kit-core/tests/fixtures/node_control_transit_canonical_wire_v1.json).
It compares direct RFC 8785 encoding with stored UTF-8 text, hashes those bytes
against the stored digest, and compares a helper-built grant's descriptor,
canonical bytes and digest with those expectations. Mapping and raw-byte round
trips are also checked. Eleven named exports must share identity between root
and module and appear in root `__all__`; the private wire module name must not
appear there. This is one fixed grant vector plus selected export assertions,
not a cryptographic signature vector or complete export inventory.

The maximum example fills identifier fields and target references with 128
characters, issuer with 256, uses a 64-character digest, large safe epochs and
the weighted replacement codec. It asserts the reachable 265-byte derived
audience, 2,834-byte canonical grant and 300-second published lifetime bound.
A 2,835-byte raw input must fail the aggregate guard without a cause/context
chain. The maximum grant is synthetic: its mocked request digest and changed
codec are not validated against the helper's scalar request.

The field-bound test separately rejects five 129-character identifier fields,
257-character issuer, 65-character digest, four oversized top-level graph
references, all four oversized target references and each epoch above 2**53-1.
It accepts an example ending exactly at the maximum safe epoch. These assertions
establish rejection, not which internal guard rejected every case; mapping
canonical-domain admission can fail before the field validator. They do not
exhaust all malformed strings or combinations of individually bounded fields.

## Raw inputs and local construction

Eight raw candidates cover malformed UTF-8, incomplete JSON, deep arrays,
duplicate top-level/nested keys, NaN, leading whitespace and a missing key plus
trailing whitespace. Each must raise the nominal transit error with text at most
128 characters, no attempt identifier and no cause/context. The last candidate
violates both shape and canonical form, so it does not isolate one guard.

Mapping checks establish one valid round trip, reject each of the 21 top-level
keys when missing or replaced with None, reject one unknown top-level key and
reject a missing or extra nested target key. Those mapping checks assert error
type without the raw loop's diagnostic assertions. None is only one wrong-type
representative, and the apply-command fixture makes a null command codec invalid;
read-state's valid null codec is not contradicted by this test.

A separate recursion test lowers the process-wide recursion limit to 200 and
passes 300 nested arrays. It expects the nominal bounded, cause-free error and
restores the previous limit in `finally`. This is a controlled parser witness,
not a per-request depth policy or concurrency guarantee. The
[actual codec](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_transit.py)
checks the byte cap, parses, reconstructs the typed mapping and requires exact
canonical re-encoding; it does not translate every possible Python callback or
serializer exception into the same error.

The constructor test confirms audience is not a dataclass field and fixes the
derived example. Ten invalid replacements cover inconsistent coordinates, wrong
reference roles, temporal contradictions, bool epoch, raw-string purpose and
raw-string profile. Five malformed descriptors cover audience/coordinate mismatch
and unknown profile/canonicalization, with no cause/context chains. The typed
constructor admits any recognized purpose enum; the following verifier test
demonstrates why that is distinct from accepted transit authority.

## Verification order and language separation

The verifier test fixes the ordered fourteen rejection-code strings, accepts the
default grant and exact not-before endpoint, and rejects the exact expiry
endpoint. Fourteen cases exercise each bounded rejection code. Several include
later contradictions so the earlier code must win: wrong purpose precedes wrong
issuer, issuer precedes key, key precedes time, and time precedes attempt. Changed
request coordinates similarly distinguish workspace/revision/node/socket checks.
This samples consequential precedence, not all combinations of failing claims.

The command case changes to a read request, changing both operation and codec.
The final request case changes both request ID and digest; it does not isolate
digest-only or idempotency-key-only failures. The suite does not test every
invalid expected argument. Two verification-result constructor examples reject
accepted-with-code and rejected-without-code; they do not enumerate all bad types.

The cross-family test constructs transit, workload-command, workload-surface-read
and gateway-probe grants. Every ordered foreign pair is rejected at object encode
and descriptor decode: twelve cases per representation. It also checks three
foreign values against the transit verifier and transit against the workload
command and surface-read verifiers. It does not test every possible verifier
direction or raw-byte family substitution here. The surface declaration identity
is a mock digest and the probe is data; neither calls a workload or gateway.

## Disclosure and finite ownership guards

The final test injects thirteen public canaries into a grant, checks six fields'
explicit repr=False metadata, and checks all canaries plus the derived audience
are absent from the rendered grant. This includes nested reference suppression;
it does not require those public claims to disappear from descriptors. Four bad
descriptors exercise credential-like issuer, endpoint-like issuer, unknown purpose
and unknown key/value. Error text must be bounded, omit the selected canary
substrings and carry no cause/context; error repr is not checked in this loop.

AST inspection requires the shared public-wire import and excludes thirteen
exact imported module names. It considers normal and from-import syntax, including
aliases' original names, but exact-name comparison does not reject every possible
submodule import. Two further finite checks exclude six shared-helper definition
names and nine public transport/secret-oriented class/function names. They inspect
top-level ClassDef/FunctionDef nodes, not all assignments, async definitions or a
complete transitive import graph. These checks help preserve ownership without
proving a universal absence of effects.

Authoring used the full suite/helpers, full transit owner and fixture retained
from the owner review, with selected assertion sections refreshed. The related
[wire contract](../../../../control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md)
requires complete canonical payloads at signing boundaries; this suite supplies
no signature, key-trust, revocation, replay, graph-provenance or provider evidence.
Only this test's row gains coverage in this slice. No imports, executable tests,
hashing tools, signing, persistence or provider effects were run while writing
this companion. Passing pure claim checks is not permission to execute.
