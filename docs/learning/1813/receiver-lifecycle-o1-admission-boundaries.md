# O1.C admission boundaries and execution lookup proposal

Status: unaccepted design proposal for the next joint B+C review. No targets,
application source, schema execution or effect release. Source remains accepted
A `2a1bf73`; the selected C-N9 semantic decision is separately recorded in the
[admission plan](receiver-lifecycle-o1-admission-plan.md). That capsule's narrow
PASS does not approve this proposal. B's two graph tables remain as described in
the [storage plan](receiver-lifecycle-o1-storage-plan.md).

## Supported entrypoint disposition

Use **enforce** for an existing owner that can establish the entire applicable
contract in its caller-owned transaction. Use **refuse** when a supported direct
entry lacks that contract. This is the final intended product boundary, not a
temporary blanket V2-rejection stage. A Python-private name or possession of A's
lifecycle guard is not proof of lifecycle authority.

The exact composite persistence API is still a review question. The proposed
shape is an action-owned operation that performs validation and all related
writes together. Existing public single-record writers cannot commit an early
successor graph/projection/pointer step on their own. Do not add a caller-set
`validated`, `accepted`, `ignore_run`, or bypass flag. Do not synthesize an action
from `created_by`, workspace identity or a supplied origin hint.
The composite completes every applicable action/provenance/index/pointer write
before returning, without committing its caller's UoW or handing out reusable
authority for later public writes. Keep introduction/continuation, publication/
selection and completed advancement as distinct semantic entry cases, sharing
pure derivation and store primitives rather than a caller-directed mutation
object. Implicit identity persistence uses that same internal write path;
constructing another projection store on the connection grants no authority.
Exact immutable storage replay compares actual stored material and adds no
binding, pointer transition or permission.

All paths below refer to existing Operations owners. Legacy-only compatibility
does not exempt a transition whose **old** current/desired material contains a
receiver. Both sides matter, including successor-to-empty or successor-to-legacy.

