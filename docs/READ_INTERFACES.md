# Control Plane Read Interfaces

Current reads are operations-owned projections exposed through cpk-server.

## Projection Ownership

```text
Postgres stores
  -> private projection families
    - workspace_graph
    - operations_history
    - observations
    - authority_secrets
    - gateway_security
  -> InstanceReadService (explicit composition only)
    -> CpkServerReadService
      -> shared CpkServerOperationsApplication
        -> HTTP routes
        -> MCP resources/tools
```

The five private families own projection behavior according to the durable
truth they read. `workspace_graph` owns workspace, graph, operator, and control
surface views. `operations_history` owns sessions, actions, plans, approvals,
runs, events, risk, and recovery views. `observations` owns freshness
interpretation against an injected trusted clock. `authority_secrets` owns
runtime authority, delivery, ingress authority, secret-provider, and
secret-reference metadata. `gateway_security` owns gateway probes, delegation
signing-key inventory, and verifier configuration.

`InstanceReadService` is the public composition facade for these ordinary
projections. Its methods make
explicit one-hop calls to those owners; it does not implement projection
behavior, dispatch through reflection, or provide an alternate read API.

## Interface Law

The law is:

```text
interfaces expose the model; they do not define the model
```

## Workspace Administration And Current Request Authority

The Core HTTP/MCP contract also declares six workspace-local administration
reads: permission list, exact permission, permission history, managed-runtime
assignment list, exact assignment, and assignment history. They use the
AUTHORIZATION service role and ADMIN transport metadata. The canonical read
projection policy is `authorized-workspace-administration`; read-only safety
still applies. `requires_workspace_scope=True` constrains the target workspace,
not ordinary membership. The explicit `WorkspaceAdministrationPolicy` decides
administrator authority from the verified full principal identity.

`CpkServerWorkspaceAdministrationService` composes the existing workspace,
permission and managed-runtime command/read owners. Supply it as
`workspace_administration` to `cpk_server_services`. It serves those six reads
and the five corresponding commands: administrator workspace creation,
permission grant/revoke and managed-runtime access grant/revoke. It does not
initialize an installation, seed policy, register a runtime or configure a
transport. Those prerequisites remain separate owner actions.

Arguments are closed and acting identity never comes from payload. The adapter
validates public input before owner invocation, passes page limits 1..10
(default 10) to the owner and returns its descriptors/cursors unchanged. Cursors
remain bound to installation, workspace and their exact target. A page is an
independent owner read, not a cross-request snapshot. Creation returns its
historical administration receipt on replay. Ordinary legacy workspace creation
retains its separate response in static mode.

Malformed input is 400, administrative denial 403, typed missing requested
resource 404 and typed revision/idempotent-intent conflict 409. Unclassified
owner/store failure is a fixed, redacted 503. An inconsistent persisted receipt
is not a caller conflict, and an unavailable outcome does not promise rollback,
transience or safe retry. The Core route budgets are 64 KiB for input and single
results, and 1 MiB for pages. Existing owner value domains still apply: workspace
record identifiers/names and graph actor subjects reject control characters;
permission identity fields have their own bounds.

To use durable current permissions, construct `CpkServerOperationsApplication`
with `authority_mode="dynamic"` and an installation-bound
`workspace_context_resolver=WorkspaceContextResolver(...)`. The shared public
sync/async entrance resolves each ordinary request once from its verified
principal and bounded workspace target. It retains that admitted context during
internal dispatch; later owner authorization may narrow it. A new public request
resolves again, even if a previous internal request carrier is resubmitted.
Grant changes cannot expand a request already admitted. Resolver failure has no
startup-grant fallback.

Exact administration routes instead invoke their separate administrator policy;
dynamic mode refuses the legacy workspace-create entrance. Confidential material
provisioning keeps its existing HTTP-only, authority and service checks before
body access. Its material is deferred to that existing owner, never copied by
generic request preparation or written into history.

The default authority mode remains static for existing compositions. Dynamic
mode requires a resolver; static mode rejects an accidentally supplied resolver.
Server composition must preserve the configured application, rather than
reconstructing one from only `.services`. These Core/Operations contracts do not
by themselves prove Servers adoption, credential verification, raw query parsing,
deployment or provider behavior; those are separate composition and live gates.

## Read Models

`control_plane_kit_operations.read_services.InstanceReadService` composes
bounded models for:

