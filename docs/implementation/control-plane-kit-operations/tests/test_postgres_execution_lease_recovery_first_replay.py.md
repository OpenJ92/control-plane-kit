Source: [control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_first_replay.py](../../../../control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_first_replay.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These 21 tests exercise first execution, repeated recovery, historical replay and
selected authority/history boundaries through the actual
[recovery interpreter](../src/control_plane_kit_operations/execution_lease_recovery_interpreter.py.md).
They use PostgreSQL stores and UoWs, with the
[base fixture](execution_lease_recovery_fixture.py.md) supplying initial approval,
claimed request, run and journal truth. They do not authenticate real principals,
run a provider adapter or prove a live deployment recovered successfully.

The fixture builds synthetic authority references, fixed IDs and active/expired
claim times. Its schema installation and workspace truncation rely on the supplied
test database, not a database allocated or ownership-checked by this file. Seed
records and later test mutations may be committed in separate transactions. The
base snapshot reads selected request/claim, event, action and run fields; it omits
some columns, session closure and other tables, and its four autocommit queries
are not one consistent snapshot under arbitrary concurrent writes.

The shared-support test wraps the interpreter's approval and journal functions
while still calling their actual implementations. Fresh active renewal for both
approval subjects must call approval before journal, using the active-renewal
decision. This proves delegation for those exercised paths, not an independent
implementation of approval or journal laws inside the test.

Scope tests require every recovery decision to reject empty or wrong scopes before
the UoW factory is entered or IDs are allocated. Expected denial is bounded and
omits the authority-reference canary. Separate positive cases add an unrelated
scope alongside the required scope and require fresh execution. These protect
capability-set membership behavior; the supplied RecoveryAuthority remains test
data rather than a credential validated against an external identity source.

The all-four-decisions test invokes the real service and requires the exact result
type, replayed=False, three ID calls in decision/consequence/action order, matching
event kinds and equal event/action observation times. It checks actor, command
fingerprint, decision evidence and selected descriptor omissions. Persisted snapshot
assertions require two added events, one action and unchanged run count, plus
selected IDs, event kinds and recovery payload. These are actual database history
checks, but this method does not independently reload every complete result record
or compare every claim column to a separately calculated expected transition.

Exact replay after session closure is exercised for all four decisions. The test
forbids lease observation and ID allocation, requires the original complete result
with replayed=True, and compares the selected snapshot. The snapshot was captured
before the SQL session-close edit but does not contain session status, so equality
does not assert that closure was undone. The law is that existing recovery history
can be returned after closure without a new recovery; the interpreter still uses
a transaction, locks and retained-authority/history validation.

Repeated-renewal tests issue different command keys with the replacement fence.
Active renewal advances generation seven to eight to nine on the same retained
run, with two decision/consequence pairs and two actions. Another test expires the
first replacement through SQL before a second expired renewal and checks the same
one-run structure. These are new recovery commands, not duplicate-command replay;
they do not assert that replay of the first command remains valid after its claim
has been replaced again.

A lifecycle-composition test renews an active claim, starts the retained run via
the actual [lifecycle service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py),
manually inserts step-start/step-failure events, then uses FailActivityRun and
forces lease expiry through SQL before expired renewal. The resulting generation
and complete selected event-kind sequence must match. Start/Fail are real durable
lifecycle commands; the intervening step events do not come from an actual adapter
failure, and forced expiry is not elapsed wall-clock time.

Historical active-renewal replay is tested after the retained run fails and later
retry runs exist. persist_linked_retry_truth constructs a successor, retry decision,
opening event and action, validates ActivityRunRetryResult, and inserts those
records directly through stores in a UoW. Its action fingerprint is fabricated b*64.
It does not invoke RetryFailedActivityRun or its interpreter, authorize a real retry
or demonstrate safe re-execution of an effect. fail_run_for_retry similarly surrounds
manual step events with real lifecycle Start/Fail commands.

Those helpers build run-a to run-b and then run-c. Replaying the original active
renewal after session closure must retain run-a, now FAILED, and preserve the same
replacement fence and selected snapshot without clock/ID calls. The return is
historical recovery evidence, not a newly executed retry or a replacement result
for the newest successor. The actual
[shared support](../src/control_plane_kit_operations/_execution_lease_recovery_support.py.md)
validates the linked chain and its relation to the latest run.

The lock-order test holds run-b in a separate connection, launches replay and uses
pg_blocking_pids to observe that blocker. While replay waits, run-c must remain
NOWAIT-lockable and request-a/run-a must already resist NOWAIT locks. After release,
replay must succeed with run-a retained. This is causal lock evidence for one
intermediate-chain stage; it does not exhaust every chain length, branch or database
schedule. That method forbids IDs but does not independently install a no-observation
sentinel; other replay methods establish that separate assertion.

Chain-corruption cases change the second successor's predecessor, attempt or
recovery fences. Further cases remove or alter the first retry action's successor/
event selectors or timestamp. Each must conflict without clock/IDs and preserve
the snapshot taken after the corruption. These edits may violate several record
or payload relationships together, so they are selected end-to-end rejection laws,
not isolated coverage of every traversal validator. The tests do not repair or
adopt malformed successor truth.

Command-binding tests change retained duration to another valid value, or rewrite
the request, recovery event, consequence kind and action payload coherently as a
takeover while replaying the original expired-renewal command. These must conflict
without clock/IDs or additional snapshot changes. The coherent rewrite keeps the
stored action fingerprint rather than recomputing it for a new command, so the
test requires semantic command binding beyond simply finding the same key/digest.
Same-fence edits to claimed_at or lease_expires_at separately require rejection.
These are stronger than the pure result's range/shape checks, but do not test every
timestamp format, duration or decision combination.

A malformed-event case replaces general evidence with a string in persisted JSON,
requiring categorical conflict with no canary leak, clock or IDs. Changed-intent
coverage resubmits a different duration under the same key and expects
RunLifecycleIdempotencyConflict before the patched request/latest-run/observation
methods. This supports early fingerprint rejection; the patched set is not an
exhaustive instrument of every lock the service could take, including the earlier
session/key advisory lock.

The broad retained-truth rejection group changes current fence, adds an unlinked
newer run, alters approval digest/scope, removes recovery events or changes event
evidence/time/kind and action type/actor/fingerprint/payload. Missing events expect
NotFound, changed fingerprint expects IdempotencyConflict, and the other selected
forms expect Conflict. Each uses clock/ID sentinels and preserves post-edit selected
truth. The separate action-lookup test substitutes session/key drift in the loaded
record in memory, exercising coordinate revalidation rather than persisting a
different lookup key and relying on that altered SQL lookup to find it.

Approval-subject coverage forbids both mutable gateway-store get methods while
fresh active renewal succeeds for activity-plan and gateway-key-rotation approvals.
Despite the method's rechecked wording, these positive cases are fresh executions.
A gateway approval review-digest edit must then raise the lifecycle error base
class without IDs or snapshot changes. This negative does not independently forbid
clock observation or require an exact Conflict subclass/message. The tests validate
retained subject reconstruction, not a live key rotation or new human approval.

The root/inventory test requires exact service export identity, one inventory row,
the single expected public export and operation ownership. It checks that selected
effect/provider module names are absent from declared internal dependencies. This
is inventory metadata evidence, not an AST import audit, transitive dependency
proof or demonstration that an arbitrary injected callback cannot perform IO.

safe_error checks no cause/context, a maximum combined str/repr length of 512 and
selected canary absence for the expected errors where invoked. It does not sanitize
exceptions itself or establish behavior for every raw driver failure. Patched store
or interpreter bindings are restored in finally blocks; fixture SQL edits and
directly persisted chains are intentional test setup, not production mutations
authorized by this note.

The file protects fresh/replayed recovery correspondence and selected operational
history boundaries. Other tests own broader eligibility/rollback, codec behavior,
race schedules and request-scoped selectors. No test here proves process restart,
ambiguous commit reconciliation, universal retry safety, compensation or cleanup
of live resources. Run/status/event names describe durable evidence, not a provider
operation observed by this suite.

Read depth: full 1,517-line source, all 21 methods and helpers, assembled from
bounded full reads of earlier sections and newly completed remaining sections;
full 572-line base fixture, 599-line recovery interpreter, pure owner/result and
shared support context retained. Actual UoW and selected lifecycle Start/Fail
dispatch, store selectors/history reads and typed record contracts were inspected.
No source/pin changes, new tests or test matrix, executable validation/imports,
database setup, credentials/private-key access, provider/runtime actions, staging
or publication occurred. This companion adds no security surface or live authority.
