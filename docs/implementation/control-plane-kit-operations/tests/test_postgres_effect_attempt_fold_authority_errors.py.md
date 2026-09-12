Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_fold_authority_errors.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_fold_authority_errors.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These seven PostgreSQL tests distinguish current execution authority, historical
attempt lineage, missing truth and selected decoding failures at the fold service.
The [fold fixture](postgres_effect_attempt_fold_fixture.py.md) seeds ordinary
execution outcomes or a synthetic uncertain attempt for recovery; this file does
not iterate compensation or guarded provider-observation modes. The represented
runtime effect is not executed by a provider. Documentation review did not run
these tests or their database setup.

The direct-fold matrix begins with an attempt fenced to worker-a, generation 7.
Its exact current claim produces NewlyFolded. After the stored claim rotates to
worker-b, generation 8, the old command is denied with the exact authority-error
message. A command matching that new current claim instead receives the exact
incongruent-fold conflict: current authority is necessary but cannot directly
settle this still-started attempt under a different historical fence. Both
rejections forbid the database lease-observation method, check bounded chain-free
errors without the supplied worker canaries and preserve the selected snapshot.
The service helper's ID label says must-not-allocate, but this test does not
inspect an ID sequence; the label alone is not an allocation assertion.

Recovery uses a different lineage law. The five-case matrix first makes each
command's fence equal to the current stored claim. It accepts the original
worker/generation, a greater generation with the same worker and a greater
generation with a new worker. Each successful recovery preserves the original
attempt fence and consumes exactly its one event ID. An equal generation with
a foreign worker or a lower generation conflicts with the exact invalid-truth
message before lease observation or ID allocation, with the snapshot unchanged.
Those negative cases isolate historical lineage because the command already
matches the current claim. They do not test stale recovery authority or a
generation gap policy; generation 9 is simply greater than the original 7.

The recovery-replay test first commits one recovered-succeeded result, then
successively changes the current claim. A later worker-c/generation-11 command
returns ExistingFold(first.attempt, None). Equal-generation foreign ownership
and a lower generation still produce invalid-truth conflicts. Every replay
case forbids lease observation, allocates no IDs and preserves its pre-call
snapshot. Replay therefore remains subject to lineage checks even after a
terminal recovery result exists. This is repeated service execution with fresh
unit-of-work connections, not a process restart or an actual concurrent worker
rotation. The fixture changes claim columns directly.

Expiry is tested separately for succeeded and recovered-succeeded. The inherited
[start fixture](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_start_fixture.py)
sets lease_expires_at to a fixed date in 2000 while retaining the claim's current
identity and status. Each first fold succeeds; its exact replay returns the
original attempt and outcome record and leaves the selected snapshot unchanged.
The replay forbids lease observation but does not explicitly inspect ID calls.
These cases concern the unguarded execute path, not permission to start new work
or acceptance of an expired guarded observation.

