Source: [advancement.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/advancement.py).
Maintain this companion alongside its source.

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
