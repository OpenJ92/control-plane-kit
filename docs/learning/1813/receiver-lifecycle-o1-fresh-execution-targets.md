# O1.C3 fresh execution and acceptance targets

Status: planning/target-only work released by North; no application source,
causal-red execution, joint acceptance or live/adoption release. Selected base
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
| Acceptance/retirement | Real C2 introduction → canonical initial/teardown plan with explicitly retained complete-success evidence → real advancement records first acceptance/retirement. Actual same-effective-graph A→B→C no-ops preserve that witness. Desired omission, tombstone, failure and stale/incomplete completion do not retire. Losing CAS/late/deferred failure rolls back all records. Native dispatch closure is exercised separately. |

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

Completed-target independent review must assess remaining receiver-law coverage
before checkpoint/run release; file presence and static inspection earn no green
evidence. Predecessor fixtures using public writers may need explicit retained
premise translation only after source closure, with original assertions intact.

Targets must be independently reviewed before North releases the ordinary
causal-red gate. Owning command remains `./control-plane-kit-operations/test.sh`
with clean architecture sibling `7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`.
No host Python, targeted selector, alternate runner, harness change or test
execution has occurred for C3. A later source release requires genuine missing
behavior evidence, not collection/fixture failures. Preserve all predecessor
assertions; no skip/xfail or broad fixture repair hidden in targets.

Security/data/history: existing auth, approval, fences and bounded errors stay
authoritative. Durable intent, STARTED, acceptance and retirement have explicit
owning UoWs; no transaction spans external I/O. No schema change, migration,
backfill, credential operation, provider effects or new public data exposure.
Ambiguous effects remain conflicting. B/C joint acceptance, D and live/adoption
remain separate North gates.
