Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/image_pull_authority_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/image_pull_authority_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

ImagePullAuthorityStore retains workspace-scoped registry/repository authority
references on a supplied PostgreSQL connection. The
[registration service and values](../products.py.md) own commands, deterministic
identity and the surrounding UoW. A row contains the Core authority descriptor,
registry/repository columns, an opaque credential reference, first admission
actor/time, active/revoked status and metadata. This owner does not resolve a
credential, validate provider permission, authenticate to a registry or pull an
image. Core's permits predicate is a separate scope calculation.

register constructs the candidate and encodes canonical UTC admitted_at before
its first lookup. An existing workspace/authority ID returns unchanged, including
first attribution and revoked status. An unseen identity scans the workspace's
active rows for equal registry and repository and raises a replacement conflict
when that exact scope is occupied. The candidate ID includes the credential
reference, so changing a credential under the same active scope takes that
conflict path. Exact repository equality is the check; overlapping parent/child
repository scopes are not rejected by this scan.

The selected
[schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
enforces authority primary ID, workspace existence, active/revoked status and
a secret:// prefix on the credential-reference column. Its scope index is not
unique. No lock, upsert or serialization retry surrounds register's read/scan/
insert sequence. Sequential replay/replacement behavior must not be generalized
to concurrent callers: distinct candidate IDs can pass the same scope scan,
while identical inserts can meet a database uniqueness error. Driver failures
are not converted to a replacement decision here.

get scopes by workspace and authority ID. list_active orders registry,
repository NULLS FIRST and authority ID, loading the whole active set without
paging. revoke returns an already-revoked row unchanged; otherwise it updates
status and rereads. The record remains inspectable. Re-import of that same
identity does not reactivate it, and revocation does not delete credential
material, revoke a registry token or affect running workloads.

Row reconstruction decodes the authority JSON through ImagePullAuthorityCodec,
normalizes the stored timestamp to UTC and checks the registered value's shape.
The separately stored registry/repository/credential-reference columns are not
selected for comparison with that JSON, and authority ID is not recomputed.
Metadata is a Mapping without a local deep-redaction or size policy. Reference
handles are deliberately retained; arbitrary stored values and driver exceptions
are not certified safe for public logging. The store commits nothing independently
and appends no separate revocation actor/time or activity event.

Read depth: retained full 186-line owner read, guarded unchanged against source
087a892 for this companion; full products owner and
[registered-product tests](../../../tests/test_registered_products.py.md), selected
actual Core ImagePullAuthority/codec and schema contracts, previously full
UoW/temporal codecs. No tests, database/credential/registry operations or source
changes occurred. This documentation introduces no new security/runtime surface.
