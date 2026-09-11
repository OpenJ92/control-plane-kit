Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_completion_program.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_completion_program.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This program completes a rotation after accepted B-only deployment: retire the
old public signing-key record, persist exact provider-version revocation intent,
invoke an injected adapter, then mark the local key revoked and rotation completed
after a matching receipt. These are separate durable and external boundaries.
The [retirement execution program](gateway_key_rotation_retirement_execution.py.md)
stops at RETIREMENT_READY; completion owns the later OLD_KEY_RETIRED,
REVOCATION_PREPARED and COMPLETED transitions, with BLOCKED for selected outcomes.

CompleteGatewayKeyRotation names rotation, expected retirement-ready version,
actor and scopes. IDs use a bounded 1..200-character ASCII identifier grammar;
the expected version is an exact positive integer and scopes must be a nonempty
tuple of PolicyScope values. Scopes are not normalized or deduplicated here.
Every progress call, including completed/blocked replay, requires all four of
DELEGATION_KEY_ROTATE, DELEGATION_KEY_RETIRE, DELEGATION_KEY_REVOKE and
SECRET_PROVIDER_REVOKE before reading the rotation. Actor/scopes still require
authentication at the outer interface.

This is not a new approval-request workflow for the exact revocation grant. It
relies on the rotation's prior approved progression and focused caller scopes;
it does not create or reread a separate exact-version approval decision. The
[rotation service](gateway_key_rotations.py.md) validates approval records on its
earlier approval transitions, not on these completion transitions. No execution
worker lease/fence, ordinary activity admission or coordinator is used here.

Preparation requires the exact expected version at RETIREMENT_READY, plus one
at OLD_KEY_RETIRED and plus two at REVOCATION_PREPARED. Retirement truth means an
accepted retirement checkpoint with non-null accepted graph/projection IDs,
current workspace pointers equal to those IDs, and the active key ID equal to
the replacement. It does not decode the current graph to establish B-only content,
reload advancement action/event or approval history, compare desired pointers,
or observe a gateway. Workspace/key checks use ordinary reads in separate UoWs,
not a lock held through local retirement or provider dispatch.

Before local retirement, derivation loads the old key's private reference and
requires an active reference registration plus an active provider registration
in the rotation workspace. The reference must allow GATEWAY_PROBE_SIGNING_KEY.
Its metadata must contain an identifier-shaped provider_version_id and exact
positive integer provider_version_number. The selected
[provider/reference selectors](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/secret_provider_store.py)
are non-locking. This method does not freshly validate provider prefix/intent
policy through a separate admission service or query the provider for version
existence. Exactness is derived from admitted durable metadata at that read.

Revocation correlation is `<rotation-correlation>:revoke-old-version`. Canonical
JSON SHA256 binds rotation/workspace, provider registration, endpoint and
credential references, old secret reference, version ID/number, actor and
correlation. The revocation ID is `srevoke_<digest>`. The
[Core grant](../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py)
carries those reference-only coordinates, fingerprint and operation ID; session,
run, activity and effect IDs remain unset. The stored checkpoint keeps provider,
secret/version, revocation/correlation IDs, digest and prepared time. Endpoint,
credential and actor are bound through the digest rather than separate checkpoint
fields. No private key or provider credential bytes are resolved by this owner.

At RETIREMENT_READY, the old key must be VERIFY_ONLY or already RETIRED. The
[key service](delegation_signing_keys.py.md) commits VERIFY_ONLY to RETIRED, then a
separate rotation transition records OLD_KEY_RETIRED using the persisted key
retirement time. The key store locks purpose/issuer scope and row, admits only
VERIFY_ONLY for fresh retirement and retains the original result on RETIRED
replay. Rotation advances use fixed rotation-derived transition IDs, locked
status/version CAS and transition history; they are not atomic with key changes.

At OLD_KEY_RETIRED, the program derives current retirement/custody truth again
and stores the revocation checkpoint through prepare-revocation. Before dispatch
it derives once more, preserving checkpoint.prepared_at, and requires exact
checkpoint equality. Drift in bound custody/version/actor semantics can therefore
reject a retry. This is not serialization against concurrent callers: reads and
provider IO remain separate, with no claim or dispatch-attempt lock. Stable
correlation/fingerprint requires a replay-safe adapter/provider implementation.

GatewayKeyRotationRevocationAction pairs the checkpoint with the typed grant,
checks their revocation/provider/reference/version/correlation/digest coordinates
and the prepared-version offset. Its constructor does not independently bind
every grant field back to a live rotation or recompute the digest, and the
prepared-version offset check is equality rather than a separate exact-int check.
Normal derivation supplies those values; directly constructing an action is not
an authorization or evidence lookup.

