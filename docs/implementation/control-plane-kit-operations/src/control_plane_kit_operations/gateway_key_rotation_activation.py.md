Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_activation.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_activation.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This program moves accepted overlap from active old signer A / verify-only B to
verify-only A / active B, records rotation activation and draining transitions,
then reports waiting or ready-for-retirement against a persisted deadline. Key
activation here changes Operations signing-key selection state; it does not
generate a private key, contact a signer/provider, redeploy the verifier graph
or revoke the old key. The purpose is to retain A for verification while allowing
the bounded lifetime of previously issued grants to pass before retirement.

ProgressGatewayKeyRotationActivation contains rotation ID, expected overlap
version, actor ID and actor scopes. Identifiers use the bounded local
200-character grammar, the version is an exact positive integer, and scopes are
a typed tuple normalized by deduplication/sorting. progress requires both
DELEGATION_KEY_ROTATE and DELEGATION_KEY_ACTIVATE before opening its snapshot
UoW, including later waiting/ready calls. It takes no worker lease fence or
idempotency key. Outer interfaces authenticate these supplied actor/scope values.

The snapshot reads rotation, workspace, old/new key records, the active replacement
secret-reference registration and rotation transitions. The reference must allow
GATEWAY_PROBE_SIGNING_KEY; this intent is used even when the rotation has another
supported delegation purpose. The snapshot requires an accepted overlap checkpoint
whose accepted graph/projection equal its desired pair and the workspace current
pair. It requests commit and closes before any activation mutation. These are
ordinary non-locking selectors, not one locked snapshot spanning key activation
and rotation updates.

Accepted-overlap checking compares stored checkpoint and current coordinates.
It does not reload the advancement action/event, graph descriptor or public key
set, compare today's desired pointer/revision, or observe gateway health. The
replacement key's private reference must equal the rotation's reference. Neither
snapshot nor the selected key activation service queries the pinned provider's
active status, resolves the reference or verifies its bytes against the generated
provider version/public key. Active reference admission is not proof of active
provider custody. Reference/workspace/key truth can change between these separate
transactions; this owner does not lock those other writers out through the fold.

Lineage uses deterministic transition IDs `gkrot-activation:<rotation-hash>:` plus
new-key-active or draining. At overlap-ready it requires the exact requested
version and neither transition ID. At new-key-active it requires version+1 and
only the first ID; at draining-old-grants it requires version+2 and both IDs.
The latter states also require A verify-only and B active. It checks transition
ID membership, not each retained transition's full fingerprint, actor/time or
from/to evidence. Other rotation phases conflict rather than becoming an
already-advanced activation result.

At overlap-ready, exact active-A/verify-B calls the
[signing-key activation service](delegation_signing_keys.py.md). It independently
requires activate scope and rereads the key's active reference/signing intent.
The [key store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/delegation_signing_key_store.py)
validates canonical activation time before its SQL, takes workspace/purpose then
issuer advisory locks and locks the candidate row. An already-active candidate
returns its original record; otherwise it must be verify-only. The store demotes
any active key in the issuer scope to verify-only and activates B with actor/time
in the same transaction. It does not validate that the previously active key is
still exactly snapshot A or count every verify-only key. The key service commits
this mutation independently of the rotation folds.

If snapshot A is already verify-only and B active while the rotation is still
overlap-ready, the program treats this as activation committed before its fold.
It reuses B's recorded activated_at and requires that timestamp to exist; it does
not reactivate or overwrite key attribution. This path does not establish which
earlier caller performed activation. Any other role pair conflicts. Later
new-key-active/draining snapshots require the same final role pair and active
replacement reference again; replay is conditional on current prerequisites.

The program next calls the ordinary
[rotation writer](gateway_key_rotations.py.md) to record new-key-active using
B's activation timestamp, a newly sampled advanced_at and the fixed stage ID.
That writer takes its row lock, validates canonical command times and computes
the deadline from its trusted epoch now plus maximum grant lifetime and skew.
The deadline is set at the rotation fold, not by parsing B's earlier activated_at.
After a delayed recovery it can therefore be later than activation epoch plus
the allowance. Once retained, replay does not reset the deadline. A second
rotation transaction records draining-old-grants with the other fixed ID.

There can be four successful commits on initial progress: snapshot read, key
activation, new-key-active fold and draining fold. Failure after any mutation
does not roll back earlier commits. The program has no compensating key switch,
provider rollback, automatic retry loop or cleanup. Key lifecycle columns retain
activation attribution; rotation transitions retain the two progress records.
It creates no new activity run, session/action, provider receipt or deployment
acceptance event for these mutations.

Already-draining calls validate fresh prerequisites/lineage, then compare a
new trusted epoch observation with the stored deadline. Both must be nonnegative
exact integers. Before deadline returns waiting; equality and later return
ready-for-retirement. The result remains a draining rotation. It does not sleep,
scan issued grants, enforce a monotonic clock, start retirement or prove every
consumer stopped issuing old-key grants. The rotation writer separately checks
its deadline when a retirement deployment is requested.

GatewayKeyRotationActivationResult requires a draining rotation, typed outcome
and nonnegative exact-integer observed/deadline values with the appropriate
comparison. Its bare constructor does not compare the supplied result deadline
with rotation.drain_deadline_epoch; progress passes the actual retained value.
The trusted epoch is sampled for rotation transitions and again for the final
result, so malformed final clock output can fail after earlier commits. Textual
event time and epoch time are independent injected contracts.

Deterministic stage IDs do not guarantee every concurrent invocation succeeds.
The ordinary rotation writer compares an exact command fingerprint on replay,
including actor, advanced_at and activation evidence. The
[concurrency test](../../tests/test_gateway_key_rotation_activation.py.md) uses
the same actor and constant textual time for both calls. Concurrent callers
that reach the same stage from an earlier snapshot with different times/actors
can conflict rather than replay. A later fresh call that sees valid draining
truth can return the existing deadline without issuing either stage command.

The fixed missing-authority error is distinct from conflicts. Selected snapshot
lookup failures become a fixed prerequisite message with the original cause;
key and rotation failures retain str(error) and explicit causes. Database,
malformed-time and unrelated errors are not all normalized by this owner.
Names describing bounded errors do not guarantee every imported message/context
is redacted. No private-key or provider credential bytes are accessed, though
full rotation/key records contain opaque references and operational identities
that outer interfaces must expose appropriately.

Read depth: full 427-line owner and 364-line tests; retained full rotation,
signing-key/store, secret-provider/reference and shared fixture/overlap contexts.
Actual key activation service/store locking, reference selector and rotation
deadline/replay paths were rechecked. This documents local durable state and
inspected test laws, not live signing/grant-drain proof. No tests, source/pins,
credentials, key/provider/database/runtime actions or merges were performed for
these notes; this documentation introduces no security or mutation surface.