- workspace summary and graph pointers;
- current and desired graph descriptors;
- operator graph projection;
- activity timeline, sessions, plans, and approvals;
- observed state and control surface;
- runtime and ingress authorities;
- runtime authority deliveries;
- secret-provider and secret-reference metadata;
- exact protected-material provision status, including uncertainty or pending admission;
- gateway probe history and verifier configuration;
- delegation signing-key metadata.

Read models contain no raw secret values. Authority and secret-provider reads
expose bounded metadata and references only.

For dynamically admitted public ingress, `read.run-events` enriches the original
step-start event with its bounded `ingress` history. The route materializes the
event page, immutable ingress selection, at most three unit decisions, at most
two provider/custody results, and allocation-owner state in one fresh read-only
repeatable-read transaction. It reuses the operations-history event projection;
the stored event payload and ordinal are unchanged. Malformed or oversized
retained facts produce a bounded read error rather than disappearing from the
page. Secret references, credentials, endpoint references and custody receipts
are excluded from this public summary.

The history distinguishes provider facts, custody outcomes and the final
allocation record. A stored result does not prove its acknowledgement reached
the caller, and a pending admitted unit is an unknown outcome. If provider
creation succeeds but custody or the final allocation record fails, the known
facts remain visible. The first version has no automatic retry/delete and normal
Remove cannot clean an incomplete allocation; partial-allocation cleanup remains
explicitly unsupported. Successful allocation and desired-empty teardown retain
their normal paths. The outer `effects_attempted` count still counts admitted
activity intent, while the ingress units report the finer capability history.
The summary separates `current_owner_state` from `owner_fold`. A later normal
removal by another run remains visible without attributing that success to an
earlier denied command or hiding its retained unit evidence.

Dynamic ingress composition requires `IngressInvocationService` and an explicit
`IngressProviderRegistration` for profile `cpk.ingress-invocations.v1`. Passing
an unqualified legacy adapter, including a wrapper accepting arbitrary keyword
arguments, refuses before provider entry. The registration asserts reviewed
adapter support; the actual Interpreter and Server owning suites must establish
adoption. The Operations port fixtures establish only Operations semantics.

`read.secret-material-provision` is an unpaged exact workspace/request lookup,
available through HTTP and MCP. It requires workspace visibility and
`secret-provider:read` independently of the provision command. Its public DTO
includes only state, safe correlation/timestamps, retained version metadata and,
after successful admission, the admitted reference. It excludes raw material,
the original actor/grant and provider credentials. A conflict's status navigation
does not authorize this read; a custody receipt alone does not imply admission.

## Public Material Disclosure

```text
delegation signing-key inventory
  includes: bounded public metadata
  omits: public_key_pem, private_key_reference
gateway verifier configuration
  includes: bounded public_key_pem, derived public environment
  disclosure: purpose-limited public verification material, not a secret
all read surfaces
  forbid: private-key bytes, provider credentials, resolved secret values
exact delegation-key generation receipt
  requires: workspace visibility and delegation-key:read
  includes: authorized destination reference, validated public key/version, distinct provider/admission status
  omits: provider endpoint/credential references and raw provider responses
other read surfaces
  forbid: private-key references
```

Public verification material is disclosed by verifier-configuration projections
and the exact authorized generation receipt. Calling public PEM non-secret does not make it part of
general signing-key inventory or permit disclosure of any private or resolved
credential material.

## Shared HTTP And MCP Boundary

Core defines pure route vocabulary. Operations maps route ids such as
`read.workspace`, `read.current-graph`, `read.activity`, and
`read.runtime-authorities` to one `CpkServerReadService`. The cpk-server process
then exposes that same application through FastAPI and MCP.

Transport authentication establishes a trusted principal. Operations enforces
workspace and focused read permissions. HTTP and MCP do not receive independent
authorization semantics or direct store access.

## Transaction Boundary

One read request opens one short read UnitOfWork, constructs its projection,
and closes the transaction before returning through the process adapter. Reads
do not perform Docker, Cloudflare, secret-provider, filesystem, or health IO.

## Package Ownership Evidence

`control-plane-kit-operations/tests/test_read_services_package.py` is the
executable current inventory for the installed read-services subtree. It fixes
the canonical public object identities, private protocol boundary, exact module
set, and acyclic local import graph.

`docs/architecture/package-module-inventory.json` is deliberately different:
it is retained evidence for the historical pre-extraction aggregate package.
It is not rewritten to describe current external packages.

