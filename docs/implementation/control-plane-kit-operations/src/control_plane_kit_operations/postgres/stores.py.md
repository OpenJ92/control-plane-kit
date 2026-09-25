Source: [stores.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/stores.py).
Maintain this companion alongside its source.

The frozen PostgresStoreBundle constructs every store from the caller's one
connection. `health_effect_preparations` exposes immutable health evidence using
that same connection, alongside original intents, attempts, graph projections,
keys and secret-use owners. It introduces no independent connection or commit.
Unit-of-work callers retain transaction and rollback ownership. Merely creating
the bundle performs no admission, signing, provider I/O or schema mutation.
