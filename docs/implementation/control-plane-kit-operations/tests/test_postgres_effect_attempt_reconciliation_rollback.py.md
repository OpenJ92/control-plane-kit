Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_reconciliation_rollback.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_reconciliation_rollback.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These four PostgreSQL tests check selected initial-read failures, committed
authorization evidence after partial failure, raw observer exceptions and failure
at the guarded-fold call boundary. Despite the rollback filename, no test invokes
the real guarded fold writer and then fails after its writes. The
[reconciliation fixture](postgres_effect_attempt_reconciliation_fixture.py.md)
supplies real setup/admission/authorization persistence and selected snapshots;
observation results and fold failures are doubles.

_FailingFold records the guarded command and immediately raises the supplied
exception object. It has no underlying fold service, unit of work, ID allocator
or persistence call. Every test supplies this double, so the fixture's default
real fold-service construction is bypassed. Unchanged fold history here proves
behavior around this raised-before-write boundary, not atomic rollback of event,
observation, outcome and attempt writes.

The initial-read matrix injects KeyError into request locking, lease observation
and intent lookup in three independently seeded ordinary success worlds. Missing
request truth must become the fixed reconciliation not-found error; missing lease
or intent truth must become the fixed invalid-truth conflict. Each message is
checked exactly, with no cause/context, and complete_reconciliation_snapshot must
equal its before value. The patched method raises instead of doing its actual
read; earlier unpatched reads may still acquire locks. This matrix does not
exercise a write followed by failure, malformed-return admission, raw read errors
or every initial lookup boundary.

Those rows construct RecordingObserver(story.value) and a failing fold inline,
without retaining or asserting their call lists. The snapshot equality and error
categories are the explicit evidence; no dedicated observer-call or authorization
spy is installed. Actual
[reconciliation source](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation_interpreter.py)
raises these categories before leaving initial validation for fresh authorization
and observation. Its initial locked-read unit of work has no commit request.

The partial-authorization test seeds remote authority, requires at least two
required-use pairs and admits the provider/references before reconciliation.
A wrapper counts authorize_resolution invocations: the first delegates to the
actual service, the second raises SecretProviderRegistrationError before calling
that service. The resulting fixed authority denial must have no cause/context,
both retained observer/fold call lists must be empty, exactly one selected
authorization row must remain, and non_advancement_snapshot must match its before
value. The second authorization does not enter its own real transaction or write
anything; this is a committed prefix followed by rejection, not a second write
rolled back midway.

The actual
[authorization service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/secret_providers.py)
checks SECRET_PROVIDER_USE and canonical time, locks correlation and active
reference/provider admission, checks the permitted reference/intent and returns
congruent existing evidence or inserts new evidence. The inspected
[authorization store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/secret_provider_store.py)
performs the INSERT without committing itself. authorize_resolution constructs a
grant, requests commit and exits its own unit of work before returning it.
Provider admission and each reference admission are likewise separate setup
transactions. None is enclosed by a later fold transaction, and reconciliation
does not compensate already committed authorizations when a later step fails.

The one-row assertion is a count from the clean fixture world. It does not compare
the remaining row's identity, reference, intent or fingerprint against the first
enumerated use, nor does it assert the wrapper's final call count. Source ordering
and the forwarding wrapper explain which authorization commits. The accompanying
non-advancement snapshot covers surrounding orchestration state rather than
attempt/event/outcome history, so it is narrower than complete_snapshot.

The observer-fault test admits all remote uses, then has RecordingObserver raise
one RuntimeError object. The identical object must escape, the retained fold
call list must stay empty, complete_snapshot must remain equal, and the number
of selected authorization rows must equal len(uses). It establishes retained
authorization evidence alongside unchanged selected attempt/history truth after
an observer failure. It does not assert observer call count or compare each
authorization's contents. The actual observer call occurs after initial reads and
individual authorization transactions have exited, outside the block that
normalizes invalid observation results.

The final four rows separately reseed ordinary success truth and inject fold
denial, fold conflict, TypeError and RuntimeError through _FailingFold. Denial maps
to the fixed reconciliation authority error; conflict maps to the fixed
incongruent reconciliation error; both require no cause/context. The raw exceptions
must escape by identity. Every row requires exactly one fold-double call, unchanged
complete_snapshot and len(uses) authorization rows. The test does not retry the
same invocation, execute any fold store writer, compare grant identities or test
a commit/rollback exception. Its no-duplicate-persistence name should not be read
as a retry/idempotency or partial-fold-write rollback law.

The actual
[fold interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
would plan a result, write event, endpoint observations, outcome and attempt CAS,
check exact acknowledgements and request one commit. The actual
[PostgreSQL unit of work](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
commits only on a successful exit with commit requested, otherwise rolls back,
and closes in finally; a commit exception triggers a rollback attempt. Those
paths clarify the transaction ownership but are not exercised by _FailingFold.
This file supplies no ledger, connection-method spies, post-write fault injection,
concurrent schedule or restart probe to independently establish those properties.

complete_snapshot combines inherited attempt and non-advancement selections.
It includes request-a claim timestamps, selected events/actions/runs, attempt
columns and intent preimages, graph/projection values or counts, observation
count, selected outcome columns and outcome-observation memberships. It omits
full observation payloads, full outcome preimages and runtime/secret-provider
registration tables. Authorization evidence is also excluded unless the initial
matrix uses complete_reconciliation_snapshot, which adds authorization_rows.
That query selects twelve columns from all authorization rows but omits provider/
reference registration IDs, intent fingerprint and probe ID. Later tests compare
only its row count. These multi-query snapshots are not atomic full-database
images, and equality cannot exclude changes to omitted fields or payloads.

The fixed-domain-error rows establish selected message/category and exception-chain
boundaries; raw observer/fold identity preservation is intentionally different
and does not establish universal sanitization of arbitrary provider messages.
All runtime observations are synthetic. Real mutations are confined to fixture
setup and actual admission/authorization paths when the tests run, using secret
references rather than resolving credential bytes. Inherited reset/teardown
cleans test tables, so execution belongs in the isolated PostgreSQL test context.

Read depth: the complete 205-line source, all four tests and local failure helper
were read, together with the complete reconciliation fixture and relevant inherited
snapshot/reset helpers. Selected actual initial-read/authorization/observer/fold
error boundaries, authorization service/store, real fold writer and unit-of-work
contracts were checked. This is not a full review of those owners or adjacent
rollback suites. Companion validation was limited to local links, whitespace and
frozen-source comparison; no application imports, tests, database/provider calls,
credential access, source/inventory changes or publication were performed.
