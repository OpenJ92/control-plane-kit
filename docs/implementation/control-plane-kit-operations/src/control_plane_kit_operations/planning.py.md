Source: [planning.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/planning.py).
Maintain this companion alongside its source.

The planning command validates pinned base and desired projections and explicitly
selects the management-graph-pair-v1 profile inside the caller-owned unit of work.
Fresh commands then apply the separate runtime-management planning policy before secret-delivery
admission, plan persistence, or action persistence. Unsupported material raises
a fixed `InvalidOperationCommand`; rollback leaves no new plan/action rows.
The policy receives the workspace's active product registrations and checks
each node's exact normalized reference against its matched registered management
projection (transit and complete SDK surfaces). For nonempty plans, nodes carrying
transit or SDK surfaces require a management selection in their runtime on the
same side. Omitted or changed declarations
cannot hide behind a faithful sibling. Malformed product references are refused
independently of catalog contents, before the canonical-empty exception.
Equal or name-only managed graph pairs retain their compiler-proven empty plan.

Complete selected pairs may persist ready plans or Core's explanatory review
plans. Missing gateway readiness and selected variable-only material do not gain
invented observation or variable effects. A real graph-valid dependency cycle
raises Core's `InvalidActivityPlan`; only the fresh derivation call maps this to
fixed `ActivityPlanningGraphInvalid("persisted graph pair cannot produce an activity plan")`
outside the exception context. It creates no plan/action/event/observation rows.
Unexpected exceptions retain their identity, and historical derivation keeps its
existing error semantics.

Fresh plan and action records carry the same explicit profile. Profile selection
is internal service policy, not a command option. Request descriptors and intent
fingerprints remain unchanged so retries can still reach their original history.

Historical `_activity_plan_replay` verifies exact record/action profile presence
before decoding the pinned pair and selecting its declared derivation. Both
absent means legacy structural interpretation; null, unknown or unequal markers
fail even on equal-output plans. Stored explicit graph-pair profiles use Core's
graph-pair compiler, independently of current fresh policy. Result congruence
uses the same dispatcher and binding. Malformed stored envelope errors become
fixed replay conflicts without parser cause/context; unrelated failures retain
their existing semantics. Result descriptors expose only explicit profiles.

Replay reproduces its recorded pair and plan without applying new-command
admission or allocating plan/action/event records. This preserves old truthful history while the
execution coordinator separately refuses unsupported new effects. The policy
does not add schema, provider access, transaction commits, or approval powers.
`test_planning_transition_replay.py` protects the graph pair boundary, no-op,
no-persistence refusal, and exact nonempty historical replay. Recorded graph
references remain internal durable coordinates; rejection text contains none.

No existing row or uncertain attempt is rewritten. The history store owns the
strict legacy/envelope representation. Fresh managed planning now exists;
transport adoption remains separate work. The closed profile's prior reader
upgrade/downgrade requirements still apply before any rollout.
