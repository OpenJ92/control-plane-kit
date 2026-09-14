Source: [extraction_parity/tests/test_reference_parity_evidence.py](../../../../extraction_parity/tests/test_reference_parity_evidence.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These six tests exercise the [reference evidence module](../reference.py.md)
with synthetic log strings, resource names and identity values, plus temporary
JSON files. A successful summary preserves counts; failure-only text, a missing
Ran line and oversized text are rejected. A resource-set example returns only
the added name. No Docker resource is created or deleted by these test bodies.

Evidence construction rejects unequal supplied commits and preserves selected
summary, runtime, dependency and cleanup fields. Its accepted abc123 commit and
placeholder image/input hashes illustrate that this is not a digest-validation
test. Assertions about absent output/environment fields do not establish general
metadata secret redaction. The test named for atomic encoding checks repeatable
bytes, JSON equality and no remaining .tmp file after successful writes; it does
not inject interruption or concurrent writers.

The [wrapper](../../reference-test.sh.md) test checks literal archive/naming,
output-cap and volume-cleanup snippets and absence of four prune commands. It
does not execute the wrapper or prove that detached additions belong to its run,
that attached-volume refusal works at runtime, or that cleanup succeeds.
Contradictory/multiple summaries, unchecked counts and metadata, nonempty residue,
dependency-basename collisions and partial publication are also outside these
cases. Fixture count 1043 is synthetic, not the committed baseline's 1112.

The full 122-line test owner, 174-line implementation and 117-line wrapper were
read, along with the selected frozen scripts and historical baseline. No test,
wrapper, Docker action or evidence generation was executed for this companion.