## Current Validation

Current operations tests prove projection behavior, missing-workspace failure,
bounded errors, secret redaction, permission separation, and HTTP/MCP adapter
parity. The source-live cpk-server image smoke proves authenticated HTTP and MCP
requests traverse the same operations application against real Postgres.

The retired aggregate CLI, `create_instance_read_app`, and
`ReadOnlyMcpAdapter` imports are historical APIs. They are not current
compatibility surfaces.

## Saved topology catalogue (#1762–#1763)

Authenticated workspace operators can save alternative desired-topology designs
without changing either workspace graph pointer. Operations owns the named draft
and append-only revision catalogue; each revision references an immutable Core
graph version. Saving or revising does not prepare a plan, request approval, or
execute runtime effects.

| Route | HTTP path under `/workspaces/{workspace_id}` | MCP tool |
| --- | --- | --- |
| `command.desired-topology-draft.create` | POST `/desired-topology-drafts` | `create_desired_topology_draft` |
| `command.desired-topology-draft.revise` | POST `/desired-topology-drafts/{draft_id}/revisions` | `revise_desired_topology_draft` |
| `command.desired-topology-draft.select` | POST `/desired-topology-drafts/{draft_id}/select` | `select_desired_topology_draft` |
| `command.desired-topology-draft.delete` | POST `/desired-topology-drafts/{draft_id}/delete` | `delete_desired_topology_draft` |
| `read.desired-topology-drafts` | GET `/desired-topology-drafts` | `list_desired_topology_drafts` |
| `read.desired-topology-draft-revisions` | GET `/desired-topology-drafts/{draft_id}/revisions` | `list_desired_topology_draft_revisions` |
| `read.desired-topology-draft-revision` | GET `/desired-topology-drafts/{draft_id}/revisions/{revision}` | `get_desired_topology_draft_revision` |
| `read.desired-topology-draft-revision-preparations` | GET `/desired-topology-drafts/{draft_id}/revisions/{revision}/preparations` | `list_desired_topology_draft_revision_preparations` |
| `read.desired-topology-draft-revision-attempts` | GET `/desired-topology-drafts/{draft_id}/revisions/{revision}/attempts` | `list_desired_topology_draft_revision_attempts` |

Create accepts `session_id`, `idempotency_key`, `title`, and a Core `graph`
descriptor. Revise accepts the same session/key/graph inputs and requires
`expected_head_revision`. Actor identity and workspace edit/read permissions come
from the authenticated principal. Titles contain 1–512 characters and no controls;
graph JSON transport is bounded to 1 MiB. Exact revision reads apply existing graph
redaction; summaries contain no graph bodies. Titles are operator-authored text,
so callers must not use them to store credentials.

The draft and revision catalogue reads accept `limit` (1–100, default 50) and `after`. Draft summaries
order by creation instant, then draft identity; revision summaries order by revision,
then graph identity. Their cursors bind collection, workspace, and (for revisions)
draft identity. These domains are independent of overview/run-event pagination.

The two revision-history collections use `limit` 1–10 (default 10) and separate
`after` cursors. A cursor binds the exact collection, workspace, draft, positive
int8 revision, and canonical microsecond UTC instant plus item identity. A cursor
from another collection or revision is rejected before opening the read UoW.
Existing read authorization applies before argument/cursor decoding. These routes
introduce no mutation or external effect and return no private source metadata,
action payload, event payload, credentials, or provider addresses.

Preparations are the deduplicated union of exact saved-source sessions and
sessions containing a plan whose desired authored graph is this revision's graph.
They order by session creation time and session ID. Each item exposes session
identity/status, independent `source_linked` and `has_target_plan` association
flags, source attribution, and three exact-target presence booleans for plans,
execution requests, and attempts. Presence comes from complete membership, never
the current page or unrelated sibling plans.

Attempts order by run creation time and run ID. Every run is independently
qualified through its own exact-target plan and congruent tenant/session/request
identities. Items retain all recorded run statuses, request/plan/run/prior IDs,
attempt number, the actual five-field plan fence, and advancement attribution.
Desired selection generation is not a draft revision ordinal. `source` is either
`saved` with the exact revision coordinates or `unavailable` with null coordinates;
saved attribution requires the immutable source/session/start commitment and,
for attempts, the entire committed plan fence. Missing or incongruent provenance
does not erase a true target association or establish inline origin.

