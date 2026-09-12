Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_overlap.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_overlap.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner specializes desired verifier-material publication for the overlap
phase of a gateway key rotation. PublishGatewayKeyRotationOverlapProjection names
the rotation, session, actor, expected rotation version, authored graph, current
and desired realized projections, desired revision, caller scopes and idempotency
key. It validates nonempty text, exact positive/nonnegative integers, a typed
scope tuple and IdempotencyKey. Text checks here do not impose identifier length
or control-character limits. The result pairs the rotation ID with generic
desired-publication evidence and has no additional local constructor validation.

execute requires DELEGATION_KEY_ROTATE before opening a UoW. It obtains an injected
timestamp, prepares publication serialization, builds the phase-specific command,
publishes it with an injected action ID, then requests commit. Both factories are
called on replay too. Successful UoW exit commits before the result reaches the
caller; exceptions roll back. The clock runs after entering the UoW and outside
the local exception-normalization try block. This is not the rotation service's
pre-UoW canonical timestamp admission path.

The consequential implementation is in the shared
[projection builder](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_projection.py)
and [desired-publication owner](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/desired_realized_projections.py).
Preparation takes a session/idempotency transaction advisory lock and, for new
work, locks and requires an open session. The builder locates the rotation, locks
its workspace then rotation, and requires key-generated at the exact expected
version with a replacement key ID. Current and desired authored IDs must both
equal the expected authored graph; current/desired projection IDs must match the
expected values and each other, with the expected desired revision. Authored and
current projection records must belong to that workspace and source lineage.

Derivation decodes those graph records and reconstructs the verifier projections
for every authored delegation binding. Each must be covered by matching current
node/purpose/issuer material, and the current graph must contain no unbound
verifier nodes. The target binding must match rotation node, purpose and issuer.
It loads old and new signing-key records and requires the replacement's private
reference to equal the rotation's generated reference. The active/verify-only
key collection for workspace/purpose/issuer must contain exactly these two IDs.
The target audience is `gateway:<workspace>:<node>`; the current target must match
that audience and issuer, contain only the old public key, and equal its stored
public material. Old must be active and new verify-only.

These signing-key reads use ordinary get/list_for_verification selectors, without
row or purpose advisory locks in this path. Workspace and rotation locks do not
themselves stabilize independent key lifecycle writers. The checks compare
persisted key/reference identity, not provider custody, active provider admission,
private/public cryptographic pairing or live gateway observations. The public
derivation helper takes supplied stores/rotation/graphs and delegates this phase
derivation; it does not add scope, session, rotation-status/version, workspace
pointer validation or transaction ownership from execute.

The target verifier contains the two stored public keys; Core sorts keys by
key_id. Other authored bindings retain their current verifier projections.
[Core materialization](../../../../../control-plane-kit-core/src/control_plane_kit_core/delegation_authority.py)
requires exact unique binding coverage, matching issuers and no conflicting
reserved verifier environment on authored nodes. It starts from the authored
graph and returns updated node values. This preserves other verifier material,
not arbitrary unrelated edits that exist only in the current realized graph.
It neither edits the authored record nor mutates an external runtime.

The builder assigns projection ID `gateway-rotation-<rotation>-overlap`, projection
key `gateway-rotation:<rotation>:overlap` and a phase-specific verifier ID. The
realized record factory computes its digest from canonical graph material and
lineage. Generic publication rechecks session/workspace ownership and desired
lineage, saves the projection, CAS-updates only desired_realized_projection_id
while incrementing desired_graph_revision, then appends one session action.
The action records publication lineage/digest/revision and source rotation ID/
version with an intent fingerprint. Rotation status, current pointer, authored
graph and signing-key status do not change in this publication operation.

The [graph store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/graph_store.py)
reuses a projection by workspace/source/kind/key when its digest matches, keeping
the first row's identity/attribution; changed material conflicts. The candidate's
deterministic projection ID is not itself the store's only equivalence key.
Projection insert, desired-pointer CAS and action insert share one transaction.
A late action failure therefore rolls them back together, as the tests assert.
No provider rollback is needed for this local publication, and this module
implements no external compensation or deletion of retained history.

Replay is receipt-based. Preparation finds the action before requiring an open
session. The builder checks requested authored/current/desired lineage and prior
revision against its payload, loads the retained projection and rebuilds the
generic command. It does not reread rotation state, current workspace pointers or
keys. Generic replay checks action kind and the canonical command fingerprint,
then the retained projection digest. That fingerprint includes actor, session,
workspace, projection identity/digest/kind/key, source rotation/version, expected
lineage and idempotency key; created_at, generated action ID and scopes are
excluded. Changed rotation version or actor conflicts. Matching replay returns
the original action and published revision even if later operations have moved
the workspace on; it does not republish or establish present readiness.

There is a different-ID reuse caveat: generic publication fingerprints the
candidate before save, including its projection ID, but the action payload uses
the row returned by save. If an equivalent stored projection has a different ID,
the first publication can commit using that row. Rotation replay reconstructs
the command from the returned ID, yielding a different fingerprint and a
conflict despite an unchanged caller command. This follows from the inspected
source; the projection tests do not exercise that equivalent-row/different-ID
case. Same-material storage reuse does not guarantee replay through this wrapper.

The wrapper converts selected delegation, signing-key, publication, rotation,
graph, lookup and value failures to its conflict type using str(error) and an
explicit cause. It is not a blanket bounded/redacted error boundary; database
uniqueness failures and factory failures can propagate. Private-key bytes are
never resolved here. Public verifier PEM is intentionally present in realized
graph material, while new private-key references remain inputs to local identity
checks. Outer interfaces must authenticate scope/actor claims and authorize
access to returned operational evidence.

Publication means committed desired material plus an action. This owner creates
no execution admission, lease, run, deployment checkpoint or acceptance event;
it does not activate the replacement, wait for grants or deploy a gateway.
Those sibling owners require their own review and evidence. No new listener or
runtime/provider mutation surface is introduced by this documentation change.

Read depth: full 216-line owner, full 344-line shared projection builder and
384-line desired-publication owner, full
[539-line projection tests](../../tests/test_gateway_key_rotation_overlap_projection.py.md),
and retained full [620-line shared fixture](../../tests/gateway_rotation_overlap_fixture.py.md).
Selected actual Core materializer/projection and realized-record constructors,
graph/publication stores and signing-key selectors were inspected; signing-key,
rotation, UoW and temporal contracts had prior full reads. No executable tests,
database, credential, provider or runtime actions were performed for these notes.
