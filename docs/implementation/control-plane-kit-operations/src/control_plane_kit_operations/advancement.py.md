Source: [advancement.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/advancement.py).
Maintain this companion alongside its source.

#2078 adds `execute_with_diagnostic` for the application adapter, sharing the
same computation and UnitOfWork with ordinary `execute`. The latter retains
original success, exception identity and cancellation behavior. The diagnostic
entry catches only Exception after complete context exit and returns an immutable
closed stage/reason/correlation with acceptance unverified. Unrepresentable
correlation re-raises the original failure. Per-call state distinguishes an
unchanged body exception from a new exit exception; result construction remains
in the body. A commit request is not a physical commit, and even a postcommit
close failure stays unverified. No diagnostic SQL, events, exception attributes,
second execution or persistent state is added. Detail is available only after
already-read run/request linkage binds the requested workspace, run and plan;
otherwise fresh and replay failures use unknown/internal-error and echo only the
requested correlation. No extra reads or admission changes are introduced. The
body-error tracker surrounds the inner evidence context, so its existing conflict
translation remains a body failure before the outer UnitOfWork exits. Focused
green evidence is pending.

Current source supports nonempty membership and E4.C qualified claim transfers;
see the final section below. The earlier B2 slice and pending-review statements
record historical stages, not current restrictions or acceptance status.

Current advancement retains the existing action-idempotency, fence, lineage,
revision and complete-success laws. Fresh mutation uses lifecycle then its own
request, run, session and workspace locks. Original replay uses its original
receipt before any fresh lifecycle selection.

B2 roots one evidence ledger through the existing configuration store before
preliminary run/request reads. All participating bounded getters and scalar
write returns share that ledger; closed capacity/unavailable failures become a
bounded advancement conflict. The caller UoW still commits or rolls back all
writes. Accounting is resource evidence, not acceptance or mutation authority.

The next bounded source slice prepares original evidence before CAS, then writes
typed event/action records and an explicit zero-slot acceptance header in the
same UoW. Public standalone advancement history insertion refuses. Consumer
preflight and original-header replay use the shared ledger. This slice supports
validated runtime-only projections; nodes refuse until complete membership is
implemented. Source review and focused Docker validation are pending; the earlier
accounting-only green does not establish typed/header or full B2 acceptance.

## E4.C: current acceptance now produces qualified transfers

The historical zero-slot-only description above is superseded by the current
nonempty acceptance path and issue #1947. Fresh advancement prepares exact
membership and eligible own admitted successful configuration completions, then
checks material, future-consumer and publication capacity before clock/ID
allocation. It binds the actual event/action and checks the bound declaration
again before current CAS.

Current CAS, original event/action, acceptance header/slots, all eligible paired
claim/ref transfers, and receiver FINISH share the caller's single commit.
Transfer insertion follows actual receipt/slot readback and precedes FINISH.
The additional transfer-phase guard checks current membership even when there
are no eligible transfers. Any failure rolls back the compound transaction.
Original replay validates and returns its own history without minting transfers,
backfilling old accepted claims or dispatching runtime work.
