Source: [extraction_parity/reconciliation.py](../../../extraction_parity/reconciliation.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This module defines reviewed dispositions layered over reference laws. Reference
reviews distinguish current-isomorphic, current-strengthened, reviewed-supersession,
future-issue and archived-obsolete. Mutable-only reviews instead distinguish
current-new-law/current-strengthened from reviewed-archived. The records preserve
rationale and negative-case/obsolete-assumption decisions as supplied text; the
module does not independently judge semantic equivalence or review quality.

decode_reconciliation closes the root, review, mutable-review and future-issue
field sets. It bounds each checked text value to 1024 UTF-8 bytes, rejects boolean
or nonpositive issue numbers, and requires unique reference/source identities and
test lists. Current reviews need tests and no future issue; future reviews need
an open issue declaration and no tests; non-current reviews name neither. Archived
mutable reviews need an archive-artifact string, while current mutable reviews
need tests and no archive. Empty review collections are permitted.

Those are document constraints: open state is a declaration, archive_artifact is
not checked on disk, and reviewed_by_issue is not authenticated reviewer approval.
There is no aggregate document/list-size bound or secret-redaction policy. The
decoder returns the original mutable object, and arbitrary malformed Python
inputs are not universally normalized to ReconciliationError.

validate_reconciliation validates one selected issue's assignment slice. Every
reference assigned to that issue must have exactly one review by it, with the
same law in inventory and manifest. Named tests must belong to a caller-supplied
current-ID set. Current review IDs must equal manifest successor IDs as sets and
have no supersession; future reviews must have no completion records; other
reviews require a non-null supersession. Mutable reviews must exactly cover that
issue's mutable assignments, match their target distribution and name known IDs.

The function decodes all reconciliation records structurally, but checks semantic
alignment only for the selected issue. Inventory/manifest receive schema-tag
checks rather than their full decoders; duplicate index keys can overwrite, and
cross-document source identity, owner claims and detailed supersession contents
are not comprehensively verified. It does not inspect successor passing status,
resolve an evidence index, execute tests or prove that the supplied current IDs
exist in source. A valid report means the issue-local correspondence passed
these checks, not that all repository migration work is complete.

The [builder](reconciliation_builder.py.md) supplies IDs from source AST scans,
applies decisions and runs full manifest decoding before this validator. The
[completion layer](../../../extraction_parity/completion.py) adds whole-document
coverage, digest and recorded-evidence checks. Later
[retirement promotion](retirement.py.md) can transfer review ownership, so an old
provisional issue slice is not automatically the final ownership view.

The [stored reconciliation](../../../artifacts/extraction/semantic-test-reconciliation.json)
contains 1090 reference reviews and 120 mutable-only reviews; these are historical
record counts. The full 304-line owner and
[1018-line tests](tests/test_semantic_reconciliation.py.md) were read, with actual
selected artifacts. No validation, test execution or artifact mutation ran for
this companion.
