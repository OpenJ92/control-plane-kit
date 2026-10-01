# O2 / #1883 retained receiver health

Status: source gate failed; bounded fixture/contract correction awaiting review
and hosted validation. Governing [issue #1883](https://github.com/OpenJ92/control-plane-kit/issues/1883)
and [PR #1915](https://github.com/OpenJ92/control-plane-kit/pull/1915).

## Accepted boundary and evidence

O1 merge `9d03610c2bdd02e12c27c7e82ef1b23d44e8ae0c` is the accepted base.
The reviewed target-only checkpoint is head `6242a1fd1cd4bb18be998e2ee578090c27765c54`,
tree `9dd008a5b8eec318257fc2a2081459b1133d6e1c`. Its nine test files and
43 methods remain unchanged by this implementation candidate.

[Run 36791904903](https://github.com/OpenJ92/control-plane-kit/actions/runs/36791904903)
used actual CI merge `8c9f8988b366164234efc8a6239ab108b87f9d1f` with that same
tree and architecture-testing `7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`.
Core passed 21 integrity checks and 908 tests, compilation and import. Operations
ran 2,163 tests in 3,099.331 seconds: five failures and 37 errors, all new targets.
The independent reached boundaries were the missing V2 preparation record,
first-start receiver selection refusal and missing V1-live-pair refusal. The
other tails were blocked; none earned execution credit. Operations compilation
and import were not reached. Suppressed EXIT cleanup did not verify removal.

[Evidence](https://github.com/OpenJ92/control-plane-kit/pull/1915#issuecomment-5922257188),
[Meridian's intended-red PASS](https://github.com/OpenJ92/control-plane-kit/pull/1915#issuecomment-5922334268)
and [North's source release](https://github.com/OpenJ92/control-plane-kit/pull/1915#issuecomment-5922346691)
are the durable decision record. The red run does not prove that the green suite
fits its existing 60-minute limit. The separately passing current-backend run
used unchanged locked dependencies and establishes no O2 adoption.

## Implementation shape

- `health_effect_preparations.py` keeps V1 literal history and adds the explicit
  V2 nominal record/codec arm under the same 16 KiB bound and attempt ID domain.
  Target identity is stable; authority_context carries graph/projection identity.
- `_health_receiver_trust.py` composes existing Core common V2 configuration for
  workload and gateway own-control targets. Pure original reconstruction is
  shared with persistence; current slot/signer checks remain separate. Gateway
  transit retains its existing product-specific decoder and may use a different
  socket from gateway own-control.
- `effect_attempt_start_interpreter.py` checks existing O1 receiver permission
  before fresh health key/use work. `_health_effect_attempt_start.py` constructs
  and atomically retains only V2 for fresh health attempts.
- `postgres/health_effect_preparation_store.py` reconstructs V2 using original
  common bytes, complete graph binding sets and introduction action provenance.
  It does not call current selection or retirement policy. The V1 arm does not
  call a common configuration codec. Execute/fetch errors retain provenance.
- `health_signing_authority.py` acquires lifecycle, request, ordered runs,
  attempt, session and workspace before current permission, key and time work.
  Its private held-prefix value is scoped to the same UoW and database
  transaction. Nested reentry rechecks changed truth without discovering later
  keys or reacquiring lifecycle. A bare run prefix is insufficient.
- `effect_attempt_fold_interpreter.py` distinguishes terminal replay with
  nonlocking locators, rejects changed locators, and supplies the complete
  prefix for fresh health. Runtime authority stays in dispatch/fold.
- `coordinator.py` accepts exact Core ReceiverHealthReadResult and compares the
  complete request before projecting the existing outcome. No SDK verifier is
  duplicated. There is no new schema, provider, decoder registry or policy.

The new record is public from its owning module. The package root did not export
the old preparation record and does not introduce a new facade for the sibling.

## Directly reached fixture translation

Current-live health fixtures now use real workspace creation, desired-graph
introduction and execution admission. They retain explicitly recorded original
plan/approval, lease/run and predecessor journal premises where those owners
are outside the focused test. The intent includes the selected product bytes,
environment and runtime reference required by O1. This is not provider delivery
or accepted deployment evidence. The managed-chain fixture continues to own
coordinator translation and complete recording-port composition.

| Governed case | Current premise and required refusal/proof |
| --- | --- |
| Positive workload and both signed bootstrap stages | Real receiver introductions and canonical DESIRED initial-deployment admission; exact V2 start/store/reload |
| Transit issuer/workspace/runtime/node mismatch, workload key mismatch | Authorable selected artifacts; independent first-start trust refusal remains required |
| Transit slot ID/path and workload slot ID substitutions | Authorable common receiver material; independent live declared/selected slot join refuses |
| Declared-A/selected-B common slot redirect | Two separately valid slots; authoring succeeds and live slot agreement refuses |
| Unknown common profile or absent environment binding | Existing O1 scanner treats these as non-receiver material; live health selection independently refuses |
| Common purpose/socket/declaration mismatch or missing selected common path | O1 authoring can reject malformed receiver material; test instead corrupts the original projection after valid owner setup and requires first-start refusal with all original witnesses intact |
| Different retained receiver ID in original common bytes | A different ID is not inherently invalid for a new introduction; post-setup corruption tests refusal to rebind the original identity |
| Reload common codec refusal | Documented Core refusal on coherent original bytes remains independently exercised; does not rely on malformed authoring |
| BASE and equal-content source identity | Explicit V1 historical codec/store premise preserves both projection IDs and BASE authored identity; no fresh BASE admission claim |
| Exact replay without transit support/current keys | No injected transit or legacy common decoding; pure original V2 configuration reconstruction is permitted |
| Existing prepared-reload same-UoW run mutation | Obtain the actual lifecycle-first complete prefix before request/run locks; preserve successful initial reload, held RUNNING snapshot, changed-run refusal, no-new-latest/no-write assertions and rollback history |

Corruption helpers preserve original descriptor digests, bindings, introductions
and actions, compare unchanged history while the corruption is present, and
restore the exact descriptor in `finally`. Such negatives establish original
integrity refusal; they do not claim a later common codec was reached. No target
is skipped, weakened or pointed back to a historical implementation.

Fresh A/B/C positives are three isolated empty-current worlds. Desired revisions
retain the same pending introduction before each world's sole admission. They
are not three accepted deployments; #1912 owns accepted-current retained update.

## Security, data and operational history

The current authority boundary changes representation, not who may act. Actor
scopes, exact approval, latest RUNNING attempt, fence, selected membership and
active key/reference/provider chain remain required. Stable IDs cannot borrow
another graph's permission. No new route, public exposure or credential access
is introduced. Preparations and returned resolution references remain protected;
private key material is neither resolved nor persisted here.

First-start writes its existing event, intent, attempt, two secret-use records
and one preparation in the caller transaction; failure rolls them back. Insert
once and exact replay prevent renewal. Reload writes no history. Fold uses the
existing structured transition/outcome owners and releases the transaction
before external delivery. Changed locators or stale guards refuse; no automatic
retry or compensation is introduced. Retired history remains reconstructible
without granting dispatch. No cleanup or retention policy changes.

## Source review and first implementation gate

Meridian's source review of candidate `495651bd` found one omitted governing
translation in `test_postgres_lifecycle_health_fold_locks.py`: its positive
nested reload still supplied a bare run prefix. The correction obtains the
actual complete health prefix before later locks and preserves the full law
above. Production bare-prefix refusal remains intact. No other concrete
production blocker was found in that affected-boundary review; this is source
review, not green execution evidence. Meridian passed that delta and Kepler
passed the source architecture at `6c51f008`; North released one hosted gate.

[Run 36800988280](https://github.com/OpenJ92/control-plane-kit/actions/runs/36800988280)
checked out merge `81dba69037cd9999f90a75c65eb585ab861cc0d9`, tree
`f97ccb2a1da64a84c2b99a8fca4a29c953c44018`, identical to source
`6c51f0088324c0f67588766222698e93b7ada8d5`, with accepted O1 and that source as
parents. Architecture-testing remained `7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`.
Core passed 21 integrity checks, 908 tests and compilation/import. Operations
ran 2,163 tests in 2,526.675 seconds: three failures and 73 errors, ordinary exit
1 rather than timeout. All 43 unchanged receiver-health targets explicitly
passed, including their lifecycle, revocation, original-history and retirement
tails. All 73 older errors occurred during shared fixture admission setup:
59 missing ingress-use scopes and 14 missing runtime-use scopes. Their later
method bodies receive no execution credit. Operations compilation/import were
not reached; suppressed cleanup remains unverified.

The three assertion failures were stale exact fold import/call expectations,
the coordinator's old result import, and its expected inventory dependency set.
The unchanged locked Backend run `36800988404` passed all nine stages but still
selected CPK `f45384e7`; it is not O2 adoption or Operations cleanup evidence.

[Terminal inventory](https://github.com/OpenJ92/control-plane-kit/pull/1915#issuecomment-5923367803)
and [independent classification](https://github.com/OpenJ92/control-plane-kit/pull/1915#issuecomment-5923402204)
retain exact source associations, error groups, target outcomes and log hashes.

## Bounded correction and next boundary

North [released correction edits only](https://github.com/OpenJ92/control-plane-kit/pull/1915#issuecomment-5923402549):
the positive fixture's admission actor explicitly receives `PLAN_EXECUTE` and
both authority-use scopes; the two contract files track only the reviewed
production import/call/dependency changes. The shared helper default, production
authorization, independent missing-use/register-only laws and all 43 targets
remain unchanged. Each exact fold call delta is traced in its test companion.
No authentication law is relaxed; no runtime, credential, provider or durable
data effect is introduced by the correction.

Meridian must review this delta before North's next checkpoint/run decision.
Restored setup may expose other older-law failures; their behavior and full-green
runtime cost remain unproven. Required green includes all targets, older laws and
Operations compilation/import. No local duplicate, harness change, automatic
rerun, merge, live mutation or downstream dependency adoption is released.
