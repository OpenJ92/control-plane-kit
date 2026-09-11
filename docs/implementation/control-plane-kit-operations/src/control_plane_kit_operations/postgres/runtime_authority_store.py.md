Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/runtime_authority_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/runtime_authority_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This file owns RuntimeAuthorityStore and RuntimeAuthorityDeliveryStore on a
supplied PostgreSQL connection. The [application service](../runtime_authorities.py.md)
owns commands, scope decisions and commit requests. Authority rows admit Docker
access descriptions; delivery rows admit a workspace/authority delivery value.
Neither table records successful access delivery to a named recipient, live
provider permission, secret resolution or a successful Docker connection.

Authority register builds a deterministic candidate and validates admitted_at
before SQL. It finds the active workspace/reference row: equal runtime kind and
authority returns the old receipt, while changed material conflicts. Delivery
register also validates time first, requires an active authority by a plain
SELECT 1, then compares its active workspace/reference delivery for equality.
That active-authority requirement applies even to delivery replay. Neither
register acquires an advisory lock or calls get_active_for_update.

The [schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
has primary admission IDs and separate partial unique indexes enforcing at most
one active row per workspace/reference in each table. Concurrent registration
can therefore reach a database uniqueness error rather than the sequential
replay/conflict path; the store does not catch/retry it. Delivery's existence
check is not a row lock and has no authority-reference foreign key or active-state
constraint linking the two tables. Concurrent authority revocation is not
serialized through that check. Delivery admission also does not compare Docker
authority kind, TLS secret handles or provider permissions with delivery material.

Both getters prefer an active row, otherwise the newest admitted_at and ID among
revoked rows. They are historical detail selectors, not active-only permission
checks. list_active returns all active rows ordered by authority_ref. revoke
returns an already-revoked selection or updates the active row(s) for that
workspace/reference, then rereads. Revoking an authority does not update the
delivery table; active delivery lists do not join the authority table to recheck
its status. Caller workflows must distinguish these independent lifecycles.

Unlike image-pull registration, these register methods look only for active
matches. Re-admitting exactly the same material after revocation generates its
old primary ID and attempts insertion, which can fail uniqueness; it is not
implemented reactivation or historical replay. Changed material can generate a
new ID once the active slot is vacant. No tombstone deletion, provider cleanup,
socket unmount, credential removal or revocation event is performed here.

get_active_for_update is a separate authority selector for callers needing a
transaction-held row lock. It validates exact workspace/reference input types,
reconstructs the reference, selects active rows FOR UPDATE, requires exactly one
nine-column tuple, and normalizes selected ValueError decoding failures to fixed
lookup/row messages outside the caught exception context. Missing rows have a
fixed not-found message. It does not validate arbitrary driver errors or every
exception type, and the ordinary get/list/register paths do not share this
normalization. Its row lock is meaningful only within the caller's transaction;
it does not confer provider authority.

active_page on each store requires its own ReadCollection, filters workspace
and active status, seeks authority_ref strictly greater than the identity cursor,
and orders ascending with limit+1. IdentityReadCursor and ReadPage provide the
common bounded continuation envelope. There is no offset/count or snapshot:
status changes and newly admitted references can change later pages. Full
list_active selectors remain separately unbounded. Delivery paging uses the
relational authority_ref for the cursor and decodes the delivery JSON for the
item; it does not compare those redundant representations.

Authority persistence writes a full storage descriptor with the private endpoint
and a separate credential-reference JSON projection. Reads reconstruct from the
authority JSON and runtime-kind column, not the separately stored authority_kind
or credential-reference columns. Delivery reads reconstruct from delivery JSON,
not the redundant delivery-kind/secret-reference columns. Neither row decoder
recomputes admission ID. Timestamps normalize to canonical UTC and record
constructors validate their shapes; this is not full redundant-column coherence
validation. The schema allows more runtime-kind spellings than the current
Docker-only authority value admits.

The read projection adds redaction to public descriptors. Database storage
deliberately retains endpoint and reference material; metadata and driver errors
are not universally bounded/redacted here. Stores commit nothing independently
and record first admission plus current status, without revocation actor/time
or separate activity history. Read depth: full 595-line owner, full 674-line
language/service and [701-line tests](../../../tests/test_runtime_authorities.py.md),
selected Core delivery/reference, schema, read-page and projection contracts;
previously full UoW/temporal codecs. No executable validation, database activity,
credential access or runtime mutation accompanied this documentation.
