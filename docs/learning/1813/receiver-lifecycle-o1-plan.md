# #1882: graph-owned receiver lifecycle planning

Status: planning accepted in PR1895 at `9a1ece35`; O1.A causal-red accepted and
bounded source work released. O1.B/C/D and parent acceptance remain held.

The original dry run below records the `03ae77` source. Its provisional
lock-closure/child-publication notices are superseded by the
[final reviewed ledger](receiver-lifecycle-o1-lock-ledger.md),
[freeze review](https://github.com/OpenJ92/control-plane-kit/issues/1896#issuecomment-5881898456),
and [North's O1.A release](https://github.com/OpenJ92/control-plane-kit/issues/1896#issuecomment-5882109601).
Published children are [A #1896](https://github.com/OpenJ92/control-plane-kit/issues/1896),
[B #1897](https://github.com/OpenJ92/control-plane-kit/issues/1897),
[C #1898](https://github.com/OpenJ92/control-plane-kit/issues/1898), and
[D #1899](https://github.com/OpenJ92/control-plane-kit/issues/1899).
The [A target checkpoint](receiver-lifecycle-o1-lock-targets.md) records its
test translation. A adds locking only; C owns successor provenance refusal,
unresolved-scope admission and lifecycle activation.
The [corrected causal-red review](https://github.com/OpenJ92/control-plane-kit/issues/1896#issuecomment-5883254768)
and [bounded source release](https://github.com/OpenJ92/control-plane-kit/issues/1896#issuecomment-5883262738)
supersede the target-only stage; source review and owning green remain pending.

Governing [release](https://github.com/OpenJ92/control-plane-kit/issues/1882#issuecomment-5881554480)
follows [C1 acceptance](https://github.com/OpenJ92/control-plane-kit/issues/1881#issuecomment-5881553598).
Base: `03ae77bc14a411814e242fae81960bba21d17696`; tree
`407d6e6e948c599154300c18f8092ceb54fcdb51`. Inspection reused the tree-equivalent
`97f8a8c` checkout and the bounded readiness assessment. Branch:
`codex/1882-lifecycle-planning`, destination `roadmap/1813-runtime-control`.
This document changes no application, target, schema, provider or live state.

## Result and explicit hold

The missing connection is Operations admission of Core's accepted receiver
configuration into durable graph truth. Existing commands persist immutable
graphs, own replay, and fence desired lineage; they do not prove introduction,
continuation or terminal retirement. Existing read descriptors are redacted and
cannot supply complete authoring material.

Split O1. A coordinated lock-protocol child must precede activation of receiver
indices and admission. The known advancement workspace-first assertion is an
explicit incompatible structural law, not a flaky fixture. Additional inversions
exist in run lifecycle and nested projection publication. No target or source
release is justified until the intersecting lock map and its test transition are
independently reviewed. The child contracts below are proposals for North to
publish and release; the parent is not complete.

F0 section 4 and ADR0008 already decide the schema and lifecycle semantics.
They supersede all additive-upgrade/backfill language in the earlier issue.
Only an object-free namespace can install the new exact baseline; exact-current
reentry is query-only; other owned state is refused without repair/reset. No
historical receiver identity, introduction or acceptance witness is fabricated.
Pending lineage is settled by F0, not an open product decision.

## Governing method-level law cards

Paths below are relative to `control-plane-kit-operations/tests/`. These are
current owned tests inspected at the base, not a revived frozen package. C1's
terminal Operations 1,836-test evidence is baseline green; no O1 tests ran.
I = isomorphic; S = strengthened; N = new-law. Method names are the provenance;
line numbers are navigation aids at the base. New targets remain unwritten.

| Card | Governing method | Observable law and negatives | Discarded assumption / future owner |
|---|---|---|---|
| G1 I/S | `test_graph_authoring.py:287 test_stale_expected_desired_graph_rolls_back_graph_insert` | Stale desired coordinates reject; no graph insertion survives. Strengthen with accepted current/projection CAS and no orphan receiver claim. | Desired-only CAS is insufficient for receiver admission; graph workflow/store. |
| G2 I | `test_workspace_graph_stores.py:105 test_graph_store_preserves_typed_descriptor_and_latest_version` | Read-back equals original graph record and typed graph; latest version is correct. | Preserve semantic/exact stored descriptor material, not incidental JSONB whitespace; graph store. |
| D1 I | `test_desired_topology_drafts.py:113 test_replay_precedes_allocation_and_later_session_product_and_head_admission` | Exact create/revise replay survives later head, product revocation and session close without allocation; changed title/graph conflicts; all truth unchanged. | Replay is evidence recovery, not renewed execution admission; draft command. |
| D2 S | `test_desired_topology_drafts.py:184 test_concurrent_distinct_revision_commands_have_one_winner_and_reject_stale_head` | Two commands from one head produce one revision and one graph; stale loser leaves truth unchanged. | Add combined current/desired CAS and introduction uniqueness; draft command. |
| D3 I/S | `test_desired_topology_drafts.py:216 test_concurrent_identical_create_replays_one_durable_revision` | Identical concurrent requests converge on original graph/draft IDs and one durable revision. | Also one introduction reservation, never remint; draft command. |
| D4 S | `test_desired_topology_drafts.py:231 test_action_failure_rolls_back_graph_revision_head_and_action_for_create_and_revise` | Late action failure rolls back graph, revision, head and action; subsequent exact retry succeeds. | Extend to introductions/bindings; do not enlarge the existing injected-failure mechanism into a second store model. |
| D5 I | `test_desired_topology_drafts.py:242 test_command_authority_and_session_workspace_are_checked_before_allocation` | Missing edit scope or foreign session rejects before ID allocation and without writes. | Auth remains Operations/trusted-context owned; no permission implied by a valid receiver ID. |
| S1 S | `test_draft_selection.py:264 test_selection_lock_order_and_concurrent_distinct_cas_have_one_winner` | Actual SQL order plus concurrent CAS one-winner/generation increment. | Explicitly supersede old key/session/workspace/draft sequence by inserting lifecycle guard before row locks; retain one-winner proof. |
| S2 I | `test_draft_selection.py:288 test_select_and_delete_each_lock_owner_excludes_the_other` and `:313 test_planning_and_delete_share_workspace_serialization_in_both_orders` | Both schedules serialize; selected/referenced draft cannot be tombstoned. | Keep real contention and preserved history; guard must cover planning/reference publication, not selection alone. |
| E1 I/S | `test_execution_admission.py:249 test_revocation_after_approval_blocks_new_execution_but_preserves_receipt` | Old receipt replays; fresh revoked execution leaves no request. | Apply same distinction to retired receiver/old approval; admission owner. |
| E2 S | `test_execution_admission.py:334 test_approval_cannot_be_reused_after_projection_cycles_back` | A→B→A projection cycle still invalidates old plan through generation. | Receiver ID equality cannot erase graph/projection/generation authority. |
| E3 I | `test_execution_admission.py:390 test_concurrent_identical_admission_converges`, `:408 test_admission_replay_survives_close_but_new_admission_is_fenced`, `:436 test_late_action_failure_rolls_back_execution_request` | One admitted identity, original replay, closed-session fresh refusal, action/request atomicity. | Preserve when lifecycle checks are inserted; no external effects in admission. |
| A1 I/S | `test_current_graph_advancement.py:333 test_complete_durable_success_advances_current_graph_once` | Exact pointer/projection, one event/action, original receipt after session close. | Same transaction additionally records first acceptance and terminal retirement. |
| A2 S | `test_current_graph_advancement.py:520 test_first_execution_locks_workspace_before_request_and_run` | Blocking workspace leaves request/run independently NOWAIT-lockable. | Explicitly superseded structural ordering: this exact order conflicts with F0. Strengthen the shared concurrency law only through reviewed replacement; preserve CAS/fence/rollback and prove new order using real blocking. |
| A3 I/S | `test_current_graph_advancement.py:482 test_first_execution_locks_request_before_run`, `:562 test_replay_locks_request_before_run_but_changed_intent_locks_neither` | Request blocked before run; changed replay intent fails without those locks; matching replay remains fenced. | Request-before-run remains. Fresh lifecycle guard must not make historical replay depend on current desired/session state. |
| A4 I/S | `test_current_graph_advancement.py:840 test_incomplete_uncertain_or_failed_evidence_cannot_advance`, `:886 test_stale_realized_lineage_and_revision_fail_closed` | Unsupported/uncertain/failed evidence, stale current projection, desired projection or generation makes no pointer/event advancement. | Add no acceptance/retirement witness on any refusal. |
| A5 S | `test_current_graph_advancement.py:1110 test_late_action_failure_rolls_back_pointer_and_event`, `:1139 test_concurrent_advancement_has_one_winner` | Late failure leaves old pointer/no event; concurrent distinct requests have one winner. | Add index rollback and advance-versus-selection schedules. |
| L1 I/S | `test_run_lifecycle.py:1022 test_first_transition_locks_request_before_run`, `:1204 test_lifecycle_replay_survives_close_but_new_transition_is_fenced` | Request-before-run, historical claim replay after close, no new start after close. | Remove session-before-request acquisition, not session fencing or lease-generation checks. |
| H1 I | `test_revision_history_advancement.py:51 test_success_without_receipt_is_none_recorded_then_real_acceptance_survives_pointer_change`, `:71 test_accepted_history_survives_current_claim_rotation_and_removal` | Success alone is not accepted history; accepted event/action receipt survives later pointers and request claim/status changes. | Lifecycle summary cannot replace original receipt or infer acceptance from status. |
| C1 I/S | `test_current_schema_installation.py:654 test_object_free_install_creates_only_exact_current_truth`, `:919 test_current_reinstall_is_query_only_and_identity_stable` | Exact baseline only; stable object/row identity and no writes on reentry. | Update exact catalog expectations only for reviewed new baseline, never weaken exactness. |
| C2 I | `test_current_schema_installation.py:945 test_nonempty_owned_object_families_require_reset_before_effect`, `:968 test_cross_schema_objects_are_preserved_and_ignored`, `:1000 test_current_drift_rejects_without_repair_or_row_loss` | Noncurrent owned state refuses unchanged; foreign schema preserved. | No migration or repair; installer owner. |
| C3 I/S | `test_current_schema_installation.py:1083 test_empty_install_failure_rolls_back_every_schema_effect`, `:1091 test_caller_owned_outer_transaction_retains_authority`, `:1112 test_concurrent_empty_installers_serialize_to_one_current_schema` | Failure/outer rollback undo all installation, concurrent installers converge. | Include new indices/constraints without new transaction ownership. |

New F0 cards: N1 A introduces X, accepted B/C retain X despite image/key/declaration
change; N2 foreign workspace/runtime/node/socket, duplicate ID, relabeling an
accepted scope, retired/historical revival all reject atomically; N3 pending
continuation is exactly selected desired or same live draft head/original binding,
never arbitrary ancestry; N4 draft omission/tombstone preserves reservations and
cannot retire accepted X; N5 accepted removal retires only with lawful completion,
failed/uncertain removal does not; N6 global uniqueness wins races across workspaces
without disclosing the winner; N7 unresolved same-scope activity cannot be bypassed
by a fresh ID, superseded graph or abandoned request; bounded overflow refuses;
N8 exact authenticated authoring context includes CAS, permitted public selected
material and original introducing-action reference for pending lineage; preserves
F0 graph/key-read permissions, never exposes secrets or treats redacted graph as
complete input.

## Existing computation and missing connections

All source paths below are relative to
`control-plane-kit-operations/src/control_plane_kit_operations/` at the base.
Operations depends on Core `>=0.1.0`; the owning suite installs these source
packages together. Accepted Core is available here; independently pinned backend
repositories are baseline composition, not receiver adoption.

1. `cpk_server.py:538` maps trusted route requests to graph/draft commands.
   Inline `DeploymentProgram.prepare` (`deployment_program_interpreter.py:79`)
   starts a durable session, calls `SetDesiredGraph`, then requests a plan.
   It already carries expected current lineage, but its `SetDesiredGraph` call
   at :119 forwards only desired lineage. Therefore a later planning refusal
   cannot replace atomic current-CAS checking at graph admission.
2. `planning.py:413` owns action idempotency/replay, session, graph helper, action
   and commit. `graph_authoring.py:147` locks workspace, checks desired tuple,
   validates registered products, saves exact graph and identity projection,
   selects desired. The low-level standalone `GraphAuthoringService` has no
   introducing action; it cannot accept successor receivers without new explicit
   action provenance. Do not fabricate an action or allow it as an alternate path.
3. `desired_topology_drafts.py:193` create/revise owns graph, draft revision/head
   and action in one UoW. `:362` select/delete owns replay, session/workspace/draft
   locks, validation, projection/desired update or tombstone, action and commit.
   Saved revision selection currently permits an old valid revision; successor
   lifecycle must additionally reject unlawful retained identities.
4. `saved_deployment_preparation.py:112` pins existing selected revision/current/
   desired tuple before creating session/source records. Exact replay validates
   original immutable references. This is a reference-publication participant,
   not a second graph-authoring owner.
5. `desired_realized_projections.py:179` publishes projection/desired CAS/action;
   its preparation helper :286 acquires session first. Gateway overlap :137 and
   retirement :134 call this helper, then projection builder :88 locks workspace
   and rotation, then generic publisher. Guard acquisition solely in the inner
   publisher would occur too late.
6. `admission.py:226` verifies execute scope, action/admission idempotency, original
   replay, session, approval/risk, current+desired projection/generation and
   authority-use, then inserts request/action atomically. Insert lifecycle
   validation before request publication, under the common guard.
7. `advancement.py:285` locates request/run, checks replay, locks and revalidates
   worker fence/linkage, validates graph ownership and complete success, CASes
   current, appends event/action and commits. First acceptance/retirement belongs
   here, in the same UoW after the existing evidence checks.
8. `read_services/workspace_graph.py:292` explicitly redacts graph descriptors.
   Add a narrow public receiver-authoring projection; do not alter that existing
   safety contract into a raw complete-graph API.

Core extraction reuses `receiver_configuration.py:96`
`select_receiver_node_control_configuration_artifact`, its closed codec and
`NodeControlReceiverTarget`. Compare target workspace/runtime/node/socket with
the actual graph node and selected provider socket, not caller claims. Persist
the selected artifact content digest and declaration identity; do not regenerate
bytes, keys or IDs. Historical configuration has no invented receiver identity.

## Lock map and required compatibility transition

F0 prefix: command idempotency guards → workspace lifecycle advisory guard →
request → run → attempt → session → workspace → graph/index rows. Omit categories
not used. Locator reads may identify workspace/session/request before locking;
all relevant linkage is revalidated after locks. Proposed store method:
`stores.graphs.lock_receiver_lifecycle(workspace_id) -> None`, caller UoW only,
using `pg_advisory_xact_lock(hashtextextended(key, 0))` and namespaced key
`receiver-lifecycle:<workspace_id>`. Hash collision can over-serialize, never
authorize another workspace. No raw identity is emitted in conflict messages.

| Path | Existing acquisition / effect | Required planning disposition |
|---|---|---|
| Desired graph command | action key → session → workspace → graph/projection inserts → workspace update → action | Guard after replay, before session; carry current CAS; allocate introducing action before writes. |
| Standalone graph helper | workspace → graph/projection → workspace update | Explicitly reject successor receiver input without workflow provenance; never acquire guard after a caller's session lock. |
| Draft create/revise/select/delete | action key → session → workspace → draft → graph/revision/action | Same outer guard; retain head CAS and tombstone reference exclusion. |
| Planning/reference publication | action key → session → workspace → plan/action | Guard before session to preserve selection/deletion serialization with changed writers. |
| Saved preparation | session key → workspace → draft → new session/source inserts | Guard before workspace; new session insertion/FK locks must be included in review, not mistaken for an existing session row lock. |
| Projection generic/nested gateway | action key → session → workspace → rotation → projection/action | Outer entry resolves workspace and takes guard before preparation. Prepared UoW context must reach inner helper; helper cannot independently assume correct order. |
| Execution admission | action key → admission key → session → workspace → new request/action | Both idempotency guards precede lifecycle guard; retain replay before fresh admission. New-row/FK acquisition is part of the transaction proof. |
| Advancement fresh | action key → session → workspace → request → run → workspace CAS/event/action | Change coherently to guard → request → run → session → workspace; replay keeps request/run fence proof without new lifecycle admission. |
| Lifecycle claim | action key → session → request UPDATE → run/event/action INSERT | Remove inverse session→request edge; request must be acquired before session. Preserve DB lease time sampling and exact claim generation. |
| Lifecycle transition | action key → session → request → run → event/action | Request/run before session; current worker fence, close fencing and replay remain. |
| Execution coordinator | request→run (`coordinator.py:2245`), then effect attempts; specialized retry/uncertainty paths also lock runtime authority | Do not add a late lifecycle lock while holding these rows. Review any fresh executable admission/resume path against scope exclusion; effects remain outside transactions. |
| Rotation request | binding advisory key → rotation INSERT (workspace FK) | No later lifecycle acquisition. Record binding key as an auxiliary owner, not silently a command idempotency guard. |
| Rotation advance | rotation → CAS/checkpoint/transition rows; approval evidence read-only | No later prefix acquisition observed in `_advance_locked`; include FK/ON CONFLICT interactions in child review. |
| Rotation deployment advance | request → run → rotation → checkpoint/transition | Compatible prefix subset; preserve fenced deployment evidence. |
| Rotation approval | action key → session → rotation → approval/action INSERT | No later prefix row acquisition observed; FK edges must be considered with rotation update. |
| Workspace creation | absent workspace → new workspace/empty graph/identity projection | Empty topology cannot introduce a receiver; ordinary create replay preserved. |
| Store direct graph/projection/pointer mutations | mutable pointer UPDATE, immutable graph INSERT/ON CONFLICT, implicit FK locks | Workflow guard is not permission for a direct successor bypass. Public low-level paths must require derived evidence or reject unsupported successor material. |

Kepler's bounded consultation supports the preliminary coordinated lock child;
it does not yet approve the whole closure map. Draft/rotation rows may form a
named auxiliary suffix only after every dependency is checked; they are not
reclassified as graph truth by naming. Define deterministic class+ID order for
multi-owner acquisitions. A required later→earlier edge is a contract hold,
not a silent F0 amendment.

Implicit lock review must include UPDATE/INSERT/FK/ON CONFLICT, not just
`FOR UPDATE`: request→workspace/session FKs, actions→session, projections→graph/
workspace, rotations→workspace, approval→rotation/session, checkpoint upserts.
Before A freezes, its closure ledger must record transaction entry, resource and
exact key, acquisition statement, lock mode, actually conflicting counterpart,
held prefix, and release/commit boundary. A foreign-key check and a non-key
UPDATE do not automatically create the same wait edge as FOR UPDATE. Prove
actual conflicts from schema/SQL and contention tests; do not blanket-lock every
FK parent or expand into unrelated owners. New session insertion in saved
preparation requires its actual FK/uniqueness analysis, not an invented existing
session-row acquisition. The table above is a path map, not that completed
lock-mode/key closure ledger. No rolling mixed
old/new writer compatibility is claimed; activation requires one coordinated
Operations version against its exact schema, with no concurrent old writer.

Concrete nested-helper candidate for A: generic publication `execute` receives
workspace from its command; overlap/retirement `execute` resolve workspace from
the immutable rotation locator and later revalidate rotation/workspace linkage.
Each calls a revised `prepare_desired_realized_projection_publication(uow,
workspace_id, session_id, idempotency_key)` before building a projection or
locking session. Preparation acquires exact command key, resolves replay, then
takes the lifecycle guard and session row in that order. It returns a narrow
prepared publication context bound to the same UoW and exact workspace/session/
command key; generic publication consumes that context and revalidates its
identity. Direct callers must enter through this preparation before any later
row, or fail the precondition; no first lifecycle acquisition is hidden in the
inner persistence helper. Reacquiring an already-held identical key is distinct
from acquiring another workspace/session/action key. A's review must trace those
identities and enforce the entry contract, not simply assert a table order.
This proposed context carries transaction preparation, not reusable authorization
and not a process-global lock registry.

## Proposed exact interface and storage contract

These names are a review candidate, not exported code. Reuse existing
`GraphProjectionLineage` and `TrustedCommandContext`; do not add a second topology
or execution language.

```text
ReceiverAdmissionSource(
  current: GraphProjectionLineage,
  desired: GraphProjectionLineage | None,
  desired_graph_revision: int,
  pending_draft: (draft_id: str, head_revision: int, graph_id: str) | None)

ReceiverGraphBinding(
  target: NodeControlReceiverTarget,
  graph_id: str, realized_projection_id: str,
  selected_configuration_digest: str, declaration_identity: existing Core value)

ReceiverAuthoringContext(
  workspace_id: str, source: ReceiverAdmissionSource,
  bindings: tuple[public selected receiver configurations, graph provenance,
                  original introducing_graph_id/action_id/draft_id references])
```

Source tuple is caller expectation, never authority. Compare current authored+
projection and desired authored+projection+generation under lifecycle/workspace
locks; pending draft must be exact live head. Add this required successor input
to `SetDesiredGraph`, draft create/revise/select and realized publication, and
include it in intent fingerprints. Existing historical commands retain their
profile semantics; missing source with successor material fails closed, without
guessing it from current state. `PrepareDeploymentProgram` forwards its already
present expected current tuple to graph admission. A source mismatch is conflict,
not permission to generate a replacement ID or rewrite a saved request.

Graph store owns F0's two normalized tables, workspace-owned graph/action/draft
references, global UNIQUE(receiver_id), immutable scope/introduction and
write-once first acceptance/retirement. Reserved rows survive draft tombstone;
referenced history cannot be deleted. Save/selection/publication is not exposed
as a caller-writable binding registry. Operations derives bindings from exact
Core-validated graph material within caller UoW. Deterministic identity
projection is available for draft graphs too; the schema child must prove its
durable graph/projection FK representation before choosing insert order.

Proposed graph-store operations are `receiver_introduction(workspace_id, id)`,
`receiver_bindings(workspace_id, graph_id, projection_id)`, and a caller-UoW
admission method accepting exact graph/source plus introducing action/draft
provenance. It reserves absent IDs or validates allowed continuation and persists
derived bindings. A separate advancement operation records accepted/retired
witnesses after complete-success checks. None commits, allocates IDs or calls
providers. Conflicts are fixed bounded categories; global uniqueness refusal
does not reveal another workspace or distinguish its receiver membership.

Execution store owns
`unresolved_receiver_scope(workspace_id, runtime_id, node_id, provider_socket_name)`.
Result is a closed clear/conflict/overflow decision, never a partial list.
Index from graph bindings at stable scope through both base/desired plan
associations to requests/runs, including historical superseded/abandoned bindings.
Existing plan base/desired indices are present; current active-plan partial index
alone is insufficient for abandoned uncertainty. Query work and candidate count
must be bounded. Lookup and its required execution indices belong together in C;
B does not publish a provisional query or active-status-only index contract.
Final index keys, predicate and limit must be reviewed before C targets. C's
schema change follows the same exact-baseline/object-free-only installation law.

Do not equate request CANCELLED/ABANDONED or run terminal status with resolved
provider reality. The closure includes attempt STARTED/UNCERTAIN/ABANDONED and
durable resolution evidence, not merely selected graph IDs. The exact status/
evidence predicate is a remaining execution-owner design obligation before that
child's targets: include queued work, active attempts and unresolved creation/
removal, exclude the advancement's own run only on the existing verified
complete-success evidence (never a caller-requested omission), and prove a newly
admitted/resumed conflicting request cannot race the check. An unrelated node's
ordinary authoring must remain possible. This document does not pretend that
this predicate or bounded SQL has already been implemented or fully reviewed.

Authenticated context preserves F0's graph/key-read permissions and workspace
scope, including `DELEGATION_KEY_READ` for public verification material as used
by `cpk_server.py:329–335`; edit permission alone does not grant key reads. It
returns one consistent pinned view, bounded complete selected public material,
and no raw full-graph dump. Exact selected artifact content may contain public
verification keys; arbitrary metadata, environment secrets, signing keys,
provider bodies and tokens are excluded. Oversized/invalid material refuses
rather than truncating authoring input. O1 owns Operations projection/application
mapping; actual server transport and pre-wire authoring remain Servers #238.
Pending context exposes the original introducing-action reference required by
F0 section 3, correlated to the exact live head and graph-owned introduction.
Echoing that reference in a command is an expectation to revalidate, never
caller-supplied evidence of lawful origin.

## Proposed child topology and release conditions

1. **O1.A — Coherent lifecycle lock protocol.** Own the complete intersecting
   writer map above and explicitly reviewed structural-test supersession. No
   receiver semantics/schema activation. Preserve all existing behaviors and
   replay/fence laws; real PostgreSQL blocking tests cover both schedules for
   advancement vs lifecycle, selection vs advancement, selection/delete/planning,
   nested publication and rollback. Freeze source ceiling only after auxiliary
   closure review; no piecemeal advancement-only merge.
2. **O1.B — Exact graph-owned lifecycle storage.** After A, define/install/verify
   exact tables, FK/unique/write-once constraints and deterministic draft projection
   representation. Keep unresolved-scope lookup and its execution indices together
   in C, after that predicate is settled. Prove source-byte preservation,
   uniqueness races, orphan rollback and noncurrent refusal. No new successor
   admission becomes available through public command or direct store paths.
   If inert storage cannot be landed without a bypass, combine B with C rather
   than introducing a temporary permissive mode.
3. **O1.C — Coordinated graph/execution lifecycle admission.** After A/B, integrate
   inline/draft/saved/publication, execution admission/resume and advancement in
   one coherent activation. Own N1–N7 and complete entrypoint audit, current+desired
   CAS, exact replay, unresolved-scope predicate, atomic witnesses. This is one
   durable invariant across existing owners, not several independently activated
   flags. If still too broad, split implementation internally while holding its
   aggregate merge until every path rejects or enforces successor semantics.
4. **O1.D — Authenticated complete authoring context.** After C, expose N8 using
   approved source tuple/material, bounded Operations read and framework-neutral
   adapter mapping. Preserve existing redacted reads. Hand exact API/examples to
   Servers238, Operations1883/1884 and Core1886. No SDK/signing/provider adoption.

North owns actual child creation/order/release. A's test transition and C's
unresolved predicate are not optional follow-up hardening. They are required
before dependent targets/source. A/B/C/D names here are provisional issue labels,
not GitHub issue numbers or a claim of four approved PRs.

## Validation, security, data and history

Planning validation: `git diff --check`; no executable examples changed. Future
owning gate is `./control-plane-kit-operations/test.sh`, with exact clean sibling
architecture-testing `7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef` as declared in the
script/CI. Establish prerequisites only when released. No host Python/database,
custom harness or executable gate ran for this plan. Actual target-red will
demonstrate the selected missing law after interface/lock review, not broken
collection, fixture setup or schema apparatus.

Security: no new runtime surface in this documentation. Proposed implementation
adds admission and bounded authenticated read behavior, consumes existing scopes,
and returns no secret material. Identity correlation does not authenticate a
receiver or confer current permission. Global uniqueness conflicts are redacted.

Data/history: graph, binding, introduction, action and CAS share one caller UoW;
advancement witnesses join pointer/event/action atomically. Exact retries return
original evidence; changed intent conflicts. Existing immutable attempts/receipts
survive later lifecycle state. Failure rolls back new truth; no automatic cleanup,
compensation, reset, migration, physical deletion or provider effect is introduced.

Residuals: auxiliary/FK lock closure and unresolved-scope predicate require
independent resolution before source. No live deployment or old-writer coexistence
is established. Core C1 is complete; foundation G1 and all downstream composition/
live gates remain open. Timed prompt remains off.
