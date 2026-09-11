Source: [control-plane-kit-operations/tests/test_execution_lease_recovery_retry_totality.py](../../../../control-plane-kit-operations/tests/test_execution_lease_recovery_retry_totality.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These two tests require an existing retry marker in a retained journal to produce
a categorical RunLifecycleConflict when the
[shared recovery helper](../src/control_plane_kit_operations/_execution_lease_recovery_support.py.md)
is asked to check RENEW_EXPIRED_CLAIM. One marker is terminal; the other is followed
by an ordinary cancellation event. This protects rejection behavior for those two
shapes, not totality over arbitrary malformed Python values or every recovery
decision. No store, UoW, database or provider is used.

The fixture constructs a generation-7 worker fence, a failed but unsettled first
run admitted to request-a, and a planned record containing an empty ActivityPlan.
The run has a start time; the plan/run/request identifiers and times are synthetic
record values. There is no real claim, lease-expiry observation, approval history,
effect execution or persisted failure. In particular the empty plan does not prove
the positive resolved-effect-failure journal required for fresh retry/recovery.

Both histories begin with RUN_OPENED, RUN_STARTED and RUN_FAILED on run-a at
ordinals 1..3. At ordinal 4 they include RECOVERY_DECISION_RECORDED with typed
[recovery evidence](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
for RETRY_AS_NEW_RUN, a Core RunId for the retained run and equal prior/replacement
fences. The followed variant appends RUN_CANCELLED at ordinal 5. These inputs are
constructed through actual record constructors; they are not deserialized corrupt
rows or mutated hostile subclasses.

The helper is always invoked with RENEW_EXPIRED_CLAIM and the same expected fence.
The tests do not ask it to admit a new RETRY_AS_NEW_RUN decision against clean
failed history. That distinction matters: a prospective retry can have an eligible
failed journal, while an already-recorded retry marker cannot be stripped as an
ordinary lease-renewal decision/consequence pair.

The actual support source first checks nonempty same-run events and contiguous
ordinals, then parses recovery pairs. The terminal marker has no following
consequence, so the parser returns invalid at that boundary. In the followed case,
the explicit RETRY_AS_NEW_RUN guard rejects before attempting the ordinary
consequence-kind lookup, whose mapping contains only renew/takeover/abandon kinds.
That path must not leak an incidental unsupported-key error. Both inputs therefore
produce the same outer invalid-journal conflict before saga projection; their
internal rejection points need not be identical.

assert_retry_history_is_categorically_ineligible snapshots event repr and each
event object's identity, invokes the helper and requires the exact message
retained run journal is invalid. It also requires no cause/context and at most
512 characters in combined str/repr. After failure it compares the repr and object
identity tuples with the snapshots. These assertions protect those representations
and event objects from change; they do not trace every nested field or establish
database rollback, since no durable mutation occurs here.

The guarded module import handles absence of the exact private support module with
a targeted later assertion while allowing unrelated missing dependencies to fail
normally. There are no mocks replacing journal parsing: the two tests call the
actual helper. The companion
[interface tests](test_execution_lease_recovery_support_contract.py.md)
check private signatures and ownership separately; they add no positive journal
or replay-chain evidence to these two negatives.

This file does not test a successful recovery, fence rotation, all malformed pair
fields, multiple historical renewal pairs, successor-chain traversal, ambiguous
effects, authority scopes or real lease timing. It neither creates a successor
run nor authorizes a retry. The retained marker's identity/fence evidence is used
only to exercise safe rejection at the shared journal boundary.

Read depth: full 156-line test and fixture, retained full 390-line support owner,
actual recovery-evidence fence laws and journal context; the 131-line interface
test was also read fully for this pair. No source/pin changes, executable tests,
database setup, credentials/private-key access, provider/runtime actions or
publication occurred. Documentation introduces no security surface and does not
claim these tests ran or passed during authoring.
