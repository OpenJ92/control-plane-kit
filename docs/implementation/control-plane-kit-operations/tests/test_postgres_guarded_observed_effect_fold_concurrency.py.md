Source: [control-plane-kit-operations/tests/test_postgres_guarded_observed_effect_fold_concurrency.py](../../../../control-plane-kit-operations/tests/test_postgres_guarded_observed_effect_fold_concurrency.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These three tests submit guarded folds alongside authority/lease mutations or
other folds, and separately hold one unrelated attempt lock. They use real
PostgreSQL transactions through the [guarded fixture](postgres_guarded_observed_effect_fold_fixture.py.md),
with synthetic observed-success and authority data in the ordinary phase. They
do not run a provider, resolve credentials or restart a process. Documentation
authoring did not execute these tests or their database setup.

_concurrent creates one executor worker per supplied call, submits the calls in
order and retrieves futures in submission order. Fold conflicts and denials are
returned as exception objects for result classification; other failures propagate.
Each future.result has a fifteen-second timeout measured from that wait. There
is no start barrier, PID/blocking handshake or ordered release, so submission
does not prove that both transactions overlap at a specific boundary. The first
call can finish before the second reaches its transaction. Each arranged case
runs once, without forcing both winner orders or repeated scheduling coverage.

The first matrix runs one guarded observed fold alongside replacement, revocation
or forced lease expiry. Command intent and accepted authority are prepared before
submission, with register=False in guard construction. Revocation calls the real
runtime-authority store inside a unit of work. Expiry directly updates the request
lease timestamp to a fixed date in 2000; it does not wait for wall-clock expiry.
Replacement first commits revocation, then registers remote authority in another
transaction and requires a different registration ID. It is not one atomic
replacement: the fold can encounter the interval with no active registration.

The mutation returns its case label. After excluding that label, the test requires
one remaining result, either NewlyFolded or EffectAttemptFoldDenied, and at most
one outcome row in the reset test database. Conflict is not an accepted fold
result here. The test does not map a result to a measured transaction order,
require both outcomes to occur, compare the final authority/lease state, assert
exact event/CAS/ID counts or take a complete aggregate snapshot. A zero outcome
count is permitted for denial. The two workers are a fold and a mutation, not
two competing outcome writers in this matrix.

The actual [authority store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/runtime_authority_store.py)
revokes by reading the registration and updating active rows for its workspace/
reference; registration then inserts or reuses a matching active authority. The
fresh guarded [interpreter](../src/control_plane_kit_operations/effect_attempt_fold_interpreter.py.md)
locks request, scoped run and attempt, validates stored intent, observes the lease
and locks the active registration when required. Missing/unequal accepted authority
or expired guarded lease denies. These source paths explain the permitted race
results, but this test does not instrument authority-lock acquisition, order or
blocking duration. Remote TLS registration is durable synthetic test data, not
evidence of connectivity to the named endpoint.

The ordinary-fold/observation matrix covers succeeded and recovered-succeeded
commands against the same guarded observed-success command. Both rows require
exactly one NewlyFolded and one EffectAttemptFoldConflict among the two results.
They do not identify the winner explicitly, compare committed outcome contents
or assert error text. Execution-success and observed-success are different
outcome profiles, so the second admitted terminal transition is not exact replay.

The recovery row has a narrower meaning than its title suggests. seed_guarded_source
creates a STARTED attempt, not UNCERTAIN. The actual
[core fold](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
requires uncertainty before a recovery transition; it also rejects that recovery
after the observed success has settled. Therefore the recovery command is
ineligible in either ordering and the observation is the eligible winner. This
does not establish arbitration between two lawful recovery/observation candidates
or demonstrate a recovery-first success.

The next matrix submits two guarded observations over the same started attempt.
Identical observed-success commands must yield one NewlyFolded and one ExistingFold;
observed-success versus observed-failed must yield one NewlyFolded and one conflict.
Again, scheduling is not controlled or reversed. The test classifies results
without comparing the replay's attempt/outcome to the new result, retaining ID
sequences, forbidding lease/authority lookups, or counting events/outcomes/CAS.
The imported Sequence is not used for an assertion in this file.

The actual replay branch retains request/run/attempt and current-authority checks,
then validates exact transition/failure/outcome evidence and reconstructs
ExistingFold before the fresh intent/lease/active-registration path. This explains
why an identical command can be returned as replay. It is inspected source context,
not a no-clock/no-authority-lookup spy in this concurrency test. Different observed
terminal meanings instead conflict under the core fold and replay checks.

The unrelated-attempt case supplies the explicit synchronization in this file.
It seeds a no-authority-reference guarded start plus a foreign run/attempt, reads
that foreign attempt, then synchronously locks its row FOR UPDATE on a separate
connection before submitting the target fold. The fold must finish within the
five-second future wait, before the blocker is rolled back, and return NewlyFolded.
A later typed read must equal the original foreign attempt. This supports freedom
from that particular held foreign-attempt lock and preservation of that selected
record. The actual [attempt store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_store.py)
locks by run/activity/attempt identity.

That case does not test a foreign request/run lock, shared authority lock,
multiple unrelated resources, global deadlock freedom or every query's lock scope.
Its no-reference intent deliberately omits registration lookup. No backend PID,
NOWAIT probe or pg_blocking_pids assertion appears here. The successful fold while
the known foreign lock remains held is the relevant witness, rather than a claim
that every race in the file has a forced ordering.

The [unit of work](../src/control_plane_kit_operations/postgres/unit_of_work.py.md)
binds stores to one connection, treats commit as a request and commits physically
on successful context exit when requested. Thus successful fold completion follows
its commit; lock/replay behavior is not implemented by _concurrent itself. Errors
roll back through the unit-of-work path, but this file does not inject rollback or
commit failures or verify rollback snapshots. Replacement's revoke/register
transactions remain separate despite sharing the same fixture interface.

The inherited unit-of-work factory opens ordinary psycopg connections without
the explicit worker lock/statement timeouts used by the separate unguarded
concurrency tests. The executor context waits for running work on exit, so future
timeouts do not create a hard whole-test deadline or cancel running SQL. In the
unrelated case, finally rolls back/closes the blocker and then calls shutdown with
wait=True/cancel_futures=True; pending-future cancellation does not stop running
SQL. Sequential cleanup can mask an earlier failure or skip later cleanup if it
raises, and connection/executor construction precedes that try/finally. There is
no additional crash-recovery or provider-cleanup mechanism.

Security and history evidence is selected success/denial/conflict partitioning,
an at-most-one outcome count in the authority/expiry cases and one unchanged
foreign attempt. No categorical error-message, rendering-bound, secret-redaction
or complete durable-history assertion appears here. Do not infer actor permission,
live credential validity, broad deterministic scheduling or full serializability
from the test titles.

Read depth: all 203 source lines, three tests and _concurrent; retained full guarded
and parent fixtures; actual authority revoke/register/active-lock, fresh/replay
interpreter, core recovery precondition, identity-scoped attempt lock and complete
unit-of-work commit/rollback paths. No tests, application imports, database
connections, source/dependency changes, credentials, Docker or provider actions
were executed while authoring this companion.
