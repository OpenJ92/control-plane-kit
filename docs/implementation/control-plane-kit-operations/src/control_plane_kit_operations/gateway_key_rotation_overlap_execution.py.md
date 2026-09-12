Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_overlap_execution.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_overlap_execution.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner is the typed overlap exterior of the shared rotation execution
program. ProgressGatewayKeyRotationOverlap always constructs an OVERLAP
ProgressGatewayKeyRotationDeployment and copies its normalized actor scopes.
The shared command checks bounded rotation/actor identifiers, exact positive
prepared version, typed scopes/worker/fence/idempotency key and agreement of
worker ID with fence worker ID. It does not prove current authority merely by
constructing the command. The injected ExecutionCoordinator owns activity
dispatch; this wrapper maps the shared result and two shared error categories.

Outcomes are dispatched, progressed, accepted, accepted-replay, already-advanced
and blocked. The result retains rotation/checkpoint, optional coordinator status,
nonnegative exact-integer effects_attempted, optional advancement and failure code.
Its constructor requires an accepted checkpoint exactly for the three accepted/
later outcomes and a non-null failure code exactly for blocked. It does not
independently enforce phase/rotation/checkpoint identity, type/congruence of the
advancement field, or the content/length of a directly supplied failure code.
Normal kernel failure codes are fixed mappings; constructor acceptance alone is
not a complete evidence or redaction guarantee.

The full [shared execution program](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_deployment_execution.py)
requires actor DELEGATION_KEY_ROTATE and worker EXECUTION_OPERATE before its
rotation read, including replay. An unadvanced invocation requires the exact
overlap-deploying rotation version and a prepared overlap checkpoint. It takes a
non-locking read snapshot of workspace, plan, request and run, checking desired
graph/projection/revision, plan/session/base/desired lineage, admission and
approval IDs, run linkage and current claim fence. Current graph/projection may
be either the checkpoint base or desired pair; any other pair conflicts. This
read snapshot is not a transaction held across a provider effect; downstream
writers perform their own locking and authority checks.

