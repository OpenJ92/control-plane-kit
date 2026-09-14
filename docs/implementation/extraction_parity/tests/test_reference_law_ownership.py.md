Source: [extraction_parity/tests/test_reference_law_ownership.py](../../../../extraction_parity/tests/test_reference_law_ownership.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

Three tests exercise [classification](../ownership.py.md) using a small local
rule dictionary. Exact LawOwner equality covers the default Core destination,
hello, system and one named deferred product. A guarded but unlisted module must
raise OwnershipError. A two-record inventory with three total occurrences checks
the preserved aggregate count, record count and resulting owner-kind set.

The test named for exactly one owner does not exercise overlapping rule lists;
nor does it assert every per-entry occurrence, owner count or output field.
Production rule-file coverage, malformed/duplicate inventory inputs, deeper
module paths, serialization and the Docker wrapper remain outside these three
cases. Preserve those limits when reporting what a green suite establishes.

The complete 53-line owner and actual rule input were read. These pure fixture
checks perform no frozen-test discovery or migration execution, and they do not
establish current product ownership. No executable validation ran for the note.
