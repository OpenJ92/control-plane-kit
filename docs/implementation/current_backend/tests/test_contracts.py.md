Source: [current_backend/tests/test_contracts.py](../../../../current_backend/tests/test_contracts.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These tests give the [static validator](../contracts.py.md) a temporary
four-repository tree through a directly constructed MaterializedBackend. They
load the real [contract manifest](../../current-backend.contracts.json.md), then
populate minimal source, metadata, catalogue/checksum and script fixtures. This
couples the fixture to the selected contract inventory without cloning Git
repositories or proving a locked source selection.

The positive case fixes 13 owned source files, selected parsed edges, one product,
six protocol names and the source-live acceptance identity. Negative mutations
cover reverse/cyclic and forbidden edges, source ownership gaps/overlap, stale
or unowned archive pins, descriptor/catalogue checksum drift, missing protocol
methods, forbidden Operations imports and overclaimed caller/mock flags. Loader
cases reject unknown fields, a traversing source glob and duplicate prefixes.
Several tests check finding substrings rather than a complete diagnostic report;
the matrix does not exhaust malformed inputs or every graph/path case.

The fixture makes evidence limits visible. Protocols and implementations contain
method stubs. The acceptance script consists of a shebang and required markers
in comments, and the residue script simply exits successfully if invoked; neither
is run by these tests. The optional Secrets archive appears in Interpreter
metadata for pin scanning while base declared-edge extraction excludes extras.
Synthetic repeated-digit commits, image digests and locally calculated hashes
establish correspondence within the fixture, not external artifact provenance.

The complete 476-line owner, including fixture writers, was read for this note.
Temporary file mutation is part of test construction, not a provider effect.
No contract tests, package gates, Docker runs or acceptance scripts were executed
for this documentation. Keep [source-lock tests](../../../../current_backend/tests/test_source_lock.py)
and runner/executable-stage evidence separate when interpreting a green result.