| Supported entry | Proposed disposition | Required evidence / writes |
| --- | --- | --- |
| `WorkspaceService.create` normal empty bootstrap | Enforce existing empty bootstrap. | No receiver introduction or acceptance. |
| `PostgresWorkspaceStore.create` populated receiver bootstrap | Refuse. | A pointer-bearing record cannot create accepted lifecycle truth. Empty/legacy-only behavior retains its existing contract. |
| `PostgresGraphTopologyStore.save` direct successor save | Refuse single-record admission. | Save-only commit must not leave a receiver graph without complete original graph/action/binding admission. Action-owned composite persistence is the positive path. |
| `PostgresRealizedGraphProjectionStore.save` direct successor save | Refuse single-record admission. | Existing source graph, matching digest or A guard alone is insufficient. Composite owner derives exact material/bindings. Preserve exact immutable storage replay only as evidence recovery. |
| `identity_for_authored` read/derive | Enforce existing read/derivation contract. | No persistence or lifecycle permission is gained by obtaining the in-memory projection. |
| Workspace desired setter/CAS, including implicit identity save | Refuse direct receiver-affecting selection without the complete action-owned operation. | Both current and desired expectations, exact permitted pending/current lineage, derived bindings and applicable scope exclusion. `_projection_for_source` implicit save is part of the same boundary. |
| Workspace current setter/CAS | Refuse direct receiver-affecting acceptance. | Only existing advancement proof plus atomic pointer/event/action and acceptance/retirement can perform this transition. Inspect old membership when replacement is empty/legacy. |
| `GraphAuthoringService.set_desired_graph` standalone | Refuse successor admission or receiver-affecting selection. | It has no operation action/session witness. Preserve ordinary legacy-only authoring; never invent provenance. |
| `DesiredGraphCommandService` / `set_desired_graph_in_unit_of_work` command-owned path | Enforce complete introduction/continuation and combined CAS. | Existing command auth/idempotency, real action/session, exact material, B introduction/bindings and pointer in one UoW. Standalone helper invocation remains subject to the refusal above. |
| Draft create/revise | Enforce complete introduction/continuation. | Exact live head/original provenance where pending; graph, actual identity projection, draft/revision/head, real action, claims and bindings atomic. |
| Draft select | Enforce complete selection law. | Current/desired tuple plus selected live-head context; an arbitrary old revision is not pending authority. Same transaction for projection/bindings, pointer and action. |
| Draft tombstone | Enforce existing reference/ownership checks under L. | Preserve introductions/history; never record acceptance or retirement merely from tombstoning or omission. |
| Saved deployment preparation | Enforce reference-publication law. | Pinned saved revision/current/desired context; no graph minting. Validate lifecycle eligibility before new durable preparation while retaining original replay. |
| Inline `DeploymentProgram.prepare` | Enforce via the action-owned graph command. | Forward its existing current expectation into admission; later plan refusal cannot repair an earlier committed stale graph selection. |
| Activity planning | Enforce lawful pinned graph references. | A plan is intent, not receiver introduction/acceptance. Preserve planning-versus-tombstone exclusion and no effect permission. |
| Generic desired realized publication | Enforce exact material and lifecycle law. | Retain authored source and real publication action; validate each realized binding and pinned current/desired tuple, then projection/CAS/action/bindings together. |
| Gateway overlap/retirement publication composition | Enforce through the same publisher with prepared outer prefix. | Preserve A's K→L→session/workspace/rotation order; no late acquisition inside publication and no exemption for internal callers. |
| Fresh execution admission | Enforce complete live lifecycle plus original plan/approval association. | Derive complete execution scope accounting for every supported profile, request/action/index atomic. Matching original replay does not become new authority. |
| Direct execution request insertion | Enforce derived accounting and complete applicable lifecycle, or refuse if required context is absent. | No caller-written scope list or empty default. Exact API composition remains to be settled with public-store contract. |
| Claim/open, start/resume, retry, lease takeover with resumed execution | Enforce fresh eligibility under L before existing request/run locks where they can reactivate affecting work. | Revalidate original association and current lifecycle. Exact historical replay follows original fencing, without reminting or renewed permission. |
| Begin compensation / compensation attempt start | Enforce existing recovery authority plus lifecycle serialization where starting fresh affecting work. | Original source/inverse/program/material-side proof, no new compensation semantics. A completed run label is not disposal. |
| New effect attempt start / coordinator dispatch | Enforce request/run eligibility and indexed scope coverage. | A new or resumed effect cannot bypass stale lifecycle admission through a direct interpreter service. Whether L is needed at each outer path depends on the reactivation proof below. |
| Pure fold/reconcile/reload of original effect evidence | Preserve original evidence contract; no new permission. | Do not add lifecycle locking indiscriminately to history recovery. A path that also permits a fresh effect belongs to the fresh row above. |
| Current advancement | Enforce existing complete success and association plus lifecycle acceptance. | Conditional own-run handling is internal to verified proof. Pointer/event/action/first acceptance/retirement share one UoW. Older unrelated work is not ignored. |
| Read/history/original completed-command replay | Preserve exact historical profiles, bytes and receipts. | No backfill, identity minting, new permission, or history rewrite. D's new authoring read surface remains a separate child. |

The fresh reactivation row is an obligation, not a claim that its exact service
enumeration is finished. For each transition, the final matrix must prove one
of: serialized current revalidation; inability to reactivate; or continued
conflict while that capability remains. A request marked `CLAIMED` after a
successful run is not automatically capable of starting a second run: current
`lifecycle.py:_claim` requires `QUEUED` and no retained run, while failed-run
retry requires `FAILED`. Conversely, absence of an attempt does not make queued
work clear. Both query-first and reactivation-first schedules need target laws.

## Profile-independent scope derivation

A scan driven only by B's receiver binding rows is incomplete: legacy graphs
and executions have no such rows. Original plan operations, both pinned
graph/projection identities, and actual retained effect intent remain the
scope evidence. No historical receiver ID is invented.

