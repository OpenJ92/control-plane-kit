Source: [effect_outcome_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_outcome_store.py).
Maintain this companion alongside its source.


B1 / #1923 configuration reads use the command's shared pretransport budget for
the complete outcome row, full bounded preimage, and ordered observation
membership plus a completeness sentinel. Joined membership/observation reads
charge both relational identities. Existing outcome, event and observation
validators remain authoritative; no alternate state machine or cached mutable
authority is introduced.
