Source: [control-plane-kit-core/tests/test_scheduling.py](../../../../control-plane-kit-core/tests/test_scheduling.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Readiness from plan dependencies and supplied evidence

A diamond-shaped ActivityPlan and explicit SagaState fixtures exercise
[derive_schedule](../src/control_plane_kit_core/planning/saga.py.md). Tests
protect initial readiness, fan-out, waiting joins, failed-predecessor reasons,
transitive blocking and global stopping of unstarted work after failure.
Cancellation preserves running evidence; compensation remains unavailable until
forward and compensating work settles, then follows reverse completion order.

Evidence mismatch tests reject missing, foreign and duplicate step IDs and
incoherent completion/failure lists. The fixture explicitly supplies status and
compensation availability; these tests do not authenticate events or prove that
state came from the canonical journal. The test named for absence of layer
barriers uses a diamond fixture; it is not a general concurrency or fairness
proof.

The scenario loop validates each catalogue graph pair, runs the actual diff
and compiler, and checks deterministic initial scheduling. Review plans have
no ready work and only selected review/blocker reasons; executable nonempty
plans have initial ready work. It does not run scenarios to completion or
compare every expected activity and dependency from the catalogue.

Full 343-line test file and helpers read alongside the full saga owner.
The scenario catalogue is an imported fixture dependency, not exhaustively
reviewed by this note; see
[its source](../../../../control-plane-kit-core/src/control_plane_kit_core/planning/scenarios.py)
and test_planning_scenarios.py for catalogue assertions. No thread scheduling,
leases, provider calls, persisted replay or live acceptance occurs.
