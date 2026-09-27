Source: [instance.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/instance.py).
Maintain this companion alongside its source.

The public InstanceReadService facade delegates workload_verifier_configuration(workspace_id, purposes) directly to the existing gateway security projection owner, preserving argument and result identity. It does not acquire a second store or transaction, select issuers, or define new authority.