The adapter's revoke_version call occurs directly outside a UoW. This owner has
no local effect-attempt journal, command receipt, observation/reconciliation
service or saved provider receipt. It persists prepared intent before IO, but
not a distinct attempted dispatch or each definite-failure result. A typed
DEFINITE_FAILURE returns RETRYABLE with the action and freshly read rotation;
normally it remains REVOCATION_PREPARED and the old key RETIRED. Another progress
call can invoke the adapter again after rederivation. The program trusts the
adapter's classification that mutation definitely did not occur.

A typed UNCERTAIN result blocks with its identifier-shaped failure code; a
non-result object blocks with revocation-malformed-result; a revoked receipt
whose identity does not match the grant blocks with revocation-receipt-mismatch.
Blocking first rechecks prepared rotation version/checkpoint and current
retirement truth, then records a revocation-uncertain transition. Thus concurrent
truth drift can cause a conflict rather than a successfully persisted block.
Returned uncertainty leaves the local key retired and does not declare the old
secret revoked. It does not undo any provider mutation that may have occurred.

Thrown adapter exceptions, including ordinary Exception and BaseException, are
not caught or converted to typed uncertainty. They propagate with prepared intent
retained, so another call may dispatch the same grant again. This differs from
the returned UNCERTAIN path that records BLOCKED. Safety after an unacknowledged
provider success depends on external idempotency/correlation semantics, not
exactly-once execution supplied by Operations. No concrete provider adapter is
implemented or validated by this file.

A matching Core receipt compares revocation ID, provider registration, secret
reference and version ID/number; receipt construction fixes status to REVOKED.
This is a typed identity match, not cryptographic or independently observed proof.
Success rechecks the prepared action/current retirement truth, reads the old key,
and locally changes RETIRED to REVOKED (or accepts an already REVOKED key), then
separately advances the rotation to COMPLETED with a supplied revocation time.
The key store's generic revoke can revoke other existing statuses; this program's
earlier status read is its narrower guard and is not held through that write.
Concurrent lifecycle drift is not globally excluded by these separate operations.

After provider success, a stale action or changed workspace/active key can reject
folding while provider state has already changed. A crash can also leave the key
REVOKED but rotation REVOCATION_PREPARED. There is no provider compensation,
reference-registration revocation, deletion of history or general repair loop
here. Re-entry uses current metadata, exact prepared intent and adapter replay;
metadata becoming inactive can prevent rederivation even after provider success.

COMPLETED replay requires exactly retirement_ready_version+3 and reconstructs a
receipt from the stored revocation checkpoint. It does not load an original
provider receipt, revalidate current/key/provider truth or call the adapter.
BLOCKED replay returns the stored code without an expected-version or
completion-checkpoint check; a rotation blocked at another phase can take this
path. Both still require the four scopes. These terminal classifications are
not evidence of current provider health or automatic uncertainty recovery.

Effect-result construction requires a typed revoked receipt exactly for REVOKED
and a bounded identifier failure code for other outcomes. It does not force an
arbitrary non-receipt value to None on failure. Completion-result construction
checks rotation/outcome, completed status and typed receipt presence, and required
action/code presence for retryable or blocked results; it does not validate every
action/code type, length or cross-object identity. Neither dataclass is a blanket
redaction boundary. The clock helper checks only nonempty text of at most 128
characters; imported transition/store timestamp validators impose canonical UTC
requirements. Independently sampled times and transition fingerprints can cause
conflicts for changed or concurrent retries.

Durable history consists of local key lifecycle timestamps/status, rotation
revocation checkpoint, transition fingerprints and completion/block fields.
The provider receipt returned to the caller is not stored by this program.
The public rotation read model omits references/detailed checkpoints, while
full action/grant/result values contain operational and secret-reference IDs.
Those values require access controls even though they contain no secret bytes.
Selected lookup/fold/block conflicts retain explicit exception causes; adapter
and other imported failures can escape, so this is not universal bounded logging.

Read depth: full 674-line owner and
[414-line tests](../../tests/test_gateway_key_rotation_completion_program.py.md),
retaining full rotation/key/provider/retirement contexts. Actual Core revocation
grant/receipt, key retire/revoke and PostgreSQL locks, provider active selectors,
rotation approval/transition/timestamp and revocation persistence paths were
inspected. The tests use a simulated provider and do not prove concurrency,
real provider idempotency or every post-success durable prefix. No executable
validation, source change, key/credential, database/provider/runtime action was
performed for this documentation; it adds no security surface.
