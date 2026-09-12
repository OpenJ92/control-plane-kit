Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_start_intent.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_start_intent.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 578-line suite contains eleven unittest methods for start-intent admission,
event/evidence/attempt write order, full-evidence replay and rollback at selected
write boundaries. It uses actual PostgreSQL stores through the
[intent-store fixture](postgres_effect_attempt_intent_store_fixture.py.md), which
inherits the reviewed PostgreSQL start fixture. Its race-named final test is
sequential; the file introduces no threads, barriers, provider calls or process
restart. The main guard runs unittest on direct execution, which was not performed
during this documentation task.

Most tests call require_intent_store and require_intent_schema. The first checks
the captured store binding for presence; the second checks that the current
in-memory schema contract names the intent relation, not a live catalog query.
Inherited setup already installs schema and seeds predecessor truth. The imported
store_module binding is not otherwise used in this suite.

The stage-one test deliberately omits those two explicit presence checks, but it
still runs within the inherited database fixture. It builds a command and an
unpersisted attempt/intent-record pair, then compares record identity, intent and
fingerprint with the command. Direct queries confirm request-a/workspace-a and
run-a/request-a predecessor coordinates. It does not execute the start service,
persist that pair or prove that all optional schema/dependency surfaces can be
absent. The helper's record construction can itself read existing run coordinates.

The write-order test wraps real event, evidence and attempt writers and the
unit-of-work instance's commit method. It requires the exact trace event, evidence,
attempt, commit and checks the evidence's original event ID at its write boundary.
The result must have exact type NewlyStarted. A fresh transaction retrieves intent
evidence and compares its intent to a default start command and its original event
to the returned attempt's start event. This is actual store readback after service
exit, not merely comparison of writer return objects.

The recorded commit is a call to PostgresUnitOfWork.commit, which requests commit
on successful context exit; it is not interception of the connection's database
commit. The actual
[intent store](../src/control_plane_kit_operations/postgres/effect_attempt_intent_store.py.md)
read admits the identity, fetches intent coordinates/preimage joined with original
event data and reconstructs the record. Its insertion validates/re-encodes the
record and writes through the caller's transaction without committing independently.
This suite does not independently test every canonicalization or row-decoder case.

The fresh-intent test first reads the actual request/plan and checks the default
intent's workspace, plan and graph coordinates against them. It compares the
scheduled forward operation and compensation operation to the two fixture intents,
then verifies that both construct coherent command/fingerprint values. Constructing
the compensation command here is not executing it against the initial forward
world; each later negative case resets its own phase.

Six coherent-but-foreign candidates change workspace, plan, base graph, desired
graph, runtime target or phase operation. The last supplies the forward operation
in a compensation world. Every candidate has a transition fingerprint matching
its own intent, so rejection is not just a malformed fingerprint check. The
service must produce the fixed invalid-truth conflict before lease observation,
ID consumption or any of the three patched write methods, with unchanged selected
snapshot and safe rendering. These guards cover the named methods, not all
possible SQL operations.

The actual
[start-service boundary](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start_interpreter.py)
checks plan/request/run eligibility and scheduled phase before comparing intent
workspace/plan/graphs/activity/operation with durable truth. Those comparisons
occur before database-time observation and event construction. Command-level
identity/fingerprint coherence alone therefore does not admit a foreign plan or
operation. The six cases do not enumerate every possible drift in nested intent
material or every service rejection branch.

The exact-replay test seeds a STARTED attempt/evidence chain directly, wraps the
real evidence get method and forbids lease observation plus event, evidence and
attempt writes. A newly constructed service must return ExistingAttempt(current),
call the evidence getter once, consume no ID and preserve the snapshot. Its
restart wording means a fresh service and transaction connection over retained
data; no process/database restart or preceding first-start service call occurs
in this case.

_hostile_intent_record creates a subclass of EffectAttemptIntentRecord that records
all ordinary attribute reads, equality and inequality operations. Equality returns
false and inequality true. The helper allocates without construction and copies
the original dataclass fields with object-level access/assignment, so it does not
use the hostile instance's admission or virtual read hooks during assembly.

The hostile-replay test substitutes that subclass as the intent getter's return.
It requires the fixed replay conflict, empty dispatch list, no ID consumption,
unchanged snapshot and no observation/write calls. This checks rejection of a
non-exact outer record before its recorded attribute/equality hooks run. It does
not cover every malformed nested value inside a forged exact-type intent record.

