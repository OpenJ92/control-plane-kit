Source: [activity_history.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/activity_history.py).
Maintain this companion alongside its source.

`add_plan` preserves the existing lineage-enforcing insert and caller-owned
transaction. Its existing JSONB payload stores either the unchanged legacy Core
descriptor or the strict Operations envelope defined by `plan_derivation`.
No column, constraint, index, migration, backfill or installation policy changes.

The shared `_plan_record` decoder retains the optional profile through get,
session list, paginated list and overview reads. Unknown or malformed envelopes
produce a bounded, cause-free `PlanDerivationError`; they are never treated as
legacy. Existing stored/canonical JSONB values are preserved, without claiming
preservation of arbitrary input JSON text formatting or physical database bytes.

Stores never commit independently. The planning service writes the matching plan
and action profile in one transaction under its existing idempotency lock. Replay
does not rewrite payloads, allocate records or backfill absent profiles. Old
readers cannot read newly profiled envelopes; do not downgrade them onto such
history or strip markers as an operational workaround.
