Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_retirement_program.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_retirement_program.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This program prepares the approved A+B-to-B child after the stored grant-drain
deadline. It publishes desired B-only material, plans and admits its change, claims
and starts an Operations run, then records a prepared retirement checkpoint.
Preparation stops at retirement-deploying; it does not execute runtime activities,
accept the B-only current graph, retire key A, revoke its secret or complete the
rotation. Those are separate owners and evidence boundaries.

PrepareGatewayKeyRotationRetirement names rotation/source version, authored
graph, settled current/desired realized projection and desired revision, actor/
scopes, worker authority and lease duration. It validates bounded 200-character
identifiers, exact positive/nonnegative versions, matching expected current and
desired projections, typed scopes and imported worker/lease values. Scopes are
deduplicated/sorted. Imported ExecutionLeaseDuration bounds exact integer seconds
to 1..3600. Command construction is not authentication or current lease proof.

prepare requires actor DELEGATION_KEY_ROTATE and PLAN_EXECUTE plus worker
EXECUTION_OPERATE before its rotation read, including replay and later-state
classification. It does not require execution-operate on the actor separately.
New work requires draining-old-grants at the expected version, approval request/
decision IDs and a persisted deadline. It samples an exact-integer trusted epoch
and rejects now < deadline before child creation. The initial rotation read still
has its own successful UoW commit. No grant enumeration, sleeping or consumer
observation occurs; this is a stored deadline gate, not a live grant-drain probe.

The full [shared preparation helper](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_deployment_preparation.py)
uses prefix `gkrot-retirement:` plus SHA-256 of rotation ID and stage suffixes
`:session`, `:projection`, `:plan`, `:admission`, `:claim`, `:start`. Session,
publication, planning, admission, claim/open and run start each commit separately.
The program then writes a `:prepared` rotation transition in another transaction.
Together with the initial read, these are the eight commit boundaries exercised
by the tests. The program has no encompassing transaction, automatic retry loop
or compensation for an already committed prefix.

[Retirement publication](gateway_key_rotation_retirement.py.md) rechecks the
deadline on new publication, requires settled workspace lineage, derives exact
verify-only A / active B into desired B-only verifier material, and records
projection/revision/action atomically. Planning validates and compiles the pinned
realized graph diff, checks applicable process authority delivery admission, and
records a canonical plan/action. The helper supplies no external readiness
attestations to admission. Graph-dependent runtime/ingress use scopes and other
admission requirements can therefore reject later than the fixed initial gate,
after earlier child transactions exist.

The selected actual [admission owner](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/admission.py)
interprets draining-old-grants as the retirement phase of the approved rotation
subject. It checks the original subject/review digest, approval request/decision
IDs, approved rotate-approve authority and high/destructive review meaning, plus
the original approval request action. It rederives the phase graph, requires the
canonical retirement projection ID/kind/key, compares the plan to the canonical
diff, checks workspace/plan lineage and the child publication action's rotation/
version evidence. Replayed admission uses its retained request/action fingerprint
path rather than rerunning every fresh-plan check.

Claim/open and start use the supplied worker authority and lease. The underlying
claim transaction records a generation-1 request lease with database timestamps,
a claimed run and RUN_OPENED event/action. The separate start transaction checks
current worker/fence and records running status with RUN_STARTED event/action.
The helper constructs its prepared checkpoint from admitted approval/request
identity and the actual plan/run, setting prepared_at to the start event time.
It returns the checkpoint plus claim fence. It performs no adapter call.

The [rotation fenced writer](gateway_key_rotations.py.md) locks/rechecks request,
run, plan linkage, current fence and rotation before moving draining-old-grants
to retirement-deploying and recording the checkpoint/transition. Its trusted
clock checks the drain deadline again. Early gate, new projection publication
and final fold sample independently; this owner does not enforce a monotonic
clock across them. A later failure, including a backward clock or stale fence,
does not undo a published projection, admitted request or started child run.

For retirement-deploying, prepared replay requires a prepared retirement
checkpoint and rotation version source_version+1. It compares approval IDs,
unchanged authored identity, expected base projection, canonical retirement
desired projection ID and desired revision+1. It obtains a current handoff for
the supplied worker and returns PREPARED_REPLAY. It does not rerun the deadline,
publication, admission, claim or start, compare new actor/lease duration with
the original command, renew a lease or independently test lease expiry. The
handoff reader verifies stored child linkage and current worker claim; returning
it does not establish current provider or runtime readiness.

ALREADY_ADVANCED covers only completed, with rotation version greater than the
requested source version and matching checkpoint lineage. It returns no handoff
and does not query current child execution/advancement evidence or require a
particular checkpoint status in this local classifier. Retirement-ready,
old-key-retired, revocation-prepared and blocked all conflict. This must not be
described as accepting every later retirement state. Normal completion laws
belong to the rotation writer; the result constructor here checks value types
and requires a typed handoff for prepared outcomes, not full status/evidence
congruence among all supplied objects.

Shared projection limitations remain relevant. Core sorts verifier keys by ID,
while retirement derivation compares current keys to unsorted (old, new), so
old key-z / new key-a can fail despite valid overlap material. If equivalent
phase material already exists under another physical projection ID, publication
can reuse it and planning can commit before admission rejects its noncanonical
ID; publication replay can also fingerprint-conflict on that returned ID.
The latter requires that preexisting equivalent row, whose routine public
workflow provenance is not established. Both are source-confirmed caveats,
unexecuted and unfixed in this documentation; the fixture's key-a/key-b names
and canonical generated projection IDs do not cover them.

Selected child planning/admission/rotation/publication/workflow/lifecycle errors,
including plain ValueError in the child block, become preparation conflicts via
str(error) and from None. Selected admission/projection/lifecycle denials map to
authorization-denied. Rotation authorization failure in the main child block
maps to conflict, while prepared-replay handoff authorization failure is mapped
explicitly to denial. The initial clock call is outside that child try block.
Suppressed displayed causes do not erase retained context or sanitize arbitrary
messages; database/factory failures are not universally normalized.

Outer interfaces authenticate actor/scope values and control exposure of
operational references/history. This program does not read private keys or
credentials, resolve secrets, delete history or perform provider cleanup. Session
actions, plan, admission, lifecycle events and rotation transition explain each
committed stage, including partial preparation. Their existence is not live
runtime success and is not authorization to retry external work.

Read depth: full 470-line owner and
[152-line tests](../../tests/test_gateway_key_rotation_retirement_program.py.md),
retaining full retirement fixture366/publication183/shared child helper145 and
reviewed rotation/overlap contexts. Actual retirement admission and final drain
fold were rechecked; selected session/planning/admission/lifecycle contracts had
prior direct reads. Separate retirement execution tests remain outside this
pair. No tests, source/pins, keys, credentials, database/provider/runtime actions
or merges were performed for the notes; no security/mutation surface was added.