Source anchors confirmed at `2a1bf73`: Core `planning/activity_plan.py` defines
node/runtime targets and compensation material sides; `planning/codec.py:82`
retains the closed activity array; `topology/graph.py:105` retains node/runtime
placement independently of configuration profile. Operations
`records.py:561` retains both plan sides, `postgres/activity_history.py:352`
persists their workspace/source association, and
`effect_attempt_intent_evidence.py:201` validates original operation/source/start
evidence. `runtime_effects.py:_material_graph` selects base for node/runtime
stop/remove and desired for start/reconcile; compensation must use its recorded
base/desired material side rather than infer from today's graph.

Proposed closed internal classification (not a new public activity language):

| Operation family | Receiver-scope coverage |
| --- | --- |
| Start/stop/remove/reconcile node | Node-wide at original material's runtime/node. Do not narrow a whole-node effect to only the configuration's selected socket. |
| Start/stop/remove/reconcile runtime | Runtime-wide; covers requested receiver scopes within that runtime. |
| Wait-for-health / management bootstrap observation / node health observation | Positively classified observation, no direct create/remove scope. Retain independent observation/execution rules. |
| Add/switch/remove socket connection | Current supported adapter records topology only; separately planned node start/reconcile/stop retains its own coverage. |
| Allocate/remove public ingress | External non-receiver effect under ingress owner; not read-only. Preserve independent ingress/run-acceptance rules, without turning C into all-resource conflict tracking. |
| Review change | Non-executable review requirement; preserve existing refusal. |
| Destroy data resource / unknown or unaccounted variant | Do not call harmless or silently emit empty scope. Fresh executable use needs a supported scope/effect contract or refusal; retained attempted evidence without that coverage makes accounting unavailable. |

The socket classification is anchored by
`test_runtime_interpreter_dispatcher.py::test_socket_connection_operation_is_recorded_without_runtime_effect`
and `coordinator.py:_socket_connection_outcome`. Ingress routes through
`ActivityExecutionDispatcher` and `ingress_realization.py`, with separate
connector node activities. The current runtime translator's `_node_target`
does not cover `DestroyDataResource`; no receiver destruction semantics are
invented here. These conclusions describe the named supported owners, not an
arbitrary injected adapter with different effects. Such an adapter cannot gain
coverage merely by sharing a method name.

The union preserves distinct original runtime coordinates: for a node moving
from runtime A to B, derive every affected forward side and the recorded BASE
compensation side. Same node names do not collapse those coordinates. Before
any fresh forward or compensation dispatch, its actual retained intent/material
scope must be a subset of the immutable admitted coverage. Wrong material side,
relocation drift or an unaccounted variant refuses; it cannot expand the index
or reinterpret today's graph as the original source.

## Proposed bounded execution index, not yet selected schema

Preferred option for review: one immutable execution-owned scope relation
derived when an existing execution request is admitted. B still owns only its
two graph relations; this additional C relation would index existing execution
intent, not receiver identity or lifecycle authority. No independent service,
registry, lifecycle enum, public scope-writing DTO or repair subsystem is added.

Conceptual row fields are `request_id`, `workspace_id`, `scope_kind`
(`node`/`runtime`), `runtime_id`, and nullable `node_id` (null only for runtime).
Distinct coverage is derived from all relevant forward and compensation
operations against the retained plan sides. A mandatory immutable derived
completeness witness records valid coverage, including a legitimate empty set;
a request count/digest is the preferred representation to refine. Exact
names/keys/header placement remain proposed, not frozen SQL. The final design
must specify its write-once and validation law. It cannot be a caller assertion
that zero rows means safety.

Completeness belongs at every supported request-insertion path, in the same
transaction as request/action and under the lifecycle guard. Direct stores must
derive and validate the complete source or refuse. Current-schema validation
must compare derived coverage with retained source without mutation. Negative
query soundness is an inductively maintained supported-writer invariant:
complete admission creates immutable coverage, and no supported writer may
alter/delete it or its original source association. Name and enforce those
public-store refusals in the final API. An FK or count/digest alone cannot prove
that all required rows exist. A bounded lookup refuses missing/mismatched
evidence on encountered candidates; it cannot discover a wholly omitted or
misindexed request that never enters its reverse query. Do not claim that the
header solves that discovery problem. Exact-current source revalidation and
supported-store invariants define the proposed guarantee, not hostile raw-SQL
corruption detection. Stronger online discovery would need an explicit separate
design choice. No lazy backfill or repair is introduced.

