Source: [validate-semantic-test-migration-inventory.sh](../../validate-semantic-test-migration-inventory.sh).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This wrapper creates a temporary directory and calls the
[builder](build-semantic-test-migration-inventory.sh.md) with its output redirected
to a temporary inventory. It then uses cmp -s against the fixed repository path
artifacts/extraction/semantic-test-migration-inventory.json. Success establishes
byte equality for that regeneration, including ordering/formatting, rather than
semantic test equivalence or a passing package gate.

Repository/ref overrides still flow to the builder through the inherited
environment; its current scanner/rules/artifact inputs and mutable Python image
remain inputs to validation. The wrapper overrides the output-path variable, so
a caller's alternative output path does not change the expected artifact here.
The child builder still archives source and runs Docker. Redirecting its stdout
does not make the check read-only or suppress stderr.

With set -e, builder failure stops before comparison. A differing, missing or
unreadable expected file reaches the same stale/regenerate diagnostic through
cmp failure; that message alone does not identify the cause. The script does not
repair the stored artifact. Its EXIT trap deletes its temporary directory, while
child cleanup remains the builder's responsibility; no broader residue audit,
concurrent-input snapshot or timeout is added.

The full 22-line owner and its 90-line builder were read alongside the scanner
and focused tests. No wrapper execution or artifact regeneration occurred during
this review, so no current/freshness result is claimed.
