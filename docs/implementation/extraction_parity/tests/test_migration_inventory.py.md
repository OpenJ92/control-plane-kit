Source: [extraction_parity/tests/test_migration_inventory.py](../../../../extraction_parity/tests/test_migration_inventory.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

Rule tests exercise a separate mutable-only target, reject extra rule/target
fields and reject a module assigned to two issues. They also show that
decode_rules returns the same dictionary. These are synthetic assignments, not
checks that the referenced GitHub issues exist or own a particular law.

A temporary source tree checks configured test/helper/script roots and excludes
a shell file under an unconfigured src directory. Its class need not inherit
unittest.TestCase to be scanned. An AST fixture checks missing/reject name hints,
assertRaisesRegex and the case subtest keyword, without executing the fixture.
This protects source recording, not runtime test discovery or negative-case
behavior.

The decoder fixture intentionally supplies minimal nested records and no source
lanes, then changes current_methods to test count-drift rejection. Acceptance of
that fixture is not evidence of full nested-schema or provenance validation.
The build fixture supplies seven synthetic lanes, one reference and a current
method with the same name. It checks a provisional target, candidate identity,
legacy import projection, a mutable-only target override and stale/missing module
assignment rejection.

The [implementation](../migration_inventory.py.md) has broader branches than
these cases: inherited/dynamic tests, source/path limits, conflicting input
identities, most decoder inconsistencies and output-write failure/concurrency
are not exercised. Neither shell wrapper is executed or inspected by this file.
The full 313-line owner and 765-line implementation were read; this documentation
review ran no tests, repository scan, builder or historical-artifact rewrite.
