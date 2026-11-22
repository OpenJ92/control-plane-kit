# O1.B+C proposed joint interface freeze

Status: concrete candidate for Meridian/Kepler review and North's disposition.
Not target/source release. Application base is accepted A `2a1bf73`; planning
collection is PR #1901. This proposal combines the [B storage contract](receiver-lifecycle-o1-storage-plan.md),
[entrypoint matrix](receiver-lifecycle-o1-admission-boundaries.md) and
[exact execution scope query](receiver-lifecycle-o1-scope-query.md).
Those documents' earlier planning PASS results are not acceptance of this freeze.

## Command expectations and original replay

Add one Operations value in `receiver_lifecycle.py`:

```text
ReceiverLifecycleExpectation(
  current_graph_id: str,
  current_realized_projection_id: str,
  desired_graph_id: str | None,
  desired_realized_projection_id: str | None,
  desired_graph_revision: int,
)
```

Its closed descriptor has exactly those five keys. Use the existing Core public
graph/projection reference validation. Current pins are both required; desired
pins are either both absent with generation zero or both present with positive
generation bounded by the existing PostgreSQL generation contract. Workspace
comes from the existing command/trusted context. This value is a caller
expectation, never an authentication credential, write token or stored authority.

Use optional keyword-only `receiver_lifecycle: ReceiverLifecycleExpectation |
None = None` on `SetDesiredGraph`, Create/Revise/Select desired draft and generic
`PublishDesiredRealizedProjection`. A fresh receiver-affecting call requires the
complete value. Inspect old current/desired and proposed material before calling
a transition legacy-only, including receiver-to-empty/legacy cases. D's future
authoring projection may expose these public pins; C does not expand graph-read
permissions or treat redacted graph readback as complete input.

When present, `receiver_lifecycle` is fully included in each existing intent
descriptor/fingerprint. When absent, omit the key entirely, preserving original
legacy fingerprint bytes; do not add a null key to old receipts. A supplied null
wire member is malformed, distinct from absence. Any duplicated existing
desired/current field must exactly equal the new product, or reject before
writes. No fallback to today's workspace supplies omitted expected pins.
Fresh receiver validation happens after matching completed-command replay;
replay remains original evidence and never new execution permission.

For new commands carrying the product, persist that exact five-key descriptor
as a `receiver_lifecycle` member of the existing owning action payload. This is
the retained fingerprint-format discriminator and expected-pins witness, not
authority. Old action payloads remain byte/shape unchanged. Draft `_replay_result`
and `_reference_replay` currently require closed key sets: retain their exact old
variant and add only the explicit old-keys-plus-`receiver_lifecycle` variant,
validating it against the original command before constructing the unchanged
result DTO from result fields. Do not simply allow arbitrary extra payload keys.
Validate the complete action payload at the 64 KiB evidence bound before any
write; the five-pin member cannot silently enlarge its replay/read envelope.
Generic publication and desired-command replay use the same explicit absence/
presence rule. A composed old-child replay selects its format only from that
existing owned action, then verifies the original fingerprint and retained
material; a missing receipt is always fresh work with the complete new checks.

No caller per-receiver origin list or ancestor selector is introduced. Derive
original introducing graph/action/draft from B rows and exact permitted graph
membership under held truth. Inline authoring/publication may use the pinned
selected desired pending context; revise/select may additionally use that same
command's still-live draft ID/current-head revision. An arbitrary old revision,
other draft or copied abandoned introduction is not permitted. Accepted
continuation comes only from pinned accepted current. If more than one permitted
source contains R, the one immutable original introduction must agree; this is
not permission to select an unrelated source. Original introduction/binding
rows, exact retained pins and the action's existing draft/revision coordinates
retain the proven source references; do not duplicate a large per-receiver
origin list in the action or accept caller origin claims as authority.

Exact propagation responsibilities:

