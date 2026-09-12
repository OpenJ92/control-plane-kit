Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_overlap_program.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_overlap_program.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This program prepares a gateway rotation's overlap child through a started
Operations run and a durable prepared checkpoint. It composes session, desired
projection, planning, admission, run lifecycle and rotation services. Each owns
its transaction; the program is not one atomic transaction across those stages.
It neither executes planned runtime activities nor accepts their effects. A
started run here is durable control-plane state, not a running gateway proof.

PrepareGatewayKeyRotationOverlap names rotation/version, expected authored graph,
settled current/desired realized projection, desired revision, actor/scopes,
worker authority and requested lease duration. Identifiers use the local bounded
200-character grammar; version/revision are exact positive/nonnegative integers.
Current and desired expected projection IDs must agree. Scopes are a typed tuple,
deduplicated and sorted. Worker authority and lease must be the imported value
types; ExecutionLeaseDuration bounds seconds to an exact integer in 1..3600.

prepare requires actor DELEGATION_KEY_ROTATE and PLAN_EXECUTE, plus worker
EXECUTION_OPERATE, before reading the rotation, including replay/later-state
paths. It does not require EXECUTION_OPERATE on the actor separately. These are
supplied authority values, not authentication. Other graph-dependent admission
scopes can be required later, after earlier transactions have committed.
The initial rotation read itself requests a UoW commit. New preparation requires
key-generated at the expected version and non-null approval request/decision
IDs; actual approval meaning is checked by admission, not by this initial gate.

The full [shared child helper](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_deployment_preparation.py)
uses prefix `gkrot-overlap:` plus SHA-256 of rotation ID and fixed suffixes
`:session`, `:projection`, `:plan`, `:admission`, `:claim`, `:start`. These correlate
the separately committed operations without accepting a caller idempotency key.
It starts a session with rotation/phase metadata, publishes the desired overlap
projection, requests the exact current-to-desired activity plan, admits that plan
under the rotation approval, claims/opens its run, then starts the run. The final
rotation transition uses `:prepared`. Retry behavior comes from those services'
identity and evidence checks, not from an in-memory progress counter here.

[Projection publication](gateway_key_rotation_overlap.py.md) requires settled
authored/current/desired lineage and derives exact A+B verifier material from
stored keys. It changes desired projection/revision, keeping current unchanged.
The selected actual planning path locks session/workspace, checks expected
pointers/revision, validates and compiles the realized graph diff, checks fresh
runtime-authority delivery admission where applicable, and records plan/action.
Planning replay verifies retained action/plan linkage and recompiles the stored
graph pair; it does not simply accept any plan with the same identifier.

[Execution admission](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/admission.py)
requires a nonempty planned, execution-ready plan and current workspace lineage.
For a rotation approval it checks the stored rotation subject/review digest,
approved decision and IDs, rotate-approve scope, high-risk/destructive review
meaning, and the original approval request action. It rederives the exact phase
graph, requires the canonical phase projection ID/kind/key, compares the plan
with the canonical graph diff, and requires one matching publication action in
the child session with rotation/version/lineage evidence. It also enforces
graph-dependent runtime/ingress use scopes, process authority delivery admission
and any external readiness requirements. The helper supplies no readiness
attestations. Admission replay checks retained request/action fingerprints
before those fresh-plan checks; a receipt does not reauthorize external effects.

ClaimAndOpenActivityRun uses the worker and requested lease. The selected actual
[lifecycle owner](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py)
and PostgreSQL claim path require a queued, unclaimed request with no run;
claim_request locks it and uses PostgreSQL clock_timestamp to set generation 1,
claimed_at and lease_expires_at. That transaction inserts a claimed run,
RUN_OPENED event and CLAIM_RUN action. StartActivityRun checks matching stored
request claim/worker/fence and transitions claimed to running with RUN_STARTED
event/action. Its timestamp comes from the injected program clock, independently
of the database lease clock. These selected start/claim replay checks do not
independently compare wall time against lease_expires_at.

