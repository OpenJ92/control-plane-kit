Source: [management_compiler.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/management_compiler.py).
Maintain this companion with source and imported contract changes.

compile_graph_activity_plan accepts two ValidatedGraph values, derives their diff
and preserves the structural compiler's operations before refining managed health
obligations. Empty/name-only plans remain empty; retained-runtime management
cutover remains ReviewChange. It supplies no infrastructure, provider effects or
Operations Deploy object. ActivityPlan still owns dependency/cycle validation.

The submitted topology has an ordinary gateway node and connector, plus a named
ingress. RuntimeManagement selects those values. For example:

```python
runtime = DockerRuntime(
    runtime_id="cluster",
    children=(gateway, connector, workload),
    management=RuntimeManagement("gateway", "management"),
)
```

Here the three children are already-authored block values and the topology also
includes the named ingress targeting the gateway's exact transit socket. This
excerpt does not manufacture them. The complete executable fixture is
tests/test_management_bootstrap_planning.py::topology. Application ingress is
separate from management ingress.

For actual SDK health work, policy unique-socket-readiness-preferred-v1 first
counts all health-capable sockets. One permits READINESS if advertised, otherwise
explicit LIVENESS. Multiple sockets are ambiguous. This is static selection,
never fallback after a failed/unknown/denied readiness request. Gateway-local
readiness independently requires exactly one own readiness-capable socket; its
transit socket is not implicitly its own SDK socket. A liveness-only gateway is
a valid declaration but cannot fulfill this bootstrap obligation.

Fresh creation requires the runtime, gateway, connector and ingress identities to
be absent in the current graph, plus actual structural StartRuntime, exact
StartNode operations for both nodes, and AllocatePublicIngress. Reconcile does not
substitute for StartNode. Classification is per runtime, before dependency rewrites;
the current graph need not be empty. Existing validation/readiness selection and
whole-plan management-retarget review still apply.

Fresh order is gateway start -> ingress allocation -> connector start, then two
branches: authenticated path -> gateway-ingress-ready, and connector-connected.
Every selected SDK health request joins both branches, including connector SDK
health. No synthetic connected/path/readiness edge is introduced. Only the chosen
allocation's old gateway wait predecessor is removed; gateway startup replaces it.
Other structural service prerequisites, gateway wait prerequisites and mutation
IDs survive. Real dependencies may constrain the branches; cycles are rejected.

Retained/reconcile paths keep gateway-local-ready -> connected -> authenticated
path -> SDK health. Actual connector start/reconcile and allocation still wait for
local readiness there. No starts or allocations are invented. Complete replacement
can qualify on its desired side while preserving old teardown. Previously admitted
plans decode unchanged without recompilation.

Runtime lifecycle suppression is executable coverage for non-fresh classification.
New non-owned gateway/connector combinations can fail earlier in the existing
structural compiler (#1865); this issue does not claim they reach managed fallback.
Exact node-start eligibility remains required, with isolated lifecycle regression
coverage owed by that separate structural fix.

Independent verification and legacy health_path intent are preserved. SDK success
cannot discharge SQL/HTTP/body checks. Those requirements retain their original
WaitForHealthy activity identity and dependencies and are explicitly review-blocked
where gateway fulfillment is unsupported. Gateway independent checks do not gate
allocation of their own ingress. No private fallback or temporary probe exists.
Non-SDK/variable-only nodes remain graph-valid and gain no fabricated SDK request.
Unsupported generic/empty health obligations remain explicit reviews.

Graph digests use each ValidatedGraph's own codec, RFC8785 canonical bytes, SHA256
and domain control-plane-kit.management-graph.v1 followed by one NUL (1 MiB bound).
Relation digests use domain management-relation.v1 under the same control-plane-kit
prefix and commit runtime, management references, full named ingress, exact transit
declaration and independently selected own-readiness socket/kind (4096-byte bound).
Observation IDs hash the entire request with management-activity.v1 domain;
categorical review IDs hash their exact ReviewChange operation with
management-review.v1 domain. All domains end in one actual NUL.

resolve_management_observation compares a candidate with an independently selected
accepted-plan operation, then re-derives every pin/selection from the exact graph
side. Even identical graph pairs cannot erase side mismatch. It returns immutable
graph material, not observed results or granted authority. Full graph fingerprints
include custom codec material; incompatible codec languages preserve structural
review refusal. Bounded/categorical errors retain no candidate cause/context.

Teardown retains base connector-stop -> ingress-removal -> gateway-stop semantics;
new checks use desired material. No observations are invented solely for teardown.
Operations #1860 owns bootstrap admission/signing/history; Interpreters #148 owns
bootstrap transport/execution and legacy-helper retirement. Interpreters #149 owns
paired signing, Servers #188 connection evidence and #181 composition. Core planning cannot
establish successful stage observations or freshness. The existing Operations
unsupported-execution guard remains intact.
