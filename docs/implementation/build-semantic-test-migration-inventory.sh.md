Source: [build-semantic-test-migration-inventory.sh](../../build-semantic-test-migration-inventory.sh).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This wrapper archives selected source trees and invokes the
[migration scanner](extraction_parity/migration_inventory.py.md) to replace the
selected inventory file. External repositories default to sibling checkouts
derived from the root repository's Git common directory; environment overrides
can choose repository paths, source refs and output. It requires those Git objects
locally and does not fetch missing repositories or run their test gates.

Defaults select frozen reference 20129959d3b0f8e8bd5dbdafdf51c0a5d592a9ec,
coordination f45384e72a79f59c93a715fd08f409f86a91218a,
Interpreters 2335a21adc5c0b0ae2f592bd15757c6ca1a55e4b,
Servers 43e9f359ca828c83fe4994ed1b62e1be54277ddd and
Secrets 96e86dc3248d578780d64d5d7fc5d6359631d1d6. The coordination snapshot
supplies mutable legacy, Core and Operations lanes. These selected historical
coordinates are not automatically advanced to current package heads.

The reference tag is checked against an overridable expected commit. Other refs
are resolved for the source-commits document without an independent expected-head
check. Archives use the original refs again, rather than the resolved values;
symbolic overrides therefore assume ref stability between resolution and archive.
The scanner receives the generated declarations but does not verify Git content
against them itself.

The container mounts archived trees and source-commits read-only, alongside the
current working-tree scanner and four current artifact inputs: reference tests,
parity manifest, reference demos and
[migration rules](../../artifacts/extraction/semantic-test-migration-rules.json).
Those inputs are not frozen with the source snapshots. python:3.14-slim is a
mutable image tag, and the invocation does not disable Docker's default network.
The output directory is writable as a whole; the scanner writes the chosen file
using a fixed .tmp sibling and replacement, not a merge with existing records.

This performs local filesystem/Git archive and Docker process effects even though
test bodies are only parsed as AST. It does not execute the listed package gates,
prove candidate equivalence or mutate the parity manifest. The default output is
the [stored migration inventory](../../artifacts/extraction/semantic-test-migration-inventory.json).
A failure can leave its prior contents intact. There is no overall timeout or
transaction spanning input selection and output publication.

The EXIT trap deletes this invocation's temporary tree; --rm requests container
removal. It does not remove the Python image or audit every possible residue.
Concurrent calls can share the output file and its fixed temporary sibling.
The [validation wrapper](validate-semantic-test-migration-inventory.sh.md) directs
a rebuild to a temporary output and compares it with the stored artifact.
The full 90-line owner was read, with scanner/test owners, selected input records
and all rule assignments; no build, scan or artifact replacement was executed.
