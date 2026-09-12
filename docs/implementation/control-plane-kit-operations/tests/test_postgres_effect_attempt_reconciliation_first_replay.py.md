Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_reconciliation_first_replay.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_reconciliation_first_replay.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These six PostgreSQL tests cover arranged start/terminal truth, exact terminal
replay, current-claim and historical-lineage precedence, selected missing/corrupt
adapter returns and fresh-path expiry/intent rejection. The
[reconciliation fixture](postgres_effect_attempt_reconciliation_fixture.py.md)
provides real database setup and synthetic runtime evidence. This file does not
exercise a successful fresh provider observation followed by reconciliation.
_forge_exact bypasses dataclass constructors using object.__new__ and
object.__setattr__, copying all declared fields except replacements. Its forged
values represent invalid exact-type adapter returns, not admitted public values.

The first control seeds a STARTED attempt and reads back its exact intent record.
It then separately arranges execution-succeeded and observed-succeeded terminal
worlds and reads each exact outcome by attempt identity and latest transition
event ID. Each persist_terminal resets fixture truth, computes the state with
production fold helpers and writes event, endpoint observations, outcome and
attempt CAS through real stores in one unit of work. These successive arranged
worlds are not a fresh reconciliation execution or an independently derived
oracle for outcome construction. The control does not count intent rows or
inspect an aggregate database snapshot.

The terminal replay matrix has eight ordinary-phase cases: execution/observed
success profiles, original/newer current claim, and unexpired/expired lease.
The newer claim is written as worker-b/generation eight; commands match whichever
claim is current. Expiry is an actual test-row update to a date in 2000, while
claim replacement writes future claim/expiry dates. Each service call must return
exactly ExistingFold(attempt, outcome), and retained observer/fold call lists must
remain empty.

That matrix installs raising sentinels on intent lookup, lease observation,
active runtime-authority lookup and secret-use authorize_resolution. The separate
FailIfObserver and FailIfFold prohibit provider observation and guarded folding.
It therefore establishes replay without those selected fresh-path interactions,
including under an expired but still current claim. It does not bypass current
claim ownership. There is no ID-sequence spy, authorization-row comparison or
before/after aggregate snapshot in this test; the assertions do not independently
prove zero allocations or that every table remains unchanged. Reading stored outcome
evidence can still load persisted endpoint observations through its adapter.

The actual
[reconciliation interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation_interpreter.py)
validates command shape and EXECUTION_OPERATE before opening its unit of work.
It then locks request, request-scoped run and exact attempt in that order, checks
their coordinate relationships, and requires CLAIMED status with a claim fence
equal to the command fence. Historical-lineage rejection follows current-claim
admission: a stored recovery decision, a lower generation, or a different worker
at equal generation is incongruent. A higher generation may proceed when it
matches the current claim. These checks precede the non-STARTED replay branch.

The inspected
[execution store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
uses FOR UPDATE for request and scoped run reads; the attempt adapter likewise
locks its coordinate-selected row. Replay reads the outcome for the attempt and
latest event, checks its exact type, request workspace and full attempt equality,
then reconstructs the actual
[ExistingFold](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold.py).
That value validates the non-STARTED attempt and outcome relationship. Replay
returns from inside the initial unit of work without requesting commit; the
actual PostgreSQL unit of work rolls back that read transaction and closes its
connection. These source checks explain the branch; this file does not measure
lock ordering, transaction counts, physical rollback or concurrent serialization.

The current-claim precedence test first covers four worlds: STARTED, execution
terminal, observed terminal and an actually committed recovered success. For the
recovery world it seeds an UNCERTAIN predecessor and then invokes the real fold
service to commit recovery before reconciliation. This differs from merely
labelling an uncertain seed as recovery. Each world receives a newer current
claim while reconciliation still uses worker-a/generation seven. All must raise
the fixed authority denial before the patched outcome, intent, lease, runtime
authority and secret-authorization methods, or the fail-on-call observer/fold.

Two further rows make the command match a lower current generation or an
equal-generation other worker against an observed terminal attempt. They must
raise the incongruent reconciliation conflict without reading outcome evidence.
A final row commits recovery under a matching current claim and also requires
that conflict before direct-outcome lookup. Together the cases distinguish stale
claim denial from a current caller's incompatible historical lineage/recovery
classification. They assert fixed messages, but do not assert cause/context
absence or complete unchanged history for these precedence rows.

The locked-truth test injects KeyError separately into request, scoped-run and
attempt locked reads. Each becomes EffectAttemptReconciliationNotFound with the
fixed not-found message and no exception cause or context. It then injects
TypeError and RuntimeError at the request lock and requires the identical error
object to escape. Despite the test's missing-and-invalid name, it contains no
malformed returned-row matrix or OperationsRecordError/ValueError lock-injection
case. The actual wrappers normalize those latter categories to invalid-truth
conflicts, but that behavior is not asserted by these rows. Raw error identity
also means this test is not evidence of universal exception sanitization.

Four terminal-outcome faults cover a missing row, a corrupt-row exception, an
exact-type outcome with foreign workspace, and an outcome whose nested attempt
has a drifted latest event ID. The latter two use _forge_exact and leave the real
stored row untouched. All produce EffectAttemptReconciliationConflict with the
fixed invalid-truth message and no cause/context. This checks categorical handling
and selected replay congruence boundaries, not every possible corrupted outcome
field or the PostgreSQL decoder's complete validation surface.

The fresh-path expiry case wraps the actual locked lease observation, replacing
its returned time and expired flag. It does not change the database lease in
this case. A raising intent-lookup sentinel establishes that expiry denial
precedes intent loading, and the observer/fold doubles forbid reaching effects.
The expected result is the fixed authority denial. This is distinct from the
terminal matrix's actual expired lease rows, which replay without lease observation.

Four fresh intent faults then inject missing/corrupt exceptions or forged identity
and original-start-event mismatches. Each must raise the fixed invalid-truth
conflict with no cause/context before observation/folding. Actual source compares
the intent record's type, identity, full original event, request, workspace and
request fingerprint with locked truth; only the listed fault dimensions are
isolated here. These rows do not explicitly spy on secret authorization or active
runtime-authority lookup, and they do not compare durable snapshots afterward.

No test in this file calls complete_reconciliation_snapshot, authorization_rows
or an ID ledger. Its positive replay result equality, prohibited-call sentinels
and negative error categories are the concrete evidence. They should not be
reported as comprehensive non-mutation, grant persistence, all-profile,
compensation, provider, concurrent-locking or retry coverage. The observed profile
describes stored synthetic evidence; no real provider is observed by these tests.
Inherited setup, terminal persistence, claim updates and recovery folds do mutate
test database state, so execution requires the isolated PostgreSQL test context.

Read depth: the complete 368-line source, all six tests and local forging helper
were read, together with the complete reconciliation fixture and relevant inherited
terminal/recovery seed, claim update and fold-wrapper helpers. Selected actual
initial-lock/current-claim/lineage/replay, ExistingFold, PostgreSQL lock/outcome and
unit-of-work implementations were checked. This is not a full review of those
owners or adjacent suites. This companion was checked for local links, whitespace
and frozen-source consistency only; no application imports, tests, database or
provider operations, credential access or publication were performed.
