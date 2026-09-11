Source: [control-plane-kit-operations/tests/atomic_effect_attempt_fold_fixture.py](../../../../control-plane-kit-operations/tests/atomic_effect_attempt_fold_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 90-line fixture adds six construction helpers to
[EffectAttemptFoldFixture](../../../../control-plane-kit-operations/tests/effect_attempt_fold_fixture.py).
It supplies the direct-outcome and recovery-without-outcome arms used by the
[atomic fold contract tests](../../../../control-plane-kit-operations/tests/test_atomic_effect_attempt_fold_contract.py).
There are no test methods, store calls or transaction boundaries in this file.
"Atomic" names the consuming contract, not a guarantee established by constructing
these values. NewlyFolded and ExistingFold are re-exported constructor bindings,
not evidence that this helper committed or replayed anything.

direct_outcome_record accepts an OutcomeStory object, unlike the parent helper's
story-name/compensation arguments. It obtains an admitted outcome through the
inherited outcome_for, derives observation IDs through the inherited helper,
calls the actual effect_outcome_observation_records function with workspace-a
and story.attempt, then constructs EffectAttemptOutcomeRecord from those values.
It does not use a copied expected-observation implementation or read persisted
rows. The supplied story already contains its attempt snapshot.

The inspected [outcome fixture](../../../../control-plane-kit-operations/tests/effect_outcome_evidence_fixture.py)
constructs execution-result or provider-observation stories for ordinary and
compensation event families. outcome_for chooses ExecutionEffectOutcome for the
execution-result profile and ObservedEffectOutcome otherwise. Observation IDs
are deterministic strings containing the story name and one-based endpoint index;
they are not globally allocated identities. Synthetic provider values and private
endpoint examples are constructor inputs, not evidence of provider calls or probes.

The actual [outcome owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py)
checks the direct attempt/outcome identity, request fingerprint, state and start
event correlation before producing observations. The helper supplies no intent
record. RuntimeEndpointObservation projection yields UNKNOWN status and transport
probe outcome, marked FRESH at the latest event's time; this is not HTTP health
success. The owner's separate VerificationCompleted branch needs authoritative
intent context, so this fixture should not be treated as a general verification
record builder. EffectAttemptOutcomeRecord further validates the bound attempt,
failure and observation rows; construction is still not storage or authorization.

direct_command derives both transition and failure from the same admitted outcome.
It defaults request_id to request-a, worker authority to worker-a with
EXECUTION_OPERATE, and the execution fence to worker-a/generation seven. Callers'
keyword changes replace any defaults before FoldEffectAttempt construction. This
supports negative test inputs without bypassing that production constructor.
The helper does not independently assert the command's field surface; consuming
tests use their inherited or local require methods for interface checks.

recovery_command instead accepts a story string, defaults to recovered-succeeded,
uses the parent's transition builder and sets outcome=None. Only recovered-failed
gets the parent's synthetic terminal FailureEvidence; other recovery stories get
None. The parent builds reconciled success/failure or abandonment transitions from
fixed decision and fingerprint values. These are fabricated test decisions, not
an executed observation or user approval. Overrides again reach the constructor;
an unsupported story is not given a new safe fallback by this helper.

recovery_record obtains the parent's typed record for the selected story and
compensation flag. Only recovered-failed replaces the latest event with a copy
carrying the same synthetic failure used by the command, then reconstructs
EffectAttemptRecord with the existing state and original start event. Other
stories return the parent's record unchanged. The parent selects ordinary versus
compensation event kinds and fixed event IDs/ordinals; its default original time
is later than the latest time, so these examples are not chronological traces
from a live clock. No durable event is rewritten by dataclasses.replace here.

direct_result calls the caller-supplied variant with story.attempt and a newly
constructed direct outcome record. recovery_result calls it with the recovery
record and None. Neither helper validates which callable the caller supplied.
The actual [fold language](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold.py)
validates direct command congruence with the outcome and requires no outcome on
the recovery arm. Its two result constructors validate a non-STARTED attempt and
require a matching direct outcome record, or None for a recovery decision. Those
production value checks explain the fixture shape; they do not establish locks,
transaction atomicity, idempotency, concurrent replay or effect dispatch safety.

The parent loads language/service modules optionally and exposes missing symbols
as None for explicit consuming-test interface guards; transitive import failures
are re-raised. The present file adds no absence guard, retry or error translation.
Its exports are only this fixture and the two result variants. Synthetic worker,
scope, fence, fingerprints and recovery decisions carry no authenticated authority.
Separate consuming tests must supply negative assertions and any service/store
evidence; no recovery or compensation policy is authorized by fixture support.

Read depth: full 90-line fixture and full 259-line parent; selected actual outcome
story/admission/observation helpers and record-fixture identity/state/event/record
paths; actual fold command/result validators, attempt-record validation and outcome
projection/record paths; selected consuming interface/arm/result tests. Parent
fixtures and owners beyond those paths were not fully reviewed for this note.
No tests/imports, database, provider, credential or Docker actions were performed.
