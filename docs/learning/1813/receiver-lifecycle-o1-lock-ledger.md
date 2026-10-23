# O1.A lock closure — source-only review candidate

Base/source tree: accepted03ae77 / tree407d6e6e; docs32bb522 does not change source.
Scope: writers intersecting graph selection/publication, execution admission and
advancement. No application edits, targets, SQL execution or claim of observed
deadlock. This ledger supplements PR1895's path map; it is not source release.

The ordinary suite uses PostgreSQL16 (`test.sh:60`). Mode terminology follows
[PostgreSQL16 row locks](https://www.postgresql.org/docs/16/explicit-locking.html#LOCKING-ROWS)
and [uniqueness checks](https://www.postgresql.org/docs/16/index-unique-checks.html).
FU = FOR UPDATE; NKU = non-key UPDATE; KS = FK parent key-share; A = transaction
advisory exclusive. FU conflicts with all row modes; NKU and KS are compatible.
Unique conflicts can wait on an uncommitted insertion. A table name alone does
not prove a conflicting wait. All locks below last until the caller transaction
ends; `PostgresUnitOfWork.commit()` only requests commit at successful context
exit, and error/commit failure rolls back (`postgres/unit_of_work.py:55–99`).
No external effect is held inside that boundary.

All code paths below relative to Operations `src/control_plane_kit_operations`.

## Resources and exact ownership

| Resource | Key and acquisition | Actual counterpart / closure |
|---|---|---|
| Session-start identity | `operation-session:{workspace_id}:{idempotency_key}`, A via hashtextextended, `postgres/activity_history.py:81` | Same exact session creation/replay key; ordinary session start and saved preparation. Saved helper reentry uses this same key, not an action key. |
| Action identity | `operation-action:{session_id}:{idempotency_key}`, A, activity_history:89 | Every action writer for same session/key, including a changed command family. Must precede request FU even in compensation admission. |
| Execution admission identity | `execution-admission:{workspace_id}:{idempotency_key}`, A, execution:106 | Admission has action-key A then this key A; no inspected caller acquires the opposite pair. Both precede lifecycle guard. |
| Coordinator command identity | `execution-command:{run_id}:{idempotency_key}`, A, execution:118 | Same run/key receipts; not an execution request row. Coordinator takes it before request/run/receipt mutation. |
| Proposed lifecycle | `receiver-lifecycle:{workspace_id}`, A, graph-owned store | All fresh graph/reference/publication/admission/advancement entrants explicitly listed below. First acquisition precedes any later row. Same-key reentry is not a new wait; another workspace key is forbidden within the prepared command. |
| Request | `cpk_execution_requests.request_id`; FU execution:315/claim_request:260/observe_request_lease_for_update; CAS claim/status UPDATE after FU | Advancement, lifecycle, retry/recovery, effect start/fold/reconciliation/signing reload, compensation and rotation deployment all share request serialization. Claim/status columns are non-key; claim_request explicitly FU-locks before UPDATE/time sampling. |
| Run | `cpk_activity_runs.run_id`, FU execution:529; scoped `(request_id,run_id)` :384; latest under request :363 | Same request's lifecycle/effect/recovery writers. Request lock first. Multiple historical retry runs retain deterministic predecessor→successor chain order; do not replace it with lexical ID sorting. |
| Attempt | `(run_id,activity_id,attempt)`, FU effect_attempt_store:67/117; INSERT ON CONFLICT :73; CAS :89 | Start/fold/reconciliation/reload and compensation source/inverse reads. Common request/run prefix serializes same admitted execution; source/inverse identity and prior-run linkage remain checked. |
| Session | `cpk_operation_sessions.session_id`, FU activity_history:111 and next_action_ordinal:277; close status NKU :216 | Ordinary close/record/approval plus graph/planning/admission/lifecycle/advancement writers. next_action_ordinal is a real FU acquisition even if helper name hides it. Under correct command paths it is same-session reentry. |
| Workspace | `cpk_workspaces.workspace_id`, FU graph_store:63/226; pointers/lifecycle UPDATE :66/78/99/165/188 | Graph/planning/draft/publication/admission/advancement and compensation; node-control authority readers also FU-lock this row. Pointer/lifecycle updates do not modify workspace unique key, so implicit UPDATE alone is NKU. |
| Authored graph | `graph_id` PK; `(workspace_id,version)` and `(workspace_id,graph_id)` uniqueness; graph_store:282 INSERT | Immutable after publication. Same identity insert waits only on relevant uniqueness/transaction conflict then verifies/refuses. No public graph UPDATE/DELETE writer. FK parent KS does not warrant blanket FU locking. |
| Realized projection | `projection_id` PK and immutable projection identity; graph_store:355 INSERT ON CONFLICT DO NOTHING | Concurrent same immutable identity insert may wait on unique check; exact digest mismatch refuses. No mutable projection writer. Workspace pointer FK references this identity; no inverse graph-row UPDATE path. |
| Draft | `(workspace_id,draft_id)`, FU draft_store:43; head/tombstone NKU :52/:129 | Create/revise/select/delete and saved preparation. Held after workspace; same draft/head checked. Revision INSERT takes KS on exact draft and graph; deferred head FK checks inserted revision at commit. |
| Saved source | `session_id` PK, source_store:30 INSERT; FKs `(session_id,workspace_id)` and exact `(workspace_id,draft_id,revision)` | Saved preparation inserts only after pinned workspace/draft. New session is not an existing-session FU lock; its uniqueness/FKs are still real waits. No source UPDATE/DELETE. |
| Compensation program | `program_id`, FU failed_run_compensation_store:167; immutable program/step INSERT | Currently only attempt-start explicitly FU-locks program, before request/run/workspace. That noncanonical sequence is distinguished from a demonstrated opposite edge; normalize if needed for common prepared prefix without changing first-incomplete-step policy. |

Relation modes: ordinary SELECT uses AccessShare, explicit row-lock SELECT adds
RowShare, INSERT/UPDATE uses RowExclusive. Those modes coexist for these DML
paths. Installer/DDL/reset/table truncation are excluded from concurrent
application execution; no online schema migration claim. The ledger does not
turn test fixture TRUNCATE into an application writer.

## Concrete cycles prevented by one coordinated transition

1. **Session/request**: current lifecycle `_claim` at lifecycle:354 and
   `_transition`:490, retry_interpreter:89 and lease_recovery_interpreter:91
   hold session FU then wait request FU. Proposed advancement request FU then
   session FU would wait on the same session/request in the opposite order.
   Move these existing fresh writers to request/run before session in the same
   child as advancement. Session close uses only session→action and has no
   request wait; it can serialize without acquiring the new guard.
2. **Action-key/request**: failed_run_compensation:194 takes request FU before
   action key :208. Other writers with the same supplied session/key take action
   key first then request. Use an unlocked request locator solely for the
   session/workspace IDs; take exact action key first, resolve changed intent,
   then lock and revalidate request before further truth. This changes lock
   acquisition, not recovery authorization or idempotency result.
3. **Workspace/request**: current advancement holds workspace before request.
   Existing compensation attempt lineage locks request/run then workspace.
   Use F0 request/run→session→workspace everywhere a transaction needs both;
   fresh graph writers that do not need request/run omit them. Retain actual
   request/run fence and combined workspace CAS under locks.
4. **Late lifecycle acquisition**: outer overlap/retirement preparation already
   holds session/workspace/rotation before inner generic publication. Merely
   adding lifecycle A inside the inner helper would oppose another command
   holding lifecycle A and waiting session/workspace. Move first acquisition to
   the outer command entry, before preparation's session lock; exact context
   carries ownership inward. No helper may infer ownership from a workspace ID.

These are source-derived opposing edges, not claims that the ordinary suite has
reproduced a deadlock. They justify a coherent lock child. Splitting by module
would expose the inverse edge at an intermediate merge.

## Fresh transaction sequences and reentry contract

Notation `K` is only the entry's known command idempotency key(s), `L(w)` the
new lifecycle guard. Fresh entry locators are nonlocking reads of durable IDs;
under locks compare actual linkage to locator/command before accepting effects.

| Entry/callees | Proposed sequence | Reentry and terminal point |
|---|---|---|
| Desired graph set → graph helper | K(action) → L(w) → session FU → workspace FU → graph/projection inserts → desired NKU → ordinal/action | helper receives same prepared workspace/session/UoW; ordinal reacquires same session. Commit once in command. In A standalone helper omits action/session and guards before workspace while preserving current behavior. Successor/provenance refusal is C's activation obligation, not a new A validator. |
| Draft create/revise/select/delete | K(action) → L(w) → session FU → workspace FU → draft FU if existing → graph/revision/projection/pointer/tombstone/action | Any graph/draft FK refers to same workspace and validated graph. Deferred new head FK resolves at transaction end; rollback covers all rows. |
| Activity planning | K(action) → L(w) → session FU → workspace FU → immutable plan/action | Preserves planning-versus-tombstone exclusion. No execution request or external effect. |
| Saved preparation → start_in_unit_of_work | K(session) → L(w) → workspace FU → draft FU → new session/action/source INSERT | Nested start reuses exact K(session). It never locks an existing session on fresh branch; matching completed replay bypasses current admission and is read-only. |
| Generic projection publication | K(action) → L(w) → session FU → workspace FU → projection/pointer/action | Prepared context created before any row lock. No first key/guard acquisition inside persistence. |
| Gateway projection publication | unlocked rotation locator → K(action) → L(w) → session FU → workspace FU → rotation FU → projection/pointer/action | Revalidate locator workspace and exact session linkage, rotation version/current facts under locks. Inner publisher consumes context matching same UoW, w, session and command key. |
| Execution admission | K(action) → K(admission) → replay check → L(w) → session FU → workspace FU → rotation FU when approval requires it → request/action INSERT | Request absent before INSERT; FKs target already validated/locked session/workspace and immutable plan/approval. Rotation check in admission.py:479 is an auxiliary suffix, not a preexisting request lock. Replays remain original receipts. |
| Advancement | locator → K(action) → replay check → L(w) → request FU → run FU → session FU → workspace FU → CAS/event/action | Matching replay retains request→run authority fencing without fresh lifecycle admission; changed intent rejects before request/run. |
| Claim/transition | locator → K(action) → replay check → request FU → existing run FU if needed → session FU → request/run/event/action writes | No L in A. Claim has no run until INSERT. Retain lease clock after request lock. C owns any later successor admission/resume participation, which must occur at outer entry before request. |
| Retry/recovery | locator → K(action) → replay check → request FU → prior/latest run FU → session FU → decision/new run/events/action | No L in A. Request-scoped chain order preserved. All current approval/eligibility/fence/time rules retained; exact replay still has no fresh clock/IDs/session-open requirement. |
| Begin compensation | locator → K(action) → replay check → request FU → run FU → session FU → workspace FU → program/step/event/action | No L in A and no action advisory wait while holding request. Current-row/action correspondence checked after lock, original journal untouched. C owns later receiver admission semantics. |
| Compensation attempt start | immutable program locator → request FU → run FU → needed source/bound attempt FU → workspace FU → program FU/revalidation → inverse/intent/event/binding inserts | No L in A. Collect exact source/binding references by read, then lock/revalidate under request prefix before relying on them. Do not claim old program-first shape is a demonstrated cycle without an opposite program waiter. No session FU presently required. |
| Effect start/fold/reconcile/reload | request FU → all branch-required request-scoped run FU → attempt FU → authority suffix if needed | No session/workspace/lifecycle acquisition is introduced. Distinct latest-run after an existing attempt is an explicit F0-order gap despite common-request serialization. Normalize only branch-required first acquisitions; preserve historical attempt replay and scoped-run refusal. Exact branches below. |

Prepared publication API candidate (no implementation): outer entry obtains a
same-UoW context from `prepare_desired_realized_projection_publication(uow,
workspace_id, session_id, idempotency_key)` before projection construction.
Context is exact workspace/session/key plus transaction ownership; consuming
publisher validates it, not arbitrary user construction. Direct entry uses the
same preparation. Refuse mismatched or inactive UoW and any attempt to acquire a
different guard/key after prefix rows. No global registry or reusable authority.
Known exact-key reacquisition (`next_action_ordinal`, lease observation,
request/run probes) is recorded distinctly from first acquisition/new key.

### Frozen A participation decision

Mandatory **fresh** L(w) entrants in A are: `DesiredGraphCommandService`,
standalone `GraphAuthoringService`, draft create/revise/select/delete,
`ActivityPlanningCommandService`, saved deployment preparation, direct desired
realized publication, outer gateway overlap/retirement publication,
`ExecutionAdmissionCommandService`, and fresh `CurrentGraphAdvancementCommandService`.
Each resolves its original idempotency/replay result first where one exists and
takes L before its first existing row lock. Draft delete and planning/reference
publication participate because they change/protect admissible pending lineage.

Explicitly **no new L in A**: claim/start/pause/resume/complete/fail/cancel run;
activity-run retry; execution-lease recovery; begin compensation and compensation
attempt start; effect start/fold/reconcile and signing reload; coordinator command
receipts/dispatch/reobservation; ordinary session start/close/record; approval
request/decision; standalone rotation request/advance/deployment-advance; existing
node-control intent/signing readers; workspace bootstrap/metadata/lifecycle
store operations. They receive only the proven request/key/session ordering or
run-before-attempt normalization where specified. Compatible paths stay unchanged.
They never call guarded graph/publication helpers or acquire L after another
row. Nested callers of mandatory guarded helpers are mandatory outer entrants,
not members of this no-L set. Raw stores do not gain a universal late-lock hook.

This partition does not authorize successor execution. C adds its common lifecycle
validator and explicitly reviews any newly admitted/resumed executable path
against unresolved-scope exclusion. C's predicate is not a prerequisite for A's
lock-only changes. No current recovery policy or stale-approval meaning changes
in A; no no-L path is silently declared safe for new-profile execution in C.

### Narrow A interfaces and enforceable entry preconditions

These are package-internal helpers, not new root exports, caller-authorized
capabilities, Core values or a general lock manager:

```text
PostgresGraphTopologyStore.lock_receiver_lifecycle(workspace_id)
    -> WorkspaceLifecycleGuard

set_desired_graph_in_unit_of_work(
    uow, command, *, lifecycle_guard, graph_id, created_at)
    -> existing desired graph result

prepare_desired_realized_projection_publication(
    uow, workspace_id, session_id, idempotency_key)
    -> ExistingPublication(action) | PreparedPublication(guard, session)

publish_desired_realized_projection_in_unit_of_work(
    uow, command, *, prepared, created_at, action_id)
    -> existing publication result

_lock_effect_run_prefix(
    uow, locked_request, requested_run_id, *, latest_required: bool)
    -> PreparedEffectRunPrefix(request, requested_run, latest_run_or_none)
```

Guard records its exact workspace and transaction-owned graph-store identity, minted
only after the graph-store advisory call. Publication preparation records that
same owner plus exact action key/session and returns before fresh locking on
an existing action. Its caller precondition is outer entry before any existing
row lock; all supported call sites are enumerated/tested here. Persisting helper
requires `prepared`, checks active same-UoW/store identity and exact command
workspace/session/key, and never acquires a new key or guard. A stale/mismatched
context fails before persistence. This is an internal transaction contract;
it does not claim to police arbitrary raw SQL or malicious in-process Python.
No public route accepts a serialized prepared context from a client. Desired
graph helper likewise requires same-owner/workspace `lifecycle_guard`; standalone
GraphAuthoringService obtains it before the helper, and DesiredGraphCommandService
obtains it before its session lock. The helper validates against active
`uow.stores.graphs` identity and never first-acquires L after the caller's session.

Effect-prefix helper requires an already locked, linkage-validated request in
that same active UoW. It uses bounded request-scoped locator reads to identify
requested/latest runs, refuses foreign linkage before locking a foreign row,
locks only needed run identities (preserving existing chain order), then
revalidates. Result records owner/request/exact held run IDs. It has no attempt,
runtime, clock or provider effect. Native first-fold branch obtains it before
attempt; terminal replay requests no latest run. Standalone health reload uses
the same helper; nested health fold supplies it before attempt/runtime locks.
Inner health reload validates same owner/request/held run identities; it cannot
first acquire a missing distinct run. Same held keys may be re-read under lock.
If request-scoped plain latest lookup is needed, add the bounded `ORDER BY attempt
DESC LIMIT 1` counterpart to the existing locked store query, not an unbounded
`runs_for_request` scan. No extra reusable authority is conferred by either context.

### Concrete target boundary for A

1. Real PostgreSQL blocker/NOWAIT probes establish action-key → L → request →
   run → attempt → session → workspace for categories used by each entrant;
   test exact key identity, not merely order of table-name strings. No matching
   replay is forced through current desired/session-open admission.
2. Opposing-service schedules cover advancement vs lifecycle/retry/recovery,
   compensation vs an action-key-first writer using the same reused key,
   selection/publication vs advancement, and existing select/delete/planning
   exclusion. Both leadership orders terminate with the original winner/refusal
   semantics and unchanged rollback/fence guarantees.
3. Native fresh fold and standalone/nested health reload: a blocker on a distinct
   required run leaves attempt independently lockable until the run prefix is
   complete; same-run reentry remains legal. Terminal native/health fold replay
   proves no new latest-run/authority/time calls and exact retained result. Invalid
   request/run linkage, stale fence and malformed attempt preserve bounded denial
   precedence and no writes. Effect-start absent-row and reobservation already-
   ordered cases keep their original tests/behavior, not rewritten SQL traces.
4. Publication context tests reject another UoW/workspace/session/key, stale
   context and an unprepared direct persistence call before write; real outer
   overlap/retirement paths take first L before session and reuse exact keys.
   No test pretends a context authenticates an end user.
5. Compensation attempt tests preserve exact ordered steps/contiguous bindings,
   same source/inverse attempts and first-incomplete policy after revalidation;
   changed collected set rejects atomically rather than taking a late new key.
   Late action/commit failure preserves original graphs/journal and rolls back
   all newly inserted program/attempt/event/action truth.

Target source ceiling is those ownership-local tests plus explicit structural
supersession of advancement520 and draft-selection264. The owning full suite
remains the validation boundary. No tests are written or executed by this ledger.

### Branch-specific distinct-run closure

- `effect_attempt_start_interpreter.py:119–150`: request FU, requested run FU,
  attempt SELECT FU; an existing attempt replays and returns before latest-run
  lookup. Only missing attempt enters latest-run :143. An absent row creates no
  attempt-row lock, so this branch is not itself an observed inversion. Preserve
  that absence proof and historical replay; any restructuring can use a plain
  attempt existence locator under the already-held request, then lock the
  branch-required runs before attempts and revalidate absence/exact identity.
- `effect_attempt_fold_interpreter.py:235–240` locks run and existing attempt;
  native `_prepare_native_fold:477` locks latest only for STARTED. This is a real
  distinct-key possibility. Under request FU read the attempt only to select the
  existing replay-versus-first branch; for STARTED locate requested/latest runs,
  require request ownership and acquire those run keys before attempt FU. Then
  revalidate attempt identity/state and run linkage before unchanged native
  time/outcome checks. Preserve existing denial/error precedence by validating
  the nonlocking attempt representation and locked request authority before any
  first fresh latest-run acquisition that the old denied branch would not reach.
  Terminal replay never gains a latest/current
  eligibility check or new clock. A changed locator under locks refuses rather
  than automatically entering another branch.
- `health_signing_authority.py:290–294` requires requested/latest current run;
  take both branch-required run keys before attempt. In nested health fold
  (`effect_attempt_fold_interpreter.py:321–328`), establish this run prefix in
  the outer fold before attempt/runtime-authority locks. Inner reload consumes
  the exact held run context; no first new run key after runtime/attempt. Public
  standalone reload builds the same prefix itself. Existing terminal fold replay
  returns before signing reload and must keep that behavior.
- `coordinator.py:1128` managed dispatch already checks latest before attempt.
  Reobservation :1182 reads receipt FOR UPDATE; a present receipt returns at
  :1184–1189. With absent receipt there is no locked receipt row, and :1191 latest
  run precedes prior attempt :1194 and successor :1210. Therefore no new late-run
  change is justified for this path. Its receipt guard and terminal replay remain.
- Ordinary reconciliation has request→run→attempt and no latest-run acquisition;
  leave that prefix unchanged. Runtime/secret authority suffix cannot become a
  place to acquire a different request/run. Exact existing same-run event ordinal
  FU and same-request lease observation are permitted reentry, with identities
  retained explicitly.

### Compensation program preparation and revalidation

For `failed_run_compensation_attempt.py`, replace program-first preparation with
a plain immutable program/record locator carrying exact request/run/workspace,
program digest and ordered steps. Under request FU, verify request ownership;
lock that run, read exact bindings for program, verify contiguous positions and
choose only original replay position or first incomplete candidate. Precollect
source and bound inverse attempt identities from those steps/bindings, reject
foreign run/request linkage before locking, and take their deterministic
`(run_id, activity_id, attempt)` locks before workspace/program. Then workspace
FU and program FU, reread exact record/digest/steps/bindings and require agreement
with the collected identities. Any mismatch is bounded conflict with no writes,
not permission to take a newly discovered earlier lock or skip a step.

Only after that validation run existing original-source/outcome/fingerprint,
intent, prior-inverse success, authority/fence/current lineage and first-incomplete
checks. Matching original position replays exact binding without writes; a new
step inserts only its exact source-linked inverse/event/intent/binding. Required
attempts absent at collection cannot become a newly accepted arbitrary binding.
Request serialization excludes competing starts/folds while the collected set is
used, but explicit revalidation remains. No policy change permits later steps,
unproven compensation, or physical cleanup. Program-first was an F0 conformance
gap, not asserted as a reproduced or proven program/request deadlock.

## FK and uniqueness counterparts without blanket locks

- Requests INSERT: approval decision/request, plan/session, workspace/session
  FKs (`current_schema.sql:1246–1258`) take parent checks; session/workspace FU
  already belongs to this transaction. Plan and approval evidence is immutable;
  no writer locks a new request and then mutates its foreign plan identity.
- Run/event/attempt INSERT: request-plan, prior run, event run, attempt run and
  prior attempt/event FKs (:1129–1160). Existing request/run/attempt parent locks
  are same execution prefix; new event/run rows owned in this UoW. Original
  events remain append-only. A latest/prior run with another key must be
  request-scoped and chain-validated, not trusted from event payload.
- Session INSERT: workspace FK; saved preparation holds workspace FU then new
  session identity uniqueness. Ordinary session creation holds session-start A,
  inserts session then workspace KS. It never waits on a draft/lifecycle guard
  afterward; no opposite prefix edge. Existing-session terminal writer has no
  workspace dependency. Cross-workspace session mismatch must reject before
  requesting another workspace key.
- Graph/projection INSERT and workspace pointer UPDATE: exact graph/workspace
  and projection/workspace FKs. Graph/projection rows are immutable. Current
  parent KS checks do not mandate upgrading immutable parents to FU. Same
  workspace fresh graph writers share L(w)/workspace FU; exact identity conflict
  remains bounded refusal, not an overwrite.
- Draft/revision circular FK: revision checks existing draft/graph; head FK is
  DEFERRABLE INITIALLY DEFERRED (`current_schema.sql:1388–1389`). Head changes are
  non-key on draft PK `(workspace,draft)`; pointer checks do not create a draft
  primary-key update. Select/delete/revise/saved preparation keep workspace→draft
  order. Fresh create inserts both within UoW; no commit between them.
- Saved source checks its own newly inserted session and immutable exact revision;
  it does not reacquire an unrelated session or search arbitrary revision history.
- No new FU lock is proposed merely because an FK exists. Registration/authority
  writers which touch only their own row and compatible workspace KS are not
  dragged into child A without a concrete later wait on a changed prefix key.

## Independently checked rotation/approval suffix

Kepler checked these accepted-source paths independently. No unavoidable cycle
was found in this slice; this is source reasoning, not a runtime test.

| Entry | Exact key/mode and held prefix | Actual SQL counterpart / disposition |
|---|---|---|
| `gateway_key_rotations.py:563 request` | Root UoW; A hash of `gateway-key-rotation:{workspace}:{node}:{purpose.value}:{issuer}` (`postgres/gateway_key_rotation_store.py:39`) → INSERT rotation | Unique rotation_id, (workspace,correlation), partial nonterminal (workspace,node,purpose,issuer); workspace FK KS. May wait on workspace FU or terminal rotation update, but acquires no request/run/session/lifecycle after it. No inspected prefix holder later takes this binding A. Keep root transaction separate; do not inject lifecycle guard or compose inside publisher. |
| `gateway_key_rotations.py:660 advance` | FU(rotation_id) → `_advance_locked`:723 → CAS non-key columns → checkpoint upserts → revocation → transition | Already-held FU dominates NKU. Rotation workspace key is unchanged. Checkpoint key (rotation,phase), fixed overlap then retirement; revocation key rotation; transition keys (rotation,transition_id)/(rotation,to_version). Their FK KS refers to same held rotation. ON CONFLICT conditional refusal still locks its conflicting row. No fresh prefix-row acquisition. |
| `gateway_key_rotations.py:690 advance_deployment` | FU(request) → FU(run) → FU(rotation) → same suffix | Preserves actual fenced deployment linkage. Checkpoint descriptive session/plan/request/run/graph fields are NOT FKs in this baseline. Do not invent parent wait edges. |
| `approvals.py:354` rotation approval request | A(action key) → FU(session) → same-session ordinal FU → FU(rotation) → approval/action INSERT | Actual approval FKs are rotation and session, both already held. Unique request_id, (session,idempotency), partial rotation_id. No lifecycle/request acquisition afterward. |
| `approvals.py:445` decision | A(action key) → FU(session) → plain approval read → decision/action INSERT | Decision FK KS to immutable approval request. Ordinary rotation.advance reads approval/decision without row locks; rotation.approval_request_id/decision_id are NOT FKs here. No reverse approval→rotation wait is invented. |
| nested overlap/retirement publisher | Current outer helper action A → session FU → workspace FU → rotation FU; generic helper repeats identical A/session/workspace keys | First L(w) moves before outer session FU. Builder preserves action/session identity and same workspace; direct entry uses same preparation. Immutable projection INSERT uniqueness/FK and workspace CAS occur after prefix; ordinal is same-session reentry. |

Named suffix when these owners compose: existing rotation FU → deployment
checkpoints (overlap, retirement) → revocation → transition, omitting unused
categories. Graph/projection insertion can follow rotation in publication, while
history uses already-held parent session. This is an explicit F0 suffix refinement,
not a declaration that rotation owns graph truth. It does not permit any suffix
holder to first acquire a new request/session/workspace/key. No custom triggers
appear in the baseline schema. Real FK anchors: current_schema.sql:1228,
:1234–1237, :1261–1270 and :1308–1311.

## Additional governing tests read for closure

These are I (preserved), strengthened only by new cross-path schedules:

- `test_postgres_activity_run_retry_concurrency.py:317`
  `test_replay_locks_request_then_prior_then_new_run`: real blockers/NOWAIT
  protect request→prior→successor; no clock/ID allocation during replay.
- `test_postgres_execution_lease_recovery_concurrency.py:340`
  `test_database_clock_is_after_request_and_run_lock_release`: authoritative DB
  time is sampled after blockers release; keep both request/run schedules.
- `test_postgres_execution_lease_recovery_first_replay.py:1203`
  `test_changed_intent_conflicts_before_request_and_run_locks`: changed replay
  intent makes no dependent lock, time or identity call and preserves truth.
- `test_postgres_failed_run_compensation.py:36`
  `test_first_admission_is_exact_atomic_and_preserves_originals`, `:266`
  `test_two_connection_same_key_replays_one_exact_program`: original journal
  unchanged, exact program/events/action, one winner and one replay.
- `test_postgres_failed_run_compensation_attempt.py:111`
  `test_first_incomplete_step_starts_one_exact_linked_inverse_atomically`, `:154`
  `test_exact_duplicate_is_write_free_and_incongruent_replay_fails`: only first
  incomplete position, exact source/inverse link, replay no writes, changed
  intent/fence refuses. Current/desired pointers remain unchanged.

Original A2/S1 structural ordering supersession remains explicit in PR1895.
New target schedules must use real PostgreSQL blockers across opposing services,
both leaders, original rollback/fence/replay assertions intact. No target body,
assertion count or green result is predicted by this source-only ledger.

## Remaining bounded review before A can freeze

Kepler has resolved the rotation/approval/binding-key suffix above. Meridian
must review this ledger and the candidate
compensation/program/latest-run treatment. Classify which noncanonical-but-
serialized paths must change to satisfy F0 and which remain documented owner
subsets without new locks. This is a concrete pending review of the mapped
source, not a request for implementation permission or a claim of complete
closure. No new recovery policy, adoption, effect, timer or schema change.
