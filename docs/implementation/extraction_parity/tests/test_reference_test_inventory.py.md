Source: [extraction_parity/tests/test_reference_test_inventory.py](../../../../extraction_parity/tests/test_reference_test_inventory.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These focused tests exercise [inventory values and helpers](../inventory.py.md):
optional discovery-prefix canonicalization, law identity based on behavior-named
methods, ambiguous-name overrides, sorted subTest keyword names, duplicate
reference/law rejection and descriptor ordering. A repeated-collection example
distinguishes three occurrences from one law rather than silently dropping the
extra collection count.

The descriptor test named closed checks selected schema/count/order fields,
not every key or every malformed field. The shell test checks source substrings
for a Git archive and test-stage build and excludes broad prune/editable-install
strings. It does not run the shell, resolve the frozen tag, build an image or
prove cleanup and archive identity under concurrent tag changes.

The complete 111-line owner was read. It neither calls collect_tests nor imports
the frozen test corpus. Whole-file method lookup, discovery errors, class-level
skips, `**kwargs` rejection, unused override behavior and output-write failure are
not all exercised here. Keep these unit laws separate from a real inventory
collection and from execution of the historical tests. No executable validation
ran for this documentation.
