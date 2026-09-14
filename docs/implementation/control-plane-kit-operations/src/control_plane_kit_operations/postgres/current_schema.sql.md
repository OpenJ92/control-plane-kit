Source: [current_schema.sql](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql).
Maintain this companion alongside its source.

Fresh stores admit `workload-node-health-read` and
`gateway-node-health-read-transit` in signing-key rows, and their exact
`workload.node-health-read-signing-key` and
`gateway.node-health-read-transit-signing-key` use intents in authorization rows.
Old literals retain their order. Rotation purpose storage is unchanged.

The SQL and frozen semantic mirror change only those two constraint expressions.
`test_current_schema_installation.py` checks exact vocabulary, actual health row
persistence, query-only reentry, refusal of each pre-health constraint with data
intact, and continued unknown/health-rotation rejection. Existing installer
transaction, concurrent installation, rollback and drift laws still govern.

Installation executes only in an object-free namespace. Retained incompatible
stores are never reset, migrated or backfilled by this program. The bounded
reset-required diagnostic does not authorize a reset. A future live deployment
must prove a fresh target or supply its separately reviewed retained-store plan.
Persistence alone does not authorize generation, signing or runtime execution.
