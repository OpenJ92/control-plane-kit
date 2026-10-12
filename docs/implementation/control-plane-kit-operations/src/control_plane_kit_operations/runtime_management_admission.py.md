Source: [runtime_management_admission.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_management_admission.py).
Maintain this companion alongside its source.

The fresh planning predicate accepts a validated deployment transition, with no
supplied plan, profile, or ready flag. It first parses every node in both snapshots
using graph_authoring's authoritative per-node extractor. A second pure call to
the shared graph-pair derivation proves canonical emptiness; this preserves the
existing no-op exception without trusting a supplied plan or execution policy.

Managed-update profile selection is a narrow opt-in. A structural prefilter
recognizes only the retained single managed runtime/gateway family with exactly
one node introduced or removed and no non-artifact gateway change, or a retained
workload content candidate. The common Core compiler owns the exact closed union;
the content prefilter only locates the affected runtime. Recognized
candidates must then satisfy the complete registered-product, lifecycle,
readiness, authority-delivery, and exact route-artifact predicate; failure is a
fixed refusal and cannot fall back to legacy execution. Other established
management updates outside these closed laws retain
`management-graph-pair-v1` planning and its existing execution guard. Equal and
name-only pairs likewise retain their canonical empty legacy plan.

For retained content, all nodes in the affected Docker runtime must match an
active exact registered product, including gateway, connector and every retained
receiver. The complete configured route set must equal the scoped expected set.
No workload count or same-product X/Y condition is imposed on this relation.
Unrelated runtimes do not contribute routes or effects to the content update.
Current plan selection and independent execution admission both enforce these
facts; the supplied plan must equal the ready canonical current derivation.

The managed route slot is decoded with Core's shared
`ReceiverManagementRoutesCodec` and compared to a complete value independently
reconstructed for each pinned graph. The expected value selects each node's V2
receiver configuration, requires its exact workspace/runtime/node/socket scope,
uses the registered product's unique HTTP provider port for the private origin,
and includes gateway self plus every readiness workload. Equality therefore
preserves retained bindings and admits only the graph's exact Y addition/removal;
an absent self binding, arbitrary origin, stale target, extra binding, or partial
list is a fixed refusal. The accepted private origin is explicitly Docker-only,
where graph node identifiers are the provider's private DNS names; other runtime
kinds cannot inherit that provider-specific addressing law. This is
application-independent: X and Y must share the
same exact registered product within one transition, while unrelated product
references in separate applications remain valid.

Route `target_id` values remain caller-owned opaque aliases under Core's shared
contract. Admission therefore compares the complete target, declaration, and
origin set to graph-derived expectations without inventing alias names, while
requiring every retained target's alias to remain byte-for-byte stable across
the update. A newly introduced target may use any valid unique alias supplied by
the accepted authoring path; an alias rewrite of gateway or retained X is refused.

For nonempty plans, each node's `(gateway_transit, control_surfaces)` must exactly
equal the same projection of every matched identity-plus-digest registered
contract. Each node declaring either surface needs management selected in its
own runtime on that same graph side. Core already validates the complete gateway,
connector, ingress, socket and runtime relation. Unrelated catalog entries do not
taint a graph, and an unrelated extra provider socket does not trigger a broader
product audit. One faithful sibling or selected runtime cannot cover another.

Complete selected pairs may yield ready or review-blocked Core plans. An
unselected-to-selected SDK repair still fails the current-side law. Selected
variable declarations may explain review work but grant no variable effects.
Real compiler cycles propagate `InvalidActivityPlan` from this pure policy; the
fresh public service owns its fixed error mapping. Planning performs no I/O.

The execution predicate examines both complete pinned graphs for an explicit
runtime management selection, a gateway transit declaration, or SDK control
surfaces. Fresh and retained/mixed management execution stays guarded until
the accepted transport exists. It does not infer support from a node name,
metadata flag, changed node, supplied plan, or product registration. Ordinary
legacy verification without this material retains existing behavior.

The no-op exception permits only a congruent no-op: the supplied plan has no activities, both graphs validate,
and Core's diff/compiler also produces no activities. An invalid graph or forged
empty plan cannot qualify. Supplying no plan means direct activity translation
and never receives this exception, even for equal graphs.

Nonempty managed execution requires the actual recorded
`PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1`, an existing `TeardownDeployment`
from validated graph values, and full equality with its ready canonical derived
plan. Teardown's empty desired boundary includes delegation authorities as well
as nodes, edges, runtimes and ingress. Equality preserves targets, dependencies,
risk and retention decisions without another operation allowlist. The existing
planning predicate additionally enforces same-side management selection and
catalog declaration fidelity. Legacy/absent profiles and string lookalikes do
not acquire support. Callers still own full activity membership and permission;
this exception grants neither. The pure and real PostgreSQL teardown targets
prove support, direct-membership refusal, real approval/custody and uncertainty
behavior; they do not prove live resource deletion.

This owner imports
Core and Operations' existing pure product-reference/registration values;
callers retain transaction, authorization, and history ownership.

The same refusal applies when authored fields omit a declaration that the exact
referenced registered product supplies. Both graph reference sets use the
existing normalized identity-plus-digest extractor; only matched descriptors
are inspected. Unrelated registrations and same-identity/different-digest
descriptors do not trigger refusal. The caller supplies the registration snapshot:
there is no extra status filter absent from direct effect material selection.
Any SDK surface, including variable-only surfaces, is conservatively guarded;
the advertised transit profile itself remains health-only.

Malformed reference parsing returns a fixed refusal independently of catalog
contents, before the no-op exception. Only the extractor's ValueError family is
handled here; its candidate-bearing exception does not escape or remain as the
cause/context of a caller's error. Unexpected failures outside that call retain
existing error/uncertainty semantics. This checks declared powers in selected
material, without auditing or rewriting general graph/product congruence.

The policy performs no I/O and persists no facts. It introduces no credential,
address, grant, network exposure, or destructive effect. The three consumer
test groups cover both graph directions, transit-only and SDK material,
equal/name-only no-ops, forged emptiness and direct activity rejection.
General product-contract congruence and transport support remain downstream work;
declarations and these tests do not prove live execution.
