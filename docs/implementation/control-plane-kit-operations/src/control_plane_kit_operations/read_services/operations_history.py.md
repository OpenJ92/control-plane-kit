Source: [operations_history.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/operations_history.py).
Maintain this companion alongside its source.

Plan summaries and details project `derivation_profile` only when the record
explicitly carries one. Legacy absence stays absent. The existing `payload`
continues to contain the unchanged Core plan descriptor; the stored Operations
envelope is not substituted for Core wire in the public history projection.

Session plan pages and focused plan details share the same summary projection.
Existing workspace membership, graph redaction, risk and recovery logic remains
in place. Malformed stored envelope failures remain bounded through these read
paths. Reads never fill missing profiles or change historical records.
