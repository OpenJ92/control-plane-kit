# O1.B receiver storage: test-conditioned design

Status: draft for independent review; no target tests, application source or
execution released by this document. Governing child: [#1897](https://github.com/OpenJ92/control-plane-kit/issues/1897).
Starting coordinate: accepted O1.A merge `2a1bf73dc1ba97806bf7bd26991d1450dc8af9d8`.
Reuse [F0 section 4](../../design/0007-logical-receiver-lifecycle.md), the
[parent plan](receiver-lifecycle-o1-plan.md) and unchanged
[A lock ledger](receiver-lifecycle-o1-lock-ledger.md). C/#1898 owns complete
admission, unresolved-execution predicate/query and its execution indexes.

## Accepted predecessor and evidence

A establishes the participating workspace guard and coordinated lock order,
including prepared publication and native/health run-prefix composition. It
does not activate receiver lifecycle semantics. [PR #1900](https://github.com/OpenJ92/control-plane-kit/pull/1900)
merged at the starting coordinate after [final independent PASS](https://github.com/OpenJ92/control-plane-kit/pull/1900#issuecomment-5885231678)
and [North acceptance](https://github.com/OpenJ92/control-plane-kit/pull/1900#issuecomment-5885260390).
Exact source/test head `73557ac` passed the ordinary Operations wrapper: 1,861
tests, compile/import and exact disposable-resource cleanup. Hosted PR merge
`730b2a7` passed Core 907 and Operations 1,861 tests. The separately locked backend
passed nine stages with CPK `f45384e7`, Interpreters `2335a21a`, Secrets
`96e86dc3`, Servers `43e9f359`; it is not adoption evidence for A or B.
[Detailed handoff](https://github.com/OpenJ92/control-plane-kit/pull/1900#issuecomment-5885203840).
No rerun is needed to restate that accepted evidence.

## Governing test context, inspected before interface design

These are current accepted tests, not invented frozen identities. Classes are
isomorphic (I), strengthened (S), new-law (N), or planning scaffold (P).

| Card | Inspected tests | Preserved observable law and negative cases | Discarded structural assumption / owner |
| --- | --- | --- | --- |
| C1 I/S | `test_current_schema_installation.py::test_current_contract_has_only_exact_functional_truth`, `::test_object_free_install_creates_only_exact_current_truth`, `::test_current_reinstall_is_query_only_and_identity_stable` | Exact catalog and row/object identities; current reentry performs no DDL/DML. | Old 41-table baseline changes only through reviewed new exact contract; installer. |
| C2 I | Same file `::test_nonempty_owned_object_families_require_reset_before_effect`, `::test_cross_schema_objects_are_preserved_and_ignored`, `::test_current_drift_rejects_without_repair_or_row_loss`, `::test_query_path_index_drift_is_reset_required_without_repair` | Noncurrent objects/indexes/rows refuse unchanged; foreign namespace untouched; generic error. | No migration/history ledger or repair path; installer. |
| C3 I/S | Same file `::test_empty_install_failure_rolls_back_every_schema_effect`, `::test_caller_owned_outer_transaction_retains_authority`, `::test_concurrent_empty_installers_serialize_to_one_current_schema`, `::test_relation_lock_timeout_is_generic_and_retryable_after_release` | All DDL rolls back, caller retains transaction authority, concurrent installers converge, lock failure stays bounded. | Extend exact object coverage; do not introduce another transaction owner. |
| G2 I/S | `test_workspace_graph_stores.py::test_graph_store_preserves_typed_descriptor_and_latest_version`, `::test_next_version_and_compare_and_set_are_workspace_scoped`, `::test_workspace_and_graph_writes_roll_back_together` | Original descriptor/typed graph survives exactly; workspace versions and pointers remain isolated; no orphan after rollback. | A receiver binding is a derived index, not rewritten graph truth; graph owner. |
| D3/D4 I/S | `test_desired_topology_drafts.py::test_concurrent_identical_create_replays_one_durable_revision`, `::test_action_failure_rolls_back_graph_revision_head_and_action_for_create_and_revise` | Identical commands converge; late action failure rolls back all prior writes; exact retry succeeds. | Extend the same real-UoW proof to projection/introduction/bindings when C integrates; no duplicate fake workflow. |
| U1 I/S | `test_unit_of_work.py::test_commit_publishes_all_shared_connection_writes_together`, `::test_late_write_failure_rolls_back_every_earlier_write`, `::test_exception_after_commit_request_rolls_back_real_postgres_writes` | Commit request is not physical commit; exceptions still roll back the whole command. | New stores never commit; Operations UoW. |
| L1 I/S | `test_postgres_lifecycle_graph_locks.py::test_guard_is_owned_by_exact_workspace_and_transaction`, `::test_fresh_graph_reference_entrants_take_exact_guard_before_session_and_workspace` | Exact active owner/workspace; first acquisitions remain in A order. | New graph suffix cannot reacquire an earlier new key; graph owner. |
| B1–B5 N | F0 section 4 | Global receiver uniqueness, immutable original provenance, exact derived material, write-once acceptance/terminal retirement, retained history and bounded conflicts. | No current receiver-store test is claimed to exist. |

No executable red/green is claimed for B. Targets follow reviewed interface and
release; only the ordinary Operations Docker suite may execute them.

## Current selected-source trace and missing connections

All anchors below describe accepted `2a1bf73`, whose local Core and Operations
are the selected source. The ordinary suite requires architecture-testing
`7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`; no dependency change is proposed.

* `postgres/schema.py:install_schema` takes a namespace transaction advisory
  lock. Object-free installs execute `current_schema.sql`, lock relations and
  verify the exact contract. Existing namespaces must match first; query-only
  `current_data_validation.validate_current_rows` verifies retained semantics.
  It never upgrades, repairs or backfills. `current_schema_verification` rejects
  owned functions/triggers outside its exact contract; B adds none.
* `records.py:GraphVersionRecord.from_graph` encodes Core graph data;
  `postgres/graph_store.py:PostgresGraphTopologyStore.save` inserts that descriptor.
  Neither interprets receiver lifecycle. Generic Core graph validation checks
  topology, not lawful introduction/continuation or unresolved execution.
* `graph_authoring.py:set_desired_graph_in_unit_of_work` consumes A's guard,
  checks workspace/product/desired CAS, saves authored graph and deterministic
  identity projection, then updates desired pointer. `planning.py` appends its
  `set-desired-graph` action afterward in the same UoW. Standalone authoring has
  no operation action and cannot fabricate one for receiver provenance.
* `desired_topology_drafts.py` create/revise saves graph, draft/revision and
  action together. It does **not** save an identity projection. Selection later
  derives/saves one before desired-pointer update. `RealizedGraphProjectionRecord.
  identity_for_authored` already deterministically derives the real ID and digest;
  use it rather than a phantom projection reference or another ID allocator.
* `cpk_operation_actions` has `session_id`, not `workspace_id`; workspace truth
  belongs to `cpk_operation_sessions`. Existing graph and realized-projection
  composite unique keys establish workspace/source ownership. Draft revision
  has unique `(workspace_id, graph_id)` and a deferred draft-head FK.
* `admission.py:ExecutionAdmissionCommandService.execute` and `advancement.py:
  CurrentGraphAdvancementCommandService.execute` consume graph/plan/current-truth and A's
  guard; they do not yet implement F0 introduction/continuation/retirement rules.
  No new receiver admission is inferred from their existing policy/approval checks.

Smallest missing storage connections are a retained original binding, direct
workspace-safe witness references, actual draft projection insertion, and
derived graph indices sharing the existing transaction. Those do not by
themselves supply C's permission to introduce, continue, accept or retire.

## Inactivity finding and selected joint acceptance gate

Opaque V2 artifact persistence and graph/pointer selection are current graph
intent operations. They are **not** receiver lifecycle admission, runtime
adoption, deployment success or evidence of a new live vulnerability. However,
that distinction does not satisfy #1897's literal acceptance promise that no
supported graph entry bypasses C's complete law. An unused/private writer is not
an enforced boundary; FK existence and correct derivation do not grant authority.

Kepler and Meridian agree no smaller enforced seam has been demonstrated. North
selected the already-declared B+C contingency in the
[parent disposition](https://github.com/OpenJ92/control-plane-kit/issues/1882#issuecomment-5885365831),
[B update](https://github.com/OpenJ92/control-plane-kit/issues/1897#issuecomment-5885364985)
and [C update](https://github.com/OpenJ92/control-plane-kit/issues/1898#issuecomment-5885365409):

1. A temporary non-release collection branch,
   `codex/1882-receiver-lifecycle-integration`, starts at `2a1bf73`; a draft
   aggregate PR targets existing `roadmap/1813-runtime-control`.
2. B storage and C admission retain separately reviewed child PRs into that
   collection, in dependency order. B's internal merge is partial integration,
   not independent #1897 acceptance. Both issues retain their joint unmet laws.
3. No collection promotion or intermediate deployment until final B+C exact
   schema, all supported graph/selection/admission/advancement entries, replay,
   unresolved predicate/indexes and rollback laws pass together. D and downstream
   adoption start only at the accepted aggregate coordinate.

Entry accounting: authored/realized store saves preserve material; workspace
pointer setters/CAS publish selected intent; standalone/command authoring,
draft create/revise/select, saved preparation and publication compose those
stores; planning references them; execution admission and advancement consume
them. C must enumerate supported low-level entries and either require its full
validated composition or explicitly refuse unsupported successor use. Direct
raw SQL is not a product entrypoint. Existing historical profile/replay laws
remain. No absence-as-permission, identity fabrication or retrospective backfill.
The old-profile health selector remaining old-profile does not close generic
graph/selection bypasses. No blanket V2 detector/rejection framework is proposed.

## Exact relational shape proposed for review

Two graph-owned tables; no independent receiver registry/store-bundle service.
No lifecycle enum, provider field, secret material, clock or ID allocation.
Additional linkage columns below are normalized witness keys, not user inputs.

`cpk_graph_receiver_introductions`:

```text
workspace_id, receiver_id
runtime_id, node_id, provider_socket_name
introducing_graph_id, introducing_realized_projection_id
introducing_action_id, introducing_session_id
introducing_draft_id NULLABLE
first_accepted_action_id NULLABLE, first_accepted_session_id NULLABLE
retired_action_id NULLABLE, retired_session_id NULLABLE
```

* PK `(workspace_id, receiver_id)`; global UNIQUE `(receiver_id)`; supporting
  UNIQUE `(workspace_id, receiver_id, runtime_id, node_id, provider_socket_name)`.
* Receiver ID is exactly 32 lowercase hexadecimal characters. Scope uses Core's
  existing role-specific reference validation; no new meaning for old graph IDs.
* Acceptance and retirement action/session pairs are either both null or both
  present. Retirement requires first acceptance and a different action witness.
  No default witness or inferred lifecycle status.
* Introducing graph FK uses existing `(workspace_id, graph_id)` ownership.
  Introducing projection uses existing `(projection_id, workspace_id)` and
  `(projection_id, source_authored_graph_id)` keys, requiring that exact source.
* Each action/session pair references a new UNIQUE `(action_id, session_id)`
  on actions; each session/workspace pair references the existing sessions key.
  This establishes all three witnesses' workspace through the owning session
  without adding/repopulating `workspace_id` on every action record.
* Optional draft provenance FK `(workspace_id, introducing_draft_id,
  introducing_graph_id)` references a new UNIQUE `(workspace_id, draft_id,
  graph_id)` on draft revisions. It proves the original graph belongs to that
  retained draft revision, not merely that a same-workspace draft exists.

`cpk_graph_receiver_bindings`:

```text
workspace_id, graph_id, realized_projection_id
runtime_id, node_id, provider_socket_name, receiver_id
selected_configuration_digest, declaration_identity
```

* PK `(workspace_id, graph_id, realized_projection_id, node_id,
  provider_socket_name)`; UNIQUE `(workspace_id, graph_id,
  realized_projection_id, receiver_id)` prevents one ID binding twice in a graph.
* Graph/workspace and projection/workspace/source FKs reuse existing exact keys.
  Full scope FK `(workspace_id, receiver_id, runtime_id, node_id,
  provider_socket_name)` references the introduction scope key. The redundant
  runtime column allows PostgreSQL to reject scope substitution directly.
* Both digests are exact lowercase SHA256. Configuration digest comes from the
  selected artifact's `content_digest`; declaration identity comes from the
  decoded declaration's `identity().value`. Neither is independently writable
  caller evidence, and neither substitutes for configuration slot selection.
* Introduction's original `(workspace, graph, projection, receiver)` references
  this binding's unique key with **DEFERRABLE INITIALLY DEFERRED** FK. This is the
  sole new deferred edge: insert introduction before binding, but committed
  introduction cannot lack its exact original binding. Its projection column
  preserves actual origin instead of guessing identity projection on read.

All new FKs use NO ACTION, with no CASCADE deletion/update. Tombstoning a draft
does not delete either index or its witnesses. Only constraint-owned supporting
indexes are proposed in B. Execution lookup and its indexes stay in C. Exact
constraint/index names and full catalog entries must be frozen with target
design; no count/hash is guessed from this prose. No triggers, functions or
installer policy expansion enforce immutability.

## Store API and composition boundary proposed for review

Typed internal records live in an Operations receiver-lifecycle value module;
graph-owned PostgreSQL implementation may use a narrow internal helper module.
The public store owner remains `PostgresGraphTopologyStore`; no second service,
bundle-level registry, network route, root re-export or client binding DTO.
Read interfaces are workspace-scoped point/member reads:

```python
graphs.receiver_introduction(workspace_id, receiver_id)  # record or None
graphs.receiver_bindings(workspace_id, graph_id, realized_projection_id)
```

Member reads load exact bounded graph/projection material and bound query output
by its derived receiver count plus one; mismatch/overflow refuses, never returns
a partial membership set. Installer validation uses existing 64-row keyset batch
and bounded descriptor transport conventions; no ancestor scan or global list.

Implementation review refinement (North, Meridian and Kepler): exact-current
receiver validation traverses the union of retained binding projection identities
and every introduction origin. It strictly rederives each complete referenced set
and all original provenance. Unindexed generic history retains its existing
validation; a wrapper environment name alone adds no receiver requirement.
Explicit receiver member reads and writers remain strict, including missing
selected artifacts and malformed selected successor material. Distinct historical
V1 and successor V2 nodes may coexist; B does not impose graph-wide profile policy.

This scan cannot discover omission or deletion of ALL continuation bindings for
a later non-origin projection. Direct member reads still rederive and refuse
that missing set; original origins remain referenced and physically protected.
C's supported graph/projection publication must atomically establish complete
receiver membership or refuse. Universal admission/completeness remains a joint
B+C gate obligation, not storage-only acceptance or a raw-SQL omission guarantee.

Internal persistence operations, available only within the coordinated C
composition, have these responsibilities (names are provisional, laws are not):

```text
reserve_introductions(exact graph/projection, original action/draft, guard)
persist_derived_bindings(exact graph/projection, guard)
record_first_acceptance(exact receiver/action witness, guard)
record_retirement(exact receiver/action witness, guard)
```

There is no public `save(binding_row)` or arbitrary lifecycle update/delete.
Material is decoded/validated through Core's existing graph codec, receiver
configuration selector and codec. Exact workspace/runtime/node/socket, selected
slot, configured declaration, target ID and digests must agree. Duplicate IDs,
malformed/mixed selected successor material and foreign scope refuse generically.
Unrelated application artifacts are not scanned for suggestive JSON keys.
The profile selection decision and allowed original-action mapping belong to
C's complete graph admission design, not a partial B detector.

Stores validate structural identity/material and active same-owner/workspace
guard; C validates authority, action semantics, allowed continuation/current or
pending source, retirement eligibility and unresolved execution. A syntactically
valid same-workspace action is not automatically a lawful witness. No placeholder
C token, permissive flag or caller assertion is treated as that validation.

Reserve insert is immutable. An exact same-workspace immutable tuple can replay;
conflicting/global duplicate ID returns a fixed bounded conflict without exposing
the owner workspace, SQL detail or a distinguishable global membership read.
First acceptance is NULL-to-exact-witness once; retirement requires acceptance
and is NULL-to-exact-witness once. Identical witness replay reads original truth;
changed/cleared witnesses refuse, and retirement cannot be undone. Implement
these laws with guarded store mutation under A's guard, not unrestricted updates.
CHECK/FK constraints prove valid committed shapes/references; they do not prove
temporal immutability against privileged raw SQL. Current-row semantic validation
detects inconsistent derivations/references; it cannot reconstruct a deleted past
value. This is the existing trusted store/interpreter boundary, not a DB trigger
or hostile-administrator guarantee.

## Insert, lock and transaction plan

Caller acquires command key → A workspace guard → required request/run/attempt →
session → workspace → reviewed draft/graph suffix, without a later first key.
Cross-workspace receiver claims sort receiver IDs identically before global-unique
inserts; same-workspace claims serialize through A's guard. Global conflicts do
not lock/read foreign workspace rows or disclose them.

For a newly introduced draft receiver, the coordinated command writes graph →
actual deterministic identity projection → draft/revision → original action →
sorted introduction rows → derived binding rows. The existing deferred draft-head
edge and new deferred original-binding edge resolve by commit. Inline authoring
omits draft writes; projection publication uses its actual stored projection.
The action row exists before its immediate witness FKs. Pointer/head CAS and all
history/index writes share the caller UoW: any stale CAS, late action/index/FK
failure, commit-request exception or physical commit failure rolls back all.
Exact C integration placement must also preserve A's auxiliary suffix and action
ordinal locks; a proposed new inverse edge stops for review rather than moving
an earlier lock late. Standalone authoring cannot invent missing action provenance.

Advancement first performs C's complete-success/association/admission checks,
then writes its action and first-acceptance/retirement witnesses with current CAS
in one UoW. Desired omission or draft tombstone does not retire a receiver.
Stores never commit, allocate IDs, sign, observe clocks or call providers.

## Exact-schema data decision and intended targets

The eventual joint baseline adds these two tables and supporting constraints,
updates SQL/current semantic contract/hash/catalog expectations, and extends
bounded current-row validation. Fresh empty install remains transactional;
current verification is query-only; old baseline, partial baseline and drift
refuse unchanged with existing reset-required guidance. A failure rolls back all
new and old DDL from that install. Noncurrent namespaces retain their data under
compatible tooling; no reset/export/import/migration/backfill is authorized.
Only fresh disposable namespaces are used during B/C implementation testing;
the intermediate collection baseline is not deployed.

After reviewed design and release, the smallest real-PostgreSQL targets are:

1. Exact object-free/current/old-baseline/index/FK drift laws, stable OIDs/rows and
   no reentry DDL/DML, outer rollback and concurrent installer convergence.
2. Two independent workspace transactions racing one global receiver ID produce
   one retained owner and bounded loser, with no foreign values in any exception.
   Same-workspace exact replay preserves original immutable graph/action/draft.
3. Each graph/projection/source/action-session/draft FK rejects crossed ownership;
   deferred original-binding failure rolls back the attempted command at commit.
4. Derived bindings preserve exact artifact bytes, slot, scope and declaration;
   changed digest/target, duplicate use, phantom projection or orphan origin refuse.
5. NULL→accept→retire, identical-witness replay, changed/cleared witness refusal,
   retire-before-accept refusal, and retained tombstoned-draft/history references.
6. Existing command replay/CAS/late-action rollback targets are strengthened at
   C integration, including graph/projection/action/index rollback and no ID remint.
   Existing A guard/protocol laws remain; no synthetic policy substitute.

The ordinary gate is `./control-plane-kit-operations/test.sh` with the exact clean
architecture-testing sibling above. Focused causal-red evidence, if required by
the released target contract, still uses that owning gate. No host Python/DB,
per-test wrapper or alternate dependency. Apparatus stop rule remains unchanged.

## Review, risk and downstream handoff

Review must settle the proposed cyclic FK, action/session and draft composite
keys, DB-versus-store immutability boundary, exact method contracts and C's
complete activation design within the selected gate before targets/source. New modules require exhaustive inventory
entries and implementation mirrors; source changes stay Operations-owned.
Objects are immutable graph material and normalized witnesses; derivation maps
material to bindings, and the caller transaction commits lawful C-validated
transitions atomically. Foreign keys are integrity laws, not authorization.

Security: no new network, credential, provider or secret surface. Global conflicts
are bounded and tenant-blind. Risks are a missed entry/admission bypass, deferred
FK/commit failure, implicit suffix lock inversion, or treating present/absent
storage as permission. History references are retained; no cleanup or reset API.
North owns topology/release; Meridian reviews independently. No B acceptance,
C/D/adoption/live release, or timer restart follows from this planning document.
