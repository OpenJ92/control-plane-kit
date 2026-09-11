Source: [extraction_parity/tests/test_parity_validation.py](../../../../extraction_parity/tests/test_parity_validation.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These tests construct small ownership/demo inventories and use the real
[manifest builder](../manifest.py.md) before exercising [parity validation](../validation.py.md).
The foundation case explicitly expects valid=true with incomplete required laws
and migration_complete=false. Migration policy requires completion. Negative
cases isolate missing/stale/reference/owner mappings, absent evidence, status
mismatch and aligned failed evidence; matching declared passing evidence permits
completion.

Evidence-index cases reject extra fields, duplicate IDs, unknown status and a
short digest. Accepted digests are synthetic repeated hex characters; no artifact
is opened and no test identified by successor.id runs. Supersession fixtures
provide complete explanatory text, not authenticated review. These cases prove
document consistency rules rather than real execution or digest provenance.

Core-closeout cases preserve required non-Core/deferred work, require Core
completion, accept explicit Core successors or supersessions and reject non-Core
supersession. Family-inventory tests fix deterministic grouping/order and reject
malformed entries or duplicate laws. A family projection can be valid even while
its input closeout describes unfinished work.

The complete 419-line owner was read. Tests mostly call pure functions; they do
not exercise the 16-MiB file-reader boundary, CLI/wrapper, report-write failures,
concurrent writers or fresh historical artifacts. Some negative checks compare
finding-code sets rather than every report detail. No executable validation ran
for the companion.
