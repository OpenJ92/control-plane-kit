Source: [control-plane-kit-core/tests/test_node_control_surface_read_results.py](../../../../control-plane-kit-core/tests/test_node_control_surface_read_results.py).
Maintain this document alongside its source file. When result binding, canonical vectors, coverage semantics, size limits or assertion depth change, verify and update this companion in the same change.

This 792-line suite has eight tests for surface-read capability/status results.
Helpers require the result module and named contracts to exist; absence is a
failure, not a conditional skip. They construct typed variable declarations and
requests, decode fixed fixture contexts, and build codecs tied to those contexts.
No live registry, HTTP handler, workload operation or provider supplies evidence.

## Fixed outputs and public value shape

The first test uses the
[canonical fixture](../../../../control-plane-kit-core/tests/fixtures/node_control_surface_read_canonical_wire_v1.json)
to construct one capabilities result and three status results representing empty,
alpha-only and alpha/beta installed tuples. `assert_vector` compares the encoded
descriptor, value canonical bytes, direct RFC 8785 encoding and SHA-256 of stored
expected bytes, then checks mapping decode equality. The status declaration and
request preimages have their own descriptor/text/hash checks. These are fixed
canonical-output and mapping assertions, not raw-byte decoding or SDK execution.
The test also fixes both nominal kinds, request associations, derived status
coverage and the exact two-member result union.

The second test asserts exact stored dataclass fields: request/declaration for
capabilities, plus installed names for status. For one capability value it checks
derived profile, canonicalization, request ID/digest, declaration identity and
descriptor equality through encode. It rejects both wrong-kind constructions,
a mismatched declaration, and a socket mismatch deliberately built with the
new declaration's correct identity. The latter tests both the value and codec,
separating socket consistency from identity consistency. Two wrong-type contexts
also fail in both places. The exact field checks do not themselves assert
immutability, all generated dataclass methods or every invalid argument type.

## Substitution and structural coverage

Cross-kind objects and descriptors are rejected in both directions. A codec
bound to another request ID rejects the original status value. Six descriptor
substitutions change request ID, digest, kind, declaration identity, profile or
canonicalization and must fail. These six cases assert the error class without
proving that each reached its corresponding semantic check; the codec's context
size admission happens first.

A separate wrong-identity request fails codec construction. For capability
declaration substitution, the test constructs a different same-sized declaration,
asserts equal declaration and complete result byte lengths, then expects a
declaration error. This provides a focused semantic-binding witness that cannot
be satisfied merely by rejecting a larger payload.

The coverage test separately checks none/partial/complete for the two-name
declaration. Five constructor negatives cover unsorted, duplicate, undeclared,
wrong-role and list-instead-of-tuple names. Eight wire negatives cover contradictory
or unknown coverage, unsorted/duplicate/undeclared names, a nontext name and a
nonlist field. It does not query a registry or establish that installed handlers
match the names. The [result owner](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_read_results.py)
derives coverage from a canonical declared subset; these tests exercise that
structural interpretation.

## Strict fields and admission order

For the capability and status descriptors, the strict-codec test drops every
key individually: seven and eight cases respectively. It adds each of eleven
foreign fields to both variants, including state/version/evidence, payload,
endpoint/signature/diagnostic, target/registry and health/readiness. Four wrong
field-type examples and encoding an arbitrary object also fail. These checks
assert error type, not universal redaction or which early size/key/type guard
won. They keep this read-result vocabulary distinct from state or runtime reports.

Maximum helpers construct a capability declaration using nineteen variables with
carefully sized descriptions, and a status declaration using thirty-three map
variables with 128-character names. With maximum request-ID length, results reach
the published 16,902 and 4,811 canonical-byte bounds. The capability declaration
is exactly 16,453 bytes; the status surface body is 16,146. These witnesses show
reachable sizes, not that every individually bounded declaration/context is
admitted or that 128 maximum-length names fit.

For each variant, a helper replaces the nested declaration or name-list field
with padded text and verifies the candidate is exactly global-limit-plus-one.
The aggregate-bound diagnostic must win over nested type validation. Two smaller
contexts then gain one request-ID character and must fail their context bounds;
the capability candidate is explicitly below its global limit. Finally, a
129-name status list is explicitly below the global byte cap yet must fail with
"too many". These are targeted order witnesses, not a complete resource-cost or
arbitrary Python Mapping safety proof.

## Error disclosure and ownership guards

The diagnostic test adds an unknown canary key/value to a status descriptor and
checks both str and repr omit those strings, with no cause/context chain. Two
nested cases place a credential canary in a capability description or installed
name; they require categorical malformed errors, omit the canary in str/repr and
retain no chain. Unlike some other suites, these assertions do not impose an
explicit numeric error-text length cap.

Selected request ID, declaration, installed-name and topology strings must also
be absent from status/capability repr. That does not require their public
descriptors to omit them, nor does it test every possible exception pathway or
sensitive spelling. The actual owner translates selected nested/canonical errors;
it is not a universal wrapper around callbacks and serializer failures.

The final test checks eight root bindings have identity with their module values
and that each name appears in the module's `__all__`. It does not check root
`__all__`, exact equality of either export list or every public binding. AST
inspection excludes six dependency roots for normal/from imports, including
submodules and aliases' original names. A source-string assertion looks for the
module name in test_milestone_closeout.py. That is a finite textual membership
guard, not execution of the milestone or a complete package inventory.

Authoring used the full test/helpers and full 591-line owner retained from the
owner review, with substitution/strict-field sections freshly reread and the
full fixed fixture retained. The
[wire contract](../../../../control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md)
keeps declaration/name consistency distinct from live registry provenance. No
imports, executable tests, hashing tools, HTTP, signing, persistence or provider
effects were performed while authoring. Only this test's row gains coverage;
these pure assertions do not establish freshness, authentication or permission
to act on the result.
