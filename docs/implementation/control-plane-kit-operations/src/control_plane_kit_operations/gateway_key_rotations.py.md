Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotations.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotations.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner records the intent and ordered progress of a gateway signing-key
rotation. GatewayKeyRotation binds workspace, gateway node, purpose, issuer, old
key, replacement secret reference, generation correlation, lifetime/skew and
operator correlation. It accumulates approval IDs, generation identity,
replacement key/version identity, two deployment checkpoints, activation/drain
time, retirement and revocation evidence. It calls Operations stores; it does
not generate keys, deploy a gateway, resolve secrets or revoke provider bytes.

The normal status chain is requested, awaiting-approval, approved,
generation-prepared, key-generated, overlap-deploying, overlap-ready,
new-key-active, draining-old-grants, retirement-deploying, retirement-ready,
old-key-retired, revocation-prepared, completed. Awaiting approval can instead
become rejected; approved and subsequent nonterminal states can become blocked.
Completed, rejected and blocked are terminal. Blocking retains checkpoints and
requires a failure code; it also releases the one-nonterminal-rotation binding.
That release does not establish that uncertain child effects were compensated.
There is no transition here that resumes a blocked rotation.

Records check enum/value types, bounded identifiers, digest shapes, positive
versions and selected paired fields. A deployment checkpoint is prepared with
no accepted coordinates/time, or accepted with all three present. It retains
session, plan, approval, execution-request, run, base/desired graph and projection
coordinates, revision and preparation time. A revocation checkpoint pins a
provider registration, secret reference, exact provider version, revocation ID,
correlation, action digest and preparation time. Bare constructors neither
recompute intent hashes nor prove the full historical state chain. Lifetime
1..300 and skew 0..60 use range comparisons rather than exact-integer checks;
the epoch clock and several version/deadline fields have stricter integer checks.

Request and advance services require DELEGATION_KEY_ROTATE from caller-supplied
typed scopes. Request validates canonical UTC requested_at before opening the
UoW, then locks workspace/node/purpose/issuer. Its candidate ID is gkrot_ plus a
canonical JSON SHA-256 fingerprint of intent, including correlation and secret
reference, excluding requested_by, requested_at and scopes. Same workspace/
correlation plus the same fingerprint returns the retained record and original
attribution, including a terminal record. Different intent conflicts; another
nonterminal rotation for the binding conflicts. Request does not itself prove
that the graph node, old active key or replacement provider admission exists.

Ordinary advance validates all supplied timestamps, including nested checkpoint
times, before UoW/replay access. It rejects deployment preparation, acceptance
and deployment-blocking shapes, which require advance_deployment. Under the
rotation row lock, an existing transition ID with an identical fingerprint
returns the current rotation, not a historical post-transition snapshot. This
ordinary replay bypasses expected-state, approval and epoch-clock checks;
timestamp and scope admission still precede it. The fingerprint includes actor,
time, expected state/version and all command evidence, but excludes scopes and
the separate deployment handoff/fence. Changed reuse conflicts.

A new transition checks expected status/version and approval evidence, reads a
nonnegative exact-integer trusted epoch, applies the legal state transformation,
then performs store CAS and inserts a transition record in the same UoW. The
record retains from/to status and version, fingerprint, actor/time and failure
code; it is not a full copy of the command. Commit is requested within the UoW;
successful exit commits, and failure rolls back the grouped writes.

Awaiting approval requires a stored undecided request whose subject equals the
rotation projection and whose required scope is DELEGATION_KEY_ROTATE_APPROVE.
Approval/rejection requires a stored decision for that request with the supplied
decision ID, matching decision kind and that review scope. The
[Core approval subject](../../../../../control-plane-kit-core/src/control_plane_kit_core/approval_subjects.py)
binds rotation/workspace/node/purpose/issuer/old-key identity, lifetime/skew and
the intent digest, and describes overlap roles old/new and retirement role new.
It excludes the raw replacement secret reference. It is review meaning, not a
credential; outer authentication establishes the caller identities and scopes.

deployment_handoff requires EXECUTION_OPERATE worker scope, reads the stored
checkpoint, plan, execution request and run, checks their linkage to the rotation
and approval, and requires the stored claim's worker ID to match. It returns the
claim's [ExecutionLeaseFence](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_leases.py), containing worker ID and
generation; repr hides this field on the handoff. These reads take no row locks.
advance_deployment locks the execution request, run and rotation, rereads the
plan, repeats linkage checks and compares the current claim's full fence with
the handoff. It then checks checkpoint identity and acceptance before reaching
transition replay. Thus deployment replay can fail on changed execution truth
or authority even when its transition fingerprint matches. This owner makes no
independent lease-expiration clock comparison and hides no provider call there.

Deployment shapes are closed: key-generated to overlap-deploying or
draining-old-grants to retirement-deploying carries the prepared checkpoint;
deploying to ready carries its accepted form; deploying to blocked carries the
prepared handoff and no command deployment value. Existing checkpoint identity
must agree apart from status and accepted coordinates/time. Plan linkage binds
session, base/desired graph/projection and revision; execution linkage binds
workspace, request, run, plan and approval IDs.

Ready acceptance looks up the phase-specific hashed rotation :advance action
in the checkpoint session, loads its event and constructs
[CurrentGraphAdvancementResult](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/advancement.py).
That imported constructor requires a failure-free CURRENT_GRAPH_ADVANCED event,
an ADVANCE_CURRENT_GRAPH action, matching event/run/plan/workspace and claimed
base/accepted graph transition, projection digest, revision and positive claim
generation in the action payload. This owner additionally requires execution
request/session linkage and equality of action, event, acceptance and transition
times. It validates recorded evidence against the claimed accepted coordinates;
it does not independently read today's workspace pointer or contact a gateway.
The advancement/execution programs own producing that evidence and accepting
the planned graph. Selected malformed evidence is converted to a fixed conflict
outside the exception handler; other lookup paths can retain exception context.

Activation records the supplied canonical timestamp but calculates the drain
deadline from trusted epoch now plus maximum lifetime and skew. Retirement
deployment requires now >= that persisted deadline. No sleep or enumeration of
outstanding grants occurs here. Generation/key admission, activation, retirement
and revocation transitions record supplied evidence; this service does not
recheck signing-key or provider-custody stores. Completion requires an accepted
retirement checkpoint, a revocation checkpoint and supplied revocation time.
It is not independently verified provider deletion.

get and transitions take rotation identity without workspace or scope arguments;
outer interfaces must authorize access. read projects an explicit subset that
omits secret references and detailed checkpoints. Full records still contain
opaque secret references and operational identities, and repr suppression of a
fence is not a universal logging/redaction guarantee. This owner creates no
network listener and no separate runtime activity run/event; linked child
history and its own durable transitions explain progress. It deletes no history
and implements no provider compensation.

Read depth: full 1,381-line owner, full
[357-line store](postgres/gateway_key_rotation_store.py.md) and
[731-line tests](../../tests/test_gateway_key_rotations.py.md); full 620-line
shared overlap fixture; selected actual Core approval subject, advancement
result/validators, worker authority and schema contracts, plus the full small
lease-fence contract. UoW and canonical temporal boundaries were previously
read in full. This documents the inspected source, not a live rotation claim.
No executable tests or runtime/provider actions were performed for this
documentation change; no new security or data-mutation surface was introduced.
