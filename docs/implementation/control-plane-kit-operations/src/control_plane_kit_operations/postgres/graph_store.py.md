Source: [graph_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/graph_store.py).
Maintain this companion alongside its source.

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