| Owner/input | Proposed change |
| --- | --- |
| `planning.SetDesiredGraph`, `_desired_graph_fingerprint` | Add optional expectation product; require and compare it on fresh receiver-affecting paths before graph insertion. Existing desired fields remain compatibility inputs with equality checks. |
| Draft Create/Revise and `_reference_command_intent` Select | Add product to command and fingerprint only when present. Create/Revise gain both-current-and-desired CAS; Select preserves its existing desired comparison and adds current comparison. Draft head remains explicit existing command intent. |
| `cpk_server.CpkServerPlanningService` draft branches | Decode the exact optional closed member for create/revise/select; reject unknown/missing product fields. Existing trusted context supplies auth/workspace. |
| Same `CpkServerPlanningService` desired-graph branch | Forward the exact product to `SetDesiredGraph`; do not drop it or infer it from a read. Route error mapping remains bounded. |
| `PrepareDeploymentProgram` / inline interpreter | Existing command already has complete expected current/desired lineages and generation. Derive the new child expectation **from those original command fields** and forward into SetDesiredGraph; no duplicate new parent field. Preserve existing parent intent bytes/child keys. Historical child replay needs the original absent-product fingerprint path, chosen by an existing matching old child receipt, not fallback after fresh validation failure. |
| Saved preparation | Reuse its existing explicit current/desired fields and exact saved revision, validating lifecycle before new session/source publication. Preserve old immutable replay and metadata bytes. |
| Generic realized publication | Add the product to descriptor/fingerprint; its desired authored/projection/generation must equal the existing publication fields. Current expectation is new and mandatory for receiver-affecting publication. |
| Gateway overlap/retirement composition | Existing commands pin one settled authored graph, current/desired projections and generation. Forward those original fields into the generic product after exact held equality checks; preserve historical publication replay shape by its recorded old receipt. Do not populate missing expectations from workspace reads or enable unsupported key/configuration profiles. |
| Activity planning / RequestPlanExecution / AdvanceCurrentGraph | Reuse original plan/current/desired/approval coordinates already retained; no duplicate expectation DTO. Validate lifecycle against those exact coordinates. |
| Claim/retry/start/resume/recovery | Reuse immutable original request/plan associations and current eligibility, not a fresh caller scope list. New permission must pass the reactivation gate below. |

The two composed-child compatibility cases require positive old-receipt tests:
unchanged old parent/child request must recover its exact old result after
supersession; a fresh caller must not select a legacy fingerprint to bypass
receiver checks. Do not probe old format after new-profile admission failure.
Include an interrupted parent with an already-completed child, where that
compatibility branch is actually exercised, as well as completed parent replay.

## Semantic composites and public-store closure

Keep the current command/service owners as the complete supported operations:
`DesiredGraphCommandService.execute`, `DesiredTopologyDraftCommandService.execute`
(distinct Create/Revise/Select/Delete cases), existing
`publish_desired_realized_projection_in_unit_of_work`,
`ExecutionAdmissionCommandService.execute`, and
`CurrentGraphAdvancementCommandService.execute`. No new generic lifecycle
mutation service or optional public `execute_in_unit_of_work` API is needed for
this slice. Shared pure derivation and internal SQL primitives are implementation
details; they are not alternate supported admission APIs.

Each composite finishes every applicable durable write before returning, on its
existing caller-owned UoW. Existing publication preparation proves only A's
prefix; publishing rechecks actual held truth and semantic authority. No reusable
admission token, bypass flag or ambient validated-workspace bit is introduced.

For fresh authored material the concrete insertion dependency order is graph →
actual identity projection → draft/revision when applicable → real original
operation action → sorted introduction rows → exact binding rows → desired/head
CAS as applicable. Existing deferred draft-head and B original-binding FKs
resolve at commit. Construct the actual action from the validated intended
result; any losing CAS or late/deferred failure rolls it all back. Scope lookup
is bounded evidence reading under L and cannot acquire request locks after
session/workspace. If it needs new row locks, redesign the prefix before source.

Advancement keeps complete-success/association proof first, then current CAS,
event/action and first-acceptance/retirement witnesses in one transaction. Real
action rows exist before immediate witness FKs. No witness is written on failed
or uncertain completion or stale association. Desired omission and draft
tombstone never retire receivers. Same-UoW mutation between derivation and a
write must fail the final pins/head/material checks and roll back all writes.

Supported public single-record boundaries have concrete final dispositions:

* Fresh successor graph/projection `save` refuses outside the complete semantic
  case. Preserve graph identity-conflict behavior; do not invent graph-save
  idempotency. Exact retained projection replay adds no binding or permission.
* Workspace desired/current setters and CAS classify **both old and new**
  material under L→workspace before allowing legacy-only writes. Receiver-
  affecting calls refuse outside their full semantic owner. Owner internals
  use private record primitives and never acquire L late. Pointer-bearing
  receiver bootstrap refuses; empty bootstrap remains supported.
