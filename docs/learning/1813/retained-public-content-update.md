# Retained public-content updates — O1 / #1971

Base: accepted C1 `a13dc6c0d84bad50d7f4b89df61368bd2a29e5e2`.
Destination: `roadmap/1813-runtime-control`.

The user clarified that obsolete plan formats and APIs need not remain compatible
before deployment. The [revised plan](https://github.com/OpenJ92/control-plane-kit/issues/1971#issuecomment-6116058593),
[independent plan review](https://github.com/OpenJ92/control-plane-kit/issues/1971#issuecomment-6116121106)
and [release](https://github.com/OpenJ92/control-plane-kit/issues/1971#issuecomment-6116138013)
govern this bounded consolidation.

## Chosen shape

One Core entry point, `compile_managed_update_activity_plan(current, desired)`,
recognizes the existing closed add/remove relation and C1's exact retained public
JSON content relation. The private recognizers and operation ordering stay with
the management compiler. The content-only public function and exports are removed
without an alias. This intentionally replaces the test requiring the common
entry point to refuse valid content; useful add/remove/content laws remain.

Operations consumes that union through its existing `MANAGED_UPDATE_V1` profile.
Content admission checks every relevant selected participant in the affected
Docker runtime and its complete receiver route set. Extra retained receivers are
included; unrelated runtimes do not become routes or effects. No fixed workload
name, product identity or X/Y count is added to this relation.

Execution image authority is the immutable registered descriptor selected by
identity and digest. Optional `oci_image` node metadata is descriptive and is not
an Operations selection input. Desired instance content overlays declared slots
through the existing lowering owner. A selected image-pull authority refuses this
content family in event-free lowering, before a fresh attempt can commit.

No additional enum, effect kind, table, migration, command or duplicate state
machine is introduced. Existing configuration preparation, original replay,
coordinator receipt checks and independent fold already consume this profile.
Those paths are exercised rather than copied. Unrelated unprofiled/cleanup
representations are outside this issue.

## Governing laws and evidence

Core preserves C1's exact content-only relation, strict wrapper selection, scope,
no-op, disruptive replacement-before-health, unchanged infrastructure and input
values, and secret/data/authority exclusions. Existing add/remove positive,
ordering and refusal laws remain. One obsolete API-separation assertion is
explicitly replaced, following the user correction.

Operations tests target current profile roundtrip, no-op profile selection,
complete registered participants/routes, exact canonical plan and selected
instance material. Real PostgreSQL/application scenarios establish the original
receiver first, then prepare/approve/admit/execute content replacement, retry the
same command, refuse forged/missing installation proof, and explicitly advance.
Recording ports prove Operations ordering/correlation and transaction boundaries;
they do not prove physical installation.

Target `422e6d9d`: unchanged Core Docker gate ran 979 tests with exactly two
expected common-entry content acceptance failures; all 21 support tests passed.
Maintained Operations admission selection ran four tests with four expected
profile-selection failures, no errors. [Causal-red record](https://github.com/OpenJ92/control-plane-kit/issues/1971#issuecomment-6116223270).
Expanded target `b63b15cd` produced five intended profile failures and three
fixture errors: a deliberately stale command reused the valid command's key.
Those errors receive no causal-red credit. The authorized one-line correction
uses a distinct negative-intent key. Corrected target-only `653a10c3`, with
unchanged production, ran all three service cases to the intended stored-profile
failure, with no errors (212.918 seconds).

Implementation `fbaa820b` passed the unchanged Core gate: all 979 package and
21 support tests, integrity, compile and installed import. Candidate `3d15034b`
ran 28 focused Operations tests: 26 passed, including actual replacement,
wrapper-reference reuse, app-reference change, forged-fold refusal, history,
retry, advancement and missing-receipt uncertainty. Two test expectations were
incorrect: the product contract canonically sorts artifacts, and the existing
async Operations application forwards `InvalidOperationCommand` directly.
Corrections preserve full artifact equality and the no-effect/attempt/reference
snapshots, with an exact fixed refusal message. They require no production
change. The affected two-test rerun passed full selected-material equality and
reached the exact pull refusal, but its global snapshot incorrectly prohibited
an independent management observation before refusal. Review narrowed this to
the intended mutation boundary: complete runtime/ingress traces, all configuration
refs/claims, no attempt at any number for the exact Reconcile, only graph-authorized
management observations, no desired workload health and unchanged current graph.
No source ordering change is required. The affected single-test rerun at
`3c4fc67f` passed in 83.791 seconds; the unchanged owning script completed
integrity, compile and installed import with exit 0. All 28 selected laws are
green across the retained 26 passing cases and these focused corrected reruns.
Application source stayed identical to reviewed `fbaa820b` throughout the test
expectation corrections. Hosted Core and current-backend passed at `efce87b3`.
Hosted Operations ran 2,653 tests in 9,154.465 seconds and failed with one failure
and one error; full acceptance remains held. The exact runtime-intent architecture
manifest omitted the reviewed same-package `PlanDerivationProfile` import and
one `type`, `any`, and `InvalidOperationCommand` call each. Its exact policies
remain intact; only those entries change.

The older #1950 feasibility test used a TEXT/liveness/incomplete-management
fixture that remains outside the supported content contract. O1 now rejects its
material-change candidate at planning instead of allowing a generic plan to reach
coordinator refusal. Initial-start/read-budget feasibility remains unchanged.
The negative law now expects the exact planning refusal and checks unchanged
graph/execution truth plus all plan and approval rows. It cannot advance the
accepted graph or create a new plan, approval, run, or attempt. No production
effect port exists on the planning service; the retained effect-start tripwire
and durable-state checks carry that boundary evidence. The disconnected recording
adapter and its empty-list assertions were removed as vacuous after review.
No production admission relaxation or fixture conversion is required. Kepler independently
classified both failures and North released these bounded test corrections.
The maintained two-test local reproduction on `efce87b3` reproduced one failure
and one error in 24.639 seconds, exit 1. The corrected focused pair passed in
15.814 seconds. The disconnected-adapter cleanup followed the test-container
snapshot; it changes no executed command or effective assertion. The owning
script completed integrity, compile and installed import with exit 0. Production
remains identical to reviewed `fbaa820b`. This is combined evidence from the
prior full run's 2,651 passing tests and two corrected focused passes, not a
single complete green run. Acceptance and the next full-suite timing remain
North/user decisions; no replacement CI run is started by this checkpoint.
[Hosted failure record](https://github.com/OpenJ92/control-plane-kit/pull/1981#issuecomment-6117979646).

## Security, data and history

The expanded admission relation is the changed security surface. Current runtime
and actor authorization, active exact registered products, receiver scope,
canonical plan and independent result correlation remain required. A healthy old
receiver cannot certify replacement: coordinator and independent fold require
the original request/selection's REPLACED receipt before accepting Reconcile.

Existing owners retain session/plan/approval, configuration protection,
attempt/outcome, current-pointer and history facts. Fresh preparation retains its
atomic transaction boundary; no provider call spans a database transaction.
Original current-model replay retains exact immutable material and never
reselects today's values. Failure/uncertainty is not automatic rollback or retry.
No live/shared record reset, secret resolution, credential output, provider,
ingress, image publication or network exposure is introduced.

## Handoff

S1 / Servers #249 consumes the accepted coordinate and proves actual source
process replacement and business behavior. Physical provider work and later
image/secret/verifier/declaration families retain their own acceptance gates.
Live grandparent testing remains downstream. The original tunnel, DNS and
protected token are unchanged.

## Retained receiver read correction — #1982 (focused green; owning gates pending)

S1/Servers #249 exposed a larger retained-current topology than O1's original
positive fixture. A reviewed owner-only reproduction at `8206de53` passed the
original control but refused four configured receivers before dispatch. At the
final permission check, 4095 consumed records plus a prospective 5-record query
exceeded the unchanged 4096 limit. The earlier forecast was 3062 consumed plus
755 peak remaining records (3817); accounted bytes were below 16MiB. This proved
an incomplete suffix expression, not missing credentials or a need to raise caps.

Source review found that two reference validations each reread current/desired
origins and accepted history. The expression still modeled initially empty
current. #1982 keeps one complete fresh traversal per existing validation call,
preserving separate current/desired tuples and comparing each to its exact pinned
original projection. The legacy source helper retains its exterior and ordering.
No evidence/permission is reused across checks, writes, commands or transactions.

The matching expression now covers both complete retained sides, their maximum
captured origin multiplicity, and every captured acceptance-action group with
request/material verification, runs, events, advancement actions and historical
bindings. It uses source-derived widths/cardinalities, not measured constants or
cache credit. Native caps, shared ledger, approval alternatives and rollback stay
unchanged. Source changes are confined to `receiver_lifecycle.py` and
`postgres/configuration_preparation_store.py`.

Test context and causal evidence:

- Strengthened retained public law: unchanged O1 assertions with two additional
  initially accepted siblings. Wrapper origin/history/replay/current advancement
  remain part of the test. At target checkpoint `61ce5bcc`, prospective records
  exceeded forecast (756 > 755); both new post-preparation refusal tests passed.
- Strengthened footprint law: every actual query reservation and final settled
  footprint must fit the declared suffix. A small shared test utility observes
  real calls on the original accounting object, including rejected reservations.
- Strengthened multiple-history law: the existing real add-B/removal-C scenario
  proves two distinct first-acceptance actions, then measures its existing reverse
  update. At `63447e09`, it failed 642 > 641; original uncertainty assertions remain.
- Strengthened corrupt-proof consumer law: current/desired order, duplicates and
  graph-side tuples are tested independently after original start insertion.
  Representative current-order target red did not raise the required conflict.
  This fault injection does not claim the real material reader reorders rows.
- Freshness/rollback: a real binding deletion after start insertion reached final
  receiver-permission denial, rolled back target attempt/intent/outcome and
  configuration protection, and dispatched nothing. Existing approval, captured
  growth, direct-write and lock-order laws remain governing evidence.

First focused red: 3 tests/341.956s (one intended failure, two passes). Remaining
focused red: 3 tests/353.001s (two intended failures, final permission negative
passed). Integrity passed and exact Docker resources were removed in both runs.
The reviewed implementation at `8ba51c66894611c1b4c9a38c8a781446e18add09`
passed all 12 focused tests in 852.563 seconds: original O1 control, expanded
four-receiver update/forecast, genuine two-history update/forecast, all six tuple
variants and three freshness/refusal cases. The maintained Operations script also
passed integrity, compile and clean installed import, exiting 0; both dedicated
Docker resources were absent afterward. No diagnostic fixed-line observers
remain. Normal Core/Operations/current-backend gates and final independent
acceptance remain pending; focused green does not close S1 or this owner issue.

Security/data/history: no new credential/network surface, schema, authority type,
budget increase, provider call or durable semantics. Transaction/lock boundaries,
fresh proof, exact replay and uncertain-effect behavior remain. The residual risk
is an omitted branch or overbroad membership comparison; forecast and refusal
tests explicitly protect both. Formula-only repair would reject earlier and
cannot alone satisfy S1. Accepted owner merge and normal dependency adoption must
precede Servers S1; live grandparent testing remains downstream.

[Owner issue, law plan and red evidence](https://github.com/OpenJ92/control-plane-kit/issues/1982).

### Full-gate receiver-free regression and correction

The first normal gate at `8ad5b69e` did not pass: Operations ran 2663 tests in
6622.557 seconds with 111 failures and four errors (including repeated subtest
entries). Core and current-backend passed. The preceding focused green did not
establish owner acceptance.

The failures had four families. Forty-eight identity/factory entries failed
because a fixed empty-material double did not provide newly invoked getters.
Sixty-four cleanup entries stopped at a shared rehearsal premise encoding the
old repeated traversal counts. Two large-provenance cases failed while building
ordinary configuration history with 409585-byte metadata, before their final
publication measurements. The compact accepted-use boundary failed its first
no-new-start-ID assertion after a fold conflict; subsequent runtime and retained
state assertions were not reached in that failed run.

Source review found that the optimization had unconditionally added retained
graph/projection getters even for receiver-free originals. This expanded actual
transport and the suffix forecast; treating all failures as stale fixtures would
have hidden a compatibility and capacity regression.

The bounded correction restores the old meaningful empty-membership behavior:
after existing pins and fresh original verification, derive both expected tuples
and check both complete raw durable binding sets when they are empty. The raw
reader independently derives receiver membership from current stored material.
Receiver-bearing originals still use one fresh complete traversal and exact
side-preserving equality. The forecast uses the smaller branch only if the
maximum across all captured binding sets is zero. Limits, history, transaction
boundaries and refusal policy are unchanged.

Two additional proof-consumer tests return canonical false-empty original
projections after actual verification and start insertion, while keeping real
receiver material. One also deletes a durable binding. They require rejection
by the receiver validator itself, final permission refusal, rollback and no
target dispatch; later unrelated rejection cannot satisfy the test. Review
caught and corrected a test-construction false positive before execution.
The identity fixture remains unchanged. Cleanup's shared premise alone changes
from repeated `(14,14,2)` reads/two event fetches to `(4,4,1)`/one; all downstream
security and transport assertions remain.

Local checkpoints were deliberately kept unpublished during focused validation
to avoid automatically starting another full gate on the open PR: targets
`3dd19e32`, cleanup structural premise `0eb1df4a`, source `e1fbaec1`. Kepler reviewed
both exact deltas before execution. At `e1fbaec19b5fd6a5e8c63e0d3e2791e31a244f6d`,
the maintained Docker runner passed all 67 selected tests in 2402.894 seconds,
including all four failed families, all start-bound tests and the retained
receiver/forecast/freshness targets. Integrity, compilation and clean installed
import passed; script exit was zero and the exact owned container/network were
absent afterward. The unchanged 64-use boundary and both large-metadata laws
passed. Full normal CI and final acceptance remain required before downstream
Servers adoption; this focused result does not establish live acceptance.

[Full-gate classification and correction plan](https://github.com/OpenJ92/control-plane-kit/pull/1983#issuecomment-6130058291).
[Exact correction review](https://github.com/OpenJ92/control-plane-kit/pull/1983#issuecomment-6130134308).
[Focused command and local checkpoints](https://github.com/OpenJ92/control-plane-kit/pull/1983#issuecomment-6130149644).
