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

`get_plan_for_share` is an additive bounded read for current health authority
reload. It shares the original query/decoder with `get_plan`, adding only the
constant `FOR SHARE` lock through the caller's transaction. It introduces no
plan mutation or new approval policy. Missing/decoder/driver exceptions retain
their existing identity. The ordinary getter and list/projection behavior are
preserved.

`_projection_publication_actions` is a private retained-association lookup for
an already validated child session. Its parameterized SQL selects only the
session, publication kind and desired projection, returning at most two typed
actions. The caller requires exactly one and checks workspace, independently
expected rotation/version/revision/base/phase and original material. A malformed
matching duplicate cannot be filtered out in favor of a valid row. No full
session list or arbitrary long-history cutoff is used. LIMIT bounds returned
row/decoding count only: payload bytes and PostgreSQL scan/JSON predicate work
are not capped by this query. No schema/index change or independent commit.

B1 configuration commands use bounded complete-row reads for pinned plans,
sessions and approvals, with the existing decoders and lock semantics. Each
read joins the logical command's budget. Legacy callers keep their original
path; history is not replaced by a compact or fabricated execution context.

During an active configuration ledger, action idempotency locking, original
lookup, ordinal allocation and action INSERT returns are charged before SQL.
Original payload and text lookup cells are bounded; writes return a scalar.
Inactive callers retain their existing behavior. B2's separate prepared
advancement writer boundary is still pending at this accounting checkpoint.

B2 advancement actions now require the private prepared owner and exact original
record. Typed workspace/request/plan/run/revision locators derive from that owner;
public standalone advancement insertion refuses. Unrelated kinds retain NULL
locators. The action and paired event/header remain in the caller's transaction.
