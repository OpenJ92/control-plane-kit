# #2033 test context and source dry run

Base: active PR1829 / roadmap/1813-runtime-control at 879efb9576ac0580a6236d0a7372c036537b4dfe. Feature: codex/2033-recovery-evidence. This is source planning, not live recovery authorization.

## Governing law cards (inspected current tests)

- `test_effect_outcome_evidence_contract.py::test_outcome_descriptors_commit_only_to_bounded_coordinates`: retained evidence exports exact identity, request/outcome fingerprints and closed profile/transition/count; no provider details, addresses or canary values. Classification: strengthened for the new aggregate proposal and repr/error boundary.
- `test_effect_outcome_record_contract.py::test_snapshot_binds_identity_start_effect_request_and_direct_state` and `test_projection_rejects_workspace_and_coordinate_hostility`: original owner record must correlate identity, start event, request, workspace and outcome. Classification: isomorphic reuse of owner validation, plus new-law proposal query expectation checks.
- `test_postgres_effect_attempt_reconciliation_first_replay.py::test_terminal_profiles_replay_without_guard_time_authority_or_fold` and `test_terminal_missing_corrupt_foreign_and_drifted_outcomes_conflict`: retained outcomes are exact history; reads neither reobserve nor refold them, corrupt/missing/foreign joins fail. Classification: isomorphic preservation, new proposal-specific read entry.
- `test_read_services.py::test_operator_overview_rejects_a_plan_from_a_stale_base`: divergent current/base truth cannot appear actionable. Classification: strengthened by explicit stale-plan blocker in the proposal, never granting continuation authority.
- `test_postgres_configuration_preparation.py::test_another_approved_activity_cannot_remint_an_unresolved_slot` and `test_exact_replay_keeps_original_refs_claims_and_attempt_without_ids`: unresolved protective claims remain; read/replay grants no new birth. Classification: isomorphic preservation; new-law read snapshot must perform no reservation/write/ID allocation.
- Existing read snapshot tests for ReceiverAuthoringContext and configuration cleanup establish a one-use REPEATABLE READ READ ONLY unit of work, rollback/close, bounded owner reads and closed error sanitization. Reuse this shape, not old product fixtures or bespoke SQL dumps.

This is not frozen-package migration; current accepted tests are the governing context. No immutable reference reproduction is required or claimed. Tests have not yet been run for this issue.

## Last working boundary and missing computation

Durable effect intent/outcome stores already retain canonical validated records. Reconciliation maps non-STARTED attempts to retained folds, so it cannot serve as a fresh recovery evidence command. Generic provider observation is unsupported for this configuration/secret-bearing product; invoking it or a secret resolver would expand authority and effect scope. Operations has no dedicated bounded proposal read joining original attempt evidence to current plan/run/claim pins.

Proposed computation:

    authorized exact query
      -> one read-only durable snapshot
      -> existing owner-validated intent/attempt/outcome + current pins
      -> bounded immutable diagnostic proposal with explicit blockers

There are zero fresh provider reads in this slice. `provider_observation = not-collected` and `historical_outcome = uncertain` are explicit. A retained provider-observation profile remains historical evidence and never becomes a fresh observation. No raw caller observation payload is accepted. This is a useful inspection contract, not an assertion that recovery admission is ready. Trustworthy current owner inventory/overlap evidence and execution-owner predecessor quiescence remain an explicit unresolved design prerequisite to #2034 admission; a green durable snapshot does not fulfill that prerequisite. Kepler and Meridian will determine the smallest bounded owner child before claiming the full continuation path complete.

## Proposed public interface

Operations `read_services.activity_recovery`:

- `ActivityRecoveryQuery(workspace_id, identity: EffectAttemptIdentity, expected_request_fingerprint, expected_outcome_fingerprint)`, strictly validated with bounded coordinate/fingerprint grammar and strict mapping decoder.
- `ActivityRecoveryReadService(snapshot_factory).read(query, context=TrustedCommandContext)`; operator principal, matching workspace and INSTANCE_WORKSPACE_READ required before opening a snapshot.
- `ActivityRecoveryProposal.descriptor()` returns fixed profile `activity-recovery-proposal.v1`, `status: blocked`, exact source identity and session/request/plan pins, original intent/outcome fingerprint references and event/time coordinates, selected configuration fingerprint/count (never contents), current graph/revision and claim/fence/time snapshot, and closed blockers.
- Blockers include `provider-observation-not-collected`, `predecessor-quiescence-not-established`, `continuation-authority-not-established`; these mean not established by this snapshot, not proof of an active dispatch or absence of external permission. Add `run-not-current`, `run-not-running`, `plan-stale`, or `claim-unavailable-or-expired` when observed. There is no ready/allowed/retry-granted result.
- Closed errors: malformed, forbidden, missing, stale-evidence, unavailable, capacity. Messages omit candidate data and causal exception chains. Logical result JSON is bounded to 16 KiB; result representation hides underlying payload. Proposal hash commits only to the bounded descriptor, not a reusable execution authorization.

Postgres adapter: a dedicated narrow snapshot facade exposed by `PostgresUnitOfWork.activity_recovery_snapshot()` using existing `_read_snapshot()` and `_composed_read()` accounting. Reuse exact owner getters and validation for request/run/plan/session/workspace/attempt/intent/outcome. Verify configuration protection with the preparation owner for selected configuration activity. Query a bounded database timestamp/current claim snapshot without FOR UPDATE, lease renewal or commit. Do not call `observe_request_lease_for_update`, `ReconcileEffectAttempt`, secret services, or provider helpers. The current claim is a recorded fact only; admission later rereads/fences it.

The complete aggregate refuses if owner congruence, bounds or required original evidence fails. Missing current provider evidence remains a blocker, not a fabricated result. Per-attempt provider/runtime identity references are retained only through approved identifiers/fingerprints, never arbitrary metadata or secret refs.

## Intended changes and ownership

Add the Operations public projection module and Postgres narrow read facade; add one named read-snapshot method to its existing UoW; update package inventory/public module exports and concise example/docs. No schema migration, cpk-server route replacement, saga change, effect preparation, reservation or dispatch. #2034 and owning Core/Operations integration under #339 add those later.

## New focused target tests

1. Strict query, authority-before-factory, aggregate output bounds and closed errors/cause-free failures.
2. Real PostgreSQL seeded owner history returns exact original references and explicit unknown/blockers; no provider capability is supplied.
3. Mismatched workspace, expected fingerprints, corrupt/missing/foreign owner records fail; stale current plan/claim/run cannot appear actionable.
4. Unknown result evidence containing secret/config/provider canaries is absent from descriptor, repr and errors.
5. Read-only transaction and no commit/ID/effect/refold/reservation; before/after original history, intent/outcome and protection rows identical. Current observation time comes from database, original recorded time remains separate.
6. Unsupported current observation remains not-collected, even if prior evidence says action-not-started or observed-absent. Neither implies historical no-effects or retry permission.

Write tests against this interface after review. Focused red must fail for the missing capability (using intentional availability assertions before lookup if needed), not broken collection/import/setup. Run only the owning Operations script with focused `-k` filters; exact architecture-testing prerequisite must be clean. Then implement, rerun focused green and required broader gates; no custom host harness.

Security/data/history: bounded authorized read only; no secrets/config bodies/provider messages; no new network exposure; no provider I/O in a database transaction; no writes or cleanup; immutable uncertain history preserved. Remaining uncertainty is explicitly visible and blocks continuation. The full #225 recovery still requires reviewed admission, public integration and a separate live release.
