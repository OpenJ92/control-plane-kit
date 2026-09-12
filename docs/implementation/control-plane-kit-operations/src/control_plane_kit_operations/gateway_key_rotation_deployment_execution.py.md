Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_deployment_execution.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_deployment_execution.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This is the shared orchestration owner for progress after a rotation child has
a prepared deployment checkpoint. The typed
[overlap](gateway_key_rotation_overlap_execution.py.md) and
[retirement](gateway_key_rotation_retirement_execution.py.md) wrappers select the
phase and translate its results/errors. This program checks child truth, delegates
dispatch to ExecutionCoordinator, advances current graph through its service,
and records accepted or blocked rotation state through the fenced writer. It
does not publish desired material, prepare a run, implement a provider adapter,
activate/retire a signing key or revoke a secret.

ProgressGatewayKeyRotationDeployment validates bounded 1..200-character rotation
and actor IDs, a typed phase, exact positive prepared version, typed tuple of
scopes, worker authority, fence and IdempotencyKey. Scopes are sorted/deduplicated;
empty scopes can construct but fail progress authorization. Worker IDs in the
authority and fence must agree. The program requires an actual ExecutionCoordinator
instance and creates advancement/rotation services over the supplied UoW factory.
Its textual clock and ID factory feed advancement; trusted epoch feeds rotation
transitions, not database lease expiry.

Results retain rotation, checkpoint, six-valued outcome, optional coordinator
status, effects_attempted, advancement and failure code. Construction checks the
main value types and exact nonnegative attempt count, requires an accepted
checkpoint exactly for accepted/accepted-replay/already-advanced, and non-null
failure code exactly for blocked. It does not independently enforce phase or
rotation/checkpoint identity, advancement type/congruence, or failure-code content
and length. Normal orchestration supplies fixed codes and validated downstream
results; directly constructing one is not an evidence or redaction boundary.

Every progress call first requires actor DELEGATION_KEY_ROTATE and worker
EXECUTION_OPERATE, then reads the rotation and classifies existing state. Fresh
execution requires the exact prepared version, the phase's deploying status and
a PREPARED checkpoint for that same phase. Scope/fence values are supplied
authority; outer interfaces must authenticate them. The command does not itself
prove current claim ownership, lease freshness or approval.

The fresh snapshot reads workspace, plan, execution request and run in one UoW,
commits that read, then checks desired authored/projection/revision; plan ID,
session and base/desired lineage; request workspace/session/plan and approval
IDs; run/plan/admission linkage; and exact current claim fence. Current graph and
projection must equal either checkpoint base or desired as a pair. Missing child
records or mismatches conflict. These are ordinary non-locking reads: the snapshot
does not hold authoritative locks across dispatch or constitute a new review of
the approval decision. Coordinator and mutation services apply their own gates.

