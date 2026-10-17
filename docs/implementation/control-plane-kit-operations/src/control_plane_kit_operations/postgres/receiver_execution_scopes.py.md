Source: [receiver_execution_scopes.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/receiver_execution_scopes.py).
Maintain this companion alongside its source.

This existing complete-history owner can accept the active configuration
reader. Its graph, plan, event, action, scope and acceptance validators remain
authoritative while bounded transport joins one logical command budget.
Candidates and transaction guard probes also charge that reader. Without an
active configuration command, the original transport ledger remains unchanged.
Length probes, sentinels, complete membership and exact immutable cache keys
are retained; no fresh mutable authority is inferred from retained history.

Receiver scope evidence joins the active configuration reader when invoked by
advancement. Repeated prepare/finish reads retain their actual costs in the
same command ledger; a fresh storage instance does not reset accounting.

## #1993 shared record policy

When candidate discovery joins a configuration command, its remaining-record
calculation consumes `MAX_CONFIGURATION_EVIDENCE_RECORDS` (8192) from the
existing capacity owner. This is distinct from the unchanged native
`MAX_CANDIDATE_ROWS = 4096` and the unrelated 4096-byte row reservation estimate.
The query still intersects native cardinality, remaining transport bytes and
prior shared accounting before SQL. A filled shortened prefix still refuses
before request deduplication; no history read or freshness check is removed.
Focused owner validation passed at `1dacbc4a`; full validation is pending.
