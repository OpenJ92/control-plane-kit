Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_retirement.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_retirement.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner publishes the desired B-only verifier projection after a rotation's
stored grant-drain deadline. It specializes the shared projection/publication
machinery for RETIREMENT. It does not deploy B-only material, update the current
projection, retire a signing-key row, revoke provider bytes or complete the
rotation. Its durable output is desired projection/revision plus an operation
action. Consumers must separately plan, admit, execute and accept that material.

PublishGatewayKeyRotationRetirementProjection contains rotation, session, actor,
expected rotation version, authored/current/desired projection lineage, desired
revision, scopes and IdempotencyKey. It checks nonempty text, exact positive/
nonnegative integers and typed tuple/key values. Text checks here do not impose
bounded identifier grammar, and the command itself does not require current and
desired projection IDs to agree. New-publication builder validation imposes that
settled-lineage condition. The result merely pairs rotation ID and generic
publication result; it adds no local congruence or commit-proof validation.

execute requires DELEGATION_KEY_ROTATE before opening a UoW. It samples textual
created_at, prepares session/idempotency serialization, calls the shared builder
with RETIREMENT and a sampled trusted epoch, publishes with an injected action
ID, then requests commit. Successful UoW exit commits before returning. Textual
clock evaluation happens inside the UoW but outside the local try block; trusted
epoch and action-ID evaluation occur inside it. On a successful replay path all
three factories are still invoked, even where retained receipts supply the result.

The full [shared builder](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_projection.py)
handles both phases. For new retirement publication it locks the workspace and
rotation, requires draining-old-grants at the exact expected version with a new
key ID and a persisted deadline, and requires an exact-integer trusted epoch at
or beyond that deadline. It does not enumerate issued grants or observe consumer
drain. Current and desired authored pointers must both match the expected graph,
current and desired realized pointers must match expected values and each other,
and desired revision must match. The graph records must belong to the rotation's
workspace and authored lineage.

Derivation reconstructs all current verifier projections for the exact authored
binding set, rejects unbound material, and requires the target binding, issuer
and gateway workspace/node audience to agree. It reads old/new signing-key rows
and requires exactly those two keys in the active/verify-only collection for
workspace/purpose/issuer. The new key's private reference must equal the rotation
reference. For retirement, A must be verify-only and B active, with current A+B
public material equal to those stored key records. It replaces only the target
verifier with B and materializes all projections onto the authored graph.
Unrelated verifier projections are retained; arbitrary other realized-only edits
are not copied into the new authored-based graph.

There is a confirmed source-level key-order limitation: Core
[DelegationVerifierProjection](../../../../../control-plane-kit-core/src/control_plane_kit_core/delegation_authority.py)
sorts public_keys by key_id, but the retirement builder compares the current tuple
to (old_key_id, new_key_id) and (old_public_key, new_public_key) without sorting
those expected tuples. Old key-z / new key-a can therefore form valid sorted
overlap material yet fail retirement's ordered comparison. The fixture's
key-a / key-b naming does not expose this case. This is a source-confirmed
limitation, not an executed test result or a source fix in this documentation.

The key selectors are non-locking get/list reads, distinct from the workspace/
rotation locks. They do not stabilize concurrent key lifecycle changes through
commit. Reference equality is not provider admission, generated-version proof,
private/public cryptographic pairing or live signer/gateway observation. The
materializer enforces Core binding/issuer and reserved-environment consistency;
the realized record factory validates canonical material and computes its digest.

Candidate identity is `gateway-rotation-<rotation>-retirement`, with projection
key `gateway-rotation:<rotation>:retirement` and a phase-specific verifier ID.
The full [publication owner](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/desired_realized_projections.py)
serializes session/idempotency, checks open session/workspace and desired lineage
for new work, saves the projection, CAS-updates desired_realized_projection_id
and increments desired_graph_revision, then appends the publication action with
lineage/digest/source rotation/version. These writes share one UoW; failure rolls
back that publication transaction. It does not change rotation status, current
graph, authored graph or either key's lifecycle, and creates no execution run.

Replay reads the action before requiring an open session. The builder compares
requested previous lineage/revision with action evidence and loads the recorded
projection; it does not reread rotation/key/current workspace truth or validate
the newly sampled epoch against a deadline. Generic replay checks action kind,
command fingerprint and retained projection digest. The fingerprint includes
actor, expected lineage, source rotation/version, projection ID/digest/kind/key
and idempotency key; clocks, generated action ID and scopes are excluded.
Matching replay returns recorded publication evidence, not present readiness.

The conditional different-ID replay caveat also applies here. If equivalent
phase material already exists under a different physical projection ID, the
[graph store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/graph_store.py)
can return that row by semantic identity/digest. Generic publication fingerprints
the candidate ID before save but records the returned ID in its action. The
retirement builder reconstructs replay from that returned ID, so the fingerprint
can conflict despite an unchanged caller command. The prerequisite is a
preexisting equivalent row with a different ID; its provenance through a routine
public workflow is not established. This is source reasoning, not a reproduced
provider/test result. Retirement child admission separately requires the
canonical phase ID, so storage reuse must not be described as universally safe
end-to-end publication/admission replay.

Selected delegation, key, publication, rotation, graph, lookup and value errors
are translated to retirement conflict using their original message and explicit
cause. That is not a blanket bounded/redacted error boundary; database and other
factory errors can propagate. No private key/credential is resolved here. Public
verifier PEM is intentionally part of realized graph material, while operational
references and returned history require outer access controls. The module has
no network listener or provider compensation/history cleanup behavior.

Read depth: full 183-line owner and
[366-line retirement fixture](../../tests/gateway_rotation_retirement_fixture.py.md),
retaining full shared builder344/publication384, actual Core key/materializer,
rotation/signing-key/store and reviewed activation/overlap context. Consequential
retirement preparation/execution construction was selectively read, not their
complete owners/tests. No separate retirement projection-test file exists in
this checkout; program/execution tests remain a later review group. No tests,
source/pins, keys, credentials, database/provider/runtime actions or merges were
performed for these notes; the documentation adds no security/mutation surface.
