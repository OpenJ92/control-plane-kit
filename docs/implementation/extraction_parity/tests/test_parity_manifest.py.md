Source: [extraction_parity/tests/test_parity_manifest.py](../../../../extraction_parity/tests/test_parity_manifest.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These tests exercise [manifest structure and writing](../manifest.py.md) with
small in-memory ownership/demo inputs and a temporary output directory. Cases
cover required/deferred entry acceptance, extra root fields, duplicate entries,
incomplete supersessions, successor field/status validity, successor/supersession
exclusion and a 513-byte ASCII reference-tag rejection. A complete supersession
fixture supplies all five explanation fields; no external reviewer is consulted.

The accepted successor uses sha256:abc as evidence text and the reference uses
commit rather than an actual Git hash. Those fixtures demonstrate this layer's
bounded-text contract rather than digest/provenance validation. The builder test
checks deferred/required state ordering for a Core law and deferred demo; it
does not test preservation of later successor evidence or every owner field.

The writer test repeats a write of an empty manifest, compares bytes and checks
that no .json.tmp remains on successful return. Despite atomic in its name, it
does not simulate interruption, competing writers, fsync failure or rollback.
Likewise, decoding the same dictionary is not a detached serialization round
trip or an immutability proof.

The complete 108-line owner was read. These tests do not run the Docker wrapper,
historical demonstrations, current successor suites or migration-complete
validation. No executable tests or artifact regeneration ran for this note.
