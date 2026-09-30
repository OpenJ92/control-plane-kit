# O1.D receiver authoring context targets

Status: **target-only, unexecuted, awaiting independent target-integrity review**.
No production/interface shim, dependency upgrade, push, Docker invocation or
provider action accompanies this checkpoint. Selected base is
`0aadecdef61e106bc628157974989fe3aba405d3`, tree
`fa9b51628f407b3d81bcc463c100e7e399b7a1b6`; branch
`codex/1899-receiver-authoring-context` follows the existing O1 integration
handoff. North owns the subsequent destination/publication/execution release.

Governing [#1899 target-only release](https://github.com/OpenJ92/control-plane-kit/issues/1899#issuecomment-5917581601)
accepts the planning artifact with SHA256
`11accdced30b06ce4afcbe57fe2d8640247c097f8f5d6ace03f8be50e7ce2013`.
The [Servers #238 adoption handoff](https://github.com/OpenJ92/control-plane-kit-servers/issues/238#issuecomment-5917581953)
is already durable. The frozen planning record follows below; its statements
about no targets/commits are historical planning status, superseded only by this
checkpoint section.

## Target shape and law mapping

Paths below are under `control-plane-kit-operations/tests/` except Core.

| Target | Governing law cards / concrete observation |
|---|---|
| `test_receiver_authoring_context.py` exact success | D-R1/R3/R4, strengthened/new N8: literal closed descriptor, exact selected original bytes/digests, source binding and immutable action attribution; direct/HTTP/MCP-shaped equality; existing redaction unchanged. |
| Same draft and legacy head | D-R4/R6: original introducing action survives real revision; desired and live head retain distinct content; stale/tombstoned/foreign/missing head refuses; receiver-free legacy head does not mint an identity projection. |
| Closed query and authority | D-R2/R9: both scoped read grants, operator kind, principal presence, no payload grants; direct service and adapter refuse before UoW allocation; no arbitrary graph/cursor/partial expectation; 400/403/404/409 remain categorical and cause-free. |
| Original action/binding corruption | D-R4/R7: valid selected successor still requires the exact original source and introducing-action correspondence; whole bounded refusal without echo. |
| `test_receiver_authoring_context_snapshot.py` owner schedules | D-R5/R6/R8, new N8: pause after a real fetched data row; actual desired/head/advancement owner commits while reader is held; old response is complete, next read is new; stale later mutation refuses. Current advancement uses the existing explicitly assumed completion fixture, never claimed receiver update/health evidence. |
| Snapshot UoW lifecycle | D-R8: real PostgreSQL reports repeatable-read/read-only before data; it rejects a write; already-used connection and entry setup failure close/rollback; ordinary UoW defaults remain. |
| Retired attribution | D-R6: real teardown removes receiver sources without exposing the global reservation; corrupt reselection of a retired source refuses. |
| `test_receiver_authoring_context_bounds.py` capacity | D-R7/new N8: 64 source bindings including duplicates succeed, 65 refuse; selected JSON exactly 64 KiB preserves legal whitespace; original graph/projection/action corrupt oversize is SQL-guarded; ancillary actor guard and excluded arbitrary metadata; one context-wide aggregate allowance over distinct real originals. |
| Serialized body | New N8: 1 MiB exact body succeeds and one additional legal whitespace byte refuses; original JSON quotes/backslash/newline/tab remain; a lawful receiver-free UTF-8 workspace reaches its 2 KiB bound. The named body serializer is used, not HTTP percent encoding or an MCP envelope. |
| Core `test_receiver_authoring_context_contract.py` | D-R10: one nonpaged read declaration, explicit 16 KiB request/1 MiB response schemas, projection/policy and MCP parity identity, roundtrip and unchanged old request metadata. |
| Existing exact Core catalogue assertions | D-R9/R10: additive expected entries only in `test_read_projection_contract.py` and `test_adapter_parity_contract.py`; no old entry is removed or equality weakened. Existing Operations exact route/policy equality remains unchanged. |

The pass-through `receiver_authoring_context_fixture.py` creates requests,
resolves the proposed public API inside test methods, and observes actual
PostgreSQL cursors. It never supplies rows or projects the response. Data-mode
observation uses `SHOW`, not a separate MVCC data read. Barriers release in
`finally`; the writer must finish while the reader is held, also detecting a
lifecycle/workspace lock improperly acquired by the read. Each measured context
must open one connection, use one snapshot, close, and stay within all four
transport ceilings. No registry reselection or execution-outcome traversal is
allowed by the observed statements.

Field spelling fixed by these targets: top-level `profile`, `workspace_id`,
`expectation`, `current`, `desired`, `pending_draft`; source projection key
`realized_projection_id`; draft `head_revision`. Receiver origin has exactly
five introducing references plus the two first-accepted references. Direct
service refusals use the existing `ReadModelError` family; adapter errors retain
`CpkServerApplicationError` and the reviewed status contract. Exact diagnostic
message text remains a bounded categorical implementation choice, not a fixture
string. These concrete spellings are part of Meridian's target review.

## Boundary-case evidence limits

Meridian's static review of `9aa25142` held only for missing selected-material
negatives. The additive correction exercises the D read with missing V2 verifier
fields, duplicate JSON profile keys, a selected environment slot pointing to an
unrelated application artifact, and exactly 65,537 content bytes. Each case
starts with a real admitted receiver and published successor projection. Generic
Core artifact/graph/projection construction and the existing digest owner keep
outer material coherent; only the selected receiver representation is invalid.
A separate syntactically malformed JSON cell is injected below the generic
artifact owner with correct outer hashes, explicitly a corruption case. Every
case requires the same bounded 409 category, measured snapshot cleanup, unchanged
durable truth and successful restoration of the original exact read. No owner
validation is weakened to make these records admissible. This correction is
unexecuted and returns to Meridian before North's intended-red release.

Tests do not invent fields in closed action/query/configuration payloads to
manufacture an exact-cap positive. Selected JSON whitespace and existing public
application artifact content are legitimate values, with unchanged validators.
The exact 1 MiB body case changes only pending-head content; old original and
selected desired material remain real records. Its precondition checks fixed
identity/digest widths and available legal artifact capacity.

Graph/action oversize tests explicitly inject corrupt database cells; they prove
pre-transport guards and whole refusal, not successful authoring at those sizes.
The row/statement/aggregate ceilings are measured over actual queries, probes
and scalar values. The aggregate case uses distinct admitted originals to
prevent source deduplication from accidentally turning a global limit into a
per-source limit. It does not claim an exact byte/row/statement-cap positive.
The unchanged smaller closed field grammars may make some nominal ceilings
unreachable; there is no padding-key or synthetic-selector apparatus to force
those boundaries. Duplicate raw HTTP query keys and JSON-RPC envelopes belong
to Servers #238, outside this framework-neutral mapping checkpoint.

## Expected red versus apparatus

No executable result is claimed. New modules are resolved using `find_spec`
inside test methods, following the existing catalogue fixture convention;
there are no imports of absent application modules during collection. The
accepted base should fail on absent public read/declaration/snapshot behavior.
Existing default-UoW and unauthorized adapter paths can remain green. A broken
fixture, import, database setup or collection is **not** an intended red and
must stop under the owning suite rule. No skip, xfail, synthesized result,
production collection shim or alternate runner is included.

Before any execution or publication, Meridian reviews this exact local target
commit/tree and North releases the intended-red boundary separately. Eventual
owning commands remain `./control-plane-kit-operations/test.sh` with exact
architecture-testing prerequisite `7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`
and `./control-plane-kit-core/test.sh`. Only static `git diff --check` has been
performed at this stage. No host Python/import/compile validation was used.

Security/data/history: the tests intentionally disclose only public selected
configuration to combined graph/key readers, assert read-only transactions and
bounded error/transport behavior, and preserve provenance without minting
history. Fault injection and cleanup are confined to the established disposable
package fixtures. There is no new runtime exposure or provider mutation in this
checkpoint. Remaining risks are implementation, unexecuted fixture assumptions,
and actual Servers transport/adoption, which remain explicit later boundaries.

## Frozen accepted planning record

# #1899 test-conditioned dry run: receiver authoring context

Planning only, 2026-09-30. Selected accepted roadmap commit `0aadecdef61e106bc628157974989fe3aba405d3`, tree `fa9b51628f407b3d81bcc463c100e7e399b7a1b6`. [Current issue](https://github.com/OpenJ92/control-plane-kit/issues/1899), [accepted B+C milestone](https://github.com/OpenJ92/control-plane-kit/pull/1901#issuecomment-5917233415), [final-head owning evidence](https://github.com/OpenJ92/control-plane-kit/pull/1901#issuecomment-5917206799). The exact combined owner suite is green: Operations 2,097 tests / 2,690.201s, Core 907 + 21 integrity, compile/import. This plan adds no executable result. No source, targets, commit, push or run has been performed for D.

## Governing law cards, inspected before target design

Paths in this table are relative to `control-plane-kit-operations/tests/` unless marked Core. I = isomorphic, S = strengthened, N = new-law. These are current governing tests; no historical package rerun is needed.

| Card | Inspected governing method | Observable law / negatives | Structure to discard; future owner |
|---|---|---|---|
| D-R1 I/S | `test_read_services.py::test_workspace_and_graph_reads_are_redacted`; `test_workspace_graph_read_projection.py::test_pointer_and_graph_failures_are_categorical_and_candidate_free` | Existing reads retain graph redaction and bounded cause-free decode errors; new exact public material uses a separate explicit read. | A redacted graph is not lossless authoring input; projection owner. |
| D-R2 I/S | `test_cpk_server_adapters.py::test_principal_for_another_workspace_is_denied_before_store_access`, `::test_forged_payload_scopes_do_not_authorize_an_ungranted_principal`, `::test_use_and_read_permissions_do_not_imply_execution_or_mutation` | Trusted workspace and focused permissions precede stores; payload scopes and edit permission cannot grant key-read authority. | HTTP/MCP spelling is not authentication; Operations application boundary. |
| D-R3 S | `test_cpk_server_adapters.py::test_delegation_key_routes_drive_overlap_through_http_and_mcp`; `test_gateway_security_read_projection.py::test_key_inventory_and_verifier_work_without_probe_store` | Explicit key-read permits public verifier material while private references stay absent; both mappings agree. | Do not call the live registry selector or require its current active key to read installed graph material; D projection. |
| D-R4 S | `test_receiver_graph_admission.py::test_selected_pending_continuation_and_new_identity_keep_distinct_real_origins`, `::test_same_live_head_continues_pending_origin_and_reserves_only_new_identity`, `::test_copied_other_draft_cannot_continue_origin_or_leave_new_identity`, `::test_selection_requires_current_live_pending_head_and_persists_exact_pins` | Exact selected desired/live draft and original introducing action distinguish lawful pending continuation from historical copying. | No arbitrary graph-ID/history search or manufactured retention permission; existing graph provenance owner plus read projection. |
| D-R5 S | `test_receiver_graph_admission.py::test_matching_desired_with_stale_current_pair_refuses_atomically`, `::test_duplicate_desired_fields_must_agree_with_product_before_writes` | All five current/desired pins matter; later mutation rejects stale expectations. | Equal receiver IDs are not CAS; read emits expectations, admission still owns enforcement. |
| D-R6 S | `test_receiver_graph_admission.py::test_omission_then_tombstone_preserves_reservation_and_blocks_historical_copy`; `test_receiver_acceptance_advancement.py::test_real_advancement_alone_creates_acceptance_from_assumed_completion` | Introduction reserves; current advancement accepts; omission/tombstone does not retire. | No accepted managed-update or health proof inferred from fixtures; read observes accepted facts. |
| D-R7 S/N | `test_receiver_execution_scope_transport.py::test_oversized_original_plan_or_graph_never_reaches_python_as_a_full_cell`, `::test_growth_after_length_probe_returns_bounded_unavailable` | Bounds apply before transport/decode and survive growth; incomplete/exhausted evidence is refusal. | Reuse the law, not the execution-history reader/state machine; new bounded context transport. |
| D-R8 I/N | `test_unit_of_work.py::test_uncommitted_and_exceptional_exits_roll_back_and_close`, `::test_physical_commit_failure_rolls_back_closes_and_propagates` | Existing defaults and lifecycle stay unchanged; new read has one fresh snapshot transaction and closes on every outcome. | Several READ COMMITTED statements are not one snapshot; PostgreSQL UoW owns isolation. |
| D-R9 I/S | `test_cpk_server_adapters.py::test_every_public_route_has_an_explicit_authorization_policy`, `::test_read_errors_are_bounded_without_sql_or_secret_leakage` | Exact route/policy coverage and bounded safe failures survive. | Do not add an uncatalogued route; closed framework-neutral mapping. |
| D-R10 S/N | Core `test_read_projection_contract.py::test_operator_overview_is_one_closed_http_mcp_read_projection`; `test_draft_catalogue_contract.py::test_catalogue_reads_have_distinct_http_and_read_only_mcp_contracts`; `test_adapter_parity_contract.py::test_projection_bindings_must_match_http_route_service_and_schema` | One declared read identity has congruent HTTP contract, read-only MCP name, response schema and security parity. | Declaration is not an implemented/deployed transport; Core data contract, Servers #238 adapter. |

N8 adds exact allowed public configuration, complete-or-refused bounded context and concurrent consistent-snapshot evidence. Old test layout/fake stores must not force a copied admission state machine or a transport implementation here.

## Reused trace and the bounded missing connection

Reuse the accepted B/C source trace and supported-entry matrix; do not retraverse lifecycle, effects, scope classification or advancement. The affected path is:

`trusted context → explicit read policy → owned snapshot UoW → graph/draft/provenance facts → Core selected-configuration validation → detached closed public context → framework-neutral response`.

Current anchors:

- `cpk_server.py:236–289,468–536,1897–1920`: route policy, authorization before UoW, ordinary read construction, trusted workspace context. Ordinary read UoW presently specifies no stable-snapshot mode.
- `postgres/unit_of_work.py:32–111`: owns connection/transaction lifetime; `postgres/stores.py` binds stores to that connection. Add no route SQL and change no default command/other-read isolation.
- `read_services/workspace_graph.py:23–111` and its existing redaction helpers: separate redacted workspace/graph response remains untouched.
- `receiver_lifecycle.py:104–174,290–339`: selected current/desired/live-head provenance meaning and existing introduction/binding records. `_receiver_sources` includes fresh-permission/acceptance evidence work: D must not invoke the execution conflict machine or advertise a cached admission decision.
- `postgres/receiver_lifecycle_store.py:26–110`: SQL-side guarded cells, exact workspace graph/projection ownership and derived/stored binding equality. Its material routine discards records after deriving bindings; it can expose a small bounded material read to its existing graph owner, not duplicate graph truth.
- `postgres/graph_store.py:387–439`: exact introducing action/session/workspace and action-variant correspondence. Reuse this validation; its raw graph/projection getter calls must not bypass the new context budget. Factor only the necessary bounded read seam, preserving existing owner semantics.
- `postgres/desired_topology_draft_store.py:46,92`: exact workspace draft and revision selectors. Use live head only, no historical fallback or identity-projection persistence on read.
- Core `receiver_configuration.py:39–114`: closed V2 configuration and exact artifact selector; `configuration.py:44–88` supplies exact content/digest/artifact identity; wrapper configuration ceiling is 65,536 bytes.
- Core `operations/http.py:300,868`, `projections.py:267`, `parity.py:711,819`: declarative route, projection and MCP-name linkage. Security parity is derived from these read bindings. `operations/mcp.py` is streamable-HTTP protocol data and requires no new registration mechanism.

## Proposed field-level contract

Names are the proposed public interface for review, not released code. One request `ReceiverAuthoringContextQuery`, one successful `ReceiverAuthoringContext`, one dedicated snapshot-owning `ReceiverAuthoringContextReadService.read(query, context=TrustedCommandContext)`. The public service enforces auth and snapshot even when called without the CpkServer adapter. It belongs to Operations; no Core deployment algebra or domain language is invented.

Request:

| Field | Shape / meaning |
|---|---|
| `workspace_id` | Existing bounded workspace identity. Must equal the trusted context workspace. |
| `expected` | Optional complete existing `ReceiverLifecycleExpectation` five-pin product; absent means capture current snapshot. Partial fields/extra keys are invalid. An explicit product compares only to the captured snapshot. |
| `pending_draft` | Optional closed `{draft_id, expected_head_revision}` with existing draft-ID/int8-positive revision rules. Read exactly this live head or refuse; no implicit latest/historical fallback. |

No caller receiver list, arbitrary graph IDs, cursor/page size, permission flag, key-selection request, metadata or mutation input. Reading all receiver bindings in current/desired/one named live draft avoids a subset silently masquerading as complete authoring context.

Successful response (closed descriptor profile `receiver-authoring-context.v1`):

| Field | Exact source / disclosure |
|---|---|
| `workspace_id` | Authorized workspace only; omit arbitrary workspace metadata. |
| `expectation` | Existing five pins: current graph/projection, desired graph/projection, desired generation. Existing `ReceiverLifecycleExpectation` requires non-null current IDs. Unassigned current is bounded refusal, never a widened expectation. Only the desired pair may be null, with generation zero; half-pairs refuse. |
| `current` / `desired` | Each assigned source has graph ID, realized projection ID and a complete deterministic receiver tuple. Current must be assigned; desired may be explicit null under the existing expectation rule. An assigned receiver-free graph has an empty tuple. |
| `pending_draft` | Null or exact live draft ID/head revision/graph ID and identity-projection ID, plus complete receiver tuple. A legacy receiver-free head without a persisted identity projection is represented without minting/persisting one. Its absent projection is explicit. |
| receiver `binding` | Existing workspace/runtime/node/provider socket/receiver IDs, source graph/projection IDs, selected configuration digest and declaration identity. Derived values must equal stored binding records. |
| receiver `configuration_artifact` | Exact selected V2 artifact descriptor: artifact ID, target path, media type, file mode, original UTF-8 content, content digest, source digest. Closed V2 decode proves allowed target/declaration/public verifier families only; preserve original content bytes/whitespace rather than recanonicalizing. No other artifacts, environment or metadata. |
| receiver `origin` | Original introducing graph/projection/action/session/draft references; first-accepted action/session references where present. Validate original binding and introducing action correspondence under existing graph-owned rules. First acceptance is a recorded graph fact, not recomputed execution success. No full action payload or actor metadata is returned. |
| receiver `lifecycle` | `current` only when recorded accepted origin corresponds to captured current membership; `pending` for unaccepted origin in selected desired or the explicit live draft. A retired/dangling/mismatched source refuses as a whole. These labels project recorded graph facts, not a fresh execution proof, cached admission decision or retention/selection permission. |

The same receiver may appear in different source tuples with different selected configuration bytes. Do not flatten away its source-specific association. Deduplicate internal reads only, not public meaning. Retired/global omitted reservations are not a newly exposed catalogue. Exact retry uses the author's retained preparation artifact and original command receipt, not regeneration from this latest-context read.

## Snapshot, authorization and bounds

Kepler's bounded consultation recommends a narrowly requested `READ ONLY` + `REPEATABLE READ` transaction at the PostgreSQL UoW boundary. Proposed local framework-neutral protocols in the new read module are `ReceiverAuthoringSnapshot` (bounded graph/workspace/draft read capabilities), `ReceiverAuthoringSnapshotUnitOfWork.read_snapshot() -> ContextManager[ReceiverAuthoringSnapshot]`, and `ReceiverAuthoringSnapshotFactory = Callable[[], ReceiverAuthoringSnapshotUnitOfWork]`. `ReceiverAuthoringContextReadService` receives that factory. The existing PostgreSQL UoW supplies this explicit snapshot entry; CpkServerReadService passes its ordinary UoW factory to the dedicated read service because the concrete PostgreSQL UoW implements both entries. No Postgres import enters the read module and no change is required at ordinary factory consumers.

The snapshot entry is fresh/unentered-only, opens exactly one connection/transaction, verifies no data snapshot has already been acquired, sets the mode before the first data read, then vends bounded read capabilities over the same store bundle. An already-used factory connection refuses; mode-setup failure during context entry must itself rollback/close because normal context exit will not be called. The dedicated public read service owns that entry; arbitrary pre-opened stores cannot be passed to its public read method as a claimed snapshot. Default `with PostgresUnitOfWork(...)` behavior remains unchanged. The direct service and adapter share this owner. These are narrow local protocols, not a generic transaction-mode framework.

Consistency means one complete committed database snapshot as of its first data read. It may be superseded before delivery. No end-of-read live recheck is claimed, and later mutation rechecks live pins. No L/advisory lock, `FOR UPDATE`, external I/O or new isolation/retry framework. On success detach the complete value before transaction exit; on any failure rollback/close, with no durable action/event/ID allocation. Existing read security parity remains history-not-recorded.

Require both `INSTANCE_WORKSPACE_READ` and `DELEGATION_KEY_READ`, existing operator principal kind and explicit workspace grant, before creating the UoW or decoding graph/configuration material. Edit-only, key-register, worker/service, forged payload scopes and another-workspace context refuse. Missing principal/workspace/scope follows existing 403 behavior without enumerating stored identities. Exact selected configuration is the public-key source: no key-registry active-set lookup, signing, key allocation or private-reference lookup.

Proposed explicit budgets for review:

- At most three selected source tuples and 64 total returned source bindings (duplicates across sources count). Overflow is whole refusal; no pagination/truncation.
- Each graph/projection descriptor at most 1,048,576 UTF-8 bytes, matching existing receiver storage guard; each selected configuration at most 65,536 bytes. Text identifiers at most existing 2,048-byte receiver bound, with tighter existing identity grammar where present.
- One aggregate 8,388,608-byte transport budget over the context's selected material and required original graph/action/provenance reads; one decoded origin per unique receiver and one material load per unique graph/projection. Each introducing-action payload at most the existing 65,536-byte ceiling. No unbounded session/action/graph metadata is transported for this read.
- Whole serialized public context body at most 1,048,576 bytes; normalized typed query body at most 16,384 bytes. The exact count is `len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"))`. It includes the context/query object, all its fields, JSON escapes and UTF-8 non-ASCII bytes. It excludes future HTTP headers, percent-encoded query bytes, MCP JSON-RPC envelope and any transport-specific escaping, which #238 must independently bound. No NaN/infinity or alternate serializer can silently change D's count. Raw wire validation remains a separate transport boundary.
- Validate cardinality/bytes before graph decoding; SQL-side CASE/length guards prevent oversized cells crossing into Python. Each bounded batch/point lookup reserves from the same context budget. Cap total transported rows at 4,096 and data-read statements at 1,024, counting probes and repeated reads; exhaust either before issuing the next read and refuse the whole context. These are conservative application ceilings, not a DB CPU/scan guarantee. The selector accounting below covers the complete read without a second generic query framework.

Exact equality at each cap succeeds when all other laws hold; cap+1, malformed binding/configuration/origin, missing required source or mismatched pending action returns one bounded categorical refusal and no partial successful context. Missing workspace/draft remains bounded 404 after authorization; malformed query 400; stale supplied pins/live-head expectation 409; unavailable/corrupt/over-budget context, including unassigned current, uses a fixed safe 409 category. No submitted material, SQL, provider response or causal exception is echoed. Final error class/status spelling is part of Meridian's contract review.

Bounded validation factoring is explicit: the graph-owned context read loads only pins/live-head coordinates and bounded graph/projection/binding/origin/action records. It reserves each row/statement/value-byte allowance before fetching/decode; SQL guards suppress oversized cells, including workspace/draft/action text, and arbitrary metadata is never fetched. Cache each immutable graph/projection by its complete workspace/source identity within this one snapshot. Extract the existing origin-action correspondence checks into a small pure internal validator accepting the bounded action, graph, projection and exact original-draft-revision fact; both the current origin checker and D use that validator. The existing mutation caller keeps its behavior. D must not call `_require_receiver_origin_action` through its current raw `.get` enrichment or wrap unbounded stores and claim a budget afterward. Likewise expose/reuse material validation over already bounded graph/projection records and exact bounded binding rows; no copied admission classifier, execution-history traversal or in-memory imitation store. The same context budget covers original provenance material as well as selected material.

### Closed selector and reservation ledger

These are bounded graph-owned read operations, not arbitrary table/query input from callers. All scope values are validated parameters. SQL projects text/JSON/timestamps explicitly as text (and fixed boolean probe flags); every UTF-8 value byte is charged, including identifiers and internal created-by/time fields needed by existing record validation. JSON text is guarded before decoding. Do not load full workspace/session/draft metadata merely to construct an existing large record.

| Selector | Exact scope / data needed | Cardinality and budget ownership |
|---|---|---|
| Workspace pins | `workspace_id`; workspace ID, current/desired graph+projection IDs, desired revision only | One unique-key point. No name or metadata. Unassigned current refuses. |
| Optional live draft/head | requested workspace+draft; draft ID/workspace/head/deleted marker, exact head revision's graph ID | One draft plus one requested revision point; require live/exact head. No title/metadata, no history page. |
| Authored graph material | workspace+graph ID; graph ID/workspace/version/descriptor/created-by/time | One point per distinct ID; descriptor ≤1 MiB, bounded ancillary text. Exclude graph record metadata; the pure identity projection law uses descriptor and creation fields, not that metadata. |
| Realized material | workspace+projection ID+source authored ID; projection kind/key/digest/descriptor/creation fields | One point per distinct complete source identity; descriptor ≤1 MiB; decode and validate its digest/ownership. Legacy receiver-free draft absence is explicit, not generated storage. |
| Binding rows | workspace+graph+projection; all existing ReceiverBinding fields | Deterministic node/socket order, ≤64 per queried source plus one overflow sentinel; total returned selected-source bindings ≤64. Original-source rows also debit the global row/byte budget. Require exact equality with derived membership. |
| Original introduction | workspace+receiver ID; all existing ReceiverIntroduction fields | One point per unique selected receiver; scope/origin/first-accepted/retired reference pairs validated; no global ID lookup. |
| Introducing action/session witness | exact original action ID+session ID, joined to authorized workspace | One point; bounded payload ≤64 KiB and bounded action identity/type/fingerprint/creation fields needed by existing action validation. Join only session workspace/identity, not metadata. Existing closed SET_DESIRED/CREATE_DRAFT/REVISE_DRAFT/PUBLISH variants retain their original correspondence checks. |
| Original draft revision witness | origin workspace+draft+action revision+graph | Fixed boolean EXISTS; validates original revision correspondence, never substitutes a historical head as a selected source. |
| Recorded acceptance reference witness | origin first-accepted action/session and workspace when present | Fixed boolean EXISTS for recorded foreign-key attribution; no run/attempt/outcome/health traversal and no recomputation of acceptance. Current membership supplies the contextual lifecycle label. |

One private context-local budget object starts once for the whole snapshot. Before each SELECT reserve one statement, its declared maximum result rows including missing/overflow sentinels, and the upper bound of all scalar transport values. For a size/count probe with at most 32 scalar columns, reserve 512 value bytes per possible probe row before execution (nulls cost zero, boolean one, each returned length is a bounded decimal scalar); after retrieval release only unused reservation and keep actual rows/bytes charged. A zero-result point still spends its statement; a returned missing/overflow flag row spends its row/flag bytes.

For material retrieval, use only probed bounded candidate rows, reserve their complete observed UTF-8 cell lengths plus validation/overflow flags before executing, and repeat those per-cell limits in SQL CASE guards. Enforce the row cap in SQL with one counted overflow sentinel; exceeding a nominal family cap or any global reservation refuses without a partial response. Charge actual returned text/flags before JSON decode; no raw-get fallback, per-source reset or uncharged action/origin enrichment. Snapshot isolation ensures probes and retrieval see one committed version; guards still prevent a malformed/corrupt cell crossing the boundary. Cache only complete validated immutable records under exact IDs in this context; a hit incurs no transport and must not mint a second budget. Final response JSON sizing is a separate check after complete public projection.

## Minimal catalogue/application shape and alternatives

North explicitly expanded the planning ceiling to the minimal pure Core catalogue delta. Propose one route ID `read.receiver-authoring-context`, GET contract `/workspaces/{workspace_id}/receiver-authoring-context`, READS/read/read-only, nonpaged `ReceiverAuthoringContextReadResponse` capped at 1 MiB and `ReceiverAuthoringContextReadRequest` query contract capped at 16 KiB; MCP parity name `get_receiver_authoring_context`; new projection kind and explicit public receiver-authoring policy. The optional query fields are closed adapter data, not path identities. HTTP path workspace and payload/query workspace must never conflict; MCP arguments carry workspace exactly once. Both map to the same typed query/service. Actual query-string decoding, transport registration and client preparation remain Servers #238.

Necessary Core files: `operations/http.py` (route plus this route's explicit named 16-KiB request-schema override and 1-MiB response; `_read_route` currently defaults to `EmptyRequest`/1,024), `operations/projections.py` (kind/policy/definition), `operations/parity.py` (one read binding). Existing `HttpSchemaRef` already supports both bounds; the request schema describes logical query arguments, not GET-body support. Combined focused scopes stay in Operations' existing tuple-valued RouteAuthorizationPolicy; coarse Core READ is not sufficient authorization and no Core scope/protocol extension is needed. No change expected in `operations/mcp.py`, command catalogue, Core policies or topology algebra. Existing exact catalogue tests must retain every prior entry while adding the new one, including descriptor roundtrip and security parity; no allowlist hole or weakened equality. Core's ordinary suite must therefore join Operations in a later explicitly released implementation gate.

### Actual external consumer and adoption boundary

Read-only trace used exact Servers roadmap `66bd9ce63df3ae459f7aea1eca83118a09799177`, also the source anchor in [Servers #238](https://github.com/OpenJ92/control-plane-kit-servers/issues/238). The separate local Servers checkout has unrelated changes and was not used as evidence or modified. Exact source blobs are retained under `/tmp/cpk-1899-servers-*-66bd9ce.py`.

- [composition.py:242](https://github.com/OpenJ92/control-plane-kit-servers/blob/66bd9ce63df3ae459f7aea1eca83118a09799177/products/cpk_server/src/control_plane_kit_servers_cpk_server/composition.py#L242) builds HTTP routes from installed Core and at 245 builds projection parity. [server.py:529–539](https://github.com/OpenJ92/control-plane-kit-servers/blob/66bd9ce63df3ae459f7aea1eca83118a09799177/products/cpk_server/src/control_plane_kit_servers_cpk_server/server.py#L529) forwards the raw query and installs catalogue-prefix handlers. [http_host.py:16–40](https://github.com/OpenJ92/control-plane-kit-servers/blob/66bd9ce63df3ae459f7aea1eca83118a09799177/products/cpk_server/src/control_plane_kit_servers_cpk_server/http_host.py#L16) installs those handlers, and [boundary.py:259](https://github.com/OpenJ92/control-plane-kit-servers/blob/66bd9ce63df3ae459f7aea1eca83118a09799177/products/cpk_server/src/control_plane_kit_servers_cpk_server/boundary.py#L259) matches every catalogue route. A new Core entry plus Operations mapping CAN automatically expose the authorized no-query HTTP read upon dependency adoption, without a Servers source edit.
- [boundary.py:303–342](https://github.com/OpenJ92/control-plane-kit-servers/blob/66bd9ce63df3ae459f7aea1eca83118a09799177/products/cpk_server/src/control_plane_kit_servers_cpk_server/boundary.py#L303) accepts only `limit` and `after` query keys. It rejects D's `expected` and `pending_draft`; the new descriptor's schema name does not extend this decoder. Full optional-query HTTP support is currently absent.
- [boundary.py:411–466](https://github.com/OpenJ92/control-plane-kit-servers/blob/66bd9ce63df3ae459f7aea1eca83118a09799177/products/cpk_server/src/control_plane_kit_servers_cpk_server/boundary.py#L411) dynamically resolves projection parity names for MCP `resources/read` and forwards mapping arguments to Operations. Read-only `tools/call` is rejected. Thus MCP argument support can become reachable automatically while HTTP optional arguments still refuse; there is no second D service or blanket transport parity claim.

This child does not run/deploy a Server or change its dependency coordinates, but catalogue metadata is not an exposure barrier. Servers #238 must coordinate dependency adoption, route-specific HTTP query encoding/duplicate/nested-object handling, MCP `resources/read` behavior, combined-scope enforcement and the owning composition tests before full external transport/pre-wire capability is accepted. Framework-neutral D mapping tests prove only the typed request boundary. This concrete partial-exposure risk is carried to North for the adoption decision; do not publish/adopt downstream packages as an incidental D action.

Existing dependency order already carries this boundary: #1899 is the final O1/#1882 child; Servers #238 explicitly depends on accepted O1 and unblocks #237. [Servers #237](https://github.com/OpenJ92/control-plane-kit-servers/issues/237) names #238 and the whole [foundation #1879](https://github.com/OpenJ92/control-plane-kit/issues/1879) as prerequisites. Foundation G2a includes authoring acceptance, while selected product composition G2b remains downstream. Add no #1899→#238 reverse prerequisite and no foundation→#237 cycle.

Proposed exact additive amendment for North to put on Servers #238 (not published by this plan):

> Adoption of O1's receiver-authoring-context declaration requires the accepted exact Core/Operations coordinates and composed transport validation. Existing Servers 66bd9ce automatically consumes Core HTTP and MCP parity catalogues: the authorized default HTTP read and MCP resources/read may become reachable on dependency upgrade, while the current HTTP query decoder rejects expected/pending_draft. Before selecting/deploying the new coordinates in a production cpk-server, implement route-specific bounded query decoding and prove expected/pending_draft, duplicate keys, conflicting workspace arguments, nested JSON/error behavior, identical permitted public context and both workspace/graph-read plus DELEGATION_KEY_READ across HTTP and MCP resources/read. Retain no-GET-body semantics and separately bound percent-encoded queries, serialized responses and MCP envelopes. Keep missing/malformed/over-budget material a whole refusal. This is part of #238's existing authoring/adoption gate, not a prerequisite back-edge from O1 and not implicit production exposure authority. #237 consumes the accepted compatible #238 result.

Prefer this single declared read over a method-only interim interface: a method-only contract would defer the explicit framework-neutral mapping required by #1899, leave Servers #238 to invent a second public naming/auth boundary, and cannot be inserted into the current exact route-policy map without the catalogue. Pure metadata does not claim actual Server transport is implemented.

Prefer the snapshot UoW to a large single-statement relational capture: existing provenance reads span multiple statements; a joined capture would duplicate selection/integrity decoding, introduce fanout/aggregation budget risks and obscure source ownership. A truly small single-statement alternative would be valid only if it captured every returned pin, live head, graph and origin/action fact together. Capturing pins and later enriching under READ COMMITTED is not equivalent. Retrying torn reads or acquiring mutation locks is unnecessary for the declared snapshot semantics.

## Small implementation plan after review/release

1. Freeze exact query/response/error/budget and snapshot-entry spellings; establish focused target laws below before implementation. One conceptual #1899 PR remains coherent: declaration → authorized snapshot read → exact public projection. If the bounded graph/provenance seam requires a lifecycle or generic transaction redesign, stop and split rather than expand this child.
2. Add the narrow UoW snapshot-read entry and focused transaction tests without changing normal UoW defaults. Add graph-owned bounded material/origin read support that reuses existing validation and accounts every needed byte/row in one context budget; no schema change.
3. Add the dedicated Operations public read service/value/closed descriptor with pure configuration projection and auth before snapshot. Keep ordinary InstanceReadService redacted reads unchanged. Add the CpkServerReadService branch before its ordinary UoW so the dedicated owner supplies exactly one snapshot transaction, not a nested mixed read. Reuse existing trusted context/policy decisions.
4. Add the three pure Core metadata entries and framework-neutral HTTP/MCP mapping tests. Update exact package/read-query inventories only for actual new modules/selectors; document fields/bounds and a small pending/retention example in existing read/lifecycle documentation.
5. After separate execution release, ordinary Operations Docker suite with exact architecture-testing `7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`, ordinary Core suite for the declared contract, and diff check. No host substitute, provider/live or Servers transport gate. Stop on apparatus/failure under the existing rule.

Expected Operations source ceiling: `read_services/receiver_authoring_context.py` (new public values/service and private projection, no Postgres imports), `read_services/__init__.py` / root exports as necessary; `postgres/unit_of_work.py`; graph-owned `postgres/graph_store.py` and `postgres/receiver_lifecycle_store.py` bounded seam; small draft reader addition only if required for bounded live-head fields; `cpk_server.py`. Relevant inventories: `POSTGRES_READ_CARDINALITY.toml`, exhaustive package/module inventory and read-package tests. No admission/execution/advancement, key-registry, schema, provider or dependency changes. If module conventions require a separate local protocol file, name it in the release rather than hiding a broad refactor.

## Minimal target law matrix (not written or run)

- **N8 exact success (S/N):** selected current/desired and exact live draft from legitimate existing owners; literal expected target/configuration bytes/digests and immutable origin references; assigned receiver-free graph versus absent desired distinction; null desired only at revision zero; unassigned/half-null current refuses without widening the existing expectation. No unrelated metadata/environment/artifact/private references. Existing redacted endpoints unchanged.
- **N8 auth and mapping (S):** graph-read-only, key-read-only, edit-only, wrong workspace/kind, absent principal and forged scopes refuse before UoW; authorized HTTP/MCP-shaped requests yield identical descriptors; direct public service enforces the same auth/snapshot. Closed unknown/duplicate/conflicting input keys refuse.
- **N8 origin/pending (S):** same live head continuation keeps original action despite later revision; other/historical/tombstoned/stale head, mismatched original binding/action or retired receiver source refuses completely. A read creates no graph, binding, action, session, event, key or ID.
- **N8 concurrency (N):** pause after first real snapshot read; real other owner commits desired/head change; returned context is wholly old, next read wholly new. Reverse writer-before-reader order returns wholly new. Include current/acceptance witness changes using the existing explicit assumed-completion fixture boundary, not invented accepted update. Later mutation with old five pins refuses. No two-transaction stitched view, no advisory lifecycle lock held by this read.
- **N8 bounded transport (S/N):** exact/cap+1 cases at each owning boundary plus one combined-budget/source-dedup law; malformed/duplicate JSON and selected artifact mismatch; observe actual cursor values to prove an oversized graph/action never arrives as a full cell. Add proportional quote/backslash/control escaping and non-ASCII UTF-8 cases for the explicitly named JSON-body serializer. An exact-cap positive is required only when reachable through the closed valid field grammar; do not invent padding/extra fields to force it. Use the smallest independent cases, not a full cross-product fixture matrix. No “complete” response with dropped receivers or redaction placeholders.
- **Snapshot UoW (N/I):** mode set before first read; attempted durable write refused by PostgreSQL; already-used factory transaction fails closed; success/failure close cleanly and ordinary UoW commit/rollback defaults remain. Do not add tests merely mirroring helper names.
- **Catalogue (S/N):** one closed Core route/projection/read-only parity entry with 1 MiB response; policy coverage exact; all previous routes/metadata and errors preserved. This is metadata/application mapping evidence, not HTTP/MCP server execution.

Focused target-red, if released, must be due to the absent public read/snapshot behavior, not fixture setup or broken imports. No synthetic red is claimed at this planning checkpoint.

## Teaching and downstream handoff

Use one bounded non-executing example in the final documentation: accepted A contains receiver X; read current A plus desired B and capture their distinct selected configuration and unchanged original introducing action; proposing C carries the captured five pins, and later admission revalidates. Label accepted A's completion premise explicitly; B/C are authored proposals, not proof of accepted managed updates (#1912). A pending same-draft revision uses the exact live head and original origin, whereas omission/tombstone never frees X. An interrupted command retry uses its original prepared artifact/receipt; it never calls this read to silently regenerate or select replacement keys. Servers #238 receives the exact query/response, auth, budgets, stale handling and public material boundary for transport/pre-wire authoring; O2/O3/Core #1886 receive the same factual source association. No health/signing/adoption capability is created here.

Security/data/history: this intentionally discloses exact selected PUBLIC verification configuration only to graph+key readers in scope. No private key/token/provider credential, ID minting, signing, selection or durable action is hidden in the read. Database snapshot isolation is not authorization or provider freshness. Complete snapshots may immediately become stale; budget refusal is a deliberate availability limit. SQL internal work remains outside byte/row transport guarantees. This child performs no Server run or dependency adoption; the traced existing adapter can auto-expose installed declarations, so coordinated #238 transport acceptance remains necessary. No cleanup/compensation/rollback of provider resources is involved.

Review requested: Meridian auth/data/test-integrity and exact field/budget plan; Kepler bounded snapshot/catalogue ownership review. North owns any subsequent target/source and execution release. This local artifact is not a publication or implementation authorization.
