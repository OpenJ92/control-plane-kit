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

At this source checkpoint, typed original writer guards and complete acceptance
headers are still pending. The focused target gate records their causal failures
on PR #1926; this accounting change alone does not establish B2 acceptance.
