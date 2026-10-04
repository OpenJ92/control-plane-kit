# C2: exact configuration cleanup inspection and approved intent

Issue: [#1928](https://github.com/OpenJ92/control-plane-kit/issues/1928), child of
[#1920](https://github.com/OpenJ92/control-plane-kit/issues/1920).
Accepted base: C1 merge `7f1ac6235d40203819165305784d4aee8841b9f9`, tree
`5a60242a1ef37446b4d840bd3c6e950696207f9e`. Branch targets
`roadmap/1813-runtime-control`.

## State of evidence

The final field, authority, capacity and target design received independent
Meridian and Kepler PASS. See the [complete contract](https://github.com/OpenJ92/control-plane-kit/issues/1928#issuecomment-5970580636)
and [accepted refinements](https://github.com/OpenJ92/control-plane-kit/issues/1928#issuecomment-5970619807).
North released concrete targets, checkpoint and causal red. Meridian accepted
the actual missing-interface red at `c37efd65`: Core 943 tests with two expected
failures and 941 passes; focused Operations 40 tests with 32 expected failures
and eight passes, no errors. Compilation/import did not run after those failures.
At source checkpoint `59f07021`, the ordinary Core gate passed 943 tests and
support/compile/import; focused Operations passed 41 tests in 524.909s and
compile/import. See the [terminal record](https://github.com/OpenJ92/control-plane-kit/pull/1930#issuecomment-5971516602).
This is focused C2 evidence, not full Operations or provider acceptance.

The source checkpoint introduces immutable canonical proposal/inspection values,
one composed evidence read, the dedicated read-only snapshot, exact plan/action
publication, and conditional lifecycle locking and evidence revalidation in the
existing approval owner. No SQL schema change is required. The publication
receipt follows the plan/action/replayed convention without inventing the graph
transition required by the separate graph-planning result. Its public name is
`ConfigurationCleanupPlanningResult`.

Intermediate independent review found missing reciprocal whole-selection
consistency inside proposal syntax, a missing durable session/workspace binding,
parser exceptions escaping the fixed redacted boundary, and an overly broad
accepted-occurrence run ID. Bounded corrections and targets passed in source1.
The approved fixture-only run-ID padding fixes
canonical lexical order without changing any assertion or original red evidence.

Consolidated review identified unbounded command-prelude reads before the shared
inspection ledger. The follow-up extends the same read/accounting instance from
publication's action lock and from approvals' action lock plus fixed-size exact
target routing through replay, lifecycle lock, reloads, original evidence and
returned write values. Contradictory or missing markers remain bounded; proven
legacy graph/rotation bodies retain their existing behavior. Before clock/IDs,
the caller checks the finite remaining owner reads and returned write scalars
against the same budget. Near-capacity fault injection and actual SQL telemetry
protect this complete boundary. Missing cleanup owners return fixed command
errors; existing approval missing-target APIs keep `ApprovalTargetNotFound`.
These follow-up corrections still require their owning gate and frozen review.

## Chosen boundary

Operations inspects explicit original-attempt/artifact selectors and complete
expected allocation refs. It proves B's original birth, all reciprocal claims,
full original invocation selections and complete direct outcomes. Exact current
membership and every retained claim protect an allocation. Desired graph and
projection values pin staleness; equal desired material cannot choose a historical
allocation incarnation or erase a claim.

Inspection distinguishes active, terminal-unprofiled and completed invocations.
It returns every requested candidate with fixed blockers, or a whole-request
Unavailable/Capacity result. An invocation selecting `{a,b}` cannot be proposed
for closure by requesting only `{a}`. Inspection exposes counts and commitments
without disclosing the unselected sibling. C only proposes closure; it neither
releases claims nor proves provider non-use.

The new plan envelope freezes the exact proposal and digest. Destructive approval
uses the existing approval owner with a subject binding plan ID and proposal
fingerprint. Legacy wire and immutable receipts retain their meanings. A fresh
cleanup approval cannot fall back to an unprofiled plan-ID subject.

Inspection owns one fresh repeatable-read, read-only snapshot. Publication and
both fresh approval commands follow action/replay, nonauthorizing locators,
lifecycle lock, session, workspace, reload/evidence, then writes. Receipt replay
is not renewed dispatch authority. The two existing cleanup execution refusals
remain closed.

## Target law provenance

Governing law cards C-L01–10/C-N01–06 were extracted before the source dry run
and retained in the [parent design](https://github.com/OpenJ92/control-plane-kit/issues/1920#issuecomment-5969529717).
The finite target groups are:

| Target group | Classification | Governing behavior |
| --- | --- | --- |
| Core approval subjects | New-law C-N05; preserved C-L03 | Exact proposal digest, closed new form, unchanged legacy digest |
| Operations cleanup contract | Strengthened C-L01/02/07; new-law C-N04/05 | Exact refs and witnesses, whole selection, context, fixed plan shape, codecs and bounds |
| Same-original evidence/cache | Isomorphic C-L08; strengthened C-N02/03 | Complete original proof, full result, neutral cache cannot satisfy B's success consumer with FAILED |
| Current and retained claims | Isomorphic C-L09/10; strengthened C-N04 | All claims, exact current use, same-material incarnations and explicit partial selection |
| Snapshot/accounting | Strengthened C-L09; new-law C-N03/04 | One coherent snapshot/ledger, actual transport, fixed limits and complete refusals |
| Planning/auth/history | New-law C-N04/06; preserved C-L05/07 | Trusted context before UoW, exact publication, replay, staleness, atomic rollback |
| Approval/current validation | Strengthened C-L03/04/05/07; new-law C-N05/06 | Destructive/separate principals, exact subject, lock order, no downgrade, valid/corrupt/legacy current data |
| Existing execution refusals | Isomorphic C-L06 | Approval cannot activate cleanup translation/admission |

The contract tests use explicitly syntactic data. They do not certify retained
provenance or provider state. The membership fixture's optional simulated result
callback receives an actual runtime request; its result flows through the existing
start/fold/outcome owners. Its default producer and prior assertions are preserved.

The 64-claim owner target advances every completed reuse before starting the next:
B requires each historical use's original acceptance. This corrects the initial
test draft, which incorrectly omitted intermediate advancements. The real test
proves complete inspection with a current-use blocker, not deletion eligibility.
The deliberately copied 65th ref/claim sentinel is separate negative reader
evidence, inserted atomically to preserve reciprocal foreign keys. Global budget
exhaustion injection is explicitly a boundary test, not natural producer capacity.

Maximum-size fixtures use valid 512-byte paths with legal path segments, admitted
Unicode/escaping domains, both occurrence variants, and the complete 512KiB plan
envelope. Canonical numeric capacity refuses rather than rounding. Actual
PostgreSQL byte accounting is measured independently.

## Validation sequence

Concrete target review precedes the pushed target checkpoint and ordinary Docker
causal-red runs. Core's ordinary script has no filter. Operations uses its existing
repeatable `-k` option for the named C2 classes; this is focused evidence, not full
package acceptance. The clean architecture-testing prerequisite remains
`7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`.

Application source starts only after the concrete missing-behavior red is reviewed.
The same targets must become green. Full owning package and pinned current-backend
evidence follow at the issue's required review boundary. No harness, dependency pin,
skip, xfail or schema/reset change is part of this target stage.

## Inspect, review, publish

The caller obtains exact original attempt/artifact refs and the five current
pins through authenticated Operations composition. The authenticated command
context is supplied separately from those values:

```python
query = InspectConfigurationCleanup(session_id, workspace_id, pins, selectors)
result = cleanup_service.inspect(query, context=trusted_context)
if result.state == "complete":
    review = result.inspection.descriptor()
    blockers = [row["blockers"] for row in review["candidates"]]
    # Present all candidates and blockers to the operator. A partial selection
    # can report "incomplete-invocation-selection"; never add siblings silently.
    if not any(blockers):
        command = RequestConfigurationCleanupPlan(
            session_id, workspace_id, IdempotencyKey("reviewed-cleanup"),
            pins, selectors, result.inspection.evidence_digest,
        )
        receipt = cleanup_service.request_plan(command, context=trusted_context)
        # Existing RequestApproval then DecideApproval bind the exact proposal.
        # The destructive approver must be distinct from the requester.
```

This creates reviewable durable intent. Neither the receipt nor approval opens
the retained admission and runtime-effect cleanup refusals. If inspection is
unavailable/capacity, no partial candidate set is returned. If current truth
changes before publication or approval, the caller must inspect and review again.

## Security, data and handoff

Only bounded identifiers, commitments and fixed reasons are public. Ordinary
outcome observations and failures remain internal; the nonempty endpoint fixture
must survive owner decoding without appearing in inspection. Trusted scope and
workspace checks precede evidence access. Existing destructive approval and
principal separation are retained.

Plan/action and approval mutations use caller-owned transactions and preserve
historical replay. No provider I/O occurs under these locks. Both old history and
new exact profiles must survive current-data validation without repair.

D #1921 will own completion linkage, actual closure, claim dispositions and
reservation. Interpreters #177 owns terminal production and fresh provider non-use
proof. C2 does not enable either boundary or authorize live resource changes.

## Final C acceptance — 2026-10-03

The intermediate validation paragraphs above are historical checkpoints. C1 and
C2 are accepted. PR #1930 merged as
`f0eff627aec3b983ef075439cda43eac6459ebaa`, tree
`f50d4d90410f32f88f51c739b596c5007bd34221`, exactly matching the reviewed source
and tested synthetic merge tree. Meridian and Kepler issued final PASS; North
closed C2 #1928 and C parent #1920.

Final owning evidence: Operations 2343 tests, Core 943 package plus 21 support
tests, and compile/import passed. Required current-backend passed its declared
pinned-family composition; it does not establish C2 runtime adoption or provider
behavior. [Final validation and exact coordinates](https://github.com/OpenJ92/control-plane-kit/pull/1930#issuecomment-5973298211)
and [independent review handoff](https://github.com/OpenJ92/control-plane-kit/pull/1930#issuecomment-5973312504)
retain the source3 full failure and source4 structural correction honestly.
A successful-suite psycopg connection warning remains an unattributed,
nonblocking cleanup observation; no warning-free claim is made.

D #1921 planning starts from this actual merged base. Original claims remain
protective and both cleanup execution refusals remain closed. Planning D does
not release targets, source activation, provider effects or a live grandparent run.