`advancement` is `accepted`, `none-recorded`, or `unavailable`. Acceptance requires
exactly one correlated retained advancement action/event pair and its actual plan
transition and projection digest. The compact receipt contains event/action IDs,
occurred-at, and the lowercase projection digest. Neither candidate means
none-recorded; orphan, duplicate, oversized, or incongruent evidence is unavailable.
Run success alone proves no advancement. Later current-pointer or mutable worker
claim changes do not invalidate historical acceptance; no command replay,
current worker fencing, planner, or provider inspection is involved.

Revision detail adds `history` with scope
`source-or-target-sessions-and-exact-target-attempts`, complete
`preparations_present` / `attempts_present` booleans, and completeness
`association-records-only`. Old revisions and tombstoned drafts remain readable.
False presence describes retained identity associations, not universal absence
across copied graphs or erased records. Lists remain bounded summaries.

Each page obtains membership, presence, and private evidence in one set-based
read-committed SELECT, optionally preceded by immutable parent admission. SQL
guards identifiers at 2048 UTF-8 bytes (public grammar remains 512 characters),
each private evidence blob at 64 KiB, and each receipt probe at two candidates.
The owner validates all `limit+1` candidates, including hidden lookahead, and emits
the last exposed cursor. Unsafe identity/tenant/paging evidence fails the whole
page with a bounded error. Canonical owner-descriptor JSON using `ensure_ascii`,
sorted keys, compact separators, UTF-8 and no newline is capped at 1 MiB; this is
not a claim about complete HTTP/MCP wrapper or wire size.

Pagination bounds returned data, not UNION deduplication or sorting cost. Existing
source/target/timeline indexes and two partial receipt indexes support the query;
owning Postgres tests retain representative EXPLAIN evidence without requiring an
optimizer tree. Later pages see later commits; backdated rows before a cursor can
be missed. Refresh/restart is a fresh traversal, not a historical snapshot.

The transaction lock order is action idempotency key, operation session, workspace,
then draft. Identical replay returns the original workspace/draft/revision/graph
coordinate before current session status, product registration, or head admission.
Changed intent and stale heads conflict. First publication validates Core graph
semantics and workspace-active product references. Graph, draft head, immutable
revision and bounded action evidence commit together, or all roll back. A retry
uses the same session and key. There are no provider calls or compensation steps.

The new exact schema includes two catalogue relations, a composite workspace/graph
foreign key, and a deferred composite head/revision foreign key. Saved-only graphs
participate in current-row verification. This changes the exact-current schema:
a populated older namespace requires an explicit reset; no in-place migration or
data-preserving upgrade is provided. Do not install over a retained demo database
without separately accepting its reset boundary. This feature does not perform a
reset. Saved preparation consumes these immutable coordinates as described below.

Select accepts `session_id`, `idempotency_key`, exact `revision`, and all three
expected desired fields: `expected_desired_graph_id`,
`expected_desired_realized_projection_id`, `expected_desired_graph_revision`.
The graph/projection fields are explicitly nullable as a pair. The command
revalidates saved Core graph semantics and workspace-active products after
locking. It creates or reuses the deterministic identity projection of the saved
graph, never another authored graph. Holding the workspace row lock, it checks
the complete expected tuple then updates/increments generation. Selecting the
same graph increments generation too; overflow and stale/ABA requests fail
without writes. Current graph truth, plans and runtime resources do not change.

Delete accepts the same session/key and `expected_head_revision`. It tombstones
only a live draft whose entire revision history is unused by workspace
current/desired pointers and all retained plan base/desired graph references.
Separate indexed EXISTS checks run while holding the workspace and draft locks;
planning holds that same workspace lock through its plan commit. Tombstoning
preserves graphs, projections, revisions and actions. Tombstoned summaries,
revision history and exact detail remain readable; new select/revise commands
are rejected. There is no graph cascade, runtime teardown or compensation.

Selection results retain workspace/draft/revision/graph, desired projection ID
and resulting desired generation. Delete results retain workspace/draft/head and
exact deletion actor/time. Their action payloads contain only these bounded
coordinates. Replay verifies immutable revision/graph/identity-projection or
retained tombstone evidence before current session, product, head, tombstone or
desired admission. It returns the historical result even after later changes,
without new writes; malformed retained evidence fails closed. Product admission
uses the existing registration read contract and adds no new revocation fence.

