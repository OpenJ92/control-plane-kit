Source: [workspace_permission_upgrade.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/workspace_permission_upgrade.py).

#2095 restores the existing explicit workspace/managed transition's fixed pair:
`c8cc4205` source to `7288fee8` historical target. Plan defaults, admission,
recognition, verification, receipts and inspection all name this same target.
No historical CHECK domains change. Existing authority checks, transaction and
locking boundaries, atomic seeds/receipt, provisional results, uncertain commit
and exact replay after revocation remain owned by this implementation.

Fresh/current `75b6b900` and sampling-expanded pseudo-priors are not admitted by
this historical entrypoint; inspection reports unknown. Conversely, ordinary
current startup refuses the historical target without mutation. This change
does not authorize or supply a retained-to-current bridge.
