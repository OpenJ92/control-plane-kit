# O1.C execution scope: proposed exact interface and query contract

Status: candidate for joint design freeze, not accepted or executable. North
selected the execution-owned derived index and supported-writer completeness
direction after the [boundary checkpoint](receiver-lifecycle-o1-admission-boundaries.md).
This document makes that direction concrete for review. It neither changes B's
two graph-owned tables nor authorizes source, targets, database installation or
external effects. Selected application source remains `2a1bf73`.

Query-contract amendment for C1 [PR #1906](https://github.com/OpenJ92/control-plane-kit/pull/1906#issuecomment-5899826124):
North accepted Kepler/Meridian's clarification that candidate rows returned from
each prefix, transport and subsequent processing are bounded; PostgreSQL's
internal scan work is not. The candidate-query and plan-proof wording below
supersedes the earlier required pre-cap ordering and all-three-index-selection
expectations. Exact catalog indexes, caps, completeness and authority are unchanged.

## Ownership and interface

Add Operations module `receiver_execution_scopes.py` for closed internal scope
values, pure source derivation, bounded-evidence classification and limits.
Add `postgres/receiver_execution_scopes.py` for SQL representation/read helpers,
composed by the existing `PostgresExecutionStore`; no new StoreBundle member,
root export, route, service registry or independently writable scope DTO.
Register both modules in the exhaustive module inventory.

Proposed internal values:

```text
ExecutionReceiverScope(runtime_id, node_id?)
  node_id absent: runtime-wide
  node_id present: node-wide, including every socket on that node

DerivedExecutionReceiverScopes(scopes, source_digest)
  immutable canonical distinct tuple, including positively derived empty tuple

ReceiverScopeEvidence
  bounded original requests/plans/projections/runs/attempts/events/receipts
  complete or explicit unavailable/capacity result; never truncated clearance
```

The pure derivation receives the exact validated stored plan and both pinned
realized graph records, plus request workspace/plan identity. It uses the closed
operation classification in the boundary document and explicit compensation
material sides. Scope is independent of configuration profile and receiver ID.
Unsupported operations do not default to empty coverage. Actual fresh effect
scope must be contained in this immutable coverage before dispatch.

`PostgresExecutionStore.add_request` remains the supported request-persistence
entry but must compose complete admission/derivation or refuse missing context;
it cannot accept precomputed caller scopes. Final semantic composite placement
is coordinated with the entrypoint API plan. A guard proves only same active
transaction/workspace serialization. Existing request-return/replay records
need not expose the new SQL completeness fields as user-editable attributes.

Proposed read responsibility:

```text
execution.receiver_scope_evidence(workspace_id, requested_scopes, guard)
```

Only the lawful workflow derives requested scopes from actual receiver changes
or affected plan material. This is an internal evidence read, not a route or
fresh execution permission. It contains no caller-supplied excluded run IDs,
clearance booleans or accepted/disposed flags. Advancement interprets its own
run only inside the existing complete-success/association proof and atomic
acceptance operation; SQL candidate selection still retains older other runs.

## Exact proposed schema delta

Add to `cpk_execution_requests`, both `NOT NULL` with **no default**:

```text
receiver_scope_count integer
receiver_scope_digest text
```

Constraints:

* `cpk_execution_requests_receiver_scope_count_check`: count between 0 and 1024.
* `cpk_execution_requests_receiver_scope_digest_check`: exactly 64 lowercase
  hexadecimal characters.

Add one relation `cpk_execution_receiver_scopes`:

```text
request_id       text NOT NULL
workspace_id     text NOT NULL
scope_ordinal    integer NOT NULL
scope_kind       text NOT NULL             # node | runtime
runtime_id       text NOT NULL
node_id          text NULL
```

Constraints and constraint-owned index:

* `cpk_execution_receiver_scopes_pkey`: PK `(request_id, scope_ordinal)`.
* `cpk_execution_receiver_scopes_request_workspace_fk`: `(request_id,
  workspace_id)` references the existing request/workspace unique key; immediate,
  NO ACTION on update/delete.
* `cpk_execution_receiver_scopes_position_check`: ordinal 0 through 1023.
* `cpk_execution_receiver_scopes_kind_check`: `node` or `runtime` only.
* `cpk_execution_receiver_scopes_runtime_check`: runtime text is nonblank,
  PostgreSQL-compatible, 1–2048 UTF-8 bytes. Preserve exact admitted spelling.
* `cpk_execution_receiver_scopes_node_check`: runtime kind requires NULL node;
  node kind requires nonblank node text of 1–2048 UTF-8 bytes.
* `cpk_execution_receiver_scopes_key_bytes_check`: the sum of UTF-8 byte lengths
  of workspace, runtime, request and node-or-empty is at most 1024. Enforce this
  before insertion too. This explicit fresh-admission capacity limit bounds the
  raw composite B-tree keys; individual 2048-byte transport limits are not an
  assertion that all maximal references fit together in an index entry.
  The supported ordinary-suite PostgreSQL baseline uses 8 KiB pages; verify
  that prerequisite with the eventual suite. Do not truncate or normalize a
  failing key. Include incompressible and multibyte near-limit/one-over cases.

Three explicit lookup indexes; the first two are unique and prevent duplicate
scope rows for one request:

```text
cpk_execution_receiver_scopes_runtime_lookup
  (workspace_id, runtime_id, request_id) WHERE scope_kind = 'runtime'

cpk_execution_receiver_scopes_node_lookup
  (workspace_id, runtime_id, node_id, request_id) WHERE scope_kind = 'node'

cpk_execution_receiver_scopes_runtime_nodes_lookup
  (workspace_id, runtime_id, request_id, node_id) WHERE scope_kind = 'node'
```

The first two indexes are unique; the third is a nonunique access path for a
runtime-wide requested scope intersecting all historical node scopes there.
The optimizer may choose either compatible node index for that runtime prefix;
the catalog does not require every query to select a particular index name.
It does not enumerate only nodes still present in today's graph.

Add `cpk_operation_actions_receiver_cancel` on existing actions:
`(session_id, (payload ->> 'run_id'), action_id) WHERE action_type = 'cancel-run'`.
This supports the exact cancellation/no-dispatch positive proof with at most
two action candidates; cancellation events come from the bounded run journal.
Do not scan all session actions to recover that witness.

No trigger, function, lifecycle enum, migration ledger, cascade, scope-update or
scope-delete API. No tail/predecessor chain. Request/source/coverage immutability
is maintained by supported writers and verified against original source during
exact-current validation; these constraints alone do not prove derivation.

The witness digest is SHA-256 over RFC8785 bytes of this exact closed object:

```text
profile: "receiver-execution-scopes.v1"
workspace_id, request_id, plan_id
base_graph_id, base_realized_projection_id, base_realized_projection_digest
desired_graph_id, desired_realized_projection_id, desired_realized_projection_digest
desired_graph_revision
plan_digest
scopes: [{scope_kind, runtime_id, node_id}, ...]
```

`plan_digest` is SHA-256 of RFC8785 bytes from the original stored plan descriptor
including its derivation profile. Projection digests are retained validated
record digests. Scope array order is `(runtime_id, scope_kind,
node_id-or-empty)` using exact text, with ordinal assigned from zero. A valid
empty array has count zero and its actual non-null digest. Do not substitute a
zero digest or derive absence from missing rows.

Insertion: under the existing admission prefix, revalidate source association,
derive and bound coverage, insert request with count/digest, insert sorted scope
rows, append existing real admission action, return on the caller's UoW. Any
failure rolls back all rows. Matching original command replay reads its original
request and coverage; it does not recompute a replacement witness or mint rows.
Current verification reads only request identities in 64-row keyset batches and
rederives one complete scope set at a time, with its own 16 MiB value budget,
releasing decoded material before the next request. This is an exhaustive
query-only schema-verification traversal, not the online 64-candidate scope
lookup. No whole-database byte cap or 64-times-material batch is implied.
A mismatch refuses.
The new exact catalog adds one relation, eight columns total, nine constraints
and five indexes beyond the separately frozen B delta; verify those counts
against generated catalog evidence before accepting source.

Negative-query soundness is intentionally limited: every supported insertion
atomically creates complete immutable coverage, and no supported update/delete
can break that invariant. An encountered candidate's missing/mismatched evidence
refuses. The reverse lookup cannot detect a wholly omitted or misindexed request
created by privileged raw SQL; a header digest does not fix that. No stronger
online corruption-discovery guarantee is claimed.

## Proposed fixed capacity and transport limits

These are proposed Operations refusal boundaries, not claims of existing Core
limits or measured performance. Freeze them visibly with representative positive
and overflow target cases before implementation:

| Quantity | Proposed cap | Enforcement |
| --- | --- | --- |
| Distinct receiver scopes checked by one command | 1024 | Deduplicate actual scope requests before SQL; over-cap refuses. |
| Activities in one admitted plan / derived distinct scopes | 1024 each | Closed plan/graph decoding plus exact whole-set derivation; never silently omit compensation. |
| Distinct request candidates across the entire lookup | 64 | Concrete-point/history branches use 65; runtime→node branch uses the separate raw-row cap below. Dedup against one shared distinct budget. |
| Total candidate scope rows before request deduplication | 4096 | Separate shared limit-plus-one; runtime-wide node-history fanout cannot bypass this cap. |
| Total retained runs across candidates | 256 | Request-prefix ordered limit-plus-one; do not load only the latest. |
| Total effect-evidence rows across candidate runs | 2048 | Attempts, independently enumerated intents, compensation steps and bindings share this row budget; each returned row counts. |
| Total journal events across candidate runs | 8192 | Existing `(run_id, ordinal)` unique-key order, one shared remaining budget. |
| Scope rows for one candidate request | 1024 | PK request prefix, limit 1025; compare exact derived count/order/digest. |
| Each graph descriptor, stored plan descriptor, intent or compensation program | 1 MiB | Check size in SQL before transport/decode; graph/intent/program bounds reuse existing conventions; plan cap is new. |
| Each event/action JSON payload | 64 KiB | Reuse existing revision-history bounded blob convention; malformed/oversized required evidence refuses. |
| Each direct outcome preimage | 8192 bytes | Preserve its existing storage contract. |
| Each transported reference text | 2048 UTF-8 bytes | Bounded SQL projection; do not truncate identifiers into alternate identities. |
| Combined raw lookup key | 1024 UTF-8 bytes | Workspace/runtime/node-or-empty/request sum, checked at fresh admission and by the named SQL constraint. |
| Transported source/evidence value bytes per complete lookup | 16 MiB | Shared counter reserved before retrieval; repeated fetched values still count. Excludes PostgreSQL wire framing and Python object overhead. |

The 64-candidate window and 64-row validation cadence align the small initial
lookup with existing bounded store practice; 256/2048/8192 allow several run,
attempt and event records per candidate without an unbounded history walk.
1024 plan/scope values and 1 MiB material are explicit fresh-admission ceilings.
Value bytes mean UTF-8 bytes of each explicitly projected scalar/JSON text or
raw bytea length; NULL contributes zero. Counts/length probes, sentinels, side
reads and repeated values all count. SQL returns bounded scalar text for this
accounting; this is not a bound on protocol framing, resident decoded memory or
PostgreSQL's internal detoasting. 16 MiB is an aggregate value ceiling, not
16 MiB per candidate. These are deliberate
initial capacity choices requiring review, not a performance claim. Existing
large retained same-scope settled history can overflow and refuse; no status-only
pruning or automatic cleanup is authorized to hide that limitation.

## Bounded query stages and index proof obligations

1. Under L, a node-point request probes runtime rows at `(workspace,runtime)`
   and node rows at `(workspace,runtime,node)`. Each exact partial predicate
   takes at most 65 scope rows without SQL ordering. A runtime-wide request
   probes the same runtime rows **and all historical node rows** at
   `(workspace,runtime)`, using an eligible indexed prefix. That branch takes
   at most the remaining 4096-row
   budget plus one, further reduced by the transport reservation below. No
   relational `DISTINCT`, sort, join or expansion precedes its cap. Deduplicate
   requests only from these bounded rows, then return canonical sorted IDs.
   A 65th distinct request or 4097th scope row refuses. A filled effective page
   always refuses before row interpretation, including exactly-full populations;
   a shorter page contains the entire visible prefix irrespective of order.
   Collapse requested node points covered by an already-requested runtime-wide
   scope and probe repeated prefixes once. Historical nodes missing from either
   current plan side remain discoverable.
2. Fetch each candidate request and its exact plan/session/projections by key.
   Recheck workspace/source associations and complete derived scope witness.
   Fetch only bounded scalar byte lengths first, reserving 512 value bytes for
   each fixed-size probe (at most 32 int4-length/null columns). Refuse if the
   projected row's measured sum exceeds the remaining value budget. Fetch it by
   exact identity with SQL total-size/shape guards capped at that reservation;
   if the row changed or grew, return only the bounded unavailable sentinel.
   Individually guarded columns must not collectively exceed the reserved row
   size. Charge returned values before decoding; release unused reservation.
   Cache only within this call by immutable identity/digest;
   no cross-command authority cache. Missing/unknown/foreign evidence refuses.
3. Read **all** candidate runs by `(request_id, attempt)` using existing
   `cpk_activity_runs_request_attempt`. Read attempts by PK
   `(run_id, activity_id, attempt)`, events by existing `(run_id, ordinal)`.
   Each query receives shared remaining count + 1. No status predicate,
   desired-pointer predicate or latest-run shortcut may erase older history.
4. Join needed intent/outcome/start/latest-event evidence only from bounded
   identities. Each is an indexed point read. Recoveries may have no direct
   outcome row; absence of that row is not absence of the attempt. Preserve
   exact direct-versus-recovered interpretation and original fingerprints.
   Also enumerate retained intents independently by their existing
   `(run_id,activity_id,attempt)` primary-key prefix, within the shared 2048
   effect-evidence row/value budget. The FK is attempts→intents, not the reverse:
   zero attempts does not prove zero intents. Compare exact identity sets;
   orphan/missing/incongruent intent is unavailable. In particular, cancellation
   cannot establish no-dispatch while any retained intent is hidden by an
   attempt-driven join. No new index is needed for this independent prefix.
5. Probe advancement event/action pairs using the existing partial indexes,
   maximum two per side, preserving ambiguity refusal and original association.
   Probe compensation program by its unique run key; steps/bindings by program
   and position/source keys. Charge their rows and bytes to the same operation's
   attempt/event/source budgets rather than introducing unlimited side reads.
   Cancellation action attribution uses the new cancel partial index with two
   candidates maximum; it does not inherit advancement's predicate accidentally.
6. Interpret complete evidence. Return bounded conflict/unavailable/capacity or
   clear; no candidate list exposed as user authority and no partial-clear result.

Index shapes provide exact-prefix access paths. Actual ordinary-suite SQL-plan
tests must demonstrate a compatible indexed path for each runtime, node-point
and all-nodes prefix, and no relational sort/join/dedup/JSON expansion before
the cap on representative PostgreSQL 16 mixed-scope data. Prefix conditions are
query-specific: node equality is required for node-point queries, not every use
of the overlapping node index. Exact three-index catalog checks remain separate
from the optimizer's choice among those indexes.

These bounds cover candidate rows materialized beyond each prefix query,
transported values and subsequent processing. They do not bound bitmap
construction, index/heap work, database memory/CPU/pages or latency. Bitmap
access is permitted index evidence, not proof that only LIMIT-many matches were
examined. Large matching histories may therefore require substantial database
work while L is held, despite capacity refusal and transport safety. This is an
availability limitation; no implicit history pruning or authority relaxation
follows. The amended query's actual plan still requires owning-suite validation.

Several requested scopes can share candidates. Charge each unique request once
against 64; charge all returned scope rows against 4096, and all transported
value bytes including repeated IDs against 16 MiB. No false distinct overflow merely
because the unique budget is exhausted and a later branch returns known IDs.
Source decoding can be deduplicated; repeated fetched value bytes are not exempt. A
candidate row projection is fixed-width scalars plus checked references with a
4096-byte reservation per row; reduce SQL LIMIT to the number of those reserved
rows fitting remaining value bytes. Less than one row's reservation remaining
refuses before querying. For large evidence, query checked lengths first and
reserve exact bytes plus the fixed scalar envelope before fetching one payload;
never fetch a full batch and then discover that the value budget was exceeded.
At most 1024 node prefixes plus 1024 runtime prefixes are eligible after
requested-scope normalization; no latency guarantee is inferred from this cap.

Every limit is an exhaustion proof, not permission to truncate. If a
transport-reduced effective SQL limit is filled, return capacity refusal unless
an already-budgeted sentinel proves the required complete prefix. Do not treat
fewer rows than the *configured* cap as exhaustion when SQL actually requested
a smaller limit. The simple implementation refuses on any filled reduced limit;
it adds no continuation loop. With only one row reservation remaining, zero
rows proves empty, while one row cannot prove that a second is absent. Apply
this rule to candidates, runs, attempts, events, scope sets and all side reads.
An exact unique-key point read proves its own cardinality; it does not need a
fictitious second row. Size reservation still applies to its returned values.

## Governing and proposed target laws

Reuse E1–E3, A1/A3–A5, H1 and A lock evidence from the parent plan. Inspected
`test_execution_admission.py` protects exact replay, both admission keys before
L, atomic request/action rollback and approval/generation fencing. Inspected
`test_revision_history_advancement.py` protects missing/duplicate/malformed
receipt refusal and the two exact receipt indexes. Inspected atomic-fold tests
protect recovered evidence without direct outcomes. Preserve these laws.

New scope targets (unwritten): legacy/no-binding affecting request blocks V2
reuse; queued/no-attempt work remains visible; node/runtime-wide overlap covers
socket changes; relocation and BASE compensation retain both original runtime
coordinates; unrelated nodes and positive observations do not conflict; genuine
empty coverage differs from missing/forged coverage; actual dispatch outside
coverage refuses; row/witness/action failures roll back atomically; supported
mutation cannot change source/coverage; exact-current reentry verifies without
writes; cross-workspace rows refuse without identity disclosure.
Include intent-without-attempt as an explicit C-N11 negative, an independently
exhausted intent-prefix budget, and direct fresh intent insertion refusal; the
legitimate first-start positive commits its intent/attempt/event atomically.

Capacity targets use a positive near-limit witness and one-over negative for
each independent counter and payload cap, plus combined fanout/duplicate-prefix
cases. For a concrete node query, unrelated node-only history must not consume
the runtime-wide-history branch. For a genuinely runtime-wide query, historical
nodes on that runtime are relevant and must consume scope-row/byte capacity.
Older affecting work cannot disappear behind newer accepted history. Query-first
and reactivation-first contention must preserve the same exclusion. Assertions
must exercise returned behavior/real SQL ownership, not mirror helper names.

Security/data/history: this is documentation only. New SQL stores no tokens,
provider responses or private configuration; original graph/intent material
remains under existing protections. Public errors are bounded and tenant-safe.
Schema policy stays object-free install/exact-current verify/otherwise refuse.
No backfill, reset, deletion, provider call or transaction across provider I/O.
