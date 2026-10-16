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

`compile_managed_update_activity_plan` is the closed pure refinement for one
retained managed runtime with gateway, connector, ingress and one unchanged
SDK-readiness workload. It accepts only one owned, ephemeral-compute,
same-runtime SDK-readiness workload addition or removal paired with runtime-
containment and retained-gateway configuration-artifact reconciliation. Removing
the changed child from the changed runtime tuple must recover the other graph's
exact child tuple, so the profile cannot hide retained-child reordering. It
recognizes graph structure, never product names, artifact IDs, origins,
permission or provider success.

For addition it orders Start(new workload) before Reconcile(gateway), then uses
the desired retained bootstrap path for both new-workload and retained-workload
SDK health. For removal it creates the otherwise absent desired connected/path
and retained-workload observations after gateway reconciliation, then gates the
base workload Stop -> Remove chain on that proof. All observation targets retain
the existing desired graph/relation pins and activity identities. Extra graph
changes, independent verification, ambiguous SDK selection or a different
managed shape add a categorical review instead of broadening update support.
Liveness-only workloads and retained compute are explicit refusals; removal also
requires the structural plan to contain both the exact Stop and Remove activity.

The compiler adds no effect or result value. Reconcile success is only an order
predecessor here; Operations admission remains closed and the provider owner must
separately prove installed selected material before execution support can use the
plan.

`compile_managed_update_activity_plan(current, desired)` (#1971) also
recognizes the closed content relation introduced by #1970: one existing non-wrapper public JSON file
on one retained workload changes actual content. Its artifact ID, path, media and
read-only mode remain equal. The strict V2 wrapper, receiver scope/identity,
declaration and public trust remain byte-for-byte fixed, as do all other graph
values. Restoring the old artifact field must recover the whole current graph.
The selected wrapper target must name that runtime/node/control socket.

```python
plan = compile_managed_update_activity_plan(
    validate_graph(current_graph), validate_graph(desired_graph),
)
```

The complete authored graphs are inputs, including the unchanged actual wrapper
and changed application file. The executable example is
`ManagedConfigurationUpdateTests.pair` in the owner test module. The compiler
reuses structural disruptive `ReconcileNode` and the existing desired protected
health dependency. It adds no ordering algorithm or operation. The unchanged
gateway-local -> connected -> authenticated-path chain still precedes health;
no retained infrastructure start, allocation or replacement is introduced.

The workload must be owned ephemeral compute with no data, unresolved effective
secret uses (including those compiled from connected sockets), runtime-authority
deliveries or unsupported independent verification. A unique readiness surface
and unchanged managed runtime are required. Gateway/connector updates, missing
or wrong-target wrappers, provenance-only deltas and every other graph change
are review-blocked. Equal material remains empty. Add/remove and content use one
current compiler; the superseded content-only public API is removed without an alias.

This pure relation grants no permission and proves no installation. Operations
#1971 independently admits registered product/authority facts using the current
managed-update profile. It owns original attempts, installation evidence and advancement.
Verifier and declaration updates retain their later separate contracts.
No provider, persistence or network dependency is added.

The same entry point also recognizes the #1972 immutable-image relation. One
eligible workload changes exactly `product_identity`, `product_descriptor_digest`
and `oci_image`. Both references must be canonical existing Core product values:
the product namespace/name and image registry/repository stay equal; the explicitly
authored positive contract revision, descriptor digest and actual image digest
must differ. Revision ordering is not inferred. Mutable tags, repository moves,
unrelated products and mixed configuration/image edits remain review-blocked.

Restoring only those three metadata values must recover the entire current graph,
including all ordinary block metadata. Image and content updates share the same
workload eligibility and exact receiver-wrapper selection. Image updates preserve
all artifacts and receiver bytes, and reuse the existing disruptive replacement
followed by desired health. `ManagedImageUpdateTests.pair` constructs the example
through genuine product descriptors and product instantiation.

This proves graph-level preservation only. Operations #1973 must independently
validate both active registered descriptors, actual runtime-contract equality,
current successor authority and durable execution evidence. Servers #251 owns
the real Docker public-client experiment; neither publication nor image pull
authority follows from this pure relation.

#1991 qualifies retained image/content updates with the same delivery contract
on both sides and complete canonical selected resolving uses. Reference-only
deliveries add no resolving use. Missing coverage is invalid; unresolved values
remain refused. Image/content restoration already requires exact selections to
remain equal, including versions. The retained wrapper and all existing
readiness, lifecycle, authority and graph restrictions remain in force.

The new version-only relation requires exactly one SECRET_SELECTIONS field
change on one retained eligible workload. Restoring that field must recover the
whole current graph. Canonical coverage and unchanged delivery contracts fix the
use keys; exactly one selected version may differ. It reuses the generic diff,
disruptive reconciliation and managed health ordering. Combined version/image/
content edits, two version changes, multiple consumers and changed slots do not
acquire support. Recognition grants neither selection nor execution authority.
