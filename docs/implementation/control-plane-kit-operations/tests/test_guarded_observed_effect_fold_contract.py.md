Source: [control-plane-kit-operations/tests/test_guarded_observed_effect_fold_contract.py](../../../../control-plane-kit-operations/tests/test_guarded_observed_effect_fold_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These eleven tests cover guarded observed-fold value admission, representation
hiding, selected hostile inputs and service preflight. Four controls exercise
existing outcome/intent/fold surfaces without the explicit guarded-publication
presence check; seven target tests require the guarded language. The source's
ungated labels describe those dependencies, not a guaranteed unittest execution
order. None of the tests returns a working unit of work, performs a database
transaction, registers runtime authority or calls a provider.

The [guarded fixture](guarded_observed_effect_fold_fixture.py.md) supplies twelve
synthetic provider-observation stories, covering six observation variants in both
ordinary and compensation event families. The first control requires exactly
twelve, exact ObservedEffectOutcome types, the story's transition kind, matching
canonical failure and matching request fingerprint. It compares the transition
kind rather than the entire transition, and does not inspect endpoint observation
records here. Story values and expectations share production constructors and
fingerprint functions through the inherited outcome fixture.

The intent/authority control takes the first observed story in each phase and
checks intent-record identity, original event and request fingerprint against
the story, plus exact registration type and matching workspace/reference/Docker
kind. It then uses the final, compensation story to construct a no-reference
intent and a reference-bearing EXTERNAL intent as individually lawful intent
records. The no-reference case asserts absent reference, empty intent deliveries
and no fabricated authority. The helper also clears product deliveries, but this
test does not separately assert that tuple for each product. These controls do
not claim that every individually valid intent is an admitted guard.

The ordinary-fold surface control requires the existing command's six fields,
both result variants' two fields and their union. It checks service constructor
parameter names unit_of_work_factory/id_factory and execute's self/command names,
and constructs exact execution and observed outcome types. These are selected
shape checks, not equality of every annotation, parameter kind, default or class
member. The fourth control submits an ordinary execution command to a service
whose factory raises a stored AssertionError. The identical error object and one
factory call prove that this selected command reaches the existing boundary;
they do not establish successful execution after that point.

Guard surface checks require the exact ordered fields fold, intent_record and
runtime_authority, frozen dataclass metadata, the same root-exported class and
repr=False on both protected members. Combined str/repr of the default guard
must omit its synthetic authority-reference name, client-key reference and private
upstream URL. The remote-authority case later checks that combined local/remote
guard rendering omits the remote host and all three remote TLS reference strings.
These canaries are present in supplied fixture values, making them meaningful
selected representation checks. The tests do not inspect every secret/reference,
all nested descriptors, logs or storage encodings, and do not bound successful
guard rendering. Frozen metadata is checked without an attempted mutation.

The positive guard matrix constructs all twelve stories and requires exact
ObservedEffectOutcome, canonical full transition and failure, intent/outcome
identity agreement and preservation of the story's original event. This exercises
the public constructor across the represented observation and phase variants.
It does not persist any guard or establish that the supplied intent and authority
match current durable state. The
[atomic fixture](atomic_effect_attempt_fold_fixture.py.md) and guarded fixture
construct the represented values rather than performing the described effects.

The authority-form test accepts the default active local Docker registration,
a synthetic remote TLS registration and a no-reference intent paired with None.
The explicit None is preserved by the fixture's sentinel, allowing a separate
missing-authority negative for a reference-bearing intent. The converse negative
supplies an authority to the no-reference intent. Three individually constructed
registration variations isolate revoked status, foreign workspace and foreign
reference. Each must give the categorical invalid-guard error. A final negative
changes the intent to EXTERNAL while retaining a Docker registration. It violates
both the referenced-intent Docker restriction and runtime-kind agreement; it is
not an isolated equality check or a proof that every non-Docker guard is impossible.
No combined no-reference/EXTERNAL case is exercised here.

The actual [guard validator](../src/control_plane_kit_operations/effect_attempt_fold.py.md)
requires an exact guard, exact revalidated fold and exactly ObservedEffectOutcome,
then reconstructs the intent record and authority values. Without an intent
reference it requires runtime_authority=None. With a reference it requires an
exact active Docker registration matching workspace, reference and runtime kind.
The no-reference branch does not itself impose the Docker-kind condition. Its
exact-type/field checks and reconstruction are stricter than the more general
[registration constructor](../src/control_plane_kit_operations/runtime_authorities.py.md),
which permits REVOKED as a lawful stored-value status. Fabricating such a value
does not revoke or register anything in a store.

Five cross-joined negative constructors exercise different guarded joins. An
ordinary execution fold and a recovery fold are each individually admitted fold
forms but lack the required observed-outcome arm. An opposite-phase intent record
changes the operation/deliveries and request fingerprint while retaining the
fixture's identity and original event ID; this is a changed-intent correspondence
case, not an isolated run/activity identity or phase guard. A distinct-start-event
record changes only the original event ID and explicitly asserts that its intent
fingerprint remains equal. That is a focused witness for the event-ID join beyond
the fingerprint. Changing only the valid fold's request_id to request-b provides
a separate request-ID join witness. All five use the same fixed invalid-guard
category/message; this file does not independently vary every identity field.

The actual validator joins the intent record's identity to fold transition and
outcome identity, request ID to fold request, derived intent fingerprint to
outcome request fingerprint and original event ID to observation effect ID.
The [intent-record owner](../src/control_plane_kit_operations/effect_attempt_intent_evidence.py.md)
admits the distinct event record because its constructor checks identity/run/
activity relationships and intent representation, not this later observation
event-ID join. The fixture repairs an observation fingerprint for a changed
selected intent without rewriting the original story attempt. Admission of the
supplied guard therefore remains separate from checking a stored attempt.

The hostile-constructor matrix has six inputs: a guard subclass, shallow subclass
copies of fold/intent-record/registration, an exact forged registration with
HostileText as workspace_id and an exact forged intent record whose nested intent
has source=object(). Each rejects with fixed safe error and leaves one shared
dispatch list empty. HostileText specifically instruments __class__ access,
equality and hashing, appending a marker and raising on each; it appears only in
the forged workspace case. Those spies protect those operations on that selected
field. The other subclass/forgery cases contain no such instrumentation, so their
rejection is not independently a proof of no arbitrary nested dispatch. Helpers
use object.__new__/object.__setattr__ to bypass normal admission deliberately.

The guarded service-entry test requires execute_observed's parameter names to be
self and command, then submits a valid guard to the failing factory. It requires
the identical stored AssertionError and exactly one call. Despite its title's
only-exact-entry wording, this does not enumerate all service members or prove
that no other entry point was added. The guarded fixture's presence check uses
hasattr; this test adds signature and actual invocation evidence.

The final preflight matrix bypasses constructor admission for a guard missing
required authority, one carrying the forged nested intent and a guard subclass
whose __getattribute__ records and rejects every attribute access. Each call to
execute_observed must raise the exact invalid-guard error, pass safe-error checks,
leave the factory call count at zero and leave the shared attribute-dispatch list
empty. The whole-guard access spy is stronger and distinct from the earlier
HostileText spies; it instruments only that subclass case. A structurally valid
guard with empty worker scopes separately raises EffectAttemptFoldDenied before
the factory, but this last case checks no exact denial text or safe-error helper.

The actual [interpreter](../src/control_plane_kit_operations/effect_attempt_fold_interpreter.py.md)
revalidates the entire guard before accessing its fold for execution, then checks
execution:operate scope and fence representability before opening a unit of work.
This explains the zero/one factory-call witnesses. Every FailIfUnitOfWork raises
before returning anything, and the service uses an uncounted constant ID lambda.
The tests establish selected preflight rejection and successful boundary arrival,
not ID-allocation counts, claim freshness, locked registration equality, lease
expiry, rollback, replay or durable event/outcome history.

assert_invalid_guard requires InvalidOperationCommand with the exact message
guarded observed effect fold command is invalid. The inherited
[error helper](../../../../control-plane-kit-operations/tests/effect_attempt_record_fixture.py)
requires no cause/context, at most 256 characters in combined str/repr and exclusion
of supplied canaries. Repr hiding and the two kinds of dispatch spies supply the
security evidence actually asserted here. Synthetic authority/secret references
remain data; no credentials are resolved, external authorization checked, provider
effect observed, or cleanup/retry process exercised.

Read depth: all 505 source lines, eleven tests and local factory/error/HostileText
helpers; retained full guarded/atomic/parent fixtures and fold-owner review;
actual guarded validators and service preflight, intent-record and registration
admission, and relevant story/product/error helpers. No tests, application imports,
database connections, source/dependency changes, credentials, Docker or provider
actions were executed while authoring this companion.
