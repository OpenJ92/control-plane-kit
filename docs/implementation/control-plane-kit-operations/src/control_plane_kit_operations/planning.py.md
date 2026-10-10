Source: [planning.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/planning.py).
Maintain this companion alongside its source.

`SetDesiredGraph.proposed_graph_id` optionally supplies a new immutable authored
graph name. It is validated under the shared public graph-reference law, then
included in the command descriptor and full graph-bearing intent fingerprint
only when present. Omission preserves historical descriptor/fingerprint bytes.
The service resolves action replay before choosing the supplied name or calling
the existing allocator. Workspace/session/product admission and pointer fences
still precede the immutable INSERT. Graph, projection, pointer and action share
the caller-owned transaction; a collision rolls back with fixed refusal and
never adopts an existing graph. No new reservation, schema or effect is added.
`test_planning_commands.py` covers persistence, legacy bytes, replay, fresh and
concurrent collision, detached error chains and unrelated late-action failure.

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

The closed `managed-update-v1` profile is selected only after the pure admission
owner identifies the bounded one-node add/remove candidate and proves its exact
registered material. A recognizable but incomplete candidate is refused rather
than downgraded. Updates outside that candidate family keep the established
`management-graph-pair-v1` derivation and remain subject to its existing planning
and execution guards; profile selection does not reinterpret or broaden their
execution authority.

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