* Standalone `GraphAuthoringService` and its action-free UoW helper refuse
  receiver-affecting work. Public draft `create/append` likewise cannot mint
  live-head provenance from historical receiver material; complete draft cases
  own that write. No fabricated session/action bridges these paths.
* Public execution `add_request`, `claim_request`, `rotate_request_claim`,
  `add_run`, permission-enabling `compare_and_set_run_status`, and fresh
  `EffectAttemptStore.insert_absent` / `EffectAttemptIntentStore.insert` must
  derive/enforce the complete supported
  semantic case or refuse absent context. The proposed disposition is refusal
  for direct fresh mutations with nonempty affecting coverage, using internal
  primitives from the legitimate service. This is profile-independent, including
  legacy affecting work. Positively nonaffecting and pure evidence operations
  retain their existing contracts; missing coverage is not nonaffecting.
  Legitimate first-start commits intent, attempt and start event together;
  direct intent insertion cannot establish fresh permission from a guard alone.
  The independent intent prefix read still detects retained orphan evidence;
  supported-writer closure does not replace that C-N11 absence proof.

These guarantees concern the supported API, not malicious arbitrary Python or
privileged SQL. Internal visibility by itself is not the enforcement: existing
public methods positively inspect/refuse, and positive semantic entries complete
the law rather than handing out partial write permission.

## Evidence predicate and narrow positive cases

C-N9 remains North's selected new law: known-complete affecting work without
accepted/disposed accounting conflicts against competing reintroduction/reuse.
North also selected **C-N10**: generic direct/recovered failure or successful
inverse operation does not clear an affecting scope absent a supported
operation-specific no-effect/disposition proof. Known failure or completion is
not renamed uncertainty. Current compensation can prove an individual inverse
completed; StartNode's inverse StopNode does not remove the resource. No
Operations whole-run compensation-completion service was demonstrated. C adds
no such service and no generic disposition inference.

Interpret complete bounded evidence in this order for each overlapping candidate:

1. Positively nonaffecting operation or disjoint original scope contributes no
   receiver-effect conflict. Still account for every other forward/inverse
   operation and original run/attempt; an unknown variant is unavailable.
2. Queued affecting work remains pending conflict even without attempts. Live
   claimed/running/paused/compensating work that can dispatch remains conflicting.
   Request `CLAIMED` after settled success is not alone proof of fresh capability;
   use the actual closed transition and journal evidence.
3. Affecting STARTED/UNCERTAIN/ABANDONED, or incomplete/incongruent necessary
   evidence, never clears. Cancellation, claim abandonment, lease expiry and
   run terminal labels do not erase an earlier dispatch witness.
4. Exact accepted advancement event/action attribution accounts only for its
   proven request/run/plan/graph coverage, preserving H1 across later pointers
   and claims. Apply current receiver continuation/retirement rules separately;
   a later receipt does not cover unrelated older failed/uncertain attempts.
5. An own advancing run is handled only after the existing full completion and
   original association proof, with atomic acceptance in this UoW. No caller
   ignore parameter or exclusion of prior/other runs is permitted.
6. Successful affecting work without that accounting is C-N9 conflict. Generic
   failed/recovered-failed or individually compensated affecting work without
   operation-specific disposition is C-N10 conflict. Explicitly report the
   resulting same-scope liveness limitation; do not add automatic recovery.
7. **C-N11**, the proposed no-dispatch positive law: exact lawful cancellation
   before any original STEP_STARTED/STEP_COMPENSATION_STARTED, with complete
   all-run journals, zero attempt/intent/compensation bindings and no earlier
   dispatched run, can be nonconflicting only with the full fresh-gate closure
   below. Query intents independently through their run-prefix primary key:
   attempts reference intents, so an attempt-driven join cannot prove their
   absence. An orphan intent is unavailable, not no-dispatch. Match cancellation
   action/request/plan/run/status/event ID/type/ordinal
   and event/action time through the existing lifecycle payload contract;
   missing/duplicate/foreign evidence refuses. Do not require `started_at=NULL`:
   current CancelActivityRun sets started and settled timestamps even from CLAIMED.
8. Exhausted row/value budgets or unavailable evidence refuses, never partial
   clearance. Positive no-dispatch proof is not blanket CANCELLED/FAILED or
   absent-attempt clearance. No other generic no-effect case is selected here.

