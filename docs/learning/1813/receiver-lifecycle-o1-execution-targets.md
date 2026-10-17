# O1.C1 execution scope: target interface and test context

Status: planning and targets only under [#1902](https://github.com/OpenJ92/control-plane-kit/issues/1902).
Selected source is staged B merge `a1ce6fc97c6792881319916feaa66f8a38f47b71`.
This supplements the accepted [joint freeze](receiver-lifecycle-o1-joint-freeze.md)
and [scope/query contract](receiver-lifecycle-o1-scope-query.md); it does not
change their limits or release C application source. C2, C3 and D remain held.

## Existing evidence and source trace

B's exact source `15bfa3636f12a79a9019ca17d4787c544862597c`, included by the
selected merge, passed the ordinary Operations suite: 1883 tests locally and
in CI, followed by compile/import checks. See [terminal evidence](https://github.com/OpenJ92/control-plane-kit/pull/1905#issuecomment-5889778068)
and [independent PASS](https://github.com/OpenJ92/control-plane-kit/pull/1905#issuecomment-5889794128).
This is the unchanged predecessor baseline, not C1 validation. No C1 executable
validation has run at this planning checkpoint.

All source/test paths below are relative to `control-plane-kit-operations/`.
The selected package consumes the co-located Core; the ordinary harness requires
the clean architecture-testing sibling at `7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`.

| Inspected law | Observable behavior and negative cases | Classification |
| --- | --- | --- |
| E1: `test_execution_admission.py`, revocation and identical replay methods | Original request/action replay survives later revocation; fresh admission still requires permission; changed actor/fingerprint refuses. | Isomorphic replay; strengthened immutable coverage. |
| E2: same file, approval projection cycle | A to B to A does not restore the old generation or approval. | Isomorphic; no witness based on today's pointers. |
| E3: same file, concurrent admission and late action failure | One original identity under replay; action failure rolls back request. | Strengthened to include header and every scope row. |
| H1: `test_revision_history_advancement.py` | Success alone is not acceptance; exact acceptance survives pointer/claim changes; missing or duplicate sides are unavailable. | Isomorphic attribution, new-law C-N9 conflict kept distinct. |
| Schema: `test_current_schema_installation.py::test_current_reinstall_is_query_only_and_identity_stable` | Exact reentry performs no DDL/DML and preserves identity; drift refuses. | Strengthened with complete original scope rederivation. |
| Intent: `test_postgres_effect_attempt_intent_schema.py` | Attempt without intent violates FK; retained intent without attempt is possible corruption and refuses verification. | Strengthened independent enumeration; no attempt-driven absence proof. |
| Recovery: `test_postgres_effect_attempt_coordinator_first_replay.py` | Recovered terminal results retain their evidence without new effects. | Isomorphic; direct outcome absence is not attempt absence. |
| Socket: `test_runtime_interpreter_dispatcher.py` | Socket switch can succeed as an adapter record with zero Docker requests. | Isomorphic nonreceiver classification; not a rule for ingress. |

Current path: `admission.py:227` takes command/idempotency locks and returns exact
replay before fresh-state checks. At `:264` it acquires the workspace lifecycle
lock; `:334` resolves the stored plan's original projections (or the established
authored identity rule when those original fields are absent). At `:413` it
constructs the queued request, calls `execution.add_request`, appends the real
admission action, then commits the caller-owned UoW. `postgres/execution.py:71`
currently inserts only the old request fields. This is the smallest missing
connection: derive and persist coverage in that same semantic admission.

`postgres/activity_history.py:472` reads the original stored plan; the new
bounded read must not call that whole-payload path before size reservation.
`runtime_effects.py:235` selects BASE for stop/remove and DESIRED otherwise;
recorded compensation material selects its own side. Consume this existing
closed Core operation language, not a receiver-specific operation taxonomy.
`lifecycle.py:620` and `:700` show exact cancellation action/event association;
the classifier must also check complete journals and the frozen time law.

## Designed internal interface

The following names are internal to Operations, with no root export, StoreBundle
member, route, caller scope writer or authority token:

```python
ExecutionReceiverScope(runtime_id: str, node_id: str | None)
DerivedExecutionReceiverScopes(scopes: tuple[ExecutionReceiverScope, ...],
                               source_digest: str)

derive_execution_receiver_scopes(request_identity, plan_record,
                                base_projection_record,
                                desired_projection_record)

execution.receiver_scope_evidence(workspace_id, requested_scopes, guard)
classify_receiver_scope_evidence(evidence)
```

Derivation accepts existing typed original records, verifies their identity,
workspace, plan/session and material association, and returns canonical distinct
scopes and the exact frozen RFC8785 witness. Every forward and inverse activity
is accounted for. Positive empty coverage is an actual count-zero witness;
unsupported, nonexecutable or missing material refuses. No current compiler
reinterprets a historical plan. Relocation preserves both runtime coordinates.

`ReceiverScopeEvidence` is an internal closed result: complete evidence, bounded
unavailable reason, or capacity reason. A complete value contains requested
scopes and all overlapping original request evidence. Each request carries its
existing request/plan/projection records, stored count/digest, complete derived
scope tuple, and all run evidence. Run evidence carries existing run, attempt,
independently enumerated intent, direct outcome (when applicable), journal,
compensation program/step/binding, and candidate advancement/cancellation action
records. Reuse existing typed values and attribution helpers; do not introduce
parallel durable records, caller-asserted accepted/disposed flags, or a second
execution transition machine. The reader validates completeness and source
association before it constructs a complete result.

Classification returns a closed disposition plus bounded reason and exact
request/run correlations: `nonconflicting`, `conflict`, `unavailable`,
`capacity`, or `requires-fresh-gate-closure`. The last disposition is the C-N11
historical cancellation/no-dispatch proof with an explicit unresolved C3
obligation, **never clearance**. No `reactivation_closed` argument is accepted.
For mixed evidence, unavailable/capacity cannot become clearance; any retained
conflict or unresolved proof requirement prevents a nonconflicting result.
Exact accepted attribution accounts only for that original run's proven
coverage. This is an internal result. Only bounded disposition/correlations may
reach an owning command's existing redacted error/report mapping; the evidence
aggregate, intents and direct outcomes must not become a public payload or a
default repr/log dump.
Target observations use result `disposition`, `reason`, `request_ids` and
`run_ids`; identity tuples are canonical distinct original correlations, not
caller inputs or lists to ignore. Derivation failures use bounded
`ReceiverScopeUnavailable` or `ReceiverScopeCapacity` errors. Reader failures
remain closed evidence results so classification preserves their disposition.

C2 exhaustively matches all five dispositions: `status != conflict` is never
a success rule. Unavailable, capacity and unresolved closure each prevent C2
completion on their own. C3 alone can
establish fresh-gate closure and handle its own advancing run inside complete
success/association proof plus atomic acceptance. There is no ignored-request
or ignored-run argument at either the reader or classifier boundary.
C3 also owns the final C2 consumer integration: under L and current expected
pins it can combine exact no-dispatch evidence with the fresh-path closure
invariant proven by the integrated implementation/tests. This is not a runtime
Boolean, feature flag, registry check or user acknowledgement, and does not
mutate evidence into authority or add a second classifier.

## Persistence and fixture boundary

Keep `add_request(record)`'s existing signature. A direct call can persist only
positively derived nonaffecting coverage using complete original stored context.
Missing/unknown or nonempty affecting coverage refuses before insertion,
profile-independently. This is a store operation, not fabricated admission.
The legitimate admission owner captures its existing lifecycle guard and calls
a private persistence primitive after the full admission checks. That primitive
revalidates retained source on the same connection and writes request/header/
scope rows; admission appends its real action before UoW commit. A guard proves
active transaction/workspace serialization, not permission. Do not acquire L
late inside the bare store call or pass caller-computed scope rows.

Fixture audit found six direct calls in four test files: execution lease
recovery, failed-run compensation, revision history, and current advancement.
Fifteen further test/fixture files contain direct request SQL. Classify each
before translation: actual direct-writer behavior becomes a strengthened
refusal test; unrelated service setup uses real admission where its law needs
supported execution; intentionally recorded/corrupt history can use explicit
test-only SQL without claiming supported-writer credit. Valid empty fixtures
need genuine original material. No zero digest/default/empty stub is allowed.
Preserve original assertions, prior-run relationships, approvals and negative
cases. Do not create a fixture admission implementation.

Meridian's independent audit confirmed none of the six existing public calls
asserts that bare affecting insertion is legal. The new direct-writer tests own
that strengthened refusal. Concrete source-phase translations (all old fixtures
remain unchanged for causal red, avoiding missing-column/setup failures):

| Existing fixture / method | Preserved purpose and translation |
| --- | --- |
| `execution_lease_recovery_fixture.py::seed_truth` | Real admission with actual runtime-a material; retain active/expired/failed leases, fences and explicit approval negatives. |
| `failed_run_compensation_fixture.py::seed_truth` | Real admission with runtime-a/node-a material; preserve failed program/step/history laws; old seeded events alone are not C1 complete evidence. |
| `revision_history_fixture.py::add_attempt` | Explicit recorded relationships; retain CANCELLED request, varied run statuses and absent-run cases using test-only rows plus genuine identity-specific derivation. |
| `test_current_graph_advancement.py::seed_truth`, `_seed_execution`, reassigned-request negative | Valid api material and real admission for setup; preserve distinct projection/generation laws. Avoid nested admission after workspace locks; adversarial reassignment remains explicit recorded SQL with its own witness. |
| `test_postgres_schema.py::_seed_minimal_execution_truth` | Valid codec plan replaces incidental `{}` before exact targeted schema/row corruption. |
| `test_large_read_collection_pages.py::_insert_run` | Recorded paging population; preserve IDs, times, counts and seek assertions. |
| `test_execution_coordinator.py::seed_execution_request` | Real admission for legitimate execution; isolate invalid/unsupported source negatives; preserve adapter assertions and exact history deltas. |
| `test_query_path_indexes.py::_seed_runs` | Keep bulk 10,000-row EXPLAIN population as recorded data; genuine shared empty source and per-request witness, no new constraint-disabling mechanism. |
| `test_read_services.py::seed_activity` and broken retry fixture | Genuine empty coverage from existing codec plan; preserve redaction and exact malformed-chain negative. |
| `test_native_temporal_ordering.py::_seed_runs` | Genuine empty witnesses; preserve original native times and tie ordering. |
| `test_run_lifecycle.py` primary/second request setup | Real admission with executable nonaffecting plan for primary lifecycle path; secondary collision/foreign history remains recorded, not fabricated lawful cancellation. |
| `large_read_history_fixture.py::_seed_runs`, `_seed_events` | Keep bulk synthetic history and real empty codec source; each request has its own witness. |
| `test_postgres_execution_lease_recovery_codec.py::seed_run` | Replace incidental `{}` source; preserve recorded codec timestamps, fences and targeted invalid values. |
| `activity_run_retry_interpreter_fixture.py::seed_foreign_run` | Recorded foreign clone retains attribution/locking law; both new request and plan identity require rederivation. |
| `test_postgres_execution_lease_recovery_scoped_run.py::seed_foreign_run` | Same clone rule; preserve independent NOWAIT proof without added semantic locks. |
| `test_postgres_ordinal_read_pages.py::_seed_parent_truth` | Genuine empty source; preserve exact ordinals and page data without adding admission actions. |
| `test_run_identity_schema.py::_seed_direct_rows` | Valid codec baseline replaces incidental `{}`; preserve boundary-width IDs and exact grammar/constraint negatives. |
| `test_cpk_server_adapters.py::seed_run_event` | Genuine empty witness; preserve intentionally small read/event data and wire/redaction assertions. |
| `test_postgres_effect_attempt_intent_schema.py::test_reduced_commitment_key_accepts_simultaneous_widths` | Preserve 512-character request, 200-character run/activity and 512 maximum-Unicode event widths; derive cloned identity witness without shortening the positive. |

Recorded fixtures may use the production pure derivation plus explicit test-only
row persistence once source exists. That helper supplies no admission policy or
successful action receipt. Deliberate corruption must roll back or be restored
before a later `install_schema`, which precedes cleanup in several fixtures.

## Target groups and expected causal red

Targets exercise the existing admission/store/SQL owner, with pure derivation
or classification assertions where useful. Missing new APIs are asserted inside
test methods, not imported at collection time. First missing-boundary failure
earns only that causal credit, not every deeper assertion in the same method.

| Group | Required observable cases |
| --- | --- |
| Derivation | Legacy/no bindings; node and runtime scopes; canonical distinct order; relocation and BASE inverse; positively nonaffecting observation/socket work; ingress distinguished; unknown/ReviewChange/DestroyDataResource refuses; immutable original source/digest. |
| Admission | Atomic request/header/rows/real action; replay retains exact witness; public affecting insert refuses; genuine empty insert; foreign/missing source; rollback at row/action boundary; existing concurrency and approval laws preserved. |
| Retrieval | Both overlap directions, removed historical node, disjoint node, older run behind newer acceptance, all original runs, independent orphan intent, recovered attempt without direct outcome, original witness drift and cross-workspace refusal. |
| Classification | Queued/active conflict; incomplete/uncertain never clears; exact acceptance accounts only its run; C-N9 known success and C-N10 direct/recovered failure or inverse remain conflicts; C-N11 exact cancellation yields only unresolved closure requirement; duplicate/missing/foreign/time-mismatched cancel witnesses refuse. |
| Bounds | Near-limit and one-over scopes/activities, requests, raw candidate rows, runs, shared attempts/intents/compensation rows, events, payloads, lookup-key bytes, aggregate transported values; duplicate prefixes and already-known IDs; reduced LIMIT requires exhaustion proof; query-only reentry walks all requests with per-request budget. |
| Catalog/query | Exact frozen table/header/constraints/indexes, 8 KiB page prerequisite, actual prefix/index plans without pre-limit unbounded sort; read-only exact reentry and drift refusal. |

No C3 fresh-dispatch/public claim/start closure, end-to-end C-N11 clearance,
own-run exclusion or contention activation is credited by these C1 tests.
The old suite must remain collected; no skip/xfail or weakened assertion.

The complete candidate target checkpoint contains 67 test methods in seven
files, with one shared real-service fixture. This is an authored inventory,
not a predicted suite result or executable evidence. It awaits independent
target review and ordinary causal red. Tests intentionally assert the absent
module or catalog inside the method, so intended red is an assertion rather
than a collection/import failure. A first-boundary failure proves only that
boundary; later fixture feasibility and semantic assertions still need green.

Exact inherited admission anchors are
`test_revocation_after_approval_blocks_new_execution_but_preserves_receipt`,
`test_identical_replay_returns_original_and_changed_intent_conflicts`,
`test_approval_cannot_be_reused_after_projection_cycles_back`,
`test_concurrent_identical_admission_converges`,
`test_admission_replay_survives_close_but_new_admission_is_fenced`, and
`test_late_action_failure_rolls_back_execution_request` in
`test_execution_admission.py`. The exact inherited socket anchor is
`test_runtime_interpreter_dispatcher.py::test_socket_connection_operation_is_recorded_without_runtime_effect`.
Inherited transport laws remain in
`test_postgres_effect_attempt_intent_store.py::test_transport_bounds_gate_before_python_decode_on_get_and_current`
and `test_postgres_effect_outcome_store.py::test_exact_8192_byte_preimage_roundtrips_and_workspace_is_derived`
with their existing negative/schema tests. C1's real-cursor targets additionally
observe the combined reader's transported values, oversized original material,
64 KiB cancellation action boundary and growth after a length probe.

Capacity interpretation is deliberately conservative. The 4096 raw-row positive
uses four exhausted prefixes, with 32 distinct requests and 128 scopes per
request. A single full transport-reduced prefix must instead refuse, even at
the nominal raw-row cap. The 16 MiB negative uses twenty distinct individually
legal near-cap original descriptors; immutable caching cannot erase their
minimum total. The monitor delegates actual SQL/results unchanged, counts
projected scalar text/bytea (NULL zero), and never supplies rows or alters limits.
The one race target injects a rolled-back database mutation after the actual
length probe; it never changes the probe result delivered to the reader.

Row-budget targets use real execution/retry/compensation owners for complete
attempt/intent/outcome evidence. The independent-intent one-over negative is a
rolled-back deletion of a freshly started attempt before any outcome exists,
leaving its real intent. Compensation's at-limit case includes steps and
bindings; its next lawful atomic start adds three inseparable rows and must
refuse. Recorded pause/resume event populations are read fixtures, not provider
execution claims. Runtime cost and the feasibility of the large fixture values
remain unproved until the owning suite reaches those assertions.

## Review, validation and handoff

The full review of `3bf99c4` returned HOLD. This target-only correction retains
the real persisted intent when constructing an inverse, uses its actual start
event ID for the outcome, and supplies canonical UTC clocks through the existing
PostgreSQL temporal codec. It adds missing-direct-outcome and post-dispatch
cancellation negatives, and a failed-run/later-accepted-retry history within
one request. Persisted forged digests, an entirely missing scope set, and
corruption beyond the first 64 identities must all refuse current verification
without repair; only discoverable corruption receives online assertions.

The actual EXPLAIN proof now requires each candidate prefix's index scan to
reach a Limit before combination, deduplication or sorting. A separate two-node
case fills the 64-request budget and repeats those same identities on a later
distinct prefix before adding a 65th identity. A point query also excludes
over-cap unrelated node-only history on the same runtime. These are strengthened
and new-law target corrections against the frozen query/classification contract,
not application implementation or executable evidence. Independent delta review
must pass before publication of this complete candidate and ordinary causal red.

Before ordinary causal red, publish the reviewed target checkpoint as unvalidated
on a draft child PR into `codex/1882-receiver-lifecycle-integration`. Meridian
reviews targets. Vale alone runs `./control-plane-kit-operations/test.sh` with
the exact clean sibling and fresh suite-owned resource names. No host Python,
custom database or alternate harness. Apparatus/collection failure stops with
no behavioral credit. Meridian reviews causal red before North releases source.

Security: no new credential, network or provider effect. Tenant-safe bounded
refusal and original-source validation are new test obligations. Data changes
are prospective until source release: one relation, eight columns, nine
constraints, five indexes beyond B; verify actual catalog rather than copying
totals. No migration, repair, backfill, deletion or changed install policy.
Operational history stays original request/action/run/event truth. Row/value
caps can refuse otherwise settled history; there is no pruning/recovery escape.
The supported-writer completeness limit against privileged raw SQL remains
explicit. C1 acceptance is staged accounting, not B/C joint completion or live
adoption.

## Released source candidate after causal red

North released bounded C1 source after Meridian accepted the exact target head
`823f62c17a6ff40567e2481c3a9137ce5022750f`. Local and hosted ordinary suites
both collected 1,950 tests and produced exactly 58 missing-module and nine
missing-relation failures, with no errors. This is first-boundary red evidence;
no deeper history, budget, schema or fixture law earned green credit.
Release: [#1902 comment](https://github.com/OpenJ92/control-plane-kit/issues/1902#issuecomment-5891920884).

The source candidate adds the two reviewed internal modules. Pure derivation
unions actual forward and compensation targets from original material and hashes
the frozen witness. Existing admission derives and stores the header/rows under
its existing lifecycle lock and transaction before the real admission action.
The old public writer accepts only genuinely empty derived coverage. Replay and
request DTOs retain their original shape.

The SQL reader reserves finite transport before every fetch, caches immutable
original material within a read, verifies complete scope sets and independently
retrieves intents. Existing attempt, intent, outcome and compensation codecs
remain the semantic owners; small decode-with-supplied-evidence functions let
the reader reuse them without unbounded hidden fetches or fake connections.
Historical acceptance reuses the existing receipt law and the advancement
owner's complete-success law on the journal preceding that receipt. It never
re-evaluates current lease eligibility or treats compensation as disposal.

Kepler's source findings produced three corrections: concrete candidate prefixes
are capped at 65 rows; journal-only socket/ingress starts are associated with the
original plan and checked through the existing saga projection; compensation
steps must match the original plan's inverse operation and material source.
Two source-phase regression targets add mixed real-coordinator history and a
damaged success journal beneath an exact acceptance receipt. Three further
review targets cover wrong retry predecessors/gaps/chronology, a damaged original
opening beneath cancellation, and valid no-dispatch pause/resume cancellation.
There are now 73 C1 target methods; the six source-phase additions have no
independent red/green claim yet.

All 19 audited request-producing fixture files are translated. Recorded
read/schema/index histories derive identity-specific witnesses using production
derivation, preserve times, widths and populations (including 10,000 query-plan
rows), and use explicit test-only SQL. Service fixtures use actual admission
with real original targets before recorded lease/failure overlays. Empty
coordinator-history cases remain explicitly recorded because admission rejects
empty plans. Managed teardown retains its own actual approval/admission path;
source preparation does not admit early. Advancement preparation commits before
admission to preserve lock order. Assertions account for the exact admission
action baseline while retaining operation deltas and failure types.

This candidate is unvalidated. Exact PostgreSQL normalization of the prospective
44 relations, 557 columns, 449 constraints and 147 indexes, shared-budget positive
feasibility, and preservation of all predecessor fixture laws remain owning-suite
obligations after independent source review. Static diff checks are not execution
evidence. No installation migration, backfill, repair or default witness exists.

Security/data/history: fixed candidate-free refusals, bounded source transport,
tenant and original-identity checks; no root exports, route, StoreBundle service,
provider effect or new authority. Admission writes header/scopes/action atomically
and rolls back together on late failure. Current reentry verifies every request
in keyset batches without repairing history. The reader returns only an internal
classification; `requires-fresh-gate-closure` remains a C3 obligation. C2/C3/D,
aggregate acceptance, publication/adoption and live execution remain separate.

### Independent source review correction

Meridian held source candidate `56c74b14` for two incomplete-history cases and
three fixture/inventory defects. Request-local retry linkage and native-instant
chronology now share a pure association helper in the existing revision-history
owner, used by both its prior SQL projection and C1. Cancellation envelope checks
live in the existing lifecycle owner and share its unchanged ordinary transition
facts, including its narrower CLAIMED/PAUSED cancellation rule. The reader uses
these only after bounded retrieval; it does not replay current claim or authority.

The three new fetch-all sites are recorded in the exhaustive cardinality
inventory (71 total; five repeated selectors); discovery remains strict.
Coordinator source setup now selects workspace pins/generation in the intended
seed, and managed cases admit only after their source/authority preparation.
The 10,000-row query fixture supplies real non-null original projection pins.
These are review corrections, not green evidence or broader release authority.

Delta review also caught the distinct cursor timestamp representation and a
valid active-renewal/cancel history. The SQL revision projection now normalizes
its six-digit cursor instants through existing temporal codecs solely for the
shared association helper; cursor output is unchanged. The lease-recovery owner
now exposes its existing bounded historical pair projection separately from
the current-fence comparison. C1 uses only that projection before checking the
cancellation envelope. A real renewal/cancel target and corrupted-consequence
negative preserve both the lawful path and refusal without fabricated authority.

### First source owning gate and fixture correction

At reviewed `c7833aba`, the unchanged ordinary local gate ran 1,956 tests in
6,031.201 seconds and exited 1 with 32 failures and 11 errors. Setup/collection
completed; post-suite compile/import did not run. The harness removed its exact
container and network. [Terminal evidence and cleanup](https://github.com/OpenJ92/control-plane-kit/pull/1906#issuecomment-5894861004).
Hosted Operations stopped at its configured 30-minute limit without terminal
behavioral results; Core and Current Backend passed. No hosted retry or timeout
change was made. Neither partial gate earns Operations green or adoption credit.

The terminal failure ledger is:

| Root cause | Terminal observations | Correction and preserved law |
| --- | --- | --- |
| Schema/atlas companions | 9 failures | Keep exact counts, hash, all table fields, FK ledger and graph assertions; update companions to the already checked current catalog. |
| Real admission action omitted from old snapshots | 10 failures | Preserve the complete initial action prefix and assert exactly one retry/recovery action or exactly two renewal actions in addition. Concurrency winners, loser denials, event identities and replay remain unchanged. |
| Default scope fixture included runtime work | 11 failures, 2 SQL boundary errors | Store a separate explicit node-only default plan before real admission. Preserve exact node footprint/digest, runtime overlap, forged-count and 1024/1025-byte assertions. Keep the inherited structural plan as distinct original truth. |
| Repeated active requests reused one plan | 6 errors | Give every real queued candidate its own immutable approved plan. Preserve candidate/raw-row populations, exact prefixes and overflow results; do not disable the active-plan unique constraint. |
| Recorded query workspace missing | 1 error | Create the actual workspace before authored material; retain the 10,000-row indexed history population. |
| Invalid socket/node fixture examples | 1 failure, 1 error | Use the actual backend-switch scenario and valid product role IDs; preserve socket positive-empty, exact ordering and duplicate-node laws. |
| Health source changed after request witness | 1 error | Explicit recorded-health fixture prepares final plan/profile/material/options before deriving its first request witness. Default recovery fixtures still use real admission; no production re-witness, repair or current-verifier weakening. |
| Tiny index-planner sample | 1 failure | Populate distinct real-admitted plans with many same-runtime nodes and ANALYZE. Retain all three indexes, actual SQL, prefix conditions and LIMIT-before-sort assertions; report missing indexes and candidate EXPLAIN on failure. Selected-plan success remains unverified. |

No production source defect or new semantic decision has been established by
these traces. This correction changes tests/fixtures and documentation only;
all resulting behavior still needs exact-head owning evidence. Meridian's
independent terminal focus confirmed the stale health witness and the absence
of an established production index defect.

The health family is explicitly recorded history, unlike its recovery-service
base fixture. Its BASE-side and deliberately false relation-digest variants
must reach the existing health-owner assertions, without pretending those
histories passed execution admission. Construction preserves original IDs,
times and actual intent/preparation owners. Final originals precede request
coverage; current reentry remains read-only and refuses genuine corruption.

The five shared row-budget methods completed without terminal failures, but
large single-run coordinator histories dominated runtime. Reviewed fixture
refinement uses 32 real runs of 32 effects for 1,024 attempts plus 1,024
independent intents. A real overflow start, with only its attempt deletion
rolled back, still proves literal 2,049-row refusal and restoration at 2,050.
Compensation uses 22 actual programs: 668 forward attempts/intents and 646
steps, then 22 first inverse starts yield 690/690/646/22 rows, exactly 2,048.
Folding the last program's first inverse and starting its next ordered step
produces 691/691/646/23, the first lawful +3 overflow at 2,051. Persisted category
counts, real semantic owners and all ceilings remain explicit. This strengthens
shared accumulation across programs; it does not retain a claim of one huge
681-step program stress coverage. Performance improvement is unmeasured until
the corrected ordinary gate runs.

Security/data/history: no production schema, authority, provider, credential,
network or cleanup behavior changed. No limits, negative assertions or owner
checks were removed. No fake outcomes, new executor, alternate harness or
current-schema repair was introduced. Corrected source review precedes the
next owning run; staged merge and C2/C3/D remain separate North dispositions.

### Candidate query plan diagnosis and projection correction

The corrected fixture gate at `2b4f0021` completed 1,956 tests with one remaining
failure locally and in CI: a candidate index scan reached Sort before Limit.
Diagnostic-only `878f2e99` preserved all assertions, data, planner settings and
production SQL, adding the actual query and plan to the failing assertion.
Its ordinary local gate completed 1,956 tests in 3,863.535 seconds, exit 1,
one failure and no errors; the exact runner, PostgreSQL container and network
were absent afterward. Hosted evidence was 1,956 tests in 1,461.163 seconds,
also one failure. Neither gate reached compile/import or earns green.

Both plans identify the exact-node prefix: the correct node lookup index fed
a Bitmap Heap Scan, then Sort on request ID before Result/Limit. The candidate
query unnecessarily fetched ordinal and kind outside the lookup keys. The
consumer only needs request/workspace identities and its validity scalar.
The narrow correction projects those guarded identities, using existing
indexed request/workspace/runtime/node fields for node-branch byte checks.
The runtime branch uses its predicate-known zero node length, avoiding a heap
node reference without adding a filter that could hide corrupt candidates.
Full original scope verification still checks every retained ordinal, kind,
runtime and node before any complete evidence/classification is returned.

Raw row counting, 4,096-byte reservation, actual returned-value charging,
sentinels, request caps, predicates, order and LIMIT remain unchanged. No
schema/index, planner setting, fixture population, timeout or assertion change
is included. This permits an index-covered path but does not establish the
planner's choice: the unchanged actual-plan assertions must prove the physical
cap-before-sort law in the owning gate. The correction is unvalidated pending
independent delta review and ordinary validation. No new authority, durable
mutation, provider, secret/logging or network surface is introduced.

### Accepted unordered prefix correction

The smaller projection at `1755bed5` did not fix the chosen plan: hosted
1,956 tests in 1,741.096 seconds and local 1,956 tests in 4,105.763 seconds
both retained one failure and no errors. Local exit was 1; full traceback
matched the exact-node bitmap/heap/Sort/Result/Limit path. Compile/import did
not run. The exact local runner, PostgreSQL container and network were absent
afterward. No green credit follows from this failed candidate.

North accepted Kepler/Meridian's [query-contract clarification](https://github.com/OpenJ92/control-plane-kit/pull/1906#issuecomment-5899826124)
before source changed. Candidate prefixes now omit SQL ordering; the existing
final sorted request IDs remain canonical. Any filled effective LIMIT refuses
capacity before row interpretation, including an exactly-full population.
Otherwise the entire visible prefix was retrieved, so ordering cannot change
completeness. Fixed prefix traversal, guards, raw-row charging, transport
reservations/actual charges, all ceilings and full original verification remain.
Multiple simultaneous faults can choose different closed refusal reasons under
unordered traversal; no precedence between such faults is promised.

The actual-plan target now proves all three prefix kinds, each from the captured
production query. It checks exact predicate/LIMIT tails, actual synthetic prefix
parameters and positive integer limits, two branches per requested scope,
compatible indexed conditions for every query, and the unchanged forbidden
relational operations before the nearest Limit. Node equality follows the query
branch rather than the chosen overlapping index's name. Exact three-index
catalog tests, fixture populations, planner settings and all behavioral capacity
and completeness targets remain unchanged. This is an explicit contract/test
amendment, not a claim that the previously failing assertion was green.

The guarantee bounds rows returned beyond each prefix, transport and subsequent
processing. Bitmap construction, index/heap scans and database memory/CPU/pages
or latency are not bounded by SQL LIMIT; large matching histories can still cost
database work while the lifecycle lock is held. No pruning, mutation authority,
new schema/index, planner policy, provider action or security relaxation is
introduced. The amended source/doc/test delta requires independent review and
ordinary owning validation before any completion or merge claim.
