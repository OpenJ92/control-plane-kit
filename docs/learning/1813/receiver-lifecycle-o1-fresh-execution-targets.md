# O1.C3 fresh execution and acceptance targets

Status: final joint B+C architecture, transaction, security and test-integrity
review is PASS. The sole missing admission-versus-selection race pair passed
in the full 2,097-test gate; PR #1913 merged at `b4a139ae` with the exact tested
tree. Aggregate PR #1901 promotion, required checks and coordinated #1897/#1898
closure remain pending with North. The final milestone below supersedes earlier
checkpoint holds without rewriting their historical evidence. C3/#1904 was
accepted at `eaa47463` after its 2,095-test gate. The original C3 selected base
is C2 merge `1d09c78d6598b4102c5c39012687f6ae9ec4d163`, containing B `a1ce6fc9`
and C1 `89dd5229`. Governing issue is #1904; branch
`codex/1904-receiver-execution-targets` targets
`codex/1882-receiver-lifecycle-integration`. Reuse the frozen
[joint contract](receiver-lifecycle-o1-joint-freeze.md),
[C1 evidence interface](receiver-lifecycle-o1-execution-targets.md),
[C2 handoff](receiver-lifecycle-o1-graph-admission-targets.md) and
[A lock ledger](receiver-lifecycle-o1-lock-ledger.md).

