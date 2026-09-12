Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_store.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These eight PostgreSQL tests cover record reconstruction on new connections,
commit/rollback versus duplicate insertion, selected integrity constraints, exact
row locking, competing compare-and-set, stale prior values and decoder exception
translation. They inherit the
[store fixture](postgres_effect_attempt_store_fixture.py.md)
for destructive isolated setup, event/intent/attempt persistence and synthetic
replacement records. The tests use real PostgreSQL operations but do not execute
runtime providers, resolve credentials or exercise a complete activity fold.

The first matrix resets truth for each of eight stories in ordinary and
compensation phases, persists a fixture-built record with original/latest
ordinals ten/twenty and reads back an equal record in a later unit of work.
The second test does the same for a STARTED event whose ID is 512 ASCII characters.
Each unit of work opens a fresh connection; the database server and application
process are not restarted. These are selected cross-connection reconstruction
checks, not crash recovery, every Unicode boundary or independent transition
history verification. Expected records share the fixture's construction logic.

The rollback test inserts events, intent and attempt through real stores but
omits commit. A subsequent connection must get the fixed KeyError miss. It then
successfully persists the same record, and a direct duplicate insert_absent must
return None in a committed unit of work. This distinguishes real uncommitted-write
rollback from the primary-coordinate conflict branch. The duplicate call bypasses
the fixture's earlier event/intent writes; it does not prove persist is idempotent.
No assertion injects a physical commit/rollback failure or compares every table.

