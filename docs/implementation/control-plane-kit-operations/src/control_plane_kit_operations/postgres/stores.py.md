Source: [stores.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/stores.py).
Maintain this companion alongside its source.

The frozen PostgresStoreBundle constructs every store from the caller's one
connection. `health_effect_preparations` exposes immutable health evidence using
that same connection, alongside original intents, attempts, graph projections,
keys and secret-use owners. It introduces no independent connection or commit.
Unit-of-work callers retain transaction and rollback ownership. Merely creating
the bundle performs no admission, signing, provider I/O or schema mutation.

The B1 configuration preparation/provenance store shares this bundle's exact
connection. It grants no independent transaction or provider capability; its
private prepared value is valid only for this bundle, original intent/identity
and held lifecycle guard.

The B2 configuration acceptance store shares this same connection and caller UoW.
It prepares original advancement evidence and validates retained receipts; it
introduces no independent transaction or provider capability.