`read.operator-overview` adds only `graphs.desired.draft` with `state`, `selected`
and `head`. A selected/head value is `{draft_id, revision, graph_id}`; the two
revisions may differ. A legacy desired graph with no catalogue mapping yields
`none`; a malformed, plural or tombstoned mapping yields `unavailable` with null
coordinates. Workspace and draft/head anchors are reread to reject observed
changes. These fields add no title, graph body, catalogue page or event cursor.
The existing workflow and informational next-action projection still grants no
authority. Saved-revision preparation uses the existing program continuation below.

The #1763 schema change adds only two plan graph lookup indexes and the two
operation-action kinds. Its exact baseline is 39 relations, 503 columns,
379 constraints, 127 indexes and 85 foreign keys; the same explicit reset-on-drift
boundary applies. No retained database is reset by these commands.

### Prepare an exact saved revision

`command.deployment.prepare` / `prepare_deployment` retains its existing route,
authentication and result variants. Supply exactly one input mode: inline
`desired_graph`, or both `draft_id` and `revision`. Mixing modes, including an
explicit null `desired_graph`, is malformed. Saved mode additionally requires
`expected_current` and `expected_desired`, each containing `authored_graph_id`
and `realized_projection_id`, plus positive `expected_desired_graph_revision`.
The existing `title`, `idempotency_key` and optional `approval_comment` remain.

Operations accepts `PrepareDeploymentProgram(desired=SavedDesiredTopologyRevision(
 draft_id, revision), ...)`. The exact revision must be selected; it may be older
than the draft head. Admission compares both complete graph/projection lineages
and desired generation under the workspace lock, validates retained graph
semantics, the desired identity projection and current projection membership,
and re-reads workspace-active desired products. Preparation neither copies nor
publishes graphs, creates projections, nor increments desired generation.

The saved service locks the workspace-scoped session idempotency key, then the
workspace and draft before creating the existing session/start action in one
transaction. The session-start operation accepts a caller-owned transaction and
never commits it. Existing plan and approval-request services retain their own
subsequent commit boundaries. If selection changes after admission but before a
new plan, preparation fails stale and retains the admitted session; it never
reselects or rebases automatically. A retry with identical intent reuses existing
session/plan/approval evidence. Completed historical replay survives later
selection changes, draft revisions, product revocation and session closure.
Changed intent sharing the parent key conflicts, including inline versus saved
mode. Corrupt retained evidence fails closed.

Saved session metadata is a closed nine-string protocol. The marker is
`deployment_prepare_source: saved-revision.v1`, accompanied by
`deployment_prepare_intent_sha256` and seven `deployment_prepare_saved_` keys:
`draft_id`, `revision`, `graph_id`, `current_graph_id`, `current_projection_id`,
`desired_projection_id`, and `desired_generation`. Revision and generation are
canonical positive decimal strings. The owner computes the SHA256 commitment
from canonical JSON of the saved profile, authenticated workspace/actor, exact
saved input, both lineage fences, generation, title and comment. Caller scopes
are not durable authority. Replay correlates the complete session/start-action
identity, actor, title, key, metadata, fingerprints, action kind/ordinal/payload
and creation time with immutable revision and graph/projection records.

The existing overview adds `workflow.prepared_draft`:

```json
{"state":"prepared","revision":{"draft_id":"draft-a","revision":1,"graph_id":"graph-a"}}
```

This coordinate comes only from the session of the plan already selected by the
overview. It does not introduce a latest-preparation selector or alter the run
history cursor. Inline/legacy evidence yields `{"state":"none","revision":null}`.
Ambiguous workflow, malformed/incongruent metadata or missing immutable evidence
yields `{"state":"unavailable","revision":null}`. Every reserved saved key
requires the exact marker and complete mapping. Removing all saved evidence is
indistinguishable from legacy metadata; the read does not claim to detect that
erasure. Existing plan/session and saved-revision anchors are reread before the
projection is returned.

Security/data: workspace-edit and plan-request are both required. Results and
errors contain bounded coordinates, not graph bodies, submitted title/comment
or credentials. Preparation requests review; it does not approve, execute,
advance current truth or call a provider. Product admission has the existing
ACTIVE reread but no new concurrent revocation fence. No schema migration or
retained-database reset is part of this capability.

