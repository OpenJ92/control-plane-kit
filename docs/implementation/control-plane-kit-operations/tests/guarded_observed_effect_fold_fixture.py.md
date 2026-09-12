Source: [control-plane-kit-operations/tests/guarded_observed_effect_fold_fixture.py](../../../../control-plane-kit-operations/tests/guarded_observed_effect_fold_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 200-line fixture builds the observed-fold command, protected intent record
and runtime-authority values used by the guarded contract tests. It extends the
[atomic fixture](atomic_effect_attempt_fold_fixture.py.md), not a PostgreSQL
fixture. Its helpers construct or deliberately forge values; they do not register
authority, execute a fold, open a unit of work or call a runtime provider. The
separate [PostgreSQL guarded fixture](../../../../control-plane-kit-operations/tests/postgres_guarded_observed_effect_fold_fixture.py)
inherits the persisted fold fixture and owns different setup behavior.

GuardedObservedEffectFold is captured with getattr from the parent's optionally
loaded fold module, falling back to None. require_guarded_language checks only
its presence. require_guarded_service first checks the inherited service binding,
then checks that execute_observed exists; it does not establish that the attribute
is callable, inspect its signature or invoke it. The
[parent](effect_attempt_fold_fixture.py.md) owns optional module loading and
transitive-import-error behavior. This fixture does not add import recovery or
an execution fallback.

observed_stories filters the inherited twenty-story catalogue by the literal
provider-observation profile, retaining twelve values: six observation variants
in ordinary and compensation event families. It does not itself assert a count,
and it constructs synthetic observations rather than querying an observer. The
first retained story, used by default, is ordinary observed-succeeded.

intent_for_story asks the inherited record fixture to construct an intent with
the story's compensation flag, run ID and activity ID. That path instantiates
the pure [intent fixture](effect_attempt_intent_fixture.py.md); it does not consult
a store. Defaults describe a Docker StartNode or compensation StopNode operation
with synthetic product material and authority reference/deliveries. If and only
if authority_ref is the exact False object, this helper clears the intent's
authority_ref, its authority_deliveries and every product's
runtime_authority_deliveries. This keeps the no-reference variation coherent
across those fields. Other authority_ref arguments do not replace the reference.
A non-None runtime_kind replaces that field through dataclasses.replace and
therefore still runs the production intent constructor.

intent_record_for_story pairs the story's attempt identity and original start
event with the supplied truthy intent or a newly constructed default. The actual
[intent-record owner](../src/control_plane_kit_operations/effect_attempt_intent_evidence.py.md)
reconstructs identity/event/intent, validates canonical intent round-tripping and
checks run/activity relationships. Its request fingerprint is derived from its
intent. The helper neither persists that evidence nor changes the story attempt
to match a modified intent. Intent-record admission does not independently prove
that the story's state fingerprint commits the modified request fingerprint;
the live fold interpreter later compares the stored intent and attempt truth.

runtime_authority_for_intent returns None immediately when the intent has no
authority reference, even if overrides were supplied. Otherwise it constructs
RegisteredRuntimeAuthority directly with fixed registration ID runtime-authority-a,
operator-a, a fixed 2030 admission-time string, empty metadata and Docker runtime
kind. Workspace and reference default from the intent; authority defaults to
LocalDockerSocketAuthority and status to ACTIVE. This is a fabricated registration
value, not evidence that a registration row exists or that an operator approved it.
The runtime kind is always Docker here, even if a caller varies the intent kind.

Workspace, reference and concrete authority use truthiness fallbacks, so None or
other falsey overrides select defaults. Status is passed directly to the actual
constructor. That constructor allows the REVOKED status as a lawful registration
value; the guarded command imposes its separate active-authority requirement.
This distinction lets consumers construct individually valid but incompatible
guard inputs without bypassing every underlying constructor.

remote_docker_authority constructs a RemoteDockerTlsAuthority with a synthetic
tcp endpoint at mac-mini.local:2376 and three SecretReference values under
secret://local/docker for ca, cert and key. The actual
[authority owner](../src/control_plane_kit_operations/runtime_authorities.py.md)
validates endpoint shape and reference types; it does not resolve these references
or contact Docker during construction. The remote value hides its endpoint and
references from dataclass repr, but its public descriptor retains reference IDs
and its storage descriptor retains the endpoint. The helper does not call either
descriptor, access credentials, read certificates or test endpoint reachability.
LocalDockerSocketAuthority likewise describes process-provided access without
opening a socket in this fixture.

fold_for_story chooses an intent, reads the story observation and replaces the
observation's request fingerprint when it differs from the selected intent's
production fingerprint. It preserves the observation's other fields, including
effect ID, and does not rewrite story.attempt. The resulting ObservedEffectOutcome
uses the story attempt identity. A supplied truthy outcome replaces that default;
the observation/fingerprint preparation still precedes this selection. The helper
derives transition and failure from the selected outcome, supplies its intent's
request ID and inherited worker-a/generation-seven authority/fence, then applies
keyword changes before constructing FoldEffectAttempt. Overrides can deliberately
break correspondence; dependent defaults are not recomputed after values.update.

guarded_command resolves story, intent, intent record, runtime authority and fold,
then calls the actual GuardedObservedEffectFold constructor. A private sentinel
distinguishes omitted runtime_authority from explicit None: omission fabricates
the default authority for the selected intent, while explicit None is passed
through and can exercise the missing-authority rejection. Other optional values
use truthiness fallbacks. In particular, supplying an intent record alone does
not cause the fold or authority to be derived from that record's intent; defaults
still come from the separately selected story/intent. Supplying a fold does not
reconcile the other members. This supports cross-joined negative cases.

The actual [fold owner](../src/control_plane_kit_operations/effect_attempt_fold.py.md)
requires an exact guard containing an exact, revalidated FoldEffectAttempt whose
outcome is exactly ObservedEffectOutcome, plus a revalidated exact intent record.
With no intent authority reference, runtime_authority must be None. With a
reference, it requires an exact active Docker registration with matching workspace,
reference and runtime kind, and reconstructs the concrete local/remote authority.
Its admission is stricter than the registration constructor, including exact
field types and dict metadata. A reference-bearing non-Docker intent is rejected;
this should not be generalized into a claim about every no-reference intent.

Guard admission also joins intent identity to transition/outcome identity,
request ID to fold request, derived intent fingerprint to outcome request
fingerprint and original start-event ID to observation effect ID. The guard's
intent and runtime-authority fields are repr-hidden. These constructor laws
admit a coherent supplied value, not current durable authority. On a fresh guarded
fold, the actual [interpreter](../src/control_plane_kit_operations/effect_attempt_fold_interpreter.py.md)
compares the locked attempt with stored intent evidence, checks the current claim
and lease observation, then loads the active runtime registration for update when
one is required and checks it against the supplied value. Replay follows its
separate earlier branch; this fixture does not establish that every replay rereads
registration or lease time.

request_fingerprint imports and calls the production
[intent fingerprint function](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py)
when invoked. It is not an independent hashing oracle, cache or fake constant.
The helper adds no exception normalization or safe-error assertions; normal
constructors and consumers determine which errors are expected.

subclass_copy creates a new subclass of a dataclass value's exact type, allocates
it without initialization and shallowly copies its dataclass fields through
object.__setattr__. Nested values remain shared; no hostile properties or dispatch
counters are added by this helper. forge_guard copies the declared guard fields,
overwrites selected values and delegates to forge_exact to allocate the exact
class without constructor validation. These are controlled hostile-input builders,
not alternate production admission paths. forge_guard has no presence guard of
its own before fields(GuardedObservedEffectFold).

Selected [consumer tests](../../../../control-plane-kit-operations/tests/test_guarded_observed_effect_fold_contract.py)
check twelve accepted observed worlds, local/remote and absent-reference forms,
repr hiding, cross-field rejection, subclass/forgery rejection and the service's
preflight boundary. Those assertions belong to the consumer. In particular,
subclass_copy itself proves neither rejection nor absence of dispatch. The
fixture exports its guard/service/fold bindings, RuntimeKind and forgery helpers,
but has no test methods, database lifecycle, registration mutation, provider
execution, event history, cleanup or retry behavior of its own.

Read depth: all 200 source lines and local helpers; retained full atomic/parent
fixture and fold-owner review, actual guarded interpreter branches, inherited
intent/story/fingerprint construction, intent-record admission, runtime-authority
value/endpoint validation and selected consuming assertions. This is not a full
review of the guarded consumer suite or the separate PostgreSQL fixture. No tests,
application imports, database connections, source/dependency changes, credentials,
Docker or provider actions were executed while authoring this companion.
