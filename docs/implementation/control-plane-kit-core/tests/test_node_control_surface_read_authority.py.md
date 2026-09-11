Source: [control-plane-kit-core/tests/test_node_control_surface_read_authority.py](../../../../control-plane-kit-core/tests/test_node_control_surface_read_authority.py).
Maintain this document alongside its source file. When surface-read identities, unsigned grant comparison, bounds, public-material laws or assertion limits change, verify and update this companion in the same change.

This 721-line suite has nine tests for the surface-read declaration/request/grant
language. Helpers require the module and named symbols to exist, build a one-mode
scalar declaration, a capabilities request and matching unsigned grant claims.
There are no conditional skips for missing contracts. Registry names, targets,
keys and time are synthetic inputs; no signature, live route, provider or durable
replay store supplies evidence.

## Identity and reachable-size witnesses

The declaration test compares its helper-built descriptor and canonical bytes
with the fixed [surface-read fixture](../../../../control-plane-kit-core/tests/fixtures/node_control_surface_read_canonical_wire_v1.json),
hashes the fixture bytes against their stored SHA-256, checks declaration identity
and mapping round-trip equality. A separate nineteen-variable declaration with
tailored description lengths reaches the 16,384-byte inner surface and 16,453-byte
envelope. A padded 16,454-byte mapping with a string replacing surface must fail
with the aggregate-bound diagnostic before nested type admission. That assertion
accepts any Exception matching the diagnostic, not specifically the nominal
surface-read error class.

The request test repeats the fixed descriptor/bytes/hash/digest and mapping checks
for capabilities. Seven changed values—four target coordinates, kind, declaration
identity and request ID—must each change the computed digest. This is a finite
preimage-sensitivity sample, not collision resistance proof. A maximum-identifier
request reaches 951 canonical bytes; a padded 952-byte mapping with a string
target must fail the aggregate diagnostic, again using broad Exception matching.
Neither test exercises raw-byte decoding or a cross-language implementation.

The grant test checks one mapping round trip, the surface-read purpose/profile,
and the 1,984-byte published cap. It checks request-ID repr metadata and five
grant fields' repr=False flags, omitting their values from grant repr. It does
not require all public hashes/claims to disappear from repr or descriptors.
A synthetic maximum grant with long references/identifiers, mock digests and safe
epoch values reaches 1,984 bytes using direct RFC 8785 encoding. It is not checked
against a matching request or derived workload audience. A 1,985-byte padded
string-target mapping must hit the aggregate diagnostic with the same broad
exception assertion. There is no fixed grant byte/hash vector in this test.

## Unsigned grant comparisons and authority separation

The verifier test accepts the normal grant at epoch 150 and exactly at not-before,
then checks TEMPORALLY_INVALID exactly at expiry. Thirteen cases cover the current
rejection codes, often combining an earlier and later contradiction: purpose
before issuer, issuer before key, key before audience, audience before time,
then temporal/target/read-kind/declaration/request failures. This samples the
specified precedence rather than enumerating every conflicting combination or
asserting the complete enum membership/order as a separate value.

The final case changes both request ID and digest; it does not isolate digest-only
failure. The suite does not exhaust malformed expected issuer/key/audience/time
arguments. The [actual comparator](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_reads.py)
compares unsigned claims against caller-supplied expectations. Recognized foreign
purpose is constructible as data but rejected at the comparison boundary; no
signature, trust-chain, revocation or JTI-use decision is made here.

The disjoint-authority test passes a surface grant to the workload-command
verifier, then passes a command grant and gateway-probe grant to the surface-read
verifier. All three directions return GRANT_TYPE_MISMATCH. It does not cover every
authority family, every reverse verifier direction or cross-family codec/raw-byte
substitution. The helper's gateway probe is constructed but never sent.

## Public material, strict shapes and time

Three malformed mappings use unknown request kind, unknown grant purpose and an
unknown grant key/value. They must raise the nominal error with text at most 128
characters, omit the selected canary substring and carry no cause/context chain.
This loop checks str, not error repr, and is not a universal diagnostic audit.

The public-material test consumes only the authority-reference sections of the
[public-material fixture](../../../../control-plane-kit-core/tests/fixtures/node_control_public_material_v1.json).
Accepted and rejected values replace issuer only. Rejected cases must match the
fixture's named law: some fail reference grammar before credential/endpoint
classification. The test does not apply that matrix to every grant field or
claim that every secret or address spelling is recognized. Bare DNS labels and
selected invalid-port strings are admitted public references, not permissions to
contact those locations.

The strict-shape test removes each declaration/request/grant top-level key and
replaces each with None: two, six and fifteen keys respectively. It does the same
for all four nested target keys in both request and grant. These are finite
missing/None cases, not all wrong Python types. Five unknown profile/canonicalization
descriptors and three nested credential examples must fail without chains; the
nested loop does not separately assert canary absence from error text.

Fifteen constructor failures cover selected short/uppercase/long identities,
oversized request/key/JTI/reference strings, negative/unsafe epochs, invalid
not-before/expiry relationships and excessive lifetime. A boundary grant ends at
2**53-1 with a 300-second lifetime. The suite does not test every safe/unsafe value
for every field. The owner performs canonical mapping admission before field
reconstruction, so a rejected mapping alone does not identify the winning guard.

## Protocol and ownership evidence

The last test fixes the four ordered method/path/scope triples: capabilities and
status GET routes share the surface-read scope, while variable GET and command
POST keep their separate scopes. Thirteen selected root attributes must be
non-None; this does not assert their binding identity or either `__all__` list.
The AST check rejects from-import module names beginning with five dependency
prefixes. It does not inspect ordinary import statements or establish the full
transitive package graph. Neither route declarations nor source inspection prove
that HTTP handlers authenticate requests.

Authoring used the full test/helpers and full 790-line owner retained from the
owner review, both fixtures and actual result-consumer context; public-material
and diagnostic assertion sections were refreshed. The
[wire contract](../../../../control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md)
separates exact public identities from authentication and live observation.
Only this test's row gains coverage. No imports, executable tests, hashing tools,
HTTP, signing, persistence or provider effects were run while authoring. These
assertions describe pure contract evidence, not an authorization or executed read.
