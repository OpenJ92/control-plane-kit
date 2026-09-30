# C2 graph admission: source delta and target laws

Planning/targets only under [#1903](https://github.com/OpenJ92/control-plane-kit/issues/1903).
Base: accepted C1 merge `89dd522903baabad6b311ae02c1340a074d5d402`.
Branch: `codex/1903-receiver-admission-targets`, targeting
`codex/1882-receiver-lifecycle-integration`. Application source is not released.
The first ordinary target-only run at `768dd6b9` completed with 66 failures and
three test errors; its classification and target corrections are recorded below.
No C2 source or green admission evidence is claimed.

## Reused evidence and changed source

The [parent trace and G/D/S law cards](receiver-lifecycle-o1-plan.md),
[joint contract](receiver-lifecycle-o1-joint-freeze.md),
[entrypoint matrix](receiver-lifecycle-o1-admission-boundaries.md), and
[accepted A lock ledger](receiver-lifecycle-o1-lock-ledger.md) remain governing
context. Frozen provenance is `847a7053def484e516e7214e9e563ed9f376b491`.
The current [scope query contract](receiver-lifecycle-o1-scope-query.md) includes
the explicitly accepted unordered-query amendment. Do not restore obsolete
pre-cap ordering or require the optimizer to choose all three index names.
Returned rows/transport are bounded; PostgreSQL internal scan work is not.

B storage and C1 evidence are merged and selected in this worktree. C1's
[final independent PASS](https://github.com/OpenJ92/control-plane-kit/pull/1906#issuecomment-5901395621)
and [local owning green](https://github.com/OpenJ92/control-plane-kit/pull/1906#issuecomment-5901380551)
establish 1,956 passing tests at reviewed source `5a69d03a`, compilation and
clean import. They do not establish C2 admission or external adoption.

Affected paths at the C2 base (paths below are relative to Operations `src/`):

| Existing owner | Existing behavior | C2 connection |
| --- | --- | --- |
| `control_plane_kit_operations/planning.py:DesiredGraphCommandService.execute` | Original replay, K then L/session, action-free authoring, action, commit. | Compare original five pins; complete graph/projection/action/introduction/binding/desired mutation together. |
| `graph_authoring.py:set_desired_graph_in_unit_of_work` | Guard plus desired-only CAS; saves graph/identity/pointer without action. | Preserve legacy semantics; refuse receiver-affecting standalone use. Command owner cannot obtain reusable permission from this helper. |
| `desired_topology_drafts.py:DesiredTopologyDraftCommandService.execute` | Auth before allocation; closed original replay; graph/draft/revision/action. | Actual identity projection and B facts join the transaction; same live-head provenance and five pins. |
| `desired_topology_drafts.py:_execute_reference_command` | L/session/workspace/draft; selection identity/pointer/action, or referenced-head-safe tombstone. | Selection checks receiver lineage; tombstone never frees or retires a receiver. |
| `desired_realized_projections.py:publish_desired_realized_projection_in_unit_of_work` | A's prepared prefix, fresh session/desired checks, projection/CAS/action. | Prepared prefix grants no semantic authority; compare current too, derive provenance and all bindings before completing publication. |
| `postgres/graph_store.py` and `postgres/desired_topology_draft_store.py` | Public single-record writes, including implicit identity save. | Full supported semantic case or receiver-affecting refusal; classify old and new material under L before workspace. |
| `postgres/receiver_lifecycle_store.py:_ReceiverStorage.reserve` | All bindings reserve one new origin; different original origin conflicts. | Private reserve-new seam for a composite-derived mixed continuation/introduction partition; complete binding persistence remains mandatory. |
| `deployment_program_interpreter.py:DeploymentProgram.prepare` | Parent already pins both lineages; child SetDesiredGraph omits current. | Forward original pins; choose old child format only from exact owned receipt. |
| `gateway_key_rotation_projection.py:build_gateway_key_rotation_projection_publication` | Existing receipt or prepared K/L prefix; settled-lineage proof; generic publication value. | Forward original outer pins, preserving receipt-directed old format and existing supported profiles. |
| `saved_deployment_preparation.py` and `planning.py:ActivityPlanningCommandService` | Pinned saved/reference publication and planning under A's locks. | Verify lawful retained membership before fresh intent, preserve original replay. |
| `cpk_server.py:CpkServerPlanningService.handle` | Trusted context maps desired/create/revise/select arguments to Operations. | Decode and forward exact optional five-key product; explicit null and malformed member refuse. |

No new entrypoint, service, Core language, root export, external repository,
provider, schema migration, cleanup, signing or transport capability is proposed.
North explicitly included the private B-store seam in the ceiling in
[this clarification](https://github.com/OpenJ92/control-plane-kit/issues/1903#issuecomment-5901526226).

## Governing laws and target interface

Tests exercise the existing semantic commands/stores. The only new public value
is `ReceiverLifecycleExpectation` in `receiver_lifecycle.py`, with exact fields
`current_graph_id`, `current_realized_projection_id`, `desired_graph_id`,
`desired_realized_projection_id`, `desired_graph_revision`. Commands receive
keyword-only optional `receiver_lifecycle`. It is expected truth, not authority.

| Card/classification | Original observable law retained | Strengthening or new observable result |
| --- | --- | --- |
| G1 strengthened | Stale desired refuses with no saved graph. | Matching desired but stale current also leaves graph/projection/action/B/head unchanged. |
| G2 isomorphic | Stored graph material survives reload unchanged. | Exact actual identity projection and binding digest refer to that material. |
| D1 isomorphic/strengthened | Replay precedes allocation and mutable head/session/product checks. | Closed old and old-plus-member action formats; original pins survive later truth; changed pins conflict. |
| D2/D3 strengthened | One winner from one head; identical requests converge. | No duplicate/reminted introduction and no losing B writes. |
| D4 strengthened | Late real action failure rolls back catalogue writes. | Actual identity, introduction and binding writes also roll back; losing CAS/deferred failure/caller rollback remain atomic. |
| D5 isomorphic | Missing trusted edit authority or foreign session refuses before allocation. | Receiver identity and expectation never grant permissions. |
| S1/S2 strengthened | Selection/tombstone/planning serialize in both orders. | Direct old/new pointer classification takes L before workspace; implicit projection path is included. |
| F0 N1 new-law | No prior receiver admission behavior. | Original introduction survives accepted or exact live-pending continuation across permitted image/key/declaration changes. |
| F0 N2/N3 new-law | No prior provenance admission behavior. | Foreign/retired/duplicate/relabelled/scope-changed identity and arbitrary historical-copy origin refuse; same still-live head is positive. |
| F0 N4/N6 new-law | Draft history and global B uniqueness already exist. | Omission/tombstone cannot release IDs; competing workspaces have one global-ID winner and bounded nondisclosing loser. |
| Format new-law | Original intent bytes and closed draft actions are established. | Absent member leaves bytes unchanged; present member changes fingerprint and is retained exactly; null/unknown/partial product and duplicate disagreement refuse. |
| Composition strengthened | Physical child commits replay without duplicates. | Interrupted old parent/completed old child and completed old parent both recover original evidence; absent receipt never chooses legacy fallback. |
| C1 consumption new-law | Merged single classifier owns all history semantics. | Admission consumes all dispositions exhaustively; `requires-fresh-gate-closure` refuses until C3. No second history predicate. |

Representative command shapes:

```python
# Old intent: the member is absent from the fingerprint and action payload.
SetDesiredGraph(...)

# New intent: original caller pins, not pins filled from a workspace read.
SetDesiredGraph(..., receiver_lifecycle=ReceiverLifecycleExpectation(
    current_graph_id="current", current_realized_projection_id="current-identity",
    desired_graph_id="desired", desired_realized_projection_id="desired-identity",
    desired_graph_revision=3,
))
```

Result DTOs remain unchanged. Actions carrying the product keep exactly the old
result keys plus `receiver_lifecycle`, with the full payload bounded to 64 KiB
before any write. A parent may select its old child encoding only from the
matching owned receipt and original fingerprint/material. A fresh receiver call
cannot choose old format or fall back after successor refusal.

The transaction order is graph, actual identity projection, applicable draft/
revision, real original action, sorted new introduction rows, complete binding
rows, and applicable desired/head CAS. Deferred constraints and outer rollback
remain caller-owned. Internal primitive visibility is not proof of authority.
The final write rechecks material/pins/head after any intervening same-UoW change.

## Test apparatus and limits

The initial target checkpoint contains the following owning tests. Existing
predecessor tests remain unchanged; this is not a translation to a replacement
store or a rewrite of the legacy suite.

| Target file (`control-plane-kit-operations/tests/`) | Behavioral evidence sought |
| --- | --- |
| `test_receiver_lifecycle_expectations.py` | Exact immutable product, reference bounds, paired desired/generation, all five pins. |
| `test_receiver_graph_admission.py` | Atomic introduction, pending mixed continuation, scope/provenance refusal, current CAS, original replay, global/head/identical races, authorization and rollback. |
| `test_receiver_admission_boundaries.py` | Single-record/action-free refusal, implicit pointer paths, both real opposing schedules, retained projection replay and empty/populated bootstrap. |
| `test_receiver_publication_admission.py` | Genuinely fresh changed-material projection, exact returned projection bindings/digests, caller rollback, prepared-prefix and late continuation-source revalidation. |
| `test_receiver_admission_formats.py` | Wire null/closed product, exact forwarding, frozen absent fingerprint, closed old/new replay, complete 64-KiB action bound before writes. |
| `test_receiver_admission_composition.py` | Interrupted and completed old-child receipt recovery, mismatch refusal, original current pins before new graph write. |
| `test_receiver_gateway_publication_formats.py` | Gateway forwards original pins; its exact old publication receipt recovers after supersession without selecting legacy on mismatch. |
| `test_receiver_admission_execution_evidence.py` | Actual C1 history produces all five dispositions; C2 refuses the four non-clearance outcomes; disjoint and genuine legacy accepted-run positives. |
| `test_receiver_accepted_continuation.py` | Recorded accepted X plus new Y, changed declaration/key identity, old-current-only receiver detection, wrong original/acceptance evidence, retired/relabelled/moved refusal. |
| `test_receiver_reference_admission.py` | Saved-source session/reference and direct no-op planning positives, corrupted non-original receiver membership before any fresh writes, exact replay after later truth. |

`receiver_admission_fixture.py` composes existing real-store fixtures with one
explicit setup chain (older fixtures have both bare and `tests.*` import
identities). `receiver_recorded_acceptance_fixture.py` is **recorded accepted
current history** over an empty legacy plan. Current real execution admission
rejects empty plans: this fixture deliberately uses the existing recorded-request
helper, not a successful admission claim. It validates the typed receipt with
`historical_advancement` and the empty pre-advancement journal with the existing
complete-success owner, then seeds the C3-only current/B witness in the same
test transaction. Those checks do not establish deployment. The separate C1
consumer test uses real affecting-run services and actual legacy advancement.

Static review caught and corrected fixture import-chain setup and microsecond
timestamp normalization before execution. No static check or API-presence
assertion is presented as green admission evidence. Missing target value checks
occur inside tests so the owning suite can collect normally on the red base;
direct-writer and explicit-null targets exercise existing entrypoints directly.

The initial checkpoint was unexecuted at target review. Review must
assess proportional coverage against the whole #1903 contract, including shared
forwarders/retirement composition, action formats and caller-UoW semantics; the
table does not itself discharge any untested acceptance obligation.

### Initial review correction

Meridian's [HOLD at `223a216c`](https://github.com/OpenJ92/control-plane-kit/issues/1903#issuecomment-5901836881)
identified four concrete defects before publication/execution. The target-only
correction uses a nonzero microsecond fixture instant admitted by both timestamp
languages; publishes and checks a distinct changed-material realized projection;
adds receiver-specific saved-reference/direct-planning laws; and deletes a
non-original selected continuation binding after the real publication action
while pins remain unchanged. That late negative requires semantic refusal;
the original-introduction FK cannot supply it. Fresh projection rollback also
checks new bindings inside the caller UoW before proving they vanish on rollback.

Planning positives use the existing supported no-op profile, without expanding
runtime/configuration capabilities. Saved preparation is tested at its actual
session/source owner before later planning. The source/reference negatives
preserve original introduction membership while corrupting a later continuation,
so missing source admission cannot pass accidentally through FK rejection.
Meridian passed these corrections at `768dd6b9` before publication and execution.

### First ordinary run and bounded target repairs

The ordinary Operations suite at `768dd6b9` ran 2,027 tests in 3,817.853 seconds
and exited 1: 66 failures, three errors. Sixty failures stop at the intentionally
missing five-pin value assertion; they prove that missing interface, not the
downstream laws that those tests have not yet reached. Six failures exercise
existing entrypoints: missing lifecycle locking for a direct pointer, public
graph/projection writes that accept receiver material, standalone authoring,
missing-expectation desired admission, and explicit-null desired wire admission.
All reported failure/error identities belong to the new targets. Package setup
and collection succeeded. The ordinary harness removed its exact owned container
and network; compile/import stages were not reached after the failed suite.

The three errors earn no causal-red credit. Two gateway targets lacked the
existing seed builder's helper methods and failed during setup. The correction
reuses the five original static/class methods without inheriting predecessor
test methods. The explicit-null target recorded the expected missing rejection,
then read `captured.exception` after its failing assertion had been caught by
`subTest`. The correction keeps the exception/status/rollback assertions inside
the subtest and gives each route its own idempotency key and truth snapshot, so
one red route cannot substitute a cross-route receipt conflict for null rejection.
The second null route and both gateway bodies have no executable credit yet.

These target-only repairs await independent delta/evidence review and its
validation disposition; no automatic rerun or source implementation follows.
The hosted Operations run reached execution but hit its existing 30-minute
limit without a test summary; it adds no terminal causal-red or green credit.
Hosted Core/current-backend green are separate evidence. Full local output is
retained at `/tmp/cpk-1903-red-768dd6b9.log`; the governing PR is #1907.

Reuse real disposable PostgreSQL fixtures, graph builders and A's lock witnesses.
Do not construct a substitute lifecycle machine or reuse B's unrestricted writer
setup as a successful C2 admission. Accepted-current or historical-receipt setup
must be explicitly labelled retained evidence, separated from the command under
test, and may not claim C3 acceptance or execution. Kepler's bounded
[planning PASS](https://github.com/OpenJ92/control-plane-kit/issues/1903#issuecomment-5901545969)
requires coherent actual acceptance material wherever read, and missing/wrong
origin or acceptance negatives. B's generic action witness alone is insufficient.

Reviewed targets precede the ordinary causal-red gate; no application source
changes are allowed before North's separate source release. Owning command is
`./control-plane-kit-operations/test.sh`, exact clean architecture-testing
`7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`. No host Python, alternate runner,
selector feature or harness alteration. Collection/apparatus failure earns no
causal-red credit. Record exact failure identities at the target checkpoint.

Security/data/history: no effect or credential operation at this stage. Targets
must prove trusted authorization, bounded tenant-safe errors, immutable original
receipts and atomic graph/action/B/pointer truth. Finite C1 caps may refuse valid
large histories; no pruning, migration or recovery follows. B/C remain open for
joint acceptance. C3 owns all execution/reactivation closures, advancement
witnesses and C-N11 schedules; D and live/adoption remain held.
