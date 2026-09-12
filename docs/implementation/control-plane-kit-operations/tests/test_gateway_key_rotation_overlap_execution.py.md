Source: [control-plane-kit-operations/tests/test_gateway_key_rotation_overlap_execution.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotation_overlap_execution.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These seven tests exercise the
[overlap execution wrapper](../src/control_plane_kit_operations/gateway_key_rotation_overlap_execution.py.md)
with real PostgreSQL services and simulated adapter results. setUp requires the
operations test database, installs/verifies schema and resets by truncating
workspaces CASCADE, seeding the
[shared graph/key/approval fixture](gateway_rotation_overlap_fixture.py.md), and
running the actual overlap preparation program. It is database-mutating
validation for an isolated test database, not a pure fixture. No tests were
executed for this documentation change.

Preparation produces the stored checkpoint and prepared version with a real
worker-a generation-1 claim. Commands supply that fence, rotate actor scope,
execution-operate worker scope and configurable idempotency key. Preparation and
execution use deterministic textual clocks and IDs; lease time is still supplied
by PostgreSQL. Synthetic public PEM and opaque private references come from the
fixture. No cryptographic key pair is generated or used to contact a gateway.

RecordingAdapter records activity IDs, consumes supplied outcomes and raises
AssertionError if its outcome list is exhausted. Its runtime entry point maps
the supplied outcome to a RuntimeEffectResult with the requested effect ID. It
can raise an injected BaseException after recording entry. The actual coordinator
is wired to effect start/fold/reconciliation services; its fixture observer
returns fixed indeterminate evidence. An empty recovery adapter plus zero calls
asserts no second simulated provider dispatch, not an observation that a real
provider did or did not mutate resources.

The happy path requires more than one planned activity, calls progress with a
distinct key per step and checks intermediate dispatched outcomes. The final
call must yield accepted/overlap-ready with an accepted checkpoint, exactly one
reported attempt for that call and advancement carrying the command's claim
generation. Total adapter calls equal activity count, current moves to desired
realized projection, authored graph identity/count stays unchanged, and a fresh
program replay yields accepted-replay without more calls or another advancement
event. This is stable-truth replay; it does not cover every later rotation state,
lease expiry, altered accepted history or arbitrary changed replay commands.

A stale generation-2 fence against the stored generation-1 claim conflicts
before adapter entry with an exact bounded message and no cause/context.
Another test denies stale prepared version, missing actor scope and missing
worker scope before dispatch, then changes desired revision to 99 and requires
conflict with no adapter calls. This does not prove absence of every possible
earlier read receipt or durable mutation on all rejected paths.

Failed and uncertain adapter outcomes map to blocked with overlap-effect-failed
or overlap-effect-uncertain, one recorded adapter call, fixed update time and
unchanged current projection-a. Unsupported, paused/in-flight variants and all
constructor-negative cases are not individually exercised here. Blocking means
retained uncertainty/failure, not successful compensation or cleanup.

The intent-loss case raises SimulatedProcessLoss inside adapter entry after the
start/intent commit. Repeating the same command through a fresh program returns
blocked/overlap-effect-uncertain with an empty recovery adapter, one STEP_STARTED,
no graph advancement and current projection-a. This same-key coordinator path
sees an incomplete command receipt and returns uncertain without invoking the
observer. The fixture's presence of an observer is not evidence that this case
performs reconciliation. A direct SQL generation change to 2 then makes the old
command's blocked replay conflict with a cause/context-free stale-authority
message and no dispatch; this is test mutation, not a worker takeover workflow.

The post-effect interruption matrix first completes all but the final activity,
then raises after one of five committed boundaries: effect fold (5), run complete
(6), coordinator receipt complete (7), current graph advance (8), or rotation
fold (9). The counts are tied to this concrete composition. Every case asserts
one start and success event per planned activity and no duplicate recovery
adapter call. At boundaries 5 and 6, the command receipt is still incomplete:
recovery blocks as uncertain with zero new attempts, no advancement and current A
despite recorded step success. Boundaries 7 and 9 accept or replay acceptance
with one advancement. This is intentionally narrower than claiming all durable
success prefixes converge automatically to accepted.

Boundary 8 verifies current already desired while rotation is still prepared,
then manually changes claim generation and rejects the stale command without
changing rotation/transitions/events/advancement counts. The test obtains a
replacement handoff for the same worker and directly calls the rotation fenced
writer with the accepted checkpoint and existing advancement evidence. That
manual new-fence fold reaches overlap-ready. It is not a recovery performed by
the original command, automatic lease adoption, a provider retry or live operator
procedure. All interruption cases use a wrapper that raises BaseException after
physical commit; none kill a process or interrupt the database commit itself.

Eight acceptance-evidence mutations are tested after current advancement but
before rotation fold: missing action, missing event, foreign request, foreign
event link, event transition drift, and accepted graph, projection or time drift.
Each case changes generation to 2 and uses a fresh handoff, then calls the
rotation service writer directly. It requires the fixed incongruent-evidence
conflict with no cause/context and compares a durable snapshot before/after the
attempt. The snapshot includes workspace, rotation, transitions, all action
IDs/payloads and event identity/type/time/payload. This protects the final writer;
it does not show the wrapper's already-accepted replay revalidates that history.

The helpers isolate effect-attempt events by the exact evidence-key set
{effect_attempt}, count CURRENT_GRAPH_ADVANCED events/actions and query the
stored accepted checkpoint from its unique advancement action/event. Their
snapshot omits other store tables and some action columns, so equality is a
focused nonmutation assertion, not a full database image comparison.

Read depth: full 885-line test, 203-line wrapper and 651-line shared execution
kernel, retaining the full shared fixture and rotation/preparation contexts.
Selected actual coordinator receipts/dispatch/classification, advancement gates/
replay, effect-attempt authority/expiry and database lease observation paths were
inspected. Expired leases, genuinely concurrent providers/workers, real runtime
restarts, automatic takeover, key activation, retirement and cleanup are not
proved by these tests. No executable validation, credentials, provider or runtime
actions were performed for this documentation; it introduces no security or
durable-mutation surface.