The raw-get-error test injects a plain ValueError sentinel at the same getter,
captures BaseException and requires the identical sentinel to escape. It also
forbids observation/writes, checks no ID use and compares snapshots. It does not
apply the safe-error helper to that deliberately raw exception. This differs from
the general start read-helper tests: the actual intent-replay boundary catches
KeyError and OperationsRecordError, not every ValueError. OperationsRecordError
is itself ValueError-derived, but a plain ValueError remains outside that named
catch and is not promised to be redacted.

The missing/corrupt/incongruent replay method has three injected getter outcomes:
KeyError, OperationsRecordError and a different but constructible intent record
using no products. All must yield the fixed replay conflict before the guarded
observation/write methods, consume no ID and preserve the snapshot. These are
getter substitutions over existing evidence, not physical deletion or byte
corruption of the table. This method asserts the message but does not invoke the
safe-error helper for chaining/length/canaries.

The actual
[intent-record owner](../src/control_plane_kit_operations/effect_attempt_intent_evidence.py.md)
reconstructs identity/event and canonical intent, binding run/activity coordinates.
Its fingerprint property is derived from intent. Constructibility of the substituted
record does not establish equality with the expected persisted intent: replay
constructs that expected record from command intent and original event, requires
an exact observed EffectAttemptIntentRecord and compares the complete value.
This explains the incongruent-record rejection without inventing a failed decoder.

The raw-failure chain test injects RuntimeError at event insertion, evidence insertion,
attempt insertion and commit request. The writer patches raise before calling the
original methods. Thus evidence failure follows a real event write, attempt
failure follows real event/evidence writes, and commit-request failure occurs
after all three writes. Each case requires the identical sentinel and an unchanged
snapshot; the supplied Sequence is not retained for an allocation-count assertion.

For the commit case, the fixture replaces PostgresUnitOfWork.commit with a mock
that raises. It never calls the ordinary commit-request method or reaches the
underlying connection's commit. The actual
[unit-of-work exit](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
rolls back on that exception and closes the connection. This checks rollback after
failure to request commit, not a database commit error or an uncertain commit
outcome. Snapshot equality does not directly count rollback or close calls.

_NestedContext composes the optional patch context for those cases: it enters
outer then inner, exits inner then outer with the same exception arguments and
returns their suppression results combined by or. There is no finally protecting
outer cleanup if inner entry/exit fails, nor exception-state adjustment when
inner suppresses. In this suite it wraps at most one mock.patch over nullcontext;
it should not be described as a general ExitStack implementation. The helper is
defined later in the module and available when the test method executes.

The changed-write-acknowledgement method covers event, evidence and attempt writers.
Each wrapper calls the original writer, then substitutes object() for event/evidence
or a canary string for the attempt. The service must produce a fixed serialization
conflict with safe rendering and unchanged snapshot. These are failures after real
writes, unlike the preceding before-call exceptions. The tests do not retain the
service's ID sequences or independently query each candidate row after rollback.

The hostile-insert-acknowledgement test calls the real evidence insert, wraps its
admitted result in _hostile_intent_record and returns that subclass to the service.
It requires the fixed serialization conflict before any recorded hostile access,
one consumed start ID and unchanged snapshot. The actual service checks exact
intent acknowledgement type before comparing equality. This specifically tests
the evidence acknowledgement boundary, not universal no-dispatch behavior for
event/attempt acknowledgements or arbitrary exact-type forgeries.

The final method performs an actual first start followed by an identical replay
in sequence, requiring exact NewlyStarted and ExistingAttempt types and one intent
row. It then submits a coherent changed intent with no products/deliveries and
requires the replay-conflict message. Despite its "identical race" name, it has
no concurrent workers or locking barrier. It does not retain replay ID sequences,
compare returned records, repeat the row count after the rejected call, compare
snapshots for that final rejection or apply safe-error assertions there.

The inherited snapshot is a selected multi-query observation containing attempt
and intent rows plus scoped request/run/event/action fields. It is not an atomic
whole-database snapshot; comparisons follow the fixture's committed setup changes.
Safe-error checks where present require no cause/context, at most 512 characters
of combined str/repr and absence of selected nonempty canaries, not inspection of
logs or tracebacks. mock.patch contexts restore class bindings but affect all
instances while active and do not isolate unrelated concurrent callers.

Read depth: the complete 578-line suite, hostile-record helper, nested context and
all callbacks were read. Full inherited intent-store fixture158 and retained start
fixture416/interpreter431 context were checked. The actual intent-record owner265
and PostgreSQL intent store250 were read in full, with retained actual unit-of-work
semantics. This does not extend to full imported Core codecs or neighboring suites.
Validation was documentation-only: local links, whitespace and frozen-source
comparison. No application imports, tests, database/provider calls, credential
access, source/inventory edits or publication were performed.