If current already equals desired, the program skips coordinator dispatch and
still invokes current advancement. That path requires its replay/evidence checks;
pointer equality alone never creates an accepted checkpoint. Otherwise it calls
the [coordinator](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
with the stored run, supplied worker/fence, caller idempotency key and max_effects=1.
COMPLETED triggers advancement/acceptance. PROGRESSED becomes DISPATCHED when
attempts are reported and PROGRESSED otherwise. Reported effects_attempted is
coordinator attempt handling, potentially retained receipt data, not a universal
count of fresh network requests or successful external changes.

Other coordinator statuses map to fixed phase-prefixed failures: FAILED to
effect-failed, UNSUPPORTED to effect-unsupported, UNCERTAIN and IN_FLIGHT to
effect-uncertain, BLOCKED to run-blocked, with execution-unexpected as fallback.
The phase is overlap or retirement. Adapter outcomes are not coordinator statuses:
the reviewed retirement test records STEP_UNSUPPORTED but the failed schedule
classifies as FAILED, yielding retirement-effect-failed. ExecutionCoordinatorError
becomes a fixed deployment coordinator rejected progress conflict outside its
except block; it does not automatically record a blocked rotation.

The actual coordinator serializes a run/idempotency receipt and locks/checks
request/run/fence before replay. Fingerprints bind run, worker, authority scopes,
generation and maximum effects. A completed receipt returns its recorded result;
an admitted incomplete receipt returns UNCERTAIN without dispatch or observation
on that same-key path. Reusing a completed progress receipt does not necessarily
advance another activity. The phase tests use distinct caller keys for distinct
steps; those keys differ from the program's fixed advancement/rotation keys.

Maintained runtime activities use effect-attempt services: durable start/intent
before adapter IO, then typed result folding. Existing attempts enter reconciliation
rather than another dispatch, with separate recovery-decision authority where
required. A new coordinator key may reach those services; an incomplete same-key
receipt short-circuits before them. Legacy ingress/socket operations retain their
own event/adapter path. Ordinary adapter exceptions, malformed result types and
effect-ID mismatches become uncertain in the selected maintained-runtime path;
BaseException can escape after committed intent. This program does not implement
those classifications, observation or external idempotency itself.

Stored fence identity is distinct from lease expiry. Snapshot, current-handoff
replay and selected coordinator/advancement checks compare worker/generation,
without independently testing lease_expires_at. The actual
[effect-start service](effect_attempt_start_interpreter.py.md) checks database-
observed expiry for a fresh attempt; exact existing-start replay precedes that
observation. Fresh started-attempt reconciliation also checks expiry. The
[fold service](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
checks expiry on a changed guarded observed fold, whereas a direct result fold
without an observation guard does not reject solely for observed expiry; it
still checks fence and attempt authority. PostgreSQL observes lease time after
locking the request. There is no uniform fresh-lease claim covering every replay
and fold, and this program does not renew or take over a claim.

[Current graph advancement](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/advancement.py)
uses the shared hashed rotation/phase prefix with :advance. New advancement locks
the relevant session, workspace, request and run, checks open session, fence and
pinned identities, and requires a settled SUCCEEDED run with coherent successful
journal/schedule. Exactly one latest RUN_SUCCEEDED event and exact activity-success
coverage are required, with no disqualifying failure/compensation history or
in-flight/uncertain state. Current pointer CAS and CURRENT_GRAPH_ADVANCED action/
event share one transaction. Replay validates stored action/event, digest and
command/claim identity, rather than repeating the fresh pointer/schedule transition.

The accepted checkpoint copies the advancement destination and event time. A
separate [rotation fenced writer](gateway_key_rotations.py.md) call uses :accepted,
the original prepared rotation version and the snapshot handoff fence. It checks
current execution linkage/fence and exact advancement action/event congruence
before committing phase READY plus accepted checkpoint. Current advancement and
rotation acceptance are separate commits, so a crash can leave current desired
while rotation remains prepared. A stale fence is not bypassed by that state.
The writer can accept a replacement-fence fold with congruent retained advancement
evidence; phase tests do that directly, not through automatic claim adoption by
this program.

Blocking uses :blocked:<failure-code>, injected textual time, prepared rotation
version and the snapshot handoff. It retains the prepared checkpoint and records
failure through the same fenced writer. State/fence drift can reject the fold
after coordinator work has occurred. Blocking does not compensate effects,
rewind desired/current pointers, delete resources/history or resume uncertainty.
Preparation approval, effect intent/start/result, run completion, coordinator
receipt, graph advancement and rotation transition remain distinct evidence layers.

Phase classification is exact:

| Phase | Prepared status | Accepted replay status | Already-advanced statuses |
| --- | --- | --- | --- |
| OVERLAP | OVERLAP_DEPLOYING | OVERLAP_READY | NEW_KEY_ACTIVE, DRAINING_OLD_GRANTS, RETIREMENT_DEPLOYING, RETIREMENT_READY, COMPLETED |
| RETIREMENT | RETIREMENT_DEPLOYING | RETIREMENT_READY | COMPLETED |

OLD_KEY_RETIRED and REVOCATION_PREPARED are absent from both already-advanced
sets and conflict. These execution sets are not the sibling preparation programs'
sets. Existing BLOCKED classification requires a prepared phase checkpoint,
failure code and version exactly requested_prepared_version+1. Accepted/later
classification requires an accepted checkpoint and version greater than the
requested prepared version. Every successful classification obtains the current
deployment handoff and compares its fence, so changed or missing child claim
truth can reject even an apparently read-like accepted replay.

These existing-state branches do not consult the caller's coordinator receipt,
reload advancement action/event, compare today's workspace pointers or prove
runtime health. They return default zero attempts, no coordinator status and no
advancement object. In retirement's reviewed happy-path replay, the caller key
differs from the terminal step key, demonstrating this classification rather than
receipt replay. Accepted retirement still leaves old-key retirement and provider
revocation to their separate completion owner.

Imported rotation/advancement errors are wrapped using their messages and
raise-from-None inside except blocks; context can remain even when cause display
is suppressed. The typed wrappers re-raise selected translated errors outside
their handlers and have selected cause/context-free tests. Those wrapper results
must not be generalized to every direct kernel error. Factory, validation and
other unexpected errors can propagate. No private bytes are resolved here, but
configured adapters may perform real effects; operational IDs, references and
history still need appropriate access controls and bounded external exposure.

Reviewed [overlap tests](../../tests/test_gateway_key_rotation_overlap_execution.py.md)
and [retirement tests](../../tests/test_gateway_key_rotation_retirement_execution.py.md)
exercise this program through typed wrappers, real PostgreSQL and simulated
adapters. Both cover accepted progress, stale authority and selected interruption
paths. Their post-effect boundaries 5/6 block uncertain despite committed step
success; 7/9 accept or replay; 8 includes direct replacement-fence acceptance.
Overlap has eight acceptance-evidence mutations and per-activity crash event
counts; retirement has missing-evidence rejection and an unsupported-result case.
Neither suite grants exhaustive direct-kernel, expiry, concurrency, real provider
restart or cleanup coverage. Fencing/lock-order test companions remain separately
scoped reviews and are not credited as reviewed evidence by this note.

Read depth: retained full 651-line owner, overlap203/test885 and retirement208/
test555, plus full rotation/fixture/preparation contexts; consequential local
branches were rechecked. Selected actual coordinator receipts/dispatch/context/
classification/authority, advancement success/replay/result checks, effect start/
fold/reconciliation and PostgreSQL lease observation were inspected. These are
selected dependency paths, not a full audit of every coordinator or adapter.
No executable validation, source change, credential, database/provider/runtime
action or publication was performed for this documentation; it adds no security
or mutation surface.
