Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_retirement_execution.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_retirement_execution.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner gives the shared deployment execution program a typed retirement
exterior. ProgressGatewayKeyRotationRetirement always constructs the generic
command with RETIREMENT and copies its normalized actor scopes. Generic command
validation checks bounded rotation/actor IDs, an exact positive prepared version,
typed scopes, worker, fence and idempotency key, and matching worker IDs in the
authority and fence. Construction does not establish current durable authority.
The constructor forwards its unit of work, coordinator, clocks and ID factory to
the shared program; progress checks the retirement command type and maps results.

Accepted here means the B-only deployment has become current and the rotation
has reached RETIREMENT_READY with an accepted retirement checkpoint. It does not
retire the old signing-key record, revoke its private secret or complete the
rotation. Those later mutations have separate owners. The
[preparation program](gateway_key_rotation_retirement_program.py.md)
establishes the prepared child and its drain-deadline prerequisites; execution
uses that checkpoint and its admitted plan rather than rebuilding publication.

The result carries one of dispatched, progressed, accepted, accepted-replay,
already-advanced or blocked, plus rotation, checkpoint, optional coordinator
status, effects_attempted, advancement and failure code. It validates the main
value types and exact nonnegative integer attempt count, requires an accepted
checkpoint exactly for accepted/later outcomes and a non-null failure code exactly
for blocked. It does not independently validate phase/rotation/checkpoint
identity, advancement type/congruence or directly supplied failure-code content
and length. Fixed kernel codes do not make arbitrary constructed results a
complete evidence or redaction boundary.

The full [shared execution program](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_deployment_execution.py)
requires actor DELEGATION_KEY_ROTATE and worker EXECUTION_OPERATE before reading
the rotation, including replay. Fresh execution requires RETIREMENT_DEPLOYING at
the exact prepared version with a prepared retirement checkpoint. A non-locking
workspace/plan/request/run snapshot checks desired graph, projection and revision,
plan/session/base/desired linkage, admission and approval IDs, run linkage and
current claim fence. Current must equal the checkpoint base or desired pair.
These reads do not hold a transaction across provider work; downstream writers
have their own locks and authority checks.

Current already equal to desired leads directly to advancement and acceptance,
but still calls the advancement service; pointer equality is not sufficient
acceptance evidence. Otherwise the
[coordinator](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
receives the run, worker/fence, caller key and max_effects=1. Completed triggers
advancement. Progressed maps to dispatched when attempts are reported and
progressed otherwise. Failed maps to retirement-effect-failed, unsupported to
retirement-effect-unsupported, uncertain/in-flight to retirement-effect-uncertain
and blocked to retirement-run-blocked, with retirement-execution-unexpected as
fallback. An adapter's unsupported result need not produce coordinator UNSUPPORTED:
the retirement test records STEP_UNSUPPORTED while scheduler classification
returns FAILED, yielding retirement-effect-failed. Coordinator errors become a
fixed progress conflict rather than automatically blocking the rotation.

Coordinator receipt admission locks and checks current request/run/fence before
replay. Its fingerprint binds run, worker, scopes, generation and maximum effects.
A completed receipt returns its stored result; an admitted incomplete receipt
returns uncertain with no redispatch or observer call on that same-key path.
Distinct step invocations need distinct command identities, as the tests use.
A new key can encounter an existing attempt through scheduling; reconciliation
and explicit recovery-decision authority belong to the effect-attempt services.

For maintained runtime operations, an effect start and intent commit before
adapter dispatch and its typed result folds afterward. Ordinary adapter exceptions,
malformed results and mismatched effect IDs become uncertain; BaseException can
leave committed incomplete intent. Legacy ingress/socket operations follow their
own event/adapter path. effects_attempted counts coordinator attempt handling and
can include stored receipt results; it is not a universal count of fresh network
calls or successful effects.

Fence identity and lease expiry remain distinct. Snapshot, current-handoff replay
and selected coordinator/advancement checks compare stored worker/generation
without independently checking expiry. A fresh effect start checks database time,
while exact existing-start replay precedes that observation. Fresh reconciliation
and a changed guarded observed fold check expiry; a direct result fold without an
observation guard still checks fence/attempt authority but does not reject solely
for observed expiry. This wrapper renews no lease and performs no takeover.

[Current graph advancement](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/advancement.py)
uses a fixed hashed rotation/phase :advance key. A new advancement locks session,
workspace, request and run, checks the open session, fence and pinned identities,
and requires a settled succeeded run with coherent successful journal/schedule,
one latest RUN_SUCCEEDED event, exact activity-success coverage and no disqualifying
failure/compensation history or in-flight/uncertain state. Current pointer CAS and
CURRENT_GRAPH_ADVANCED action/event commit together. Replay validates stored
action/event, projection digest and command/claim identity rather than repeating
the fresh pointer and schedule transition.

The kernel then builds the accepted checkpoint from advancement destination and
event time. The [rotation service](gateway_key_rotations.py.md) performs a separate
fenced :accepted transition to RETIREMENT_READY, checking current execution
linkage and exact advancement action/event congruence. Current may therefore be
advanced while rotation remains prepared. Recovery needs matching authority and
evidence; the wrapper does not invent acceptance or automatically adopt a new
claim. The writer can accept a replacement-fence fold against existing congruent
advancement evidence; the test exercises that writer directly.

Blocking records a phase/failure-specific fenced transition with the injected
timestamp, retaining the prepared checkpoint. It does not compensate effects,
rewind pointers, delete resources or automatically resume the rotation. Receipts,
step/run events, advancement evidence and rotation transitions are distinct
durable layers; committed step success alone does not ensure an accepted child.

Existing blocked classification requires a prepared checkpoint, failure code and
version exactly prepared_version+1. RETIREMENT_READY gives accepted-replay;
COMPLETED alone is in retirement's already-advanced set. OLD_KEY_RETIRED and
REVOCATION_PREPARED are absent and conflict. Accepted classifications require an
accepted checkpoint and version greater than the requested prepared version.
All obtain a current deployment handoff and compare its fence. They do not consult
the caller's coordinator receipt, reload advancement action/event, check today's
workspace pointer or prove gateway health. Results default to zero attempts and
no advancement object. The happy-path test changes the command key for accepted
replay, demonstrating this early classification rather than receipt replay.

Only shared authorization-denied and conflict errors are translated. Their
messages are retained and the wrapper raises its own type outside the except
block, clearing cause/context on the selected tested paths. That removes retained
chaining, not sensitive content from arbitrary imported messages. Other exceptions
can propagate unchanged. Caller identities/scopes require outer authentication;
public references and history need appropriate exposure controls. The wrapper has
no listener or secret resolution but its configured coordinator can call real
runtime adapters. Acceptance neither destroys the old key nor proves cleanup.

Read depth: full 208-line wrapper, full 651-line shared execution kernel and
[555-line tests](../../tests/test_gateway_key_rotation_retirement_execution.py.md),
retaining full retirement fixture/preparation/publication, overlap fixture and
rotation/projection contexts. Selected actual coordinator receipt, dispatch,
classification and authority paths, advancement execution/replay/success checks,
effect start/fold/reconciliation expiry and PostgreSQL lease observation were
inspected. This is not a full review of every adapter. No tests, credentials,
database, provider or runtime actions were executed for this documentation; it
changes no source or security surface.
