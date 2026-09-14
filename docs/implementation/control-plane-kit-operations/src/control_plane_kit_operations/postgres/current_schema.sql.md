Source: [current_schema.sql](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql).
Maintain this companion alongside its source.

The current fresh schema includes `cpk_health_effect_preparations`, an immutable
leaf with eighteen columns: structured attempt identity, workspace/logical request,
original fingerprint/event commitment, two projection identities, four family
registration identities, both issuer/JTI pairs and the protected canonical preimage.
Checks bound identities and transport; primary and three independent unique keys
arbitrate retries and logical request/grant collisions. Eight restrictive foreign
keys retain the exact attempt, original intent, two workspace projections, two
workspace key registrations and two workspace use authorizations. No reverse
preparation requirement changes unrelated attempt ownership.

All owners must exist before insertion. The caller owns the transaction and can
roll back the insertion with the surrounding first-start evidence. There is no
new lifecycle column, mutable deadline, actor duplicate or private material.
Historical reference/key revocation and grant expiry remain readable evidence.
Current active authority is owned by subsequent admission/dispatch composition.

The existing two health signing purposes and use intents remain admitted;
rotation storage keeps its prior vocabulary. The frozen semantic mirror and
fixed metadata tests change together. The ordinary native suite compares this
SQL's actual PostgreSQL catalog to the literal and exercises concurrent inserts,
restart, corruption, bounded reads and query-only current reentry.

Installation executes only in an object-free namespace. Incompatible retained
stores are refused intact, never reset, migrated or backfilled. The reset-required
diagnostic does not authorize a reset. No live database/provider mutation is part
of this source slice.
