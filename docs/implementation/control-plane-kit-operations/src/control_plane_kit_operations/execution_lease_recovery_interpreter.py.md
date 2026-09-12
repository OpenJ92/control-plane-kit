Source: [control-plane-kit-operations/src/control_plane_kit_operations/execution_lease_recovery_interpreter.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_lease_recovery_interpreter.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

ExecutionLeaseRecoveryCommandService interprets the four
[lease-recovery commands](execution_lease_recovery.py.md) as one claim transition,
two retained-run events and one action in a caller-owned transaction. Its public
constructor takes unit_of_work_factory and keyword-only id_factory; execute returns
ExecutionLeaseRecoveryResult. The module exports only the service. It changes
durable claim authority and history, not the retained run's status, graph state or
an external runtime. Creating a successor run belongs to the retry interpreter.

The service expects values admitted by the four-command language. _decision_kind
dispatches with isinstance tests and an abandonment fallback; it is not a general
untrusted-object validation boundary. Required scopes are RENEW_CLAIM for either
renewal, TAKE_OVER_CLAIM for takeover and ABANDON_CLAIM for abandonment. Membership
is checked before UoW creation and fingerprint calculation; additional scopes are
allowed. RecoveryAuthority is supplied data: this service does not authenticate its
actor, resolve its authority reference or infer authority from knowing a worker ID.

execute opens a UoW and reads a request locator without a row lock to determine
the session. The actual [history store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/activity_history.py)
takes an advisory transaction lock keyed by session and idempotency key, then
looks up the corresponding action. Absent action means fresh execution. Existing
action with a different command fingerprint raises RunLifecycleIdempotencyConflict
before dependent request/run locks or new-work checks; a matching action enters
replay. Scope checks still precede either branch, and a digest is intent evidence
rather than credential authentication.

Fresh execution locks the session and requires OPEN, locks the request, then locks
the latest run for that request. It compares locator/current request identities,
session, run admission/plan and submitted retained run ID. The request must be
CLAIMED with the exact expected fence. Active renewal requires a CLAIMED retained
run; expired renewal, takeover and abandonment require FAILED, started, unsettled
run truth. The current generation must be below 2**63-1 for all four decisions,
including abandonment. Pure evidence admission is therefore broader than this
fresh executable boundary at the maximum generation.

The actual [execution store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
selects latest by request_id, descending attempt, LIMIT 1 and FOR UPDATE. Replay's
retained selector filters by both request_id and run_id before FOR UPDATE. These
predicates prevent adopting or locking a foreign run merely because a payload or
command names its ID. Fresh request-before-run locking coordinates participating
writers; neither the service nor these selectors provide a global lock over all
possible direct SQL writers or all service schedules.

Before observing time, the fresh path delegates retained approval and journal
eligibility to [shared recovery support](_execution_lease_recovery_support.py.md).
Approval/decision/plan identities, approved scope and supported subject are checked.
Activity-plan and gateway-key-rotation subjects are admitted without reading mutable
gateway rotation state. The helper's approval/plan reads are ordinary SELECTs under
caller coordination; its locked name does not mean it independently row-locks each
approval object. It does not mint a new approval or recompute plan risk.

The journal helper requires contiguous events on the retained run and valid older
recovery pairs. Active renewal permits only opening lifecycle history and no saga
steps. Other decisions require opened/started/failed lifecycle history and a FAILED
saga projection with no compensation requested, in-flight work or uncertainty.
Prior recovery pairs must occupy the allowed lifecycle phase and join correctly
through fences; retry markers cannot be removed as ordinary recovery pairs. These
are eligibility checks against recorded evidence, not permission to repeat an
ambiguous real effect or proof that a provider has no outstanding work.

Only then does observe_request_lease_for_update reread the locked request and
observe PostgreSQL clock_timestamp. Its request must equal the checked request.
Active renewal rejects expired=True; all other commands require expired=True.
The store defines expiry with lease_expires_at <= observed_at. There is no caller
clock or sleep until expiry. The service obtains next event/action ordinals after
this observation and uses the same time to plan the whole recovery.

For renewal, _replacement_fence increments generation on the same worker; takeover
uses next_worker_id at the incremented generation; abandonment uses None. Non-abandon
planned claims set claimed_at to the observation and lease_expires_at to observation
plus the requested duration. Renewal is based on this observation, not an extension
added to the old expiry. Abandonment changes request status to ABANDONED and clears
its claim; it neither settles the failed run nor deletes its history or resources.

_expires_at converts the expected store timestamp through datetime arithmetic and
emits UTC Z text with microseconds when present, otherwise seconds. Selected
TypeError/ValueError/OverflowError become a fixed observation-time conflict. The
helper assumes the canonical store timestamp shape, replacing its final character
with a UTC offset for parsing; it is not a standalone validator for arbitrary
timestamp inputs supplied by another UoW implementation.

Planning allocates three IDs in order: decision event, consequence event, action.
The decision is RECOVERY_DECISION_RECORDED on the retained run with typed prior/
replacement fence evidence. Its consequence has the matching renewed/taken-over/
abandoned kind at the next ordinal. Both events and action share observation time;
non-abandon claim creation shares it too. The RECORD_RECOVERY_DECISION action stores
actor, idempotency key and fingerprint plus ten common payload coordinates and
duration for non-abandonment. Authority reference/scopes are not copied into that
payload. The complete pure result is validated before the first claim mutation.

Persistence first calls rotate_request_claim or abandon_request_claim. The actual
SQL uses a conditional UPDATE on request, claimed status and expected worker/
generation, returning the decoded request. Rotation relies on the interpreter's
expiry decision under lock; abandonment additionally requires expiry <= supplied
observation in its UPDATE predicate. A no-row result conflicts, and the returned
request must equal the planned request. Decision, consequence and action are then
appended in that order, each requiring an equal adapter return. The event/action
return checks are not independent database read-backs after each insert.

Only after all writes succeed does the service request commit. The actual
[PostgresUnitOfWork](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
shares one connection across stores: commit() sets a flag, successful context exit
performs the connection commit before execute returns, and errors trigger rollback
and close. Atomicity depends on this injected transaction contract. ID consumption
outside PostgreSQL is not rolled back. There is no automatic retry or compensation
loop, and this code does not classify an ambiguous server commit acknowledgment.

Replay locks the current request and submitted retained run, then checks locator,
run, action session/key/actor identity. Shared evolution checking follows valid
later retry results from the retained run to the request's latest run and rejects
missing, branched, cyclic or incongruent successor evidence. It reads all session
actions and may scan them repeatedly; finite traversal is not constant resource
use. It neither reconstructs every successor saga nor recomputes every later retry
action's original command fingerprint. Retained approval is revalidated separately.

The action must expose exact-string decision/consequence event selectors. Replay
loads them, constructs ExecutionLeaseRecoveryResult(replayed=True), and compares
the recovered decision, prior fence and replacement fence to the submitted command.
For non-abandonment it also requires payload duration to equal the command, claim
claimed_at to equal decision time, and claim expiry to equal computed time plus
duration. These checks supply command and temporal correspondence absent from the
pure result constructor. The existing-action branch already compared the intent
fingerprint; replay is not just returning whatever row shares an idempotency key.

Replay does not require an OPEN session, observe current expiry, allocate IDs or
write new recovery records. It still takes transaction locks and requests commit.
An active-renewal result can represent its retained run after lawful lifecycle
evolution or linked retry while its original replacement claim still agrees;
expired renewal/takeover/abandonment retain their pure result status requirements.
Changing the current fence, claim times, decision semantics or required history can
invalidate replay. The returned value does not renew a lease again, start another
run or repair missing history.

Expected lookup KeyError becomes NotFound at mandatory request/run/event/session
boundaries. Selected OperationsRecordError/ValueError reads become categorical
Conflict; the optional action read has its own conflict message and no KeyError
translation. _result converts OperationsRecordError on both fresh planning and
replay to the common incongruent-history conflict. Errors are raised outside their
handlers to avoid retained chains. Arbitrary dependency errors, ID failures,
observation/ordinal/write/transaction failures can still escape; this is not
universal error sanitization or a retry policy.

Selected [first/replay tests](../../../../../control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_first_replay.py)
were read for all-four-scope denial before UoW, extra-scope admission, three IDs and
persisted event/action counts, exact replay after session closure, changed-intent
early rejection, duration/coherent-decision and same-fence timestamp drift, shared
helper delegation and both approval subjects. The retained-approval positive cases
are fresh executions despite their rechecked title. Linked-replay sections use
actual lifecycle Start/Fail around manually inserted step events; retry results
are constructed and persisted directly through stores with a fabricated fingerprint,
not executed by the retry service. Their snapshots cover selected columns/tables.

Selected [eligibility/error tests](../../../../../control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_eligibility_errors.py)
cover accepted/rejected journal fixtures, malformed persisted evidence, state/
fence/expiry/capacity and latest-run rejection before IDs, no-observation negatives,
CAS misses and failures at claim/event/action stages. Equality-expiry substitutes
an observation with expired=True rather than making the real clock hit equality.
Write failures are injected before the selected original method, with earlier
writes real. The commit wrapper raises before calling the underlying commit;
snapshot rollback evidence does not establish lost-acknowledgment recovery.

The selected [concurrency clock test](../../../../../control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_concurrency.py)
uses real blocker/worker connections, pg_blocking_pids and NOWAIT probes for request
and run stages. It requires all persisted claim/event/action times to agree and be
at least a blocker clock marker sampled immediately before lock release. This
supports lock/clock ordering for the exercised active-renewal cases, not review
credit for every race schedule in the file. Selected
[scoped-run tests](../../../../../control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_scoped_run.py)
include a direct selector miss followed by a foreign-row NOWAIT probe while the
selector transaction remains open. The public replay wrapper's separate probe is
before its selector call; these are different strengths of lock evidence.

Read depth: full 599-line owner, retained full pure owner 442, contract 688/result
513, shared support 390 and actual UoW. Actual history advisory/optional-action and
execution observation/selectors/claim-update SQL were inspected. PostgreSQL files
were read in selected sections only: first/replay 1517, eligibility/errors 779,
concurrency 435 and scoped-run 453, including the helpers supporting the claims
above. Base-fixture setup, snapshots and seed/history context were also inspected;
the codec/store suites were not reviewed here. No source/pin changes, executable
tests, database setup, credentials/private-key access, provider/runtime actions,
staging or publication occurred. This companion adds no security surface or
authority to recover, adopt or clean up live work.
