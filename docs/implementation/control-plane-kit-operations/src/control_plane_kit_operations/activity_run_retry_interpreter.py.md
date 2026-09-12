Source: [control-plane-kit-operations/src/control_plane_kit_operations/activity_run_retry_interpreter.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/activity_run_retry_interpreter.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

ActivityRunRetryCommandService interprets a
[RetryFailedActivityRun](activity_run_retry.py.md) as a linked successor and retained
recovery evidence. Its constructor takes a unit_of_work_factory and keyword-only
id_factory; execute(command) returns ActivityRunRetryResult. The module exports
only this service. It owns command coordination and transaction intent; stores own
durable reads/writes, the pure result owns record coherence, and
[shared recovery support](_execution_lease_recovery_support.py.md) owns approval,
journal eligibility and historical successor traversal. No runtime adapter is
called, and no failed external effect is automatically executed again.

execute first requires RecoveryScope.OPERATE, before creating a UoW or computing
the command fingerprint. Both first execution and replay require that scope.
RecoveryAuthority is supplied command data, not authentication performed here:
the service does not resolve its authority_reference against credentials or prove
the actor owns a worker. The command fingerprint includes the reference and actor
as intent, while excluding scopes and the idempotency key. The reference is not
copied into action payload or recovery evidence. Upstream authority construction
and the retained approval checks remain distinct boundaries.

Inside the UoW, an unlocked request lookup supplies the session locator. The
[history store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/activity_history.py)
then takes a transaction-scoped advisory lock for session plus idempotency key and
loads any action for that pair. A different fingerprint raises
RunLifecycleIdempotencyConflict before new-work checks. A matching action enters
replay. Idempotency is therefore session-scoped durable history, not an in-memory
cache or just a comparison of new run IDs. Malformed action decoding receives a
fixed conflict rather than being treated as an absent action.

The fresh path locks the session and requires OPEN, then locks the request, the
presented prior run through a request-scoped selector, and the request's latest
run. The locator and locked request must retain the same identity and session.
The request must be CLAIMED with the exact expected fence; the prior must equal
the entire latest record, match the submitted prior ID and request/plan, and be
FAILED, started and unsettled. Its attempt must be below 2,147,483,647. A stale
prior, arbitrary newer run or exhausted counter conflicts before clock/ID work.

The actual [execution selectors](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
put both request_id and run_id in the SQL predicate before FOR UPDATE. Latest-run
selection filters by request and orders by descending attempt with LIMIT 1.
The service's request-before-run order and the fresh session lock coordinate
participating commands; they do not constitute a global lock over every writer
or prove every possible cross-service schedule deadlock-free.

Fresh execution next invokes locked_recovery_approval and
require_recovery_eligible_journal(RETRY_AS_NEW_RUN, ...). Approval reconstruction
checks retained request/decision/plan linkage, approved decision and required
scope. Both activity-plan and gateway-key-rotation subjects are supported without
reading mutable gateway rotation state. The helper uses ordinary approval/plan
SELECTs under caller coordination; its name does not mean those rows each receive
FOR UPDATE, and it does not create a new approval or reassess a new plan's risk.

The shared journal check requires contiguous history on the prior run, valid
older recovery pairs, exactly opened/started/failed lifecycle history after those
pairs are removed, and a FAILED saga projection with no compensation requested,
in-flight work or uncertain work. A retained retry marker cannot be stripped as
an ordinary recovery pair. This checks whether the recorded failure is eligible
for retry planning; it neither resolves ambiguous provider outcomes nor certifies
that repeating a particular external action is safe.

Only after those checks does observe_request_lease_for_update reread the locked
request and sample PostgreSQL clock_timestamp. The observation's request must
equal the previously checked request, and expired must be false. The actual store
uses lease_expires_at <= observed_at, so equality is expired. The interpreter takes
no caller clock or lease extension. It then obtains the next prior-run event and
session-action ordinals and passes the one observed timestamp into result planning.

_plan_result allocates four identities in order: successor run, decision event,
opened event, action. It constructs a CLAIMED run admitted to the same request/plan,
attempt incremented by one, with metadata containing attempt and prior_run_id.
The old run remains FAILED and the request's claim fence is preserved. A
RECOVERY_DECISION_RECORDED event on the old run records retry-as-new-run with equal
prior/replacement fences; a RUN_OPENED event at ordinal one on the successor
carries the same metadata. Both events, run creation and action creation share
the observation timestamp.

The RECORD_RECOVERY_DECISION action carries the submitted actor, idempotency key
and fingerprint. Its complete 13-key payload links request/plan, old/new runs and
attempts, both event IDs/kinds/ordinals and recovery descriptor. This is structured
operational history rather than an explanation left only in logs. The complete
ActivityRunRetryResult is constructed and validated before any of these writes.
Record constructors validate generated values; ID factory calls themselves are
not transactional and cannot be undone by a database rollback.

Persistence order is successor run, decision event, opened event, action. Each
store return must compare equal to its planned record before continuing. This
is an adapter return-value check, not an independent read-back after each insert.
Only after all four succeed does the service request commit. It does not update
the request lease, settle the old run, start the successor, modify a graph, delete
history or compensate an effect.

The injected UoW surface is structural; atomicity depends on its implementation.
The actual [PostgresUnitOfWork](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
binds stores to one connection. commit() sets a request flag; successful context
exit performs the connection commit before execute returns to its caller. An
exception or absent commit request rolls back; commit failure attempts rollback;
the connection closes on exit. The interpreter has no automatic database retry,
repair or compensation loop. An unknown commit outcome requires retained-history
reconciliation by a caller; the service does not classify network commit ambiguity.

Replay locks the current request, submitted prior run and payload-selected
successor, in that order. The action payload must be a Mapping with exact-str
run_id, decision_event_id and opened_event_id selectors. Shared evolution checking
then follows valid linked retry actions from that successor through later retries,
rejecting branches/cycles or an unrelated latest run. It loads all session actions
and can rescan them for each successor; this is finite traversal, not a constant
resource bound. It validates linked results rather than replaying every successor's
saga journal or recomputing each later action's command fingerprint.

Replay also reloads retained approval, reads the original decision/opened events,
and constructs ActivityRunRetryResult(replayed=True). That value validates complete
lineage, metadata, event/time/fence and action-payload correspondence while allowing
the successor's ten current evolved statuses. The service additionally binds the
result to the locator, current CLAIMED request and expected fence, submitted prior,
both recovery fences, action actor, idempotency key and command fingerprint. These
command comparisons are stronger than fingerprint-shape admission in the pure
result constructor.

Replay does not require the session still be open, observe lease time, allocate
IDs, append records or create another successor. It can return historical evidence
after session closure or lease expiry while the claim remains on the same fence;
claim replacement or abandonment conflicts. It still takes locks and requests
commit, so read-only replay means no domain writes, not no transaction. The return
describes the original successor's current record, even after a later linked retry;
it does not return that later successor as a newly executed command.

Selected lookup helpers translate KeyError to RunLifecycleNotFound and
OperationsRecordError/ValueError to fixed RunLifecycleConflict messages. Raising
outside the handler avoids preserving those caught exception chains. Events-list
decoding has a selected conflict translation; replay-result construction converts
OperationsRecordError to an incongruent-history conflict. These are local expected
failure boundaries. Arbitrary TypeError/RuntimeError, ID factory errors, failures
from observation/ordinal allocation or writes, and transaction errors can escape.
There is no universal exception or log sanitizer, and fresh result construction
does not use the replay helper's error translation.

The fully read [interpreter contract tests](../../../../../control-plane-kit-operations/tests/test_activity_run_retry_interpreter_contract.py)
cover root export identity, method/execute-argument names, missing OPERATE before
UoW, inventory ownership and exact shared-helper bindings. Their AST surface test
does not assert the complete constructor signature. The fully read
[first/replay tests](../../../../../control-plane-kit-operations/tests/test_postgres_activity_run_retry_first_replay.py)
assert persisted linked records, no-clock/no-ID replay after closure/expiry,
historical replay across two retries, broken history/current-fence rejection,
changed intent, both approval subjects and approval corruption. Evolved statuses
are assigned through SQL, not demonstrated through every lifecycle transition.

The fully read [eligibility/rollback tests](../../../../../control-plane-kit-operations/tests/test_postgres_activity_run_retry_eligibility_rollback.py)
cover selected non-temporal rejections before observation/IDs, expired observations,
complete planning before writes, persistence order, unequal adapter returns and
injected failures through commit. Their equality-expiry case substitutes an
observation with expired=True; the <= boundary itself is visible in actual store
SQL. Their commit wrapper raises before calling PostgreSQL commit, so rollback
assertions do not prove recovery from a server commit whose acknowledgment is lost.

The fully read [concurrency tests](../../../../../control-plane-kit-operations/tests/test_postgres_activity_run_retry_concurrency.py)
use real separate PostgreSQL connections, paired workers, pg_blocking_pids and
NOWAIT probes. They require one fresh/one replay for equal keys, one winner for
different keys against one prior, forced schedules for either winner and staged
request/prior/successor locking. This is bounded participating-command concurrency
evidence, not exhaustive scheduling or live adapter acceptance. The fully read
[store-boundary tests](../../../../../control-plane-kit-operations/tests/test_postgres_activity_run_retry_store_boundaries.py)
inject selected decoder/missing/unexpected failures at six read boundaries, require
categorical or identity-preserving errors as appropriate, and forbid observation.

The fully read [retry fixture](../../../../../control-plane-kit-operations/tests/activity_run_retry_interpreter_fixture.py)
inherits PostgreSQL schema/setup and selected request/run/event/action snapshots.
It supplies synthetic future lease timestamps and constructed approval/history.
Between linked retries it uses real lifecycle Start/Fail commands but inserts step
start/failure events manually; no failed adapter is invoked. Snapshot equality
covers selected columns, not every durable table or all fields. These files were
read as evidence, not executed during companion authoring.

Read depth: full 466-line owner, 218-line retry fixture, 188-line contract test and
all four governing PostgreSQL retry test files (765/477/420/135 lines); retained
full 299-line pure owner and 390-line shared support plus their pure governing
tests. Actual UoW was read in full, with selected history/execution selectors and
base-fixture setup, snapshot and seed context. No source/pin changes, executable
tests, database setup, credentials/private-key access, provider/runtime actions
or publication occurred. This companion adds no security surface or authority to
retry, adopt or clean up live work.
