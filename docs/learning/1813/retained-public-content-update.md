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
No source ordering change is required. The affected single-test rerun and full
hosted acceptance remain pending.

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
