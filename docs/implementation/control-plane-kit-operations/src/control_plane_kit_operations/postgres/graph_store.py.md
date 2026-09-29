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
