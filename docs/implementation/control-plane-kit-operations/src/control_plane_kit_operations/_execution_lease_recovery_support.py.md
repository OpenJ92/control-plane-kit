Source: [control-plane-kit-operations/src/control_plane_kit_operations/_execution_lease_recovery_support.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/_execution_lease_recovery_support.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This private module shares three recovery checks: retained approval linkage,
journal eligibility and replay evolution through linked retry runs. Approval and
evolution helpers read stores; journal eligibility interprets supplied records.
The module has empty __all__ and owns no UoW, commit, new approval, lease mutation,
retry creation, event append or external execution. Its two actual callers are the
[lease-recovery interpreter](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_lease_recovery_interpreter.py)
and [retry interpreter](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/activity_run_retry_interpreter.py).
They own command scopes, locked request/run state, clock observations and durable
recovery results.

locked_recovery_approval loads the request's retained approval request, its decision
and the execution request's plan. It requires approval/request/session identities,
the retained decision ID, an APPROVED decision whose scope is the approval's
required scope, and plan identity/session agreement. An ActivityPlanApprovalSubject
must name that plan. GatewayKeyRotationApprovalSubject is also supported without
reading mutable gateway rotation state; other subject types conflict. It does not
mint permission or recompute a new plan/risk approval. Typed approval reconstruction
and the caller's command authority are separate boundaries.

The word locked describes caller context, not three FOR UPDATE reads implemented
here. The selected actual
[history store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/activity_history.py)
uses ordinary SELECTs for approval, decision and plan. Fresh callers already hold
their session/request/run coordination; this helper neither acquires those locks
nor independently locks mutable gateway state. Missing lookup/decision becomes
RunLifecycleNotFound, malformed selected history becomes RunLifecycleConflict,
and identity/decision/scope/subject drift has fixed conflict messages. Decoder
validation remains meaningful: malformed durable approval material can be rejected
while these records are loaded, before the helper's cross-record comparisons.

require_recovery_eligible_journal requires exact RecoveryDecisionKind and
ExecutionLeaseFence types, nonempty events, every event on the supplied run and
ordinals exactly 1..len(events) in tuple order. It then validates/removes prior
recovery decision/consequence pairs, projects the remaining mapped events through
the actual [journal mapper](activity_journal.py.md) and Core saga projector, and
classifies unmapped lifecycle events. It does not itself validate caller scopes,
request claim state/expiry or run/plan ownership; those are caller/record laws.
The helper assumes typed event/run/plan records rather than wrapping arbitrary
attribute failures in a universal validation boundary.

_CONSEQUENCE_KIND maps active/expired renewal to REQUEST_CLAIM_RENEWED, takeover
to REQUEST_CLAIM_TAKEN_OVER, and abandonment to REQUEST_CLAIM_ABANDONED. A recovery
decision in retained history must have its matching consequence immediately after
it, with adjacent ordinal, identical occurred_at, empty consequence evidence and
no failure. An orphan consequence is invalid. Renew-active pairs may occur only
after opening and before start/failure; other mapped pairs require opening, start
and failure already seen. RETRY_AS_NEW_RUN markers are categorically ineligible
for this pair-stripping path, whether terminal or followed by another event.

Across accepted pairs, each later prior_fence must equal the previous replacement,
and the final replacement must equal the supplied expected_fence. The first pair's
prior fence has no separate initial-claim anchor in this helper; with no prior
pair, there is no final replacement comparison. Actual
[recovery evidence](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
constructors own exact fence transition laws: renewal advances generation with the
same worker, takeover advances with a different worker, abandonment has no
replacement, and retry preserves the fence. Caller request-state checks supply
the current claim relationship. Removing pairs preserves the original base event
objects and ordinals; it does not rewrite or renumber the durable journal.

For RENEW_ACTIVE_CLAIM, the stripped history must contain no saga events and only
RUN_OPENED as lifecycle history. Other admitted decisions, including a prospective
RETRY_AS_NEW_RUN, require exactly RUN_OPENED, RUN_STARTED, RUN_FAILED lifecycle
events. The saga projection must be FAILED with no compensation requested and no
in-flight or uncertain forward/compensation work. Thus a definite settled effect
failure can be eligible, while ambiguous or unfinished effect work is rejected.
This is eligibility to consider recovery, not a decision that replaying a real
external effect is safe. The caller still checks the appropriate active/expired
lease condition after this helper returns.

There is a distinction between asking to retry a failed run and reading a retained
run that already contains a retry marker. The former can pass the failed-journal
law; the latter cannot be treated as another ordinary lease-recovery pair. Historical
command replay follows explicit successor evidence through require_replay_run_evolution
instead of erasing retry markers and reusing the old journal as fresh work.

require_replay_run_evolution loads all actions for the execution request's session.
Starting at the retained run, it filters RECORD_RECOVERY_DECISION actions whose
nested recovery decision is retry-as-new-run and whose retained_run_id equals the
current run. No candidate ends traversal; multiple candidates conflict. A candidate
must carry exact-str successor run, decision-event and opened-event IDs and must
not revisit a run. It reads the successor with a request-scoped FOR UPDATE selector,
loads both events, and validates an ActivityRunRetryResult with replayed=True.
Only after that complete value passes does the traversal advance.

The actual [retry result contract](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/activity_run_retry.py)
checks exact record types, claimed request/claim, failed unsettled prior run,
same request/plan, distinct successor with attempt increment and exact prior-run
metadata, recovery/opened event linkage, fence agreement and equal result times.
The action must match the complete expected payload and carry valid idempotency/
fingerprint shapes. Replay permits its closed set of evolved successor statuses;
the helper does not require every historical successor still to be merely claimed.
It does not independently recompute each retry action fingerprint from a submitted
command or replay every successor's saga journal. Typed result coherence and the
outer command replay checks are distinct layers.

Traversal is bounded by the number of fetched session actions, with a visited-ID
set rejecting cycles. At the end it locks the request's latest run and requires
the full reached ActivityRunRecord to equal it, not just its ID. The selected actual
[execution store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
selects successors by both request and run ID before FOR UPDATE, and latest by
request with descending attempt, LIMIT 1 and FOR UPDATE. This prevents the helper
from accepting an unlinked latest row as lawful evolution. It is a read/validation
traversal, not adoption, chain repair or creation of missing successors.

The actual actions_for_session query reads all session actions with fetchall and
no page limit. Each successor search scans that tuple again; finite traversal is
not a constant resource bound for arbitrarily large sessions. Relevant branching
or malformed candidate evidence conflicts, but unrelated action payloads are not
all validated as retry actions by the filter. Consistency relies on caller
transactions and participating store/history rules; this module does not acquire
a global session-action lock or start its own isolated snapshot.

Expected store missing/invalid errors receive fixed NotFound/Conflict translations
outside handlers, avoiding retained exception chains. During successor reconstruction,
selected missing/conflict reads collapse to the common evolution conflict; a
missing latest run retains its own NotFound category. Actions lookup catches
OperationsRecordError/ValueError, not every driver exception. Projection catches
SagaJournalError/SagaStateError, and retry-result construction catches
OperationsRecordError. Unexpected database, attribute or other failures can still
escape. These helpers provide bounded expected errors, not universal log scrubbing.

Fresh callers invoke approval/journal checks before observing the database-timed
lease and planning/persisting a recovery. The lease interpreter rotates or abandons
the request and writes decision/consequence/action evidence. The retry interpreter
adds a successor run plus decision/opened/action evidence without rotating the
retained fence. Both request commit only after their exact writes succeed. Replay
callers invoke evolution and approval checks within their own transactions before
reconstructing the original result. None of these responsibilities turns this
private helper into the durable state owner.

The fully read
[support contract tests](../../../../../control-plane-kit-operations/tests/test_execution_lease_recovery_support_contract.py)
check three exact function signatures, empty exports/root absence, one inventory
entry and predecessor import ownership without duplicated old helper functions.
They are interface/static ownership checks, not runtime approval or journal coverage.
The fully read
[retry-totality tests](../../../../../control-plane-kit-operations/tests/test_execution_lease_recovery_retry_totality.py)
construct an empty plan and failed-run history containing a retry marker, both
terminal and followed by RUN_CANCELLED. They require the exact invalid-journal
conflict, no exception chain and unchanged event repr/object identities. They do
not prove a positive failed-effect recovery path or all fence-chain combinations.

Selected actual
[lease-recovery tests](../../../../../control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_first_replay.py)
wrap the shared approval/journal functions while preserving their implementation,
and forbid gateway-store get/get_for_update for both supported approval subjects.
They also mutate review_digest and require bounded rejection before IDs with an
unchanged fixture snapshot. Selected
[retry replay tests](../../../../../control-plane-kit-operations/tests/test_postgres_activity_run_retry_first_replay.py)
build two linked retries through the retry service, replay the first after the
second, and corrupt the last run's prior_run_id to require rejection. Their clock
and ID sentinels must remain unused and their selected request/run/event/action
snapshot unchanged. Failure between retries is constructed with lifecycle commands
and manually inserted step events, not a real failed adapter invocation. These
selected PostgreSQL assertions are not full-suite or live-runtime acceptance.

Read depth: full 390-line owner, 131-line support contract test and 156-line retry-
totality test. Selected caller first/replay/persistence paths, complete retry-result
validation helpers, recovery-evidence fence laws and actual history/execution
selectors were inspected. Selected PostgreSQL test sections cover shared-helper
delegation, approval subjects, linked replay/broken lineage and their relevant
fixture/snapshot helpers; their full files were not reviewed here. Meridian confirmed
no competing reservation before authoring. No source/pin changes, executable tests,
database setup, credential/private-key access, provider/runtime actions or publication
occurred. Documentation adds no security surface or authority to recover live work.
