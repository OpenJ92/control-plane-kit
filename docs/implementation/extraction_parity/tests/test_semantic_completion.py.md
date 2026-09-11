Source: [extraction_parity/tests/test_semantic_completion.py](../../../../extraction_parity/tests/test_semantic_completion.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

The synthetic six-document fixture contains a current law, a required future law
and a superseded demo, one Core source/test identity and one inventoried script.
Input hashes are computed from those dictionaries; issue content/evidence digests
and source commits are declarations rather than verified external objects. The
success case explicitly accepts zero_unowned with one future-owned law and two
implemented-or-superseded entries, preserving that distinction in the public
[completion policy](../completion.py.md).

Negative cases remove a demo or current identity, close a future declaration or
remove its assigned law, change expected counts, remove the live-law entry and
change a package source coordinate. Where relevant, the fixture refreshes the
input digest so validation reaches the intended correspondence failure. A removed
current ID is tested as a missing inventoried identity, not as a method deleted from a
real test module. The package fixture contains only Core, so this suite does not
establish a mandatory five-distribution set.

The repository case loads the six actual artifacts, invokes validation, checks
1107 entries/zero unowned and compares the entire result with the stored report.
It additionally hashes each additional-current-test source file and compares that
digest with closeout. That filesystem check is stronger than the validator's
source-digest syntax check, but it still does not execute those tests, authenticate
their historical passing evidence or refresh remote issue state.

The cases do not exercise CLI report writing/failure, concurrent input changes,
every nested decoder rule, exhaustive mutable-only assignment coverage or general
proof provenance. The full 372-line test owner and 536-line implementation were
read, with actual report and selected closeout metadata/records; large evidence
collections were not reviewed record by record. No test, application validation,
source-hash check or artifact regeneration was run for this companion.
