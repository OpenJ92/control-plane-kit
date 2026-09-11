Source: [extraction_parity/completion.py](../../../extraction_parity/completion.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This module validates semantic migration closeout as linked evidence documents.
It distinguishes implemented/superseded laws from future-owned laws and can return
valid=true and zero_unowned=true while required behavior remains future-owned.
Those flags describe recorded coverage under this policy, not completion of every
implementation or a fresh successful package/live run.

decode_semantic_closeout closes the issue-1326 root and nested additional-test,
future-issue, demo-review, live-law and expected-count records. Selected text is
bounded to 2048 UTF-8 bytes; digest strings require lowercase canonical SHA-256
syntax, issue numbers exclude booleans, and counts are nonnegative integers.
Identity collections must be unique. Additional tests must be parity-owned;
future snapshots must declare open state and an identity-matching GitHub URL;
demo dispositions control whether current evidence or a future issue is allowed.
Live-law classifications come from a fixed set covering gates, diagnostics,
publication and resource audits as well as authoritative live work.

validate_semantic_completion calls the imported
[manifest](manifest.py.md), [reconciliation](reconciliation.py.md),
[migration inventory](migration_inventory.py.md) and
[evidence-index](validation.py.md) decoders. It recomputes canonical JSON hashes
for five input documents, matches them to closeout, aligns reference declarations
and checks the inventory's manifest digest. This catches inconsistent linked
documents; it does not authenticate their author or independently establish the
execution claims they contain.

Aggregate package evidence must identify issue 1348 and have the same distribution
keys as the inventory's selected current-source entries. Method counts must match;
package commits are compared when the package record supplies one. This does not
require all five distributions to be present, force a commit field, verify Git
objects or rerun gates. Other aggregate provenance/status fields are not given a
full closed-schema validation here.

Reference test and demo review sets must exactly match their manifest entries and
laws. Current test reviews must name inventory/additional IDs; their manifest
successors must have exactly the expected IDs and both successor/index statuses
must be passing. Current demo reviews use the same passing-record check without
requiring their evidence identities to be test IDs. Future reviews must name a
snapshot and retain no completion evidence; each snapshot's law list must equal
the laws assigned to it. Other dispositions require a supersession record.

Additional IDs cannot overlap inventoried IDs. Mutable-only reviews are checked
for known current IDs, but this function does not call issue-local reconciliation
validation or independently require exhaustive correspondence with mutable-only
inventory assignments. Live-law distribution/path pairs must exactly match the
inventory's current script pairs; their evidence text is not resolved into an
execution record. Expected counts must match computed values, with unowned and
stale_successors reported as zero after these checks, and separate future/required-
future totals remain visible.

The validator does not read additional test source files, execute named methods,
resolve archive content or retrieve issue bodies. Source/content digest syntax,
open state and gate/path fields remain declarations at this boundary. The
[repository test](tests/test_semantic_completion.py.md) separately hashes additional
test files. Imported decoders retain their own validation limits; the closeout
decoder returns its input dictionary, and no immutable input snapshot is created.

The CLI reads six caller-selected JSON files and writes a report only after
successful validation. Reads/serialization have no overall size bound or general
secret-redaction pass. The report uses a fixed sibling .tmp and os.replace,
without backup, fsync, concurrent-writer isolation or exceptional temporary-file
cleanup. A failure can leave an older report intact; there is no failure report
written by a catch-all handler or transaction over input reads.

The stored [report](../../../artifacts/extraction/semantic-migration-completion-report.json)
records 1107 manifest entries, 368 future-owned laws (152 required), 739 implemented
or superseded entries and 1336 current test identities. These are historical
document counts. The full 536-line owner, 372-line test owner and relevant imported
contracts were read, with selected actual closeout/evidence records. No validator,
test suite, source-hash gate or report generation ran during this review.
