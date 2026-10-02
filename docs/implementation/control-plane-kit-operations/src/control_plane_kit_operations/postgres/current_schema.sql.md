Source: [current_schema.sql](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql).
Maintain this companion alongside its source.

Receiver storage adds graph-owned introductions and complete per-projection
binding sets. Seven keys include global receiver uniqueness and structural
action/session and draft/graph support. Seven checks constrain receiver/digest
grammar and paired acceptance/retirement witnesses. Fifteen NO ACTION foreign
keys retain exact workspace, graph, source projection, action/session, draft and
scope provenance. Only original binding is deferred until commit; introducing
and binding rows are created in one caller transaction. Store transitions own
write-once history, without temporal triggers or a public arbitrary-row writer.
No lifecycle admission or execution authority follows from these facts.

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

Managed health execution adds NOT_READY attempt state and the
`native-connection` outcome profile. This profile allows only SUCCEEDED or
NOT_READY with zero endpoint-observation rows; its protected canonical preimage
retains the original bounded native sample, acceptance timestamp and reason.
The acceptance timestamp must agree with the completion event. Native counts
use canonical decimal strings so the full uint64 domain is preserved.
Explicit observation restart/not-ready event vocabulary retains immutable
attempt commitments. The existing receipt leaf retains nullable managed caller
and predecessor provenance; legacy receipt fingerprints remain unchanged.

These are changes to the exact current schema, not an automatic migration.
Installation still rejects incompatible populated namespaces without changing
them. Application admission/fold transactions own all related writes.

B1 / #1923 adds immutable `cpk_effect_configuration_refs` and
`cpk_configuration_claims`. Exact original intent commitments retain provenance;
a deferred self birth FK and reciprocal ref/claim FKs require one complete
protective aggregate at commit. A partial unique index allows one birth per
workspace/allocation. Indexed ref identities must agree with the canonical
preimage and digest under current-row verification. The owning start transaction
writes this aggregate; installation still accepts only empty or exactly current
namespaces, with no automatic migration, deletion or backfill.

B2 E7 adds `cpk_workspace_initializations`, keyed by workspace with the exact
original graph/projection pair, canonical graph commitment, existing projection
digest, explicit zero configuration membership and original creator/key. Four
restrictive FKs preserve the workspace and graph/projection composition. Only
the guarded creation owner writes it, atomically with the existing creation
transaction. Missing original evidence refuses the new authority path; the
installer does not fabricate receipts for legacy workspaces. Source validation
is pending on draft PR #1926.
