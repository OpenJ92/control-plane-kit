Source: [control-plane-kit-core/tests/test_node_control_public_wire_ownership.py](../../../../control-plane-kit-core/tests/test_node_control_public_wire_ownership.py).
Maintain this document alongside its source file. When fixture use, shared-law ownership, assertions or evidence limits change, verify and update this companion in the same change.

This 197-line suite has five tests for the private
[_node_control_public_wire.py](../src/control_plane_kit_core/_node_control_public_wire.py.md)
owner. Helpers locate/import that module, require named members to exist and
load JSON fixture files. These are source-reviewed tests, not newly executed
evidence. They establish selected representation laws and structural ownership
checks, not live node-control behavior or authorization.

## Behavioral assertions

The classifier test checks the three violation enum values in order, every
`accepted` and `rejected` entry in
[node_control_public_material_v1.json](../../../../control-plane-kit-core/tests/fixtures/node_control_public_material_v1.json),
credential precedence over a mixed endpoint example, rejection after one ASCII
percent pass and admission of the chosen double-encoded example. It does not
consume that fixture's `authority_reference_accepted` or
`authority_reference_rejected` sections. For rejected entries, any law string
other than `credential-envelope` selects the endpoint expectation; the loop is
not a closed validation of fixture law labels or metadata.

The shape test checks identifier lengths 128/129, reference lengths 256/257,
selected empty/non-string/grammar failures, and public-material checks after
valid shape. It distinguishes the same HTTP-shaped text as identifier-shape
failure versus reference-endpoint failure. Digest examples cover one valid
64-character value, short, uppercase and non-hex values plus `None`. Epoch
examples cover zero, the maximum safe integer, boolean, negative and overflow.
These are selected cases, not a complete type/subclass or grammar matrix.

The canonicalization test consumes the three `requests` in
[node_control_canonical_wire_v1.json](../../../../control-plane-kit-core/tests/fixtures/node_control_canonical_wire_v1.json):
read-state, a large scalar float and weighted exponents. It compares the helper's
bytes exactly with each supplied `canonical_utf8` string encoded as UTF-8. It
does not check the fixture's hex/digest fields, workload-grant section, separate
number vectors or execution by its named non-Python consumers. The NaN case
expects the shared error, exact fixed text, no attacker-key text in str/repr,
no cause/context and an empty instance dictionary. It does not cover every
dependency failure or canonical JSON boundary.

## Structural assertions and limits

The shared-owner AST test collects ordinary imports and non-null from-import
module names. It requires a direct `rfc8785` import and excludes nine named
roots, including Core itself, Operations, Interpreters and selected network/
database/server packages. Ordinary import aliases still expose their original
module name to this scan. It also asserts only that `_node_control_public_wire`
is absent from `core.__all__`; the test itself explicitly imports the private
module. These checks do not prove all possible effects absent or inspect
dynamic imports and transitive dependencies.

The consumer AST test visits `node_control.py`, `node_control_surface_reads.py`
and `node_control_surface_read_results.py`. It rejects ordinary imports whose
full names are exactly `ipaddress` or `rfc8785`, and five named synchronous or
asynchronous helper definitions. That import check does not cover from-imports
or all submodule spellings. Despite the test name, it does not positively assert
that shared helpers are called or that codecs remain in their owners. Selected
actual consumer reads supply that composition context; they are separate from
what these assertions prove.

Review depth: full test/helpers and shared owner, both fixture inputs, and
selected consumer classification/error/size-bound paths. Reading fixture files
as dependencies grants no additional companion coverage. No tests, imports,
network access, database or provider mutations were executed. Neither lexical
admission nor canonical-byte equality grants permission to execute a command.
