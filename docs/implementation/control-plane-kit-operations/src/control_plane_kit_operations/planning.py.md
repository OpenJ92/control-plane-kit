Source: [planning.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/planning.py).
Maintain this companion alongside its source.

The planning command validates pinned base and desired projections and derives
the Core structural plan inside the caller-owned unit of work. Fresh commands
then apply the shared runtime-management refusal policy before secret-delivery
admission, plan persistence, or action persistence. Unsupported material raises
a fixed `InvalidOperationCommand`; rollback leaves no new plan/action rows.
Equal or name-only managed graph pairs retain their compiler-proven empty plan.

Historical `_activity_plan_replay` reproduces its recorded pair and plan without
applying new-command admission. This preserves old truthful history while the
execution coordinator separately refuses unsupported new effects. The policy
does not add schema, provider access, transaction commits, or approval powers.
`test_planning_transition_replay.py` protects the graph pair boundary, no-op,
no-persistence refusal, and exact nonempty historical replay. Recorded graph
references remain internal durable coordinates; rejection text contains none.
