Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_projection.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_projection.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This shared owner derives verifier material for overlap A+B and retirement B,
then constructs a generic PublishDesiredRealizedProjection command. The
[overlap](gateway_key_rotation_overlap.py.md) and
[retirement](gateway_key_rotation_retirement.py.md) publication services own
typed caller commands, scope admission, clocks, publication serialization,
transaction commit and selected error translation. The builder takes an existing
unit of work and duck-typed command; it neither opens/commits that UoW nor invokes
publication itself. Its other public function derives a graph from supplied
stores, rotation, authored graph and current graph. These entry points have
different validation depth and should not be treated as interchangeable services.

For new publication the builder locates the rotation, locks its workspace, then
locks the rotation and verifies their linkage. Overlap requires KEY_GENERATED;
retirement requires DRAINING_OLD_GRANTS. Both require the exact expected rotation
version and a replacement key ID. Retirement additionally requires a stored
drain deadline and an exact-integer trusted_epoch at or beyond it. This checks
stored time policy, not live outstanding grants or consumer drain. Overlap has
no corresponding trusted-epoch condition.

Current and desired authored pointers must both equal the expected authored ID;
current and desired realized pointers must equal their expected values and each
other; desired revision must match. Authored and current realized records must
belong to the rotation workspace, and the realized record must name that authored
source. Their descriptors are decoded through DEFAULT_GRAPH_CODEC before graph
derivation. These are new-publication checks, not the replay path's guarantees.

Derivation gathers a current verifier projection for every authored delegation
binding, requiring exact binding identity and issuer. The set of current nodes
carrying verifier material must equal the authored delegate-node set, excluding
unbound verifier material. The target binding must match the rotation's gateway
node, purpose and issuer. It loads old/new key records in workspace/purpose/issuer
scope, requires the new private reference to equal the rotation's generated
reference, and requires exactly those two IDs in list_for_verification. Target
issuer and audience must match `gateway:<workspace>:<gateway-node>`.

Overlap requires old ACTIVE, new VERIFY_ONLY and current target containing exactly
the old stored public key; the replacement contains both stored public keys.
Retirement requires old VERIFY_ONLY, new ACTIVE and current target exactly equal
to the old/new public material tuple; the replacement contains only new. Other
authored bindings retain their current verifier projections. The target verifier
ID is `gateway-rotation-<rotation>-<phase>-verifier`. Projections are ordered by
node/purpose before Core materialization onto the authored graph.

[Core materialization](../../../../../control-plane-kit-core/src/control_plane_kit_core/delegation_authority.py)
enforces exact unique binding coverage, issuer consistency and reserved verifier
environment rules, returning updated graph values. It starts from authored truth:
retaining other verifier projections does not preserve arbitrary unrelated
realized-only edits. The authored record is not rewritten and no external gateway
is changed by graph derivation.

Core sorts public_keys by key_id, but retirement compares current keys to unsorted
role tuples (old_key_id, new_key_id) and (old_public_key, new_public_key). An old
key-z / new key-a pair can produce valid sorted overlap material yet fail
retirement. The reviewed fixtures' key-a / key-b order masks this source-confirmed
limitation. It is not a reproduced test result or a fix made by these notes.

The direct derive function does not add builder checks for rotation status/version,
deadline, workspace pointers, graph-record ownership or session/actor authority.
It also owns no transaction. Key get/list selectors are non-locking; workspace
and rotation locks in the builder do not stabilize separate key lifecycle writers.
Private-reference equality establishes recorded identity, not active provider
admission, custody/version proof or private/public cryptographic pairing. Neither
entry point resolves private bytes or observes runtime signer/gateway readiness.

New record identity is `gateway-rotation-<rotation>-<phase>`, kind
DELEGATION_VERIFIER and semantic key `gateway-rotation:<rotation>:<phase>`.
RealizedGraphProjectionRecord.from_graph derives canonical material/digest with
workspace/authored lineage and supplied actor/time. The returned publication
command binds that record, previous realized projection/revision and source
rotation ID/version. Building it alone saves no projection, pointer or action.

The wrappers pass it to the
[desired-publication owner](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/desired_realized_projections.py),
which serializes session/idempotency, checks new session/workspace/desired truth,
saves the projection, CAS-updates the desired realized pointer and revision, and
records publication lineage/digest/source in a session action within one UoW.
Current, authored graph, rotation and key lifecycle are unchanged. Projection,
pointer and action writes roll back together on transaction failure; this does
not roll back an earlier session committed by a preparation program.

Builder replay first finds the session/idempotency action and checks its recorded
projection ID and exact-integer revision. Requested authored/current/desired
lineage must match recorded authored/previous projection and revision minus one.
It loads the recorded projection and rebuilds a generic command using caller
actor/rotation/version. It does not reread rotation, key or current workspace
truth, check the retirement deadline, or independently admit the action kind and
full fingerprint. Generic publication supplies those latter action/fingerprint
checks plus retained projection-digest validation. Successful replay returns
retained evidence rather than proving current readiness or publishing again.

The [graph store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/graph_store.py)
can reuse an equivalent row by workspace/authored source/kind/key and digest,
retaining its original physical ID. Conditional caveat: if that row predates this
publication under a different physical ID, generic publication fingerprints the
candidate ID before save but records the returned ID in the action. This builder
reconstructs replay from the returned ID, so unchanged caller intent can conflict
against the original fingerprint. The prerequisite is a preexisting equivalent
phase row with a different ID; routine public-workflow provenance is unestablished.
This is source reasoning, not an executed case. Child admission's separate
canonical phase-ID requirement can also reject after publication/planning have
committed; semantic storage reuse is not an end-to-end replay guarantee.

GatewayKeyRotationProjectionConflict covers explicit phase/lineage/key failures;
lookup, codec, Core and record-constructor errors can also propagate. The helper
does not normalize or redact every failure. Public verifier PEM intentionally
appears in graph material; outer interfaces must authenticate scope/actor claims
and control access to operational references/history. No listener, private-key
resolution, provider mutation, compensation or history deletion is owned here.

Evidence is phase-specific. The reviewed
[overlap projection tests](../../tests/test_gateway_key_rotation_overlap_projection.py.md)
exercise material/publication/replay and transactional failures. Reviewed
[retirement preparation tests](../../tests/test_gateway_key_rotation_retirement_program.py.md)
exercise B-only material, unrelated verifier retention, deadline boundary and
extra-key rejection through the composed program. These do not grant generic
validation coverage to every direct builder/derive invocation, key ordering,
equivalent-row reuse or concurrent key change. Deployment acceptance and later
key/secret cleanup require separate execution and lifecycle evidence.

Read depth: full 344-line owner with retained full publication384, overlap216,
retirement183, preparation programs/tests and fixture/rotation contexts. Actual
Core sorting/materialization, realized record construction, graph-store reuse,
generic fingerprint/save/action/replay and key selectors were inspected in
selected reads. No executable validation, source change, credential, database,
provider or runtime action was performed for this documentation. It adds no
security or mutation surface.
