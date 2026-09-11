Source: [control-plane-kit-operations/tests/test_execution_lease_fence.py](../../../../control-plane-kit-operations/tests/test_execution_lease_fence.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These eight tests check lease-language structure, selected duration/generation
bounds, SQL observation shape and the checked-in schema contract. Despite the
filename, they do not directly construct ExecutionLeaseFence or test every fence
consumer. Most checks inspect Python values, dataclass fields, type hints or
method signatures; one invokes a real store method over a fake connection. No
PostgreSQL connection/schema installation, provider or runtime is used by these
tests, and no tests were executed for this documentation.

The duration test requires the root export to be the exact lifecycle
ExecutionLeaseDuration value, accepts endpoints 1 and 3600, and rejects True, zero,
-1, 3601, 1.5 and a string canary. ClaimIdentity must have exactly worker_id,
generation, claimed_at and lease_expires_at fields; generation 1 and 2**63-1 are
accepted, while True, zero, -1, 2**63 and a string canary fail. Claim timestamps
are literal claimed/expires placeholders, so these constructions do not validate
canonical timestamps or temporal ordering. Worker-ID/fence bounds are not covered.

assert_safe_error requires no cause/context, combined str/repr length at most
512, and omission of supplied canaries. These assertions protect the selected
duration/claim-generation rejection messages, not every error from stores,
lifecycle or the [fence value](../src/control_plane_kit_operations/execution_leases.py.md).
No arbitrary secret-bearing logs, descriptors or authentication path is exercised.

ClaimAndOpenActivityRun must have exactly request_id, authority, lease_duration
and idempotency_key fields. A constructed duration-600 command must describe
lease_duration_seconds=600 and omit lease_expires_at. For overlap and retirement
preparation commands, dataclass fields must include lease_duration and exclude
lease_expires_at, and resolved type hints must point to the same duration class.
This is public interface consistency, not execution of either preparation program
or evidence that a real claim was timed correctly.

The store interface test uses inspect.signature to require claim_request(self,
request_id, worker_id, lease_duration_seconds) and hasattr to require the locked
observation method. It does not invoke claim_request. Actual
[store source](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
separately checks the duration range, locks the queued request, sets generation 1
and computes claimed_at/lease_expires_at from database clock_timestamp. Those
implementation facts are not results established by this signature assertion.

The observation test invokes observe_request_lease_for_update on
_LeaseObservationConnection. The fake records SQL, discards parameters and
returns a claimed request row whose claimed/expiry time equals a fixed aware
datetime. For clock observation it supplies that same time and True as the
expired result. _Row only supplies fetchone. It does not execute SQL, acquire
locks, evaluate comparisons or sample a database clock.

The test requires observation.expired to be True, concatenates normalized SQL and
checks for FOR UPDATE, exactly one textual clock_timestamp() occurrence and
lease_expires_at <=. This protects selected emitted SQL tokens, including the
inclusive comparison operator. It does not independently prove lock-before-clock
ordering: it never compares statement positions, and the fake even supports a
combined lock/clock query. The returned True is scripted rather than the result
of evaluating expiry equality in PostgreSQL. Actual source currently calls the
request-lock selector before the clock query; that is a source observation, not
real lock/timing evidence from this test.

The direct schema-contract test reads
[CURRENT_POSTGRES_SCHEMA_CONTRACT](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py)
as Python data. claim_generation must have formatted_type bigint, and some
single-column generation constraint must contain the lower-bound and maximum
integer text. The named claim-shape constraint must list status/worker/generation/
claimed/expiry columns and contain IS NULL and IS NOT NULL fragments for all four
claim fields. Actual contract text pairs fully present claim fields with claimed
status and fully absent fields with other statuses. The test does not install a
schema, query a live catalog, execute invalid inserts or prove arbitrary boolean
logic from those substring checks.

The final test requires RunLifecycleResult fields to be request, run, event,
action and replayed, with no duplicated generation field. Actual
[lifecycle source](../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py)
can still include claim_generation in its descriptor by reading request.claim.
The field-layout assertion neither forbids that projection nor establishes
correct generation handling throughout execution/replay.

The owner/value distinction matters: ClaimIdentity stores time and generation,
ExecutionLeaseDuration requests lifetime, and ExecutionLeaseFence projects worker/
generation for comparison with current request truth. The tests here do not call
ClaimIdentity.fence, prove fence nominality, authorize workers, expire/renew claims,
increment generations, exercise takeover, or establish race/deadlock behavior.
Reviewed [rotation fencing](test_gateway_key_rotation_deployment_fencing.py.md)
and [lock-order](test_gateway_key_rotation_deployment_lock_order.py.md) suites
contain different, database-backed checks and retain their own scope.

Read depth: full 246-line test and 48-line fence owner; selected actual lifecycle
duration/claim/result/ownership, ClaimIdentity, store claim/locked observation and
three consequential schema-contract entries were inspected, retaining reviewed
rotation/kernel consumers. This is not a full review of execution/lifecycle or
the entire generated schema contract. No executable validation, source edits,
credentials, database/provider/runtime actions or lease mutation occurred for
these notes; documentation adds no security or mutation surface.