Two equality-prefix B-tree candidate paths are sufficient for the proposed
node/runtime scope shape: `(workspace_id, runtime_id, request_id)` for runtime
rows with a `scope_kind = 'runtime'` predicate, and
`(workspace_id, runtime_id, node_id, request_id)` for node rows with
`scope_kind = 'node'`. These must be disjoint partial-index/query predicates
(or equivalent equality keys), so the runtime-wide branch cannot scan unrelated
node-only history. Partial uniqueness can also prevent duplicate coverage for
one request. Query each prefix with a candidate cap plus one **before**
combination; deduplicate only that bounded union and charge decoding once per
request against a single shared budget. Do not join all runs/events before the candidate cap or filter
to current graph, new receiver ID, active status or latest run.

For each bounded request candidate, use the existing request/attempt index to
read every retained run up to a shared total budget, then the attempt primary
key for bounded complete attempts. Read original intent/start/latest event and
direct **or** recovered outcome evidence through exact keys. Compensation
source/inverse links and acceptance event/action pairs retain their own exact
association checks. Existing advancement receipt partial indexes support the
two-candidate ambiguity check. No direct-outcome inner join may erase recovery
history. Do not use the existing unbounded `runs_for_request` or
`events_for_run` methods as the new scope lookup.

Numeric caps are not chosen yet: candidate requests, total runs/attempts/events,
graph/plan/intent bytes and total transported bytes must be frozen together
after realistic positive fixtures are checked. Existing 1 MiB intent and graph
validation conventions and bounded history projections provide precedent, not
automatic aggregate budgets. Every nested query needs an index-supported
prefix and limit-plus-one overflow sentinel; per-branch caps alone must not
hide unbounded total fanout. A query returning too much relevant settled
history can also refuse: without a separately justified pruning proof, that
is an explicit capacity limitation rather than permission to discard history.

Alternative: a complete bounded indexed query over original typed plan/graph
descriptors without another relation. No such bounded-work proof has yet been
demonstrated. A workspace scan with JSON expansion and a final `LIMIT`, or a
receiver-binding join followed by an ad hoc legacy fallback, is not accepted
as that proof. Prefer the smaller proven design after review, not fewer tables
at the cost of missed candidates.

## Remaining decisions and proposed implementation slices

The next review should settle these finite questions:

1. Choose the execution index alternative; freeze exact columns, completeness
   witness, FK/index/catalog contract, caps and bounded query shapes.
2. Freeze per-status/per-attempt accounting and exact supported disposition
   proof. C-N9 itself is settled; no general recovery mechanism is proposed.
3. Name the action-owned composite persistence interface and precise direct
   store refusal behavior. Account for implicit projection-store construction,
   same-UoW state changes and deferred insertion order; no reusable authority
   token or bypass flag.
4. Enumerate the fresh reactivation service branches and show how their prefix
   prevents a negative lookup followed by conflicting work. Preserve original
   replay and pure evidence folding.
5. Complete command DTO/fingerprint/framework-neutral mappings for combined
   current/desired CAS and permitted pending context; classify focused target
   laws before source release.

If these interfaces exceed one coherent C PR, proposed reviewable slices are:
execution-scope derivation/storage/lookup; graph/draft/publication admission and
direct-store closure; execution/reactivation/advancement integration. These are
planning seams, not new issues or independent public activation. North decides
the final sequence after interface review; B's reviewed staged source still
precedes dependent C source, and all slices remain on the non-release collection
until the complete B+C matrix and owning suite pass together.

Security/data/history: no executed changes. The main risks are incomplete legacy
coverage, caller provenance becoming authority, unsupported effects silently
classified as empty, and a reactivation race after apparent clearance. Each is
an explicit acceptance obligation. Preserve exact-schema object-free install /
current verification / other-state refusal; no migration, backfill, reset,
provider effect, secret exposure or deletion. Validation for this proposal is
documentation review and diff checking only.
