Source: [control-plane-kit-operations/src/control_plane_kit_operations/secret_providers.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/secret_providers.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner defines provider/reference admission, durable authorization of one
secret use, and pure grant/custody projections. RegisteredSecretProvider names
the provider, opaque endpoint and credential references, allowed reference
prefixes/intents and admission history. RegisteredSecretReference pins one handle
to an exact provider registration and allowed intents. AuthorizedSecretUse records
one correlated use. These objects contain no resolved secret material and do not
perform provider IO, encryption, custody writes or runtime delivery.

Provider kind currently admits only control-plane-kit-secrets. Providers and
references have active/revoked/superseded states; revoked requires actor/time,
while other states reject revocation fields. Prefixes and intents are nonempty
typed tuples, deduplicated and sorted. A reference must share workspace and
provider identity, stay within an allowed prefix and request a subset of provider
intents. Prefix comparison uses the parsed Core reference's path-segment tuple,
not a raw string prefix: a sibling path with the same initial characters is not
automatically inside the admitted subtree.

Metadata is copied into a MappingProxyType after validation: at most 32 lowercase
identifier keys and scalar values, no nested objects, finite floats and bounded
nonempty string values. Selected exact secret-bearing key names and string markers
such as URL schemes, Bearer and PEM material reject. Display names have similar
marker checks. This is stronger than an arbitrary Mapping but not a universal
secret detector or aggregate encoded-byte bound; integers and prefix tuple counts
have no separate size cap here. Direct descriptors intentionally expose opaque
endpoint/credential/reference handles and accepted metadata. The
[read projection](read_services/authority_secrets.py.md) returns typed provider/
reference descriptors without applying its generic authority redactor.

sprov_ and sref_ IDs hash canonical sorted JSON admission semantics, including
metadata and supersession target, but excluding admission actor/time and lifecycle
status. same_admission_as uses those semantics. Commands build validated candidates
and normalize typed PolicyScope tuples. The service requires SECRET_PROVIDER_REGISTER
for both provider and reference registration, and SECRET_PROVIDER_REVOKE for both
revocations. Scopes and actor strings remain caller inputs that the composing
authentication boundary must establish. Direct record constructors do not recompute
the supplied deterministic ID.

Reference registration selects an exact active provider through the ordinary
non-locking selector, validates the reference policy, then calls the reference
store. It does not resolve the handle or prove the provider is still active
through commit. The [stores](postgres/secret_provider_store.py.md) own replay,
supersession and lifecycle mutations. Existing matching IDs preserve old records,
including revoked/superseded history, rather than reactivating them. Provider
revocation/supersession leaves reference rows intact, but subsequent use must
select their pinned provider registration as active. This is local admission
enforcement, not revocation of provider-held bytes or credentials.

SecretUseAuthorizationService requires SECRET_PROVIDER_USE and validates
canonical UTC requested_at before opening a UoW. Its shared in-UoW helper repeats
these checks, locks workspace/correlation, locks the active reference, then locks
its exact active provider registration. It revalidates workspace/provider/prefix/
intent admission before considering replay. The fingerprint binds registration
IDs, reference, intent, actor, correlation and operation/session/run/activity/
effect/probe coordinates; requested_at and caller scopes are excluded. A matching
correlation returns the first receipt/time, while changed fingerprint conflicts.
Revoked or superseded authority can prevent replay because active checks come
first. This helper inserts evidence but does not commit independently.

authorize_resolution projects the authorized row and selected provider into a
[Core SecretResolutionGrant](../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py)
and returns it only after successful UoW exit/commit. The standalone
secret_resolution_grant_for helper checks value types and matching workspace/
provider registration, but does not itself inspect a commit or provider status.
Similarly, authorized_secret_use_for builds evidence from supplied objects; the
surrounding authorization program supplies the locked admission checks. Core
grant permits compares reference and intent; these values have no expiry or
provider signature and do not by themselves prove current external permission.
Locks end with the Operations transaction, before later provider resolution.

secret_custody_grant_for constructs authority for a generated reference using
supplied provider truth. It checks the selected required scope (default
SECRET_PROVIDER_USE), workspace, prefix and intent policy, but does not select
or check active provider status, commit a ledger row or perform custody. Its
custody ID hashes workspace/provider registration/reference/intent; the custody
fingerprint also includes actor/correlation and operation coordinates. This
distinguishes stable custody identity from one caller's detailed request.

generated_secret_reference_candidate requires typed grant/receipt and uses the
receipt's matches predicate: custody ID, provider registration and reference.
It retains custody/version metadata in a reference candidate. That predicate
does not require active receipt status or attest a provider mutation. The
helper's internal registration-scope tuple constructs a value; it is not an
authenticated registration or write. Correlation helpers hash contextual fields
for retry-stable strings; they validate selected inputs and run identity, rather
than every optional field checked later by the resulting command/grant.

Admission/revocation commands use one caller-created UoW and request commit.
Admission records retain supersession links and revocation actor/time; secret-use
rows retain authorization intent. These are not provider resolution outcomes,
and this owner appends no separate activity run/event or compensation record.
Local record timestamps are bounded text; registration stores encode canonical
UTC before their own SQL, including replay. Selected validation errors may retain
imported causes, and accepted public text is not certified safe for every log.

Read depth: full 1,307-line owner, full 939-line combined store and
[1,096-line tests](../../tests/test_secret_providers.py.md); selected actual Core
provider/reference/resolution/custody values, schema and read projection; retained
full receipt, run/activity grammar, UoW and temporal reviews. Source remains
087a892. No tests, database, provider, credential or runtime operations were run;
this documentation introduces no new security surface or authority.
