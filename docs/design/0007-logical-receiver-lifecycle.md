# Logical receiver lifecycle and configuration delivery

Status: proposed contract for G0 review; not implementation or effect authority.
Governing issues: [1880](https://github.com/OpenJ92/control-plane-kit/issues/1880)
and [1879](https://github.com/OpenJ92/control-plane-kit/issues/1879).
Baseline: Core/Operations `6b2d173`, SDK `e19b7ed`, Interpreters `39118d7a`,
Servers `66bd9ce`, Secrets `7a26fdc`.

This specification chooses the public semantics and owner boundaries for the
implementation children. Examples are non-executable values, not new APIs that
already exist. It reuses the [recorded investigation](https://github.com/OpenJ92/control-plane-kit-servers/issues/237#issuecomment-5860996500)
and [team choice](https://github.com/OpenJ92/control-plane-kit-servers/issues/237#issuecomment-5861072074).

## 1. Objects and laws

The graph is the complete requested deployment. A logical receiver is one
continuing managed endpoint. Its installed configuration, present permission,
and process observation have different meanings:

| Object | Identity / evidence | Owner |
|---|---|---|
| Requested deployment | authored graph and realized projection IDs | Operations graph store |
| Logical receiver | receiver ID plus workspace/runtime/node/socket scope | Core value; Operations graph lifecycle |
| Configuration | selected artifact bytes/digest, slot and declaration | Core graph; provider delivery interpreter |
| Permission | current actor, selected graph/plan, approval, eligible keys and secret authorization | Operations policies/workflows |
| Exact work | immutable request/preparation/attempt and original interval | Operations history/execution |
| Observation | bounded provider or authenticated endpoint result | interpreter, recorded by Operations |

The core law is:

```text
current approved context authorizes request Q addressed to receiver R
R verifies Q against independently installed scope, declaration and trust
Q's graph context is not R's identity
```

Identity does not prove authorization, installed image/configuration, physical
continuity, revocation, or exactly-once application commands. Health does not
adopt resources or settle uncertain mutations. A compatible old process can
accept an already-issued bounded grant during replacement; effect verification
must establish installation separately.

### Lifecycle policy

| Transition | Receiver ID | Required distinction |
|---|---|---|
| Unrelated graph revision | retain | new graph authority, same selected receiver |
| Ordinary restart | retain | fresh bounded observation; no process-epoch promise |
| Continuing endpoint image upgrade or rollback | retain | approved replacement, not logical recreation |
| Application/configuration/key/declaration change | retain | exact new material/declaration/trust, explicit activation |
| Gateway routing change | retain own receiver ID | route configuration may require replacement |
| Workspace/runtime/node/selected socket scope change | fresh | first supported policy does not relocate identity |
| Accepted logical deletion, then new deployment | fresh | identical old names/content do not revive identity |
| Exact prepared retry | original value | no generation, reauthoring or key reselection |

Desired omission, draft retirement, failed replacement, and ambiguous provider
removal are not accepted logical deletion. A new graph that contains the same
logical scope cannot replace its accepted receiver ID merely to evade pending
work: explicit accepted removal must end that deployment first. A scope-changing
replacement may remove the old endpoint and create the new endpoint in one
approved plan, with both identities and effects explicit.

## 2. Versioned public contract

### Target and authority values

Introduce `NodeControlReceiverTarget` alongside historical `NodeControlTarget`.
Its exact descriptor fields are:

```text
workspace_id          existing public workspace reference
runtime_id            existing public runtime reference
node_id               existing public node reference
provider_socket_name  existing public provider-socket reference
receiver_id           32 lowercase hexadecimal characters, generated once
```

The constructor validates each reference role and the receiver ID exactly.
The scope is the four reference fields. New IDs use the existing injectable ID
facility with cryptographically random 128-bit production values; tests supply
deterministic IDs. Randomness prevents accidental collisions, not unlawful
reuse: section 4 provides durable validation. The entire target is compared
with local installed target; audience text alone is never sufficient.

`NodeControlAuthorityContext` has exactly `authored_graph_id` and
`realized_projection_id`, both existing bounded public references. It names the
graph selected by the operation, not a promise the receiver can read current
controller state. Operations checks current lawful association and the original
plan/attempt independently. Receivers verify context equality between the exact
signed grant and request, but do not compare it with a startup graph revision.

The new target has no `graph_revision`. No old field is reinterpreted. Plan,
run, attempt, actor and approval IDs remain in their existing Operations
records; they are not all copied into every receiver credential.

### Profiles and closed field transformations

The table specifies exact successor profile strings. Existing declaration V2
has another meaning and is not renumbered merely for this change.

| Family | Historical representation | Selected successor |
|---|---|---|
| Common wrapper configuration | `workload-node-control-configuration.v1` | `workload-node-control-configuration.v2` |
| Health request / workload grant / result | corresponding `workload-node-health-read-*.v1` | corresponding `workload-node-health-read-*.v2` |
| Health gateway transit grant | `gateway-node-health-read-transit-grant.v1` | `gateway-node-health-read-transit-grant.v2` |
| Surface-description request / workload grant | corresponding `workload-node-control-surface-read-*.v1` | corresponding `workload-node-control-surface-read-*.v2` |
| Surface-description result | existing V1/V2 result profiles | `workload-node-control-surface-read-result.v3` |
| Variable state/control request | historical unprofiled request | `workload-node-control-request.v2` |
| Variable state/control workload grant | historical unprofiled grant | `workload-node-control-grant.v2` |
| Variable state/control result | historical unprofiled result | `workload-node-control-result.v2` |
| Variable state/control gateway transit | `gateway-node-control-transit-grant.v1` | `gateway-node-control-transit-grant.v2` |
| Health preparation | `health-effect-preparation.v1` | `health-effect-preparation.v2` |
| Control intent | `node-control-intent.v1` | `node-control-intent.v2` |

The historical command formats were unprofiled; the suffix does not invent a
previous literal `*.v1` tag. Historical decoders retain their exact old shapes.
New live decoders select only the new family profile; they never probe old
formats after failure. Declaration codecs/identities retain their existing
V1/V2 meanings; the declaration hash still identifies the actual declaration.

Closed transformations from the baseline descriptors are:

- Configuration: keep `profile`, `declaration`, `verifiers`; replace `target`
  with the new target and remove sibling `runtime_id`, now in target. No
  authority context belongs in installed receiver configuration.
- Every new request: keep existing operation-specific fields; replace target;
  add `authority_context`. Remove sibling runtime if present. Command requests
  additionally have explicit `profile` and `declaration_identity`. Payload,
  precondition and idempotency semantics do not change.
- Every workload grant: preserve issuer/key/purpose where already present,
  audience, request ID/digest, temporal/JTI and operation-specific bindings;
  replace target; add the exact request's authority context. Remove duplicate
  runtime if present. Command grants add explicit profile and declaration
  identity. The complete new request, including context, is canonically hashed.
- Health/command transit grants: replace target; add authority context;
  replace `gateway_node_id` with `gateway_target`, a complete independently
  selected `NodeControlReceiverTarget`. Remove the old command-only sibling
  workspace/graph revision and health sibling runtime, all represented by the
  new values. Keep original attempt/request/interval/key/purpose bindings.
  Workload and gateway target workspace/runtime must agree for this supported
  local management path. Gateway own target is checked against local config.
  `gateway_target.provider_socket_name` is the gateway's configured common
  own-receiver control endpoint, not its transit socket. Independently preserve
  the exact graph-selected transit socket, protocol, ingress and management-path
  relationship checks. Own-receiver identity neither substitutes for transit
  admission nor requires those distinct sockets to have the same name.
- Results: retain operation-specific outcome/state and original exact request
  ID/digest binding under the new result profile. Surface-result V3 uses the
  existing declaration-appropriate payload semantics; it does not rename
  variable registry coverage or add new capabilities. Command results add the
  profile to their historical result variant shape.
- Preparations/intents: new profile encloses the exact new requests/grants and
  preserves existing plan/projection/attempt/key/event witnesses. Redundant
  witnesses must be validated against canonical payload, never independently
  mutable alternative truth. Record owners retain explicit old-profile readers.

All new structured fields are included in the relevant canonical bytes and
digests. RFC8785 canonicalization, signature algorithm, purpose separation,
maximum intervals and existing per-family aggregate byte limits remain. A
maximal input that no longer fits is explicitly refused; C1 tests reachable
positive bounds instead of silently expanding limits. Private credential and
endpoint values remain prohibited in public references/errors/history.

Workload audience remains the existing node/socket audience; gateway audience
keeps existing workspace/node routing semantics using `gateway_target`. These
strings are routing selectors, not complete authorization scopes. Every exact
target, declaration, request/context and purpose check remains mandatory.

### Ownership matrix and supported surface

| Protocol | Core | Receiver | Authority/history | Signing/transport/composition |
|---|---|---|---|---|
| Health | C1 #1881 | SDK43; Secrets41; Servers237 | O2 #1883 | Interpreters175; later148/181 |
| Surface-description capabilities/status | C1 #1881 | SDK43; Secrets41; Servers237 | existing direct caller boundary only | migrate existing SDK/product caller paths; no new Operations command |
| Variable READ_STATE / APPLY_COMMAND | C1 #1881 | SDK43 and actually declared product variables | O3 #1884 | Interpreters175 for supported consumers; actual Servers composition retains its owner |
| Config/target association | C1 value/codec | SDK local snapshot | O1 #1882 | authoring Servers238, delivery Interpreters177 |

Surface-description read is not READ_STATE. No maintained Operations surface
signer/route is invented to make the table symmetrical. C1/SDK migrate its
existing pure/direct protocol, while any composed direct caller supplies the
explicit authorized context; absence of a controller route remains explicit.
Interpreters175 must not invent a second command signer where Servers owns the
actual supported adapter. F0 maps protocol coverage; it does not add capabilities.

## 3. Pre-wire authoring and exact replay

Servers238 supplies one maintained authoring transformation:

```text
ordinary application graph + authenticated pinned authoring context
  + public verifier snapshot + explicit retain/introduce intent
    -> complete concrete graph + local immutable preparation artifact
    -> submit exact graph -> inspect/approve plan -> execute selected values
```

The authenticated context contains workspace, accepted current authored and
realized IDs, desired authored/realized IDs and desired generation, plus the
exact public target/declaration/configuration association for requested scoped
receivers. It is a bounded read owned by O1, subject to existing graph/key-read
permissions. An explicit pending context adds the selected desired tuple or
live draft ID/head revision and introducing action reference. Redacted graph
readback is not treated as a lossless source. No private verifier key, token,
provider credential, or arbitrary private application configuration is returned.

The authoring function retains existing IDs only through that pinned context;
it selects all public keys/configuration before submission. New receivers get
fresh IDs. The complete graph includes every actual gateway, ingress, route
binding and artifact: no topology expansion, logical receiver-ID generation or
public verifier selection happens inside an effect or after approval. Physical
configuration allocation IDs follow the distinct prepared-effect rule in
section 6. A proposed authored graph ID solves record-ID
availability; a reused saved graph ID is not a fresh receiver generation.

Write the generated graph to a private immutable local preparation file using
write/flush/atomic rename before recording its canonical digest and request in
the versioned journal and before first send. A journal references only a fully
persisted artifact. Orphan local preparation files after a crash are not runtime
effects; bounded inspection can report them. Missing/corrupt/mismatched files
refuse resume rather than regenerate. Preserve original user source and its
hash; existing source-change refusal remains. The journal profile is
`topology-client-preparation.v1`, with exact generated-file identity/digest and
original source identity in addition to the existing command replay metadata;
it contains no bearer tokens. Existing journal decoders retain old semantics.

Two explicit commands have different meanings:

- **Instantiate saved application intent:** use its ordinary graph description
  to author a new concrete graph against current pinned context. Continuing
  endpoints retain IDs; deleted endpoints receive fresh IDs. Historical bytes
  remain unchanged. This is not a new template language or the whole future
  [restore feature1774](https://github.com/OpenJ92/control-plane-kit/issues/1774).
- **Retry prepared deployment:** read the exact saved preparation and resend
  the original canonical graph/request. No random generation, key reselection,
  source rewrite, or changed interval. A lost CAS is a conflict, not permission
  to reauthor. Rebase is a separate explicit preparation with new request ID.

O1 API admission validates the complete supplied graph and claims. It does not
trust the client simply because it used the maintained authoring function.

## 4. Bounded graph-owned lifecycle evidence

### Storage choice

O1 adds normalized graph-store indices, not an independently writable receiver
registry or runtime-membership service:

```text
graph_receiver_introductions
  PK(workspace_id, receiver_id)
  UNIQUE(receiver_id)                          # no cross-workspace recycling
  immutable runtime_id, node_id, provider_socket_name
  immutable introducing_graph_id, introducing_action_id
  immutable introducing_draft_id?               # null for inline selection
  first_accepted_action_id?                    # write once
  retired_action_id?                           # write once, terminal

graph_receiver_bindings
  PK(workspace_id, graph_id, realized_projection_id, node_id, provider_socket_name)
  receiver_id -> graph_receiver_introductions
  selected_configuration_digest, declaration_identity
```

The global receiver-ID uniqueness constraint rejects cross-workspace copying
as a generic conflict without disclosing the other workspace. Introducing
graph/action, acceptance and retirement references are validated
workspace-owned foreign keys into existing durable graph/history records. A
claim row is written only with its original graph/action transaction; bindings
are projections of exact immutable graph material, never caller-written facts.
Projection publication validates bindings too. A stored lifecycle enum is not
needed: pending means no first acceptance, accepted means first acceptance and
no retirement, retired means a retirement witness. Graph membership still comes
from the selected graph, not from this summary. An accepted ID missing from the
current graph cannot be retained merely because its first-acceptance row exists.

Introduction reservations survive draft retirement and graph-history display
filtering. The introducing/accepting/retiring records cannot be physically
deleted while referenced by these integrity indices. No garbage collection or
history deletion API is added here. Bounded point lookup by `(workspace,R)`
and indexed graph/projection membership replace unbounded ancestor scans.

### Admission rules

1. Authenticate/authorize and check exact command idempotency first. Existing
   matching completed command replays its original result; that does not grant
   a new execution permission.
2. Lock the workspace lifecycle and compare both current and desired pinned
   tuples, including current realized projection and desired generation. A
   mismatch makes no graph/index/action write.
3. Validate exact graph bytes, registered slots/products and every new-profile
   target/configuration binding. Duplicate ID use in another scope is invalid.
4. If no introduction row exists, reserve `(workspace,R)` with its exact scope
   and introducing graph/action in this same transaction. A current accepted
   endpoint at that scope cannot be relabeled without its explicit lifecycle.
5. If a row exists, require the same scope and no terminal retirement. An
   accepted continuation must be present in the pinned accepted current graph.
   An unaccepted continuation must be present in the exact pinned selected
   desired graph or the same still-live draft's current head, with its original
   introduction binding. The authenticated context identifies which case;
   arbitrary historical graphs, caller-provided origin IDs and ancestor search
   are not evidence. Initial selection of the introducing live draft is allowed
   through that exact draft head. A copied abandoned introduction cannot become
   a new claim. Exact original command replay remains readable.
6. Persist graph, normalized bindings, claims, action and desired/draft CAS
   together or roll them all back. Immutable source bytes are unchanged.

Omission from a desired graph leaves an introduction reserved, not retired.
Once it is neither accepted-current nor the explicitly permitted live pending
head, a new authoring command cannot recover it by copying an old graph. Use a
fresh instantiation. Retiring a draft never retires an accepted receiver.

### Execution and advancement races

The same validator runs at execution admission and accepted advancement, not
only graph writes. Approval of an old plan does not reserve permission forever.
Execution checks the plan's original current/desired projection association and
the live lifecycle facts. A plan based on a superseded accepted endpoint cannot
unretire it. Original attempts remain inspectable without becoming executable.

On accepted advancement, compare old current and completed desired bindings:
newly accepted IDs acquire their first-acceptance witness; IDs absent from the
new current graph acquire a retirement witness only when existing advancement
laws prove the required removal completed. Uncertain or failed effects prevent
that advancement and retirement. Same ID with changed configuration is a
continuation; scope changes remove the old ID and accept the new ID. These
index updates, current-pointer CAS and advancement history share one UoW.

Serialize lifecycle mutations with a workspace-scoped transaction advisory
lock acquired before lifecycle row reads/writes. The total order is command
idempotency guard -> workspace lifecycle guard -> execution request row -> run
row -> attempt row -> session row if required -> workspace row -> graph/index
rows in deterministic ID order; omit only categories not needed by that
operation. The idempotency guard is not an execution request-row lock. No path
may acquire the lifecycle guard while holding a later-order row. All graph
selection/publication, execution admission and advancement paths that can change
this lifecycle must take the same guard; no route-specific exemption. O1's
source dry run must map every affected existing path to this order before code.
A known incompatible path is an explicit contract hold for review, not a
contradiction deferred to testing. Actual Postgres tests then exercise the
agreed order and rollback, rather than substituting fake-store behavior.

Add an indexed bounded lookup over the existing execution owner for all
unresolved requests/runs affecting each stable workspace/runtime/node/socket
scope, including superseded and abandoned graph bindings. Do not restrict the
search to currently selected graph IDs or the newly supplied receiver ID. Its purpose
is to refuse a conflicting reintroduction/scope change while creation/removal
is active or uncertain, not to classify provider reality from graph state. The
query returns a bounded conflict (including overflow), never a partial list
interpreted as complete. Execution admission participates in the lifecycle
guard, preventing a new conflicting claim between lookup and graph commit.
Ordinary authoring of unrelated intent remains possible; an unresolved effect
cannot be bypassed by choosing another receiver ID at its affected scope.

### Exact-schema policy

This index design changes the exact Operations schema baseline. Under
[ADR0008](../adr/0008-transactional-data-engineering-policy.md), O1 installs it
only in an object-free owned namespace and verifies an already-current one.
Other owned schemas produce reset-required refusal **without executing a
reset**. Earlier issue language entertaining additive upgrades/backfills is
superseded: no inferred in-place migration or invented receiver IDs for old
records is allowed.

A fresh test namespace can use the new baseline. Existing namespaces/history
remain preserved under compatible tooling and are held from unsupported new
execution. If valuable data must move, a separately reviewed export/reset/import
plan is required; it is not part of F0 and is not an automatic prerequisite for
a fresh namespace. Any importer must preserve original profiles and may not
fabricate introduction/acceptance witnesses. A fresh namespace does not excuse
abandoning old live resources or unresolved effects.

## 5. Historical replay, new authority and cutover

New-live profile selection is explicit across Core, Operations, SDK,
Interpreters, Secrets and Servers. Existing history readers retain original
profiles, canonical bytes, digests, key registrations, source/attempt/event and
validity intervals. New-profile constructors do not reinterpret old graph
revision fields. A new binary's historical codec support does not imply its
new exact schema installer can open an old namespace.

Reconstructing a preparation is effect-free. Signing/dispatch separately
rechecks current actor/key/secret/runtime/approval/fence eligibility and the
original interval. Renewed leases do not extend it. Key revocation can stop
new signing while an already-issued compatible grant remains valid until its
expiry in installed trust. SDK APPLY replay remains process-local.

The later Servers181 cutover plan inventories selected profiles, active runs
and attempts, uncertain outcomes, running receivers and retained bootstrap
resources. Any unfinished old-profile action or live receiver without an
explicit disposition holds cutover. Authorized old tooling may finish or cancel
where existing policies permit; cancellation is not provider rollback. No
automatic bulk cancel, history deletion, profile coercion, token replacement,
adoption or replay is proposed. Keep old records and resource ownership until
an approved disposition is verified. No permanent dual live stack is required
without a concrete separately reviewed continuity requirement.

## 6. Configuration material and explicit cleanup prerequisite

### Existing limitation and new required owner slice

Interpreters `docker/runtime.py:614–626` removes a changed container before
`:1040–1071` may reject old bytes in the same-named configuration volume.
`:1145–1163` removes only a container. Therefore Interpreters177 cannot invent
volume deletion policy inside its implementation.

Required bounded prerequisite, for North to add before affected source release:
**CONFIGURATION.CLEANUP.CONTRACT — represent exact configuration cleanup intent,
approval and history**, owned by Core/Operations on the current roadmap. It
depends on C1's scope values and precedes Interpreters177 provider implementation.
It is not a generic garbage collector, recovery engine or new runtime registry.
Publishing/implementing it is outside this documentation PR.

Core's planning/effect language introduces the closed provider-neutral value
`ConfigurationInstanceRef` with exactly `allocation_id`, workspace/runtime/node
scope, `artifact_id`, `target_path`, `media_type`, `file_mode` and full SHA256
content digest. All selected slot/material contributes to identity. It carries
neither Docker volume names nor receiver ID as a substitute for content.

`allocation_id` identifies one immutable physical allocation lifetime, not the
logical receiver. The existing effect-preparation owner generates and persists
it in the exact prepared effect request/intent, covered by that request's
canonical fingerprint before first dispatch, under the approved exact scope/
slot/content allocation operation. Original-request retry reuses that exact
value; it cannot be reminted between preparation and provider call. Outcomes
reference the original prepared allocation rather than inventing one after an
uncertain effect.
Evidenced reuse carries the existing allocation reference; a genuinely new
allocation gets a fresh discriminator even if content is identical. No new
allocation registry or graph expansion is required: the original effect intent
and bounded outcome carry the reference. The interpreter includes this value
in the provider name and verifies the entire ownership/material association.
Operations refuses recreation of a terminally removed allocation; uncertain
allocation/removal is not permission to generate another or reuse its name.

This prevents an old approved cleanup from targeting a new same-content
allocation through name reuse. A content digest alone cannot prevent that
substitution. Cleanup references are fixed and inspectable before their own
destructive approval; preparation cannot expand that approved candidate set.

The minimal new cleanup intent names an exact bounded set of these refs, each
with its original graph/projection and recorded effect-attempt provenance.
Operations resolves candidates from selected before/after graph material and
recorded staging outcomes, validates caller authority, produces an inspectable
destructive plan, records approval, and admits execution with current runtime
authority. There is no prefix scan or implicit post-health cleanup selection.

The prerequisite freezes exact new activity/request/result codec spellings and
operation-profile membership before its source work; the semantic closed fields
above are fixed by F0. It must expose an explicit `CleanupConfigurationInstances`
activity with request identity, approved candidate set/provenance and runtime
scope. Each candidate outcome is removed, already absent, retained/in-use,
refused, or unknown, with bounded evidence and original attempt correlation.
No provider call is hidden in graph construction or a status projection.

### Safe staging and optional cleanup are different steps

Reconcile may stage and verify its exact selected replacement artifacts under
the already-approved replacement intent before removing old compute. Changed
bytes use distinct owned immutable instances; equal full material can reuse a
verified existing allocation reference. A preparation failure preserves old compute and records any
staged residue/unknown boundary. Nonmatching populated material is never
overwritten. Subsequent replacement follows only its existing approved effects.

Successful replacement requires supported provider postconditions; an old
same-identity healthy process is insufficient. On failure, retain material
needed for diagnosis/recovery and report the known phase. No automatic rollback,
adoption or compensation is authorized by this ordering change.

Cleanup is a separate explicitly approved action. Exact references and
observed non-use do not grant deletion authority. Before each deletion require
the approved candidate/provenance, current ownership, no selected current or
active-execution reference, and a fresh provider mount/use check. The execution
owner reserves the approved cleanup candidate while dispatch is active; other
CPK dispatches cannot start using it until cleanup's recorded outcome resolves.
Provider deletion is non-forced and must retain its own in-use refusal. The
database reservation does not prevent an external administrator attaching a
volume; the supported policy does not claim atomic compare-delete against a
hostile trusted administrator. If safe discrimination or non-use cannot be
established under supported concurrency, refuse and retain. A stale
or ambiguous observation, concurrent/shared reference, foreign resource,
secret volume or retained-data volume refuses/retains the candidate. Partial
cleanup reports each known result; it does not automatically retry unknowns.

The prerequisite must specify reservation and conflicting-dispatch validation
inside existing execution request/attempt ownership, with bounded indexed
queries. This is an explicit missing owner contract, not an assertion the
current RemoveNode already supplies it. North's prerequisite issue and review
are required before I177 source begins; G0 may accept this narrowly named
prerequisite, while G2a cannot pass until its implementation is accepted.

### Material equality is not effect correlation

Interpreters176 separates the semantic resource-material digest from the full
operation/request fingerprint. Identical material under graphs A/B can permit
an authorized supported Reconcile reuse; it never proves a prior Start or new
attempt succeeded. Preserve original creation provenance and attempt/fence
checks. Include all effectful image, environment, authority delivery, artifact,
network and retained-mount inputs in material comparison; omit incidental graph
revision only from that predicate. Current configuration-bearing observer
refusals remain: a label digest is not installed-content evidence.

## 7. Law ledger and implementation handoffs

F0 is non-executable-scaffold. The following are exact existing test selectors
and successor law assignments, not claims that new tests already pass. Each
child inspects the assertions, designs its interface, then records focused
intended-red and owning green evidence. Frozen negative laws are retained.

| Law / class | Governing existing test identity | Successor owner and negative case |
|---|---|---|
| Retained infrastructure / isomorphic + new target law | Core `test_management_bootstrap_planning.py::test_retained_infrastructure_observes_without_restarting_or_reallocating` | C1/O2: A→B→C targets X; wrong graph authority/right X refuses |
| Exact configuration / isomorphic | Core `test_wrapper_configuration.py::test_shared_wire_round_trip_is_canonical_and_preserves_configured_facts` and `::test_artifact_selection_is_exact_unambiguous_and_ignores_unrelated_application_files` | C1/SDK43: canonical new profile; wrong/ambiguous slot refuses |
| Independent local binding / strengthened | Core `test_node_health_read_authority.py::test_local_bindings_are_independent_of_consistent_signed_claims` | C1/SDK43: incoming target cannot define local expectation |
| Surface protocol separation / isomorphic | Core `test_node_control_surface_read_authority.py::test_authority_languages_are_disjoint_in_both_directions` | C1/SDK43: surface-read cannot substitute for READ_STATE or health |
| Exact transit / strengthened | Core `test_node_control_transit.py::test_verifier_binds_every_claim_with_exact_precedence` | C1/Interpreters175: wrong gateway receiver/request/context refuses |
| Draft concurrency / strengthened | Operations `test_desired_topology_drafts.py::test_concurrent_distinct_revision_commands_have_one_winner_and_reject_stale_head` | O1: atomic introduction/CAS, retired-ID and stale advancement refusal |
| Replay before new admission / isomorphic | Operations `test_desired_topology_drafts.py::test_replay_precedes_allocation_and_later_session_product_and_head_admission` | O1/A1: original bytes recover; no remint or new dispatch authority |
| Canonical preparation / isomorphic | Operations `test_health_effect_preparations.py::test_closed_canonical_bytes_reject_parser_and_independent_payload_drift` | O2: old bytes unchanged; cross-profile coercion refuses |
| Original interval / isomorphic | Operations `test_postgres_health_signing_authority.py::test_saved_shared_interval_has_exact_edges_and_renewed_lease_does_not_extend_it` | O2/O3: renewed lease cannot extend expired grant |
| Control replay / isomorphic + strengthened target law | Operations `test_node_control_intents.py::test_exact_replay_precedes_clocks_graph_reads_and_key_rebinding` | O3: old request exact; changed current authority can deny signing |
| Material reuse / strengthened | Interpreters `test_docker_runtime_interpreter.py::test_reconcile_node_reuses_canonically_equivalent_environment_material` | I176: graph-only change permits supported reuse; wrong Start correlation still refuses |
| Material change / strengthened | Interpreters `test_docker_runtime_interpreter.py::test_reconcile_node_recreates_owned_container_when_material_changes` | I177: stage-before-remove, preparation failure preserves old compute |

Additional new-law cards required before source: copied abandoned pending ID;
retired ID via saved graph; conflicting concurrent introduction; acceptance
racing deletion; unresolved old-scope effect hidden by a superseded graph/new ID;
exact local-file replay after crash; fresh instantiation;
Secrets rejection before provider initialization; frozen controller plus
unlisted product; staging residue after acknowledged provider write; cleanup
reservation race, in-use/foreign/shared/unknown refusal and non-forced deletion.
These have no fabricated historical test identities. O1 owns actual Postgres
concurrency coverage; cleanup prerequisite owns plan/approval/history laws;
I177 owns provider call order and refusal. Tests do not duplicate owners' state
machines in Servers fixtures.

## 8. Teaching example and roadmap disposition

```text
A: app api, receiver X, config digest h1
   author complete graph -> admit introduction -> approve/run -> accept A
B: same api/X/h1, add worker Y
   retain X from accepted A -> introduce Y -> approve B
   health request: target X, authority B, exact request digest q
   signer checks original plan pins (accepted A, selected desired B/projection),
   current applicable authority/keys and target association; X verifies local X
   and signed q. Result stays under B's pending operation until lawful advancement;
   it records B + X + original attempt, not a claim about new image bytes
C: api/X/h2 (new public key bundle), worker Y
   plan exposes changed material; stage/verify h2 before replacement
   existing X identity stays; selected installed trust must cover the signer
D: accepted removal of api/X after approved verified teardown
   advancement retires X in graph-owned index
E: instantiate saved application description containing api
   new complete graph gets Z; copied X is refused
lost response to E: resend exact E/Z, never generate another receiver
```

If B changes gateway routes, those are explicit artifacts/effects; its own
receiver may remain stable. If an effect is uncertain, acceptance/retirement
does not pretend it succeeded. A process restart preserves X but not an
unsupported promise of uninterrupted observation or cross-process APPLY replay.

G0 requires Vale implementability, Meridian security/data/replay and Kepler
architecture review of the exact candidate, then North's recorded disposition.
The whole parent1879 blocks Servers237. Parent closeout is its accepted
foundation children plus G0/G1/G2a, including the required cleanup prerequisite
once North installs its dependency. Downstream G2b belongs to237/148/181 and G3
to225 under1813; they do not block1879 and create a dependency cycle.

Live225 remains the supported approved public-API scenario. A bounded retained
edit and protected health/history are distinct from the complete protocol laws
above. State/control, restart, configuration update and recreation enter its
live plan only when supported and explicitly selected; never invent a Hello
variable or confuse surface-read with READ_STATE. Original tunnel/DNS/token
and foreign resources remain preserved under that plan.

This documentation changes no schema, code, credentials, provider resources,
images or live state. Validation is link/reference/shape review and
`git diff --check`; no artificial red test or live claim belongs to F0.
