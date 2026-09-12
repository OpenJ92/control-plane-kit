Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/gateway_key_rotation_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/gateway_key_rotation_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

GatewayKeyRotationStore persists four related kinds of truth on the caller's
connection: the rotation row, one deployment checkpoint per phase, one exact
revocation checkpoint, and versioned transition receipts. It does not own caller
authentication, legal transition planning, worker-fence validation, provider
effects or transaction commit. The
[service](../gateway_key_rotations.py.md) supplies these local orchestration
checks and groups mutations inside its UoW.

lock_binding takes a transaction-scoped PostgreSQL advisory lock derived from
workspace, gateway node, purpose and issuer. Request uses it around correlation
and nonterminal-binding selection. for_correlation is workspace/correlation
scoped; nonterminal_for_binding excludes completed, blocked and rejected. get
and get_for_update select by rotation ID alone, the latter locking the main row.
Each decoded row also issues separate deployment and revocation queries. These
child reads do not themselves lock rows. Direct read methods are not an access
control or secret-redaction boundary.

add encodes main-row timestamps before INSERT and inserts the main row only.
It does not persist nested checkpoints that a direct caller might put on its
argument. Normal service requests create the initial empty-checkpoint value.
The service factory owns deterministic identity/fingerprint derivation and
request replay; add neither recomputes these nor normalizes uniqueness errors
into an idempotent return.

compare_and_set first encodes every supplied mutable main timestamp, both
deployment preparation/acceptance timestamps and revocation preparation time,
before its first SQL statement. It updates the main row where rotation ID,
status and version match current, returning None if no row matches. It updates
mutable state/evidence/attribution, not immutable request intent. The store does
not independently enforce replacement.rotation_id == current.rotation_id, a
legal transition or exactly one version increment; the service constructs the
replacement with dataclasses.replace. Child writes and the final get use the
replacement identity, so bypassing that service contract is consequential.

After a successful main update, supplied deployment/revocation checkpoints are
upserted, then the replacement is read back. A checkpoint conflict can occur
after the main UPDATE; rollback of the enclosing UoW is necessary to undo the
whole operation. The method is not a standalone transaction or compensation
boundary. Absent replacement child values do not delete existing child rows.

Deployment upsert fixes rotation/phase and all preparation identity: session,
plan, approval IDs, execution request/run, base and desired graph/projection,
revision and prepared_at. It may update status and accepted coordinates/time
when the old status is prepared or equals the supplied status. Consequently a
direct same-status accepted upsert can replace accepted coordinates/time while
preserving preparation identity. This predicate does not make acceptance fully
immutable. Service-level fence, linkage and advancement-evidence checks provide
the stricter acceptance boundary; the store itself queries none of that truth.

Revocation upsert requires every existing field to equal the supplied value,
including provider/reference/version, revocation ID, correlation, digest and
prepared_at. Its nominal update merely assigns the same provider registration
value. A mismatch returns no row and becomes a conflict. This preserves an exact
checkpoint, not evidence that the provider performed revocation.

add_transition encodes advanced_at before INSERT. transition_for_id selects
rotation/transition identity; transitions returns all receipts ordered by
to_version with no pagination. These methods do not perform the main CAS or
verify legal from/to edges. Service transition replay compares the retained
fingerprint; the store simply returns the decoded receipt.

The [current schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
enforces main rotation identity, unique workspace/correlation and a partial
unique nonterminal workspace/node/purpose/issuer binding. Child primary keys
are rotation/phase, rotation, and rotation/transition; transitions additionally
have unique rotation/to_version. Children reference their rotation and the
rotation references its workspace. Deployment plan, run, approval and graph
coordinates, and revocation provider/version coordinates, are stored text here
without corresponding foreign keys to those owners. Service linkage is material.

SQL checks include the status alphabets, deployment acceptance all-or-none,
nonnegative desired revision, bounded run-ID grammar, transition consecutive
versions, selected digest/reference shapes, lifetime/skew ranges and main-field
pairs. Main failure_code must be present exactly for blocked/rejected. They do
not encode the complete legal state graph, recompute fingerprints, prove child
effects, or require every status-specific checkpoint across tables.

Row decoding reconstructs typed purposes/statuses, SecretReference, checkpoints
and rotation/transition values. Canonical timestamp helpers encode UTC inputs
and normalize aware PostgreSQL timestamps on reads; nested constructor checks
can reject inconsistent retained values. Decoding does not compare the entire
transition history or rebuild the intent digest. Secret references are persisted
as handles, with no resolution or private key material accessed by this store.
History is retained; there is no cleanup, expiry, external rollback or runtime
network exposure in this module.

Read depth: full 357-line store, full 1,381-line service owner and
[731-line tests](../../../tests/test_gateway_key_rotations.py.md), with selected
actual table/constraint/index/foreign-key definitions. UoW and timestamp helpers
were previously read in full. Tests were inspected, not executed for this
documentation change. It introduces no new security or data-mutation surface.
