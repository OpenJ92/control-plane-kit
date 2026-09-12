Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_fold_concurrency.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_fold_concurrency.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These five tests arrange specific overlapping PostgreSQL transactions around the
real fold service. They inherit the [fold fixture](postgres_effect_attempt_fold_fixture.py.md)
and use real connections, thread futures, backend PIDs and controlled ID allocation
to force selected winner orders. They are not provider execution, cancellation,
arbitrary scheduling or process-restart tests. The fixture's database belongs to
the owning Docker-backed package suite; this companion does not authorize running it.

_require_transaction_stage first executes a successful fold and resets fixture
truth. Thus each test depends on a working transaction path before arranging its
race, and its total execution is not just the subsequently instrumented calls.
Foreign run/attempt helpers seed selected unrelated truth. Expected IDs come from
fold_ids, which expands an event ID with one ID per outcome endpoint; recovery can
use only the event ID. These are synthetic fixture identities, not runtime constants.

_BlockingId increments calls and signals entered on its first allocation, then
waits for release for at most ten seconds. Later allocations return the remaining
predeclared strings without blocking. It is used by one worker per instance and
does not implement a general synchronized ID allocator. The service reaches it
after locked reads, fresh observation and ordinal selection, before event writes.
The imported Sequence records values it returns; a descriptive must-not-allocate
label is not itself a call-count assertion.

The worker connection factory sets lock_timeout to ten seconds and statement_timeout
to twelve, queues its backend PID and returns a PostgresUnitOfWork. The blocking
probe polls pg_blocking_pids until the specified PID appears or a five-second
monotonic deadline is exceeded. It has no sleep, and that deadline is checked
between SQL calls, not an independent timeout on the polling query itself.
Backend blocking attribution is stronger than merely observing an unfinished
future, but establishes only the arranged relationship at that point.

The first test independently blocks request, run or attempt. With the worker
verified blocked by that transaction, separate FOR UPDATE NOWAIT probes establish:

- While request acquisition is blocked, the selected run and attempt remain lockable.
- While run acquisition is blocked, the request is retained and the attempt remains lockable.
- While attempt acquisition is blocked, request and run are retained, while the
  selected foreign attempt remains lockable.

The blocker is then rolled back and the worker must return NewlyFolded. These
three worlds support request-to-run-to-attempt ordering and one unrelated-attempt
freedom assertion. They are not an exhaustive lock graph, absence of all foreign
locks, or a proof of every deadlock possibility. The actual
[interpreter](../src/control_plane_kit_operations/effect_attempt_fold_interpreter.py.md)
requests these locks in that order, and the
[attempt adapter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_store.py)
uses identity-scoped FOR UPDATE and full-prior-state compare-and-set.

Identical folds are arranged twice with left/right labels. The first submitted
worker pauses at its first ID; the second is proved blocked by its PID before the
first is released. Exactly one NewlyFolded and one ExistingFold must result, with
equal attempts and outcome records. A synchronized wrapper counts one call to the
real lease observer; the first allocator count must equal the event-plus-endpoint
ID count and the second Sequence must remain empty.

The identical-fold test title mentions one clock, ID, event and CAS. The actual
assertions count lease-observer calls and planned IDs, not database clock invocations,
event append calls or CAS calls independently. There is no post-race event-count
query in that method. Left/right changes labels, not which submitted worker wins.
The source's exact-replay branch explains skipped write work; other tests provide
separate ledger and persistence coverage.

Two incompatible pairs are exercised in both submission orders: succeeded versus
failed, and recovered-succeeded versus abandoned. After the same PID/blocking
handshake, the first must be NewlyFolded and the second must raise EffectAttemptFoldConflict.
The loser's Sequence stays empty and a typed current-attempt read equals the
winner. This is four selected outcomes, not every pair of transitions. The method
does not assert exact conflict text, failure redaction, outcome/event counts or
an independent complete aggregate snapshot.

The direct-fold/claim-rotation test forces two orders using raw SQL that changes
the request to worker-b/generation 8 with fixed future lease times. If the fold
holds the request first, the update is shown blocked, then both operations complete
and the fold must be NewlyFolded. If the update holds it first, the old command is
shown blocked until rotation commits, then must raise Denied with the fixed
authority message. A sentinel forbids lease sampling in that stale path. Its ID
Sequence is not retained for an explicit empty-calls check. No final claim/attempt
snapshot is asserted in this test, and the SQL update is not a call to the public
claim-rotation service or evidence of its policy checks.

The recovery/rotation test similarly covers recovery-first and rotation-first.
After recovery-first and rotation commit, a command using the new current claim
must replay the same attempt with no direct outcome, under a no-lease-sampling
sentinel. In rotation-first, the stale command is denied before lease sampling,
its retained Sequence is empty, and a new-current-claim command must then return
NewlyFolded. The stale recovery denial is checked by class, not exact message or
safe-error helper. These paths distinguish current claim authority from preserved
historical attempt fencing; they do not create recovery policy or prove provider
evidence. Actual Core/Operations transition validation remains authoritative.

Finally blocks release ID barriers, roll back/close blocker connections where
present, and shut down executors with wait=True/cancel_futures=True. Cancelling
pending futures does not forcibly cancel already running SQL. Future wait timeouts
and worker SQL limits do not establish a hard whole-test duration: shutdown waits,
and manually opened rotation/probe connections do not receive the worker factory's
same timeout settings. Sequential rollback/close can mask a prior error or skip
later cleanup if it fails; some setup precedes the try/finally region. No stronger
crash-safe cleanup or universal cancellation guarantee is claimed.

The [PostgreSQL UoW](../src/control_plane_kit_operations/postgres/unit_of_work.py.md)
commits physically on successful context exit and releases transaction locks there.
The [execution adapter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
owns request-scoped run locking, database lease observation and ordinal locking.
The [authority tests](test_postgres_effect_attempt_fold_authority_errors.py.md)
cover additional categorical boundaries. This file's forced interleavings are
selected durable evidence, not a new implementation of serialization or proof
that no future source change can introduce a different lock path.

Read depth: all 474 source lines, five tests and local helpers; retained full
567-line interpreter, reviewed fixture companion and actual selected inherited seeding,
ID/current-attempt helpers, Sequence, execution locks/observer/ordinal, CAS and
complete UoW. No imports/tests, database connections, Docker, provider actions or
source edits ran while authoring this companion.
