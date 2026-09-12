Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_intent_store.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_intent_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These five PostgreSQL tests exercise protected effect-intent read-back, malformed
preimage/coordinate rejection, a SQL transport guard, current-row paging and a
selected unexpected-error boundary. They inherit the
[intent-store fixture](postgres_effect_attempt_intent_store_fixture.py.md), including
disposable-database setup, constructed approval/run history, runtime-level intents
and event/intent/attempt persistence in one unit of work. Constructing an attempt
also performs the inherited run request/plan lookup. This file uses actual SQL
when executed, but no tests or database calls were run during documentation review.

The first test creates a large intent using the fixture's bounded product-prefix
search, builds its matching attempt/intent record and commits the evidence chain.
It opens a separate psycopg connection, calls the actual store's get and closes
that connection in finally. The whole loaded record, intent and request fingerprint
must match the originals. This establishes a fresh-connection codec round trip
for that generated value, not a server/process restart or an exactly maximal
1,048,576-byte lawful payload. Size-threshold assertions for the builder live in
other tests, not this method.

assert_row_error requires OperationsRecordError and the exact message "effect
attempt intent row is invalid", then uses the inherited safe-error helper to
require no cause/context, combined str/repr at most 512 characters and absence
of the supplied nonempty canaries. It does not assert exact exception type. Only
callers of this helper receive all of those assertions; the transport test uses
a simpler exception assertion.

The strict reconstruction test commits the start event and intent row without an
attempt row. It reads the canonical preimage directly, replaces it with seven
bad byte forms and requires the row error on get: invalid UTF-8, a duplicate key,
NaN, a BOM, leading whitespace, trailing newline and a non-object array. Each
candidate is written through SQL and restored after its assertion. Some remain
parseable JSON; exact canonical bytes are still required. This is a get/codec
test, not a successful current-row scan of a complete attempt chain.

Three further cases construct lawful alternative intents rather than malformed
JSON: a foreign workspace, a foreign request and changed product content. Each
alternative must construct an EffectAttemptIntentRecord and survive the public
request round trip. The test stores its canonical bytes while arranging exactly
one mismatch among copied workspace_id, request_id and request_fingerprint.
Workspace/request cases also copy the alternative fingerprint; the content case
keeps the original fingerprint. A SQL read verifies the singleton mismatch before
get must reject. Original bytes/fingerprint are restored afterward. These cases
guard semantic agreement between the preimage and copied columns, not every
possible event/provenance mismatch or authorization of the alternative coordinates.

The actual [store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_intent_store.py)
joins intent rows to their exact original event coordinates, decodes canonical
intent bytes, reconstructs typed event/identity/evidence and compares copied
workspace/request/fingerprint values. The
[private codec owner](../src/control_plane_kit_operations/effect_attempt_intent_evidence.py.md)
rejects duplicate JSON keys and nonfinite constants, reconstructs the closed
intent and requires its canonical bytes to equal the stored document. get does
not run the scanner's final orphan check, which explains the separate event/intent
setup in the malformed-preimage method.

The transport test covers stored preimage lengths zero, exactly 1,048,576 and
1,048,577, each through get and _validate_current_rows. For every case it opens a
fresh transaction connection, drops the named
cpk_effect_attempt_intents_preimage_check and writes repeated x bytes. This
intentional transactional schema weakening allows otherwise inadmissible lengths
to reach the read boundary. finally rolls back those changes and closes the
connection; a rollback exception would prevent the subsequent close because the
two cleanup calls are sequential, without a nested finally.

During each read, the private decoder is replaced by a function that records its
input and raises OperationsRecordError. The ledger must have one entry of the
exact size for the inclusive maximum, and no entries for empty or oversized
preimages. Thus the maximum-length case proves admission to decoding, not that
repeated x bytes are valid intent JSON. These assertions accept the error base
class without requiring the categorical row message, bounded rendering or
chain-free behavior for this particular matrix.

The actual [SQL constraint](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
normally admits only byte lengths 1..1,048,576. Independently, the shared SELECT
uses CASE to return preimage only within that range and NULL otherwise. The row
decoder rejects non-bytes or an invalid size before calling the private codec.
The test therefore exercises defense in depth after deliberately removing the
write-time check; it does not show a normal insert can bypass that constraint.

_TransportConnection wraps the real connection/cursor. Its cursor records any
exact bytes value above the ceiling among fetched rows for queries whose text
contains the intent relation name. fetchone and fetchall are observed; fetchall
also records whitespace-normalized query text. The test requires an empty
oversized ledger. This observation is after driver fetching, not instrumentation
of network packets, driver allocations or all Python byte-like/nested values.
Query parameters and each page's row count are not recorded by the wrapper.

The paging test commits 17 indexed chains, runs current-row validation and
requires three recorded fetchall queries mentioning the intent relation. Each
must contain LIMIT %s and omit OFFSET; queries after the first must contain the
tuple keyset comparison. The inspected implementation binds a batch size of
eight, starts at the empty run/activity cursor with attempt zero, decodes rows in
order and advances to the last row's identity. Those source facts explain the
expected three fetches; the test does not directly inspect the bound limit or
assert page row sizes.

It then corrupts activity 008 to empty-object bytes and requires the row error
after two page fetches. Next it corrupts activity 016 and restores 008, requiring
failure after three fetches. This checks reach into the second and third pages
and failure at the selected corrupt row. The final corruption remains until
inherited teardown. Empty scans, exact-multiple page counts, mixed runs,
concurrent mutations and every row position are outside this test's examples.

After successful decoding, the actual scanner performs a separate fetchone
orphan query matching attempt identity, request fingerprint and original event.
It is not included in the test's fetchall-query counts. The complete 17-chain
fixture permits that check to succeed, but this method does not remove an attempt
or otherwise isolate the orphan rejection law. Paging bounds each preimage batch;
the test does not establish a global row/time cap or a snapshot under concurrency.

The final test inserts an event and intent in a unit of work, asserting exact
EffectAttemptIntentRecord return type and value equality. It requests no commit,
then persists the same full chain through the fixture helper. The actual
[unit of work](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
rolls back the first scope on exit; the sequence relies on that rollback, without
an explicit row-absence or failure-injection assertion. Adapter acknowledgement
is not evidence that the first scope committed.

It then patches the private decoder with specific TypeError and RuntimeError
instances and requires each to escape get as the same object. Despite the test
title's driver-fault wording, these are injected codec-dependency failures, not
database-driver exceptions. The inspected store translates OperationsRecordError
from the codec into its row error but leaves these unexpected exceptions outside
that catch. The test does not promise redaction of their canary messages or extend
that identity guarantee to every failure elsewhere in row reconstruction.

Security/data evidence is canonical protected evidence, copied-column agreement,
bounded preimage retrieval and selected error handling. Synthetic secret and
authority references are never resolved here. Autocommit corruption writes and
temporary schema changes are test apparatus, cleaned by restoration/rollback or
inherited teardown; no provider effect, authentication, deployment restart or
live resource cleanup is exercised.

Read depth: all 375 test/helper lines; retained full fixture and private evidence
owner review; actual store SELECT/decoder/scanner and error catches, named DDL
constraint and unit-of-work behavior. This is source-level documentation, not a
new test run. No imports, database connections, source edits or provider actions
were performed while authoring it.
