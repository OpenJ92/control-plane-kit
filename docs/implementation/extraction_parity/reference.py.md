Source: [extraction_parity/reference.py](../../../extraction_parity/reference.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This module turns supplied unittest output, identity values and resource lists
into frozen-reference evidence. It does not run tests, inspect Docker or resolve
Git identities. The [shell wrapper](../reference-test.sh.md) supplies those
observations; the module records a summary rather than retaining the raw log.

parse_unittest_summary checks a positive byte bound, UTF-8 encoded output size,
one matching Ran line and one matching OK line. The searches are independent:
they do not require a single final summary, ordering, absence of contradictory
failure text, or a relationship between test and skip counts. The whole string
already exists before this bound is checked; the CLI reads the whole log file
first. TestSummary is a frozen record without its own validation.

build_reference_evidence requires nonempty reference identity, equality of the
supplied actual/expected commits and a truthy successful summary. It does not
validate commit/digest syntax, resolve those declarations or reject nonempty
resource additions. Image IDs and commands are recorded as supplied. The
Postgres reference is fixed to postgres:16-alpine. Dependency-input hashes are
sorted by name, while resource lists retain the caller's values. Thus the result
is a structured report, not an authenticated attestation or a residue-free gate.

added_resource_ids returns sorted unique after-minus-before values, without
ownership attribution or mutation. The CLI uses it for container/network/volume
additions and for volumes present in the observed snapshot but absent after
cleanup. The latter becomes owned_cleanup even though set subtraction alone
does not prove who created or removed those volumes. Resource-list reads are
unbounded text reads; blank lines are discarded without further ID validation.

sha256_file streams each dependency input in 64-KiB chunks. The CLI indexes these
digests by basename, so duplicate basenames overwrite an earlier entry. It records
fixed test/compile command descriptions without independently verifying their
execution. Caller-supplied image IDs are not reconciled with those file hashes.
No raw-log/environment field is emitted, but arbitrary metadata is not generally
screened for secrets, and the output document has no overall size bound.

write_evidence serializes sorted, indented JSON to a fixed sibling .tmp file and
replaces the destination with os.replace. It neither validates the document nor
provides backup, fsync, concurrent-writer isolation or exceptional temporary-file
cleanup. CLI errors before replacement can leave an older evidence file intact.
There is no failure-evidence document written by a catch-all error path.

The stored [reference baseline](../../../artifacts/extraction/reference-baseline.json)
declares commit 20129959d3b0f8e8bd5dbdafdf51c0a5d592a9ec, 1112 tests, zero skips,
success, empty final additions and one cleaned volume. These are historical
recorded values, not results obtained by this review. The
[focused tests](tests/test_reference_parity_evidence.py.md) protect selected
summary, identity, resource-difference and encoding cases. The full 174-line
owner was read; no evidence generation or executable validation was performed.
