Source: [extraction_parity/manifest.py](../../../extraction_parity/manifest.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner defines the parity ledger's structural language and initial builder.
decode_manifest requires exact root/reference/entry shapes, known kind/owner/state
values, unique (kind, reference) identities and globally unique laws. Named text
fields are nonblank and capped at 512 UTF-8 bytes. Successors have closed
id/status/evidence records; a supersession instead requires five explanation/
review fields and cannot coexist with successors. The decoder returns the same
dictionary, not an immutable value or detached copy.

These are structural checks. A passing successor string is not verified test
execution, its evidence field is not parsed as an artifact digest here, and a
review field does not authenticate a review. Commit/law strings are bounded text,
not necessarily full Git IDs or behavior-namespace identities. There is no
general document-size bound, complete normalization of arbitrary malformed
Python inputs or uniqueness check for successor IDs within an entry.

build_manifest consumes [law ownership](ownership.py.md) and the
[reference demos](../../../artifacts/extraction/reference-demos.json), requiring
their schema labels and reference identities to agree. Tests retain law/owner
fields and begin deferred only for deferred-product owners; demos use their ID
as the law and copy bootstrap_state. The builder sorts entries by kind/reference.
Occurrence counts, dimensions, skip metadata and demo scripts/prerequisites/
observables/cleanup details are not carried into this ledger; their input owners
remain necessary context.

Every newly built entry starts with empty successors and null supersession.
The builder never reads an existing ledger or merges later evidence into it.
Consequently, [the build wrapper](../build-parity-manifest.sh.md) replaces existing
successor and supersession records when it writes over parity-manifest.json.
Initial construction and maintenance of a populated ledger are different actions.

write_manifest revalidates and serializes sorted object keys, creates parents,
writes a fixed .tmp sibling and replaces the destination. It preserves the
supplied entry-list order rather than imposing the builder's ordering on arbitrary
input. There is no backup, fsync guarantee, writer isolation or guaranteed .tmp
cleanup on failure. This writer is a durable artifact mutation, not execution
of the migrations or demonstrations described by the data.

[Parity validation](../../../extraction_parity/validation.py) builds an expected
initial mapping for comparison without replacing the supplied completed ledger.
It separately checks mapping coverage and declared successor evidence under
foundation or migration-complete policy. Matching evidence documents still do
not rerun tests. [Focused manifest tests](tests/test_parity_manifest.py.md)
protect selected structural and ordinary-write laws; a decoder pass alone is
not migration completion, review authenticity or live acceptance.
