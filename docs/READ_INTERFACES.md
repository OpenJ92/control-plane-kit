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

`InstanceReadService` is the single public composition facade. Its methods make
explicit one-hop calls to those owners; it does not implement projection
behavior, dispatch through reflection, or provide an alternate read API.

## Interface Law

The law is:

```text
interfaces expose the model; they do not define the model
```

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
- gateway probe history and verifier configuration;
- delegation signing-key metadata.

Read models contain no raw secret values. Authority and secret-provider reads
expose bounded metadata and references only.

## Public Material Disclosure

```text
delegation signing-key inventory
  includes: bounded public metadata
  omits: public_key_pem, private_key_reference
gateway verifier configuration
  includes: bounded public_key_pem, derived public environment
  disclosure: purpose-limited public verification material, not a secret
all read surfaces
  forbid: private-key bytes, private-key references, provider credentials, resolved secret values
```

Public verification material is disclosed only by the verifier-configuration
projection that needs it. Calling public PEM non-secret does not make it part of
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

Create accepts `session_id`, `idempotency_key`, `title`, and a Core `graph`
descriptor. Revise accepts the same session/key/graph inputs and requires
`expected_head_revision`. Actor identity and workspace edit/read permissions come
from the authenticated principal. Titles contain 1–512 characters and no controls;
graph JSON transport is bounded to 1 MiB. Exact revision reads apply existing graph
redaction; summaries contain no graph bodies. Titles are operator-authored text,
so callers must not use them to store credentials.

The collection reads accept `limit` (1–100, default 50) and `after`. Draft summaries
order by creation instant, then draft identity; revision summaries order by revision,
then graph identity. Their cursors bind collection, workspace, and (for revisions)
draft identity. These domains are independent of overview/run-event pagination.

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
