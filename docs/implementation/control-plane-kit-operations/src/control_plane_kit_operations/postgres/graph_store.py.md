Source: [graph_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/graph_store.py).
Maintain this companion alongside its source.

The graph store also owns receiver point reads and complete binding-set reads.
Its private receiver helper shares the caller's connection; it is neither a new
StoreBundle service nor a package-root export. Private writers reserve immutable
origins, persist derived binding sets and record paired acceptance/retirement
witnesses. They require the same store, workspace and still-active transaction
as the lifecycle guard. The guard retains the existing advisory lock identity
and carries its PostgreSQL transaction ID to reject expired retained guards.

Receiver origin replay must match the immutable tuple exactly. Reservation
orders receiver IDs globally, uses PostgreSQL unique conflict arbitration and
then reads only within the caller workspace. A collision returns one bounded
detached conflict without disclosing foreign provenance. Stored graph/projection
records are authoritative; caller records are checked against exact retained
material before derivation. C owns action meaning and semantic admission.

Workspace and immutable authored/realized graph stores use their caller's
connection and never commit independently. Authored graph INSERT relies on the
global primary key to resolve concurrent name collisions. Only PostgreSQL
`UniqueViolation` naming `cpk_graph_versions_pkey` becomes the inward domain
`GraphIdentityConflict("graph identity is unavailable")`. Raising after the
exception handler removes driver detail and foreign identity from its chain.
All other constraints and unexpected errors retain their identity.

The existing unit of work rolls back the aborted transaction, including graph,
projection, pointer and action writes. There is no precheck race, overwrite,
adoption, retry, schema change or independent store transaction. Real PostgreSQL
planning tests prove equal-name refusal, foreign-state preservation, concurrent
one-winner behavior, and unrelated late-action uniqueness rollback. The store's
domain import points inward; graph_authoring has no backend dependency.

`receiver_authoring_snapshot()` vends the private bounded graph/provenance
reader for the UoW's explicit snapshot entry. The original-action validator is
shared with the existing mutation path; only its pure correspondence checks
are factored. The read uses no raw unbounded getter or lifecycle lock and cannot
write acceptance, reserve identities or acquire external evidence.

## O2 / #1883 current boundary

A narrow private _require_receiver_lifecycle method revalidates an already-held graph guard through the existing receiver storage owner. It checks the same store and transaction without acquiring lifecycle L again, allowing nested health reload to reject stale/foreign prefixes before later row locks. It introduces no public authority value or receiver policy.

Implementation validation is pending on PR #1915. The reviewed target-only red
checkpoint establishes only its recorded missing boundaries, not these green laws.

B1 configuration commands account exact graph/projection and receiver-origin
reads through their shared bounded transport. Original action/session joins
charge both identities; optional original draft-revision witnesses also consume
the command budget. The existing pure action-attribution validator still owns
correctness, and budget exhaustion refuses rather than loading an uncharged
fallback. No lifecycle permission or mutable authority is cached across UoWs.

B2 E7 adds original workspace initialization to this existing store owner.
Private creation/graph/pointer/receipt helpers require the same prepared creation
and live lifecycle guard. The existing pointer helper supplies the sole identity
projection. The immutable receipt is checked against the exact original empty
graph, projection, creator and idempotency metadata, with bounded point reads;
replay verification does not require today's current pointer to remain initial.
No helper commits, adopts metadata as origin, backfills a missing receipt or
grants accepted configuration membership. The focused owning gate at bf8d8b64
passes nine initialization and nine schema targets, compile and clean import;
broader E7/B2 validation remains pending on #1924.

The E7 command evidence scope roots/joins the existing ledger before the initial
workspace lookup. It covers guarded creation writes as scalar returns, lifecycle
checks, original receipt and response reads. Identity projection discovery and
conflict lookup use bounded owner reads. Under that ledger, projection INSERT
reserves only its bounded ID return, then retrieves the complete immutable row
through the existing bounded getter; zero-row conflicts retain statement cost.
Standalone roots are used only outside an active command, such as current-data
verification. The review-found metadata transport and missing-ledger defects
have focused causal-red evidence and pass their unchanged assertions in that
green gate. The E1/E2 accounting source now charges and bounds explicit-projection
selection. Active current CAS reserves a scalar return and then retrieves the
complete workspace through its bounded getter; misses still consume statement
cost. These advancement changes await their focused gate and prepared-owner
integration before acceptance.

B2 private current CAS requires the same live prepared original advancement
owner as the paired history writers. Every source/destination graph, projection
and pinned revision must match preparation before the existing conditional update.
The owning UoW rolls back CAS if history or acceptance preflight fails.

The no-prepared legacy current setter/CAS retains its material-only behavior for
receiver-free, configuration-free graphs. It validates both authored and realized
material under the existing lifecycle guard and creates no acceptance authority.
Desired-only material writes retain their prior contract.