If current already equals desired, the kernel goes directly to advancement and
rotation acceptance; it still invokes the advancement service and cannot infer
acceptance from pointer equality alone. Otherwise it calls the
[coordinator](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
with the supplied run, worker/fence, caller idempotency key and max_effects=1.
Completed leads to advancement/acceptance. Progressed maps to dispatched when
the coordinator reports attempts, or progressed with none. Failed, unsupported,
uncertain, in-flight and blocked statuses become fixed overlap failure codes;
in-flight and uncertain both use overlap-effect-uncertain. Unexpected statuses
use overlap-execution-unexpected. Coordinator errors become a fixed progress
conflict, rather than automatically recording a blocked rotation.

The selected actual coordinator path serializes a run/idempotency receipt and
checks current request/run/fence before considering replay. Completed receipts
return the stored result. An admitted but incomplete receipt returns uncertain
without redispatch or observer execution through that same-key path. A new key
enters scheduling and can encounter an existing effect attempt, whose recovery
belongs to the effect-attempt services. Command identity binds run, worker,
authority scopes, generation and maximum effects. Reusing a completed progress
receipt does not necessarily move to the next activity; callers need distinct
command identities for distinct steps, as the tests use.

For maintained runtime operations, the coordinator commits an effect start and
intent before calling the adapter, then folds its typed result afterward.
Existing attempts use reconciliation rather than a second adapter dispatch;
explicit recovery-decision cases require their separate recovery authority.
Legacy ingress/socket operations use their separate event/adapter path. Ordinary
adapter exceptions, malformed result types and effect-ID mismatches become
uncertain outcomes in the maintained runtime path; BaseException process-loss
simulation can escape with durable incomplete intent. Reported effects_attempted
counts coordinator attempt handling and may be replayed historical data; it is
not a universal count of newly issued network calls or successful provider work.

Fence identity and lease expiry have different owners. The kernel snapshot,
current-handoff replay and selected coordinator/advancement claim checks compare
stored worker/generation without independently testing lease_expires_at.
The actual effect-start service checks database-observed expiry before a new
attempt, while exact existing-start replay occurs before that observation.
Fresh reconciliation of a started attempt also checks expiry; a changed guarded
observed fold checks it again. A direct result fold observes database time but
does not reject solely for expiry when no observation guard is supplied; it
still checks current fence and attempt authority. No lease renewal or takeover
is performed by this overlap wrapper. This is not one uniform expiry guarantee
covering every operation, replay and fold.

[Current graph advancement](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/advancement.py)
uses a fixed hashed rotation/phase :advance key. New advancement locks session,
workspace, request and run, verifies open session, fence and pinned graph/plan
ownership, then requires a settled succeeded run with exactly one latest
RUN_SUCCEEDED event. Journal/schedule projection must be coherent and completely
successful, without in-flight/uncertain state or disqualifying failure/compensation
history, and success-event counts must exactly cover the plan. It CAS-updates
current graph/projection and records CURRENT_GRAPH_ADVANCED event/action in one
transaction. Replay validates the stored action/event, projection digest,
identity and claim generation; it does not perform the same fresh pointer and
schedule transition again.

After that commit, the kernel constructs the accepted checkpoint using the
advancement destination and event time. It calls the
[rotation service's fenced writer](gateway_key_rotations.py.md) with a fixed
:accepted transition to move overlap-deploying to overlap-ready. The writer
rechecks current execution linkage/fence and exact advancement action/event
congruence before recording acceptance. These are separate commits: current
can already be advanced while the rotation still retains its prepared checkpoint.
The next invocation's current-is-desired path supports replay under matching
authority and evidence; it does not bypass a stale fence or fabricate success.

Blocking similarly uses the fenced rotation writer, a phase/failure-specific
transition identity and injected timestamp. It retains the prepared checkpoint
and records the bounded code. It does not compensate completed effects, rewind
desired/current graph, delete resources or automatically resume the blocked
rotation. Durable receipts, step/run events, advancement action/event and
rotation transitions are separate evidence layers; a successful step record
alone does not guarantee a completed coordinator receipt or accepted rotation.

Existing blocked classification requires a prepared checkpoint, a failure code
and rotation version exactly prepared_version+1. Existing overlap-ready maps to
accepted-replay. For overlap, already-advanced covers new-key-active,
draining-old-grants, retirement-deploying, retirement-ready and completed;
old-key-retired and revocation-prepared are absent and conflict. The accepted
paths require an accepted checkpoint and a version greater than the requested
prepared version. All these classifications obtain a current deployment handoff
and compare its fence. They do not reload the advancement action/event, check
today's workspace pointer or prove current gateway health. Returned replay
results use default zero attempts and no advancement object. Missing or changed
claim truth can reject a read-like accepted replay.

The wrapper catches only shared authorization-denied and conflict errors,
retains their message, then raises its own type outside the except block. Tests
verify cause/context-free translation on selected stale-fence paths. This removes
retained exception chaining for those translations, not sensitive content from
an arbitrary imported message. Other exceptions can escape unchanged. Caller
identity/scopes need outer authentication; public graph/key references and
history still require appropriate exposure controls. The wrapper itself has no
network listener or secret resolution, but invoking its configured coordinator
can perform real runtime effects. Test adapters simulate those effects.

Read depth: full 203-line wrapper, full 651-line execution kernel and
[885-line tests](../../tests/test_gateway_key_rotation_overlap_execution.py.md),
with retained full fixture/rotation/preparation/projection context. Selected
actual coordinator receipt/dispatch/classification/context/fence, advancement
execution/replay/success/result validators, effect start/fold/reconciliation
authority/expiry and PostgreSQL lease observation paths were inspected. These
selected reads are not a full review of every generic coordinator/provider
adapter. No tests, keys, credentials, database, provider or runtime actions were
executed for this documentation; no source or security surface was changed.
