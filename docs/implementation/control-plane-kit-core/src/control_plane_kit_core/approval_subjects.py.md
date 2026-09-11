Source: [control-plane-kit-core/src/control_plane_kit_core/approval_subjects.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/approval_subjects.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# What an approval refers to

The closed ApprovalSubject sum names either one activity plan or one gateway
key-rotation review intent. It is review data, not an approval decision, grant,
database lookup or request to execute work. Operations owns the persisted
subject-to-request/decision/action relationships and subsequent admission.

ActivityPlanApprovalSubject retains only plan_id. Its review digest hashes the
tagged identity, not the activity plan contents. Correctness therefore depends
on the durable owner keeping that plan identity bound to its intended immutable
meaning; this digest cannot detect an external rewrite of plan contents.

GatewayKeyRotationApprovalSubject retains the target, purpose, old-key identity,
time policy and supplied rotation-intent digest. Its descriptor fixes overlap
roles to old+new and retirement to new; its digest hashes compact sorted-key JSON
of that descriptor. The subject omits key bytes and custody references. It does
not validate the supplied intent digest against a stored rotation, prove key
custody, or show that a gateway has installed either verifier set.

Identifiers and the intent digest have explicit syntax/length checks. Direct
rotation construction range-checks lifetime/skew but does not require their
exact integer type; in-range booleans or floats can pass those comparisons.
The descriptor decoder is stricter: it requires an actual dict, exact field
sets, exact role lists and true integer timing values before construction.
Do not claim identical admission for direct construction and wire decoding.

The decoder accepts only the two supported subject kinds. Malformed purpose
conversion can retain a chained cause. Identifier-shaped text and opaque
digests are not a universal detector for sensitive content; omission of private
key/reference fields is a specific representation boundary.

[test_approval_subjects.py](../../tests/test_approval_subjects.py.md) checks
selected round trips, review-policy digest sensitivity and focused rotation
scopes. The [policy owner](policies.py.md) supplies pure decisions; consuming
Operations services must separately establish current durable authority.
