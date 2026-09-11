Source: [control-plane-kit-operations/src/control_plane_kit_operations/execution_leases.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_leases.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

ExecutionLeaseFence is a frozen two-field value: worker_id and generation. It
names the claimed worker and exact generation that a consumer must compare with
durable execution-request truth. It is not itself a claim, lease clock, scope set,
authenticated credential or request identity. Constructing a matching-looking
value performs no authorization or database lookup. The module owns this nominal
value and InvalidExecutionLeaseFence, using only dataclasses as an import.

worker_id must be a nonempty str of at most 512 characters with no character
whose ordinal is below 32. This is a character-count/control check, not an ASCII
identifier grammar, byte limit or whitespace normalization; printable spaces and
non-ASCII text are not categorically excluded. generation must have exact type
int, excluding bool, and lie in 1..2**63-1. Invalid input raises a fixed message
without interpolating its value. No generation increment, expiry comparison,
renewal or takeover occurs in this module.

descriptor returns only worker_id and generation. Neither field is hidden from
the value's default repr or descriptor, so the fence must not be treated as a
redacted public projection. It contains no secret bytes but does contain worker
identity and operational generation. Interface authentication and exposure controls
belong to consumers. A fence is scoped by the request/run context supplied
alongside it; the same pair is not a globally unique capability.

The actual [ClaimIdentity](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
owns worker, generation, claimed_at and lease_expires_at and constructs a fence
through its property. It does not duplicate generation in another durable fence
record. ExecutionLeaseDuration lives in
[lifecycle](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py)
and describes a requested 1..3600-second lifetime; it is distinct from this
worker/generation value. Claim command descriptors send duration, while the
PostgreSQL claim operation computes claimed/expiry timestamps from database time.

Selected lifecycle command constructors require a typed fence and agreement with
the supplied worker authority. The actual ownership guard additionally requires
CLAIMED request state, a stored claim, exact claim.fence equality and matching
authority worker ID; execution scope is checked separately. That guard does not
independently test expiry. Database-observed expiry belongs to selected effect
start/reconciliation/fold paths described in the reviewed
[shared rotation execution owner](gateway_key_rotation_deployment_execution.py.md).
Fence equality and fresh lease authority are not interchangeable claims.

Nominal strictness also belongs to consumers. Lifecycle's selected command check
uses isinstance, while
[GatewayKeyRotationDeploymentHandoff](gateway_key_rotations.py.md) requires the
exact ExecutionLeaseFence type and suppresses its fence field in handoff repr.
Its fenced writer locks current request/run/rotation truth and validates the
current claim before transition replay. Those are consumer guarantees, not
validation supplied by this 48-line value module. The module does not prohibit
subclass creation or enforce a common policy on every caller.

The named [lease-language tests](../../tests/test_execution_lease_fence.py.md)
cover duration/ClaimIdentity bounds, public command shapes, a fake SQL observation
and static schema contracts. They do not directly construct this fence or exhaust
its worker/generation/descriptor boundaries. The separately reviewed
[rotation fencing tests](../../tests/test_gateway_key_rotation_deployment_fencing.py.md)
exercise selected handoff construction, hostile subclass rejection and changed
claim authority through real database services. Neither source relationship grants
review coverage to all lifecycle/coordinator/lease recovery semantics.

Read depth: full 48-line owner and 246-line focused test, selected actual claim,
duration/command/result/ownership, PostgreSQL claim/observation and schema contract
paths, with retained reviewed rotation/kernel/fencing context. No executable
validation, source change, credential, database/provider/runtime or lease mutation
was performed for this documentation. It adds no security or mutation surface.
