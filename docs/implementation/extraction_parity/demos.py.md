Source: [extraction_parity/demos.py](../../../extraction_parity/demos.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This validator checks the shape and selected file coverage of the
[historical demo inventory](../../../artifacts/extraction/reference-demos.json).
The caller supplies discovered script and fixture sets. Each declared script
and fixture must appear exactly once across all demo records, and those global
sets must equal the supplied discoveries. Demo IDs are also unique. The function
does not discover files itself or execute scripts, fixtures or cleanup actions.

Root/demo keys and kind/owner-kind/bootstrap-state choices are closed. Named text
and list items must be nonblank and at most 512 Python characters, not 512 UTF-8
bytes. Prerequisites, inputs, observables and cleanup lists must be nonempty.
Script/fixture/documentation lists can individually be empty; documentation links
and path safety are not checked. Owner text is not verified against a repository
or cross-checked with owner_kind. Normalization permits only allocated-port,
generated-id, timestamp and container-name labels; this validates declarations
without actually normalizing observations or proving semantic preservation.

The root must contain reference, but its value is not examined here. Nor is
the supplied discovery set authenticated against a commit. Thus a passing result
does not bind the inventory's declared tag/commit to the files a caller selected.
The [wrapper](../validate-reference-demos.sh.md) determines that selection and its
limitations. Empty or malformed trusted Python inputs can also escape through
ordinary type errors; this is not a complete hostile-input decoder or bounded
document parser.

[Manifest construction](manifest.py.md) separately consumes demo IDs, owners and
bootstrap states and compares reference identity with law ownership. It does
not call this full demo validator or preserve all prerequisite/observable/cleanup
details in the ledger. Later manifest validity therefore does not automatically
establish this script/fixture coverage check, and neither check proves the
historical declarations happened or current successors pass.

[Two focused tests](tests/test_reference_demo_inventory.py.md) cover representative
set correspondence, extra fields and forbidden semantic normalization. The
current note is source review; no demos, Docker validation or artifact updates
were executed.
