Source: [extraction_parity/validation.py](../../../extraction_parity/validation.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner compares a maintained parity ledger with expected mappings and
serialized successor evidence. It uses [manifest decoding/building](manifest.py.md)
to check structure and derive an initial expected mapping from ownership/demos,
without replacing the supplied ledger's completion fields. Reference identity,
missing/stale entries and law/owner/state differences become findings. This
comparison assumes the supplied inventories are authoritative; it does not
verify Git objects or execute the demos they describe.

Foundation policy accepts complete structural mapping while reporting unfinished
required entries. Migration-complete additionally rejects required entries
without a matching passing successor or structurally accepted supersession.
Deferred entries remain visible and do not require completion. Claimed evidence
on any entry can still introduce missing/status-mismatch findings, including
deferred work. At least one matching passing successor establishes completion;
an additional matching failed record does not by itself erase that passing one.
The final migration_complete flag also requires no findings, even under
foundation policy.

decode_evidence_index requires closed unique records, passing/failed statuses
and canonical sha256 text. Completion resolves successor.evidence to an index
record's ID and compares statuses; successor.id is not used to authenticate an
actual test. Digest bytes are not fetched or recomputed here. A structurally
complete supersession supplies completion directly, without verifying its review
reference. These are consistency checks on claims, not signed provenance,
fresh test runs or independent proof of semantic equivalence.

validate_required_core_closeout keeps foundation findings, requires completion
only for Core-owned required entries and rejects non-Core supersessions, even
on deferred entries. Incomplete required non-Core work stays counted for later
milestones. The family-inventory helper groups the supplied incomplete-Core
entries by reference family, sorts by size/name and rejects duplicate laws.
Its valid flag describes that projection's accepted shape, not successful
closeout; it does not independently recompute which laws are incomplete.
Neither helper is selected by the main foundation/migration-complete CLI.

The CLI reads four JSON objects and writes a report for ordinary validation
results, including invalid ones, then exits according to valid. read_bounded_json
checks a 16-MiB limit after reading the full file, before parsing. Direct library
calls bypass that file limit; named text fields use a separate 512-byte UTF-8
limit. There is no general output bound. Read/JSON/shape exceptions can escape
before report writing, leaving an older report at the destination.

write_report uses a fixed .tmp sibling and replacement without backup, fsync or
concurrent-writer protection. It validates neither an arbitrary report object nor
its freshness. [The wrapper](../validate-parity.sh.md) supplies working-tree files
and a writable report destination. The separate [observation runner](../../../extraction_parity/runner.py)
uses the bounded reader and evidence decoder; its evidence_record function hashes
a comparison result, but this validator does not replay that producer step.

[Focused tests](tests/test_parity_validation.py.md) protect policy distinctions,
mapping/evidence negatives and Core closeout/grouping. The committed report is
historical data; no fresh validation, migration, provider or runtime acceptance
was executed for these companions.
