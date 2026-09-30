# O1.C receiver admission: test-conditioned design

Status: joint B+C planning in progress; no application targets, source, schema
execution or effects released. Governing child:
[#1898](https://github.com/OpenJ92/control-plane-kit/issues/1898).
Selected source is accepted A `2a1bf73dc1ba97806bf7bd26991d1450dc8af9d8`;
the documentation-only integration checkpoint is `3329fde1` in
[PR #1901](https://github.com/OpenJ92/control-plane-kit/pull/1901).
Read with the [B storage plan](receiver-lifecycle-o1-storage-plan.md),
[parent law cards](receiver-lifecycle-o1-plan.md),
[A lock ledger](receiver-lifecycle-o1-lock-ledger.md) and
[F0 section 4](../../design/0007-logical-receiver-lifecycle.md#4-bounded-graph-owned-lifecycle-evidence).
This document distinguishes a selected semantic rule from the unfinished exact
predicate, query/index design and complete entrypoint matrix.

## Selected decision: completed effects without lifecycle accounting

[North's disposition](https://github.com/OpenJ92/control-plane-kit/issues/1898#issuecomment-5885523490)
selects this bounded C **new law**: a known-complete, scope-affecting creation or
removal without exact accepted/disposed accounting retains conflict against
conflicting reintroduction or scope reuse. Describe this as **unaccepted or
undisposed**, not uncertain. The rule does not classify provider reality from a
graph pointer or turn every missing advancement receipt into a conflict.
Read-only effects and unrelated scopes do not become conflicting merely because
their runs lack advancement receipts.

Successful provider execution and accepted lifecycle history are distinct facts.
A successful run can become superseded before first current-graph acceptance.
Changing a request to cancelled/abandoned, returning its old receipt, observing
provider health or manually removing a resource does not supply the missing
Operations acceptance/disposition evidence. No recovery, cleanup, reconciliation,
adoption or stale-plan acceptance API is added by this decision.

Operator consequence: for the specific superseded-success case below, no
supported clearance was demonstrated in the inspected advancement, failed-run
compensation and failed-run retry paths. The conflicting affected-scope
reintroduction/reuse therefore refuses. Inspection/history and unrelated work
remain possible. This is an explicit liveness limitation, not a claim that the
existing recovery workflow is complete or an exhaustive ecosystem recovery audit.
North selected this limitation within C's existing scope; it is not another user
approval gate or permission to implement a new recovery product.

## Inspected law cards and source evidence for that decision

Paths in this section are relative to `control-plane-kit-operations/` at
`2a1bf73`. These tests were inspected; no new target or test execution is claimed.

| Card | Governing inspected test | Observable law / negative cases | Classification and owner |
| --- | --- | --- | --- |
| H1 | `tests/test_revision_history_advancement.py::test_success_without_receipt_is_none_recorded_then_real_acceptance_survives_pointer_change`; `::test_accepted_history_survives_current_claim_rotation_and_removal` | Success alone has no accepted receipt; an actual retained receipt survives later pointer/claim changes. | Isomorphic history law. It does **not** prove the new scope-conflict rule. |
| H1-negative | Same file `::test_event_without_action_is_unavailable`, `::test_action_without_event_is_unavailable`, `::test_duplicate_candidates_on_either_side_are_not_arbitrarily_selected` | Missing or ambiguous event/action pairs cannot be selected as acceptance evidence. | Isomorphic receipt attribution. No status-only shortcut. |
| A4 | `tests/test_current_graph_advancement.py::test_incomplete_uncertain_or_failed_evidence_cannot_advance`; `::test_stale_realized_lineage_and_revision_fail_closed` | A successful run label cannot override incomplete step evidence or stale graph/projection/generation association. | Isomorphic; strengthen with no lifecycle witness on refusal. |
| R1 | `tests/test_postgres_atomic_effect_attempt_fold.py::test_recovery_first_fold_and_replay_never_touch_direct_outcome_stores` | Recovered success/failure and abandonment retain recovery evidence with no direct outcome row; exact replay creates no new outcome/time/ID. | Isomorphic. An inner join to direct outcomes cannot define complete candidate history. |
| C-N9 | North's selected decision linked above, extending F0 N7 | Same-scope known-complete affecting work without accepted/disposed accounting conflicts despite supersession or cancellation; nonaffecting/unrelated work does not. | New law, not an assertion inherited from H1. C execution/lifecycle owner. |

The current trace supporting the narrow clearance finding is:

* `src/control_plane_kit_operations/advancement.py:543` `_require_identity`
  requires the original plan's authored/projection associations and desired
  generation, then matching live workspace coordinates (`:566`, `:571–581`).
  Supersession prevents first acceptance; selecting old material again does not
  restore the old generation. `_require_complete_success` (`:657`) is separately
  necessary and does not replace those checks. Original replay (`:734`) recovers
  an existing action/receipt; it does not invent missing first acceptance.
* `src/control_plane_kit_operations/failed_run_compensation.py:246` requires
  `FAILED`; `:250–255` requires original workspace current/desired/generation.
  Its later successful-effect and unresolved-attempt checks do not widen that
  entry condition to a superseded `SUCCEEDED` run.
* `src/control_plane_kit_operations/activity_run_retry_interpreter.py:276`
  `_require_first_state` checks current claim/fence and latest prior-run
  linkage; `:295` requires `FAILED`. It does not reopen this successful run.
* `src/control_plane_kit_operations/revision_history.py:58`
  `historical_advancement` attributes one exact retained event/action pair,
  validates association through `CurrentGraphAdvancementResult`, and returns
  `none-recorded`, `unavailable` or `accepted`. It is read attribution, not
  disposition authority. Reuse its preserved receipt law without treating a
  receiver summary or run status as a replacement receipt.

## Predicate obligations before exact query freeze

The execution owner must distinguish these facts at stable
`(workspace_id, runtime_id, node_id, provider_socket_name)` scope. Candidate
selection cannot use the proposed receiver ID or current graph alone.

| Evidence category | Required interpretation |
| --- | --- |
| Queued/active work capable of a conflicting create/remove | Retain conflict while capable of affecting that scope. Inspect planned operation/scope; a read or unrelated operation is not enough. |
| In-flight or uncertain affecting attempt, including an abandoned request/run | Retain conflict unless exact authoritative evidence settles the attempt. Request/run terminal labels do not do that. |
| Known-complete affecting work without exact accepted/disposed accounting | Apply C-N9. Do not mislabel success as uncertainty. |
| Exact accepted or authoritatively disposed effect | Account only for the evidence's proven coverage. Never clear unrelated older attempts just because a later run has a receipt. Exact supported disposition proof remains to be mapped. |
| Nonaffecting reads/observations or unrelated scopes | Do not manufacture a lifecycle conflict from absence of acceptance. Preserve any independent existing execution/authorization restrictions. |
| Missing, malformed, inconsistent or over-limit necessary evidence | Bounded refusal; never return a partial candidate list as clearance. |

Advancement can omit **its own** run only after the existing full completion and
association proof, while producing pointer/event/action and lifecycle witnesses
atomically in the same UoW. This is an internally derived exception, not a
caller-supplied ignore list. It does not exclude older runs, prior attempts or
other requests outside that proof. Query design must cover them explicitly.

The query must preserve recovered evidence that legitimately lacks a direct
outcome row. `STARTED`, `UNCERTAIN`, `ABANDONED`, `SUCCEEDED`, request status and
run status are inputs to interpretation, not a complete settlement predicate.
All relevant retained plans/runs/attempts, including superseded and abandoned
bindings, remain candidates; a latest-run-only lookup is insufficient.

## Design still required before targets/source

1. Exhaustive supported graph/projection persistence, pointer selection,
   draft/preparation/publication, execution/resume and advancement entrypoint
   matrix. Each successor-capable path must enforce the complete law or refuse
   for a concrete missing contract; helper visibility is not authority.
2. Exact operation-to-scope classification, including original legacy-profile
   graphs and requests without receiver binding rows. A receiver-index-only
   scan cannot be presumed complete. No fabricated IDs, history rewrite or
   backfill is permitted to make a query appear complete.
3. Exact evidence predicate for each status and supported authoritative
   disposition, with negative cases and original-history compatibility.
4. Indexed bounded candidate and evidence retrieval. Freeze row/byte limits,
   overflow semantics, all-history coverage, supporting indexes and SQL work
   bounds together; a top-level `LIMIT` after an unbounded scan/sort is not a
   sufficient design. Existing history APIs that load whole run/event histories
   cannot silently become the bounded scope lookup.
5. Final graph/action/provenance and lock suffix integration with B, exact
   activation contract and target cases for both contention schedules and late
   rollback. Preserve A's reviewed lock order and caller-owned transaction.

Security/data/history note: this checkpoint changes documentation only. It
grants no new authentication, effect, schema, provider or credential authority.
The proposed refusal is bounded and tenant-safe; original history and sensitive
material remain retained under their existing readers/redaction rules. No
reset, migration, deletion, implicit adoption or external effect is authorized.
The next permitted work is completing and independently reviewing this joint
design; it is not writing executable target tests or application source.