The actual [interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
checks execution:operate scope and fence representability before opening the
unit of work, then locks request, request-scoped run and attempt in that order.
It checks their relationships and requires a CLAIMED request with a present
claim exactly matching the command fence. Direct settlement of a started,
unguarded attempt requires its historical fence; reconciled/abandoned transitions
allow a greater generation or the exact historical worker/generation. The core
fold receives the attempt's historical fence, explaining its preservation on
recovery. Exact replay is decided before lease observation. A fresh fold checks
that the observed request equals the locked request, but rejects expiry there
only for guarded execution. These are source explanations of the selected tests,
not claims that this file exhausts scope, fence-format or transition guards.

The missing-truth matrix supplies a missing request ID, a missing run ID, deletes
the existing attempt row or supplies a foreign activity ID. For changed outcome
identities it rebuilds ExecutionEffectOutcome together with its derived transition
and failure, keeping the command internally coherent. Each call must produce the
exact not-found category/message, omit the canary from bounded chain-free
rendering, allocate no IDs, avoid lease observation and preserve the snapshot
taken after arranging the missing truth. The original attempt is also read back
unchanged for missing-run and foreign-activity cases. The foreign activity has
no matching attempt, so this witnesses lookup failure rather than independently
isolating the interpreter's relationship guard against a wrongly returned record.
Deleting the attempt is fixture arrangement, not a service deletion capability.

The decoder matrix patches four adapter boundaries: locked request lookup,
request-scoped run lookup, locked attempt lookup and lease observation. At each,
ValueError and OperationsRecordError must become the exact invalid-truth conflict
without cause/context or the supplied canary, allocate no IDs and preserve the
snapshot. Earlier lookup failures additionally forbid lease observation; the
observation case leaves that method available as the injected failure seam.
These eight cases inject exceptions at adapter methods; they do not construct
eight malformed PostgreSQL rows or exercise each decoder's parsing logic.

At the same four seams, injected TypeError and RuntimeError must escape as the
identical supplied exception object. Those eight raw-error cases do not assert
redaction, snapshot equality, observation avoidance or ID counts. In particular,
they are not tests of actual psycopg/network errors or an assurance that arbitrary
internal error text is safe to expose. The selected expected errors are normalized
after their handlers have exited, which explains the categorical exceptions'
empty context as well as their fixed messages. Request/run/attempt helpers also
translate KeyError to not-found; the observation helper normalizes only the two
expected decoding error classes.

The last test patches the real observation method with a wrapper that first
performs its normal read/lock/time observation and then replaces requested_by
in the returned request. The service rejects this disagreement with locked truth
as invalid, without allocating IDs or changing the selected snapshot. This
isolates returned-observation equality, not a concurrent committed database edit.
The second part resets the fixture, persists a started attempt, then changes the
request to queued and clears all claim fields. It receives the authority denial
before observation and preserves its snapshot. Because both request status and
claim presence change, that case does not independently isolate either branch
of the current-authority guard. Its ID factory calls are not directly asserted.

The inherited [authority/error helper](../../../../control-plane-kit-operations/tests/effect_attempt_start_fixture.py)
constructs synthetic ExecutionWorkerAuthority values with execution:operate
scope and worker/generation fences. Its safe-error check requires no cause or
context, at most 512 characters in combined str/repr and exclusion of supplied
nonempty canaries. Exact categorical messages are asserted by this file. Some
canaries are labels rather than sensitive values actually injected into a record;
these are bounded-rendering checks, not comprehensive secret or log inspection.
No external authentication service or credential resolution is exercised.

Snapshot equality covers the fixture's selected request/event/action/run values,
attempt and protected intent columns, graph/projection/observation counts and
selected outcome/link columns. It is a sequence of queries, not a complete atomic
database image; counts omit in-place content changes and some outcome/observation
payloads are not compared. The inherited
[database fixture](../../../../control-plane-kit-operations/tests/execution_lease_recovery_fixture.py)
installs the schema, resets test workspace tables and manages connections. Scoped
mock contexts restore patched methods. This file adds no provider cleanup,
retry/recovery orchestration or live-resource lifecycle behavior.

Security and operational evidence is the separation of denied current authority,
incongruent direct settlement, invalid recovery lineage and missing durable truth,
plus selected no-allocation/no-observation witnesses and bounded categorical
errors. Success assertions here do not establish a complete event/outcome audit
trail; the [first/replay tests](test_postgres_effect_attempt_fold_first_replay.py.md)
and [rollback tests](test_postgres_effect_attempt_fold_rollback.py.md) describe
their own persistence and failure witnesses. No tests here independently exhaust
request/run identity mismatches, guarded expiry, permission-scope rejection,
actual concurrent rotation or raw-driver failure behavior.

Read depth: all 313 source lines and seven tests; retained full fold-fixture
review; actual interpreter dispatch, request/run/attempt lookup translation,
observation error translation, relationship/current-authority/transition guards
and selected inherited expiry, authority, error and database lifecycle helpers.
No tests, application imports, database connections, source changes or provider
actions were executed while authoring this companion.