The helper builds a prepared checkpoint from admitted approval/request identity,
the stored plan's base/desired graph/projection/revision, and started run ID.
prepared_at is the start event's occurred_at, not a new timestamp. The handoff
contains that checkpoint and the claim fence. The
[rotation service](gateway_key_rotations.py.md) then uses its fenced deployment
writer to reread/lock execution request, run and rotation, verify linkage/fence,
CAS key-generated to overlap-deploying and record the transition. Its trusted
epoch clock is distinct from both injected textual event time and database
claim time. Final checkpoint failure does not roll back earlier child commits.

For an existing overlap-deploying rotation, preparation requires a prepared
checkpoint and rotation version equal to requested source version plus one.
It compares phase, approval IDs, unchanged authored identity, base projection,
canonical overlap desired projection ID and desired revision plus one. It then
reads a current deployment handoff for the supplied worker, returning
PREPARED_REPLAY. This path does not rerun admission, claim or start, renew a lease,
or compare the new actor/lease duration with the original child's command.
The returned fence comes from current stored claim identity/linkage; it is not
an independent live lease-expiration or provider readiness proof.

ALREADY_ADVANCED is deliberately a finite classification. It covers overlap-ready,
new-key-active, draining-old-grants, retirement-deploying and completed, plus
blocked when an overlap checkpoint exists. Rotation version must be greater than
the requested source version and checkpoint lineage must match. It accepts
either checkpoint status there and returns no worker handoff. It does not query
the child run/claim or revalidate an acceptance event. Retirement-ready,
old-key-retired and revocation-prepared are absent from this local set and
conflict despite being later statuses in the rotation chain. Rejected also
conflicts. This classification must not be described as all later states or
as proof that blocked effects succeeded.

The result constructor checks rotation/outcome/checkpoint types and requires a
typed handoff for prepared/prepared-replay outcomes. It does not independently
prove rotation/checkpoint/handoff congruence or all status laws; the program and
downstream services establish those relationships. There is no general outer
retry loop, checkpoint repair, failure compensation, lease takeover or history
cleanup in this owner. A failure can leave a child session, published desired
projection, plan, admitted request or started run available for subsequent
service-level reconciliation; it is not an instruction to retry live work.

One source-derived limitation carries over from publication: if equivalent phase
material already exists under a different physical projection ID, save can reuse
that row. Its provenance through a routine public workflow is not established.
Under that prerequisite, preparation can commit publication and planning, then
admission rejects the noncanonical phase ID. A later publication replay can also
conflict because the stored fingerprint used the original candidate ID while
the receipt supplies the reused ID. The program's own checkpoint classifier
requires the canonical ID too. This conditional finding is not an executed
failure case or a source fix; the companion tests do not cover it.

Selected child planning/admission/rotation/publication/workflow/lifecycle errors
are converted to preparation conflicts using their message and from None.
Selected admission, projection and lifecycle denial subclasses instead become
authorization denial. A rotation authorization denial in the main child block
falls through to conflict, while the prepared-replay handoff path maps it to
authorization denial explicitly. Database errors, plain helper ValueError and
other exceptions can propagate. Suppressing displayed cause does not erase
exception context or bound/redact arbitrary imported messages. No credentials
or private key material are resolved by this program; outer interfaces still
own authentication and safe exposure of operational references/history.

Read depth: full 495-line owner, full 145-line shared child helper and
[290-line tests](../../tests/test_gateway_key_rotation_overlap_program.py.md),
with retained full 620-line fixture, rotation owner/store and overlap projection/
builder/publication context. Selected actual session, planning/replay, admission/
rotation-authorization, lifecycle claim/start/replay and PostgreSQL claim paths
were inspected. The separate overlap execution owner/tests remain outside this
batch. No tests, database, provider, key, credential or runtime actions were
executed; this documentation introduces no new security or mutation surface.
