Source: [control-plane-kit-operations/src/control_plane_kit_operations/delegation_signing_keys.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/delegation_signing_keys.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

RegisteredDelegationSigningKey combines immutable workspace/purpose/issuer/key
identity with verify-only, active, retired or revoked lifecycle evidence. It stores
public verification material and a private SecretReference, not private key bytes.
The service owns one unit of work per register/activate/retire/revoke command;
provider generation, private-key resolution, signing and network distribution of
verification sets belong to other owners.

Registration derives dkey_ plus SHA-256 from canonical identity fields including
the public fingerprint and private reference. same_identity_as compares the full
normalized public PEM as well as workspace, purpose, issuer, key ID, algorithm
and private reference; admission actor/time and lifecycle are excluded. Thus
matching registration replay can preserve earlier attribution and current status.
This is durable identity, not a new registration on every command.

The selected Core [key value](../../../../../control-plane-kit-core/src/control_plane_kit_core/delegation_keys.py)
supports Ed25519 and four purposes. It normalizes/fingerprints bounded ASCII
public-PEM text but does not parse the key cryptographically or prove it matches
the referenced private key. descriptor omits PEM through the Core descriptor and
exposes the private reference handle plus lifecycle metadata. This is intentional
reference/public-metadata disclosure, not a guarantee that every object or
exception is safe for arbitrary public logging.

Service commands require their distinct PolicyScope membership. Caller-supplied
actor identities/scopes are not authenticated here; registration/lifecycle
commands do not perform the strict tuple-of-enum validation used by generation.
register and activate require an active private reference with
GATEWAY_PROBE_SIGNING_KEY intent, including for the other supported purposes.
Those reference reads use get_active, not its FOR UPDATE variant, and do not
independently contact/revalidate a live provider. retire/revoke do not require
the reference to remain active.

The [Postgres key store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/delegation_signing_key_store.py)
owns transitions and replay. Mutations take a workspace/purpose advisory lock,
then an issuer-scope lock and target row lock. The schema also enforces one active
key per workspace/purpose/issuer. Activation accepts verify-only, demotes the
previous active key in that issuer scope to verify-only and retains it for overlap
verification. Already-active activation returns the stored record. Retirement
accepts verify-only or returns an already-retired record; revocation accepts any
existing non-revoked status or returns an already-revoked record. Retired/revoked
keys are excluded from verification selection; these transitions do not delete
the provider's private material or push updates to external verifiers.

Uniqueness is per issuer, not one signer across every issuer in a workspace.
The store's separate require_unambiguous_active selector rejects zero or multiple
active issuers for one purpose. These lock/selection rules must not be expanded
into a promise that non-locking secret-reference reads remain fresh through
commit. Provider/reference lifecycle and key lifecycle have distinct stores.

The record checks paired actor/time fields and evidence required by the selected
status, not a complete chronology/state machine. Local time checks are short-text
checks; store mutations encode canonical UTC before their own connection access,
including replay, and row decoding normalizes aware database timestamps to UTC.
Services may already have read records before reaching that store validation.
Lifecycle replay preserves the original transition receipt rather than comparing
every newly supplied actor/time field. Store row decoding verifies the persisted
public fingerprint against normalized public material.

[PostgresUnitOfWork](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
commits only on successful exit after the service requests commit, otherwise
rolls back and closes. Lifecycle actor/time columns are durable history here;
these commands do not append operation sessions/actions or activity events.
No provider effect or cross-provider compensation occurs inside the transaction.

Read depth: full 391-line owner and [683-line tests](../../tests/test_delegation_signing_keys.py.md),
full 469-line key store, Core key value, unit of work and temporal codecs;
selected reference/store/schema and suite dependency contracts from the same
checkout. This documentation changes no security/runtime behavior and involved
no test execution, database access, key generation or credential inspection.
