Source: [control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_program.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/gateway_key_rotation_program.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This program separates approved rotation generation into prepare and submit.
prepare commits GENERATION_PREPARED intent and returns a reference-only action;
the caller owns provider generation. submit classifies supplied evidence and
admits the generated reference/key before moving rotation to KEY_GENERATED, or
returns a retry action/records uncertainty. This owner has no provider adapter
dependency and makes no external generation call. It is not the whole rotation:
overlap, activation, retirement deployment and completion remain separate owners.

PrepareGatewayKeyRotationGeneration requires bounded 1..200-character rotation,
actor_subject and prepared_by IDs, exact positive expected version, short
prepared_at text, and a nonempty typed scope tuple containing ROTATE and GENERATE.
SubmitGatewayKeyRotationGeneration requires typed action/result, bounded
submitted_by, short submitted_at and ROTATE plus REGISTER scopes. These are
DELEGATION_KEY_* scopes. Scope admission occurs in command constructors; methods
check command type and downstream services enforce their own scopes. Actor
subject, preparer and submitter need not be the same identity. Outer interfaces
must authenticate those supplied identities/authorities.

New preparation requires APPROVED at the exact expected version. The
[rotation service](gateway_key_rotations.py.md) previously validated a matching
rotation approval request/decision when admitting that status; this program
does not issue or reread a fresh generation-specific approval. It reads the active
provider for the replacement reference's provider ID, then asks
[DelegationKeyGenerationService](delegation_key_generation.py.md) to prepare a
grant for the rotation workspace/reference/purpose/issuer/correlation and caller
actor subject. That service reselects active registration and checks scope,
reference-prefix and allowed-intent policy through the custody helper.

The [custody helper](secret_providers.py.md) fingerprints workspace, provider
registration, reference, signing intent, actor subject, correlation and optional
operation/session/run/activity/effect coordinates. This path leaves those optional
coordinates unset. The fingerprint does not include requested time, endpoint or
credential references, or the outer generation purpose/issuer fields; the grant
carries those fields separately. Its custody ID has a narrower reference/intent
identity. A digest equality claim must therefore not be expanded into equality
of every grant attribute or provider readiness.

Provider/grant preparation are read transactions; no grant issuance ledger or
provider attempt is recorded there. A subsequent rotation transition with fixed
`<rotation>:prepare-generation` ID persists provider registration and custody
fingerprint as generation_action_digest. Rotation row CAS and its transition
record commit together, separately from the prior reads. Only after successful
commit does prepare return its action. No transaction spans the caller's provider
effect and no provider rollback/compensation is supplied here.

Prepared replay accepts only GENERATION_PREPARED at expected_version+1. It uses
the stored provider ID and finds exactly one prepare-generation transition with
APPROVED to GENERATION_PREPARED shape and matching destination version. Its
original advanced_at replaces the new caller timestamp when reconstructing the
grant. It rechecks active provider and custody policy, reconstructs the action
and compares its digest with the rotation. It does not revalidate every original
transition field/fingerprint or preserve a serialized original grant. Changed
actor_subject affects the digest; changed prepared_by is not independently
compared on this branch. Inactive provider truth can prevent reconstruction.

GatewayKeyRotationGenerationAction stores before/prepared versions, transition ID,
provider ID, digest and typed grant. It checks the version offset, provider equality
and custody fingerprint equality, not a fresh digest computation over every field
or a durable lookup. The prepared version uses an equality offset check rather
than an independent exact-int check. descriptor exposes bounded rotation/provider,
purpose/issuer/correlation evidence and the reference's provider ID, omitting full
reference path, endpoint and credential references. The full action/grant still
contains operational references and is not interchangeable with that projection.

GatewayKeyGenerationResult distinguishes GENERATED with typed evidence from
DEFINITE_FAILURE or UNCERTAIN with an identifier-shaped failure code. It has
generated/definite_failure/uncertain constructors. Evidence contains workspace,
reference, purpose/issuer/correlation, provider version, public key and a replay
flag; this program accepts that typed caller submission rather than authenticating
a provider response. Core public-key validation checks bounded public PEM framing
and computes a text fingerprint, not cryptographic private/public correspondence.

submit first matches the action against durable rotation provider/digest, expected
phase/version, fixed transition name and grant workspace/reference/purpose/issuer/
correlation. Allowed states are GENERATION_PREPARED at the action's prepared
version or KEY_GENERATED/BLOCKED at prepared_version+1; later rotation states
conflict. This match does not reload the original transition or rederive the
custody digest. It relies on supplied typed action fields plus the stored anchors,
with additional provider checks during fresh key admission.

DEFINITE_FAILURE returns the same action as next_action and leaves the prepared
rotation unchanged; no individual failure attempt or code is persisted by this
branch. The caller decides whether/when to invoke a provider again. UNCERTAIN
records a fixed generation-uncertain transition to BLOCKED with the submitted
code. A matching uncertain submission can read that block again; a different
code or outcome conflicts, and no retry action is returned. That blocked replay
does not set the program result's replayed flag to True. This is explicit result
classification, not automatic exception handling around a provider call.

For GENERATED, admission matches evidence to grant and constructs an ACTIVE
custody receipt locally from the supplied version data. It builds an admitted
secret-reference candidate and VERIFY_ONLY signing-key candidate. In one UoW,
the generation service locks the delegation purpose, rechecks active provider
and matching endpoint/credential references, then registers reference and key
together. The provider selector is non-locking and the complete prepare-time
prefix/intent helper is not rerun at this fold. Reference/key stores enforce
their own identity and replay rules; the locally constructed receipt is not
independent provider attestation.

After admission commits, a separate generation-succeeded rotation transition
records key ID and provider version ID/number at KEY_GENERATED. An interruption
between these commits leaves admitted material with the rotation still prepared;
resubmission relies on registration replay before the rotation fold. Concurrent
rotation drift can reject the latter after admission has persisted. There is no
global transaction, worker fence, attempt lease or removal of orphaned provider/
admission material in this owner. Supplied timestamps enter canonical UTC checks
in the imported persistence/transition layers, beyond the local 128-character
text check; changed transition timestamps can affect replay fingerprints.

At KEY_GENERATED, replay requires GENERATED evidence with matching workspace,
reference, purpose/issuer/correlation, key ID, provider version and action digest.
It returns replayed=True without another admission and with admitted=None.
This path does not reload provider, key/reference rows or compare the public-key
PEM/fingerprint when the key ID is unchanged. Thus it validates retained rotation
coordinates, not full present custody or public material. The fresh result's
admitted.replayed comes from submitted provider evidence, while program.replayed
describes this terminal classification; those are different signals.

GatewayKeyRotationGenerationProgramResult checks rotation/outcome types, exact
bool replay flag, presence of next_action exactly for definite failure and that
admitted is only supplied for GENERATED. It does not enforce every result-state
relationship or type/congruence of non-null next_action/admitted fields. Typed
construction and method names are not universal evidence/redaction guarantees.
Selected generation/rotation conflicts are translated using str(error) and
explicit causes; other validation/store errors can propagate. No blanket safe
error projection or bounded provider log is implemented here.

Durable history is the rotation's preparation/success/block transitions and
provider/digest/version fields plus admitted reference/key rows. There is no
per-attempt journal, saved external provider response or automatic reconciliation.
The caller/provider must supply suitable correlation/idempotency behavior after
unacknowledged generation; neither the Protocol nor the focused tests prove
durable provider support. This distinction is narrower than claiming a provider
defect or authorizing another external generation attempt.

The [focused tests](../../tests/test_gateway_key_rotation_generation_program.py.md)
use real PostgreSQL approvals and admissions but directly construct synthetic
evidence, with no provider adapter/cache. They cover stable prepare replay,
success/partial-admission folding, definite failure, uncertainty and selected
drift. The broader program-acceptance suite remains a separate review; it is not
credited here. Full 615-line owner and 452-line focused tests were read, retaining
full generation406/rotation/provider/key context. Actual grant preparation,
custody fingerprint/candidate, admission, approval transitions and Core public-key
validation were inspected. No executable validation, source changes, key/credential,
database/provider/runtime action or publication was performed for these notes;
the documentation adds no security surface.