Retaining conflict against **competing** scope reuse does not prohibit an exact
original retry that still passes current pins, approval, fence and lifecycle
eligibility. It keeps that scope occupied while lawful original work proceeds.
This distinction is a required positive target, not an ignore-request shortcut.

## Fresh permission and provider-I/O boundary

After existing command K and exact replay, fresh enabling paths take L before
request/run/attempt/session/workspace. Existing nonlocking locators are scope-
validated and rechecked under locks; stale branch selection refuses or restarts
at the outer entry, never acquires L late.

| Owner branch | Fresh prefix after K/replay |
| --- | --- |
| Run lifecycle `_claim` | L → request → session → workspace; original QUEUED/no-run, complete lifecycle and scope coverage. |
| `_transition` Start/Resume | L → request → run → session → workspace; exact original plan/current eligibility. Pause/complete/fail/cancel do not enable dispatch. |
| Failed-run retry | L → request → prior/latest runs in A chain order → session → workspace/approval suffix; original failed/latest/fence laws remain. |
| Active/expired renewal and takeover | L → request → latest run → session → workspace/approval suffix; lease extension cannot restore stale lifecycle permission. Abandonment remains evidence-only. |
| Begin failed-run compensation | L → request → run → session → workspace; preserve existing source/program/authority checks. |
| Fresh compensation attempt | Scope-checked program/binding locator → L → request → run → required attempts → workspace/program; recheck set before source/inverse/intent/binding writes. Existing replay cannot dispatch. |
| Fresh effect attempt start | Scope-checked request/run/attempt locator → L → request → A-ordered required runs/attempts → workspace and existing authority suffix; actual intent/material scope must fit original coverage. Existing attempt recovery uses original evidence path and does not dispatch. |

The coordinator dispatches native runtime work only after a lawful **NewlyStarted**
result. Committed STARTED plus exact intent remains conflicting throughout
unlocked provider I/O, even if cancellation or lease expiry happens afterward.
Do not hold L or a database transaction across provider I/O. ExistingAttempt
reconciliation, completed command replay and pure outcome folding cannot call
the adapter again. Any genuinely fresh/repeated effect re-enters its semantic
gate. Nonaffecting health, socket-record and ingress owners retain their named
contracts; this does not invent new receiver effects for them.

Both no-dispatch schedules must pass: cancellation/clearance then competing
introduction forces incompatible old activation to refuse; fresh activation
first commits STARTED, so competing reuse refuses despite later cancellation.
If any direct request/run/attempt writer bypasses this closure, C-N11 is not
accepted and cannot silently clear the scope.

## Reviewable source seams and target release

Proposed C implementation order inside the existing non-release collection:

1. Execution scope derivation, immutable SQL accounting, exact schema validation
   and bounded evidence retrieval. No independent activation/completion claim.
2. Graph/draft/selection/publication composites, combined expectations and
   fingerprints, B writes and direct graph/pointer/head closure.
3. Execution admission, every enabling/direct-mutation closure, exact predicate
   and advancement witnesses; both reactivation schedules and original replay.

North chooses whether these need separate child issues/PRs after this freeze;
do not create them automatically. Each source slice follows reviewed target-red,
ordinary Operations suite and independent review before dependent source. B's
reviewed staged storage merge precedes C source. No intermediate deployment,
adopter release or collection promotion; B+C close only at combined acceptance.

Targets preserve G/D/S/E/A/H cards from the parent, C-N9/C-N10/C-N11 above and
the exact query document's scope/cap laws. Add positive old-fingerprint replay,
present-product fingerprint sensitivity, duplicate-field mismatch, stale current
despite matching desired, same live-head versus historical copy, empty bootstrap,
direct pointer race in both orders, atomic late-action/deferred rollback, source/
inverse relocation, exact original retry and no redispatch. No test is written
or executed by this planning document. Catalog hash and actual SQL-plan/ordinary-
suite evidence belong to the later released source gates, not invented here.

Security/data/history: no new external effect, credential access, auth bypass or
history mutation. Named risks are supported-entry omissions, incorrect overlap,
false disposition, legacy replay drift and bounded-capacity exhaustion. The
schema/limits and explicit liveness restrictions are reviewable; no migration,
backfill, reset, cleanup, arbitrary loss acceptance or recovery service is added.
