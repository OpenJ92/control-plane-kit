Source: [extraction_parity/ownership.py](../../../extraction_parity/ownership.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner assigns a future migration destination to each frozen reference law.
The four OwnerKind values distinguish Core, hello, deferred product and system
work; they are migration labels, not access permissions, current module ownership
proof or evidence that a product migration is complete. The actual
[rules](../../../artifacts/extraction/ownership-rules.json) identify explicit
hello/system modules, named deferred products and guarded product vocabulary.

classify_module counts explicit membership across hello, system and deferred
product lists and rejects overlap for the queried module. Otherwise, an explicit
owner wins; only an unassigned module reaches the guarded-substring check before
defaulting to Core. A product-like module omitted from both explicit rules and
guard vocabulary can still default to Core. Lists become sets, so duplicate list
items are collapsed rather than reported. Rule checks occur along the lookup
path, not through a complete closed-schema or unused-rule audit.

classify_inventory checks the reference-inventory schema and selected record
shapes, then uses the second dotted reference component as the module key. It
does not discover source imports, inspect assertions or infer ownership from
runtime behavior. This assumes the inventory's flat tests.module.Class.method
organization; deeper nesting does not change the chosen second component.
Reference identity, law and collection occurrences are copied; dimension and skip
details are omitted. The original total count is passed through, while law_count
and owner_counts count records, not weighted occurrences.

The function does not rerun [inventory validation](inventory.py.md), reject every
duplicate law/reference or reconcile the supplied total with occurrence sums.
Malformed trusted input can raise ordinary lookup/type errors outside
OwnershipError. The enum is closed, but that is not a blanket guarantee about
the input JSON. write_ownership creates parent directories and replaces the
output through a fixed .tmp sibling, without fsync, writer isolation or guaranteed
temporary-file cleanup on failure. There is no general input/output size budget.

[Manifest construction](../../../extraction_parity/manifest.py) requires matching
test/demo reference identities, preserves owner fields and maps deferred-product
to deferred migration state while other test owners begin required. Its later
decoder imposes additional uniqueness/shape rules; classification alone does
not provide that stronger contract or passing successor evidence.

[The focused tests](tests/test_reference_law_ownership.py.md) cover representative
assignments and guarded fallback. The committed
[ownership artifact](../../../artifacts/extraction/reference-law-ownership.json)
is historical classification data, not regenerated or current execution evidence.
