Source: [test_management_bootstrap_planning.py](../../../../control-plane-kit-core/tests/test_management_bootstrap_planning.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This suite uses the actual graph-pair compiler and inspects its returned DAG;
fixtures do not implement a second planner. It covers initial and retained
management observation ordering, exact graph-bound references, unsupported
changes and real service dependency cycle refusal.

`ManagedConfigurationUpdateTests` adds #1970's new-law target: exactly one
existing public JSON application file changes content while the strict V2
wrapper and all other graph material remain fixed. It checks deterministic
disruptive workload reconciliation before desired protected health, retained
management-path ordering, input preservation and no infrastructure replacement.
Equal graphs remain empty. #1971 retargets these laws to the common managed-update
compiler, which accepts the closed content/add/remove union. The obsolete
assertion that this entry point rejects valid content is intentionally replaced
with acceptance; useful add/remove behavior and refusal laws remain.
Graph-valid refusal controls cover provenance-only changes, wrapper/slot/extra
material changes, wrong receiver targets, data/unresolved-secret/authority consumers and
independent verification. The active socket-secret case uses ordinary topology
compilation to prove that unresolved effective delivered material is excluded too.
These are pure planner laws, not admission, installation or provider evidence.

#1865 restores node-suppression coverage independently of the existing
runtime-suppression law. For external and attached gateways/connectors, the
runtime and the other management node still start and ingress allocation still
exists. The suppressed node gains neither StartNode nor WaitForHealthy, and
its missing structural start excludes fresh GATEWAY_INGRESS_READY refinement.
Existing GATEWAY_LOCAL_READY and AUTHENTICATED_MANAGEMENT_PATH obligations remain;
their presence is not observed success, adoption, or execution authority.

`ManagedSelectedConsumerUpdateTests` adds #1991's three separate selected-consumer
families: one selected version, immutable image, or existing app-file content.
It preserves deterministic plans, roundtrip, no-op, one disruptive reconcile and
the established desired-health ordering. Missing selection, combined edits,
delivery/slot changes and unrelated edits remain refused. A second use distinguishes
one changed version from replacing the whole selection map; two retained consumers
cannot update their selections together. These are pure laws, not authorization
or installed-material evidence. The initial causal-red checkpoint ran1033 tests
with only the three legal-family readiness assertions failing. The implemented
Core gate at 57724876 passed 21 support tests and all 1035 package tests, plus
compile/import checks. Operations and live composition require separate evidence.

## #1976 verifier-refresh laws

ManagedVerifierRefreshTests exercises only the public graph-pair compiler. A
same-target/declaration public-key-set successor, with and without unchanged
complete secret selection, must produce one disruptive reconciliation before
desired protected health and preserve the managed-path chain. Plan/graph codec
round trips, deterministic output and immutable inputs remain observable laws.

Negative cases preserve every target component (workspace/receiver included),
strict V2 selection on both sides, wrapper slots, all non-wrapper material and
whole-graph isolation. Equal graphs remain empty; formatting, normalized family/
key reordering and provenance-only rewrites are not refreshes. Mixed app/image/
secret/topology edits and ineligible or unresolved retained consumers stay
review-blocked. No Operations authority or replay state machine is simulated.

The first tests-only checkpoint selected an artifact by position despite product
sorting and produced seven fixture errors; it is not causal red. Corrected
`d433089d` ran 1,041 package methods with only the two intended valid-refresh
subcases failing at ready_for_execution, no errors; 21 support tests and integrity
passed. Production implementation follows that causal evidence. Final green and
review belong in the issue/PR record; test count is not independent law count.
## #1978 coupled declaration and route targets

ManagedDeclarationUpdateTests exercises the existing public compiler with a
same-image, same-family product revision/descriptor successor, strict health-only
wrapper declaration and its exact gateway route. Fully constructed input graphs
are codec-normalized; artifact content remains opaque. Both health-kind directions
retain readiness. A positive case keeps complete selected secrets on both
replacement recipients. Gateway self and an unrelated peer/alias/origin stay fixed.

The positive plan is compared to the actual base graph compiler's activities,
IDs, operations, impact/risk and dependency sets. Its only added edge must order
workload reconciliation before gateway reconciliation, with the existing desired
local/connected/path/workload-health chain and no stale-route health ancestor.
Strict codecs, immutable inputs and plan roundtrip remain owner laws, not a fake
executor. Negatives cover incomplete coupling, exact targets and slots, unchanged
route decoys on either side, unrelated bindings/material, product metadata/image,
health-only readiness and eligibility of both replacement recipients.

These seven target methods are tests-first under #1978 plan6235493179,
clarification6235526740 and North release6235539397. Missing-relation/order red
must come from the unchanged owning Core Docker gate; setup errors earn no causal
credit. No production implementation or runtime acceptance is included here.
# C5 target review refinements

The variable-bearing composition uses a valid three-family receiver wrapper and
explicitly asserts the existing strict route codec's depth refusal. It earns no
isolated causal credit for a new health-only C5 predicate. Separate cases refuse
a strict-valid verifier change coupled with the declaration transition, and
gateway wrapper/route targets that agree with each other but name a foreign node
instead of the actual gateway placement. These remain unexecuted target tests.

The tests-first checkpoint at 32e377ed ran through the unchanged owning Docker
suite: 21 support passed; 1048 Core tests produced four intended managed-readiness
assertion failures and zero errors. Actual base-plan readiness passed. Deeper
assertions after managed readiness earned no red credit. The subsequent bounded
implementation preserves these tests; candidate green validation is pending.

Independent source review added one route-size boundary regression. Both supplied
route documents strictly decode; current canonical material is exactly 131072
bytes, and the implied declaration-only successor would exceed that limit.
Desired material fits only by removing an unrelated route, so the public planner
must return review-blocked instead of raising during an intermediate aggregate
construction. The fixture proves its strict-valid and oversized controls first.

## #2016 retained-image ingress targets

Six target methods exercise the separately proposed public
`compile_retained_image_ingress_activity_plan` without changing existing V1 tests
or application implementation. The exact original image relation remains generic,
including explicit reverse revisions and unchanged complete secret selection.
The new plan must contain exactly connected -> authenticated path -> ingress
readiness -> one disruptive workload reconcile -> desired protected health.
Targets inspect direct dependencies and ancestry, the complete stage set,
unchanged operation identity/risk/impact and graph preservation. They do not
implement another compiler or simulate provider outcomes.

Actual graph/plan codecs and resolver checks establish deterministic pinned
desired-graph material. Three existing image relation/refusal test methods are
re-exercised unchanged through the new class's public compiler adapter; their
original V1 executions remain intact. Additional valid graph-pair negatives cover
management/ingress/runtime edits and changed/unresolved selection. Other managed
families remain review-blocked in this new entry point. Equal graphs are empty;
Operations separately requires a nonempty eligible preparation for its profile.

The callable assertion occurs inside collected test methods and is the intended
initial red for the absent public compiler. Deeper assertions earn evidence only
after execution. This is an unexecuted target-only checkpoint under issue2016
interface review6306204025; exact target review and the unchanged owning Core
Docker gate precede any application implementation. No registry truth, provider
authority, observed readiness or live acceptance is claimed.
