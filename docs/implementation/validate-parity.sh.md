Source: [validate-parity.sh](../../validate-parity.sh).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This wrapper runs the current [parity validator](extraction_parity/validation.py.md)
in python:3.14-slim against four working-tree artifacts: the parity manifest,
law ownership, demo inventory and successor-evidence index. It replaces
parity-validation-report.json. The whole repository is mounted writable, even
though the current CLI writes only its report. --rm requests ordinary container
removal; the report persists on the host and Docker may pull the mutable image.

The first argument selects policy, defaulting to foundation. The Python CLI
accepts foundation or migration-complete; the wrapper does not expose the
separate Core-closeout helpers. Foundation can exit successfully with incomplete
required work, so inspect migration_complete and counts before describing a
completed migration. The stronger policy still checks serialized claims rather
than rerunning successor tests or authenticating their digest/review records.

The wrapper performs no Git-cleanliness, frozen-commit or artifact-provenance
selection. A validation result reflects the current input copies, whose declared
identities can be historical. Normal invalid results write a report and return
nonzero; read/parse failures can leave a prior report untouched. Existence alone
does not establish freshness or success. The writer has a fixed temporary
sibling and no fsync/concurrent-writer guarantee.

[Unit tests](extraction_parity/tests/test_parity_validation.py.md) establish
selected policy laws without invoking this shell. No report regeneration,
Docker command, successor test or runtime action was executed during review.
