Source: [control-plane-kit-operations/tests/postgres_guarded_observed_effect_fold_fixture.py](../../../../control-plane-kit-operations/tests/postgres_guarded_observed_effect_fold_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This twelve-helper fixture extends the
[PostgreSQL fold fixture](postgres_effect_attempt_fold_fixture.py.md) with observed
outcome worlds, durable authority registration, guarded command construction and
selected interaction sentinels. It declares no test methods of its own. It is not
the [pure guarded fixture](guarded_observed_effect_fold_fixture.py.md): inherited
setup/read helpers and some apparently convenient constructors perform database
work. Consuming tests own when those operations occur and what they prove.

observed_stories filters inherited synthetic stories by provider-observation
profile, producing twelve ordinary/compensation variants at this source version.
observed_story chooses the first matching name and identical compensation flag;
unknown selections raise StopIteration. It does not discover provider outcomes.
The inherited story/fingerprint builders may themselves use the PostgreSQL override
of intent_for_attempt, which reads request/plan coordinates for a run.

persisted_intent is a reconstruction helper, not an intent-store get: it asks the
inherited builder for an intent using current identity and original-event family.
That builder queries the run and produces fixture material. Only authority_ref
is False removes the authority reference, top-level deliveries and every product's
runtime-authority deliveries; another falsey value is not that exact switch.
intent_record wraps the current identity/original event and either the supplied
intent or this reconstructed one in EffectAttemptIntentRecord. Its intent default
is based on None, not general truthiness. Typed construction is not a fresh read
of the stored row or proof of every current state/fingerprint relationship.

register_runtime_authority returns None when the intent has no reference. Otherwise
it constructs local socket or, for truthy remote, fixed synthetic TLS endpoint and
three SecretReference values, registers them through an actual UoW/store, requests
commit and returns the registration. The values are references, not resolved
credentials; no socket, certificate or provider is contacted. Registration uses
the intent workspace/reference, Docker kind and fixed fixture operator/time.

The actual [runtime-authority store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/runtime_authority_store.py)
returns an existing active registration when kind and authority agree, conflicts
on a different active authority, or inserts a new record. Calling the helper is
therefore not a guarantee of a newly inserted row. Direct store registration is
fixture setup, not a public authorization or replacement-policy workflow. Physical
commit occurs on successful [UoW exit](../src/control_plane_kit_operations/postgres/unit_of_work.py.md).

guarded_observed_command defaults story/current/intent/intent_record through or:
falsey supplied values are replaced. Current defaults to a typed database read,
and intent defaults may query the run. With runtime_authority=None, truthy register
and a referenced intent, it calls the durable registration helper. Unlike the
pure fixture's sentinel, explicit None does not suppress that registration; callers
must pass register=False when they intend no automatic registration.

When fold is None, command construction replaces both the observation effect ID
and request fingerprint using the supplied current original event and intent,
constructs ObservedEffectOutcome and derives transition/failure. It does not write
that repaired observation back into the original story or persisted attempt.
A supplied fold is retained rather than recomputed. Supplied intent_record,
runtime_authority and fold are not reconciled automatically with one another;
the [guard language](../src/control_plane_kit_operations/effect_attempt_fold.py.md)
rejects incongruent members. Its constructor does not substitute for the fresh
transaction's durable intent/active-authority checks.

seed_guarded_source resets fixture truth for the story family, builds a STARTED
record with fixed run/activity/event/time and ordinal 3 or 7, derives intent and
optionally clears delivery declarations. process_delivery uses general falsiness,
unlike authority_ref's exact False switch; clearing delivery alone retains an
authority reference. The helper recomputes request fingerprint, rebuilds the
original event's state evidence, uses that same event as original/latest and
persists the event/intent/attempt via the inherited helper. It returns current,
intent and a constructed expected intent record, not an independently fetched
copy of each persisted value. It does not register runtime authority at this step.

observed_service delegates to fold_service_with_sequence and returns the inherited
service/Sequence pair, not a special observer implementation. The inherited checked
wrapper converts selected missing-implementation failures into test failures;
other errors retain their normal paths. No call-count or success assertion follows
merely from constructing this pair.

persist_terminal deliberately bypasses the fold service. It seeds a started
record, derives command and expected Core state, constructs a fixed-time event,
attempt and expected outcome aggregate, then appends event, puts observations,
inserts outcome and CASes the attempt in one UoW. Each returned acknowledgement
must equal the supplied value before commit. It returns the constructed attempt
and outcome. This establishes typed persisted setup for replay tests, not proof
that first-fold service authority/expiry/registration admission ran. The expected
outcome helper shares production projection and constructor logic and omits an
intent argument for these transport-observation fixtures; it is not an independent
verification-intent implementation or provider evidence.

forbidden_lower_interactions creates one AssertionError and returns eight patchers:
outcome get, intent get, lease observation, active-authority lookup, event append,
observation put, outcome insert and CAS. The caller must enter them; merely getting
the tuple activates nothing. The active-selector patch uses create=True, so the
helper alone does not prove that method exists. It does not forbid initial
request/run/attempt reads, ordinal selection, ID allocation, commit or every SQL
operation. Consumers use these sentinels to test a selected earlier rejection
boundary, not blanket absence of database access.

complete_snapshot combines inherited attempt and non-advancement projections.
Those include execution/attempt/intent data, selected outcome/membership fields,
counts and graph/plan/workspace projections. The name does not mean every table
or field: runtime-authority rows, all observation bodies and all outcome preimages
are not independently captured. fold_ids_for_story constructs an event label plus
one-based observation labels from the outcome; it neither allocates through the
service nor asserts uniqueness/validity. Its outcome builder can use inherited
database-backed intent/fingerprint helpers.

The [interpreter](../src/control_plane_kit_operations/effect_attempt_fold_interpreter.py.md)
rechecks stored intent and, for a fresh referenced observed fold, active authority
and lease expiry. Replay has an earlier branch that does not repeat those fresh
checks, while current-claim/fence rules still apply. The
[atomic PostgreSQL tests](test_postgres_atomic_effect_attempt_fold.py.md) and
[guarded replay tests](../../../../control-plane-kit-operations/tests/test_postgres_guarded_observed_effect_fold_first_replay.py)
compose these helpers with assertions; the fixture itself grants no replay,
recovery, secret use or provider-cleanup credit. Its thirteen exports include
classes/constants useful to those consumers, not a new public package API.

Read depth: all 295 source lines and twelve helpers/imports; actual inherited
intent/seed/expected/snapshot/ID/checked-service paths, registration store, complete
UoW and retained full fold language/interpreter; selected consuming replay and
atomic tests. Setup and shared-oracle limits are explicit. No imports/tests,
database connections, Docker, credentials, provider actions or source edits ran
while authoring this companion.
