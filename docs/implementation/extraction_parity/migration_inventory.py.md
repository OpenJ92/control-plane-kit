Source: [extraction_parity/migration_inventory.py](../../../extraction_parity/migration_inventory.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This module inventories source syntax for migration planning. SourceLane supplies
a repository/commit/gate declaration and filesystem roots; it does not validate
that those roots actually represent the declared commit or execute the gate.
The CLI defines exactly seven lanes: frozen and mutable legacy, Core, Operations,
Interpreters, Servers and Secrets. Core/Operations use package-local tests;
Servers scans both tests and products. No Server SDK lane is included.

scan_test_root parses test_*.py files without importing or running their tests.
It records top-level test functions and direct test methods of top-level classes,
including async definitions, without checking unittest inheritance. It does not
reproduce inherited/imported tests, load_tests, dynamic generation or runtime
collection multiplicity. Other Python files become helpers only when their path
contains a tests or fixtures component. An empty script root selects root-level
shell files; other configured script roots recurse. Duplicate method identities
are rejected, while helper/script paths are deduplicated and sorted.

Method records contain source locations, top-level module imports, assertion call
names, subTest keyword names and negative-case hints from selected name prefixes
and assertion names. AST walking also sees calls nested inside a method's body;
it does not prove those calls execute. Imports inside functions/conditionals and
relative import levels are not fully represented. These are review hints, not
behavioral laws or a test-strength score; subtest values and assertion operands
are not captured.

The [rules](../../../artifacts/extraction/semantic-test-migration-rules.json)
assign legacy modules to provisional issues/distributions, optionally choosing a
different mutable-only target. Rule decoding closes the declared field sets,
bounds selected text to 1024 UTF-8 bytes, requires positive issue integers and
rejects cross-assignment module ownership overlap. It does not consult GitHub or
establish that a target owns the behavior. Building requires assignments to match
the mutable legacy module set exactly and rejects missing/stale modules, wrong
lane sets and colliding method identities.

Each reference test must have a scanned frozen method, a target and a test entry
in the parity manifest. Reference target lookup uses the first two dotted name
components as the legacy module. Current successor candidates are all current
methods with the same method name, regardless of module, class, target or body.
Mutable-only entries are selected by identity absence from the reference list;
changes to an existing identity's body are not a separate migration entry.
Legacy helpers/scripts receive provisional issues, and demo script names add
optional demo links. None of these assignments grants parity completion.

The builder hashes canonical JSON for the entire supplied manifest, but counts
recorded successors/supersessions only in its test-entry index. Those counts use
record presence, not passing evidence or migration-state acceptance. Input schema
tags are checked without invoking the reference, manifest or demo modules' full
decoders; duplicate manifest references can overwrite in that index, and matching
reference coordinates/laws across inputs are not comprehensively established.

decode_migration_inventory closes top-level fields and checks selected unique
identities, list counts and aggregate provisional-target totals. It is not a
recursive closed codec: nested records, digest/source provenance and per-target
membership are not fully validated. For example, counts.mutable_only_methods is
not compared directly with its list length. Decoders return the supplied mutable
objects, and malformed inputs are not universally converted to the custom error.

Source and JSON reads, AST traversal and output serialization have no overall
size/depth limit or secret-redaction pass. Direct callers choose filesystem roots
without a containment/symlink policy. The CLI writes the entire inventory through
a fixed sibling .tmp and os.replace, with no backup, fsync, concurrent-writer
isolation or exceptional temporary-file cleanup. The
[builder wrapper](../build-semantic-test-migration-inventory.sh.md) supplies
archived trees but mounts the current scanner and artifact inputs separately.

The stored [inventory](../../../artifacts/extraction/semantic-test-migration-inventory.json)
records 1090 reference laws, 120 mutable-only methods and 1226 current methods.
These are historical inventory counts, not successful test executions. The full
765-line module and [313-line test owner](tests/test_migration_inventory.py.md)
were read, with full rules and selected artifact metadata/records. No inventory
builder, test suite or artifact rewrite ran during this documentation review.
