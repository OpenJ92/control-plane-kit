Source: [deployment_program_interpreter.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/deployment_program_interpreter.py).
Maintain this companion alongside its source.

Preparation composes existing session, desired-graph, planning and approval
owners under their existing authority and transaction boundaries. Inline input
passes the complete graph and optional proposed identity to `SetDesiredGraph`.
The proposal joins preparation intent before session creation; omission retains
the exact historical digest. Derived child keys remain unchanged, allowing
retained intent checks to detect a changed graph or proposal on replay.
Saved-input preparation retains its original lineage and cannot carry a proposal.
No graph synthesis, restamping, provider effect or new transaction is introduced.
The owning preparation tests check persisted graph/plan identity, unchanged
descriptor bytes, approval, replay and legacy digest compatibility.
