Source: [extraction_parity/tests/test_semantic_reconciliation.py](../../../../extraction_parity/tests/test_semantic_reconciliation.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

The fixture cases exercise the [review codec/validator](../reconciliation.py.md)
and [slice builder](../reconciliation_builder.py.md). They reject absent mutable
decisions, nonexistent IDs, missing archive artifacts, duplicate mutable/review
identities, law drift, closed future declarations, empty current test lists and
missing assigned reviews. Other cases permit multiple unique current distributions
and reject empty/duplicate distributions.

Temporary test trees establish explicit external-root requirements and inclusion
of both Servers repository and product tests. Their pass-only methods are parsed
as source identities, not executed as evidence. These fixtures protect scanner
selection and record correspondence rather than behavioral equivalence or test
integrity. They do not exercise the builder CLI's --check or evidence publication.

Artifact cases have different evidence depth. Core and Operations cases scan
their current working-tree tests, then validate the stored issue slice against
those identities. Interpreters, Servers and Secrets cases inspect recorded review
counts, commits, test counts and selected publication fields without executing
those external suites or retrieving published images. Later cases inspect
mutable archives, architecture/package promotion, live-script dispositions and
aggregate records. The aggregate case recomputes SHA-256 over predecessor JSON
file bytes and the script-dispositions file; that checks stored-file linkage,
not the execution claims inside them.

The tests explicitly distinguish historical aggregate counts from later #1318
promotion: 24 reviews move from the old future-owner view to current-strengthened
while the earlier aggregate artifact retains its original counts. Names such as
live tests or durable evidence do not make these artifact assertions live runs.
The cases do not establish general archive containment, deep malformed-input
handling, stale decision reconciliation, rollback of in-memory changes, CLI
evidence-digest validation or atomic multi-file writes.

The complete 1018-line owner and both implementation owners were read. Actual
decision metadata, selected reconciliation/aggregate records and external evidence
declarations were inspected; the large artifact collections were not audited
record by record. No test, source scan through the application, slice build,
publication check or artifact rewrite ran during this documentation review.