Relational saved provenance (#1772): new exact schemas record one
`cpk_saved_preparation_sources` row per saved admission session, referencing its
same-workspace immutable revision. Historical replay and prepared overview require
that source to agree with the unchanged nine-field session commitment and
revision graph; missing or contradictory evidence is unavailable and never
recreated. No new read route or positive inline-origin claim is introduced.
The installer verifies saved-key/source-owner candidates in byte-guarded batches
of at most 64 under its existing transaction and relation locks. Batch size
bounds memory and transfer, not total work or lock duration; no global scan runs
per HTTP read. An older schema is rejected without writes. The retained acceptance
installation stays pinned; no migration, reset, backfill, history import or
new-UI compatibility with that installation is implied.

Original installation sources (#1987): each new configuration acceptance retains
a private canonical `accepted-installation-sources.v1` document alongside its
existing artifact slots. Advancement events and actions expose only the source
count and document digest. Explicit empty documents are valid; missing commitments
are not interpreted as empty legacy evidence. Full acceptance, command replay and
current-schema validation prove the document and its exact original attempts.
Artifact-scoped configuration and cleanup reads retain their existing semantics.

Unchanged consumers carry the original acceptance revision and both request and
outcome fingerprints verbatim. Generated connector material additionally commits
the original allocation start, its direct successful terminal and all six durable
event columns through that terminal. Fresh discovery examines the full available
bounded journal; historical proof reads the retained prefix. Neither path uses a
latest-success search or today's credential permissions as historical evidence.

An actual qualifying provider observation may prove installation for publication.
It does not produce a direct completion or release outstanding claims. Schema
changes add three required fields and three checks to the existing acceptance
table, with no migration, backfill, reset or production-data repair. This local
implementation has passing focused Docker evidence. The first full Operations
gate reported 2,781 tests with 64 failure and 216 error entries; all 280 entries
subsequently received reviewed focused green evidence. The second full gate ran
2,781 tests with one failure and no errors: a stale atlas-digest test expectation.
The one-literal correction then passed all ten existing atlas-reader tests,
compilation and clean import. This is combined full-run and focused-correction
evidence, not a green full-suite run on the corrected candidate. Parent acceptance
remains a separate gate. No published or live compatibility with an older
installation is established.

Current connector installation (#2007): the internal Operations store method
`ConfigurationAcceptanceStore.read_current_connector_installation` takes the
workspace, expected current graph and realized-projection pins, and exact runtime
and connector node. Its strict `CurrentConnectorInstallationRead` result is
`complete`, `unavailable`, or `capacity`; only `complete` carries an installation.
This adds no cpk-server, HTTP, or MCP surface.

The read proves the existing complete installation-source bundle under the same
shared evidence budget, including original acceptance membership, exact original
attempt/outcome and generated-token provenance. It returns the original Core
request without secret-resolution grants and its typed secret installation
receipt, even when the connector has no configuration artifacts. A supported
workload content update preserves that origin while advancing the returned
current pins/revision. Missing, corrupt, uncertain or nonconnector proof cannot
fall back to an earlier success. No schema, provider call, write, lock or budget
increase is introduced.

The caller owns the UOW. Returned pins describe captured current truth and do not
freeze subsequent workspace changes. Historical authority is provenance only;
consumers must separately check fresh current authority and execution fences and
close their UOW before effects. The request and receipt are omitted from value
representations.

Native dispatch consumption (#2009): a supported `MANAGED_UPDATE_V1` connector
observation acquires this proof inside its fresh dispatch UOW, after the existing
worker/fence, accepted-plan, runtime-authority and lease checks. Current workspace
pins must still match the execution base. A new immutable
`ActivityRealizationContext.connector_installation` carries the original creation
request/receipt; the observation request and event retain their own identities.
The optional field is excluded from repr and remains `None` for fresh bootstrap
and signed health reads. Historical installation credentials are provenance;
current runtime authority is independently reloaded for dispatch.

Only the proof read activates the existing run-owned evidence ledger, and its
previous state is restored in `finally` before leaving the UOW or awaiting the
provider. Its accumulated footprint is never reset. Closed `unavailable` or
`capacity` results skip the provider and enter the existing guarded uncertain
fold. With an exhausted ledger, the later bounded context reload can still raise
`ExecutionCoordinatorConflict` after that uncertain outcome has been committed.
Response failure therefore does not imply absence of a durable outcome. Exact
command replay reports uncertainty without another proof read or provider call.
Unexpected proof-reader exceptions propagate and retain the existing STARTED
attempt; current-pin/authority denial likewise adds no retry path. This does not
activate retained-container support in an external Interpreter.
