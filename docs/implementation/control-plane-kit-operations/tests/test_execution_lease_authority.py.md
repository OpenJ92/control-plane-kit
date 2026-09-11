Source: [control-plane-kit-operations/tests/test_execution_lease_authority.py](../../../../control-plane-kit-operations/tests/test_execution_lease_authority.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These seven pure tests protect the public identity, representation and input
domain of ExecutionLeaseFence, plus its projection from ClaimIdentity. Despite
the authority name, they do not assess lease freshness, verify credentials or call
a service authorization boundary. Their observable laws concern Python values;
there is no database fixture, clock, request lookup or runtime effect here.

The defining-module test locates and imports execution_leases and requires the
package-root fence and error exports to be the very same objects as that module's
definitions. The error must subclass ValueError. The helper methods separately
assert that those root exports exist before other tests use them. The actual
[package root](../../../../control-plane-kit-operations/src/control_plane_kit_operations/__init__.py)
imports these definitions directly from their
[owner](../src/control_plane_kit_operations/execution_leases.py.md); the identity
assertions guard against an independent root definition or replacement facade.

The representation test requires a dataclass with exactly worker_id and generation
fields, an exact two-key descriptor for worker-a generation seven, and a
FrozenInstanceError when assigning generation. Its repr must contain that worker
and generation and omit five selected strings concerning lease times, authority
references, secrets and SQL. This checks the ordinary example's representation,
not universal redaction: worker_id is visible in both repr and descriptor, and
the constructor does not ban all those strings from caller-supplied worker text.
No custom repr, hidden credentials or immutable descriptor mapping is asserted.

Worker boundary cases accept one character and 512 characters, and reject empty
text, 513 characters, a newline-bearing canary and an integer. Generation cases
accept one and 2**63-1, and reject True, zero, minus one, 2**63 and a string canary.
Every selected rejection must use InvalidExecutionLeaseFence, have neither cause
nor context, render within 512 characters across str/repr and omit the supplied
candidate canary. Empty text has no canary assertion. The tests do not require one
exact error message or enumerate all Python types and control characters.

The fully read owner explains the general domain behind those examples: worker
validation uses isinstance(str), nonempty text, a 512-character bound and rejection
of characters with ordinal below 32. It does not require exact str type, ASCII,
trimmed text or a byte-length bound. Generation uses exact int type and the
positive PostgreSQL bigint range. Fixed categorical errors do not interpolate
candidate values. The file tests bool rejection directly; it does not separately
exercise string subclasses, integer subclasses or every allowed Unicode value.

The ClaimIdentity tests require exactly its four fields: worker_id, generation,
claimed_at and lease_expires_at. Its fence property must return the exact public
ExecutionLeaseFence type with the matching worker/generation and no timestamp
keys in its descriptor. The selected ordinary claim uses timestamp-shaped text;
the maximum-boundary claim deliberately uses the strings claimed and expires.
Both project successfully, including simultaneous worker-length and generation
maxima. These assertions protect projection across the represented durable
domain without introducing a second stored fence field.

The actual [ClaimIdentity implementation](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
validates its text with the same nonempty/bounded/control-character law, bounds
generation and constructs a new fence from worker/generation on property access.
It does not parse those timestamp strings or compare them with the current time.
The tests check resulting type/equality, not object identity across repeated
property accesses, expiration ordering or whether any durable request currently
holds that fence.

The final test requires ExecutionLeaseFence to be a different class from core's
[EffectAttemptFence](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
and requires an execution fence instance not to be an instance of that core type.
The actual classes both expose worker/generation descriptors, but belong to
different ownership domains. The test preserves that nominal distinction; it
does not exercise conversion, cross-type equality or every consumer's acceptance
policy.

Security evidence is the bounded candidate-free errors for selected invalid
values and the absence of extra fields on this value. A constructible fence is
not proof of authentication, scope, current claim ownership or an unexpired lease.
Consumers must establish those facts against their own authority and durable
state. This file introduces no persistence, operational history or cleanup work.

Read depth: all 148 test lines and helpers, the full execution_leases owner,
actual ClaimIdentity and text validation, selected root exports and the actual
EffectAttemptFence definition. This is a documentation-only source review; tests
and imports were not executed, and no credentials, database or provider were used.
