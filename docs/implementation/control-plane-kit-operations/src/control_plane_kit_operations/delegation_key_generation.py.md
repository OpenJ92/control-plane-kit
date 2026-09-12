Source: [control-plane-kit-operations/src/control_plane_kit_operations/delegation_key_generation.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/delegation_key_generation.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

DelegationKeyGenerationService prepares reference-only custody authority and
admits a provider result in a later transaction. The generator is a Protocol
supplied at server composition; this service never calls generate, creates key
bytes or contacts a provider. The caller owns the intervening effect and its
uncertain-outcome/recovery policy. No database transaction spans that effect.

prepare requires DELEGATION_KEY_GENERATE and an observed active provider
registration in the command's workspace. The selected
[custody helper](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/secret_providers.py)
checks provider identity, reference prefix and allowed intent, then derives
deterministic custody identity/fingerprint from the supplied coordinates. Every
supported delegation purpose uses GATEWAY_PROBE_SIGNING_KEY as the custody intent
here; the purpose remains a separate grant field. prepare requests commit of its
read transaction but writes no generation-request or issued-grant record.
Actor identity/scopes are supplied by the caller, not credential authentication.

from_provider_result reads selected structural attributes and constructs bounded
evidence: reference, purpose, issuer, correlation, provider version, public key and
replay flag. Workspace is copied from the grant. Extra provider fields are not
copied; AttributeError/TypeError/ValueError become a generic conflict with a
chained cause. This is not an authenticated provider receipt or a universal safe
error projection. _match later requires workspace/reference/purpose/issuer/
correlation equality with the grant; it does not establish private/public key
correspondence. The selected Core
[public-key value](../../../../../control-plane-kit-core/src/control_plane_kit_core/delegation_keys.py)
checks bounded ASCII PEM framing and fingerprints normalized text, without
cryptographic key parsing.

admit_generated separately requires DELEGATION_KEY_REGISTER. It builds an active
custody receipt, a registered reference containing custody/version metadata, and
a verify-only [signing-key candidate](delegation_signing_keys.py.md). The reference
is attributed to the grant's actor, while signing-key admission uses admitted_by;
the two strings need not match. Inside one unit of work it takes the delegation
purpose lifecycle lock, reselects the active provider and compares endpoint and
credential references with the grant, then registers the reference and key.
Both registrations commit together or roll back together on failure.

The selected provider lookup is the non-FOR-UPDATE variant. The key-purpose lock
does not itself lock the provider row, so this recheck must not be described as
locking all provider authority through commit. The service also does not rerun
the complete prepare-time prefix/intent admission helper at this point. Grant
objects are supplied back by the caller rather than loaded from a durable
issuance ledger.

The [reference store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/secret_provider_store.py)
owns admission identity/replay, including custody version metadata; changed
admission requires explicit supersession. The
[key store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/delegation_signing_key_store.py)
rejects changed identity under an existing key ID. Returned replayed is provider
evidence, not inferred from whether database inserts occurred. A failed durable
fold does not undo provider custody or authorize regenerating/retrying it. No
operation-action/event ledger or provider compensation is added by this owner.

Values carry references, public metadata and bounded identifiers rather than
private key fields. requested_at receives only a short-text check; persisted
admission timestamps receive stricter canonical UTC validation in the stores.
[PostgresUnitOfWork](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
commits on successful exit after the service's request and otherwise rolls back.
Errors and arbitrary metadata are not globally scrubbed for secret-like values.

The [tests](../../tests/test_delegation_key_generation.py.md) cover focused scopes,
two-store rollback, matching evidence and replay with synthetic provider output.
Read depth: full 406-line owner and 328-line tests; full signing-key store/Core
key value/unit of work, selected custody/reference/store/schema contracts from
the same checkout. No live keys, credentials, database or runtime were accessed for
this documentation review; no executable validation ran.
