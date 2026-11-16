Source: [desired_topology_drafts.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/desired_topology_drafts.py).
Maintain this companion alongside its source.

#2091 extracts `bounded_draft_graph_descriptor` from the command's existing
pre-storage admission so preview and save measure the identical canonical graph
and spaced UTF-8 JSONB bytes. The 1-MiB cap, internal intent fingerprint, replay
ordering, publication checks and transaction ownership remain unchanged.

An extension append records the submitted public recipe and commitment in the
intent fingerprint alongside the canonical graph. An exact replay resolves the
original immutable revision and graph before examining current registrations.
Fresh materialization shares the existing draft transaction, action, graph
version and receiver-origin checks. Concurrent editors presenting one head can
append at most one next revision.