C2 merged after ordinary hosted Operations 2,036 tests/1,204.322 seconds/OK,
compileall and clean import. The tested PR merge has the reviewed source tree.
The redundant local run was cancelled incomplete after hosted green, with its
exact resources absent. [Evidence](https://github.com/OpenJ92/control-plane-kit/pull/1907#issuecomment-5903874710)
and [final review](https://github.com/OpenJ92/control-plane-kit/pull/1907#issuecomment-5903870775)
are predecessor evidence, not C3 acceptance. B/C parents remain open.

## Governing laws inspected before target design

Original assertions stay under their current owners. The new targets extend
observable permission, rollback and history laws rather than copying their
fixtures or enforcing helper names.

| Law / existing test | Classification and observable obligation | Structure to discard |
| --- | --- | --- |
| E1–E3, execution admission and A lock tests | Isomorphic original receipt, approval, projection-generation and request/action rollback; strengthened receiver-source validation before new admission. | Matching a receipt is not fresh permission; no caller scope list. |
| L1, `test_run_lifecycle::test_lifecycle_replay_survives_close_but_new_transition_is_fenced` | Preserve original claim replay after session close and refusal of new Start; strengthen fresh claim/Start/Resume with L before request/run/session/workspace. | Do not apply fresh authority to evidence-only replay or change original fingerprints. |
| A1/A3–A5, `test_current_graph_advancement::test_incomplete_uncertain_or_failed_evidence_cannot_advance`, `::test_late_action_failure_rolls_back_pointer_and_event`, `::test_concurrent_advancement_has_one_winner` | Preserve complete-success/association/fence and one winner; strengthen rollback to include first-acceptance/retirement witnesses. | A succeeded label is insufficient; direct pointer setup is not receiver acceptance. |
| H1, `test_revision_history_advancement::test_success_without_receipt_is_none_recorded_then_real_acceptance_survives_pointer_change` and missing/duplicate receipt tests | Preserve exact event/action attribution across later pointers/claims; first acceptance stays original across A→B→C. | Current pointers do not reconstruct original authority or replace accepted history. |
| `test_postgres_failed_run_compensation_attempt::test_next_step_requires_every_prior_binding_succeeded_with_outcome` | Isomorphic per-step inverse evidence and order; strengthen fresh inverse admission with lifecycle serialization and original coverage. | Individual inverse success is not whole-run disposal. |
| `test_postgres_atomic_effect_attempt_fold::test_recovery_first_fold_and_replay_never_touch_direct_outcome_stores` | Isomorphic evidence-only recovered folding; no invented direct outcome or redispatch. | Recovery is not a new receiver permission gate. |
| `test_postgres_effect_attempt_start_first_replay::test_forward_and_compensation_first_start_commit_complete_truth`, `::test_exact_restart_replay_is_observation_only_after_expiry` | Preserve complete atomic start and ExistingAttempt recovery; strengthen actual commit-to-adapter boundary. | Do not convert ExistingAttempt into NewlyStarted or require fresh eligibility for observation-only replay. |
| C-N9/C-N10/C-N11 and C1 query/classification tests | New integrated law: complete cancellation proof plus closed future gates can permit reuse; known success/failure/inverse/abandonment without disposition remains conflicting. | Do not copy C1 folds, remove own requests globally, or introduce a runtime closure Boolean. |

## Selected source trace and missing connections

Anchors describe selected `1d09c78d`, not hypothetical post-C3 source.

- `admission.py:ExecutionAdmissionCommandService.execute` already takes K/L,
  retains approval and exact graph/projection/generation checks, and calls C1's
  private request admission for immutable coverage. It lacks the complete C3
  receiver provenance/permission check. C1's public `add_request` already
  refuses nonempty affecting coverage; preserve that positive empty/refusal law.
- `lifecycle.py:_claim` (369) and fresh `_transition` (473) take request/run
  locks without L. Claim writes claim/run/open-event/action together; Start and
  Resume grant new execution eligibility. Pause/Complete/Fail/Cancel do not.
  Original action replay is already a separate branch and stays separate.
- `activity_run_retry_interpreter.py:execute` (57),
  `execution_lease_recovery_interpreter.py:execute` (62),
  `failed_run_compensation.py:_admit` (193), and
  `failed_run_compensation_attempt.py:_start` (135) retain their existing
  request/run/session/approval/program proofs but lack the fresh L prefix.
  Inverse start already derives/rechecks the required attempt set before the
  workspace/program suffix. Add no late earlier lock after that suffix.
- `effect_attempt_start_interpreter.py:_execute` (103) currently locks
  request/run/attempt before choosing ExistingAttempt versus first start.
  The first branch writes start event, exact intent and attempt and commits
  before return (192–213). Preserve this atomic seam; select/recheck the
  nonlocking branch locator before adding the appropriate L prefix.
- `postgres/execution.py:claim_request`, `rotate_request_claim`, `add_run`,
  and enabling `compare_and_set_run_status` still expose independent writes.
  `postgres/effect_attempt_store.py:insert_absent` and
  `postgres/effect_attempt_intent_store.py:insert` similarly lack the complete
  fresh semantic context. Public nonempty-affecting writes must refuse;
  legitimate owners complete private writes in their existing UoW. Guards
  prove serialization only, not authority. Preserve nonaffecting/evidence use.
- `coordinator.py` native path (1410 onward) calls the start service and
  distinguishes NewlyStarted from ExistingAttempt before adapter invocation.
  The legacy branch (1383–1392) selects only the exact ingress/socket operation
  types already classified non-receiver by C1 (receiver_execution_scopes.py:
  87–108). It is not an established receiver bypass. Preserve these positives
  even when the graph contains receivers, including ingress's own authority.
  Actual node/runtime effects use the native start path; unknown operations
  cannot acquire coverage merely from the branch label or graph membership.
- `advancement.py:execute` (280) already takes L before request/run/session/
  workspace and verifies complete success. Its public current CAS now refuses
  C2 receiver-affecting advancement, and it does not write B witnesses.
  The existing advancement owner must perform the complete private CAS,
  real event/action, first acceptance and accepted removal retirement atomically.

## Target public shape and semantic decisions

No new command DTO, route, root export, caller bypass/ignore list, admission
token or recovery service. Exercise the existing command and store APIs.
Fresh owners reuse immutable original request/plan/projection/scope records,
current pinned eligibility, approval and fence. Shared internal derivation may
consume existing C1/C2 owners but must not reimplement their history classifier.

Original retry/compensation retains occupied original scope while lawful work
proceeds; it does not grant a competitor clearance. Acceptance handles its own
exact successful run inside existing completion/association proof, while prior
and other histories remain accounted for. C-N11's existing classifier result
can become usable by C2 only after all fresh/direct paths are closed in this
integrated source. This is a proved invariant, never a runtime permission flag.

Do not reuse C2 `_validate_receiver_admission` for original retry/compensation:
its nonconflicting requirement would reject that original occupied work.
Reuse retained-material/origin readers and C1 derivation separately. Exact
original association establishes which work; current pinned provenance,
approval, journal eligibility and fence establish whether it may execute now.
No historical graph is selected merely because it contains X; no retired or
replaced X, node-name substitution, or rewritten first-acceptance coordinate.

STARTED/intent/attempt commit precedes unlocked I/O. A late write or commit
failure must cause zero adapter calls. NewlyStarted permits one dispatch after
UoW/L release; ExistingAttempt, old command replay and folding do not redispatch.
Crash after commit but before I/O leaves conservative STARTED conflict. This
liveness limitation is explicit; no automatic redispatch or disposal is added.

Kepler's bounded consultation concurs with these distinctions and requires the
exact proposed target shape before final checkpoint review. No concrete split
is required merely by file count. A new disposal policy, independently owned
acceptance service or authority token would require stopping and splitting;
none is proposed here.

## Target matrix and fixture plan

| Target group | Positive and negative observations |
| --- | --- |
| Fresh owner prefixes | Real PostgreSQL blocker proves L precedes request/run/attempt/session/workspace on claim, Start/Resume, retry, active/expired renewal, takeover, begin compensation, inverse start and effect start. Recheck branch/locator/set under locks. Positive original eligibility and exact replay remain. |
| Public writer closure | Direct affecting request/claim/rotation/run/enabling CAS/attempt/intent cannot mutate, even while holding a guard. Empty/nonaffecting and evidence operations retain meaningful positives. Existing C1 add-request coverage is reused. |
| Original retry versus reuse | Real failed original run can retry or start its admitted inverse under current eligibility; C2 competing receiver selection at the same scope remains refused. Stale pins/approval/fence refuse fresh work without any added row. |
| No-dispatch reuse | Real cancellation with complete all-run C1 evidence permits C2 reuse only with closed fresh gates. Missing/foreign/duplicate cancellation, prior dispatched run, orphan intent and capacity remain refusals; reuse C1's proof tests rather than recreate its fold. |
| Both real schedules | Cancellation/clearance plus competing reuse first makes old fresh activation refuse. Fresh activation first commits STARTED; reuse then refuses even after cancellation/lease expiry. Include selection/advancement and admission/scope-lookup contention through existing A lock witnesses. |
| Dispatch transaction | Recording adapter observes committed exact event/intent/attempt and can acquire L; late action/commit failure makes zero calls. Replaying ExistingAttempt never redispatches. Actual node/runtime operations use native admission; socket/ingress non-receiver positives remain valid even in receiver-bearing graphs. |
| Coverage/material | Forward and recorded inverse material fit immutable admitted scopes. Relocation retains both original runtimes; wrong material or out-of-coverage effect refuses without expanding the index. |
| Acceptance/retirement | Real C2 introduction → canonical initial/teardown plan with explicitly retained complete-success evidence → real advancement records first acceptance/retirement. Desired-only B/C no-op plans refuse execution and preserve accepted A. Actual accepted A→B→C is transferred to #1912, not proved by that refusal. Desired omission, tombstone, failure and stale/incomplete completion do not retire. Losing CAS/late/deferred failure rolls back all records. Native dispatch closure is exercised separately. |

Reuse `ReceiverExecutionScopeFixture` for production admission/lifecycle/retry/
compensation/coordinator composition and C1 retained evidence. Reuse C2 graph
builders/command owners for lawful introduction, not B private storage as an
admission claim. Receiver acceptance positives must use meaningful canonical
plans and explicitly identified complete-success history premises. Real
advancement alone creates acceptance; C2's empty-plan seed is not that proof.
Reuse `LifecycleLockFixture` barriers over real connections for schedules.
Recording adapters have no provider I/O and do not decide lifecycle truth.
Corrupt-history negatives are explicit rollback-scoped fixture premises;
cleanup registers immediately and shared truth must be restored on assertion
failure. No fixture implements an alternate admission machine.

The staged C2 method
`test_receiver_admission_execution_evidence::test_exact_no_dispatch_cancellation_still_requires_c3_fresh_gate_closure`
is explicitly superseded by
`::test_exact_no_dispatch_cancellation_is_usable_by_closed_c3_consumer`.
This is the frozen C-N11 consumer transition, not a relaxed historical fold:
the same real cancellation still classifies `requires-fresh-gate-closure`, its
original action/event remains exact, and the fully closed consumer must now
introduce the new receiver without recording acceptance. Meridian concurred
with this one-to-one target change. All malformed/incomplete/capacity negatives
remain. The positive is insufficient without every fresh/direct gate and both
concurrency schedules.

Fixture audit discovered `LifecycleLockFixture.seed_distinct_latest_run` uses
public `add_run` to seed deliberately divergent retained FAILED/latest truth.
Once source closes that public writer, translate this setup explicitly to a
private retained-history premise while preserving its original lock/fence
assertions; do not relax production admission. No such predecessor fixture
translation has been applied in the target-only draft.

An acceptance-fixture draft exposed an existing ceiling: C2's
`sdk_health_graph` is graph-admission material, with no registered executable
product metadata/configuration-slot contract. Management execution permits
nonempty canonical MANAGEMENT_GRAPH_PAIR_V1 initial/teardown plans, not manual
StartNode/Reconcile plans or arbitrary receiver-bearing reconcile changes.
The original manual-plan draft earns no evidence and must be replaced before
target review. North and Meridian concurred with the bounded replacement on
[#1904](https://github.com/OpenJ92/control-plane-kit/issues/1904#issuecomment-5904154400):

The no-op continuation part of this historical proposal is superseded by the
completed-gate correction and #1912 transfer below: empty plans cannot enter
execution, and these continuations cannot supply accepted B/C evidence.

- Use existing planner owners for canonical initial/teardown and genuine
  no-op continuations, with typed registered product/configuration-slot material.
- Identify retained complete-success execution records explicitly as premises.
  Preserve association, coverage, approval, journal, intent and outcome evidence.
- Never seed the target current pointer, CURRENT_GRAPH_ADVANCED event/action,
  first-acceptance witness or retirement witness. Real advancement creates them.
- Exercise real supported native fresh gates, STARTED, dispatch, retry and both
  schedules separately, including successor first-start wherever supported and
  receiver-specific provenance/retirement negatives.
- Preserve damaged/missing evidence, late failure and losing-CAS negatives.
  A→B→C denotes unchanged-effective-graph identity transitions, not reconcile.

The revised intended claim is **real transactional receiver acceptance/retirement
over contract-valid retained consumer inputs, with upstream completion assumed;
native dispatch closure separately exercised; successor-health attribution and
provider reachability unproved**.
The selected health trust owner still decodes legacy
`WorkloadNodeControlConfigurationCodec`, while receiver lifecycle consumes
`ReceiverNodeControlConfigurationCodec`. C3 adds no health adopter/runtime
support. Planning concurrence is not target PASS or source/run release.

Subsequent concrete review identified a stronger fixture limit. The signed
health producer records preparation, original key/use associations and the
correlated `NodeHealthReadResult`; a bare typed successful outcome does not
represent that complete producer history. `_health_receiver_trust.py:69–89`
requires legacy configuration at the selected workload slot;
`health_signing_authority.py:_target` constructs the graph-revision target.
Those records cannot establish successor receiver-ID configuration coverage.
The invalid manual-plan acceptance tests were removed before execution.
North explicitly revised the earlier full-coherent-history premise to the
advancement-consumer boundary in
[#1904](https://github.com/OpenJ92/control-plane-kit/issues/1904#issuecomment-5904298267).
Meridian and Kepler independently concurred. This resolved the fixture-design
hold, not target review or source/run release. No receiver law or production
validation is weakened by that evidence-claim correction.

The advancement consumer itself (`advancement.py:280–424,657–715`) validates
request/run/plan/projection/claim association and complete successful journal
coverage. C1 additionally validates native attempt/intent/outcome associations;
neither proves original signed-health preparation/authority. The narrower
fixture tests exactly these consumers over explicitly synthetic evidence
premises. `receiver_canonical_acceptance_fixture.py` composes typed registered
product/slot material with real planning, approval, admission and lifecycle
owners. `receiver_recorded_completion_fixture.py` uses the canonical plan's
own dependency order, existing intent translation, Core folds and typed
attempt/intent/outcome constructors. Connected-stage outcomes retain their
native format; signed-health completion is an assumed consumer input, without
fabricated legacy preparations. Real advancement alone creates all target
current pointers and acceptance/retirement receipts. No fixture implements a
scheduler, signing owner or dispatch pipeline.

The existing adoption owner is
[O2 #1883](https://github.com/OpenJ92/control-plane-kit/issues/1883), which owns
receiver-bound health preparation/profile, selected trust, original grants and
current signing reload. Its SDK/Servers composition and coordinated rollout
must prove successor-health authority/result → completed execution → advancement.
C3 and joint acceptance cannot claim that chain. O3 #1884 owns subsequent
node-control commands, not this health gap. No new issue is needed for fixture
pressure.

## Validation and boundaries

### Draft review corrections and exact owner laws

Meridian's [first draft HOLD](https://github.com/OpenJ92/control-plane-kit/issues/1904#issuecomment-5904482388)
identified five bounded target corrections. Both race schedules now use real
Pause→Cancel from RUNNING; direct-attempt negatives obtain their original
STARTED through the real first-start service; the four staged lock laws below
are explicitly superseded; fresh-owner refusal/recheck targets and a real
nonreceiver ingress dispatch positive are added. This is a correction record,
not a claim of target PASS or executable evidence.

| Predecessor staged test | C3 replacement in the same file |
| --- | --- |
| `test_run_lifecycle::test_first_claim_takes_request_before_session_without_lifecycle_guard` | `test_first_claim_holds_c3_lifecycle_guard_before_request_and_session` |
| `test_postgres_lifecycle_execution_locks::test_retry_and_recovery_take_request_and_run_before_session_without_new_lifecycle_guard` | `test_retry_and_recovery_hold_c3_lifecycle_before_request_run_and_session` |
| `test_postgres_lifecycle_execution_locks::test_lifecycle_transition_takes_request_before_session_without_new_guard` | `test_fresh_start_holds_c3_lifecycle_before_request_and_session` |
| `test_postgres_lifecycle_execution_locks::test_compensation_attempt_prefix_is_request_run_attempt_workspace_program` | `test_fresh_compensation_attempt_prefix_is_lifecycle_request_run_attempt_workspace_program` |

Only each fresh command's L-free assertion becomes L-held; all later row-lock
observations survive. `test_compensation_action_key_precedes_request_even_when_key_is_reused`
still requires L-free while waiting for K. Original receipt replay remains
L-free. These are strengthened fresh-permission laws, not changes to replay.

The following exact methods supplement the fresh lock-prefix tests. The shared
pin witness commits desired-revision drift only after the real command is
observably blocked on L. It requires the owner's typed refusal and identical
durable truth, restores the premise in `finally`, then runs the exact unchanged
command successfully. Ordinary run state, claim and approval stay eligible.

| Fresh owner | Refusal / positive / recheck target |
| --- | --- |
| Execution admission | `test_receiver_native_first_start::test_fresh_execution_admission_requires_original_receiver_binding`: corrupt the retained binding digest, require no-write refusal, restore, then admit the same approved canonical plan. |
| Claim | `test_receiver_fresh_lifecycle_permissions::test_fresh_claim_rechecks_changed_pins_after_lifecycle_wait`; exact original replay in `test_original_claim_replay_does_not_take_fresh_lifecycle_permission`. |
| Start | Same file, `test_fresh_start_rechecks_changed_pins_after_lifecycle_wait` and `test_start_rechecks_locator_state_after_cancellation_while_waiting`: real evidence-only cancellation changes CLAIMED to CANCELLED while Start waits; stale locator cannot enable it. |
| Resume | Same file, `test_fresh_resume_rechecks_changed_pins_after_lifecycle_wait`, from real Start→Pause. |
| Active renewal, expired renewal, takeover | `test_receiver_fresh_recovery_permissions::test_renewals_and_takeover_recheck_changed_pins_after_lifecycle_wait`, one subcase per decision. |
| Retry | `test_receiver_fresh_reuse_permissions::test_stale_original_retry_refuses_but_completed_retry_receipt_stays_original`: stale generation refuses before any newer retry exists, restored original succeeds, exact completed replay survives later drift. |
| Begin compensation | `test_receiver_fresh_recovery_permissions::test_begin_compensation_rechecks_changed_pins_after_lifecycle_wait`. |
| Inverse first start | Same file, `test_fresh_inverse_rechecks_changed_pins_after_lifecycle_wait` and `test_inverse_rechecks_required_program_set_after_waiting_on_workspace`: remove a retained step only after earlier attempt selection/locks and workspace wait; refuse without writes, restore the exact row, then start the unchanged inverse. |
| Effect first start | Same file, `test_fresh_effect_rechecks_changed_pins_after_lifecycle_wait`; `test_receiver_native_first_start::test_mismatched_original_receiver_binding_refuses_before_first_start` adds receiver-specific origin/binding corruption with restored valid start. |
| Actual material and immutable coverage | `test_receiver_native_first_start::test_substituted_receiver_configuration_cannot_use_original_scope_and_plan` and `test_actual_material_cannot_move_outside_original_runtime_coverage`. |
| Nonreceiver ingress in receiver-bearing material | `test_receiver_nonaffecting_dispatch::test_real_ingress_teardown_dispatch_keeps_its_authority_in_receiver_graph`: actual coordinator/dispatcher/ingress owner and secret-use authorization, stubbed provider, one unlocked teardown, exact STARTED/SUCCEEDED and owned removal, unchanged receiver acceptance. Preceding execution completion remains assumed. |

Reused owner tests establish the remaining original laws rather than requiring
every target to duplicate the same authorization matrix:

| Owner law | Existing exact method(s) |
| --- | --- |
| Current effect fence/expiry and rotated authority | `test_postgres_effect_attempt_start_eligibility_rollback::test_absent_start_requires_current_unexpired_exact_fence`; `test_current_rotated_authority_rejects_old_attempt_fence_without_clock` |
| Lease request/run/fence/generation eligibility | `test_postgres_execution_lease_recovery_eligibility_errors::test_request_run_expiry_fence_and_generation_matrix_is_exact` |
| Original inverse program/source/graph/approval/fence and prior outcomes | `test_postgres_failed_run_compensation_attempt::test_changed_program_source_graph_approval_authority_and_fence_fail_closed`; `test_next_step_requires_every_prior_binding_succeeded_with_outcome`; `test_first_incomplete_step_starts_one_exact_linked_inverse_atomically` |
| Native ExistingAttempt never dispatches | `test_postgres_effect_attempt_coordinator_concurrency::test_every_existing_attempt_has_zero_provider_calls` |
| Both A contention orders | `test_current_graph_advancement::test_selection_and_publication_vs_advancement_preserve_cas_in_both_orders`; `test_advancement_and_lifecycle_retry_recovery_preserve_outcomes_in_both_orders` |
| Completion/association/fence and late rollback | Same file, `test_incomplete_uncertain_or_failed_evidence_cannot_advance`; `test_foreign_stale_and_larger_fences_leave_no_advancement`; `test_late_action_failure_rolls_back_pointer_and_event` |
| Forward and inverse relocation scope | `test_receiver_execution_scope_derivation::test_forward_and_base_compensation_preserve_both_relocation_runtimes`; `test_stop_inverse_uses_base_even_when_desired_node_is_elsewhere`. These prove derivation, not an executed relocation. |
| C-N9/C-N10 and malformed no-dispatch evidence | `test_receiver_execution_scope_classification::test_independent_orphan_intent_refuses_even_when_attempt_prefix_is_empty`; `test_cancel_witness_must_match_every_original_identity_and_event_coordinate`; `test_later_accepted_retry_does_not_account_for_older_run_of_same_request`; `test_successful_inverse_does_not_invent_whole_run_disposal`; `test_cancellation_after_uncertain_dispatch_does_not_prove_no_dispatch`; `test_duplicate_cancel_action_candidates_are_unavailable` |
| Shared evidence budgets | `test_receiver_execution_scope_row_budgets::test_compensation_steps_and_bindings_consume_the_same_effect_budget`; `test_8192_journal_events_fit_and_8193_refuse_without_partial_history`; `test_256_original_runs_fit_and_257_refuse_without_latest_run_shortcut`; `test_attempts_and_independent_intents_share_2048_row_budget` |

None of these reuse mappings promotes predecessor green into integrated C3
green. The ordinary owning gate must still exercise the completed source tree.

Concrete target files now cover fresh lock prefixes, public affecting-writer
refusal and nonaffecting positives, native atomic start/commit-to-dispatch,
original retry/inverse versus competing reuse, both forced lifecycle-lock
schedules, real successor first-start after assumed predecessors, material and
binding substitution, and consumer acceptance/no-op/teardown/rollback. The
two-caller advancement test asserts one winner; the separate PostgreSQL lock
witnesses prove forced overlap, so serial scheduling is not described as proof
of both orders. Static Kepler recipe review found no constructor/association
blocker in the initial consumer fixture, but gave no target PASS or executable
credit.

Reuse existing owner laws rather than duplicating them:

- C1 `test_forward_and_base_compensation_preserve_both_relocation_runtimes`
  retains both immutable runtime coordinates; C3 checks actual out-of-coverage
  runtime material at first start.
- C1 orphan-intent, exact cancellation attribution, prior-run, inverse and
  shared-budget tests retain all negative folds; the C2 consumer and both C3
  schedules exercise their integrated permission consequence.
- Existing first-start eligibility/rollback and lease recovery matrices retain
  exact current-fence, expiry and foreign-request refusals; original lifecycle
  replay tests retain their immutable receipt and generation laws.
- Existing C2 accepted-continuation tests retain retired/foreign identity
  rejection. C3 adds real acceptance/retirement production and receiver binding
  and configuration checks at native first start.

Completed-target independent review passed before checkpoint/run release; file
presence and static inspection earned no green evidence. The run exposed the
two setup errors recorded below. Predecessor fixtures using public writers may need explicit retained
premise translation only after source closure, with original assertions intact.

North released the ordinary hosted causal-red gate after independent target
and committed-byte review. Owning command remains `./control-plane-kit-operations/test.sh`
with clean architecture sibling `7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`.
No host Python, targeted selector, alternate runner, harness change or duplicate
local run occurred. A later source release requires genuine missing-behavior
evidence, not collection/fixture failures. Preserve all predecessor
assertions; no skip/xfail or broad fixture repair hidden in targets.

### First owning red and bounded fixture correction

Reviewed target head `799a14370e074e2fc7aa650c8c949514cbc7ad91` was published in
[draft PR #1908](https://github.com/OpenJ92/control-plane-kit/pull/1908).
The sole [hosted Operations gate](https://github.com/OpenJ92/control-plane-kit/actions/runs/36673553306/job/109753537427)
completed 2,083 tests in 1,348.586 seconds with 39 failures and 14 errors, exit 1,
without timeout. Actual CI checkout `46f6606e6ec1adadc61a7b8163051ecc35cb17b2`
has the reviewed tree `0a295606d8b9e62284269a7779299157198029c6`; the terminal
log confirms the required architecture pin. Integrity/installation/collection
completed; subsequent compileall and clean-import phases were not reached.
Full job-log SHA256 is
`6896f7f754fe43a4ecdbd86a011ccaf992e2672646aa9a4a280e838eefad7b93`.
[Exact attribution](https://github.com/OpenJ92/control-plane-kit/pull/1908#issuecomment-5905062450)
and [Meridian's independent review](https://github.com/OpenJ92/control-plane-kit/pull/1908#issuecomment-5905061777)
record the evidence and its limits.

The 39 failure entries comprise 28 missing-L observations, six public-writer
closure gaps, one stale-original retry gap and four receiver admission/material
refusal gaps. The 14 error entries comprise ten early C2 public-pointer guard
refusals, the C-N11 consumer refusal, one clearance-first schedule failing to
wait on L before existing cancelled-run eligibility refusal, and two genuine
Resume setup errors. No unrelated predecessor failed beyond the four documented
lock supersessions and C-N11 consumer transition. No schema cascade or global
apparatus failure occurred. Counts include repeated subtest entries.

The two Resume methods used `IdempotencyKey("start")` for setup Start, already
owned by inherited `StartOperationSession`. Production correctly rejected the
different intent before Pause/Resume. Both implementer and static review missed
the collision; it earns no Resume-law credit. North released only distinct
setup Start keys (`resume-lock-setup-start` and `resume-recheck-setup-start`) in
those two methods and this learning record. All intended Resume commands,
original/replay identities, fences and assertions remain intact. The correction
is statically reviewable but unexecuted; no automatic full rerun was released.

Missing-L failures do not prove pin mutation/reread or locator assertions that
were never reached. Early public-pointer refusal does not prove downstream
acceptance, no-op retention, retirement, winner/rollback or actual ingress
dispatch. The complete opposing schedules remain unproved. The original red
is substantive missing-behavior evidence only at its reached boundaries.
Application source remains held pending exact correction review and North's
separate release; presumed upstream completion and O2's full-chain gap remain.

Security/data/history: existing auth, approval, fences and bounded errors stay
authoritative. Durable intent, STARTED, acceptance and retirement have explicit
owning UoWs; no transaction spans external I/O. No schema change, migration,
backfill, credential operation, provider effects or new public data exposure.
Ambiguous effects remain conflicting. B/C joint acceptance, D and live/adoption
remain separate North gates.

### C3 source checkpoint and fixture ownership map (unvalidated)

North released application source after Meridian's exact `44c697e6` fixture
repair PASS. This supersedes the source-held statement above. The repair stays
a distinct commit; no additional pre-source run occurred. This checkpoint is
implementation and static inspection only. Meridian's exact-source review and
the sole next hosted Operations suite, including compile/import, remain required.

The command owners now take the lifecycle lock before their existing execution
row prefix on fresh branches, recheck exact original plan/projection coverage,
current graph pins, receiver membership/origins and existing approval/authority,
and use private physical writes inside the same UoW. Original retry and inverse
work are checked against their own original material, without demanding C2's
global nonconflicting classification. Replay and evidence-only closure retain
their existing meanings. Public affecting claim, lease rotation, run creation,
enabling run CAS and intent/attempt creation refuse bare calls, including calls
holding a lifecycle guard. Empty-scope positives remain supported.

Advancement tentatively writes its actual pointer CAS, event and action, then
uses unchanged full-history C1 accounting before B acceptance/retirement
witnesses. Final same-UoW reads recheck original materials, current pointers,
complete old/new membership, origins and actual own-run intent material; any
refusal or deferred commit failure rolls back the entire change. No run is
excluded from C1. Teardown requires the selected removal operation; stopping
alone does not invent disposal.

Kepler and Meridian's seam review identified three gaps in the first draft:
exact original-side runtime authority, supplied products when expected receiver
membership is empty, and repeating actual intent-material validation after late
writes. These are corrected, with new inverse and advancement regressions.
Lawful zero-product legacy inputs remain supported; this does not make their
node/runtime operation nonaffecting. No inverse is rebuilt from today's graph.
The receiver inverse test introduces desired truth through C2 before real
request/compensation admission; its forward effects remain explicitly assumed
history, not managed runtime/health execution evidence.

Fixture translations are classified by ownership, not by public method names:

| Owning laws | Changed files under `control-plane-kit-operations/tests/` | Translation and retained evidence |
| --- | --- | --- |
| Retained premises for read/history/lifecycle/recovery tests | `activity_run_retry_interpreter_fixture.py`, `execution_lease_recovery_fixture.py`, `failed_run_compensation_fixture.py`, `lifecycle_lock_fixture.py`, `receiver_recorded_acceptance_fixture.py`, `revision_history_fixture.py`, `test_current_graph_advancement.py`, `test_revision_history.py`, `test_postgres_execution_lease_recovery_first_replay.py`, `test_postgres_execution_lease_recovery_scoped_run.py`, maximum-ID setup in `test_run_lifecycle.py` | Physical `_add_run` retains the same supplied records and assertions. No setup earns fresh admission/dispatch credit. |
| Physical intent/attempt codec, schema, FK, acknowledgement and outcome association | `postgres_effect_attempt_intent_store_fixture.py`, `postgres_effect_attempt_store_fixture.py`, `postgres_effect_outcome_store_fixture.py`; their `test_postgres_effect_attempt{,_intent}_{store,schema}.py` and `test_postgres_effect_outcome_{store,schema}.py` consumers | Explicit `_insert`/`_insert_absent` preserve original physical laws, including malformed rows, duplicate/rollback, raw FK failures and exact round trips. Public authorization is separately exercised by the C3 direct-writer targets. |
| Atomic service write failure/replay/ordering | `test_postgres_effect_attempt_start_{intent,first_replay,eligibility_rollback}.py`, `test_postgres_activity_run_retry_eligibility_rollback.py`, `test_postgres_execution_lease_recovery_eligibility_errors.py` | Existing failure injection follows the owner's private physical write. Sentinels, rollback snapshots, no-write replay and acknowledgement assertions remain. |
| Identity/factory ordering unit double | `test_authoritative_run_identity.py` | Fixed empty original material supports the newly required reads and lock; the full trace adds lifecycle serialization and final request reread. Every invalid-ID and no-mutation assertion remains. This double gives no receiver permission or database evidence. |
| Public contract supersession | `test_run_lifecycle.py` same-worker direct claim; `test_postgres_execution_lease_recovery_store.py` duration SQL shape | Same-worker physical claim still returns no replay and leaves truth unchanged; an added public affecting-refusal assertion expresses C3. Valid duration SQL encoding uses private rotation; malformed argument tests remain public. A real empty-scope public rotation positive is added to `test_receiver_direct_execution_permissions.py`. |
| Strengthened C3 negatives | `test_receiver_fresh_recovery_permissions.py`, `test_receiver_acceptance_advancement.py` | Actual inverse owner refuses inherited wrong-side authority and receiver product. Actual advancement rolls back witness-time membership deletion and a coherently re-encoded intent/attempt/outcome material change. The latter first proves complete C1 associations in a rollback-only premise transaction. |

All existing assertions/negative cases are retained except the explicitly
strengthened trace and public-contract additions described above. No skip,
xfail, assertion removal, schema/harness change or fixture-level lifecycle is
introduced. Compensation setup accepts original graph/operation/material inputs
before coverage/program capture, plus a callback composing real C2 introduction;
legacy defaults remain unchanged.

Security/data/history: mutation still requires existing command authority,
approval and fences. Errors remain bounded; no secrets or provider response
bodies are added to records. There is one caller-owned UoW for each durable
change and no transaction spans provider I/O. No new permission token, schema,
recovery/disposal policy, public route, health adopter or external effect exists.
First acceptance and retirement retain real action/session witnesses. B/C joint
acceptance, D, live/adoption and O2's successor-health end-to-end chain remain
separate, unproved gates.

Static source association review also found that
`postgres_effect_attempt_start_fixture.py` changed its inherited node intent's
operation to Start/StopRuntime but retained an unrelated node product and runtime
authority. Its normal runtime input now carries the original runtime's absent
authority and empty product tuple. Three incompatible-replay cases in the start
intent/eligibility tests now use a distinct foreign authority instead of removing
that already-empty product tuple; every fingerprint/refusal/rollback assertion
is preserved. Physical codec fixtures retain their richer product payloads.

The first-start SQL-order spy and decoder-fault case follow the existing shared
run-prefix owner: `get_latest_run_for_request` locates the bounded latest key,
then request-scoped run locks precede attempt locks, with held-run reentry after
writes. The exact trace adds that final recheck and retains the one database
clock observation, identity and no-write/error assertions. Replay's existing
request → run → attempt → intent trace is unchanged.

### Exact-source review correction: retained first-start truth

Meridian initially passed `39f77877` statically, then superseded that disposition
with a P1 HOLD after Vale raised supplied write acknowledgements versus actual
retained rows. No checkpoint publication or gate had occurred. Final input and
material checks alone could miss an AFTER INSERT trigger deleting a just-written
forward attempt or inverse binding while the physical writer still returned its
supplied acknowledgement.

The narrow correction rereads the exact new event, intent and attempt after all
fresh writes; inverse start also compares the complete expected prior binding
prefix plus the new binding. These are nonlocking typed reads at already known
identities, followed by existing bounded refusal/rollback. Original source,
held-run and material checks remain. No classifier, new lock, replay, policy or
permission object is introduced. Two actual-owner trigger negatives require the
semantic conflict and complete rollback; the injected deletes do not depend on
SQL/FK failure. Existing coordinator tests retain commit-before-unlocked-I/O and
no-redispatch responsibility. The corrected successor requires exact delta
review before North may re-release publication and the sole hosted gate.

### First hosted source gate and bounded corrections

The a67e5fdf source gate failed: run36680349156/job109774203254, actual
merge32aead33 and tree90749517545bf7f9218b0d1f55f224ffa724d517, with the
required architecture checkout7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef.
Operations completed2086 tests in1172.692s with30 failures/664 errors.
Compilation and clean import were unreached. Core and Current Backend passed
separately. Full Operations log SHA256:
`b454ef70b8a2020c27857554a0a4db0a05ae258870a36123c246d031497cde3b`.
Terminal result: [PR1908 comment5906194892](https://github.com/OpenJ92/control-plane-kit/pull/1908#issuecomment-5906194892).

Independent classification reconciles all entries (subtests/cleanup entries are
not independent behavioral laws):

| Class | Errors | Failures |
| --- | ---: | ---: |
| Connection exhaustion / schema refusal / secondary cleanup |399+83+8|0|
| Parent rotation approval association |57|0|
| Health wrapper final readback |15|0|
| Raw health original plan changed after coverage |49|11|
| Shared fixture lost rich product/authority/secret material |48|17|
| Identical concurrent first-start / inverse winner |2|1|
| Physical store doubles / bounded import whitelist |2|1|
| Coordinator corruption before valid running premise |1|0|

Thus490 errors are cascade/secondary cleanup and174 are primary errors.
All183 receiver-module errors failed for connection capacity during setup;
none gives credit to its receiver test body. One managed-health error's wrapper
attribution is inferred from an asynchronous rethrow;14 show the final-readback
stack directly. Exact attribution of each leaked connection is not available.

The static PASS missed real composition regressions: parent/child approval
sessions, the winner/replay transition, and the health return wrapper. It also
missed fixture inheritance propagating empty material into rich codec/grant laws.
Those misses are not dismissed as successor O2 work or harmless fixtures.

North released these bounded source corrections before any new gate:

- Subject-discriminate ordinary plan approval and retained gateway child
  association. Extract a private shared lower owner for stable original review,
  approval action, publication and admitted child/checkpoint correlation. Keep
  fresh admission phase/version/canonical-plan policy at admission, and keep
  current child eligibility at execution. No late rotation lock or approval token.
- For an attempt appearing after a pre-lock absence locator, or exactly one next
  inverse binding appended to the unchanged located prefix, restart once outside
  the UoW before any ID/write. The next pass uses ordinary exact replay checks.
  Arbitrary conflicts, errors, disappeared rows and ambiguous writes never retry.
- Preserve the expected native attempt before health retention wraps the return
  value; compare the actual persisted event/intent/attempt and keep the typed
  health result intact.

Fixture corrections preserve owners and assertions:

- First-start retains congruent runtime-only material. Explicit recorded codec
  and fold/reconciliation fixtures retain rich product/authority/secret bytes,
  including all mutation, grant, fingerprint and no-observer negatives.
- Raw health fixtures construct original selected health graphs, projections,
  workspace pins, plan and approval before inserting their recorded request and
  production-derived coverage. No admitted footprint is rewritten. These are
  retained health-owner premises, including malformed bytes, not claims of real
  admission or full-chain deployment. Managed application tests keep real admission.
- Coordinator establishes the valid running prerequisite before the same deliberate
  corrupt-plan negative. Private physical store tests target physical insert seams;
  public refusal and SQL/exception assertions remain. Health rollback injections
  follow the physical methods actually called. The import whitelist gains only
  the exact receiver scope dependency.
- Connection close is registered immediately; originating test-owned rows are
  cleaned even if later setup fails, only after successful schema acceptance.
  Nested advancement cleanup tolerates partial setup. No schema-validation bypass,
  pool increase, reset policy or harness change is included.

Existing first-winner replay, managed-health wrapper, rollback, gateway admission,
retirement and restart tests govern. A gateway regression exercises real prepared
child pause/resume after rotation progress with a distinct parent session, then
rejects substituted original admission-receipt identities without new events/IDs.

Security/data/history: unchanged effects boundary, one UoW for fresh writes,
no credential/provider access, bounded errors and no external redispatch. All
corrected and previously unreached laws remain unvalidated pending exact corrected
head review and a separately released owning Docker gate. No merge, D, joint or
live acceptance follows from this classification.

Prepublication delta review caught two further integrations and corrected them:
actual first-start-intent tests inherit codec helpers, so that class explicitly
selects runtime-only material; and malformed legacy health selected slots must
retain their owner's `EffectAttemptStartDenied` contract. Health admission now
runs after the held session/workspace prefix and before generic receiver semantic
checks. It only reads/checks and acquires its existing correlation locks; IDs,
writes, lease observation and all final receiver rereads still follow. No slot
check or assertion is weakened. North released this bounded ordering correction.

### Corrected gate timeout and minimal visibility diagnostic

The corrected `b4acb56f1c372b0910d6cc8c139629e583741ca7` checkpoint passed
independent static review, then ran once in hosted run `36684793768`, Operations
job `109788053753`. The actual merge checkout was
`99b7fa604800d774de5b03322a2453a9f981621c`, with accepted C2 and b4 parents;
its tree `0c9a4604f28ef319144376f5768fc8cc28c7a36a` matches the reviewed tree.
The actual architecture checkout was
`7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`. Integrity reported 2,070 authored
methods, four mocks and zero approved skips.

GitHub explicitly annotated “The job has exceeded the maximum execution time
of 30m0s.” The job ran from 07:37:47Z to 08:08:04Z on 2026-09-30. No terminal
unittest summary, complete failure blocks, compileall or clean import appeared.
The full 75,446-byte log has SHA256
`2e3e3e5887263084bdca353aff20fff88b7b678e0b6ec1ace646019bc07fc3ef`.
Core separately passed 907 package tests, compilation and clean import;
Current Backend separately passed. Neither establishes Operations acceptance.

The last named diagnostic at 07:45:50.1567468Z says that the asynchronous body
of `ManagedReobservationAdmissionTests.test_unsupported_native_predecessor_cannot_authorize_another_read`
finished with `result=None`. Cancellation output followed at 08:08:01.7553109Z.
The roughly 22-minute gap does not identify a stall or its owner: default unittest
progress has partial lines, which may remain buffered. The captured prefix
contains 826 dots and no explicit F/E marker, but is incomplete output, not a test
count or per-law green evidence. No culprit is inferred from the last named test.
Meridian independently confirmed timeout and exact evidence identity; acceptance
remains HOLD despite the separate source static PASS.

North released one observability-only preparation: append standard `-v` to
`python -m unittest discover -s tests` in the existing Docker gate. Full stdlib
discovery, test order, assertions, environment, dependencies, cleanup and the
30-minute CI limit remain unchanged. Named test/status output with CI timestamps
can expose observed progress, failure identities and cumulative time, and narrow
where a stall may be occurring; verbose output alone cannot prove a deadlock.
There is no selector, custom runner, watchdog, stack/local dump, timeout increase
or semantic code change. The small diagnostic checkpoint requires independent
review and a separate publication/run release. No blind retry, merge, D/joint or
live acceptance is authorized by the cancelled run.

### Verbose timeout: residual failures and bounded completion budget

The sole verbose diagnostic at `acb6596d6c54cb46c9b026a6631f51a7a2c5dc5f`
ran as hosted run `36688671073`, Operations job `109800405095`. The actual
merge checkout was `329c4879a6f7f6fce17ea9bc512f0ed14f11cd7f`, with accepted
C2 and acb parents and reviewed tree `613b70fbd004d9cead573fbe31157e0ea7f9147f`.
The actual architecture checkout remained
`7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`; integrity reported 2,070 authored
methods, four mocks and zero approved skips. GitHub again explicitly annotated
the 30-minute job timeout: 2026-09-30 08:16:18Z–08:46:38Z. The full
581,309-byte log has SHA256
`fa6a53d593ff8db28f8ebb1663f5181e61ee3c4b412ae137afa0c5309d63de3a`.

Named completions continued through 08:45:49.3287762Z, approximately 44 seconds
before cancellation output. This establishes continuing reported progress close
to cutoff, not a deadlock or acceptable performance. The last completed method
does not identify the test active at cancellation. No terminal unittest summary,
complete failure traceback blocks, compileall or clean import were reached.
Core and Current Backend separately passed; Operations acceptance remains HOLD.

Raw output contains ten non-OK records across eight methods: six ERROR and four
FAIL. The initial local parser collapsed the two canonical-noop subtest errors
and incorrectly reported nine. The raw chronology is authoritative (all UTC):

| Owner and method | Non-OK record | Time |
| --- | --- | --- |
| PostgresActivityRunRetryFirstReplayTests.test_both_approval_subjects_are_admitted | FAIL, gateway-key-rotation | 08:28:09.5369935 |
| PostgresActivityRunRetryFirstReplayTests.test_retry_delegates_approval_and_journal_to_shared_support | ERROR | 08:28:25.7116564 |
| PostgresExecutionLeaseRecoveryConcurrencyTests.test_every_asymmetric_race_forces_both_winner_orders | ERROR, takeover-before-abandon | 08:34:36.5481547 |
| PostgresExecutionLeaseRecoveryFirstReplayTests.test_both_approval_subjects_are_rechecked_without_gateway_read_or_lock | FAIL, gateway-key-rotation | 08:35:04.8202418 |
| PostgresExecutionLeaseRecoveryFirstReplayTests.test_predecessor_delegates_approval_and_journal_to_shared_support | ERROR, gateway-key-rotation; then method-level FAIL | 08:35:10.7488881; 08:35:10.7504712 |
| PostgresHealthEffectStartFirstReplayTests.test_event_intent_attempt_two_uses_preparation_then_single_commit_request | FAIL | 08:38:09.6743632 |
| ReceiverAcceptanceAdvancementTests.test_canonical_noop_a_to_b_to_c_keeps_original_first_acceptance | ERROR, second; ERROR, third | 08:42:45.8538368; 08:42:46.8084486 |
| ReceiverAcceptanceAdvancementTests.test_failed_or_incomplete_teardown_never_changes_current_or_retirement | ERROR, succeeded | 08:43:04.6366748 |

Source inspection identifies seams for later traceback classification: recovery's
existing no-gateway-read law versus the new retained-association read; recorded
gateway prerequisites versus actual child association; health-order spies on
old public writers versus current private physical writers; and an invalid raw
succeeded/unsettled row before the advancement assertion. The canonical-noop
failure stage remains unknown, and its second failure may depend on the first.
These are source-context candidates, not observed exception attribution or
authorization to weaken any law, schema, approval check or assertion.

North released preparation of only the Operations workflow timeout increase
from 30 to 60 minutes plus this evidence note, for exact independent review
before publication and one separately released hosted run. This bounded budget
is intended to obtain a terminal suite result and tracebacks; it is neither
performance acceptance nor permission for automatic retries. Standard verbosity,
full discovery, commands, test order, assertions, architecture pin, Docker and
cleanup remain unchanged. Core and Current Backend limits are unchanged.
No application or test semantics, selectors, custom runners or local duplicate
gate are included. Security/data/history boundaries are unchanged; no provider,
credential, tunnel, DNS or token effects. Merge, D/joint/live and O2 holds remain.

### Completed gate and released correction (2026-09-30)

The sole bounded full gate at `f9f6cd81aeb1d7dba4f447f85e0e078b8a3e0e2c` (short
coordinate `f9f6cd81`; full coordinate recorded by the PR) reached a terminal
Operations failure, not another timeout. Run `36693017530`, job
`109814366348`, ran 08:57:53Z–09:42:49Z: **2,090 tests in 2,660.771 seconds,
six failures and seven errors, exit 1**. Compilation and clean import were not
reached. The actual merge checkout was
`5ec6cd7bb7ce522a0479a51ea26a3e8f9a33af1a`, tree
`ca9a4351474e9c97b9688c9e362a4ab1190ac3bc`, with accepted C2 and f9 parents.
Architecture remained `7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`; integrity
reported 2,070 authored methods, four mocks and zero approved skips.
The 665,619-byte full log SHA256 is
`b99d68b6ac5c996823d3fae685da87b92c7a03b6202e71973a0d21254d6ea1cd`.
Core separately passed 907 tests, integrity, compilation and clean import;
Current Backend separately passed. Neither supplies Operations acceptance.
[Terminal evidence](https://github.com/OpenJ92/control-plane-kit/pull/1908#issuecomment-5908642723)
contains the durable result.

The 13 non-OK records span ten methods. Complete tracebacks and independent
review classified them as follows:

| Group | Records | Observed cause and bounded correction |
| --- | --- | --- |
| Gateway recovery/retry | Three failures, three errors | Retained permission read mutable rotation state; fabricated child approval lacked original association. Keep the existing recovery owner, prove original receipts, and seed authentic public admission. |
| Health start ordering | One failure | Spies observed closed public writers instead of the private physical writes; retain the ordering assertions at those writes. |
| Canonical no-op B/C | Two errors | Admission correctly rejects empty plans. Replace the unreachable executable target with desired-only/refusal assertions; transfer actual accepted A→B→C below. |
| Incomplete teardown | One error | An illegal succeeded/unsettled SQL fixture violated the schema before assertions. Use legal failed and running incomplete states. |
| Ingress teardown history | One error | Current-resource lookup excludes removed resources. Select the exact removed resource from history, retaining dispatch and event checks. |
| Nonaffecting same-worker claim | Two failures | Empty coverage does not require a receiver scope. Preserve the public idempotent claim result and unchanged fence/count assertions. |

North released this bounded correction in
[the correction decision](https://github.com/OpenJ92/control-plane-kit/issues/1904#issuecomment-5912796814)
and [the bounded lookup addendum](https://github.com/OpenJ92/control-plane-kit/issues/1904#issuecomment-5912862859).
The new required managed-update parent [#1909](https://github.com/OpenJ92/control-plane-kit/issues/1909)
orders #1910 → #1911 → [#1912](https://github.com/OpenJ92/control-plane-kit/issues/1912).
#1912 owns actual accepted A→B→C, including the former
`test_canonical_noop_a_to_b_to_c_keeps_original_first_acceptance` second/third
subcases. That criterion is transferred, not removed or satisfied by a weaker
test. O1/O2 may proceed under their revised acceptance; #1879 cannot close until
this required extension succeeds. The extension itself remains Todo/Hold.

The replacement
`test_desired_noop_b_and_c_refuse_execution_and_preserve_accepted_a` is classified
**strengthened desired-only/refusal**, not isomorphic accepted A→B→C. It starts
with real accepted A, creates distinct desired B and C with the actual five
workspace pins, plans and approves each real NoOp, then snapshots after those
legitimate writes. Admission must refuse without allocating identities or
writing execution truth; current A, its original acceptance and its unretired
origin remain exact. Initial acceptance, full teardown/retirement, fresh/direct
gates, retry/replay, both schedules, C-N9/10/11 and rollback remain C3 obligations.

Decision log for the local correction:

- Chosen shape: the existing recovery approval/journal owner recognizes an
  ordinary same-session plan approval or an exact gateway parent/child
  association. Receiver eligibility is checked separately. Fresh recovery and
  retry reread approval through that same owner, then receiver eligibility,
  before their existing transaction commits.
- Original association: typed rotation subject and original review approval,
  exact REQUEST_APPROVAL action/fingerprint, child workspace/session/plan,
  retained base/desired projections and phase identity, original ADMIT action
  and approval pair, and the original publication lineage/revision/version.
  Fresh gateway admission retains its mutable rotation checks.
- Semantic decision: later optional deployment-checkpoint corruption no longer
  revokes original retained permission. Rotation workflow consistency owns
  those later checkpoints. Missing or malformed original receipts still refuse.
- Bounded read: the private history-store query selects exact child session,
  publication action kind and desired projection with SQL LIMIT 2. The caller
  requires exactly one typed matching candidate, then validates independent
  semantic fields. A good plus malformed matching duplicate is ambiguous,
  never silently filtered into a good singleton. Unrelated long history is
  allowed. The bound covers returned rows/decoding count, not payload bytes,
  PostgreSQL scan work or JSON processing; no new index/schema/cap is claimed.
- Fixture shape: actual parent rotation approval, child projection publication,
  planning, admission and ClaimAndOpen replace the prior approval overlay.
  Active renewal stops before Start; failed recovery uses the existing
  coordinator with a failing provider substitute. Only lease-clock premises
  are overlaid afterward; no RUNNING journal is rewritten into an empty run.
- Alternatives rejected: mutable rotation reads during recovery, optional
  checkpoint authority, an unbounded session-history scan, a second approval
  owner, fabricated approval overlays and executable empty plans.
- Tests: retain existing recovery/no-read/replay and schedule assertions;
  strengthen final shared-owner reread and rollback checks. New owning tests
  cover closed-parent original association, corrupt original receipts,
  missing/wrong-kind/wrong-phase/duplicate publication, unrelated long history,
  final reread rollback and no identities/writes on refusal. The checkpoint
  corruption positive preserves original-receipt refusal negatives.
- Validation: this correction has static inspection and `git diff --check`
  only. The completed failing gate is red evidence; no green result, new suite
  count, compilation or import result is claimed for these edits. Exact
  Meridian source review and Kepler association review precede North's next
  publication/run decision. The ordinary owning Docker suite remains the gate;
  no host Python, selector, substitute harness or duplicate run was used.

Security/data/history consequences: no new route, credential access, provider
call, schema, lock order, token or authority bypass. Tenant/session ownership
is checked before association trust. Recovery errors remain bounded; no
receipt payload is exposed. Commands retain their existing UoW, idempotency,
journal/replay owners and structured actions/events; late association failure
rolls the command's writes back. Replay remains observation-only after session
closure or lease expiry. Existing lock schedules remain required evidence.
Residual risks are the unvalidated correction, database/payload work outside
the row bound, and the explicit transfer of managed-update acceptance. No
merge, joint/live acceptance, tunnel, DNS, token or image effect is released by
this local correction.

Independent static review of `f059dd47e27da42b094a54d2755092391734bf76`
(tree `a1b441d43ed8e18ebc6de3602405d78382d508ed`) found one fixture
reachability blocker: both gateway abandonment/takeover race schedules used
`gateway-rotation`, while authentic setup accepts `gateway-key-rotation`.
The successor corrects that shared caller to the canonical discriminator;
both schedules and all winner/loser assertions remain. Meridian found no
additional blocker. Kepler passed the bounded association/proof review at
that checkpoint. The one-line fixture correction and this record require
delta review before North's publication/run decision; neither static review
supplies executable acceptance.

### Correction gate: two remaining test-maintenance failures

The sole run of reviewed `32683342193e44fd740fcc3dbef5f4b63d5beb20` completed
as [Operations job 109936528799](https://github.com/OpenJ92/control-plane-kit/actions/runs/36729931408/job/109936528799):
**2,095 tests in 2,726.110 seconds, one failure and one error, exit 1**.
It ran 14:32:54Z–15:18:55Z on 2026-09-30 (46m01s), without timeout;
compilation/import were unreached. Actual logged checkout
`1feb5402812cfc9ef7d8638ac4147b151cc1ff9d` has reviewed tree
`e8f908ee46fce7624dcf797a45813401d7f578dc` and accepted C2/32683342 parents.
Actual architecture remained `7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`.
Integrity reported 2,075 authored methods, four mocks, zero approved skips.
The 641,730-byte Operations log SHA256 is
`6d0300c358c5d84ea46fe61a9603d7f5815859844ded3f82a7ccbd6b958b98bb`.
Core passed 907 package tests, integrity, compilation/import; Backend passed
separately. [Complete terminal evidence](https://github.com/OpenJ92/control-plane-kit/pull/1908#issuecomment-5914309788)
retains the exact two tracebacks and holds acceptance.

Meridian independently confirmed both omissions missed by the prior static
review. Fresh closed-parent gateway renewal succeeded, but the new test then
rewrote its persisted claim times before replay. The existing replay owner
correctly rejected drift from the original decision event and lease duration.
The correction preserves those times, keeps both parent and child closed,
forbids lease observation and ID allocation during replay, and checks exact
replay plus unchanged durable snapshot. Retry-specific expiry remains a
separate subcase; this test makes no claim of actual elapsed renewal expiry.
The existing timestamp-drift refusal test and all production checks stay intact.

The other failure occurred at canonical read discovery (`72 != 71`), before
inventory-set equality. The private publication lookup adds one fixed-cardinality
read. Its exact `PostgresActivityHistoryStore._projection_publication_actions`
entry now names the production association consumer, exact SQL filters and
LIMIT 2, with row/decoding-count-only bounds. Both discovered/unique totals
become 72 and fixed-cardinality becomes nine; exact inventory-set equality,
repeated-occurrence identities and category assertions remain unchanged.

[North's narrow correction release](https://github.com/OpenJ92/control-plane-kit/issues/1904#issuecomment-5914317023)
covers only these tests, canonical inventory and this record. No production,
schema, transaction, authorization or history semantics change. Static checks
and exact successor delta review precede a separate publication/run decision.
There is no new executable green evidence, merge, downstream/live acceptance,
automatic retry or provider effect. Actual accepted A→B→C remains with #1912.

### C3 accepted; joint admission/selection schedule closure

The reviewed maintenance head `5b2e00867c1ad50f838d7546d967051ea2bc5fd7`
passed the sole [Operations run 36737073611 / job 109961467171](https://github.com/OpenJ92/control-plane-kit/actions/runs/36737073611/job/109961467171):
**2,095 tests in 2,578.823 seconds, OK**, compilation and clean import.
Actual CI checkout `975d1dea144d034855f14c1570d72a28234cef71` has tree
`90854a89ff69e631964bc87a9291e2c5ac75b3bf`, accepted C2/head parents and
architecture `7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`. Integrity was
2,075 authored methods/four mocks/zero approved skips. The full 583,248-byte
log SHA256 is `b0c4807d55ae77acdf81b906847c6783167d3732b5d98b9c486eb05e87c77a95`.
Core passed 907 tests, integrity/compilation/import; locked Backend passed.
[Complete evidence](https://github.com/OpenJ92/control-plane-kit/pull/1908#issuecomment-5915187154),
[Meridian verified PASS](https://github.com/OpenJ92/control-plane-kit/pull/1908#issuecomment-5915213433)
and [Kepler architecture handoff](https://github.com/OpenJ92/control-plane-kit/pull/1908#issuecomment-5915225050)
preceded North's C3 merge at `eaa4746334124fafc2ef794a29013b63b2fbeb8c`.
The merge has exactly the green tree and introduces no new source delta.
C3/#1904 is accepted under its revised child contract.

Joint matrix review of B/C found one additional prescribed evidence gap:
actual execution admission versus receiver selection/scope check in both
winner orders. Existing admission lock-prefix and sequential queued-conflict
tests do not compose both owners; native-start races begin after admission.
Meridian and Kepler independently held joint acceptance for this narrow gap,
without finding a production defect or reversing the C3 child result.

[North released the test-only pair](https://github.com/OpenJ92/control-plane-kit/issues/1898#issuecomment-5915387033)
on `codex/1898-admission-selection-races` from the accepted collection.
The two new methods in `test_receiver_fresh_execution_schedules.py` are
**strengthened** evidence for the existing #1898 law. They reuse the valid
node-only approved plan with no request, preconstruct the original execution
and five-pin graph commands, and reuse the existing real PostgreSQL opposing
owner barrier. Distinct command keys ensure the contested lock is L.

Admission first commits its exact queued request, derived scope header/rows
and actual action; waiting receiver selection must see this new conflict and
leave graph/pointer/origin truth unchanged. Selection first commits its real
graph/projection/action/introduction/bindings and desired generation; waiting
original-plan admission must refuse stale truth before allocating an execution
identity. Full snapshots preserve every prior row, allowing only explicitly
verified winner rows and the exact winning desired-pointer delta. Both retain
original plan/approval records and prove no run/attempt/intent creation. Existing
native-start schedules are unchanged; no pre-admitted fixture replaces the
RequestPlanExecution owner.

This local target checkpoint has static inspection and `git diff --check`
only. No synthetic causal-red is claimed for existing implemented behavior.
Exact test-integrity/architecture review precedes North's publication and
ordinary full owning-run decision. No production, schema, permissions,
transaction, provider or history semantics change; scheduling witnesses alter
neither owner SQL nor service results. No new fixture state machine or issue
topology is introduced.

Same-tree 2,095-test evidence remains valid for existing combined laws but does
not cover the absent pair. PR1901 automatic checks are observed separately.
Locked Backend uses CPK `f45384e72a79f59c93a715fd08f409f86a91218a`; it is baseline
composition, not receiver adoption. Joint B/C acceptance, D, #1912 actual accepted
A→B→C, O2 health, downstream/live and foundation closeout remain distinct holds.

Exact target review at `eb62f544d92d6549695c74c4ff031e2679c09bec` found
one constructor-reachability defect: both admission service constructors omitted
the required clock. The successor supplies the existing admission fixture's
`2026-07-22T12:04:00Z` clock at both calls, preserving their worker UoW factories,
ID allocators and scheduling/state assertions. Meridian identified no other
blocker; Kepler passed the two-order semantic shape. Exact delta review remains
required before publication or executable validation.

## Final joint B+C milestone and D handoff

[North accepted PR #1913 and the final joint review](https://github.com/OpenJ92/control-plane-kit/pull/1913#issuecomment-5916325774).
The actual collection merge is `b4a139aea1abb6a7466d1dea440d3d7be207f1d5`,
tree `8b7c16a64e3f247d8acb3ff6f15cca15d913a279`, with parents accepted
collection `eaa4746334124fafc2ef794a29013b63b2fbeb8c` and reviewed correction
`dfd9d70efc73876eb4ad5fa6ab3a36253edb80d7`. Fetch and tree comparison confirm
no source delta from the tested correction. Meridian's final independent
transaction/security/test-integrity review and Kepler's final architecture
review both PASS; their sole joint evidence HOLD is closed. Aggregate
[PR #1901](https://github.com/OpenJ92/control-plane-kit/pull/1901) promotion and
required checks remain pending. This record does not close #1897 or #1898.

### Capability, objects and executable laws

The combined Operations capability now connects graph-owned receiver provenance
and bindings to graph admission, immutable original execution coverage, fresh
execution permission and atomic acceptance/retirement. Existing command owners
perform these transformations in their UoWs; graph values and retained receipts
do not themselves authorize effects. B supplies introduction/binding and
acceptance/retirement records; C1 supplies request scope headers/rows and bounded
classification; C2 composes graph/draft/publication admission; C3 composes fresh
execution and current advancement with those owners.

| Transformation | Executable law in the combined owning suite |
| --- | --- |
| Desired graph, draft or publication → graph/projection/action/bindings | Original five pins and provenance are checked; complete owner writes are atomic and unsupported direct receiver writes refuse. |
| Original approved plan → queued request/action/coverage | Coverage remains tied to original material. Real execution admission and receiver selection serialize in both winner orders; the loser leaves no durable writes. |
| Retained execution truth → scope classification | C-N9/C-N10 stay conflicting. C-N11 permits reuse only with complete no-dispatch cancellation evidence and all future activation paths closed. Exhausted-budget, incomplete or unavailable evidence never becomes clearance. |
| Original receipt → replay or lawful retry | Replay preserves original meaning without fresh dispatch. Lawful original retry retains its occupied scope and does not clear a competitor. |
| Fresh native start → committed event/intent/attempt → adapter I/O | Exact STARTED truth commits before unlocked I/O; ExistingAttempt/replay never redispatches. Late precommit or commit failure prevents the external call. |
| Complete associated execution → current pointer/action/event/witnesses | First acceptance and accepted removal retirement commit together or roll back together. Desired omission alone cannot retire a receiver. |

Joint review found no production defect. Its narrow correction added the
prescribed public `RequestPlanExecution` versus `SetDesiredGraph` pair using
real PostgreSQL blocking and exact winner/loser snapshots. Prefix-only and
post-admission native-start schedules were insufficient for that law. Review
also caught both omitted required clock arguments before publication; the
tested successor supplies them without changing production or fixture helpers.
The two original native-start schedules remain unchanged and green.

### Exact final validation and review disposition

[Full terminal evidence](https://github.com/OpenJ92/control-plane-kit/pull/1913#issuecomment-5916269786)
records ordinary `./control-plane-kit-operations/test.sh`, run `36745663635`,
job `109991092600`: **2,097 tests in 2,603.905 seconds, OK**, compilation and
clean import, terminal success. All 2,097 named completion records are `ok`,
including both added winner orders. Integrity reports 2,077 authored methods,
four mocks and zero approved skips. The actual logged CI checkout
`c098406be26c2dd5f9be6eacb7803bc86d3016c0` has the same reviewed tree and
parents as the accepted collection merge; architecture-testing is
`7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`. The 583,618-byte raw log SHA256
is `fadd3fa3b898bf52313f08035e9276fa14559b495d29e69e09ed74a76d3ffbbe`.
Both reviewers independently verified the raw evidence and exact source
association. Core passed 907 package and 21 integrity tests plus compilation
and import. The [earlier automatic collection gate](https://github.com/OpenJ92/control-plane-kit/pull/1901#issuecomment-5916009474)
also passed 2,095 tests; it is explicitly not evidence for the two added tests.

Current Backend passed with runner `c098406b` but locked CPK source
`f45384e72a79f59c93a715fd08f409f86a91218a` and the retained external package
pins. It proves that baseline composition, not downstream receiver adoption.
This final learning update changes documentation only and requires static
diff validation and documentation review; it adds no executable claim or run.

### Remaining limits, risks and next boundary

The approved scope deviation transfers actual accepted managed A→B→C proof to
[#1912](https://github.com/OpenJ92/control-plane-kit/issues/1912); desired-only
NoOp refusal is not equivalent. Initial acceptance and full-removal consumer
tests retain their explicit valid upstream-completion premise.
[O2/#1883](https://github.com/OpenJ92/control-plane-kit/issues/1883) still owns
successor-health completion and adoption. Those capabilities, downstream server
adoption, live/provider behavior and #1879 foundation acceptance are unearned.

Security and data risks remain bounded as reviewed: tenant/approval/fence and
original-material checks remain authoritative; stable IDs and receipts grant
no adoption, cleanup or redispatch permission. Exact-schema mismatch refuses
rather than migrates. Lifetime same-scope capacity and unresolved history can
prevent progress; returned-row limits do not bound all database scan/JSON work.
Supported-writer invariants do not claim detection of hostile privileged SQL.
A crash after STARTED commit remains conservative uncertainty, with no inferred
provider rollback, compensation or cleanup. Actions, events and original
receipts retain the operational explanation; process logs are supplementary.

[D/#1899](https://github.com/OpenJ92/control-plane-kit/issues/1899) can rely on
this pending/current/retired distinction after North's aggregate release:
introduction reserves identity; real current advancement records acceptance;
desired omission or draft tombstone preserves reservation; accepted full
removal retires identity. Authoring must preserve these facts and original
receipt meaning rather than infer disposal or permission from a graph edit.
No joint architecture or executable-evidence blocker remains for that handoff,
but D is not released by this document. North still owns required-check and
aggregate merge verification, the coordinated B/C closeout and the decision
that the next milestone is safe to begin.
