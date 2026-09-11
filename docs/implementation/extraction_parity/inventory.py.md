Source: [extraction_parity/inventory.py](../../../extraction_parity/inventory.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner converts unittest discovery into reference-law inventory data for
extraction work. [The shell entrance](../reference-inventory.sh.md) supplies the
frozen test image and current override file. collect_tests discovers tests from
the working directory, flattens suites, canonicalizes optional tests. prefixes
and groups duplicate references into one record with an occurrence count. It
does not run the resulting suite, but discovery imports modules and constructs
test objects; module/load_tests code can execute. This is not a pure source scan.

Default law IDs derive from the test method name rather than module/class paths.
Repeated method names across distinct references require an override for each
ambiguous reference. Used overrides must match the behavior namespace grammar;
the current [override input](../../../artifacts/extraction/law-overrides.json)
disambiguates bulkhead/cache and idempotency/webhook pairs. An unused override
is not rejected as stale. These are curated naming identities, not inferred
equivalence of assertions or proof that a migrated test preserves behavior.

Dimensions are sorted keyword names found on subTest attribute calls, not their
values, number of cases or Cartesian combinations. `**kwargs` dimensions are
rejected. The live method path reads the method's source file and requires one
function definition with that name anywhere in its AST; it does not qualify the
match by class. Missing source-file information yields empty dimensions. Skip
metadata comes from method attributes; class-level and other runtime skip
mechanisms are not a complete part of that inventory.

Validation requires nonempty unique references/laws, nonblank recorded skip
reasons and positive occurrence counts. Descriptor output sorts records and
distinguishes total collection occurrences, unique laws and skipped records.
It does not fully validate arbitrary directly constructed values, attest Git
provenance or verify outcomes; reference tag/commit arguments need only be
nonempty here. The shell owns its separate identity comparison.

_write_json writes a shared suffix .tmp sibling then replaces the output. It
does not provide fsync durability, concurrent-writer isolation or guaranteed
temporary-file cleanup on error. Discovery, source reads, JSON and diagnostics
have no general size budget, and exceptions are not uniformly converted or
redacted. The inputs and imported frozen code remain trusted.

[Ownership classification](../../../extraction_parity/ownership.py) consumes
the reference/law/occurrence fields and carries source identity onward to the
parity manifest. The committed [reference inventory](../../../artifacts/extraction/reference-tests.json)
is a historical artifact, not a fresh test result or current-backend acceptance.
[Focused tests](tests/test_reference_test_inventory.py.md) cover naming and
descriptor laws but do not execute discovery of the entire frozen suite.
