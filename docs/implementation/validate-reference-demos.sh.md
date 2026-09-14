Source: [validate-reference-demos.sh](../../validate-reference-demos.sh).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This wrapper obtains two filename inventories from the selected Git tag. The
script set consists of entries with mode 100755, regardless of extension.
Fixtures use tests/live_*.py, examples/*_live.py and four explicitly named example
paths. These predicates define coverage; they are not automatic discovery of
every possible live helper. The whitespace-oriented script-list extraction also
assumes ordinary repository path names.

It writes discovery lists to a new temporary directory, mounts those lists and
the current checkout read-only in python:3.14-slim, and calls the
[current validator](extraction_parity/demos.py.md) against the working-tree
reference-demos.json. The validator runs no demo scripts. The container may
require an image pull, and the EXIT trap removes the temporary host directory;
this is not an entirely effect-free read even though it does not rewrite the
inventory artifact.

Unlike the reference-test inventory wrapper, this shell has no expected-commit
comparison. It reads the symbolic tag in two separate Git commands and assumes
it stays stable. The validator does not inspect the document's reference value,
so matching filename coverage does not verify its declared tag/commit against
the selected tree. The current Python code and working-tree JSON also need not
be committed or belong to that historical revision.

The final exhaustive-and-valid message means the selected filename sets and
document fields passed this validator. It does not establish that prerequisites
were available, observables held, cleanup succeeded or any current/live parity
gate passed. [Focused tests](extraction_parity/tests/test_reference_demo_inventory.py.md)
exercise the pure validator rather than this shell. No wrapper or historical
demo was executed during documentation review.