The predecessor test attempts to persist retry two before retry one and requires
a raw ForeignKeyViolation. After persisting the predecessor, the retry must
succeed. It then enumerates the table's physical columns and copies the first
attempt with a different activity ID while reusing its original/latest event
coordinates, requiring UniqueViolation. The selected
[schema](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
has predecessor, event and intent foreign keys plus unique event-role coordinates.
The tests assert exception classes, not a particular constraint name or diagnostic,
and do not prove all other integrity errors are translated or redacted.

The row-lock test persists attempts one and two in the same run/activity, with
attempt two referencing attempt one as predecessor. While a first unit of work
holds get_for_update on attempt one, a separate connection must successfully
SELECT attempt two FOR UPDATE NOWAIT and then receive LockNotAvailable when it
tries attempt one. This forces a concrete overlap and distinguishes those exact
rows; it does not establish absence of all table/metadata locks or independence
across every workspace/graph. The second connection is rolled back and closed in
finally; if rollback itself raises, its sequential close is not guaranteed.
NOWAIT avoids waiting for a conflicting row lock, not all connection/query delays.

The CAS race starts from a persisted STARTED record and builds success/failure
replacements. Both replacement events are inserted and committed before either
CAS runs. Two workers open their own units of work, set local lock_timeout to
ten seconds and statement_timeout to twelve seconds, then meet at a two-party
barrier before attempting the same prior state. The barrier has a ten-second
wait limit. Unlike unsynchronized task submission, this ensures both workers
reach the pre-CAS seam, though it does not force a winner or exact SQL ordering.

Results are awaited in submission order with a twenty-second timeout for each
future. On an exception the test aborts the barrier, cancels futures where
possible and waits for executor shutdown; it also aborts the barrier in finally.
Database statement/lock timeouts and the barrier bound specific waits, but future
cancellation does not stop already running work and these are not a single global
shutdown deadline. Connection establishment and other surrounding work are not
explicitly timed by those local database settings.

Exactly one CAS result must be non-None and equal one candidate. A later unit of
work retries the loser against the old prior, requires None, reads the current
record and requires equality with the winner. This combines a returned winner
with durable read-back and a stale replay miss. Both precommitted candidate events
remain outside the winner's CAS transaction; the test does not assert one event
in history, clean up the losing event individually or prove atomic event/outcome/
attempt folding. Replacement construction itself does not execute the Core fold.

The actual
[store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_store.py)
checks exact input record types and selected immutable coordinate agreement
between prior and replacement. Its UPDATE matches the three primary coordinates
plus all eighteen remaining physical columns using IS NOT DISTINCT FROM, then
returns the replacement or None according to RETURNING. It owns no commit and
does not run Core transition eligibility. The
[unit of work](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
commits only after successful exit with commit requested and otherwise rolls
back; this is the transaction boundary used by the workers.

The drift matrix starts from UNCERTAIN truth and prepares recovered success.
Its five rows change different sides of the comparison:

| Case | Change before CAS |
| --- | --- |
| prior-request | Supply an in-memory prior with a compensation-derived request fingerprint and refreshed event evidence; derive a matching replacement from that prior. |
| fence | Change the persisted row to worker-b/generation eight through SQL. |
| state | Persist another latest event and change the row to recovered-failed state, recovery values and latest-event coordinates. |
| prior-original-event | Supply an in-memory prior with an alternate original event and derive its matching replacement. |
| latest-event | Persist an alternate latest event and update the stored latest-event coordinates. |

All rows persist the proposed replacement's latest event before the comparison.
The prior-request and prior-original-event cases do not rewrite those persisted
prior columns; their supplied prior is deliberately stale. The alternative
original event is not itself persisted. Matching prior/replacement coordinates
let these candidates reach SQL rather than fail immutable-input comparison.

Each CAS must return None, request commit and leave the selected attempt's raw
SELECT * tuple equal before/after. This is a complete-column comparison of one
row, not a snapshot of event, intent or other tables. The five grouped cases are
not an executable one-column-at-a-time test of all eighteen prior columns.
The raw fence update does not rebuild the original/latest event commitments,
so unchanged raw-row evidence does not establish that the drifted row would
reconstruct as a valid typed record. These are CAS miss tests, not successful
reconciliation or repaired-history tests.

The decoder matrix first persists a STARTED record, writes a success event and
commits a real CAS to settled truth. Nested scalar and event helpers then replace
the store module's EffectAttemptState binding or PostgresExecutionStore.get_event
with functions that raise a selected exception. The event wrapper targets only
the chosen original/latest event ID and delegates other event reads to the real
method. These are collaborator exception seams, not corrupted SQL row values or
malformed encoded event payloads.

The matrix covers get and get_for_update, three boundaries and four error classes
for twenty-four combinations. ValueError and OperationsRecordError must become
the fixed row-invalid OperationsRecordError with no cause/context, bounded
combined str/repr and no decoder-canary. TypeError and RuntimeError must escape
as the identical injected object. Every case restores both patched bindings in
finally. The assignments affect process-global bindings and have no coordination
with unrelated threads; this fixture does not establish parallel patch isolation.

Actual row reconstruction creates typed identity/prior/recovery/fence/state,
loads both events, checks their coordinate triples against stored columns and
constructs EffectAttemptRecord. _decode_row catches only ValueError and
OperationsRecordError; the SELECT and missing-row branch occur outside it.
The injected matrix supports symmetric error behavior for these boundaries, not
every SQL exception, missing referenced event, malformed row shape or hostile
exact-type candidate. Raw exception identity is intentionally distinct from the
fixed safe-error rows and is not universal exception sanitization.

All persistence is fixture/test history: no actual provider outcome or authorized
live runtime action is established. Setup/reset/teardown truncate isolated test
tables; several cases intentionally commit extra events or raw row drift until
the next reset. The suite compares selected records/rows and categories, with no
whole-database cleanliness, descriptor migration, operational-history projection
or restart/adoption acceptance claim.

Read depth: the complete 488-line source, eight tests and nested worker/fault
helpers were read, along with the complete 126-line store fixture and actual
280-line store. Retained record fixture/owner context and selected current schema,
event adapter, intent persistence and unit-of-work dependencies were checked.
The separate pure store-contract suite is not claimed as fully reviewed here.
Validation for this companion was limited to local links, whitespace and frozen
source comparison; no application imports, tests, database/provider calls,
credential access, source/inventory changes or publication were performed.
