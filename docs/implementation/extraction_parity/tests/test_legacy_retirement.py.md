Source: [extraction_parity/tests/test_legacy_retirement.py](../../../../extraction_parity/tests/test_legacy_retirement.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These six tests combine synthetic path/owner fixtures, a mocked Git baseline,
temporary files and reads of committed artifacts. Path-policy cases cover selected
delete/retain categories and an unknown path. The twelve-owner fixture accepts
the two completed issue numbers and rejects a mismatched state/disposition or
missing merge evidence; it does not query issue status or inspect merge objects.

The pre/post retirement test patches both baseline_entries and _git, constructs
four baseline records, creates temporary placeholder files and unlinks its three
deletion candidates. It verifies the mode flags and rejects a missing retained
README. Contents deliberately need not match the synthetic blob IDs. This is
filesystem-presence behavior over a fixture, not a real repository retirement.

The test named for every baseline path checks only that the stored manifest
exists and its listed paths are unique; it does not compare that list with Git.
The promotion test reads reconciliation, parity manifest and closeout, checking
the 24 fixed mappings and absence of issues 1316/1317 from future_issues. It does
not call promote_completed_owners, execute successor tests or verify merge proof.
The instruction test accepts clean temporary documents/source, then rejects one
legacy command string in README; it does not exercise AST import rejection.

The [implementation](../retirement.py.md) also has uncaptured boundaries: exact
baseline mismatch, unexpected remaining/deleted paths, extra paths and symlinks,
promotion mutation/partial writes, stale source evidence and shell invocation
context are not established by these cases. The full 235-line owner and 899-line
implementation were read, with selected real artifact records. No test, promotion,
retirement validation or deletion was executed during this documentation review.
