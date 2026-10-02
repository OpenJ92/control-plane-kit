Source: [image_pull_authority_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/image_pull_authority_store.py).
Maintain this companion alongside its source.

B1 selects only image-pull authorities relevant to the activity's image registry
and repository. Exact/prefix matching and existing authority ranking remain
owned here; bounded candidate reads include the completeness sentinel and share
the command ledger. Absence is not replaced with a workspace-wide catalog scan.
