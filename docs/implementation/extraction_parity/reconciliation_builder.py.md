Source: [extraction_parity/reconciliation_builder.py](../../../extraction_parity/reconciliation_builder.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

apply_issue_slice applies one explicit decision slice to a parity manifest and
reconciliation document. It reads the selected current test roots through the
[migration AST scanner](migration_inventory.py.md), not test execution. Core,
Operations and parity default to roots within the coordination checkout; external
Interpreters/Servers/Secrets require explicit current roots. Servers includes
products as well as repository tests. Parity scans extraction_parity/tests only,
not current_backend or test_support. These are working-tree inputs without Git
revision or cleanliness verification.

Decision decoding closes root/slice fields and selected nested records, requires
unique issues/distributions and checks supported dispositions. It is not uniformly
deep validation: several maps/lists receive container checks, with used values
validated later. Extra stale decision keys are not comprehensively rejected or
matched to assignments. The [stored decisions](../../../artifacts/extraction/semantic-test-reconciliation-decisions.json)
contain eight historical slices, including multiple current distributions for
cross-package laws; they are authored policy inputs rather than discovered proof.

Reference handling prioritizes non-current decisions, then future decisions,
then current mappings. Non-current decisions write supersession records; future
decisions clear completion records and stamp an open-state declaration. Current
successors come from an explicit override, otherwise retained manifest IDs when
all still exist, otherwise exactly one same-method-name candidate. A compatibility
helper rewrites selected package.tests prefixes into package:tests identities.
Existence in an AST index does not establish assertion quality or equivalent
behavior; even a pass-only test definition is an identity to this scanner.

Current mappings create passing successor records with the decision's evidence ID
without resolving evidence or running tests. Strengthening flags and optional
review-text overrides determine the review; default negative-case prose is built
from inventory hints. Statements about preserving a law remain review claims,
not conclusions derived from comparing implementations. Mutable decisions must
exactly cover assigned inputs and use current IDs or an existing archive path.
Archive checks reject absolute paths and .. components, but do not establish
content, digest, regular-file type or symlink containment.

The function mutates supplied manifest entries before final validation. It
replaces reconciliation records for the selected issue and retains other issues'
records, then decodes the manifest/reconciliation and runs
[issue-local validation](reconciliation.py.md). Failure can leave the caller's
in-memory manifest changed. Later ownership promotion is not automatically
reconciled: the stored #1346 decisions still include owners subsequently promoted
under #1318, so rebuilding an earlier slice can conflict with retained reviews.
This is not a general migration between successive ownership histories.

The CLI loads fixed artifact paths under --root and optional DISTRIBUTION=PATH
working trees. --check computes proposed documents and compares parsed Python
values with stored JSON; it does not require byte-identical formatting or write
files. It still scans source and checks archive paths. With --evidence-digest,
the proposed evidence index replaces the slice's record with passing status.
That argument check enforces only sha256: prefix and length 71, not hexadecimal
characters or correspondence with actual evidence bytes, and no evidence-index
decoder is called here.

Normal mode writes manifest, reconciliation and optionally evidence in sequence.
The imported manifest writer revalidates its document, but the file set is not a
transaction. Fixed .tmp siblings and replacement provide no backup, fsync,
concurrent-writer isolation or exceptional cleanup; later failure can leave
mixed generations. The builder does not refresh the migration inventory's
manifest digest or the downstream closeout digest chain. JSON/source reads and
serialization have no overall size bound or general secret-redaction pass.

The full 630-line owner, [1018-line tests](tests/test_semantic_reconciliation.py.md)
and relevant manifest/scanner/validator contracts were read. Decision metadata
and representative records were inspected, not all 7989 decision-file lines.
No slice application, CLI check, evidence generation or test was executed during
this documentation review.
