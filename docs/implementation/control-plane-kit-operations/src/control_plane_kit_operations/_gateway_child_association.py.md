Source: [_gateway_child_association.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/_gateway_child_association.py).
Maintain this companion alongside its source.

Private Operations checks share the immutable rotation review projection,
original approval decision/action and exact publication association. Admission
still owns current rotation phase, publication version, replacement key and
canonical child-plan checks.

Fresh execution of an existing child reads its exact original admission receipt,
publication and optional deployment checkpoint. A parent approval session may
differ from the child session. Rotation progress does not rewrite the original
authorization or require the parent session to reopen. Current child pins,
session, worker/fence/lease and transition eligibility remain their owners' checks.

These checks acquire no rotation lock, allocate no IDs, return no permission
token, mutate no records and call no provider. They depend only on Core values
and Operations records, avoiding an admission/lifecycle dependency cycle.
