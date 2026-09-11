Source: [control-plane-kit-operations/tests/test_authoritative_run_identity.py](../../../../control-plane-kit-operations/tests/test_authoritative_run_identity.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These 12 tests protect canonical run identity at record, command, store-selector,
generated-ID and replay-payload boundaries. They invoke actual validators,
PostgresExecutionStore methods and RunLifecycleCommandService logic using scripted
connections/stores. No database connection is opened, schema installed or durable
journal written. The separate journal assertion concerns preservation of one
run-ID string, not reconstructed execution history or saga correctness.

The source of the grammar is public Core
[RunId](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py),
which delegates to the private canonical predicate: exact str, ASCII alphanumeric
first character, then ASCII alphanumeric/dot/underscore/colon/hyphen, at most 200
characters. Operations translates rejection to its own boundary errors. The shared
INVALID_RUN_IDS matrix contains 45 candidates: object, bool, a str subclass, empty
and whitespace strings, four invalid leading punctuation forms, slash/space
canaries, all 32 ASCII controls plus DEL, and length 201. Positive examples are a
single character and 200 repeated letters; the test does not independently enumerate
every permitted punctuation or Unicode rejection case.

The first test applies that matrix to three
[record paths](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py):
ActivityRunRecord.run_id, a retry's prior_run_id and ActivityEventRecord.run_id.
It also constructs start, pause, resume, complete, fail and cancel commands with
each candidate. Record failures must be OperationsRecordError and command failures
InvalidOperationCommand. The valid examples must construct successfully across
all nine factories. This is shared admission behavior, not proof that all six
lifecycle transitions execute successfully or that a prior run exists in storage.

The error helper requires no cause or context and at most 512 characters for the
combined str and repr, excluding supplied canary substrings. Some candidates have
no canary; the bound and chain assertions still apply. The retry/journal test
separately rejects a run naming itself as its prior run, then passes a STEP_STARTED
record with a 200-character run ID through
[activity_journal_events](../src/control_plane_kit_operations/activity_journal.py.md)
and checks that the first projected event preserves that exact ID. It does not
assert output length, all copied fields, event-kind coverage, ordinal ordering,
plan linkage or saga state. The actual mapper performs the representation change;
this single assertion must not be promoted to a full journal-history law.

The store-selector test calls the actual
[execution store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
through five direct methods: get_run, get_run_for_update, compare_and_set_run_status,
next_event_ordinal and events_for_run. Two additional paths validate event-page
scope run_id and run-page cursor item_id. The page requests deliberately use
SimpleNamespace objects rather than full public request constructors, testing the
store's own guard. Invalid direct selectors raise OperationsRecordError; invalid
page identifiers raise ReadPageError. A recording fake connection must receive no
execute calls for every invalid candidate.

For the two valid boundary examples on all seven paths, the fake connection raises
a preconstructed psycopg.OperationalError at execute. The test requires that exact
exception object to propagate. This distinguishes valid admission from successful
query execution: there are no rows, SQL parser, real locks or pagination results.
It also explicitly permits raw operational failure rather than claiming every
driver error is redacted into an identity error.

_Trace provides a fake UoW and both execution/history store interfaces on one
object, records method calls and returns prescribed typed requests, runs, events
and action values. commit and __exit__ only append log entries; writes return their
arguments. It does not implement transactions, rollback, isolation, ordinal
allocation or locking. Placeholder times such as claimed/lease/occurred are local
record inputs, not database-timed lease evidence. Its worker authority has the
operate scope and a generation-1 fence where needed; the fixture is not an
authentication or lease-expiry test.

The claimability test makes the second request read return cancelled or already
claimed state and requires conflict before run enumeration, ID generation or
claim_request. A separate existing-run response rejects before ID generation and
claim_request. These cases test the service's ordering around a scripted reread,
not simultaneous database claims. The actual
[lifecycle owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py)
performs a locator read, action-idempotency lookup, open-session lock request and
fresh request read before checking claimability and existing runs. Its first fresh
request reread is get_request; the fake trace must not be described as an observed
database FOR UPDATE lock at that position.

The generated-ID test feeds every invalid candidate as the first factory result.
It requires exactly one factory call, an exact trace slice placing runs_for_request
before that call, and no claim_request, add_run, add_event, add_action or commit.
Both valid boundary IDs must emerge unchanged in the returned run. Another test
has the factory raise a preconstructed RuntimeError and requires the same exception
object with none of those mutation calls. These are before-mutation guarantees
for this scripted call surface, not rollback or real database failure evidence.
Invalid second/third factory values are not covered by the run-ID matrix.

The valid-claim test checks the entire call trace: UoW entry, request locator,
idempotency lock/lookup, session lock, request reread, run enumeration, first ID,
claim and run insertion, second ID plus event ordinal/insertion, third ID plus
action ordinal/insertion, commit and exit. It distinguishes the run, event and
action factory calls instead of treating all generated identities as interchangeable.
The mock logs show requested operations; they do not establish persisted run/event/
action atomicity or PostgreSQL lock order. Other than the selected returned run
checks, this test does not inspect every result field or serialized history record.

_replay_action manually constructs a CLAIM_RUN action with a matching SHA256 over
sorted compact JSON command/request/worker/duration semantics and a payload naming
request, plan, run, event, status/kind, ordinal and claim generation. This is
constructed replay evidence, not an action loaded from PostgreSQL. OperationActionRecord
requires a mapping but does not type each payload field; some adversarial Python
objects in the matrix could not originate unchanged from persisted JSON. Their
purpose is to test the service's defensive payload admission boundary.

The replay-ID test puts every invalid candidate into that payload, requires a
bounded RunLifecycleError before get_run and with no factory calls, then verifies
successful replay for both valid boundary strings using matching typed run/event
fixtures. Congruence tests separately mismatch the run's admitted request and the
event's run ID and require bounded failure without ID generation. Actual replay
checks more session/plan/type/status/ordinal/generation relationships, but these
two negative cases do not exhaust that full contract or prove the records were
written together. Run identity is a linkage coordinate, not authority on its own.

The missing replay-evidence test sets one switch that would make either get_run
or get_event raise KeyError with a canary. In this path get_run fails first, so
get_event is not independently exercised as the failing lookup. The assertion
checks absence of both canaries and cleared exception chains; it must not be
counted as two missing-evidence branch tests. A separate claim-replay case makes
get_request_for_update fail and requires RunLifecycleNotFound, zero factory calls
and no run/event/action insertion or commit.

The last test sends StartActivityRun through two fake failures: missing locator
get_run and missing locked get_run_for_update. Both must translate to bounded,
chain-free RunLifecycleNotFound with the appropriate canary removed. It requires
the trace to end at the failing lookup followed by UoW exit, not a real rollback
or lock-release observation. Together these tests distinguish expected missing
identity/evidence errors from the intentionally unwrapped factory/driver failures.

No test here proves actual durable history, database reconstruction, concurrent
allocation, worker ownership under a changing lease, retention/cleanup, process
restart or external execution. Security evidence is limited to exact identity
admission, early rejection, selected replay linkage and bounded expected errors.
Raw operational exceptions remain a separate caller concern.

Read depth: full 617-line test and complete fake fixtures; full Core RunId/private
predicate and 77-line journal mapper, with selected actual record validators,
lifecycle command/claim/replay/lookup paths, execution-store guards/selectors and
Core journal-event constructor. Retained fence/identity/journal context was reused.
No source/pin changes, executable tests, database setup, credential/private-key
access, provider/runtime actions or publication occurred. Documentation introduces
no new security surface and does not claim this suite ran during authoring.
