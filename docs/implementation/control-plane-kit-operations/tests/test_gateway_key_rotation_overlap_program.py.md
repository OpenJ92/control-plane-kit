Source: [control-plane-kit-operations/tests/test_gateway_key_rotation_overlap_program.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotation_overlap_program.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These four tests cover the
[overlap preparation program](../src/control_plane_kit_operations/gateway_key_rotation_overlap_program.py.md)
against real PostgreSQL and canonical Operations services. setUp requires the
operations test database, installs/verifies the schema and resets workspace truth
with TRUNCATE workspaces CASCADE. reset_truth then invokes the full
[shared fixture](gateway_rotation_overlap_fixture.py.md) to seed product-backed
graph metadata, synthetic key records and actual rotation approval records.
The file is database-mutating validation for an isolated test database; no tests
were run for this documentation change.

The command helper uses graph-a/projection-a, desired revision 1, the seeded
rotation version, operator-a and worker-a, with a 1,800-second lease. Defaults
include rotate, plan-execute and execution-operate actor scopes; the program's
fixed actor requirements are only the first two, with execution-operate on the
worker. Because the helper uses scopes or defaults, passing an empty tuple would
restore default scopes rather than test empty authority. The negative test uses
a nonempty rotate-only tuple. Worker authority is fixed in this helper, so this
file does not cover a missing worker scope or different worker identity.

The program factory injects a finite sequence of textual timestamps, a trusted
epoch of 2000 and a fresh deterministic ID counter. The database still chooses
claim lease timestamps. No provider key generation occurs in setup; synthetic
public PEM and opaque private references establish stored test truth. Actual
approval request/decision records are created through the fixture's services,
unlike the simpler projection-only test's directly seeded approval identifiers.

The first test requires PREPARED, overlap-deploying rotation and a prepared
checkpoint with the actual approval IDs, unchanged authored graph, base
projection-a, canonical desired overlap projection and revision 2. It checks
prepared_at equals the expected start timestamp, handoff/checkpoint equality
and ExecutionLeaseFence(worker-a, 1). Database assertions retain the current
projection while changing desired and require one plan, execution request and
run, two activity events and zero observations. These are persisted preparation
and lifecycle facts; the count does not itself assert every possible effect
table is empty. Source composition contains no runtime activity adapter here.

A fresh program instance then repeats the original command, requiring
PREPARED_REPLAY with exactly the same checkpoint/handoff and unchanged plan/
request/run counts. It does not advance time beyond lease expiry, change claim
generation/worker, close the session, mutate child evidence or request another
lease duration. This is narrow stable-truth replay coverage, not a universal
permission to retry deployment.

The restart-named test resets truth for each commit number 1 through 8. The
shared CrashAfterCommitUnitOfWork raises SimulatedProcessLoss only after a real
successful commit at the chosen count: initial rotation read, session,
publication, plan, admission, claim, start or final rotation checkpoint. The
exception derives from BaseException and bypasses ordinary handlers. Recovery
uses a fresh program/UoW and another ID prefix, then requires prepared or
prepared-replay, overlap-deploying status and exactly one plan/request/run.
These cases exercise recovery after durable prefixes. They do not kill a
process, lose a connection during commit or compare every retained precrash
identity/event/action; identity equality is explicit in the first replay test,
whereas this loop chiefly checks status and cardinality.

The stale-input test rejects a changed source version, forged settled projection
IDs and rotate-only actor scope, checking that no activity run exists after each
attempt. It does not assert that all earlier child sessions/actions are absent.
In particular lineage validation happens after session creation, so absence of
a run must not be reported as absence of any durable mutation. The cases reuse
the same reset truth rather than resetting after each individual rejection.

The later-state test first prepares and then uses the shared fixture's actual
overlap execution program with SuccessfulAdapter to obtain accepted checkpoint
evidence. It snapshots child session/plan/request/run/event counts and requires
ALREADY_ADVANCED plus the same accepted checkpoint and unchanged counts when
preparation is called again. Runtime results are simulated, not observed at a
gateway. This one overlap-accepted case does not test every member of the
program's later-state set, its excluded retirement-ready/old-key-retired/
revocation-prepared statuses, or blocked/checkpoint combinations. Nor does it
exhaust the program result constructor's relationship checks.

The private _count helper interpolates only its fixed allowlist of table names;
_child_counts measures sessions, plans, requests, runs and events. Counts and
fresh UoWs supply database evidence, not live process health. Missing cases
include concurrent preparation, changed/fenced worker authority, partial child
evidence drift, factory failures and equivalent phase material under another
physical projection ID. Under that last conditional prerequisite, source
inspection shows publication/planning can precede admission's canonical-ID
rejection and publication replay can conflict; routine public provenance of
such a row has not been established and this file does not reproduce it.

Read depth: full 290-line test and 495-line program, full actual 145-line shared
child helper and retained full 620-line fixture. Selected session/planning/
admission/rotation-authorization, lifecycle claim/start/replay and database claim
contracts were inspected with prior full rotation/projection context. The
separate execution owner/tests were not reviewed in full for this pair. No
credential, key, provider, database or runtime actions were executed; the notes
introduce no security or durable-mutation surface and no new live-test claim.
