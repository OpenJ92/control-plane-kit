Source: [control-plane-kit-operations/src/control_plane_kit_operations/advancement.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/advancement.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 918-line owner advances a workspace's current authored/realized graph lineage
to its pinned desired lineage after successful durable journal evidence. It defines
the command, result, service and errors; composes identity, authority, completeness
and replay checks; and groups pointer CAS, activity event and operation action in
one caller-supplied unit of work. Graph stores own pointer mutations and projection
representation. Core owns journal/schedule interpretation. The owner does not run
activities, call providers, create a realized projection or settle an execution run.

AdvanceCurrentGraph is a frozen dataclass with workspace/run/plan identities,
expected current authored/projection IDs, desired authored/projection IDs, desired
revision, worker authority, lease fence and idempotency key. General text fields
must be isinstance(str) and nonblank; their local helper adds no maximum length or
general character restriction. run_id is admitted through actual RunId construction
while the command stores the original canonical string. Invalid run admission is
raised outside the caught ValueError, avoiding its cause/context in that path.

Desired revision is exact nonnegative int, excluding bool. At least one of authored
or realized lineage must differ, allowing the authored graph to stay stable while
the projection changes. Authority and key use isinstance checks; the fence must be
exact ExecutionLeaseFence and its worker must agree with authority. EXECUTION_OPERATE
is checked at service execution, not by this constructor. The service does not
require an exact command class or rerun every constructor validation, so ordinary
value construction is not an exhaustive defense against forged nested inputs.

CurrentGraphAdvancementResult is a frozen value containing workspace/from/to
authored and realized coordinates, target projection digest, desired revision,
run/plan IDs, event, action and replayed=False. It validates canonical run identity,
nonblank relevant text, lowercase 64-character digest and exact nonnegative revision.
It requires CURRENT_GRAPH_ADVANCED without failure and ADVANCE_CURRENT_GRAPH action,
matching event ID, a nonempty execution-request ID and positive bounded claim
generation in the action. Event run ID must match the result.

The result's expected transition has nine fields: workspace, plan, run, from/to
authored and realized IDs, target digest and desired revision. Those fields decoded
from action payload must equal the result, and event evidence must equal that
transition exactly. Action payload may contain additional keys; it is not compared
to a closed key set. The constructor does not independently validate exact outer
event/action types, replayed as bool, action actor/session, matching timestamps or
absence of event activity/recovery. Service replay adds some of those identity
checks, but no universal result-coherence claim should exceed the actual predicates.

from_graph_id and to_graph_id are authored-identity compatibility properties.
descriptor includes explicit authored/realized coordinates, digest/revision, those
two aliases, run/plan, event/action IDs and replayed. It omits full action/event
payloads and worker claim material. This is a selected public representation, not
proof that every identifier or arbitrary retained event field is secret-free.

CurrentGraphAdvancementError has sibling NotFound, Conflict, Denied, Incomplete
and IdempotencyConflict subclasses. They distinguish absent coordinates, changed
lineage, inadequate claim/scope, insufficient journal success and reused command
intent. They are not all subclasses of Conflict. Missing-row wrappers and selected
malformed action/event wrappers raise fixed errors after leaving except blocks;
covered causes and contexts are therefore discarded. Other adapter, constructor,
clock, ID or SQL failures are not globally normalized by the owner.

execute first requires EXECUTION_OPERATE, then hashes command intent before opening
the unit of work. The hash covers command kind, workspace/run/plan, both authored/
realized lineages, desired revision, worker and claim generation using sorted
compact JSON and UTF-8 SHA-256. Scope tuple and idempotency key are not in this hash;
the key separately locates a session-scoped action. Additional scopes do not change
otherwise identical advancement intent, while worker/generation changes do.

Inside the transaction, unlocked run/request reads locate the request's session.
The owner takes the session/key action-idempotency lock and reads any existing
action. The actual history adapter uses pg_advisory_xact_lock over a namespaced
session/key hash. An existing action's kind/fingerprint is checked before locking
the request then run, rechecking their linkage and current worker ownership, and
entering replay. This ordering permits changed same-key intent to reject without
waiting on those request/run row locks; it is not a claim that the branch performs
no reads or no advisory locking.

Fresh execution locks session, workspace, request and run in that order after the
locator/key phase, then reads the plan. The session must be OPEN and still match
the request. Locked run/request linkage is checked again after the earlier locator
reads. There is no local general transaction retry or deadlock-resolution loop;
other services must follow compatible lock ordering and schema ownership rules.

_require_worker_owns requires CLAIMED status, a nonmissing claim, exact claim-fence
equality and authority worker equal to the supplied fence worker. It does not
compare lease expiry with wall/database time, renew the claim or reread approval
decisions. Both fresh and replay paths use this predicate. The injected service
clock supplies history time only after fresh pointer CAS. Describing this owner
as requiring an active unexpired lease or newly verified approval would overstate
its implementation; those are different checks from ownership/generation here.

_require_identity compares command with request workspace/plan, run plan, plan
session and both authored/realized graph IDs plus revision. Workspace current and
desired authored/projection coordinates and desired revision must all match.
Both realized projections must exist and belong to the workspace and the specified
authored source. A separate helper reads both authored graph rows and verifies
workspace ownership. These are durable representation/lineage checks, not a live
comparison between a runtime environment and the graph.

The actual
[graph store and projection representation](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/graph_store.py)
decode stored records. RealizedGraphProjectionRecord validates a canonical graph
descriptor and its digest over workspace, authored source, projection kind/key
and descriptor. That binds material to lineage when read through the normal store;
the advancement owner does not regenerate projections or derive runtime material
from application state. Stable-authored changes can therefore advance one realized
projection to another while retaining the same authored ID.

Fresh completeness begins with a SUCCEEDED run whose settled_at is non-None and
exactly one RUN_SUCCEEDED event at the end of ordered run history. It rejects its
explicit set of failed, unsupported, failed-uncertainty-resolution, compensation,
accepted-uncompensated-failure, failed-run and cancelled-run kinds. Other history
conditions are interpreted through journal projection rather than every event
kind being categorically forbidden.

The
[activity-journal adapter](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/activity_journal.py)
maps supported event kinds to Core journal values with event/run/ordinal/activity
coordinates and skips unmapped kinds. It does not pass failure/evidence payloads,
effect-attempt fingerprints or provider outcome preimages into that pure journal.
The actual
[journal and schedule functions](../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/saga.py)
validate unique increasing journal ordinals, one run and plan-owned activities,
fold step/uncertainty/compensation events, then derive plan/evidence scheduling.
Schedule derivation checks exact plan/evidence identities and coherent completion/
failure sets; its successful property requires terminal work with no blocked,
failed, compensated or compensation-failed groups.

The owner catches SagaJournalError, SagaStateError and ScheduleEvidenceError and
raises a fixed Incomplete error outside the catch. Other type/value errors are not
all included. It then requires no forward in-flight or uncertain events and a
successful schedule. Finally, Counter equality requires STEP_SUCCEEDED plus
STEP_UNCERTAINTY_RESOLVED_SUCCEEDED counts to cover planned activity IDs exactly.
A resolved successful uncertainty can satisfy these checks; raw unresolved
uncertainty cannot. The owner checks journal success, not full effect-attempt/
intent/outcome consistency or a new provider observation.

After those checks, compare_and_set_current_graph must atomically match current
authored/projection, desired authored/projection and desired revision before
replacing current pointers. The store also requires replacement to be the exact
desired lineage and validates projection ownership. None becomes Conflict.
The mutation changes current authored and realized pointers only; desired pointers/
revision, authored graph bytes and run status are not updated by this command.
The earlier workspace lock and the CAS predicate are complementary protections.

Only after successful CAS does the owner call its clock. It builds bounded
transition evidence with the nine result fields, allocates event ID and next run
ordinal, and writes CURRENT_GRAPH_ADVANCED. It then allocates action ID and next
session ordinal and writes ADVANCE_CURRENT_GRAPH with worker actor, command hash
and idempotency key. The action payload extends transition evidence with execution
request ID, claim generation and event ID. Claim generation belongs to the action,
not the public event transition evidence. The two writes share the sampled time.

The actual event/action adapters return their input records after INSERT; their
ordinal helpers lock parent run/session and choose MAX(ordinal)+1. This owner uses
the returned records to construct the result rather than comparing every writer
acknowledgement with an expected exact object. Result construction checks the
transition evidence but does not supply a complete hostile-adapter acknowledgement
protocol. The workspace CAS acknowledgement is checked only for None.

The service requests commit before constructing/returning _result, but the actual
[PostgresUnitOfWork](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
commits on successful context exit. A later event/action/result failure therefore
rolls back pointer and history writes despite that commit request. IDs and clock
calls are external and are not rolled back. There is no lost-commit-acknowledgement
resolver or automatic cleanup path here. Database failure, including a duplicate
action key/ID, can escape as its original exception.

Replay rechecks action kind/fingerprint, then binds action session/actor and request
workspace/plan, run ID/plan/request, payload execution request, transition IDs,
desired revision and claim generation to the command and locked request/run.
It rereads authored graph ownership and both realized projection lineages, and
requires the payload target digest to equal the stored desired projection digest.
It loads the payload's event ID and constructs the validated result with replayed=True.
Malformed action or event decoding on the specifically wrapped paths becomes a
fixed advancement error without retained cause/context.

Replay does not lock/read the current workspace, require the session still open,
rerun complete-success journal checks, require current run status still SUCCEEDED,
or require workspace current/desired pointers to remain at the accepted transition.
It returns the recorded advancement rather than reasserting present workspace
state. It still requires current CLAIMED fence ownership, so an old generation is
rejected after claim replacement. Graph/projection records and recorded evidence
must remain congruent. No pointer/history writer, ID factory or clock is called
on successful replay, although locks, reads and outer commit still occur.

Payload helpers require nonempty isinstance(str), exact nonnegative revision and
exact positive claim generation up to 2**63-1. They raise errors naming the missing
field rather than echoing its value. Event evidence is checked as an exact transition
dictionary by the result; action payload is decoded by selected keys and permits
extras. Missing rows and selected malformed evidence have explicit translations,
but the owner does not promise bounded/redacted output for every unexpected failure.
There is no logging sink or network route defined in this module.

The companion for the
[22-test suite](../../tests/test_current_graph_advancement.py.md)
documents canonical run/fence matrices, fresh success and replay after session
closure, stale claim/lineage, explicit held-row lock probes, bounded unchained
errors for selected persisted evidence, stable-authored projection rotation and
transaction rollback on late action failure. Its fixtures seed empty graphs,
synthetic claims and five success journal events without effect-attempt/outcome
or provider records; the fixed lease has expired before the injected history time.
The final two-worker different-key test can pass under a sequential schedule and
does not replace the stronger targeted lock-order probes.

Those tests protect selected observable laws, not every guard, malformed value,
write acknowledgement, concurrency ordering or current-state replay variation.
Several negative tests inspect only pointers/counts; new-service reads are not an
actual process restart. The projection rotation demonstrates accepted stored
lineage, not live verifier/key overlap. This owner supplies durable event/action
history for the accepted transition; downstream reads should distinguish that
record from a new assertion of runtime health or currently deployed material.

Read depth: the complete 918-line owner and full 1,456-line suite were read in the
preceding slice and retained. The full journal adapter plus actual Core journal
projection, schedule derivation/validation/blocking and schedule-success functions
were checked; no full Core saga transition-owner review is claimed. Relevant graph/
projection/pointer store and record/digest paths, selected event/action writers,
decoders/ordinal/lock helpers and full unit-of-work context were checked or retained.
No full execution/history/records dependency review is claimed. Validation was
documentation-only: local links, whitespace and frozen-source comparison. No
application imports, tests, database/provider calls, credential access,
source/inventory edits or publication were performed.
