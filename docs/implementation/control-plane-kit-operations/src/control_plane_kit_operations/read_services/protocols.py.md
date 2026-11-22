Source: [protocols.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/protocols.py).
Maintain this companion alongside its source.

The existing delegation signing-key read protocol gains an optional keyword-only limit on list_for_verification. Omission preserves existing callers. The workload projection supplies 17 to distinguish an allowed 16-key family from overflow without unbounded database materialization.
