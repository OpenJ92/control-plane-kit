Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/ingress_authority_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/ingress_authority_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

Three stores share this file: IngressAuthorityStore admits authority metadata,
IngressResourceStore retains owned-allocation epochs and status, and
GeneratedIngressSecretReferenceStore records custody/reference evidence. They
use a caller-supplied connection and never commit independently. No Cloudflare,
DNS, tunnel, secret provider or connector is contacted; local active/removed
records are not current provider observations.

Authority register builds the candidate and validates canonical admitted_at before
SQL, then looks for an active workspace/reference match. Equal authority returns
the first receipt; changed material conflicts. The schema's primary admission
ID and partial active workspace/reference uniqueness protect those relational
identities, but register has no lock/upsert/retry around its plain read/insert.
Concurrent callers can receive database uniqueness errors instead of sequential
replay. Re-admitting identical material after revocation attempts its old primary
ID rather than reactivating it; changed material can occupy a vacant active slot.

Authority get prefers active, otherwise newest admitted_at/ID, while list_active
loads the complete active set. active_page uses ascending authority_ref, strict
greater-than identity seek and limit+1 without a count or snapshot. The
require_active_for_hostname selector combines a plain active lookup with the
authority's local pattern check; it holds no row lock through subsequent effects.
Revocation updates local status and rereads, without affecting resource/secret
tables or recording revocation actor/time. Row decoding uses authority JSON;
redundant provider-kind, credential-reference and hostname-pattern columns are
not compared, and admission ID is not recomputed.

record_cloudflare validates the record type and all supplied timestamps before
its lookups. Allocating, active, removing, uncertain and orphaned rows are
blocking. An existing blocking row must equal the complete supplied record to
replay, including epoch/times/source fields; otherwise it conflicts. With no
blocking row, the store replaces the supplied epoch with MAX(epoch)+1 and inserts.
After allocation advances to a later epoch, repeating an input that still carries
epoch 1 is therefore not the same receipt replay. Removed rows remain retained
and allow a later allocation epoch.

The selected
[schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
has workspace/ingress/epoch primary identity, positive epoch, closed status and
lifecycle, removal-field consistency, workspace existence and canonical run-ID
checks. Its partial unique workspace/ingress index covers allocating, active and
removing, but not uncertain/orphaned, which the store also treats as blocking.
The blocking lookup and MAX+1 allocation are not serialized or retried here.
Their sequential behavior is stronger than the partial index alone and must
not be treated as a concurrent allocation protocol.

require_active_cloudflare selects active; get_cloudflare selects the newest
blocking epoch and omits removed-only history. list_cloudflare returns all epochs
ordered by ingress and epoch. mark_removing requires active and replaces
source_run_id. mark_removed accepts active/removing, adding removal time/run;
mark_uncertain accepts a blocking row and replaces source_run_id. Updates identify
workspace/ingress/epoch, without a prior-status predicate, row lock, RETURNING or
row-count check. Repeating mark_removed after removal is not an idempotent success
path. These helpers return constructed records and do not verify provider deletion
or append immutable transition events. observed_at and source activity/event stay
unchanged during these status updates, so source_run_id is not immutable creation
attribution after a removing/uncertain transition.

Generated-secret record validates recorded_at before lookup and keys replay by
workspace, purpose and source run/activity/event. Complete equal evidence returns
unchanged; any differing evidence under that key conflicts. Provider/reference
registration, custody and version fields are stored in metadata JSON; reads require
their text fields and a positive exact integer version, without rejecting all
extra metadata keys. The schema also makes workspace/secret_ref unique, so a new
source event using an already recorded reference can fail that uniqueness rule.
There is no row lock/upsert/retry or provider/reference admission query here.
The list is unbounded and orders recorded_at descending, then purpose/event ID.

Each of these tables references workspace existence. Resource source coordinates
and generated custody/provider/reference fields are not foreign-key proofs of
matching activity events, provider custody or registered authority. The
[value owner](../ingress_authorities.py.md) supplies additional shape checks;
store reconstruction normalizes timestamps to UTC but does not certify evidence
freshness. Driver/decoder errors are not universally redacted. Storage deliberately
retains provider IDs, hostnames and opaque secret handles, with no raw tunnel-token
field. This file implements neither compensation nor deletion of retained history.

Read depth: full 845-line owner, full 941-line language/service and
[1,097-line tests](../../../tests/test_ingress_authorities.py.md), selected actual
Core receipt/ingress/identity contracts, schema tables/constraints/indexes and
read-page/projection contracts; previously full UoW/temporal codecs. No database,
provider, credential, executable-test or runtime action was performed. These
notes change no security surface or data behavior.
