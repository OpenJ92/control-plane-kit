Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_coordinator_first_replay.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_coordinator_first_replay.py).
Maintain this document alongside its source file. Recheck attempt versus command
replay, terminal classification, recovery-result admission and inherited seed/
snapshot contracts when these assertions or their dependencies change.

Six tests in 197 lines exercise selected first-selection and replay paths through
real PostgreSQL-backed Operations services. The inherited
[coordinator fixture](postgres_effect_attempt_coordinator_fixture.py.md)
owns destructive isolated database setup/reset/cleanup and composes ordinary
coordinator, start, fold, reconciliation and lifecycle services. Adapters and
observers still return synthetic values. No test, application import, database
connection or provider action was executed while authoring this companion.

Several negative cases queue AssertionError in RecordingRuntimeAdapter. That
adapter raises only exact TypeError/RuntimeError and otherwise returns the value;
the queued AssertionError is not itself an execution sentinel. Explicit empty
runtime_calls assertions are the evidence that those selected paths avoid the
adapter. The actual coordinator would separately reject a wrong returned arm.

## Accepted service controls and running-attempt reconciliation

The control method first persists a start record/intent, invokes the real start
service and requires equality with ExistingAttempt(started). Actual
[start-service replay](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start_interpreter.py)
checks current claim, attempt correlation and exact retained intent evidence
before returning the existing variant. The test does not inspect generated-ID
calls despite supplying an unused-start-id label.

After separate resets, the method exercises the real reconciliation service over
a zero-use seeded attempt and scripted observer, then the guarded observed-fold
entry with registered runtime-authority metadata and constructed observation
evidence. Both must return exact NewlyFolded; the guarded-fold case additionally
checks that ID calls equal the story-derived event/observation ID sequence.
These are three controls in separate fixture worlds, not one continuous deployment
history. Authority registration and fingerprints are stored test metadata; no
Docker connection or secret resolution follows from these controls themselves.

The running-forward method seeds an existing STARTED attempt and invokes the
ordinary coordinator. It accepts either COMPLETED or FAILED, requires one
effects_attempted, one start call, one reconciliation call carrying the exact
attempt identity, and zero runtime calls. The broad terminal-status assertion
does not independently pin success for the default observed-succeeded story.
Observer/fold counts and exact new events/outcome content are not asserted here.
Reconciliation is a selected budget unit without provider redispatch.

## Recovery-bearing service results and orphan starts

The recovery-bearing test constructs a recovered-succeeded record with an inherited
transition helper, but does not persist that replacement. Its local monkeypatch
first calls the real EffectAttemptStartService.execute, asserts the returned
ExistingAttempt equals the current persisted record, and substitutes the synthetic
recovery-bearing result. The patch is scoped by mock.patch.object.

The coordinator must reject with the exact explicit-recovery-authority message,
no cause/context and no runtime/reconciliation calls. It also checks equal attempt
identities. This tests admission of a returned service result while the journal
still represents a running step; it is not a complete persisted recovery scenario,
an exercised approval workflow or proof that every recovery transition is safe.
The event name contains a canary, but this test uses exact message and chain
assertions rather than a general bounded str/repr sanitizer matrix.

The orphan-start method commits a STEP_STARTED event without an attempt, captures
the fixture's coordinator snapshot and invokes the coordinator. It requires the
categorical start-truth conflict, no runtime/reconciliation calls and equality of
that selected snapshot afterward. Actual start eligibility projects the journal
and schedule; a running activity cannot be treated as a fresh ready start merely
because its attempt row is absent. The coordinator normalizes the service conflict.

Snapshot equality protects the selected request/run/event/attempt/outcome fields;
it omits command receipts and does not compare all payloads or durable tables.
Actual [coordinator command admission](../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
commits an incomplete receipt before entering the effect loop, and an escaping
conflict does not complete it. Thus this test must not be described as proving
zero durable writes or complete rollback of the entire coordinator invocation.
It establishes a selected safe stop without another start in the observed history.

## Terminal direct and prepared recovery history

The direct-terminal method uses persist_terminal to prepare successful attempt,
event, outcome and observation rows. The inspected
[guarded-fold fixture](../../../../control-plane-kit-operations/tests/postgres_guarded_observed_effect_fold_fixture.py)
constructs this state with Core folding, then writes it through stores and
compare-and-set in a committed unit of work. This seed does not call a provider
or replay a previously completed coordinator command.

The test calls the same coordinator command twice. Both results must be COMPLETED
with zero effects_attempted; adapter/start/reconciliation lists remain empty, and
the two prepared record identities agree. It does not assert complete equality
of the two returned results, lifecycle/fold counts, receipt contents or absence
of every store read. The actual coordinator first classifies successful history
and may perform lifecycle completion; the next same-key call can return the
completed command receipt. Terminal activity selection and command-receipt replay
are distinct boundaries even though both avoid new runtime execution here.

The final method separately prepares recovered success and failure using the
inherited seed plus real fold service. It then reads run-a status directly from
the database, expecting SUCCEEDED or FAILED, and asserts zero effects_attempted,
no adapter/start/reconciliation calls and a recovery decision on the prepared
terminal attempt. It does not directly assert CoordinatorStatus, lifecycle-call
count, settled_at, graph advancement or user recovery authorization.

Selected actual coordinator classification calls lifecycle completion/failure
from terminal journal state. The lifecycle owner has different success/failure
settlement behavior, so these status assertions cannot substitute for complete
request/run settlement laws. Recovered terminal history prepared before the call
must also remain distinct from the synthetic running-recovery result rejected
earlier in this suite.

Read depth: full 197-line suite/six tests/local patch helper; actual start replay
and first-start guards, selected reconciliation existing-fold boundary and guarded
seed/terminal/ID helpers; retained full coordinator fixture334/reconciliation385,
store fixture126, coordinator receipt/dispatch/classification, lifecycle arms and
UoW context. No full audit of every seed or imported service is claimed. This note
adds no security surface or authorization for destructive fixture execution,
runtime recovery, retry or held live work.
